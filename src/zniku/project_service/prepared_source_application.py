"""把检查、准备选择和工作参考展开接到同一 Project/Graph。

只有显式选择才改图，昂贵媒体任务始终由普通 Runtime 在后台执行。轮询只读有界报告和当前 attempt 日志；
不在 HTTP 锁内扫描全片，不把诊断结果当作准入，也不修改正在运行或等待交付的 snapshot。
"""

from __future__ import annotations

import stat
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from zniku.avenhance_v27.probe import Av27MediaError
from zniku.avenhance_v27.template import Av27TemplateError
from zniku.chapter_overlap import ChapterPlanningError
from zniku.chapter_overlap.context import ContextPlanningError
from zniku.graph import Graph, GraphValidator, NodeInstance
from zniku.media.probe import MediaNodeError
from zniku.prepared_source.template import PreparedSourceBinding, build_prepared_source
from zniku.project import Project, ProjectSnapshot, ProjectStore, ProjectStoreError
from zniku.project.storage import new_project_storage
from zniku.runtime import (
    Artifact,
    NodeResult,
    NodeRun,
    NodeRunState,
    Run,
    RunnerInput,
    RunState,
    RuntimeRepositoryError,
)
from zniku.source_preparation.contracts import check_reference_binding, read_diagnosis
from zniku.source_preparation.definitions import (
    definition_role,
    source_preparation_definitions,
)
from zniku.source_preparation.models import DiagnosticReport, summary_for_report
from zniku.source_preparation.template import build_preparation_graph

from .models import StatusEnvelope
from .prepared_source import (
    PreparedSourceAction,
    PreparedSourceChooseRequest,
    PreparedSourceCreateRequest,
    PreparedSourceError,
    PreparedSourceFailure,
    PreparedSourceFinding,
    PreparedSourceFullEnvelope,
    PreparedSourceFullRequest,
    PreparedSourceHandoff,
    PreparedSourceProcessingEnvelope,
    PreparedSourceProcessingRequest,
    PreparedSourceStageProgress,
    PreparedSourceViewEnvelope,
    PreparedSourceViewRequest,
)
from .storage_paths import prepare_storage_location

if TYPE_CHECKING:
    from .service import ProjectServiceApplication

type Route = Literal["diagnose", "direct", "builtin", "external"]


def _parse[M: BaseModel](model: type[M], payload: object) -> M:
    try:
        return model.model_validate(payload, strict=True)
    except ValidationError as error:
        first = error.errors(include_url=False, include_input=False)[0]
        raise PreparedSourceError(
            "E_PREPARED_SOURCE_REQUEST_INVALID", first["msg"], field_path=first["loc"]
        ) from error


def _error(code: str, message: str) -> PreparedSourceError:
    return PreparedSourceError(f"E_PREPARED_SOURCE_{code}", message, http_status=409)


def _direct(artifact: Artifact, port_id: str, ordinal: int | None = None) -> RunnerInput:
    return RunnerInput(
        port_id=port_id,
        artifact_id=artifact.artifact_id,
        kind=artifact.kind,
        path=Path(artifact.path),
        ordinal=ordinal,
        producer_node_run_id=artifact.producer_node_run_id,
        producer_port_id=artifact.producer_port_id,
        artifact_ordinal=artifact.ordinal,
        frame_range=artifact.frame_range,
        media_info=artifact.media_info,
        size=artifact.size,
        mtime_ns=artifact.mtime_ns,
    )


def _execution_shape(graph: Graph) -> object:
    """忽略位置和列表展示顺序；不能因拖动画布让已验证来源失效。"""
    return (
        {node.node_id: node.model_dump(exclude={"ui_position"}) for node in graph.nodes},
        sorted(
            (edge.model_dump() for edge in graph.edges),
            key=lambda item: (
                item["target_node_id"],
                item["target_port_id"],
                -1 if item.get("ordinal") is None else item["ordinal"],
                item["source_node_id"],
                item["source_port_id"],
            ),
        ),
    )


def _roles(snapshot: ProjectSnapshot) -> dict[str, NodeInstance]:
    definitions = {(item.type_id, item.version): item for item in snapshot.definitions}
    result: dict[str, NodeInstance] = {}
    for node in snapshot.project.graph.nodes:
        definition = definitions.get((node.type_id, node.definition_version))
        role = None if definition is None else definition_role(definition)
        if role is None or role in result:
            raise _error(
                "GRAPH_EDITED", "当前图已展开或自由编辑，请在工作区继续；向导不会覆盖现有节点。"
            )
        result[role] = node
    if "source" not in result or "diagnostics" not in result:
        raise _error("GRAPH_PROFILE", "当前图不是此版本的素材检查与准备图。")
    return result


def _route(roles: Mapping[str, NodeInstance]) -> Route:
    if "builtin" in roles:
        return "builtin"
    if "external" in roles:
        return "external"
    return "direct" if "admission" in roles else "diagnose"


def _check_graph(snapshot: ProjectSnapshot) -> dict[str, NodeInstance]:
    roles = _roles(snapshot)
    source = roles["source"]
    diagnosis = roles["diagnostics"]
    # 向导只操作自己声明的静态节点。用户重命名 node_id 属于高级图编辑，不猜测恢复模板身份。
    expected = build_preparation_graph(
        str(source.parameters["source_path"]),
        route=_route(roles),
        target_frame_rate=cast(str | None, diagnosis.parameters.get("target_frame_rate")),
        external_format=cast(
            Literal["mp4", "mov", "mkv"],
            roles["external"].type_id.rsplit(".", 1)[-1] if "external" in roles else "mkv",
        ),
        audio_policy=cast(
            Literal["original", "none"],
            roles["admission"].parameters.get("audio_policy", "original")
            if "admission" in roles
            else "original",
        ),
    )
    if _execution_shape(snapshot.project.graph) != _execution_shape(expected):
        raise _error("GRAPH_EDITED", "准备节点或连接已经修改，请在工作区继续，不自动覆盖自由图。")
    return roles


def _fresh(artifact: Artifact) -> None:
    """沿用本地可信模型的低成本变化检查，不把 stat 宣称为内容证明。"""
    try:
        facts = Path(artifact.path).stat()
    except OSError as error:
        raise _error("INPUT_CHANGED", "检查输入或结果已不可用，请重新检查素材。") from error
    if (
        not stat.S_ISREG(facts.st_mode)
        or artifact.size != facts.st_size
        or artifact.mtime_ns != facts.st_mtime_ns
    ):
        raise _error(
            "INPUT_CHANGED", "检查输入或结果已变化，请重新检查；旧结果不会自动用于新流程。"
        )


def _latest_attempts(run: Run) -> dict[str, NodeRun]:
    result: dict[str, NodeRun] = {}
    for attempt in run.node_runs:
        previous = result.get(attempt.node_id)
        if previous is None or previous.attempt < attempt.attempt:
            result[attempt.node_id] = attempt
    return result


def _result(app: ProjectServiceApplication, attempt: NodeRun) -> NodeResult:
    _, runtime = app._require_session()
    latest = runtime.repository.get_latest(attempt.node_id)
    if latest is None or latest.stale or attempt.state is not NodeRunState.COMPLETED:
        raise _error("STALE", "所选运行结果不是当前有效结果，请重新检查。")
    result = runtime.repository.get_result(latest.result_id)
    matches = (
        result.result_id == attempt.reused_from_result_id
        if attempt.reused_from_result_id is not None
        else result.node_run_id == attempt.node_run_id
    )
    if (
        not matches
        or tuple(item.artifact_id for item in result.outputs) != attempt.output_artifact_ids
    ):
        raise _error("STALE", "所选运行的结果已被其他 attempt 替换。")
    for artifact in result.outputs:
        _fresh(artifact)
    return result


def _bound(
    app: ProjectServiceApplication, request: PreparedSourceViewRequest
) -> tuple[ProjectSnapshot, Run, dict[str, NodeInstance], dict[str, NodeRun]]:
    app.assert_preview_session(request.project_session_id)
    store, runtime = app._require_session()
    current = store.load()
    roles = _check_graph(current)
    run = runtime.repository.get_run(request.run_id)
    if run.project_id != current.project.project_id:
        raise _error("PROJECT", "运行不属于当前工程。")
    if _execution_shape(current.project.graph) != _execution_shape(run.graph_snapshot):
        raise _error("STALE", "当前准备图已改变，请查看新运行；不会使用旧来源绑定。")
    keys = {(node.type_id, node.definition_version) for node in current.project.graph.nodes}
    current_defs = {
        (d.type_id, d.version): d for d in current.definitions if (d.type_id, d.version) in keys
    }
    run_defs = {
        (d.type_id, d.version): d
        for d in run.definitions_snapshot
        if (d.type_id, d.version) in keys
    }
    if current_defs != run_defs:
        raise _error("DEFINITION_CHANGED", "运行和当前工程的精确节点定义不一致。")
    return current, run, roles, _latest_attempts(run)


def _artifact(result: NodeResult, port_id: str, kind: str) -> Artifact:
    matches = [
        item for item in result.outputs if item.producer_port_id == port_id and item.kind == kind
    ]
    if len(matches) != 1:
        raise _error("OUTPUT_BINDING", "已完成节点的输出与声明不一致。")
    return matches[0]


def _report(
    app: ProjectServiceApplication,
    roles: Mapping[str, NodeInstance],
    attempts: Mapping[str, NodeRun],
) -> tuple[DiagnosticReport | None, Artifact | None]:
    source_attempt = attempts.get(roles["source"].node_id)
    original = (
        _artifact(_result(app, source_attempt), "media", "MediaFile")
        if source_attempt is not None and source_attempt.state is NodeRunState.COMPLETED
        else None
    )
    diagnostic_attempt = attempts.get(roles["diagnostics"].node_id)
    if diagnostic_attempt is None or diagnostic_attempt.state is not NodeRunState.COMPLETED:
        return None, original
    if original is None:
        raise _error("DIAGNOSIS_BINDING", "诊断缺少已登记原件。")
    report_artifact = _artifact(_result(app, diagnostic_attempt), "diagnosis", "DataFile")
    report = read_diagnosis(_direct(report_artifact, "diagnosis"))
    if report.original_media_artifact_id != original.artifact_id:
        raise _error("DIAGNOSIS_BINDING", "诊断报告与当前原件不一致。")
    return report, original


def _stage_progress(
    app: ProjectServiceApplication, attempt: NodeRun | None
) -> PreparedSourceStageProgress | None:
    if attempt is None:
        return None
    text, _, _ = app._read_log(attempt, "stdout.log")
    for line in reversed(text.splitlines()):
        if line.startswith("ZNIKU_SOURCE_PROGRESS "):
            try:
                return PreparedSourceStageProgress.model_validate_json(
                    line.partition(" ")[2], strict=True
                )
            except ValidationError:
                # 进度日志可能恰在写入；缺失/损坏仅显示不确定进度，不改变运行结论。
                continue
    return None


def create(app: ProjectServiceApplication, payload: object) -> StatusEnvelope:
    """排他发布新准备工程；既有文件、已打开旧工程和原件不被覆盖。"""
    request = _parse(PreparedSourceCreateRequest, payload)
    with app._state:
        app._assert_idle()
        if app._desktop_closing:
            raise _error("CLOSING", "应用正在关闭。")
        if (
            not Path(request.project_path).is_absolute()
            or Path(request.project_path).suffix != ".zniku"
        ):
            raise _error("PROJECT_PATH", "请选择绝对 .zniku 工程保存位置。")
        path = app._resolve_create_path(request.project_path)
        source = Path(request.source_path)
        if not source.is_absolute() or not source.is_file():
            raise _error("SOURCE_PATH", "请选择当前主机上可读的实体视频文件。")
        if source.resolve() == path.resolve():
            raise _error("SOURCE_PATH", "工程文件不能覆盖源媒体。")
        if path.exists():
            raise _error("PROJECT_EXISTS", "工程位置已有文件，请选择新的工程名称或位置。")
        definitions = source_preparation_definitions()
        available = {(item.type_id, item.version): item for item in app._definition_catalog}
        if any(available.get((item.type_id, item.version)) != item for item in definitions):
            raise _error("CAPABILITY", "当前服务未安装完整素材准备节点，请使用匹配的候选包。")
        graph = build_preparation_graph(str(source))
        GraphValidator(definitions).validate(graph)
        project = Project(project_id=request.project_id, name=request.project_name, graph=graph)
        if request.data_parent is not None and not Path(request.data_parent).is_absolute():
            raise _error("DATA_PATH", "工作数据父目录必须是绝对路径。")
        storage = new_project_storage(
            path,
            data_root=Path(request.data_parent) / path.with_suffix(".data").name
            if request.data_parent is not None
            else None,
            media_basename=path.stem,
        )
        prepare_storage_location(storage, current=None)
        store = ProjectStore.create_atomically(
            path, project, app._definition_catalog, storage=storage
        )
        app._store = store
        app._runtime = app._runtime_for(store)
        app._project_session_id = str(uuid4())
        app._active_run_id = None
        app._last_error = None
        return app.inspect()


def _actions(
    report: DiagnosticReport | None, original: Artifact | None
) -> tuple[PreparedSourceAction, ...]:
    if report is None or original is None:
        return ()
    summary = summary_for_report(report)
    eligible = bool(summary["available_strategies"])
    candidate = bool(summary["candidate_strategies"])
    rate_choice_only = bool(report.findings) and all(
        issue.code == "E_SOURCE_PREPARATION_RATE_SELECTION_REQUIRED" for issue in report.findings
    )
    return (
        PreparedSourceAction(
            route="direct",
            label="直接使用原件",
            enabled=not report.findings or rate_choice_only,
            reason=(
                "请选择有依据的精确帧率；下一次运行会重新完整检查，不直接放行。"
                if rate_choice_only
                else "原件符合检查要求，可继续工作源准入。"
                if not report.findings
                else "原件仍有未解决的问题。"
            ),
            estimated_additional_bytes=0,
        ),
        PreparedSourceAction(
            route="builtin",
            label="内置生成工作副本",
            enabled=eligible,
            reason="原件不改；生成新副本并完整验证。"
            if eligible
            else (
                "已识别候选策略，但真实代表与留出验收尚未通过，当前未启用。"
                if candidate
                else "此素材不满足当前内置策略的前提。"
            ),
            strategy_id="t1-clock-quantization/1",
            estimated_additional_bytes=original.size,
        ),
        PreparedSourceAction(
            route="external",
            label="导入外部保内容修复结果",
            enabled=True,
            reason="不保证可修复；必须保持全部约定内容并重新验证。内容改变时请作为新工作源。",
            estimated_additional_bytes=original.size,
        ),
    )


def view(app: ProjectServiceApplication, payload: object) -> PreparedSourceViewEnvelope:
    """不扫描媒体、不创建节点；状态只由当前的正式 Run 和准入结果派生。"""
    request = _parse(PreparedSourceViewRequest, payload)
    with app._state:
        current, run, roles, attempts = _bound(app, request)
        report, original = _report(app, roles, attempts)
        store, runtime = app._require_session()
        active = next(
            (item for item in attempts.values() if item.state is NodeRunState.RUNNING), None
        )
        waiting = next(
            (item for item in attempts.values() if item.state is NodeRunState.WAITING_EXTERNAL),
            None,
        )
        verifying_external = (
            waiting is not None
            and app._active_operation in {"submit_external", "import_external"}
            and app._active_run_id == run.run_id
        )
        if verifying_external:
            active = waiting
        failed = next(
            (item for item in attempts.values() if item.state is NodeRunState.FAILED), None
        )
        admission = attempts.get(roles["admission"].node_id) if "admission" in roles else None
        state: Literal[
            "checking", "needs_choice", "preparing", "waiting_external", "ready", "failed"
        ]
        state = "checking" if _route(roles) == "diagnose" else "preparing"
        stage = "正在检查源视频" if state == "checking" else "正在准备并验证工作参考"
        reference_path = None
        admission_status: Literal["not_started", "pending", "completed", "failed"] = (
            "not_started" if "admission" not in roles else "pending"
        )
        if admission is not None and admission.state is NodeRunState.FAILED:
            admission_status = "failed"
        if failed is not None or run.state is RunState.FAILED:
            state, stage = "failed", "当前步骤未完成；原件及已有产物保留"
        elif verifying_external:
            state, stage = "preparing", "正在验证外部修复副本"
        elif waiting is not None:
            state, stage = "waiting_external", "等待用户提交外部修复结果"
        elif run.state is RunState.COMPLETED:
            if admission is None:
                state, stage = "needs_choice", "检查完成，请选择下一步"
            else:
                binding = _completed_binding(app, current, run, roles, attempts)
                reference_path = binding.reference_video.path
                admission_status = "completed"
                state, stage = "ready", "工作源已准入，可以设置分章与处理方案"
        summary = None if report is None else summary_for_report(report)
        error = failed.error if failed is not None else run.error
        samples = app._project_progress(run, runtime)
        progress = next(
            (p for p in samples if active is not None and p.node_run_id == active.node_run_id), None
        )
        return PreparedSourceViewEnvelope(
            project_session_id=request.project_session_id,
            storage_revision=store.load_authoring().storage_revision,
            run_id=run.run_id,
            route=_route(roles),
            state=state,
            stage=stage,
            current_node_run_id=None if active is None else active.node_run_id,
            progress=progress,
            stage_progress=_stage_progress(app, active),
            source_name=Path(str(roles["source"].parameters["source_path"])).name,
            original_path=str(roles["source"].parameters["source_path"]),
            reference_path=reference_path,
            source_frame_count=None
            if report is None or not report.video.frame_count
            else report.video.frame_count,
            frame_rate=None if report is None else report.target_frame_rate,
            frame_rate_choices=()
            if summary is None
            else tuple(cast(list[str], summary["target_frame_rate_choices"])),
            audio_track_count=None if report is None else len(report.audio),
            findings=()
            if report is None
            else tuple(
                PreparedSourceFinding(
                    code=item.code,
                    reason=item.message,
                    impact="该问题可能阻止精确分章、补帧或最终音画同步准入。",
                    recommendation="请选择适用的工作副本方案；无法保持内容时作为新工作源重新分析。",
                )
                for item in report.findings
            ),
            available_actions=_actions(report, original),
            diagnosis_status="completed"
            if report is not None
            else (
                "failed"
                if failed is not None and failed.node_id == roles["diagnostics"].node_id
                else "pending"
            ),
            admission_status=admission_status,
            handoff=None
            if waiting is None
            else PreparedSourceHandoff(
                run_id=run.run_id, node_run_id=waiting.node_run_id, node_id=waiting.node_id
            ),
            error=None
            if error is None
            else PreparedSourceFailure(
                code="E_PREPARED_SOURCE_FAILED",
                message=error.message,
                related_run_ids=(run.run_id,),
            ),
        )


def cancel(app: ProjectServiceApplication, payload: object) -> StatusEnvelope:
    """向当前准备 worker 发停止信号，真正退出前仍保持 busy，不伪造已取消终态。"""
    from .prepared_source_operations import operation_cancel

    return operation_cancel(app, payload, run_only=True)


def choose(app: ProjectServiceApplication, payload: object) -> StatusEnvelope:
    """只替换未展开的已知准备图；用户下一次普通 Run 才执行新路线。"""
    request = _parse(PreparedSourceChooseRequest, payload)
    with app._state:
        app._assert_idle()
        if app._desktop_closing:
            raise _error("CLOSING", "应用正在关闭。")
        current, _run, roles, attempts = _bound(app, request)
        store, _ = app._require_session()
        if store.load_authoring().storage_revision != request.expected_storage_revision:
            raise _error("STORAGE_CONFLICT", "工程已修改，请刷新后重新选择。")
        if any(
            item.state in {NodeRunState.RUNNING, NodeRunState.WAITING_EXTERNAL}
            for item in attempts.values()
        ):
            raise _error("ACTIVE_ATTEMPT", "请先完成或明确结束当前任务，再更改处理路线。")
        report, original = _report(app, roles, attempts)
        if report is None or original is None:
            raise _error("DIAGNOSIS_REQUIRED", "请先完成素材检查。")
        action = next(item for item in _actions(report, original) if item.route == request.route)
        if not action.enabled:
            raise _error("STRATEGY_UNAVAILABLE", action.reason)
        rate = request.target_frame_rate or report.target_frame_rate
        if rate is None:
            raise PreparedSourceError(
                "E_PREPARED_SOURCE_RATE_REQUIRED",
                "请明确选择有依据的精确源帧率。",
                field_path=("target_frame_rate",),
            )
        graph = build_preparation_graph(
            original.path,
            route=request.route,
            target_frame_rate=rate,
            external_format=request.external_format,
            audio_policy="original" if report.audio else "none",
        )
        # 保留未改变节点的位置；修改布局不改变语义，新节点仍取 Python 默认位置。
        positions = {item.node_id: item.ui_position for item in current.project.graph.nodes}
        graph = graph.model_copy(
            update={
                "nodes": tuple(
                    node.model_copy(
                        update={"ui_position": positions.get(node.node_id, node.ui_position)}
                    )
                    for node in graph.nodes
                )
            }
        )
        GraphValidator(current.definitions).validate(graph)
        project = current.project.model_copy(update={"graph": graph})
        store.save(
            project,
            current.definitions,
            expected_storage_revision=request.expected_storage_revision,
        )
        app._last_error = None
        return app.inspect()


def _completed_binding(
    app: ProjectServiceApplication,
    current: ProjectSnapshot,
    run: Run,
    roles: Mapping[str, NodeInstance],
    attempts: Mapping[str, NodeRun],
) -> PreparedSourceBinding:
    if run.state is not RunState.COMPLETED or "admission" not in roles:
        raise _error("ADMISSION_REQUIRED", "工作源准入尚未完成。")
    if set(attempts) != {node.node_id for node in current.project.graph.nodes}:
        raise _error("RUN_INCOMPLETE", "所选 Run 未覆盖完整准备图。")
    results = {node_id: _result(app, attempt) for node_id, attempt in attempts.items()}
    admission = roles["admission"]
    result = results[admission.node_id]
    reference_video = _artifact(result, "video", "VideoFile")
    gate_artifact = _artifact(result, "gate", "DataFile")
    gate = check_reference_binding(
        _direct(reference_video, "video"), _direct(gate_artifact, "gate")
    )
    original = _artifact(results[roles["source"].node_id], "media", "MediaFile")
    audio_edges = sorted(
        (
            edge
            for edge in current.project.graph.edges
            if edge.target_node_id == admission.node_id and edge.target_port_id == "audio_sources"
        ),
        key=lambda edge: -1 if edge.ordinal is None else edge.ordinal,
    )
    audio = tuple(
        _artifact(results[edge.source_node_id], edge.source_port_id, "MediaFile")
        for edge in audio_edges
    )
    if original.artifact_id != gate.original_media_artifact_id or tuple(
        a.artifact_id for a in audio
    ) != tuple(a.artifact_id for a in gate.audio_bindings):
        raise _error("AUDIO_BINDING", "工作参考与原件/音频载体绑定不一致。")
    return PreparedSourceBinding(
        project_id=current.project.project_id,
        preparation_run_id=run.run_id,
        admission_node_id=admission.node_id,
        reference_video=reference_video,
        admission_artifact=gate_artifact,
        original_media=original,
        audio_sources=audio,
        audio_node_ports=tuple((edge.source_node_id, edge.source_port_id) for edge in audio_edges),
    )


def processing_preview(payload: object) -> PreparedSourceProcessingEnvelope:
    request = _parse(PreparedSourceProcessingRequest, payload)
    return PreparedSourceProcessingEnvelope(processing=request.processing)


def full(
    app: ProjectServiceApplication, payload: object, *, expand: bool
) -> PreparedSourceFullEnvelope | StatusEnvelope:
    """在同一锁内重验 CAS/直接输入并预览或保存普通完整图，保存不自动运行。"""
    request = _parse(PreparedSourceFullRequest, payload)
    with app._state:
        app._assert_idle()
        if app._desktop_closing:
            raise _error("CLOSING", "应用正在关闭。")
        current, run, roles, attempts = _bound(
            app,
            PreparedSourceViewRequest(
                contract_version="0.3.4",
                project_session_id=request.project_session_id,
                run_id=request.preparation_run_id,
            ),
        )
        store, _ = app._require_session()
        if store.load_authoring().storage_revision != request.expected_storage_revision:
            raise _error("STORAGE_CONFLICT", "工程已修改，请刷新后重试。")
        binding = _completed_binding(app, current, run, roles, attempts)
        build = build_prepared_source(current, binding, request.processing, request.publication)
        if expand:
            store.save(
                build.project,
                build.definitions,
                expected_storage_revision=request.expected_storage_revision,
            )
            app._last_error = None
            return app.inspect()
        return PreparedSourceFullEnvelope(
            project_session_id=request.project_session_id,
            storage_revision=request.expected_storage_revision,
            preparation_run_id=request.preparation_run_id,
            processing=request.processing,
            plan=build.plan,
            contexts=build.contexts,
            publication=build.publication,
            node_count=len(build.project.graph.nodes),
            edge_count=len(build.project.graph.edges),
        )


def dispatch(app: ProjectServiceApplication, action: str, payload: object) -> BaseModel:
    """统一返回稳定新版本失败；所有媒体长任务仍交给普通 Run。"""
    from .service import ProjectServiceError

    try:
        if action == "create":
            return create(app, payload)
        if action == "view":
            return view(app, payload)
        if action == "choose":
            return choose(app, payload)
        if action == "cancel":
            return cancel(app, payload)
        if action in {"operation-view", "operation-cancel"}:
            from .prepared_source_operations import operation_cancel, operation_view

            return (operation_view if action == "operation-view" else operation_cancel)(
                app, payload
            )
        if action == "processing-preview":
            return processing_preview(payload)
        if action in {"full-preview", "expand"}:
            return full(app, payload, expand=action == "expand")
        raise _error("ROUTE", "未知素材准备操作。")
    except PreparedSourceError:
        raise
    except ChapterPlanningError as error:
        raise PreparedSourceError(
            error.code, error.message, field_path=("processing", "settings", *error.field_path)
        ) from error
    except ContextPlanningError as error:
        raise PreparedSourceError(
            error.code, error.message, field_path=("processing", "fi_profile", *error.field_path)
        ) from error
    except ProjectServiceError as error:
        raise PreparedSourceError(
            error.code,
            error.message,
            http_status=error.http_status,
            related_run_ids=error.related_run_ids,
        ) from error
    except (Av27TemplateError, Av27MediaError, MediaNodeError) as error:
        raise PreparedSourceError(error.code, str(error)) from error
    except (
        ProjectStoreError,
        RuntimeRepositoryError,
        ValidationError,
        OSError,
        ValueError,
    ) as error:
        raise PreparedSourceError("E_PREPARED_SOURCE_OPERATION_FAILED", str(error)) from error
