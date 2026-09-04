"""实现单用户本地 Studio 的 0.3.0 Project Service application facade。

Facade 在一次 Project session 内只构造一个 ``RuntimeService``。mutation 在进程内串行，运行命令由
受控后台线程推进；status 只返回有界 Run summary，完整 Run、日志与 handoff readiness 通过精确身份
单独读取。所有读投影都来自 SQLite authority 和 server-declared path，不写回第二套状态。
"""

from __future__ import annotations

import base64
import json
import stat
import threading
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Final, cast

from pydantic import ValidationError

from zniku.avenhance_v27.definitions import (
    MOSAIC_RESTORATION_TYPE_ID,
    SOURCE_ADMISSION_TYPE_ID,
    SOURCE_PROGRAM_TYPE_ID,
)
from zniku.avenhance_v27.preflight import (
    Av27BindingFacts,
    Av27PublicationFacts,
    Av27SourceBindingFact,
    preflight_av27_profile,
)
from zniku.avenhance_v27.probe import (
    Av27MediaError,
    canonical_fraction,
    metadata_frame_count,
    metadata_rate,
)
from zniku.avenhance_v27.template import (
    Av27TemplateError,
    ExpandRequest,
    PreparationBinding,
    PreparationSourceBinding,
    SourceMode,
    TemplateBuild,
    build_expanded,
    build_preparation,
    validate_prepare_paths,
)
from zniku.graph import ExecutionMode, Graph, NodeDefinition, NodeInstance, PythonExecutorSpec
from zniku.presentation import (
    PresentationCatalogError,
    PresentationCatalogResolution,
    resolve_presentation_catalog,
)
from zniku.project import Project, ProjectSnapshot, ProjectStore, ProjectStoreError
from zniku.runtime import (
    Artifact,
    ArtifactQuickProbe,
    ExternalOutputTarget,
    NodeResult,
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

from .av27_handoff import project_av27_handoff_contracts
from .models import (
    AbandonRunCommand,
    ActiveProjectOperation,
    CreateAvEnhanceV27Command,
    CreateProjectCommand,
    ExpandAvEnhanceV27Command,
    ExternalHandoffReadiness,
    ExternalOutputReadiness,
    NodeLogEnvelope,
    NodeLogProjection,
    NodeProgressProjection,
    OpenProjectCommand,
    PresentationCatalogEnvelope,
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
    TemplatePreviewEnvelope,
    parse_project_service_command,
    parse_template_preview_request,
)

_LOG_TAIL_LIMIT: Final = 128 * 1024
_STATUS_TERMINAL_LIMIT: Final = 20
_AV27_PREPARATION_TYPES: Final = frozenset(
    {
        SOURCE_PROGRAM_TYPE_ID,
        SOURCE_ADMISSION_TYPE_ID,
        MOSAIC_RESTORATION_TYPE_ID,
    }
)


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
        third_party_presentation_catalogs: Iterable[object] = (),
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

        third_party_presentations = tuple(third_party_presentation_catalogs)
        try:
            presentation_resolution = resolve_presentation_catalog(
                catalog,
                third_party_catalogs=third_party_presentations,
            )
        except PresentationCatalogError as error:
            raise ProjectServiceError(
                "E_PROJECT_SERVICE_PRESENTATION_CATALOG",
                error.message,
                http_status=500,
            ) from error

        self._definition_catalog = catalog
        self._third_party_presentation_catalogs = third_party_presentations
        self._presentation_resolution = presentation_resolution
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

    def inspect_presentations(self) -> PresentationCatalogEnvelope:
        """返回与启动目录及当前 Project definitions 精确绑定的独立展示目录。

        Project 展开动态 AtomicSplit 后必须按实际 ``NodeDefinition`` 重新投影端口，不能从 type_id
        猜测 leaf 数量。读取和绑定过程不写 Project，也不接触 Runtime reuse/stale 状态。
        """

        with self._state:
            store = self._store
            if store is None:
                resolution = self._presentation_resolution
            else:
                try:
                    snapshot = store.load()
                    definitions = list(self._definition_catalog)
                    known = {
                        (definition.type_id, definition.version): definition
                        for definition in definitions
                    }
                    for definition in snapshot.definitions:
                        key = (definition.type_id, definition.version)
                        existing = known.get(key)
                        if existing is not None and existing != definition:
                            raise PresentationCatalogError(
                                "E_PRESENTATION_DEFINITION_CONFLICT",
                                "当前 Project definition 与启动 catalog 的 exact identity 结构冲突",
                            )
                        if existing is None:
                            definitions.append(definition)
                            known[key] = definition
                    resolution = resolve_presentation_catalog(
                        definitions,
                        third_party_catalogs=self._third_party_presentation_catalogs,
                    )
                except PresentationCatalogError as error:
                    raise ProjectServiceError(
                        "E_PROJECT_SERVICE_PRESENTATION_CATALOG",
                        error.message,
                        http_status=500,
                    ) from error
                except (ProjectStoreError, ValidationError) as error:
                    raise self._translate_failure(error) from error
        return self._presentation_envelope(resolution)

    @staticmethod
    def _presentation_envelope(
        resolution: PresentationCatalogResolution,
    ) -> PresentationCatalogEnvelope:
        """把内部绑定结果包装为 exact v0.3.0 只读 wire envelope。"""

        return PresentationCatalogEnvelope(
            catalog=resolution.catalog,
            diagnostics=resolution.diagnostics,
        )

    def preview_av_enhance_v27(self, payload: object) -> TemplatePreviewEnvelope:
        """无副作用地重算 AVEnhanceFlow v2.7 Graph、计划与 profile preflight。"""

        try:
            request = parse_template_preview_request(payload)
        except ValidationError as error:
            raise ProjectServiceError(
                "E_AV27_TEMPLATE_REQUEST_INVALID",
                str(error),
                http_status=422,
            ) from error

        with self._state:
            self._assert_idle()
            try:
                if request.action == "prepare":
                    build = build_preparation(request.request)
                    return self._template_envelope(build)
                current, binding = self._resolve_av27_preparation(request.request)
                build = build_expanded(current, request.request, binding)
                return self._template_envelope(
                    build,
                    binding_facts=self._binding_facts(binding),
                    publication_facts=self._publication_facts(
                        build.project,
                        output_root=request.request.publication.output_root,
                    ),
                )
            except ProjectServiceError:
                raise
            except (Av27TemplateError, Av27MediaError) as error:
                raise self._translate_av27_failure(error) from error
            except (ProjectStoreError, RuntimeRepositoryError, ValidationError) as error:
                raise self._translate_failure(error) from error

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
            artifacts = self._collect_run_artifacts(run, runtime)
            return RunDetailEnvelope(
                run=run,
                artifacts=artifacts,
                progress_samples=self._project_progress(run, runtime),
                handoff_contracts=project_av27_handoff_contracts(run, artifacts),
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
            operation = payload.get("operation") if isinstance(payload, Mapping) else None
            code = (
                "E_AV27_TEMPLATE_REQUEST_INVALID"
                if operation in {"create_av_enhance_v27", "expand_av_enhance_v27"}
                else "E_PROJECT_SERVICE_COMMAND_INVALID"
            )
            raise ProjectServiceError(code, str(error), http_status=422) from error

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
                elif isinstance(command, CreateAvEnhanceV27Command):
                    self._create_av_enhance_v27(command)
                elif isinstance(command, ExpandAvEnhanceV27Command):
                    self._expand_av_enhance_v27(command)
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
            except (Av27TemplateError, Av27MediaError) as failure:
                translated = self._translate_av27_failure(failure)
                self._last_error = ProjectServiceFailure(
                    code=translated.code,
                    message=translated.message,
                    related_run_ids=translated.related_run_ids,
                )
                raise translated from failure
            except (
                ProjectStoreError,
                RuntimeRepositoryError,
                RuntimeServiceError,
                ValidationError,
            ) as failure:
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

    def _create_av_enhance_v27(self, command: CreateAvEnhanceV27Command) -> None:
        """在完整 builder/Core/profile 验证后 no-replace 原子发布 preparation Project。"""

        build = build_preparation(command.request)
        envelope = self._template_envelope(build)
        if not envelope.profile.compatible:
            raise ProjectServiceError(
                "E_AV27_CREATE_PREFLIGHT",
                self._profile_failure_message(envelope),
                http_status=422,
            )
        project_path, _ = validate_prepare_paths(command.request)
        store = ProjectStore.create_atomically(
            project_path,
            build.project,
            build.definitions,
        )
        self._store = store
        self._runtime = self._runtime_for(store)
        self._active_run_id = None

    def _expand_av_enhance_v27(self, command: ExpandAvEnhanceV27Command) -> None:
        """重验显式 Run binding 后，以单个 ProjectStore transaction 替换 expansion 子图。"""

        store, _ = self._require_session()
        current, binding = self._resolve_av27_preparation(command.request)
        facts = self._binding_facts(binding)
        build = build_expanded(current, command.request, binding)
        envelope = self._template_envelope(
            build,
            binding_facts=facts,
            publication_facts=self._publication_facts(
                build.project,
                output_root=command.request.publication.output_root,
            ),
        )
        if not envelope.profile.compatible:
            raise ProjectServiceError(
                "E_AV27_EXPAND_PREFLIGHT",
                self._profile_failure_message(envelope),
                http_status=422,
            )
        store.save(build.project, build.definitions)

    @staticmethod
    def _template_envelope(
        build: TemplateBuild,
        *,
        binding_facts: Av27BindingFacts | None = None,
        publication_facts: Av27PublicationFacts | None = None,
    ) -> TemplatePreviewEnvelope:
        """把纯 builder 输出和纯 profile preflight 合并为唯一 preview wire。"""

        snapshot = ProjectSnapshot(project=build.project, definitions=build.definitions)
        profile = preflight_av27_profile(
            snapshot,
            binding_facts=binding_facts,
            publication_facts=publication_facts,
        )
        return TemplatePreviewEnvelope(
            phase=build.phase,
            project=build.project,
            definitions=build.definitions,
            profile=profile,
            plan=build.plan,
        )

    @staticmethod
    def _binding_facts(binding: PreparationBinding) -> Av27BindingFacts:
        """把已经逐项验证为 current 的 Repository binding 投影给纯 preflight。"""

        return Av27BindingFacts(
            preparation_snapshot_current=True,
            preparation_results_current=True,
            admission_artifact_id=binding.admission_artifact.artifact_id,
            sources=tuple(
                Av27SourceBindingFact(
                    source_ordinal=source.source_ordinal,
                    source_media_artifact_id=source.source_media_artifact.artifact_id,
                    source_frame_count=metadata_frame_count(
                        source.source_media_artifact.media_info
                    ),
                    source_frame_rate=canonical_fraction(
                        metadata_rate(source.source_media_artifact.media_info)
                    ),
                    effective_video_artifact_id=source.effective_video_artifact.artifact_id,
                    effective_video_frame_count=metadata_frame_count(
                        source.effective_video_artifact.media_info
                    ),
                    effective_video_frame_rate=canonical_fraction(
                        metadata_rate(source.effective_video_artifact.media_info)
                    ),
                )
                for source in binding.sources
            ),
        )

    @staticmethod
    def _publication_facts(
        project: Project,
        *,
        output_root: str | None = None,
    ) -> Av27PublicationFacts | None:
        """把同一次只读文件系统检查投影给纯 profile preflight。

        新 preview/expand 使用请求中的显式 ``output_root``；检查已经持久化的 expanded Graph 时，
        只能从 canonical ``target_path`` 的祖父目录恢复普通 Graph 中已经表达的发布根。任何无法安全
        解析的形状都返回 ``None``，由 preflight 结合结构诊断失败关闭。
        """

        outputs = tuple(node for node in project.graph.nodes if node.node_id == "output")
        if len(outputs) != 1:
            return None
        target = outputs[0].parameters.get("target_path")
        if (
            not isinstance(target, str)
            or not target
            or target.strip() != target
            or "\x00" in target
        ):
            return None
        try:
            target_path = Path(target)
            parent_path = target_path.parent.resolve(strict=False)
            root_path = (
                Path(output_root).resolve(strict=False)
                if output_root is not None
                else parent_path.parent.resolve(strict=False)
            )
            resolved_target = target_path.resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            return None

        def path_state(path: Path) -> tuple[bool, bool, bool, bool]:
            """返回 exists/is_dir/is_file/is_link_or_reparse，探测错误按 false 处理。"""

            try:
                exists = path.exists()
                is_directory = path.is_dir()
                is_file = path.is_file()
                is_link = path.is_symlink()
                if not is_link and exists:
                    attributes = getattr(path.lstat(), "st_file_attributes", 0)
                    is_link = bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024))
                return exists, is_directory, is_file, is_link
            except (OSError, ValueError):
                return False, False, False, False

        root_exists, root_is_dir, _, _ = path_state(root_path)
        parent_exists, parent_is_dir, _, _ = path_state(parent_path)
        target_exists, _, target_is_file, target_is_link = path_state(target_path)
        try:
            parent_contained = parent_path != root_path and parent_path.is_relative_to(root_path)
            target_contained = resolved_target.is_relative_to(root_path)
        except (OSError, ValueError):  # pragma: no cover - Path 纯词法调用的防御边界
            parent_contained = False
            target_contained = False
        return Av27PublicationFacts(
            target_path=target,
            resolved_output_root=str(root_path),
            resolved_canonical_parent=str(parent_path),
            output_root_exists=root_exists,
            output_root_is_directory=root_is_dir,
            canonical_parent_exists=parent_exists,
            canonical_parent_is_directory=parent_is_dir,
            canonical_parent_contained=parent_contained,
            target_exists=target_exists,
            target_is_regular_file=target_is_file and not target_is_link,
            target_is_symlink_or_reparse=target_is_link,
            target_contained=target_contained,
        )

    @staticmethod
    def _profile_failure_message(envelope: TemplatePreviewEnvelope) -> str:
        diagnostics = envelope.profile.diagnostics[:4]
        return (
            "; ".join(f"{item.code}: {item.message}" for item in diagnostics)
            or "AVEnhanceFlow v2.7 profile preflight 未通过"
        )

    def _resolve_av27_preparation(
        self,
        request: ExpandRequest,
    ) -> tuple[ProjectSnapshot, PreparationBinding]:
        """只从显式 Run/current latest/result/Artifact 建立 expand binding。

        这里不使用 active、view 或 newest Run 猜测 authority。指定 Run 可以是初次 preparation Run，
        也可以是 expanded Graph 上覆盖全部 preparation 节点的后续 Run；两者都先提取并严格比较同一
        preparation 子图。任一 snapshot、latest result 或 Artifact identity 漂移都失败关闭。
        """

        store, runtime = self._require_session()
        current = store.load()
        run = runtime.repository.get_run(request.preparation_run_id)
        if run.project_id != current.project.project_id:
            raise ProjectServiceError(
                "E_AV27_EXPAND_PROJECT",
                "preparation_run_id 不属于当前 Project",
                http_status=409,
                related_run_ids=(run.run_id,),
            )
        if run.state is not RunState.COMPLETED:
            raise ProjectServiceError(
                "E_AV27_EXPAND_RUN_INCOMPLETE",
                "preparation_run_id 必须是 completed Run",
                http_status=409,
                related_run_ids=(run.run_id,),
            )

        run_preparation = self._extract_preparation_snapshot(
            project=current.project,
            graph=run.graph_snapshot,
            definitions=run.definitions_snapshot,
        )
        current_preparation = self._extract_preparation_snapshot(
            project=current.project,
            graph=current.project.graph,
            definitions=current.definitions,
        )
        run_profile = preflight_av27_profile(run_preparation)
        if run_profile.status != "preparation-compatible":
            raise ProjectServiceError(
                "E_AV27_EXPAND_RUN_PROFILE",
                "指定 Run snapshot 的 preparation 子图不符合 v2.7 profile",
                http_status=409,
                related_run_ids=(run.run_id,),
            )
        if run_preparation != current_preparation:
            raise ProjectServiceError(
                "E_AV27_EXPAND_STALE",
                "指定 Run snapshot 不再精确对应当前 preparation 子图",
                http_status=409,
                related_run_ids=(run.run_id,),
            )

        nodes = {node.node_id: node for node in run_preparation.project.graph.nodes}
        latest_attempts: dict[str, NodeRun] = {}
        for node_run in run.node_runs:
            if node_run.node_id not in nodes:
                continue
            previous = latest_attempts.get(node_run.node_id)
            if previous is None or node_run.attempt > previous.attempt:
                latest_attempts[node_run.node_id] = node_run
        if set(latest_attempts) != set(nodes):
            raise ProjectServiceError(
                "E_AV27_EXPAND_RUN_INCOMPLETE",
                "指定 Run 没有覆盖全部 preparation 节点",
                http_status=409,
                related_run_ids=(run.run_id,),
            )

        results: dict[str, NodeResult] = {}
        for node_id, node_run in latest_attempts.items():
            if node_run.state is not NodeRunState.COMPLETED:
                raise ProjectServiceError(
                    "E_AV27_EXPAND_RUN_INCOMPLETE",
                    f"preparation 节点 {node_id!r} 尚未 completed",
                    http_status=409,
                    related_run_ids=(run.run_id,),
                )
            latest = runtime.repository.get_latest(node_id)
            if latest is None or latest.stale:
                raise ProjectServiceError(
                    "E_AV27_EXPAND_STALE",
                    f"preparation 节点 {node_id!r} 没有 current latest result",
                    http_status=409,
                    related_run_ids=(run.run_id,),
                )
            result = runtime.repository.get_result(latest.result_id)
            expected_result_id = node_run.reused_from_result_id
            if expected_result_id is not None:
                exact = result.result_id == expected_result_id
            else:
                exact = result.node_run_id == node_run.node_run_id
            if (
                not exact
                or tuple(item.artifact_id for item in result.outputs)
                != node_run.output_artifact_ids
            ):
                raise ProjectServiceError(
                    "E_AV27_EXPAND_STALE",
                    f"preparation 节点 {node_id!r} 的指定 Run result 已被替换",
                    http_status=409,
                    related_run_ids=(run.run_id,),
                )
            results[node_id] = result

        sources = tuple(
            sorted(
                (node for node in nodes.values() if node.type_id == SOURCE_PROGRAM_TYPE_ID),
                key=lambda item: cast(int, item.parameters["source_ordinal"]),
            )
        )
        admission_nodes = tuple(
            node for node in nodes.values() if node.type_id == SOURCE_ADMISSION_TYPE_ID
        )
        if len(admission_nodes) != 1:
            raise ProjectServiceError(
                "E_AV27_EXPAND_RUN_PROFILE",
                "preparation 必须恰好包含一个 SourceAdmission",
                http_status=409,
            )
        admission = admission_nodes[0]
        admission_artifact = self._artifact_for_port(
            results[admission.node_id],
            port_id="gate",
            kind="DataFile",
        )
        mr_by_source = self._mr_nodes_by_source(run_preparation, sources)
        source_mode = cast(SourceMode, admission.parameters["source_mode"])
        bindings: list[PreparationSourceBinding] = []
        for ordinal, source in enumerate(sources):
            source_result = results[source.node_id]
            source_media = self._artifact_for_port(
                source_result,
                port_id="source_media",
                kind="MediaFile",
            )
            source_video = self._artifact_for_port(
                source_result,
                port_id="video",
                kind="VideoFile",
            )
            mr = mr_by_source.get(source.node_id)
            effective_video = (
                source_video
                if mr is None
                else self._artifact_for_port(
                    results[mr.node_id],
                    port_id="video",
                    kind="VideoFile",
                )
            )
            chapter_label = source.parameters.get("label")
            bindings.append(
                PreparationSourceBinding(
                    source_ordinal=ordinal,
                    source_node_id=source.node_id,
                    source_media_artifact=source_media,
                    effective_video_artifact=effective_video,
                    mr_node_id=None if mr is None else mr.node_id,
                    chapter_label=(cast(str, chapter_label) if chapter_label is not None else None),
                )
            )
        binding = PreparationBinding(
            project_id=current.project.project_id,
            preparation_run_id=run.run_id,
            source_mode=source_mode,
            mr_mode="external" if mr_by_source else "off",
            admission_node_id=admission.node_id,
            admission_artifact=admission_artifact,
            sources=tuple(bindings),
        )
        current_profile = preflight_av27_profile(
            current,
            binding_facts=self._binding_facts(binding),
            publication_facts=self._publication_facts(current.project),
        )
        if current_profile.status == "incompatible":
            message = "; ".join(
                f"{item.code}: {item.message}" for item in current_profile.diagnostics[:4]
            )
            raise ProjectServiceError(
                "E_AV27_EXPAND_GRAPH_DIVERGED",
                message or "当前 Graph 已不符合可安全替换的 v2.7 profile",
                http_status=409,
                related_run_ids=(run.run_id,),
            )
        return current_preparation, binding

    @staticmethod
    def _extract_preparation_snapshot(
        *,
        project: Project,
        graph: Graph,
        definitions: tuple[NodeDefinition, ...],
    ) -> ProjectSnapshot:
        """从普通 preparation 或 expanded Graph 提取不含下游的可验证前半图。"""

        nodes = tuple(node for node in graph.nodes if node.type_id in _AV27_PREPARATION_TYPES)
        node_ids = {node.node_id for node in nodes}
        if any(
            edge.target_node_id in node_ids and edge.source_node_id not in node_ids
            for edge in graph.edges
        ):
            raise ProjectServiceError(
                "E_AV27_EXPAND_GRAPH_DIVERGED",
                "preparation 节点存在来自 expansion/未知节点的反向输入",
                http_status=409,
            )
        edges = tuple(
            edge
            for edge in graph.edges
            if edge.source_node_id in node_ids and edge.target_node_id in node_ids
        )
        keys = {(node.type_id, node.definition_version) for node in nodes}
        exact_definitions = tuple(
            definition
            for definition in definitions
            if (definition.type_id, definition.version) in keys
        )
        return ProjectSnapshot(
            project=Project(
                project_id=project.project_id,
                name=project.name,
                graph=Graph(nodes=nodes, edges=edges),
            ),
            definitions=exact_definitions,
        )

    @staticmethod
    def _mr_nodes_by_source(
        snapshot: ProjectSnapshot,
        sources: tuple[NodeInstance, ...],
    ) -> dict[str, NodeInstance]:
        nodes = {node.node_id: node for node in snapshot.project.graph.nodes}
        result: dict[str, NodeInstance] = {}
        for source in sources:
            candidates = tuple(
                nodes[edge.target_node_id]
                for edge in snapshot.project.graph.edges
                if edge.source_node_id == source.node_id
                and edge.source_port_id == "video"
                and edge.target_port_id == "video"
                and nodes[edge.target_node_id].type_id == MOSAIC_RESTORATION_TYPE_ID
            )
            if len(candidates) > 1:
                raise ProjectServiceError(
                    "E_AV27_EXPAND_RUN_PROFILE",
                    f"Source {source.node_id!r} 绑定了多个 MR 节点",
                    http_status=409,
                )
            if candidates:
                result[source.node_id] = candidates[0]
        return result

    @staticmethod
    def _artifact_for_port(
        result: NodeResult,
        *,
        port_id: str,
        kind: str,
    ) -> Artifact:
        values = tuple(item for item in result.outputs if item.producer_port_id == port_id)
        if len(values) != 1 or values[0].ordinal is not None or values[0].kind != kind:
            raise ProjectServiceError(
                "E_AV27_EXPAND_ARTIFACT_BINDING",
                f"节点结果必须恰好包含 {port_id}:{kind} 单值 Artifact",
                http_status=409,
            )
        return values[0]

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
    def _translate_av27_failure(error: Av27TemplateError | Av27MediaError) -> ProjectServiceError:
        """保留 AV27 稳定错误码，并把冲突类失败映射为 HTTP 409。"""

        code = str(error.code)
        conflict_prefixes = (
            "E_AV27_CREATE_EXISTS",
            "E_AV27_EXPAND_",
            "E_AV27_NAMING_EXISTS",
        )
        status = 409 if code.startswith(conflict_prefixes) else 422
        message = (
            error.message
            if isinstance(error, Av27TemplateError)
            else str(error).partition(": ")[2] or str(error)
        )
        return ProjectServiceError(code, message, http_status=status)

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
