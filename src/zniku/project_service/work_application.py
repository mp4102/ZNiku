"""普通工作源策略接入已有服务事务；检查结果只读，执行仍由同一 Runtime 负责。

复用既有创建、CAS、当前结果、直接绑定和运行操作；这里只解释新 policy 的选择与显示。
所有版本分流都是可信代码参数，既不换全局函数，也不把新报告转换成旧严格审计报告。
"""

from __future__ import annotations

from collections.abc import Mapping
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from pydantic import BaseModel, ValidationError

from zniku.avenhance_v27.probe import Av27MediaError
from zniku.avenhance_v27.template import Av27TemplateError
from zniku.chapter_overlap import ChapterPlanningError
from zniku.chapter_overlap.context import ContextPlanningError
from zniku.graph import GraphValidator, NodeInstance
from zniku.media.probe import MediaNodeError
from zniku.prepared_source.work_template import build_prepared_source
from zniku.project import ProjectSnapshot, ProjectStoreError
from zniku.runtime import Artifact, NodeRun, NodeRunState, RunState, RuntimeRepositoryError
from zniku.source_preparation.work_contracts import (
    check_reference_binding,
    read_diagnosis,
    read_reference_report,
)
from zniku.source_preparation.work_definitions import (
    definition_role,
    source_preparation_definitions,
)
from zniku.source_preparation.work_models import (
    WorkDiagnosticReport,
    summary_for_report,
    work_copy_estimate_bytes,
)
from zniku.source_preparation.work_template import build_preparation_graph

from . import prepared_color_application as shared
from . import prepared_color_operations as operations
from .models import StatusEnvelope
from .prepared_color import (
    ColorPreparedSourceAction,
    ColorPreparedSourceError,
    ColorPreparedSourceFailure,
    ColorPreparedSourceFinding,
    ColorPreparedSourceHandoff,
)
from .work import (
    WorkChooseRequest,
    WorkConfirmation,
    WorkConfirmationId,
    WorkCreateRequest,
    WorkError,
    WorkFullEnvelope,
    WorkFullRequest,
    WorkImpact,
    WorkOperationEnvelope,
    WorkOperationRequest,
    WorkProcessingEnvelope,
    WorkProcessingRequest,
    WorkRetryTarget,
    WorkSettings,
    WorkViewEnvelope,
    WorkViewRequest,
)

if TYPE_CHECKING:
    from .service import ProjectServiceApplication


def _settings(roles: Mapping[str, NodeInstance]) -> WorkSettings:
    """恢复实际 Graph 参数，尤其外部封装和准入解释，不用界面默认值覆盖。"""
    admission = roles.get("admission")
    params = {} if admission is None else admission.parameters
    confirmations: list[WorkConfirmationId] = []
    if shared._interpretation(roles) != "declared_only":
        confirmations.append("color_interpretation")
    if "builtin" in roles and roles["builtin"].parameters.get("retime_confirmed") is True:
        confirmations.append("retime")
    if (
        "external" in roles
        and roles["external"].parameters.get("reference_change_confirmed") is True
    ):
        confirmations.append("external_reference")
    return WorkSettings(
        route=shared._route(roles),
        target_frame_rate=cast(
            str | None,
            params.get(
                "target_frame_rate", roles["diagnostics"].parameters.get("target_frame_rate")
            ),
        ),
        external_format=cast(
            Literal["mp4", "mov", "mkv"],
            roles["external"].type_id.rsplit(".", 1)[-1] if "external" in roles else "mkv",
        ),
        interpretation_policy=shared._interpretation(roles),
        confirmations=tuple(confirmations),
        audio_source=cast(
            Literal["original", "reference", "none"], params.get("audio_policy", "original")
        ),
    )


def _check_graph(snapshot: ProjectSnapshot) -> dict[str, NodeInstance]:
    roles = shared._roles(snapshot, definition_role)
    settings = _settings(roles)
    expected = build_preparation_graph(
        str(roles["source"].parameters["source_path"]),
        route=settings.route,
        target_frame_rate=settings.target_frame_rate,
        external_format=settings.external_format,
        audio_policy=settings.audio_source,
        interpretation_policy=settings.interpretation_policy,
        retime_confirmed="retime" in settings.confirmations,
        reference_change_confirmed="external_reference" in settings.confirmations,
        diagnosis_target_frame_rate=cast(
            str | None, roles["diagnostics"].parameters.get("target_frame_rate")
        ),
    )
    if shared._execution_shape(snapshot.project.graph) != shared._execution_shape(expected):
        raise shared._error("GRAPH_EDITED", "准备图已自由修改；请在工作区继续，向导不会覆盖。")
    return roles


def _report(
    app: ProjectServiceApplication,
    roles: Mapping[str, NodeInstance],
    attempts: Mapping[str, NodeRun],
) -> tuple[WorkDiagnosticReport | None, Artifact | None]:
    source = attempts.get(roles["source"].node_id)
    original = (
        shared._artifact(shared._result(app, source), "media", "MediaFile")
        if (source is not None and source.state is NodeRunState.COMPLETED)
        else None
    )
    attempt = attempts.get(roles["diagnostics"].node_id)
    if attempt is None or attempt.state is not NodeRunState.COMPLETED:
        return None, original
    if original is None:
        raise shared._error("DIAGNOSIS_BINDING", "诊断缺少当前登记原件。")
    artifact = shared._artifact(shared._result(app, attempt), "diagnosis", "DataFile")
    return read_diagnosis(
        shared._direct(artifact, "diagnosis"), shared._direct(original, "original_media")
    ), original


def _candidate_report(
    app: ProjectServiceApplication,
    roles: Mapping[str, NodeInstance],
    attempts: Mapping[str, NodeRun],
) -> WorkDiagnosticReport | None:
    node = roles.get("external")
    attempt = None if node is None else attempts.get(node.node_id)
    if attempt is None or attempt.state is not NodeRunState.COMPLETED:
        return None
    artifact = shared._artifact(shared._result(app, attempt), "media", "MediaFile")
    return read_reference_report(shared._direct(artifact, "reference_media"))


def _actions(report: WorkDiagnosticReport | None) -> tuple[ColorPreparedSourceAction, ...]:
    if report is None:
        return ()
    summary = summary_for_report(report)
    return (
        ColorPreparedSourceAction(
            route="direct",
            label="直接使用原件",
            enabled=bool(summary["direct_eligible_with_interpretation"]),
            reason="不生成整片副本；缺少必要解释时仍须明确确认。",
            estimated_additional_bytes=0,
        ),
        ColorPreparedSourceAction(
            route="builtin",
            label="准备恒定帧率工作副本",
            enabled=bool(summary["available_strategies"]),
            reason="保留解码帧数和顺序，明确重新定时；不声称恢复拍摄时钟，不补删帧。",
            strategy_id="frame-retime-ffv1/1",
        ),
        ColorPreparedSourceAction(
            route="external",
            label="导入新的外部工作源",
            enabled=True,
            reason="允许明确的内容变化；重新检查、重新规划，并使用新参考自身音频。",
        ),
    )


def _requirements(
    report: WorkDiagnosticReport | None, candidate: WorkDiagnosticReport | None
) -> tuple[WorkConfirmation, ...]:
    if report is None:
        return ()
    summary = summary_for_report(report)
    items = [
        WorkConfirmation(
            id="retime",
            routes=("builtin",),
            label="确认重新定时",
            description="按所选精确帧率保留所有解码帧；原节奏和视频时长可能改变，音频不自动变速或裁尾。",
        ),
        WorkConfirmation(
            id="external_reference",
            routes=("external",),
            label="确认采用新的工作参考",
            description="我确认其来源和允许的变化；按新参考重新规划并采用其自身音频，不继承旧分章。",
        ),
    ]
    if summary["can_interpret"]:
        items.append(
            WorkConfirmation(
                id="color_interpretation",
                routes=("direct", "builtin"),
                label="确认 SDR 工作解释",
                description="只填补工具未确定项；不是原片实测声明或色彩转换。",
            )
        )
    if candidate is not None and summary_for_report(candidate)["can_interpret"]:
        items.append(
            WorkConfirmation(
                id="color_interpretation",
                routes=("external",),
                label="确认新参考的 SDR 工作解释",
                description="新文件自身未完整声明色彩；须依据该文件独立确认，不继承原片判断。",
            )
        )
    if report.target_frame_rate is None:
        items.append(
            WorkConfirmation(
                id="target_frame_rate",
                routes=("direct", "builtin", "external"),
                label="确认目标精确帧率",
                description="选择有依据的精确有理数，不由程序从近似读数猜测。",
            )
        )
    return tuple(items)


def _impacts(report: WorkDiagnosticReport | None) -> tuple[WorkImpact, ...]:
    if report is None:
        return ()
    video = report.video
    timing = "尚未明确目标帧率；选择后按实际帧数 N/FPS 计算工作时长，不猜测原拍摄节奏。"
    if video is not None and report.target_frame_rate is not None:
        seconds = Fraction(video.frame_count) / Fraction(report.target_frame_rate)
        timing = (
            f"按当前目标 {report.target_frame_rate} FPS 重新定时；{video.frame_count} 帧的"
            f"工作时长 N/FPS 为约 {float(seconds):.3f} 秒。目标改变时需重新观察确认。"
        )
    storage = "尚未获得可靠帧数，无法给出工作副本空间参考预算。"
    if video is not None and video.frame_count > 0:
        gib = work_copy_estimate_bytes(video) / 1024**3
        storage = (
            f"按实际采样/位深计算的未压缩参考预算约 {gib:.3f} GiB (含余量)；"
            "不是实际 FFV1 体积或压缩保证。工作副本持久保留，不自动删除。"
        )
    return (
        WorkImpact(
            id="frames",
            routes=("builtin",),
            title="帧数与顺序",
            description="保留解码帧数和顺序，不使用补帧或丢帧转换。",
        ),
        WorkImpact(
            id="timing",
            routes=("builtin",),
            title="时间解释",
            description=timing,
        ),
        WorkImpact(
            id="color",
            routes=("direct", "builtin", "external"),
            title="色彩",
            description="保留实际观察与用户工作解释的区别；明确冲突不可覆盖。",
        ),
        WorkImpact(
            id="audio",
            routes=("direct", "builtin"),
            title="音频",
            description="使用原件音轨及相对起点；尾差不自动裁切、拉伸或补静音。",
        ),
        WorkImpact(
            id="storage",
            routes=("builtin",),
            title="持久工作副本",
            description=storage,
        ),
        WorkImpact(
            id="external_reference",
            routes=("external",),
            title="新来源",
            description="外部文件可以改变内容，但必须重新观察 N/FPS/色彩，并改用其自身音频。",
        ),
    )


def view(app: ProjectServiceApplication, payload: object) -> WorkViewEnvelope:
    """只读已完成报告与有界进度；不在 HTTP 锁中运行媒体扫描。"""
    request = shared._parse(WorkViewRequest, payload)
    with app._state:
        current, run, roles, attempts = shared._bound(app, request, _check_graph)
        report, _original = _report(app, roles, attempts)
        candidate = _candidate_report(app, roles, attempts)
        summary = None if report is None else summary_for_report(report)
        signal_summary = summary if candidate is None else summary_for_report(candidate)
        settings = _settings(roles)
        store, runtime = app._require_session()
        active = operations._active_attempt(app, run, definition_role)
        waiting = next(
            (a for a in attempts.values() if a.state is NodeRunState.WAITING_EXTERNAL), None
        )
        failed = next((a for a in attempts.values() if a.state is NodeRunState.FAILED), None)
        admission = attempts.get(roles["admission"].node_id) if "admission" in roles else None
        state: Literal[
            "checking", "needs_choice", "preparing", "waiting_external", "ready", "failed"
        ] = "checking" if settings.route == "diagnose" else "preparing"
        stage = "正在观察工作源" if settings.route == "diagnose" else "正在准备工作参考"
        admission_status: Literal["not_started", "pending", "completed", "failed"] = (
            "not_started" if "admission" not in roles else "pending"
        )
        reference_path = None
        working_basis = None
        admitted = None
        if failed is not None or run.state is RunState.FAILED:
            state, stage = "failed", "当前步骤未完成；原件及已有产物保留"
            if admission is not None and admission.state is NodeRunState.FAILED:
                admission_status = "failed"
        elif waiting is not None:
            verifying = active is not None and active.node_run_id == waiting.node_run_id
            state, stage = (
                ("preparing", "正在观察新外部工作参考")
                if verifying
                else ("waiting_external", "等待显式提交新的工作参考")
            )
        elif run.state is RunState.COMPLETED:
            if admission is None:
                state, stage = "needs_choice", "工作源观察完成，请选择下一步"
            else:
                binding = shared._completed_binding(
                    app, current, run, roles, attempts, check_reference_binding
                )
                admitted = check_reference_binding(
                    shared._direct(binding.reference_video, "video"),
                    shared._direct(binding.admission_artifact, "gate"),
                )
                reference_path = binding.reference_video.path
                admission_status, state, stage = (
                    "completed",
                    "ready",
                    "工作参考已建立，可以配置处理方案",
                )
                working_basis = (
                    "按已记录的工具观察与明确用户解释处理；不是原始码流完整审计或保内容证明。"
                )
        error = failed.error if failed is not None else run.error
        progress = next(
            (
                p
                for p in app._project_progress(run, runtime)
                if active is not None and p.node_run_id == active.node_run_id
            ),
            None,
        )
        video = None if report is None else report.video
        return WorkViewEnvelope(
            project_session_id=request.project_session_id,
            storage_revision=store.load_authoring().storage_revision,
            run_id=run.run_id,
            route=settings.route,
            state=state,
            stage=stage,
            decision=None if report is None else report.status,
            inspection_scope=None if report is None else report.inspection_scope,
            impacts=_impacts(report),
            required_confirmations=_requirements(report, candidate),
            current_settings=settings,
            retry_target=None
            if failed is None
            else WorkRetryTarget(
                run_id=run.run_id, node_run_id=failed.node_run_id, node_id=failed.node_id
            ),
            current_node_run_id=None if active is None else active.node_run_id,
            progress=progress,
            stage_progress=shared._stage_progress(app, active),
            source_name=Path(str(roles["source"].parameters["source_path"])).name,
            original_path=str(roles["source"].parameters["source_path"]),
            reference_path=reference_path,
            source_frame_count=admitted.source_frame_count
            if admitted
            else video.frame_count
            if video and video.frame_count
            else None,
            frame_rate=admitted.frame_rate
            if admitted
            else None
            if report is None
            else report.target_frame_rate,
            frame_rate_choices=() if report is None else report.target_frame_rate_choices,
            audio_track_count=None
            if report is None
            else sum(len(a.tracks) for a in admitted.audio_bindings)
            if admitted
            else len(report.audio),
            findings=()
            if report is None
            else tuple(
                ColorPreparedSourceFinding(
                    code=f.code,
                    reason=f.message,
                    impact="当前处理器的已观察能力边界；不等同文件损坏。",
                    recommendation="按可用动作建立明确工作参考，或返回选材。",
                )
                for f in (*report.findings, *report.warnings)
            ),
            available_actions=_actions(report),
            diagnosis_status="completed"
            if report
            else "failed"
            if failed and failed.node_id == roles["diagnostics"].node_id
            else "pending",
            admission_status=admission_status,
            interpretation_policy=settings.interpretation_policy,
            color_interpretation_available=bool(signal_summary and signal_summary["can_interpret"]),
            color_interpretation_required=bool(
                signal_summary and signal_summary["missing_color_fields"]
            ),
            color_interpretation_reason="只解释当前工作参考的未确定项，不能覆盖已观察冲突。"
            if signal_summary
            else "等待工作源观察。",
            working_signal_basis=working_basis,
            handoff=None
            if waiting is None
            else ColorPreparedSourceHandoff(
                run_id=run.run_id, node_run_id=waiting.node_run_id, node_id=waiting.node_id
            ),
            error=None
            if error is None
            else ColorPreparedSourceFailure(
                code="E_PREPARED_SOURCE_FAILED",
                message=error.message,
                related_run_ids=(run.run_id,),
            ),
        )


def choose(app: ProjectServiceApplication, payload: object) -> StatusEnvelope:
    """选择只改变普通图；必要确认绑定当前观察，下一次 Run 才执行。"""
    request = shared._parse(WorkChooseRequest, payload)
    with app._state:
        app._assert_idle()
        if app._desktop_closing:
            raise shared._error("CLOSING", "应用正在关闭。")
        current, _run, roles, attempts = shared._bound(app, request, _check_graph)
        store, _ = app._require_session()
        if store.load_authoring().storage_revision != request.expected_storage_revision:
            raise shared._error("STORAGE_CONFLICT", "工程已修改，请刷新。")
        if any(
            a.state in {NodeRunState.RUNNING, NodeRunState.WAITING_EXTERNAL}
            for a in attempts.values()
        ):
            raise shared._error("ACTIVE_ATTEMPT", "请先结束当前任务，再改变准备路线。")
        report, original = _report(app, roles, attempts)
        if report is None or original is None:
            raise shared._error("DIAGNOSIS_REQUIRED", "请先完成工作源观察。")
        candidate = _candidate_report(app, roles, attempts)
        rate = request.target_frame_rate or report.target_frame_rate
        external = roles.get("external")
        if (
            request.route == "external"
            and candidate is not None
            and external is not None
            and (
                external.type_id.rsplit(".", 1)[-1] != request.external_format
                or external.parameters.get("target_frame_rate") != rate
            )
        ):
            # 改定义/参数会使候选失效；当前 A 的解释不能预授权尚未观察的 B。
            # declared_only 仍可建立新外部任务，但不再要求确认 A 的缺失色彩。
            if request.interpretation_policy != "declared_only":
                raise shared._error(
                    "COLOR_INTERPRETATION_REBIND",
                    "外部格式或帧率变化将生成新候选；请先采用默认声明政策，取得新候选后独立确认工作解释。",
                )
            candidate = None
        required = {
            item.id for item in _requirements(report, candidate) if request.route in item.routes
        }
        if len(request.confirmations) != len(set(request.confirmations)) or not required <= set(
            request.confirmations
        ):
            raise shared._error("CONFIRMATION_REQUIRED", "请明确确认所选路线的变化和必要解释。")
        if "color_interpretation" in required and request.interpretation_policy == "declared_only":
            raise shared._error(
                "COLOR_INTERPRETATION_REQUIRED", "缺少工作色彩解释，不能仅提交确认编号。"
            )
        signal_report = candidate if request.route == "external" else report
        if request.interpretation_policy != "declared_only" and (
            signal_report is None or not summary_for_report(signal_report)["can_interpret"]
        ):
            raise shared._error(
                "COLOR_INTERPRETATION_UNAVAILABLE", "不能在观察前或已知冲突时采用工作解释。"
            )
        action = next(a for a in _actions(report) if a.route == request.route)
        if not action.enabled:
            raise shared._error("STRATEGY_UNAVAILABLE", action.reason)
        if rate is None:
            raise shared._error("RATE_REQUIRED", "请明确选择目标精确帧率。")
        graph = build_preparation_graph(
            original.path,
            route=request.route,
            target_frame_rate=rate,
            external_format=request.external_format,
            interpretation_policy=request.interpretation_policy,
            audio_policy="reference"
            if request.route == "external"
            else "original"
            if report.audio
            else "none",
            retime_confirmed="retime" in request.confirmations,
            reference_change_confirmed="external_reference" in request.confirmations,
            diagnosis_target_frame_rate=cast(
                str | None, roles["diagnostics"].parameters.get("target_frame_rate")
            )
            if rate == report.target_frame_rate
            else rate,
        )
        positions = {n.node_id: n.ui_position for n in current.project.graph.nodes}
        graph = graph.model_copy(
            update={
                "nodes": tuple(
                    n.model_copy(update={"ui_position": positions.get(n.node_id, n.ui_position)})
                    for n in graph.nodes
                )
            }
        )
        GraphValidator(current.definitions).validate(graph)
        store.save(
            current.project.model_copy(update={"graph": graph}),
            current.definitions,
            expected_storage_revision=request.expected_storage_revision,
        )
        app._last_error = None
        return app.inspect()


def full(app: ProjectServiceApplication, payload: object, *, expand: bool) -> BaseModel:
    """绑定实际完成的 work 准入；复用同一精确建图与 CAS，不伪造未来 ID。"""
    request = shared._parse(WorkFullRequest, payload)
    with app._state:
        app._assert_idle()
        if app._desktop_closing:
            raise shared._error("CLOSING", "应用正在关闭。")
        current, run, roles, attempts = shared._bound(
            app,
            WorkViewRequest(
                contract_version="0.3.4-work.1",
                project_session_id=request.project_session_id,
                run_id=request.preparation_run_id,
            ),
            _check_graph,
        )
        store, _ = app._require_session()
        if store.load_authoring().storage_revision != request.expected_storage_revision:
            raise shared._error("STORAGE_CONFLICT", "工程已修改，请刷新。")
        binding = shared._completed_binding(
            app, current, run, roles, attempts, check_reference_binding
        )
        build = build_prepared_source(current, binding, request.processing, request.publication)
        if expand:
            store.save(
                build.project,
                build.definitions,
                expected_storage_revision=request.expected_storage_revision,
            )
            app._last_error = None
            return app.inspect()
        return WorkFullEnvelope(
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


def operation(
    app: ProjectServiceApplication, payload: object, *, cancel: bool, run_only: bool = False
) -> BaseModel:
    """任意自由图的实际受信 attempt；复用原临时操作身份，不增加 Runtime 状态。"""
    request = (
        shared._parse(WorkViewRequest, payload)
        if run_only
        else shared._parse(WorkOperationRequest, payload)
    )
    with app._state:
        run = operations._run(app, request)
        active = operations._active_attempt(app, run, definition_role)
        attempt = None
        if isinstance(request, WorkOperationRequest):
            attempt, _ = operations._attempt(run, request.node_run_id, definition_role)
        matches = active is not None and (
            attempt is None or attempt.node_run_id == active.node_run_id
        )
        if cancel:
            if not matches:
                raise shared._error("NO_ACTIVE_CHECK", "此任务当前没有正在执行的工作源操作。")
            assert app._preparation_cancel is not None
            app._preparation_cancel.set()
            return app.inspect()
        assert isinstance(request, WorkOperationRequest)
        return WorkOperationEnvelope(
            **request.model_dump(),
            active=matches,
            operation=app._active_operation if matches else None,
            stage_progress=shared._stage_progress(app, attempt) if matches else None,
            cancel_requested=bool(
                matches and app._preparation_cancel and app._preparation_cancel.is_set()
            ),
        )


def create(app: ProjectServiceApplication, payload: object) -> StatusEnvelope:
    """普通工作源复用同一排他工程创建事务，不自动启动或复制源媒体。"""
    return shared.create(
        app,
        payload,
        request_model=WorkCreateRequest,
        definitions_factory=source_preparation_definitions,
        graph_factory=build_preparation_graph,
    )


def dispatch(app: ProjectServiceApplication, action: str, payload: object) -> BaseModel:
    """只响应 work.1，旧路由的共享错误在这里换 wire 外壳，不改变错误含义。"""
    from .service import ProjectServiceError

    try:
        if action == "create":
            return create(app, payload)
        if action == "view":
            return view(app, payload)
        if action == "choose":
            return choose(app, payload)
        if action in {"cancel", "operation-cancel", "operation-view"}:
            return operation(
                app, payload, cancel=action != "operation-view", run_only=action == "cancel"
            )
        if action == "processing-preview":
            request = shared._parse(WorkProcessingRequest, payload)
            return WorkProcessingEnvelope(processing=request.processing)
        if action in {"full-preview", "expand"}:
            return full(app, payload, expand=action == "expand")
        raise shared._error("ROUTE", "未知工作源操作。")
    except ColorPreparedSourceError as error:
        failure = error.envelope.error
        raise WorkError(
            failure.code,
            failure.message,
            http_status=error.http_status,
            field_path=failure.field_path,
            related_run_ids=failure.related_run_ids,
        ) from error
    except (ChapterPlanningError, ContextPlanningError) as error:
        field = "settings" if isinstance(error, ChapterPlanningError) else "fi_profile"
        raise WorkError(
            error.code, error.message, field_path=("processing", field, *error.field_path)
        ) from error
    except ProjectServiceError as error:
        raise WorkError(
            error.code,
            error.message,
            http_status=error.http_status,
            related_run_ids=error.related_run_ids,
        ) from error
    except (Av27TemplateError, Av27MediaError, MediaNodeError) as error:
        raise WorkError(error.code, str(error)) from error
    except (
        ProjectStoreError,
        RuntimeRepositoryError,
        ValidationError,
        OSError,
        ValueError,
    ) as error:
        raise WorkError("E_PREPARED_SOURCE_OPERATION_FAILED", str(error)) from error
