"""实现单用户本地 Studio 的 0.2.1 Project Service application facade。

Facade 在一次 Project session 内只构造一个 ``RuntimeService``。mutation 在进程内串行，运行命令由
受控后台线程推进；status 只返回有界 Run summary，完整 Run、日志与 handoff readiness 通过精确身份
单独读取。所有读投影都来自 SQLite authority 和 server-declared path，不写回第二套状态。
"""

from __future__ import annotations

import base64
import json
import threading
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Final

from pydantic import ValidationError

from zniku.graph import ExecutionMode, NodeDefinition, PythonExecutorSpec
from zniku.project import Project, ProjectStore, ProjectStoreError
from zniku.runtime import (
    Artifact,
    ArtifactQuickProbe,
    ExternalOutputTarget,
    NodeRun,
    NodeRunState,
    PythonAdapter,
    Run,
    RunnerError,
    RunState,
    RuntimeConflictError,
    RuntimeNotFoundError,
    RuntimeRepositoryError,
    RuntimeService,
    RuntimeServiceError,
    Scheduler,
    utc_now,
)
from zniku.runtime.progress import MonotonicClock, WallClock
from zniku.runtime.runner import MediaProbe, NodeValidator

from .models import (
    AbandonRunCommand,
    ActiveProjectOperation,
    CreateProjectCommand,
    ExternalHandoffReadiness,
    ExternalOutputReadiness,
    NodeLogEnvelope,
    NodeLogProjection,
    NodeProgressProjection,
    OpenProjectCommand,
    ProjectServiceFailure,
    RerunFromHereCommand,
    RunAllCommand,
    RunDetailEnvelope,
    RunNodeStateCounts,
    RunSummary,
    RunSummaryPageEnvelope,
    RunToCommand,
    SaveProjectCommand,
    StatusEnvelope,
    SubmitExternalCommand,
    parse_project_service_command,
)

_LOG_TAIL_LIMIT: Final = 128 * 1024
_STATUS_TERMINAL_LIMIT: Final = 20


class ProjectServiceError(RuntimeError):
    """表示 Project Service 可安全返回给本地 Studio 的稳定失败。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int = 422,
        related_run_ids: tuple[str, ...] = (),
    ) -> None:
        self.code = code
        self.message = message[:4096] or "Project Service 失败"
        self.http_status = http_status
        self.related_run_ids = related_run_ids
        super().__init__(f"{code}: {self.message}")


class ProjectServiceApplication:
    """持有一个当前 Project session，并把 Studio intent 委托给正式领域服务。"""

    def __init__(
        self,
        *,
        work_root: str | Path,
        definition_catalog: Iterable[NodeDefinition] = (),
        python_adapters: Mapping[str, PythonAdapter] | None = None,
        validators: Mapping[str, NodeValidator] | None = None,
        media_probe: MediaProbe | None = None,
        artifact_quick_probe: ArtifactQuickProbe | None = None,
        progress_wall_clock: WallClock | None = None,
        progress_monotonic_clock: MonotonicClock | None = None,
    ) -> None:
        root = Path(work_root)
        try:
            root.mkdir(parents=True, exist_ok=True)
            self._work_root = root.resolve(strict=True)
        except OSError as error:
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_WORK_ROOT", str(error), http_status=400
            ) from error
        if not self._work_root.is_dir():
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_WORK_ROOT", "work_root 必须是目录", http_status=400
            )

        catalog = tuple(definition_catalog)
        if any(not isinstance(item, NodeDefinition) for item in catalog):
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_DEFINITION_CATALOG",
                "definition_catalog 只能包含 NodeDefinition",
                http_status=500,
            )
        keys = tuple((item.type_id, item.version) for item in catalog)
        if len(keys) != len(set(keys)):
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_DEFINITION_CATALOG",
                "definition_catalog 不得重复 type_id/version",
                http_status=500,
            )

        self._definition_catalog = catalog
        self._python_adapters = dict(python_adapters or {})
        self._validators = dict(validators or {})
        self._media_probe = media_probe
        self._artifact_quick_probe = artifact_quick_probe
        self._progress_wall_clock = progress_wall_clock
        self._progress_monotonic_clock = progress_monotonic_clock
        self._state = threading.Condition(threading.RLock())
        self._store: ProjectStore | None = None
        self._runtime: RuntimeService | None = None
        self._active_run_id: str | None = None
        self._active_operation: ActiveProjectOperation | None = None
        self._worker: threading.Thread | None = None
        self._last_error: ProjectServiceFailure | None = None

    @property
    def work_root(self) -> Path:
        """返回进程启动时固定的 attempt 根；command 不能替换它。"""

        return self._work_root

    def inspect(self, view_run_id: str | None = None) -> StatusEnvelope:
        """返回当前 Project、有限 RunSummary 与进程内后台 operation。"""

        store, runtime, active_run_id, active_operation, error = self._session_view()
        if store is None or runtime is None:
            if view_run_id is not None:
                raise ProjectServiceError(
                    "E_PROJECT_SERVICE_RUN_NOT_FOUND",
                    f"Run 不存在：{view_run_id}",
                    http_status=404,
                )
            return StatusEnvelope(error=error)

        try:
            snapshot = store.load()
            runs = runtime.repository.list_runs()
            summaries = tuple(self._summarize_run(run) for run in runs)
            by_id = {summary.run_id: summary for summary in summaries}
            if view_run_id is not None and view_run_id not in by_id:
                raise ProjectServiceError(
                    "E_PROJECT_SERVICE_RUN_NOT_FOUND",
                    f"Run 不存在：{view_run_id}",
                    http_status=404,
                )
            if active_run_id is not None and active_run_id not in by_id:
                active_run_id = None

            ordered = self._sort_summaries(summaries)
            terminal = tuple(summary for summary in ordered if not summary.actionable)
            terminal_window = terminal[:_STATUS_TERMINAL_LIMIT]
            included = {summary.run_id for summary in terminal_window}
            included.update(summary.run_id for summary in ordered if summary.actionable)
            if active_run_id is not None:
                included.add(active_run_id)
            if view_run_id is not None:
                included.add(view_run_id)
            projected = tuple(summary for summary in ordered if summary.run_id in included)
            next_cursor = (
                self._encode_cursor(terminal_window[-1])
                if len(terminal) > len(terminal_window) and terminal_window
                else None
            )
            return StatusEnvelope(
                project_path=str(store.path),
                snapshot=snapshot,
                run_summaries=projected,
                next_run_cursor=next_cursor,
                active_run_id=active_run_id,
                active_operation=active_operation,
                latest_results=runtime.repository.list_latest(),
                error=error,
            )
        except ProjectServiceError:
            raise
        except (ProjectStoreError, RuntimeRepositoryError, ValidationError) as failure:
            raise self._translate_failure(failure) from failure

    def list_run_summaries(
        self,
        cursor: str | None = None,
        limit: int = _STATUS_TERMINAL_LIMIT,
    ) -> RunSummaryPageEnvelope:
        """按稳定 opaque cursor 返回 terminal RunSummary 历史。"""

        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_LIMIT_INVALID",
                "limit 必须是 1..100 的 integer",
                http_status=400,
            )
        _, runtime = self._require_session()
        try:
            ordered = self._sort_summaries(
                tuple(self._summarize_run(run) for run in runtime.repository.list_runs())
            )
            terminal = tuple(summary for summary in ordered if not summary.actionable)
            start = 0
            if cursor is not None:
                identity = self._decode_cursor(cursor)
                positions = tuple(
                    index
                    for index, summary in enumerate(terminal)
                    if self._cursor_identity(summary) == identity
                )
                if len(positions) != 1:
                    raise ProjectServiceError(
                        "E_PROJECT_SERVICE_CURSOR_INVALID",
                        "cursor 不属于当前 terminal Run 历史",
                        http_status=400,
                    )
                start = positions[0] + 1
            page = terminal[start : start + limit]
            next_cursor = (
                self._encode_cursor(page[-1])
                if page and start + len(page) < len(terminal)
                else None
            )
            return RunSummaryPageEnvelope(
                run_summaries=page,
                next_run_cursor=next_cursor,
            )
        except ProjectServiceError:
            raise
        except (RuntimeRepositoryError, ValidationError) as failure:
            raise self._translate_failure(failure) from failure

    def inspect_run_detail(self, run_id: str) -> RunDetailEnvelope:
        """读取一个明确 Run 及其所有 input/output Artifact 引用闭包。"""

        _, runtime = self._require_session()
        try:
            run = runtime.repository.get_run(run_id)
            return RunDetailEnvelope(
                run=run,
                artifacts=self._collect_run_artifacts(run, runtime),
                progress_samples=self._project_progress(run, runtime),
            )
        except (RuntimeRepositoryError, ValidationError) as failure:
            raise self._translate_failure(failure) from failure

    def inspect_node_logs(self, run_id: str, node_run_id: str) -> NodeLogEnvelope:
        """读取精确 ``run_id + node_run_id`` 绑定的 128 KiB 日志尾。"""

        _, runtime = self._require_session()
        node_run = self._bound_node_run(runtime, run_id, node_run_id)
        return NodeLogEnvelope(run_id=run_id, log=self._project_logs(node_run))

    def inspect_external_readiness(
        self,
        *,
        run_id: str,
        node_run_id: str,
        probe: bool,
    ) -> ExternalHandoffReadiness:
        """只读检查 server-declared handoff targets；任何结果都不推进 Runtime。"""

        if type(probe) is not bool:
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_PROBE_INVALID",
                "probe 必须是 boolean",
                http_status=400,
            )
        _, runtime = self._require_session()
        try:
            node_run = runtime.inspect_external_handoff(run_id, node_run_id)
        except (RuntimeRepositoryError, RuntimeServiceError) as failure:
            raise self._translate_failure(failure) from failure
        handoff = node_run.external_handoff
        if handoff is None:  # Runtime 已经失败关闭；仅保留类型收窄。
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_HANDOFF_NOT_ACTIONABLE",
                "waiting NodeRun 缺少 handoff",
                http_status=409,
            )

        targets = [self._inspect_target(target) for target in handoff.output_targets]
        if probe:
            probe_candidates = [target for target in targets if target.state == "present"]
            if len(probe_candidates) == len(targets):
                try:
                    runtime.inspect_external_outputs(
                        run_id,
                        node_run_id,
                        handoff_id=handoff.handoff_id,
                    )
                except RunnerError as failure:
                    message = str(failure)[:4096]
                    targets = [
                        target.model_copy(update={"state": "probe_failed", "message": message})
                        for target in targets
                    ]
                except (RuntimeRepositoryError, RuntimeServiceError) as failure:
                    # 绑定、并发取代或持久化损坏不是媒体验收结果，必须 fail closed 给调用方。
                    raise self._translate_failure(failure) from failure
                else:
                    # validator 可能耗时；成功只代表刚才读取的文件内容有效，不能证明原 handoff
                    # 在返回瞬间仍是最新 waiting attempt。再次按完整 identity 复核，避免把已经
                    # superseded、submitted 或 abandoned 的 handoff 错报为 ready_for_submit。
                    try:
                        runtime.inspect_external_handoff(
                            run_id,
                            node_run_id,
                            handoff_id=handoff.handoff_id,
                        )
                    except (RuntimeRepositoryError, RuntimeServiceError) as failure:
                        raise self._translate_failure(failure) from failure
                    targets = [
                        target.model_copy(update={"state": "probe_passed", "message": None})
                        for target in targets
                    ]
            else:
                # 节点 validator 面向完整输出集合；部分目标缺失时不能对其余目标伪称整体通过。
                targets = [
                    target.model_copy(
                        update={
                            "state": "probe_failed",
                            "message": "handoff 其他 target 尚未就绪，未执行完整节点 validator",
                        }
                    )
                    if target.state == "present"
                    else target
                    for target in targets
                ]

        values = tuple(targets)
        return ExternalHandoffReadiness(
            run_id=run_id,
            node_run_id=node_run_id,
            handoff_id=handoff.handoff_id,
            checked_at=utc_now(),
            probe_requested=probe,
            ready_for_submit=probe and all(target.state == "probe_passed" for target in values),
            targets=values,
        )

    def command(self, payload: object) -> StatusEnvelope:
        """严格解析并执行一个 Studio command；成功返回 fresh status。"""

        try:
            command = parse_project_service_command(payload)
        except ValidationError as error:
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_COMMAND_INVALID", str(error), http_status=422
            ) from error

        with self._state:
            if isinstance(command, AbandonRunCommand) and self._active_operation is not None:
                _, runtime = self._require_session()
                try:
                    active_run = runtime.repository.get_run(command.run_id)
                except RuntimeRepositoryError as failure:
                    raise self._translate_failure(failure) from failure
                if any(node_run.state is NodeRunState.RUNNING for node_run in active_run.node_runs):
                    raise ProjectServiceError(
                        "E_PROJECT_SERVICE_RUN_ACTIVE",
                        "存在 automatic running attempt，不能 abandon",
                        http_status=409,
                        related_run_ids=(active_run.run_id,),
                    )
            self._assert_idle()
            self._last_error = None
            try:
                if isinstance(command, OpenProjectCommand):
                    self._open(command)
                elif isinstance(command, CreateProjectCommand):
                    self._create(command)
                elif isinstance(command, SaveProjectCommand):
                    self._save(command)
                elif isinstance(command, RunAllCommand | RunToCommand):
                    self._start_run(command)
                elif isinstance(command, RerunFromHereCommand):
                    self._rerun(command)
                elif isinstance(command, SubmitExternalCommand):
                    self._submit_external(command)
                elif isinstance(command, AbandonRunCommand):
                    self._abandon(command)
                else:  # pragma: no cover - discriminated union 的防御性封闭分支
                    raise ProjectServiceError(
                        "E_PROJECT_SERVICE_OPERATION", "未知 operation", http_status=422
                    )
            except ProjectServiceError:
                raise
            except (ProjectStoreError, RuntimeRepositoryError, RuntimeServiceError) as failure:
                translated = self._translate_failure(failure)
                self._last_error = ProjectServiceFailure(
                    code=translated.code,
                    message=translated.message,
                    related_run_ids=translated.related_run_ids,
                )
                raise translated from failure
        return self.inspect()

    def wait_until_idle(self, *, timeout: float = 10.0) -> bool:
        """供测试与受控 launcher 等待后台 command；产品 UI 应轮询 ``inspect``。"""

        deadline = monotonic() + timeout
        with self._state:
            while self._active_operation is not None:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return False
                self._state.wait(remaining)
            return True

    def _open(self, command: OpenProjectCommand) -> None:
        path = self._resolve_open_path(command.path)
        store = ProjectStore.open(path)
        runtime = self._runtime_for(store)
        self._store = store
        self._runtime = runtime
        self._active_run_id = None

    def _create(self, command: CreateProjectCommand) -> None:
        path = self._resolve_create_path(command.path)
        try:
            project = Project.model_validate(
                {
                    "project_id": command.project_id,
                    "name": command.name,
                    "graph": {"nodes": [], "edges": []},
                },
                strict=True,
            )
        except ValidationError as error:
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_PROJECT_INVALID", str(error), http_status=422
            ) from error
        store = ProjectStore.create(path, project, self._definition_catalog)
        self._store = store
        self._runtime = self._runtime_for(store)
        self._active_run_id = None

    def _save(self, command: SaveProjectCommand) -> None:
        store, _ = self._require_session()
        snapshot = store.load()
        store.save(command.project, snapshot.definitions)

    def _start_run(self, command: RunAllCommand | RunToCommand) -> None:
        _, runtime = self._require_session()
        conflicts = tuple(
            run
            for run in self._sort_runs(runtime.repository.list_runs())
            if run.state in {RunState.PENDING, RunState.RUNNING}
        )
        if conflicts:
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_RUN_CONFLICT",
                "当前 Project 已有非终态 Run；请继续或先放弃该 Run",
                http_status=409,
                related_run_ids=tuple(run.run_id for run in conflicts),
            )
        selected = () if isinstance(command, RunAllCommand) else (command.node_id,)
        run = runtime.create_run(selected_targets=selected)
        operation: ActiveProjectOperation = (
            "run_all" if isinstance(command, RunAllCommand) else "run_to"
        )
        self._begin_worker(operation, run.run_id, lambda: runtime.run_until_blocked(run.run_id))

    def _rerun(self, command: RerunFromHereCommand) -> None:
        store, runtime = self._require_session()
        run = runtime.repository.get_run(command.run_id)
        if command.node_id not in {item.node_id for item in run.node_runs}:
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_RERUN_NODE_OUTSIDE_RUN",
                f"节点 {command.node_id!r} 不属于引用 Run 的执行闭包",
                http_status=409,
            )
        current = store.load()
        snapshot_still_current = (
            run.graph_snapshot == current.project.graph
            and run.definitions_snapshot == current.definitions
        )
        if run.state is RunState.RUNNING and snapshot_still_current:
            run_id = run.run_id
            self._begin_worker(
                "rerun_from_here",
                run_id,
                lambda: runtime.rerun_from_start(run_id, command.node_id),
            )
            return
        replacement = runtime.create_rerun_run(command.node_id)
        run_id = replacement.run_id
        self._begin_worker("rerun_from_here", run_id, lambda: runtime.run_until_blocked(run_id))

    def _submit_external(self, command: SubmitExternalCommand) -> None:
        _, runtime = self._require_session()
        node_run = runtime.inspect_external_handoff(
            command.run_id,
            command.node_run_id,
            handoff_id=command.handoff_id,
        )
        self._begin_worker(
            "submit_external",
            node_run.run_id,
            lambda: runtime.submit_external(
                command.node_run_id,
                run_id=command.run_id,
                handoff_id=command.handoff_id,
            ),
        )

    def _abandon(self, command: AbandonRunCommand) -> None:
        _, runtime = self._require_session()
        runtime.abandon_run(command.run_id)
        self._active_run_id = command.run_id

    def _begin_worker(
        self,
        operation: ActiveProjectOperation,
        run_id: str,
        work: Callable[[], object],
    ) -> None:
        self._active_run_id = run_id
        self._active_operation = operation

        def target() -> None:
            failure: ProjectServiceFailure | None = None
            try:
                work()
            except (ProjectStoreError, RuntimeRepositoryError, RuntimeServiceError) as error:
                translated = self._translate_failure(error)
                failure = ProjectServiceFailure(
                    code=translated.code,
                    message=translated.message,
                    related_run_ids=translated.related_run_ids,
                )
            except Exception as error:
                failure = ProjectServiceFailure(
                    code="E_PROJECT_SERVICE_BACKGROUND",
                    message=(str(error) or type(error).__name__)[:4096],
                )
            finally:
                with self._state:
                    self._last_error = failure
                    self._active_operation = None
                    self._worker = None
                    self._state.notify_all()

        worker = threading.Thread(
            target=target,
            name=f"zniku-{operation}-{run_id[:8]}",
            daemon=True,
        )
        self._worker = worker
        worker.start()

    def _runtime_for(self, store: ProjectStore) -> RuntimeService:
        return RuntimeService(
            store,
            self._work_root,
            python_adapters=self._python_adapters,
            validators=self._validators,
            media_probe=self._media_probe,
            artifact_quick_probe=self._artifact_quick_probe,
            progress_wall_clock=self._progress_wall_clock,
            progress_monotonic_clock=self._progress_monotonic_clock,
        )

    def _session_view(
        self,
    ) -> tuple[
        ProjectStore | None,
        RuntimeService | None,
        str | None,
        ActiveProjectOperation | None,
        ProjectServiceFailure | None,
    ]:
        with self._state:
            return (
                self._store,
                self._runtime,
                self._active_run_id,
                self._active_operation,
                self._last_error,
            )

    def _require_session(self) -> tuple[ProjectStore, RuntimeService]:
        if self._store is None or self._runtime is None:
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_NO_PROJECT", "尚未打开 .zniku Project", http_status=409
            )
        return self._store, self._runtime

    def _assert_idle(self) -> None:
        if self._active_operation is not None:
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_BUSY",
                f"Project Service 正在执行 {self._active_operation}",
                http_status=409,
                related_run_ids=(self._active_run_id,) if self._active_run_id else (),
            )

    @staticmethod
    def _resolve_open_path(value: str) -> Path:
        if "\x00" in value:
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_PATH", "Project 路径不得包含 NUL", http_status=400
            )
        try:
            return Path(value).resolve(strict=True)
        except OSError as error:
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_PATH", str(error), http_status=400
            ) from error

    @staticmethod
    def _resolve_create_path(value: str) -> Path:
        if "\x00" in value:
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_PATH", "Project 路径不得包含 NUL", http_status=400
            )
        raw = Path(value)
        try:
            parent = raw.parent.resolve(strict=True)
        except OSError as error:
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_PATH", str(error), http_status=400
            ) from error
        return parent / raw.name

    def _project_logs(self, node_run: NodeRun) -> NodeLogProjection:
        stdout, stdout_truncated, stdout_available = self._read_log(node_run, "stdout.log")
        stderr, stderr_truncated, stderr_available = self._read_log(node_run, "stderr.log")
        return NodeLogProjection(
            node_run_id=node_run.node_run_id,
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            stdout_available=stdout_available,
            stderr_available=stderr_available,
        )

    def _read_log(self, node_run: NodeRun, filename: str) -> tuple[str, bool, bool]:
        if node_run.log_path is None:
            return "", False, False
        try:
            root = self._work_root.resolve(strict=True)
            work_dir = Path(node_run.work_dir).resolve(strict=True)
            work_dir.relative_to(root)
            log_dir = Path(node_run.log_path).resolve(strict=True)
            log_dir.relative_to(work_dir)
            if log_dir != (work_dir / "logs").resolve(strict=True):
                return "", False, False
            target = (log_dir / filename).resolve(strict=True)
            target.relative_to(log_dir)
            if not target.is_file():
                return "", False, False
            size = target.stat().st_size
            with target.open("rb") as stream:
                if size > _LOG_TAIL_LIMIT:
                    stream.seek(-_LOG_TAIL_LIMIT, 2)
                payload = stream.read(_LOG_TAIL_LIMIT)
            return payload.decode("utf-8", errors="replace"), size > _LOG_TAIL_LIMIT, True
        except (OSError, ValueError):
            return "", False, False

    @staticmethod
    def _collect_run_artifacts(run: Run, runtime: RuntimeService) -> tuple[Artifact, ...]:
        collected: list[Artifact] = []
        seen: set[str] = set()
        for node_run in run.node_runs:
            for artifact_id in (*node_run.input_artifact_ids, *node_run.output_artifact_ids):
                if artifact_id in seen:
                    continue
                collected.append(runtime.repository.get_artifact(artifact_id))
                seen.add(artifact_id)
        return tuple(collected)

    @staticmethod
    def _project_progress(
        run: Run,
        runtime: RuntimeService,
    ) -> tuple[NodeProgressProjection, ...]:
        """把 Runtime 进程投影精确绑定到捕获 Run 的最新 running automatic attempts。"""

        samples = {sample.node_run_id: sample for sample in runtime.progress_snapshot(run.run_id)}
        latest: dict[str, NodeRun] = {}
        for historical in run.node_runs:
            previous = latest.get(historical.node_id)
            if previous is None or historical.attempt > previous.attempt:
                latest[historical.node_id] = historical
        nodes = {node.node_id: node for node in run.graph_snapshot.nodes}
        definitions = {
            (definition.type_id, definition.version): definition
            for definition in run.definitions_snapshot
        }
        projected: list[NodeProgressProjection] = []
        for node_id in Scheduler(run.graph_snapshot).topological_order:
            latest_run = latest.get(node_id)
            if latest_run is None or latest_run.state is not NodeRunState.RUNNING:
                continue
            node = nodes[node_id]
            definition = definitions.get((node.type_id, node.definition_version))
            if definition is None:
                raise RuntimeRepositoryError(
                    "E_PROGRESS_DEFINITION_MISSING",
                    "Run progress 投影无法解析 exact NodeDefinition",
                )
            if definition.execution_mode is not ExecutionMode.AUTOMATIC or not isinstance(
                definition.executor, PythonExecutorSpec
            ):
                continue
            sample = samples.get(latest_run.node_run_id)
            if sample is None:
                continue
            if (
                sample.run_id != run.run_id
                or sample.attempt != latest_run.attempt
                or (latest_run.progress is not None and sample.fraction < latest_run.progress)
            ):
                raise RuntimeRepositoryError(
                    "E_PROGRESS_PROJECTION_BINDING",
                    "进程 progress sample 低于持久 authority 或绑定错误",
                )
            projected.append(
                NodeProgressProjection(
                    node_run_id=latest_run.node_run_id,
                    fraction=sample.fraction,
                    current=sample.current,
                    total=sample.total,
                    unit=sample.unit,
                    observed_at=sample.observed_at,
                )
            )
        return tuple(projected)

    @staticmethod
    def _summarize_run(run: Run) -> RunSummary:
        selected = (
            Scheduler(run.graph_snapshot).topological_order
            if not run.selected_targets
            else Scheduler(run.graph_snapshot).ancestor_closure(run.selected_targets)
        )
        latest: dict[str, NodeRun] = {}
        for node_run in run.node_runs:
            if node_run.node_id not in selected:
                raise RuntimeRepositoryError(
                    "E_RUN_SUMMARY_NODE_OUTSIDE_CLOSURE",
                    "Run 包含执行闭包之外的 NodeRun",
                )
            previous = latest.get(node_run.node_id)
            if previous is None or node_run.attempt > previous.attempt:
                latest[node_run.node_id] = node_run

        if run.state is RunState.PENDING:
            if latest:
                raise RuntimeRepositoryError(
                    "E_RUN_SUMMARY_PENDING_ATTEMPTS",
                    "pending Run 不得已有 attempt",
                )
            state_values = [NodeRunState.PENDING for _ in selected]
        else:
            missing = tuple(node_id for node_id in selected if node_id not in latest)
            if missing:
                raise RuntimeRepositoryError(
                    "E_RUN_SUMMARY_ATTEMPT_MISSING",
                    "非 pending Run 缺少 attempt：" + ", ".join(missing),
                )
            state_values = [latest[node_id].state for node_id in selected]

        counts = RunNodeStateCounts(
            pending=state_values.count(NodeRunState.PENDING),
            running=state_values.count(NodeRunState.RUNNING),
            waiting_external=state_values.count(NodeRunState.WAITING_EXTERNAL),
            completed=state_values.count(NodeRunState.COMPLETED),
            failed=state_values.count(NodeRunState.FAILED),
        )
        activity: list[datetime] = [run.created_at]
        activity.extend(value for value in (run.started_at, run.ended_at) if value is not None)
        for node_run in run.node_runs:
            activity.append(node_run.created_at)
            activity.extend(
                value for value in (node_run.started_at, node_run.ended_at) if value is not None
            )
        return RunSummary(
            run_id=run.run_id,
            project_id=run.project_id,
            target_mode="all" if not run.selected_targets else "selected",
            selected_targets=run.selected_targets,
            state=run.state.value,
            node_count=len(selected),
            state_counts=counts,
            actionable=run.state in {RunState.PENDING, RunState.RUNNING},
            requires_operator_action=(
                run.state is RunState.FAILED or counts.waiting_external > 0 or counts.failed > 0
            ),
            created_at=run.created_at,
            started_at=run.started_at,
            ended_at=run.ended_at,
            latest_activity_at=max(activity),
            error=run.error,
        )

    @staticmethod
    def _sort_summaries(values: tuple[RunSummary, ...]) -> tuple[RunSummary, ...]:
        return tuple(sorted(values, key=lambda item: (item.created_at, item.run_id), reverse=True))

    @staticmethod
    def _sort_runs(values: tuple[Run, ...]) -> tuple[Run, ...]:
        return tuple(sorted(values, key=lambda item: (item.created_at, item.run_id), reverse=True))

    @staticmethod
    def _cursor_identity(summary: RunSummary) -> tuple[str, str]:
        return summary.created_at.isoformat(), summary.run_id

    @classmethod
    def _encode_cursor(cls, summary: RunSummary) -> str:
        payload = json.dumps(
            cls._cursor_identity(summary),
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("ascii")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @classmethod
    def _decode_cursor(cls, cursor: str) -> tuple[str, str]:
        if not cursor or len(cursor) > 4096 or cursor.strip() != cursor:
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_CURSOR_INVALID", "cursor 格式无效", http_status=400
            )
        try:
            padding = "=" * (-len(cursor) % 4)
            payload = base64.b64decode(
                cursor + padding,
                altchars=b"-_",
                validate=True,
            )
            value = json.loads(payload.decode("ascii"))
            if (
                not isinstance(value, list)
                or len(value) != 2
                or any(not isinstance(item, str) or not item for item in value)
            ):
                raise ValueError("cursor payload 必须是两个非空 string")
            identity = (value[0], value[1])
            canonical = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
            if canonical != cursor:
                raise ValueError("cursor 不是 canonical encoding")
            return identity
        except (UnicodeError, ValueError, json.JSONDecodeError) as error:
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_CURSOR_INVALID", "cursor 格式无效", http_status=400
            ) from error

    @staticmethod
    def _inspect_target(target: ExternalOutputTarget) -> ExternalOutputReadiness:
        # ExternalOutputTarget 是严格 Runtime 模型；避免客户端路径进入该分支。
        port_id = target.port_id
        ordinal = target.ordinal
        path_value = target.path
        path = Path(path_value)
        try:
            stat = path.stat()
        except FileNotFoundError:
            return ExternalOutputReadiness(
                port_id=port_id,
                ordinal=ordinal,
                path=path_value,
                state="missing",
            )
        except OSError as error:
            return ExternalOutputReadiness(
                port_id=port_id,
                ordinal=ordinal,
                path=path_value,
                state="probe_failed",
                message=(str(error) or type(error).__name__)[:4096],
            )
        if not path.is_file():
            return ExternalOutputReadiness(
                port_id=port_id,
                ordinal=ordinal,
                path=path_value,
                state="probe_failed",
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                message="handoff target 不是普通文件",
            )
        if stat.st_size == 0:
            return ExternalOutputReadiness(
                port_id=port_id,
                ordinal=ordinal,
                path=path_value,
                state="empty",
                size=0,
                mtime_ns=stat.st_mtime_ns,
            )
        return ExternalOutputReadiness(
            port_id=port_id,
            ordinal=ordinal,
            path=path_value,
            state="present",
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
        )

    @staticmethod
    def _bound_node_run(runtime: RuntimeService, run_id: str, node_run_id: str) -> NodeRun:
        try:
            run = runtime.repository.get_run(run_id)
            node_run = runtime.repository.get_node_run(node_run_id)
        except RuntimeRepositoryError as error:
            raise ProjectServiceApplication._translate_failure(error) from error
        if node_run.run_id != run.run_id:
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_NODE_RUN_OUTSIDE_RUN",
                "NodeRun 不属于声明的 Run",
                http_status=409,
            )
        return node_run

    @staticmethod
    def _translate_failure(error: Exception) -> ProjectServiceError:
        raw_code = str(getattr(error, "code", "E_PROJECT_SERVICE_FAILURE"))
        code_map = {
            "E_RUN_NOT_FOUND": "E_PROJECT_SERVICE_RUN_NOT_FOUND",
            "E_NODE_RUN_NOT_FOUND": "E_PROJECT_SERVICE_NODE_RUN_NOT_FOUND",
            "E_SERVICE_NODE_RUN_OUTSIDE_RUN": "E_PROJECT_SERVICE_NODE_RUN_OUTSIDE_RUN",
            "E_SERVICE_HANDOFF_SUPERSEDED": "E_PROJECT_SERVICE_HANDOFF_NOT_ACTIONABLE",
            "E_SERVICE_HANDOFF_STATE": "E_PROJECT_SERVICE_HANDOFF_NOT_ACTIONABLE",
            "E_SERVICE_HANDOFF_MISSING": "E_PROJECT_SERVICE_HANDOFF_NOT_ACTIONABLE",
            "E_SERVICE_HANDOFF_STALE": "E_PROJECT_SERVICE_HANDOFF_STALE",
            "E_RUN_ABANDON_ACTIVE": "E_PROJECT_SERVICE_RUN_ACTIVE",
            "E_RUN_ABANDON_TERMINAL": "E_PROJECT_SERVICE_RUN_NOT_ACTIONABLE",
        }
        code = code_map.get(raw_code, raw_code)
        if isinstance(error, RuntimeNotFoundError):
            status = 404
        elif isinstance(error, RuntimeConflictError | RuntimeServiceError):
            status = 409
        elif isinstance(error, ProjectStoreError):
            status = 422
        else:
            status = 500
        return ProjectServiceError(code, str(error), http_status=status)


__all__ = ["ProjectServiceApplication", "ProjectServiceError"]
