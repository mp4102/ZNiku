"""编排 ZNIKU 0.2.0 Run、Scheduler、Repository 与 Node Runner。

本模块是 Phase 2 的最小同步 Project Service：Run 启动时复制普通 graph/definition snapshot，随后按
DAG 顺序执行 ready 节点、登记完整结果、复用启动前仍适用的历史 result，并持久化人工外部 handoff。
它不实现 Compiler、ExecutionPlan、digest、Evidence、checkpoint、resume、媒体业务节点或并发调度。

节点失败只会阻断其依赖分支，Run 保持 ``running`` 以等待操作者执行 ``rerun_from_start``；独立分支
仍会继续。只有全部选中节点 completed 才把 Run 终结为 completed，避免把可从头重跑的节点失败错误地
变成不可再次创建 attempt 的 Run 终态。
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Never, cast
from uuid import UUID

from pydantic import JsonValue, ValidationError

from zniku.graph import GraphValidationError, GraphValidator, NodeDefinition, NodeInstance
from zniku.project import ProjectStore

from .models import (
    Artifact,
    ExternalHandoff,
    ExternalOutputTarget,
    FailureReason,
    NodeResult,
    NodeRun,
    NodeRunState,
    Run,
    RunState,
    RuntimeFailure,
    StaleReason,
    new_runtime_id,
    utc_now,
)
from .progress import (
    BoundProgressReporter,
    MonotonicClock,
    ProgressError,
    ProgressInfrastructureError,
    ProgressSample,
    WallClock,
)
from .repository import (
    RuntimeConflictError,
    RuntimeNotFoundError,
    RuntimeRepository,
    RuntimeRepositoryError,
)
from .reuse import ReuseCandidate, analyze_reuse, capture_node_signature
from .runner import (
    HandoffInput,
    HandoffOutput,
    ManualHandoff,
    ManualSubmission,
    MediaProbe,
    NodeExecutionRequest,
    NodeRunner,
    NodeValidator,
    OutputPathSpec,
    PythonAdapter,
    RunnerArtifact,
    RunnerError,
    RunnerFailureReason,
    RunnerInput,
    RunnerResult,
    ValidatedOutput,
)
from .scheduler import Scheduler

type ArtifactQuickProbe = Callable[[Artifact], bool]
type OutputPathResolver = Callable[[Run, NodeInstance, NodeDefinition], tuple[OutputPathSpec, ...]]


class RuntimeServiceError(RuntimeError):
    """Service 无法在不破坏 Run 历史的前提下继续时的公共错误。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class _ResolvedInputs:
    """同时保留 reuse 的稳定 edge 顺序和 Runner 的端口绑定。"""

    artifact_ids: tuple[str, ...]
    runner_inputs: tuple[RunnerInput, ...]


@dataclass(frozen=True, slots=True)
class RerunImpact:
    """只读的当前重跑影响，不持久化、不保留执行计划，也不保证稍后仍可复用。"""

    rerun_node_ids: tuple[str, ...]
    reusable_node_ids: tuple[str, ...]


class RuntimeService:
    """在单用户本地 Project 上同步推进普通 DAG Run。

    构造 Service 会把遗留 ``running`` NodeRun 失败为 ``interrupted``；``waiting_external`` 不变。
    这是应用重启恢复，不接管旧进程，也不尝试恢复 attempt 内部进度。
    """

    def __init__(
        self,
        store: ProjectStore,
        work_root: str | Path,
        *,
        python_adapters: Mapping[str, PythonAdapter] | None = None,
        validators: Mapping[str, NodeValidator] | None = None,
        media_probe: MediaProbe | None = None,
        ffprobe_executable: str = "ffprobe",
        artifact_quick_probe: ArtifactQuickProbe | None = None,
        progress_wall_clock: WallClock | None = None,
        progress_monotonic_clock: MonotonicClock | None = None,
        output_path_resolver: OutputPathResolver | None = None,
    ) -> None:
        root = Path(work_root)
        try:
            root.mkdir(parents=True, exist_ok=True)
            self._work_root = root.resolve(strict=True)
        except OSError as error:
            raise RuntimeServiceError("E_SERVICE_WORK_ROOT_INVALID", str(error)) from error
        if not self._work_root.is_dir():
            raise RuntimeServiceError("E_SERVICE_WORK_ROOT_INVALID", "work_root 必须是目录")

        self._repository = RuntimeRepository(store)
        self._runner = NodeRunner(
            self._work_root,
            python_adapters=python_adapters,
            validators=validators,
            media_probe=media_probe,
            ffprobe_executable=ffprobe_executable,
        )
        self._artifact_quick_probe = artifact_quick_probe or _default_artifact_quick_probe
        self._output_path_resolver = output_path_resolver
        self._progress_wall_clock = progress_wall_clock or utc_now
        self._progress_monotonic_clock = progress_monotonic_clock or monotonic
        self._progress_lock = threading.RLock()
        self._progress_samples: dict[str, ProgressSample] = {}
        self._progress_reporters: dict[str, BoundProgressReporter] = {}
        self.recover_interrupted()

    @property
    def repository(self) -> RuntimeRepository:
        """公开共享同一 ``.zniku`` authority 的 Runtime Repository。"""

        return self._repository

    def progress_snapshot(self, run_id: str) -> tuple[ProgressSample, ...]:
        """返回当前进程中绑定指定 Run 的不可变细粒度 progress snapshot。

        该投影不是恢复信息；终态 attempt 会被移除，新 ``RuntimeService`` 实例从空 map 开始。
        Project Service 仍须按其捕获的 Run snapshot 精确过滤最新 running automatic attempt。
        """

        with self._progress_lock:
            return tuple(
                sorted(
                    (
                        sample
                        for sample in self._progress_samples.values()
                        if sample.run_id == run_id
                    ),
                    key=lambda sample: (sample.observed_at, sample.node_run_id),
                )
            )

    def create_run(
        self,
        *,
        selected_targets: tuple[str, ...] = (),
        expected_storage_revision: int | None = None,
        rerun_from_node_id: str | None = None,
    ) -> Run:
        """从当前 Project 建立普通 snapshot，并为选中闭包创建 attempt 1。

        空 ``selected_targets`` 表示整图。下游 attempt 先以空 inputs 持久化；它第一次成为 ready 时才
        一次性绑定已登记的直接输入 Artifact，避免为尚不存在的输出伪造身份。
        authoring draft 在构造任何 Run/attempt 前接受完整 GraphValidator 校验。可选存储计数前提由
        Repository 在插入事务内重验，不进入 Run，也不会因运行进度写入而递增。
        """

        snapshot = self._repository.project_store.load()
        try:
            GraphValidator(snapshot.definitions).validate(snapshot.project.graph)
        except GraphValidationError as error:
            raise RuntimeServiceError("E_SERVICE_GRAPH_INVALID", str(error)) from error
        scheduler = Scheduler(snapshot.project.graph)
        states = dict.fromkeys(scheduler.topological_order, NodeRunState.PENDING.value)
        analysis = scheduler.analyze(
            states,
            selected_targets=selected_targets if selected_targets else None,
        )
        normalized_targets = analysis.selected_targets if selected_targets else ()
        run = Run.pending(
            project_id=snapshot.project.project_id,
            graph_snapshot=snapshot.project.graph,
            definitions_snapshot=snapshot.definitions,
            selected_targets=normalized_targets,
        )
        nodes = {node.node_id: node for node in run.graph_snapshot.nodes}
        node_runs: list[NodeRun] = []
        for node_id in analysis.selected_node_ids:
            node = nodes[node_id]
            node_run_id = new_runtime_id()
            node_runs.append(
                NodeRun.pending(
                    run_id=run.run_id,
                    node_id=node_id,
                    definition_version=node.definition_version,
                    attempt=1,
                    input_artifact_ids=(),
                    work_dir=str(self._attempt_work_dir(node_run_id)),
                    node_run_id=node_run_id,
                )
            )
        return self._repository.start_run(
            run,
            node_runs,
            started_at=utc_now(),
            expected_storage_revision=expected_storage_revision,
            rerun_from_node_id=rerun_from_node_id,
        )

    def create_rerun_run(
        self, node_id: str, *, expected_storage_revision: int | None = None
    ) -> Run:
        """为终态历史之后的“从此处重新运行”建立新的普通全图 Run。

        存储 CAS、节点 ``rerun_requested``、下游 ``upstream_changed`` 与新 Run/attempt 插入同事务
        提交；任何冲突都不会提前污染 stale。未受影响 fresh 节点仍可复用，不改写终态 Run。
        """

        snapshot = self._repository.project_store.load()
        try:
            GraphValidator(snapshot.definitions).validate(snapshot.project.graph)
        except GraphValidationError as error:
            raise RuntimeServiceError("E_SERVICE_GRAPH_INVALID", str(error)) from error
        node_ids = {node.node_id for node in snapshot.project.graph.nodes}
        if node_id not in node_ids:
            raise RuntimeServiceError(
                "E_SERVICE_RERUN_NODE_UNKNOWN", f"当前 Project 不含节点 {node_id!r}"
            )
        return self.create_run(
            expected_storage_revision=expected_storage_revision, rerun_from_node_id=node_id
        )

    def inspect_rerun_impact(self, run_id: str, node_id: str, *, new_run: bool) -> RerunImpact:
        """复用同一候选检查作只读预览；不创建 attempt、不写 stale、不执行 adapter。

        当前 Project 将建立新 Run 时，以拓扑顺序解析能保留的直接输入 Artifact，再调用正式
        reuse 候选规则。被请求节点及其下游一律从头运行。现有 Run 中未被取代的 completed
        结果只作快速可读性检查；其历史记录保持不变。
        """

        original = self._repository.get_run(run_id)
        if not new_run:
            selected = self._selected_node_ids(original)
            if original.state is not RunState.RUNNING or node_id not in selected:
                raise RuntimeServiceError("E_SERVICE_RERUN_RUN_STATE", "Run 不能在原位重跑")
            latest = self._latest_attempts(original, selected)
            closure = set(Scheduler(original.graph_snapshot).downstream_closure(node_id))
            rerun = tuple(item for item in selected if item in closure)
            if any(latest[item].state is NodeRunState.RUNNING for item in rerun):
                raise RuntimeServiceError("E_SERVICE_RERUN_ACTIVE", "不能取代仍在运行的 attempt")
            reusable = tuple(
                item
                for item in selected
                if item not in closure
                and latest[item].state is NodeRunState.COMPLETED
                and all(
                    self._probe_artifact(self._repository.get_artifact(identity))[0]
                    for identity in latest[item].output_artifact_ids
                )
            )
            return RerunImpact(rerun, reusable)

        snapshot = self._repository.project_store.load()
        try:
            GraphValidator(snapshot.definitions).validate(snapshot.project.graph)
        except GraphValidationError as error:
            raise RuntimeServiceError("E_SERVICE_GRAPH_INVALID", str(error)) from error
        scheduler = Scheduler(snapshot.project.graph)
        if node_id not in scheduler.topological_order:
            raise RuntimeServiceError("E_SERVICE_RERUN_NODE_UNKNOWN", "当前图已不含所选节点")
        forced = set(scheduler.downstream_closure(node_id))
        # 普通模型仅在本次函数栈中用于共享签名比较，不写 snapshot、历史或磁盘工作目录。
        projected = Run.pending(
            project_id=snapshot.project.project_id,
            graph_snapshot=snapshot.project.graph,
            definitions_snapshot=snapshot.definitions,
        )
        nodes = {item.node_id: item for item in snapshot.project.graph.nodes}
        outputs: dict[tuple[str, str], str] = {}
        rerun_ids: list[str] = []
        reusable_ids: list[str] = []
        for identity in scheduler.topological_order:
            incoming = capture_node_signature(snapshot.project.graph, identity).incoming_edges
            source_keys = tuple((edge.source_node_id, edge.source_port_id) for edge in incoming)
            if identity in forced or any(key not in outputs for key in source_keys):
                rerun_ids.append(identity)
                continue
            attempt = NodeRun.pending(
                run_id=projected.run_id,
                node_id=identity,
                definition_version=nodes[identity].definition_version,
                attempt=1,
                input_artifact_ids=tuple(outputs[key] for key in source_keys),
                work_dir=str(self._work_root),
            )
            result = self._find_reusable_result(projected, attempt, persist_probe_stale=False)
            if result is None:
                rerun_ids.append(identity)
                continue
            reusable_ids.append(identity)
            outputs.update(
                {(identity, item.producer_port_id): item.artifact_id for item in result.outputs}
            )
        return RerunImpact(tuple(rerun_ids), tuple(reusable_ids))

    def run_until_blocked(self, run_id: str) -> Run:
        """顺序执行全部即时 ready 节点，直到完成或只剩 blocked/waiting/failed。

        一个节点失败不会提前结束循环，因此不依赖该节点的独立分支仍会执行。方法是同步的；Phase 2
        不承诺并行进程调度。
        """

        while True:
            run = self._repository.get_run(run_id)
            if run.state is RunState.COMPLETED:
                return run
            if run.state is not RunState.RUNNING:
                raise RuntimeServiceError(
                    "E_SERVICE_RUN_NOT_RUNNING",
                    f"Run {run_id} 当前状态为 {run.state.value}",
                )

            selected = self._selected_node_ids(run)
            latest = self._latest_attempts(run, selected)
            states = {node_id: latest[node_id].state.value for node_id in selected}
            analysis = Scheduler(run.graph_snapshot).analyze(
                states,
                selected_targets=run.selected_targets if run.selected_targets else None,
            )
            if analysis.ready_node_ids:
                self._process_ready(run, latest[analysis.ready_node_ids[0]])
                continue
            if selected and all(
                latest[node_id].state is NodeRunState.COMPLETED for node_id in selected
            ):
                return self._repository.transition_run(
                    run_id,
                    RunState.COMPLETED,
                    occurred_at=utc_now(),
                )
            if not selected:
                return self._repository.transition_run(
                    run_id,
                    RunState.COMPLETED,
                    occurred_at=utc_now(),
                )
            return self._repository.get_run(run_id)

    def submit_external(
        self,
        node_run_id: str,
        *,
        run_id: str | None = None,
        handoff_id: str | None = None,
        submission: ManualSubmission | None = None,
    ) -> Run:
        """验收一个仍为最新 attempt 的 manual_external handoff 并继续 Run。

        被上游 rerun 取代的旧 handoff 会失败关闭；它的历史 NodeRun 不被改写，也不能晚到覆盖新 head。
        Project Service 必须同时传入 ``run_id`` 与 ``handoff_id``；可选值只保留 Runtime 内部
        既有调用兼容，最终仍从持久 authority 解析并验证精确绑定。
        """

        persisted = self._repository.get_node_run(node_run_id)
        resolved_run_id = persisted.run_id if run_id is None else run_id
        node_run = self.inspect_external_handoff(
            resolved_run_id,
            node_run_id,
            handoff_id=handoff_id,
        )
        run = self._repository.get_run(resolved_run_id)

        request = self._execution_request(run, node_run)
        handoff = self._runner_handoff(run, node_run, request.inputs)
        try:
            result = self._runner.submit_manual(request, handoff, submission)
            self._register_runner_result(node_run, result)
        except RunnerError as error:
            self._fail_node_run(node_run, error, external_submission=True)
        except RuntimeServiceError as error:
            self._fail_service_node_run(
                node_run,
                error,
                reason=FailureReason.EXTERNAL_SUBMISSION_INVALID,
            )
        return self.run_until_blocked(run.run_id)

    def inspect_external_handoff(
        self,
        run_id: str,
        node_run_id: str,
        *,
        handoff_id: str | None = None,
    ) -> NodeRun:
        """只读解析一个精确绑定且仍可操作的最新 waiting handoff。"""

        run = self._repository.get_run(run_id)
        node_run = self._repository.get_node_run(node_run_id)
        if node_run.run_id != run.run_id:
            raise RuntimeServiceError(
                "E_SERVICE_NODE_RUN_OUTSIDE_RUN",
                "NodeRun 不属于声明的 Run",
            )
        latest = self._latest_attempts(run, self._selected_node_ids(run))
        if latest.get(node_run.node_id) != node_run:
            raise RuntimeServiceError(
                "E_SERVICE_HANDOFF_SUPERSEDED",
                "manual_external attempt 已被新的 rerun attempt 取代",
            )
        if node_run.state is not NodeRunState.WAITING_EXTERNAL:
            raise RuntimeServiceError(
                "E_SERVICE_HANDOFF_STATE",
                f"NodeRun 当前状态为 {node_run.state.value}",
            )
        persisted_handoff = node_run.external_handoff
        if persisted_handoff is None:
            raise RuntimeServiceError("E_SERVICE_HANDOFF_MISSING", "waiting_external 缺少 handoff")
        if handoff_id is not None and persisted_handoff.handoff_id != handoff_id:
            raise RuntimeServiceError(
                "E_SERVICE_HANDOFF_STALE",
                "handoff identity 已过期或不属于声明 attempt",
            )
        return node_run

    def inspect_external_outputs(
        self,
        run_id: str,
        node_run_id: str,
        *,
        handoff_id: str,
    ) -> tuple[ValidatedOutput, ...]:
        """只读执行 handoff 的完整媒体与节点 validator，不登记任何结果。"""

        node_run = self.inspect_external_handoff(
            run_id,
            node_run_id,
            handoff_id=handoff_id,
        )
        run = self._repository.get_run(run_id)
        request = self._execution_request(run, node_run)
        handoff = self._runner_handoff(run, node_run, request.inputs)
        return self._runner.inspect_manual_outputs(request, handoff)

    def inspect_external_import_candidate(
        self,
        run_id: str,
        node_run_id: str,
        *,
        handoff_id: str,
        port_id: str,
        candidate: Path,
    ) -> tuple[ValidatedOutput, ...]:
        """只读验证宿主 staging 中的人工候选；目标发布与显式 Submit 保持独立。"""

        node_run = self.inspect_external_handoff(run_id, node_run_id, handoff_id=handoff_id)
        run = self._repository.get_run(run_id)
        request = self._execution_request(run, node_run)
        handoff = self._runner_handoff(run, node_run, request.inputs)
        return self._runner.inspect_manual_candidate(
            request, handoff, port_id=port_id, candidate=candidate
        )

    def rerun_from_start(
        self, run_id: str, node_id: str, *, expected_storage_revision: int | None = None
    ) -> Run:
        """为节点及选中闭包内下游创建新 attempt，并立即执行到下个阻塞点。

        旧 attempt (包括 pending 或 waiting_external) 保持不变；旧 handoff 因不再是最新 attempt
        而不能 Submit。若闭包内仍有真正 ``running`` attempt，则拒绝并发重跑，避免两个进程竞争。
        """

        run = self._repository.get_run(run_id)
        if run.state is not RunState.RUNNING:
            raise RuntimeServiceError(
                "E_SERVICE_RERUN_RUN_STATE",
                "只有仍在等待操作者处理的 running Run 可以创建新 attempt",
            )
        selected = self._selected_node_ids(run)
        if node_id not in selected:
            raise RuntimeServiceError(
                "E_SERVICE_RERUN_NODE_OUTSIDE_SELECTION",
                f"节点 {node_id!r} 不属于当前 Run 执行闭包",
            )
        scheduler = Scheduler(run.graph_snapshot)
        selected_set = set(selected)
        closure = tuple(
            item for item in scheduler.downstream_closure(node_id) if item in selected_set
        )
        latest = self._latest_attempts(run, selected)
        active = tuple(item for item in closure if latest[item].state is NodeRunState.RUNNING)
        if active:
            raise RuntimeServiceError(
                "E_SERVICE_RERUN_ACTIVE",
                "不能取代仍在运行的 attempt：" + ", ".join(active),
            )

        nodes = {node.node_id: node for node in run.graph_snapshot.nodes}
        attempts = {
            item: max(node_run.attempt for node_run in run.node_runs if node_run.node_id == item)
            for item in closure
        }
        new_attempts: list[NodeRun] = []
        for item in closure:
            node_run_id = new_runtime_id()
            new_attempts.append(
                NodeRun.pending(
                    run_id=run_id,
                    node_id=item,
                    definition_version=nodes[item].definition_version,
                    attempt=attempts[item] + 1,
                    input_artifact_ids=(),
                    work_dir=str(self._attempt_work_dir(node_run_id)),
                    node_run_id=node_run_id,
                )
            )
        self._repository.create_rerun_attempts(
            run_id,
            node_id,
            new_attempts,
            updated_at=utc_now(),
            expected_storage_revision=expected_storage_revision,
        )
        return self.run_until_blocked(run_id)

    def abandon_run(self, run_id: str) -> Run:
        """原子放弃一个没有 automatic running attempt 的非终态 Run。

        queued pending Run 尚未物化的闭包 attempt 会在同一事务内创建并立即取消；这里只生成随机
        identity 与受控 work_dir 路径，不创建目录、日志或输出。既有 completed/failed attempt 和所有
        handoff/Artifact 都保持原样。
        """

        run = self._repository.get_run(run_id)
        abandoned_at = utc_now()
        pending_attempts: list[NodeRun] = []
        if run.state is RunState.PENDING:
            nodes = {node.node_id: node for node in run.graph_snapshot.nodes}
            for node_id in self._selected_node_ids(run):
                node_run_id = new_runtime_id()
                node = nodes[node_id]
                pending_attempts.append(
                    NodeRun.pending(
                        run_id=run.run_id,
                        node_id=node_id,
                        definition_version=node.definition_version,
                        attempt=1,
                        input_artifact_ids=(),
                        work_dir=str(self._attempt_work_dir(node_run_id)),
                        node_run_id=node_run_id,
                        created_at=abandoned_at,
                    )
                )
        return self._repository.abandon_run(
            run_id,
            abandoned_at=abandoned_at,
            pending_node_runs=tuple(pending_attempts),
        )

    def recover_interrupted(self) -> tuple[NodeRun, ...]:
        """显式重复执行幂等启动恢复；先关闭 reporter，再原子写入最后可信 fraction。"""

        # 不能先写 failed 再关闭 reporter：限频窗口内的最后 sample 只存在于内存，顺序颠倒会永久
        # 丢失它。这里只在 progress lock 下复制引用，随后由 reporter 自己的锁串行 close/report，
        # 避免持有 progress lock 等待 reporter 时与 publish/remove 回调形成锁反转。
        with self._progress_lock:
            reporters = tuple(self._progress_reporters.values())
        final_progress: dict[str, tuple[str, int, float]] = {}
        for reporter in reporters:
            sample = reporter.close()
            if sample is not None:
                final_progress[sample.node_run_id] = (
                    sample.run_id,
                    sample.attempt,
                    sample.fraction,
                )

        recovered = self._repository.recover_interrupted(
            recovered_at=utc_now(),
            final_progress=final_progress,
        )
        recovered_ids = {node_run.node_run_id for node_run in recovered}
        with self._progress_lock:
            for node_run_id in recovered_ids:
                self._progress_samples.pop(node_run_id, None)
                self._progress_reporters.pop(node_run_id, None)
        return recovered

    def _process_ready(self, run: Run, node_run: NodeRun) -> None:
        resolved = self._resolve_inputs(run, node_run.node_id)
        if node_run.input_artifact_ids:
            if node_run.input_artifact_ids != resolved.artifact_ids:
                raise RuntimeServiceError(
                    "E_SERVICE_INPUT_BINDING_CHANGED",
                    "pending attempt 已绑定 inputs 与 Run snapshot 当前上游结果不一致",
                )
        elif resolved.artifact_ids:
            node_run = self._repository.bind_inputs(node_run.node_run_id, resolved.artifact_ids)

        if self._try_reuse(run, node_run):
            return
        request = self._execution_request(run, node_run, resolved=resolved)
        definition = request.definition
        if definition.execution_mode.value == "manual_external":
            self._prepare_manual(node_run, request)
        else:
            self._run_automatic(node_run, request)

    def _run_automatic(self, node_run: NodeRun, request: NodeExecutionRequest) -> None:
        started_at = utc_now()
        log_path = str(Path(node_run.work_dir) / "logs")
        running = self._repository.transition_node_run(
            node_run.node_run_id,
            NodeRunState.RUNNING,
            occurred_at=started_at,
            log_path=log_path,
        )
        reporter = self._progress_reporter(running)
        try:
            result = self._runner.run_automatic(request, progress=reporter)
        except ProgressError as error:
            sample = reporter.close()
            self._fail_progress_node_run(running, error, progress=self._sample_fraction(sample))
        except ProgressInfrastructureError as error:
            reporter.close()
            self._raise_progress_infrastructure(error)
        except RunnerError as error:
            sample = reporter.close()
            self._fail_node_run(running, error, progress=self._sample_fraction(sample))
        except RuntimeServiceError as error:
            sample = reporter.close()
            self._fail_service_node_run(
                running,
                error,
                reason=FailureReason.EXECUTION_ERROR,
                progress=self._sample_fraction(sample),
            )
        else:
            sample = reporter.close()
            try:
                self._register_runner_result(running, result)
            except RuntimeServiceError as error:
                self._fail_service_node_run(
                    running,
                    error,
                    reason=FailureReason.EXECUTION_ERROR,
                    progress=self._sample_fraction(sample),
                )

    def _progress_reporter(self, node_run: NodeRun) -> BoundProgressReporter:
        """为唯一 running automatic attempt 构造不暴露 Repository 的 reporter。"""

        reporter = BoundProgressReporter(
            node_run.run_id,
            node_run.node_run_id,
            node_run.attempt,
            validate_target=self._validate_progress_target,
            persist=self._persist_progress,
            publish=self._publish_progress,
            remove=self._remove_progress,
            wall_clock=self._progress_wall_clock,
            monotonic_clock=self._progress_monotonic_clock,
        )
        with self._progress_lock:
            self._progress_reporters[node_run.node_run_id] = reporter
        return reporter

    def _validate_progress_target(self, run_id: str, node_run_id: str, attempt: int) -> None:
        try:
            self._repository.inspect_progress_target(run_id, node_run_id, attempt)
        except (RuntimeConflictError, RuntimeNotFoundError) as error:
            self._raise_progress_target_error(error)
        except RuntimeRepositoryError as error:
            raise ProgressInfrastructureError(error) from error

    def _persist_progress(
        self,
        run_id: str,
        node_run_id: str,
        attempt: int,
        fraction: float,
    ) -> None:
        try:
            self._repository.update_progress(
                node_run_id,
                fraction,
                run_id=run_id,
                attempt=attempt,
            )
        except (RuntimeConflictError, RuntimeNotFoundError) as error:
            self._raise_progress_target_error(error)
        except RuntimeRepositoryError as error:
            raise ProgressInfrastructureError(error) from error

    def _publish_progress(self, sample: ProgressSample) -> None:
        with self._progress_lock:
            self._progress_samples[sample.node_run_id] = sample

    def _remove_progress(self, node_run_id: str) -> None:
        with self._progress_lock:
            self._progress_samples.pop(node_run_id, None)
            self._progress_reporters.pop(node_run_id, None)

    @staticmethod
    def _raise_progress_target_error(error: RuntimeRepositoryError) -> Never:
        raw_code = str(getattr(error, "code", "E_PROGRESS_TARGET_INVALID"))
        code = raw_code if raw_code.startswith("E_PROGRESS_") else "E_PROGRESS_TARGET_INVALID"
        raise ProgressError(code, str(error)) from error

    @staticmethod
    def _raise_progress_infrastructure(error: ProgressInfrastructureError) -> Never:
        cause = error.cause
        if isinstance(cause, RuntimeRepositoryError):
            raise cause
        raise RuntimeServiceError(
            "E_SERVICE_PROGRESS_INFRASTRUCTURE",
            str(cause) or type(cause).__name__,
        ) from cause

    @staticmethod
    def _sample_fraction(sample: ProgressSample | None) -> float | None:
        return None if sample is None else sample.fraction

    def _prepare_manual(self, node_run: NodeRun, request: NodeExecutionRequest) -> None:
        try:
            handoff = self._runner.prepare_manual(request)
            runtime_handoff = self._runtime_handoff(node_run, handoff)
        except (RunnerError, RuntimeServiceError) as error:
            # prepare 尚未形成可持久化 handoff，不能伪造 automatic running 或无效 waiting 状态。
            if isinstance(error, RunnerError):
                reason = _failure_reason(error.reason, external_submission=False)
                log_path = (
                    str(error.stdout_log_path.parent) if error.stdout_log_path is not None else None
                )
            else:
                reason = FailureReason.EXECUTION_ERROR
                log_path = str(Path(node_run.work_dir) / "logs")
            self._repository.fail_node_run_before_start(
                node_run.node_run_id,
                failed_at=utc_now(),
                error=RuntimeFailure(reason=reason, message=str(error)[:4096]),
                log_path=log_path,
            )
            return
        self._repository.transition_node_run(
            node_run.node_run_id,
            NodeRunState.WAITING_EXTERNAL,
            occurred_at=runtime_handoff.created_at,
            external_handoff=runtime_handoff,
            log_path=str(Path(node_run.work_dir) / "logs"),
        )

    def _try_reuse(self, run: Run, node_run: NodeRun) -> bool:
        result = self._find_reusable_result(run, node_run)
        if result is None:
            return False
        self._repository.reuse_result(
            node_run.node_run_id,
            result.result_id,
            completed_at=utc_now(),
        )
        return True

    def _find_reusable_result(
        self, run: Run, node_run: NodeRun, *, persist_probe_stale: bool = True
    ) -> NodeResult | None:
        """共享正式复用候选规则；只读预览禁止把 probe 失败写回 latest。"""

        # attempt > 1 是操作者明确要求从头重跑的 closure；复用会悄悄撤销该意图。
        if node_run.attempt != 1:
            return None
        latest = self._repository.get_latest(node_run.node_id)
        if latest is None:
            return None
        if latest.updated_at <= run.created_at and latest.stale:
            return None

        candidates = self._repository.list_results_for_node(
            node_run.node_id,
            before=run.created_at,
        )
        if latest.updated_at <= run.created_at:
            # Run 启动时可见的 fresh head 是唯一 authority；不得回退到更旧结果绕过失效。
            candidates = tuple(
                result for result in candidates if result.result_id == latest.result_id
            )
        for result in candidates:
            if not self._candidate_matches(
                run, node_run, result, persist_probe_stale=persist_probe_stale
            ):
                continue
            return result
        return None

    def _candidate_matches(
        self,
        run: Run,
        node_run: NodeRun,
        result: NodeResult,
        *,
        persist_probe_stale: bool = True,
    ) -> bool:
        """按 active Run snapshot 检查一个在 Run 启动前完成的历史结果。"""

        source_node_run = self._repository.get_node_run(result.node_run_id)
        source_run = self._repository.get_run(source_node_run.run_id)
        current_node = next(
            item for item in run.graph_snapshot.nodes if item.node_id == node_run.node_id
        )
        source_node = next(
            (
                item
                for item in source_run.graph_snapshot.nodes
                if item.node_id == source_node_run.node_id
            ),
            None,
        )
        if source_node is None or source_node_run.node_id != node_run.node_id:
            return False
        current_definition = self._definition_for(run, current_node)
        source_definition = self._definition_for(source_run, source_node)
        expected_outputs = tuple(
            (port.port_id, port.data_type, None) for port in current_definition.output_ports
        )
        actual_outputs = tuple(
            (item.producer_port_id, item.kind, item.ordinal) for item in result.outputs
        )
        if source_definition != current_definition or actual_outputs != expected_outputs:
            return False
        current_signature = capture_node_signature(
            run.graph_snapshot,
            node_run.node_id,
            input_artifact_ids=node_run.input_artifact_ids,
        )
        previous_signature = capture_node_signature(
            source_run.graph_snapshot,
            source_node_run.node_id,
            input_artifact_ids=source_node_run.input_artifact_ids,
        )
        probe_results: dict[str, bool] = {}
        probe_reason: StaleReason | None = None
        for artifact in result.outputs:
            passed, reason = self._probe_artifact(artifact)
            probe_results[artifact.artifact_id] = passed
            if not passed and probe_reason is None:
                probe_reason = reason
        current_latest = self._repository.get_latest(node_run.node_id)
        if (
            persist_probe_stale
            and probe_reason is not None
            and current_latest is not None
            and current_latest.result_id == result.result_id
            and not current_latest.stale
        ):
            self._repository.mark_downstream_stale(
                node_run.node_id,
                probe_reason,
                updated_at=utc_now(),
                include_self=True,
            )

        decision = analyze_reuse(
            current_signature,
            ReuseCandidate(
                result_id=result.result_id,
                state=source_node_run.state.value,
                signature=previous_signature,
                output_artifact_ids=tuple(item.artifact_id for item in result.outputs),
                # global latest 在 Run 启动后的变化不属于 active snapshot authority。
                latest_stale=probe_reason is not None,
            ),
            output_quick_probe=probe_results,
        )
        return decision.reusable and decision.reused_result_id is not None

    def _execution_request(
        self,
        run: Run,
        node_run: NodeRun,
        *,
        resolved: _ResolvedInputs | None = None,
    ) -> NodeExecutionRequest:
        nodes = {node.node_id: node for node in run.graph_snapshot.nodes}
        node = nodes.get(node_run.node_id)
        if node is None:
            raise RuntimeServiceError(
                "E_SERVICE_SNAPSHOT_NODE_MISSING", f"Run snapshot 缺少 {node_run.node_id!r}"
            )
        definition = self._definition_for(run, node)
        inputs = resolved or self._resolve_bound_inputs(run, node_run)
        if inputs.artifact_ids != node_run.input_artifact_ids:
            raise RuntimeServiceError(
                "E_SERVICE_INPUT_BINDING_MISMATCH",
                "NodeRun input_artifact_ids 与 graph edge 解析结果不一致",
            )
        output_paths = tuple(
            OutputPathSpec(port_id=item.port_id, relative_path=item.relative_path)
            for item in definition.executor.output_paths
        )
        if node_run.external_handoff is not None:
            # 已交接的目标是持久身份绑定；后续命名策略不能重新解释历史收件位置。
            output_root = Path(node_run.work_dir) / "outputs"
            try:
                output_paths = tuple(
                    OutputPathSpec(
                        port_id=item.port_id,
                        relative_path=Path(item.path).relative_to(output_root).as_posix(),
                    )
                    for item in node_run.external_handoff.output_targets
                )
            except ValueError as error:
                raise RuntimeServiceError(
                    "E_SERVICE_HANDOFF_TARGET", "持久交接目标不在当前 attempt 输出目录内"
                ) from error
        elif self._output_path_resolver is not None:
            # 仅接受宿主代码注入的路径策略；Graph 不携带 callback，Runner 仍完整校验边界。
            output_paths = self._output_path_resolver(run, node, definition) or output_paths
        return NodeExecutionRequest(
            node_run_id=node_run.node_run_id,
            attempt=node_run.attempt,
            definition=definition,
            node=node,
            inputs=inputs.runner_inputs,
            output_paths=output_paths,
        )

    def _resolve_inputs(self, run: Run, node_id: str) -> _ResolvedInputs:
        signature = capture_node_signature(run.graph_snapshot, node_id)
        node = next(item for item in run.graph_snapshot.nodes if item.node_id == node_id)
        definition = self._definition_for(run, node)
        port_positions = {port.port_id: index for index, port in enumerate(definition.input_ports)}
        selected = self._selected_node_ids(run)
        latest = self._latest_attempts(run, selected)
        runner_inputs: list[RunnerInput] = []
        artifact_ids: list[str] = []
        for edge in signature.incoming_edges:
            source = latest.get(edge.source_node_id)
            if source is None or source.state is not NodeRunState.COMPLETED:
                raise RuntimeServiceError(
                    "E_SERVICE_INPUT_SOURCE_NOT_COMPLETED",
                    f"上游 {edge.source_node_id!r} 尚未 completed",
                )
            artifacts = tuple(
                self._repository.get_artifact(artifact_id)
                for artifact_id in source.output_artifact_ids
            )
            matches = tuple(
                artifact
                for artifact in artifacts
                if artifact.producer_port_id == edge.source_port_id
            )
            if len(matches) != 1:
                raise RuntimeServiceError(
                    "E_SERVICE_SOURCE_OUTPUT_AMBIGUOUS",
                    f"{edge.source_node_id}.{edge.source_port_id} 必须精确对应一个 Artifact",
                )
            artifact = matches[0]
            artifact_ids.append(artifact.artifact_id)
            runner_inputs.append(
                RunnerInput(
                    port_id=edge.target_port_id,
                    artifact_id=artifact.artifact_id,
                    kind=artifact.kind,
                    path=Path(artifact.path),
                    ordinal=edge.ordinal,
                    producer_node_run_id=artifact.producer_node_run_id,
                    producer_port_id=artifact.producer_port_id,
                    artifact_ordinal=artifact.ordinal,
                    frame_range=artifact.frame_range,
                    media_info=artifact.media_info,
                    size=artifact.size,
                    mtime_ns=artifact.mtime_ns,
                )
            )
        # NodeRun 绑定沿用 Graph canonical edge 顺序；Runner/Handoff 面向插件，使用
        # NodeDefinition 声明顺序，同一 ordered_many port 内再按 ordinal 排列。
        ordered_runner_inputs = tuple(
            sorted(
                runner_inputs,
                key=lambda item: (
                    port_positions[item.port_id],
                    -1 if item.ordinal is None else item.ordinal,
                ),
            )
        )
        return _ResolvedInputs(tuple(artifact_ids), ordered_runner_inputs)

    def _resolve_bound_inputs(self, run: Run, node_run: NodeRun) -> _ResolvedInputs:
        resolved = self._resolve_inputs(run, node_run.node_id)
        if resolved.artifact_ids != node_run.input_artifact_ids:
            raise RuntimeServiceError(
                "E_SERVICE_BOUND_INPUTS_INVALID",
                "持久化 input_artifact_ids 不再对应 Run snapshot 直接入边",
            )
        return resolved

    def _register_runner_result(self, node_run: NodeRun, result: RunnerResult) -> None:
        try:
            work_dir_matches = result.work_dir.resolve(strict=True) == Path(
                node_run.work_dir
            ).resolve(strict=True)
        except OSError as error:
            raise RuntimeServiceError("E_SERVICE_RUNNER_RESULT_WORKDIR", str(error)) from error
        if (
            result.node_run_id != node_run.node_run_id
            or result.attempt != node_run.attempt
            or not work_dir_matches
        ):
            raise RuntimeServiceError(
                "E_SERVICE_RUNNER_RESULT_BINDING",
                "RunnerResult 没有绑定当前 NodeRun attempt",
            )

        run = self._repository.get_run(node_run.run_id)
        node = next(
            (item for item in run.graph_snapshot.nodes if item.node_id == node_run.node_id),
            None,
        )
        if node is None:
            raise RuntimeServiceError(
                "E_SERVICE_SNAPSHOT_NODE_MISSING", f"Run snapshot 缺少 {node_run.node_id!r}"
            )
        definition = self._definition_for(run, node)
        expected_outputs = tuple(
            (port.port_id, port.data_type, None) for port in definition.output_ports
        )
        actual_outputs = tuple(
            (item.producer_port_id, item.kind, item.ordinal) for item in result.artifacts
        )
        if actual_outputs != expected_outputs:
            raise RuntimeServiceError(
                "E_SERVICE_RUNNER_OUTPUT_CONTRACT",
                "RunnerResult 必须按 NodeDefinition 精确返回每个声明 output",
            )
        try:
            persisted = NodeResult(
                result_id=result.result_id,
                node_run_id=result.node_run_id,
                outputs=tuple(self._artifact_from_runner(item) for item in result.artifacts),
                media_summary=_json_mapping(result.media_summary),
                validation_summary=_json_mapping(result.validation_summary),
                created_at=utc_now(),
            )
        except (ValidationError, TypeError, ValueError) as error:
            raise RuntimeServiceError("E_SERVICE_RUNNER_RESULT_INVALID", str(error)) from error
        self._repository.register_result(
            persisted,
            ended_at=utc_now(),
            exit_code=result.exit_code,
        )

    @staticmethod
    def _artifact_from_runner(value: RunnerArtifact) -> Artifact:
        return Artifact(
            artifact_id=value.artifact_id,
            kind=value.kind,
            path=str(value.path),
            producer_node_run_id=value.producer_node_run_id,
            producer_port_id=value.producer_port_id,
            ordinal=value.ordinal,
            frame_range=value.frame_range,
            media_info=_json_mapping(value.media_info),
            size=value.size,
            mtime_ns=value.mtime_ns,
        )

    def _fail_node_run(
        self,
        node_run: NodeRun,
        error: RunnerError,
        *,
        external_submission: bool = False,
        progress: float | None = None,
    ) -> NodeRun:
        reason = _failure_reason(error.reason, external_submission=external_submission)
        return self._repository.transition_node_run(
            node_run.node_run_id,
            NodeRunState.FAILED,
            occurred_at=utc_now(),
            error=RuntimeFailure(reason=reason, message=str(error)[:4096]),
            exit_code=error.exit_code,
            log_path=(
                str(error.stdout_log_path.parent)
                if error.stdout_log_path is not None
                else node_run.log_path
            ),
            progress=progress,
        )

    def _fail_service_node_run(
        self,
        node_run: NodeRun,
        error: RuntimeServiceError,
        *,
        reason: FailureReason,
        progress: float | None = None,
    ) -> NodeRun:
        """把成功登记前发现的 Service/Runner 合同错误收敛为当前 attempt 失败。"""

        return self._repository.transition_node_run(
            node_run.node_run_id,
            NodeRunState.FAILED,
            occurred_at=utc_now(),
            error=RuntimeFailure(reason=reason, message=str(error)[:4096]),
            log_path=node_run.log_path,
            progress=progress,
        )

    def _fail_progress_node_run(
        self,
        node_run: NodeRun,
        error: ProgressError,
        *,
        progress: float | None,
    ) -> NodeRun:
        """把非法 reporter sample 收敛为 execution_error，并原子保留此前可信 fraction。"""

        return self._repository.transition_node_run(
            node_run.node_run_id,
            NodeRunState.FAILED,
            occurred_at=utc_now(),
            error=RuntimeFailure(
                reason=FailureReason.EXECUTION_ERROR,
                message=str(error)[:4096],
            ),
            log_path=node_run.log_path,
            progress=progress,
        )

    def _runtime_handoff(
        self,
        node_run: NodeRun,
        handoff: ManualHandoff,
    ) -> ExternalHandoff:
        try:
            work_dir_matches = Path(handoff.work_dir).resolve(strict=True) == Path(
                node_run.work_dir
            ).resolve(strict=True)
        except OSError as error:
            raise RuntimeServiceError("E_SERVICE_HANDOFF_WORKDIR", str(error)) from error
        canonical_input_ids = tuple(
            item.artifact_id
            for item in sorted(
                handoff.inputs,
                key=lambda item: (
                    item.port_id,
                    -1 if item.ordinal is None else item.ordinal,
                ),
            )
        )
        if (
            handoff.node_run_id != node_run.node_run_id
            or handoff.attempt != node_run.attempt
            or not work_dir_matches
            or canonical_input_ids != node_run.input_artifact_ids
        ):
            raise RuntimeServiceError(
                "E_SERVICE_HANDOFF_BINDING", "Runner handoff 没有绑定当前 NodeRun attempt"
            )
        return ExternalHandoff(
            handoff_id=new_runtime_id(),
            node_run_id=node_run.node_run_id,
            input_artifact_ids=canonical_input_ids,
            output_targets=tuple(
                ExternalOutputTarget(port_id=item.port_id, path=item.path)
                for item in handoff.outputs
            ),
            instructions=handoff.instructions,
            created_at=utc_now(),
        )

    def _runner_handoff(
        self,
        run: Run,
        node_run: NodeRun,
        inputs: tuple[RunnerInput, ...],
    ) -> ManualHandoff:
        runtime_handoff = node_run.external_handoff
        if runtime_handoff is None:
            raise RuntimeServiceError("E_SERVICE_HANDOFF_MISSING", "NodeRun 缺少 handoff")
        resolved_input_ids = tuple(
            item.artifact_id
            for item in sorted(
                inputs,
                key=lambda item: (
                    item.port_id,
                    -1 if item.ordinal is None else item.ordinal,
                ),
            )
        )
        if (
            runtime_handoff.input_artifact_ids != node_run.input_artifact_ids
            or node_run.input_artifact_ids != resolved_input_ids
        ):
            raise RuntimeServiceError(
                "E_SERVICE_HANDOFF_INPUT_BINDING",
                "persisted handoff、NodeRun 与当前直接输入 Artifact 绑定不一致",
            )
        node = next(item for item in run.graph_snapshot.nodes if item.node_id == node_run.node_id)
        definition = self._definition_for(run, node)
        kinds = {port.port_id: port.data_type for port in definition.output_ports}
        try:
            outputs = tuple(
                HandoffOutput(
                    port_id=item.port_id,
                    kind=kinds[item.port_id],
                    path=item.path,
                )
                for item in runtime_handoff.output_targets
            )
        except KeyError as error:
            raise RuntimeServiceError(
                "E_SERVICE_HANDOFF_OUTPUT_UNKNOWN", f"handoff 引用未知 output {error.args[0]!r}"
            ) from error
        return ManualHandoff(
            schema_version=1,
            node_run_id=node_run.node_run_id,
            attempt=node_run.attempt,
            type_id=node.type_id,
            definition_version=node.definition_version,
            work_dir=node_run.work_dir,
            inputs=tuple(
                HandoffInput(
                    port_id=item.port_id,
                    artifact_id=item.artifact_id,
                    kind=item.kind,
                    path=str(item.path),
                    ordinal=item.ordinal,
                )
                for item in inputs
            ),
            outputs=outputs,
            instructions=runtime_handoff.instructions,
        )

    @staticmethod
    def _definition_for(run: Run, node: NodeInstance) -> NodeDefinition:
        matches = tuple(
            definition
            for definition in run.definitions_snapshot
            if definition.type_id == node.type_id and definition.version == node.definition_version
        )
        if len(matches) != 1:
            raise RuntimeServiceError(
                "E_SERVICE_DEFINITION_BINDING",
                f"{node.type_id}@{node.definition_version} 必须精确对应一个 definition",
            )
        return matches[0]

    @staticmethod
    def _selected_node_ids(run: Run) -> tuple[str, ...]:
        scheduler = Scheduler(run.graph_snapshot)
        if not run.selected_targets:
            return scheduler.topological_order
        return scheduler.ancestor_closure(run.selected_targets)

    @staticmethod
    def _latest_attempts(run: Run, selected: tuple[str, ...]) -> dict[str, NodeRun]:
        latest: dict[str, NodeRun] = {}
        selected_set = set(selected)
        for node_run in run.node_runs:
            if node_run.node_id not in selected_set:
                continue
            current = latest.get(node_run.node_id)
            if current is None or node_run.attempt > current.attempt:
                latest[node_run.node_id] = node_run
        missing = tuple(node_id for node_id in selected if node_id not in latest)
        if missing:
            raise RuntimeServiceError(
                "E_SERVICE_ATTEMPT_MISSING",
                "Run 执行闭包缺少 NodeRun：" + ", ".join(missing),
            )
        return latest

    def _attempt_work_dir(self, node_run_id: str) -> Path:
        """只用 UUID hex 派生 attempt 目录，不接受 node_id 或用户路径片段。"""

        parsed = UUID(node_run_id)
        if parsed.version != 4 or str(parsed) != node_run_id:
            raise RuntimeServiceError("E_SERVICE_NODE_RUN_ID_INVALID", "node_run_id 必须是 UUIDv4")
        return self._work_root / parsed.hex

    def _probe_artifact(self, artifact: Artifact) -> tuple[bool, StaleReason]:
        path = Path(artifact.path)
        try:
            if not path.is_file() or path.stat().st_size <= 0:
                return False, StaleReason.OUTPUT_MISSING
            probe_result = self._artifact_quick_probe(artifact)
            if type(probe_result) is not bool or probe_result is not True:
                return False, StaleReason.QUICK_PROBE_FAILED
        except OSError:
            return False, StaleReason.OUTPUT_MISSING
        except (SystemExit, GeneratorExit, Exception):
            return False, StaleReason.QUICK_PROBE_FAILED
        return True, StaleReason.QUICK_PROBE_FAILED


def _default_artifact_quick_probe(artifact: Artifact) -> bool:
    """以一次非空读取确认普通文件仍可读；媒体专用 probe 可由 Phase 4 调用方注入。"""

    with Path(artifact.path).open("rb") as stream:
        return bool(stream.read(1))


def _json_mapping(value: Mapping[str, object]) -> dict[str, JsonValue]:
    """保留 Runner 的普通 mapping，具体 JSON 合法性由严格 Runtime 模型再次验证。"""

    return cast(dict[str, JsonValue], dict(value))


def _failure_reason(
    reason: RunnerFailureReason,
    *,
    external_submission: bool,
) -> FailureReason:
    if reason is RunnerFailureReason.INTERRUPTED:
        return FailureReason.INTERRUPTED
    if reason is RunnerFailureReason.CANCELLED:
        return FailureReason.CANCELLED
    if external_submission:
        return FailureReason.EXTERNAL_SUBMISSION_INVALID
    if reason in {
        RunnerFailureReason.OUTPUT_INVALID,
        RunnerFailureReason.PROBE_FAILED,
        RunnerFailureReason.VALIDATOR_FAILED,
    }:
        return FailureReason.VALIDATION_FAILED
    return FailureReason.EXECUTION_ERROR


__all__ = ["ArtifactQuickProbe", "RuntimeService", "RuntimeServiceError"]
