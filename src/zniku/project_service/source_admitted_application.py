"""实现单一源流程的创建、显式换源和普通 Graph 展开；不引入第二份工程或 Runtime 状态。"""

from __future__ import annotations

import stat
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from zniku.avenhance_v27.probe import Av27MediaError
from zniku.avenhance_v27.template import (
    Av27TemplateError,
    PreparationBinding,
    PreparationSourceBinding,
    build_preparation,
    validate_prepare_paths,
)
from zniku.chapter_batch.definitions import definition as batch_definition
from zniku.chapter_batch.final_publish import definition as final_publication_definition
from zniku.chapter_batch.presentation import chapter_views
from zniku.chapter_overlap import ChapterPlanningError
from zniku.chapter_overlap.context import ContextPlanningError
from zniku.graph import Graph, GraphValidationError, GraphValidator, NodeInstance
from zniku.project import Project, ProjectSnapshot, ProjectStore, ProjectStoreError
from zniku.project.storage import new_project_storage
from zniku.runtime import NodeResult, NodeRunState, RunState, RuntimeRepositoryError
from zniku.source_admission import definitions
from zniku.source_admission.adapters import cancel_source
from zniku.source_admission.contracts import SOURCE_NAMESPACE, VERSION
from zniku.source_admission.mosaic_restoration import definition as mosaic_restoration_definition
from zniku.source_admission.naming import publication_target
from zniku.source_aligned.template import SourceAlignedBuild, build_source_aligned

from .models import StatusEnvelope
from .source_admitted import (
    CANCEL_ROUTE,
    CREATE_ROUTE,
    EXPAND_ROUTE,
    FULL_PREVIEW_ROUTE,
    PROCESSING_ROUTE,
    REPLACE_ROUTE,
    SourceAdmittedCancelRequest,
    SourceAdmittedCreateRequest,
    SourceAdmittedError,
    SourceAdmittedFullEnvelope,
    SourceAdmittedFullRequest,
    SourceAdmittedProcessingEnvelope,
    SourceAdmittedProcessingRequest,
    SourceAdmittedReplaceRequest,
)
from .storage_paths import prepare_storage_location

if TYPE_CHECKING:
    from .service import ProjectServiceApplication

ROUTES = frozenset(
    {CANCEL_ROUTE, CREATE_ROUTE, EXPAND_ROUTE, FULL_PREVIEW_ROUTE, PROCESSING_ROUTE, REPLACE_ROUTE}
)


def parse[M: BaseModel](model: type[M], payload: object) -> M:
    try:
        return model.model_validate(payload, strict=True)
    except ValidationError as error:
        first = error.errors(include_url=False, include_input=False)[0]
        raise SourceAdmittedError(
            "E_SOURCE_ADMITTED_REQUEST",
            first["msg"],
            field_path=tuple(
                part
                for part in first["loc"]
                if part
                not in {
                    "average",
                    "exact_frames",
                    "exact_times",
                    "external",
                    "off",
                }
            ),
        ) from error


def dispatch(app: ProjectServiceApplication, route: str, payload: object) -> BaseModel:
    """普通 HTTP facade；错误转换不吞掉存储冲突，且不执行隐式修复。"""
    from .service import ProjectServiceError

    try:
        if route == PROCESSING_ROUTE:
            request = parse(SourceAdmittedProcessingRequest, payload)
            return SourceAdmittedProcessingEnvelope(processing=request.processing)
        if route == CREATE_ROUTE:
            return create(app, parse(SourceAdmittedCreateRequest, payload))
        if route == REPLACE_ROUTE:
            return replace_source(app, parse(SourceAdmittedReplaceRequest, payload))
        if route == CANCEL_ROUTE:
            return cancel(app, parse(SourceAdmittedCancelRequest, payload))
        if route in {FULL_PREVIEW_ROUTE, EXPAND_ROUTE}:
            return full(
                app, parse(SourceAdmittedFullRequest, payload), expand=route == EXPAND_ROUTE
            )
        raise SourceAdmittedError("E_SOURCE_ADMITTED_ROUTE", "未知源准入路由", http_status=404)
    except SourceAdmittedError:
        raise
    except ProjectServiceError as error:
        raise SourceAdmittedError(
            error.code,
            error.message,
            http_status=error.http_status,
            related_run_ids=error.related_run_ids,
        ) from error
    except (ChapterPlanningError, ContextPlanningError) as error:
        field = "settings" if isinstance(error, ChapterPlanningError) else "fi_profile"
        raise SourceAdmittedError(
            error.code, error.message, field_path=("processing", field, *error.field_path)
        ) from error
    except (Av27TemplateError, Av27MediaError) as error:
        raise SourceAdmittedError(error.code, str(error)) from error
    except (
        ProjectStoreError,
        RuntimeRepositoryError,
        GraphValidationError,
        ValidationError,
        OSError,
    ) as error:
        failure = app._translate_failure(error)
        raise SourceAdmittedError(
            failure.code, failure.message, http_status=failure.http_status
        ) from error


def create(app: ProjectServiceApplication, request: SourceAdmittedCreateRequest) -> StatusEnvelope:
    """完整验证后创建独立工程，不先发布旧版工程再偷偷修改其 definition。"""
    with app._state:
        app._assert_idle()
        if app._desktop_closing:
            raise SourceAdmittedError("E_DESKTOP_CLOSING", "应用正在关闭", http_status=409)
        if request.request.source_mode != "program" or request.request.mr.mode != "off":
            raise SourceAdmittedError(
                "E_SOURCE_ADMITTED_MODE", "新源准入仅接受单一原片；MR 在处理设置中可选"
            )
        old = build_preparation(request.request)
        defs = (definitions.source_program_definition(), definitions.source_admission_definition())
        nodes = tuple(
            NodeInstance.model_validate({**node.model_dump(), "definition_version": VERSION})
            for node in old.project.graph.nodes
        )
        graph = Graph(nodes=nodes, edges=old.project.graph.edges)
        GraphValidator(defs).validate(graph)
        project = Project(project_id=old.project.project_id, name=old.project.name, graph=graph)
        path, _ = validate_prepare_paths(request.request)
        if (
            request.data_parent_directory is not None
            and not Path(request.data_parent_directory).is_absolute()
        ):
            raise SourceAdmittedError("E_PROJECT_STORAGE_PATH", "工作数据父目录必须为绝对路径")
        storage = new_project_storage(
            path,
            data_root=(
                Path(request.data_parent_directory) / path.with_suffix(".data").name
                if request.data_parent_directory is not None
                else None
            ),
            media_basename=request.media_basename
            or Path(request.request.sources[0].source_path).stem,
        )
        prepare_storage_location(storage, current=None)
        store = ProjectStore.create_atomically(path, project, defs, storage=storage)
        app._store, app._runtime = store, app._runtime_for(store)
        app._project_session_id, app._active_run_id, app._last_error = str(uuid4()), None, None
        return app.inspect()


def _preparation(current: ProjectSnapshot) -> ProjectSnapshot:
    """模板换源只接受本版的两节点原始分析图，不能覆盖自由编辑后的任何分支。"""
    graph = current.project.graph
    if len(graph.nodes) != 2 or len(graph.edges) != 1:
        raise SourceAdmittedError(
            "E_SOURCE_ADMITTED_ALREADY_EXPANDED",
            "当前图已展开或编辑，请新建分析工程；不会覆盖现有流程",
            http_status=409,
        )
    known = {(value.type_id, value.version): value for value in current.definitions}
    by_role = {
        definitions.definition_role(known[(n.type_id, n.definition_version)]): n
        for n in graph.nodes
    }
    if set(by_role) != {"source", "admission"}:
        raise SourceAdmittedError(
            "E_SOURCE_ADMITTED_PREPARATION", "不是本版单一源分析图", http_status=409
        )
    source, gate = by_role["source"], by_role["admission"]
    edge = graph.edges[0]
    if (
        edge.source_node_id,
        edge.source_port_id,
        edge.target_node_id,
        edge.target_port_id,
        edge.ordinal,
    ) != (source.node_id, "source_media", gate.node_id, "sources", 0):
        raise SourceAdmittedError(
            "E_SOURCE_ADMITTED_PREPARATION", "分析图连接不符", http_status=409
        )
    if (
        source.parameters.get("source_ordinal") != 0
        or gate.parameters.get("source_mode") != "program"
        or gate.model_dump(mode="json")["parameters"].get("sources")
        != [{"source_ordinal": 0, "source_node_id": source.node_id}]
    ):
        raise SourceAdmittedError(
            "E_SOURCE_ADMITTED_PREPARATION", "分析图参数不符", http_status=409
        )
    return current


def _execution_graph(graph: Graph) -> dict[str, object]:
    """与 Runtime snapshot 比较相同：位置只影响展示，业务参数和全部边仍精确比较。"""
    return graph.model_dump(exclude={"nodes": {"__all__": {"ui_position"}}})


def replace_source(
    app: ProjectServiceApplication, request: SourceAdmittedReplaceRequest
) -> StatusEnvelope:
    """明确换源后只改普通 Source 参数。历史结果保留，新 Run 通过普通 stale 规则重分析。"""
    with app._state:
        app._assert_idle()
        app.assert_preview_session(request.project_session_id)
        if app._desktop_closing:
            raise SourceAdmittedError("E_DESKTOP_CLOSING", "应用正在关闭", http_status=409)
        store, runtime = app._require_session()
        view = store.load_authoring()
        if view.storage_revision != request.expected_storage_revision:
            raise SourceAdmittedError(
                "E_PROJECT_STORAGE_CONFLICT", "工程已变化，请刷新", http_status=409
            )
        current = _preparation(view.snapshot)
        path = Path(request.source_path)
        if not path.is_absolute():
            raise SourceAdmittedError("E_SOURCE_ADMITTED_PATH", "候选路径必须为绝对路径")
        try:
            path = path.resolve(strict=True)
            if not path.is_file() or path.stat().st_size <= 0:
                raise SourceAdmittedError("E_SOURCE_ADMITTED_PATH", "候选必须是非空文件")
        except OSError as error:
            raise SourceAdmittedError(
                "E_SOURCE_ADMITTED_PATH",
                "候选文件当前不可读取，请重新选择",
                field_path=("source_path",),
            ) from error
        conflicts = runtime.repository.list_run_window(terminal=False)
        # 仅已停止的本分析图失败 attempt 可以被显式换源终结；任何其他 Run 都不越权处理。
        for run in conflicts:
            if (
                _execution_graph(run.graph_snapshot) != _execution_graph(current.project.graph)
                or run.definitions_snapshot != current.definitions
                or any(
                    node.state in {NodeRunState.RUNNING, NodeRunState.WAITING_EXTERNAL}
                    for node in run.node_runs
                )
                or not any(node.state is NodeRunState.FAILED for node in run.node_runs)
            ):
                raise SourceAdmittedError(
                    "E_PROJECT_SERVICE_RUN_CONFLICT",
                    "另一个 Run 尚未结束，请先处理",
                    http_status=409,
                    related_run_ids=(run.run_id,),
                )
        nodes = tuple(
            NodeInstance.model_validate(
                {
                    **node.model_dump(),
                    "parameters": {
                        **node.model_dump(mode="json")["parameters"],
                        "source_path": str(path),
                    },
                }
            )
            if node.type_id == definitions.source_program_definition().type_id
            else node
            for node in current.project.graph.nodes
        )
        updated = Project(
            project_id=current.project.project_id,
            name=current.project.name,
            graph=Graph(nodes=nodes, edges=current.project.graph.edges),
        )
        GraphValidator(current.definitions).validate(updated.graph)
        # 先结束操作者已明确替换的失败分析，再 CAS 改引用。任一 abandon 失败时源引用未动；
        # 若其后的 CAS 保存失败，只留下已结束的失败历史，不伪称全操作原子或恢复旧 Run。
        for run in conflicts:
            runtime.abandon_run(run.run_id)
        if conflicts:
            app._active_run_id = None
        try:
            store.save(
                updated,
                current.definitions,
                expected_storage_revision=request.expected_storage_revision,
            )
        except ProjectStoreError as error:
            if conflicts:
                raise SourceAdmittedError(
                    "E_SOURCE_ADMITTED_SAVE_AFTER_ABANDON",
                    "先前失败分析已结束，但候选引用未保存；原参考和媒体文件均未更换。请刷新工程后重试。",
                    http_status=409,
                    related_run_ids=tuple(run.run_id for run in conflicts),
                ) from error
            raise
        app._active_run_id, app._last_error = None, None
        return app.inspect()


def cancel(app: ProjectServiceApplication, request: SourceAdmittedCancelRequest) -> StatusEnvelope:
    with app._state:
        app.assert_preview_session(request.project_session_id)
        _store, runtime = app._require_session()
        run = runtime.repository.get_run(request.run_id)
        if run.run_id != app._active_run_id:
            raise SourceAdmittedError(
                "E_SOURCE_ADMITTED_CANCEL_STALE", "不是当前分析 Run", http_status=409
            )
        definitions_by_key = {(d.type_id, d.version): d for d in run.definitions_snapshot}
        nodes = {n.node_id: n for n in run.graph_snapshot.nodes}
        matches = [
            attempt
            for attempt in run.node_runs
            if attempt.state is NodeRunState.RUNNING
            and definitions.definition_role(
                definitions_by_key[
                    (nodes[attempt.node_id].type_id, nodes[attempt.node_id].definition_version)
                ]
            )
            == "source"
        ]
        if len(matches) != 1 or not cancel_source(matches[0].node_run_id):
            raise SourceAdmittedError(
                "E_SOURCE_ADMITTED_CANCEL_STALE",
                "分析已结束或取消句柄尚未就绪，请刷新",
                http_status=409,
            )
        return app.inspect()


def resolve_binding(
    app: ProjectServiceApplication, run_id: str
) -> tuple[ProjectSnapshot, PreparationBinding]:
    """从同一当前图、指定 completed Run 和 current latest 精确解析，不采用浏览器 N/FPS。"""
    store, runtime = app._require_session()
    current = _preparation(store.load())
    run = runtime.repository.get_run(run_id)
    if run.project_id != current.project.project_id or run.state is not RunState.COMPLETED:
        raise SourceAdmittedError(
            "E_SOURCE_ADMITTED_RUN_INCOMPLETE", "必须指定当前工程已完成的分析 Run", http_status=409
        )
    if (
        _execution_graph(run.graph_snapshot) != _execution_graph(current.project.graph)
        or run.definitions_snapshot != current.definitions
    ):
        raise SourceAdmittedError(
            "E_SOURCE_ADMITTED_STALE", "分析后图或精确定义已变化，请重新分析", http_status=409
        )
    results: dict[str, NodeResult] = {}
    for node in current.project.graph.nodes:
        attempts = [value for value in run.node_runs if value.node_id == node.node_id]
        if not attempts:
            raise SourceAdmittedError(
                "E_SOURCE_ADMITTED_RUN_INCOMPLETE", "指定 Run 未覆盖分析节点", http_status=409
            )
        attempt = max(attempts, key=lambda value: value.attempt)
        latest = runtime.repository.get_latest(node.node_id)
        if attempt.state is not NodeRunState.COMPLETED or latest is None or latest.stale:
            raise SourceAdmittedError(
                "E_SOURCE_ADMITTED_STALE", "分析结果不再有效", http_status=409
            )
        result = runtime.repository.get_result(latest.result_id)
        if (
            (
                attempt.reused_from_result_id is not None
                and result.result_id != attempt.reused_from_result_id
            )
            or (attempt.reused_from_result_id is None and result.node_run_id != attempt.node_run_id)
            or tuple(a.artifact_id for a in result.outputs) != attempt.output_artifact_ids
        ):
            raise SourceAdmittedError(
                "E_SOURCE_ADMITTED_STALE", "指定 Run 的结果已被替换", http_status=409
            )
        for artifact in result.outputs:
            try:
                facts = Path(artifact.path).stat()
            except OSError as error:
                raise SourceAdmittedError(
                    "E_SOURCE_ADMITTED_INPUT_CHANGED",
                    "分析输入或 gate 已不可读取，请重新分析",
                    http_status=409,
                ) from error
            if not stat.S_ISREG(facts.st_mode) or (facts.st_size, facts.st_mtime_ns) != (
                artifact.size,
                artifact.mtime_ns,
            ):
                raise SourceAdmittedError(
                    "E_SOURCE_ADMITTED_INPUT_CHANGED",
                    "分析输入或 gate 已变化，请重新分析",
                    http_status=409,
                )
        results[node.node_id] = result
    source = next(
        n
        for n in current.project.graph.nodes
        if n.type_id == definitions.source_program_definition().type_id
    )
    gate = next(
        n
        for n in current.project.graph.nodes
        if n.type_id == definitions.source_admission_definition().type_id
    )
    source_media = app._artifact_for_port(
        results[source.node_id], port_id="source_media", kind="MediaFile"
    )
    source_video = app._artifact_for_port(
        results[source.node_id], port_id="video", kind="VideoFile"
    )
    marker = source_video.media_info.get(SOURCE_NAMESPACE)
    if not isinstance(marker, Mapping) or marker.get("contract_version") != VERSION:
        raise SourceAdmittedError(
            "E_SOURCE_ADMITTED_PROVENANCE", "缺少新 Source 准入身份", http_status=409
        )
    return current, PreparationBinding(
        project_id=current.project.project_id,
        preparation_run_id=run_id,
        source_mode="program",
        mr_mode="off",
        admission_node_id=gate.node_id,
        admission_artifact=app._artifact_for_port(
            results[gate.node_id], port_id="gate", kind="DataFile"
        ),
        sources=(
            PreparationSourceBinding(
                source_ordinal=0,
                source_node_id=source.node_id,
                source_media_artifact=source_media,
                effective_video_artifact=source_video,
            ),
        ),
    )


def full(
    app: ProjectServiceApplication,
    request: SourceAdmittedFullRequest,
    *,
    expand: bool,
    _build_transform: Callable[[SourceAlignedBuild], SourceAlignedBuild] | None = None,
) -> SourceAdmittedFullEnvelope | StatusEnvelope:
    with app._state:
        app._assert_idle()
        app.assert_preview_session(request.project_session_id)
        if app._desktop_closing:
            raise SourceAdmittedError("E_DESKTOP_CLOSING", "应用正在关闭", http_status=409)
        store, _runtime = app._require_session()
        current = store.load_authoring()
        if current.storage_revision != request.expected_storage_revision:
            raise SourceAdmittedError(
                "E_PROJECT_STORAGE_CONFLICT", "工程已变化，请刷新", http_status=409
            )
        preparation, binding = resolve_binding(app, request.preparation_run_id)
        build = build_source_aligned(
            preparation,
            binding,
            request.processing,
            request.publication,
            definition_factory=batch_definition,
            external_factory=lambda _container: mosaic_restoration_definition(),
            publication_target=publication_target,
            batch_enhancement=True,
            final_publication_factory=final_publication_definition,
        )
        if _build_transform is not None:
            build = _build_transform(build)
        if expand:
            store.save(
                build.project,
                build.definitions,
                expected_storage_revision=request.expected_storage_revision,
                studio_state=chapter_views(build, current.studio_state),
            )
            app._last_error = None
            return app.inspect()
        marker = binding.sources[0].effective_video_artifact.media_info[SOURCE_NAMESPACE]
        warnings = cast(Mapping[str, object], marker).get("warnings", ())
        return SourceAdmittedFullEnvelope(
            project_session_id=request.project_session_id,
            storage_revision=current.storage_revision,
            preparation_run_id=binding.preparation_run_id,
            processing=request.processing,
            plan=build.plan,
            contexts=build.contexts,
            publication=build.publication,
            node_count=len(build.project.graph.nodes),
            edge_count=len(build.project.graph.edges),
            warnings=cast(tuple[str, ...], tuple(cast(list[str], warnings))),
        )
