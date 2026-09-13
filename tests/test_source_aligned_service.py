"""用合成已接纳分析验证新链普通图展开、会话CAS和用户图保护，不运行真实FI。"""

from __future__ import annotations

import json
from itertools import combinations, pairwise
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from authoring_helpers import authoring_command
from test_av27_template import _prepare_payload
from test_chapter_overlap_service import _ready
from test_project_service_host import _request, _serve
from zniku.avenhance_v27.probe import Av27MediaError
from zniku.graph import Graph, GraphValidator
from zniku.project import ProjectStore
from zniku.project_service import source_aligned_application
from zniku.project_service.service import ProjectServiceApplication
from zniku.project_service.source_aligned import (
    SourceAlignedError,
    SourceAlignedFailureEnvelope,
    SourceAlignedFullEnvelope,
    SourceAlignedFullRequest,
)
from zniku.project_service.source_aligned_application import full_source_aligned, processing_preview
from zniku.runtime import RuntimeRepository
from zniku.source_aligned.node_contracts import SourceExpectation


@pytest.fixture(autouse=True)
def synthetic_audio_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    """本模块文本媒体只验证Service authority；真实短音轨检查由媒体测试及smoke覆盖。"""
    monkeypatch.setattr(
        source_aligned_application, "require_supported_audio_origins", lambda _: None
    )


def _request_data(tmp_path: Path, *, count: int = 3) -> tuple[Any, dict[str, Any], Path]:
    app, request, path = _ready(tmp_path)
    request.update(
        {
            "contract_version": "0.3.3",
            "processing": {
                "settings": {
                    "chapter_selector": {"mode": "average", "count": count},
                    "leaf_max_minutes": 1,
                },
                "enhancement": {"model_name": "Synthetic enhancement", "actual_scale_factor": 2},
                "program_encode": {"encoder": "cpu"},
            },
            "publication": {
                "output_root": str(tmp_path),
                "title": "Example",
                "year": "2026",
                "overwrite": False,
            },
        }
    )
    return app, request, path


def test_full_preview_expand_reopen_same_graph_and_protect_second_expansion(tmp_path: Path) -> None:
    app, request, path = _request_data(tmp_path)
    before = path.read_bytes()
    preview = full_source_aligned(app, request, expand=False)
    assert isinstance(preview, SourceAlignedFullEnvelope)
    assert path.read_bytes() == before
    assert preview.plan.chapter_count == preview.plan.leaf_count == 3
    assert preview.contexts.encoded_frame_count == 7200
    assert preview.processing.fi_profile.software_version == "v1.0"
    assert preview.processing.fi_profile.model_name == "Aion"
    assert preview.status == "pending_real_acceptance"
    # 合成原片1280x720，Split规范为1080p后再2x增强，不能按原片尺寸误算为1440p。
    assert "2160p" in preview.publication.output_target_path
    assert SourceAlignedFullEnvelope.model_validate_json(preview.model_dump_json()) == preview
    result = full_source_aligned(app, request, expand=True)
    assert result.contract_version == "0.3.0"
    saved = ProjectStore.open(path).load()
    GraphValidator(saved.definitions).validate(saved.project.graph)
    assert len(saved.project.graph.nodes) == preview.node_count
    assert len(saved.project.graph.edges) == preview.edge_count
    assert {
        node.type_id
        for node in saved.project.graph.nodes
        if node.type_id.startswith("zniku.overlap.")
    } == {
        "zniku.overlap.split.leaves.3",
        "zniku.overlap.enhancement.external",
        "zniku.overlap.merge_video",
        "zniku.overlap.fi_context",
        "zniku.overlap.frame_interpolation.external",
        "zniku.overlap.fi_crop",
        "zniku.overlap.program_encode",
        "zniku.overlap.final_mux",
    }
    # 仅保存Graph，不能生成新Run/attempt/Artifact、不能自动提交外部结果。
    assert len(RuntimeRepository.open(path).list_runs()) == 1
    request["expected_storage_revision"] = result.storage_revision
    original = path.read_bytes()
    with pytest.raises(SourceAlignedError, match="E_SOURCE_ALIGNED_GRAPH_ALREADY_EXPANDED"):
        full_source_aligned(app, request, expand=True)
    assert path.read_bytes() == original


def test_all_intersecting_chapters_are_explicit_ordered_edges(tmp_path: Path) -> None:
    app, request, path = _request_data(tmp_path)
    request["processing"]["settings"]["chapter_selector"] = {
        "mode": "exact_frames",
        "frames": [1, 2],
    }
    preview = full_source_aligned(app, request, expand=False)
    assert isinstance(preview, SourceAlignedFullEnvelope)
    assert len(preview.contexts.chapters[0].sources) == 3
    full_source_aligned(app, request, expand=True)
    graph = ProjectStore.open(path).load().project.graph
    incoming = [edge for edge in graph.edges if edge.target_node_id == "overlap.context.A"]
    assert [(edge.source_node_id, edge.ordinal) for edge in incoming] == [
        ("overlap.merge.A", 0),
        ("overlap.merge.B", 1),
        ("overlap.merge.C", 2),
    ]


@pytest.mark.parametrize(
    "field", ["project_session_id", "expected_storage_revision", "preparation_run_id"]
)
def test_expand_rejects_stale_binding_without_mutation(tmp_path: Path, field: str) -> None:
    app, request, path = _request_data(tmp_path)
    request[field] = (
        request[field] + 1
        if field == "expected_storage_revision"
        else "00000000-0000-4000-8000-000000000000"
    )
    before = path.read_bytes()
    with pytest.raises(SourceAlignedError):
        full_source_aligned(app, request, expand=True)
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "extra",
    [
        {"software_version": "2.7.0"},
        {"model_version": "v1.0"},
        {"model_name": "unknown"},
        {"right_context_frames": 0},
        {"minimum_input_frames": True},
        {"status": "verified"},
        {"left_context_frames": 241},
    ],
)
def test_candidate_unknown_or_unmeasured_claims_fail_closed(extra: dict[str, Any]) -> None:
    with pytest.raises(SourceAlignedError):
        processing_preview(
            {
                "contract_version": "0.3.3",
                "processing": {
                    "enhancement": {"model_name": "Synthetic"},
                    "program_encode": {"encoder": "cpu"},
                    "fi_profile": extra,
                },
            }
        )


def test_new_http_routes_and_row_errors_preserve_project(tmp_path: Path) -> None:
    app, request, path = _request_data(tmp_path)
    before = path.read_bytes()
    with _serve(app) as (url, _server):
        status, response, _ = _request(
            url,
            "/api/studio/templates/source-aligned-overlap/processing-preview",
            method="POST",
            payload={
                "contract_version": "0.3.3",
                "processing": request["processing"],
            },
        )
        assert status == 200 and response["status"] == "pending_real_acceptance"
        status, response, _ = _request(
            url,
            "/api/studio/templates/source-aligned-overlap/full-preview",
            method="POST",
            payload=request,
        )
        assert status == 200
        SourceAlignedFullEnvelope.model_validate_json(json.dumps(response))
        request["processing"]["settings"]["chapter_selector"] = {
            "mode": "exact_times",
            "times": ["00:60:00"],
        }
        status, response, _ = _request(
            url,
            "/api/studio/templates/source-aligned-overlap/expand",
            method="POST",
            payload=request,
        )
        assert status == 422
        assert response["error"]["field_path"] == [
            "processing",
            "settings",
            "chapter_selector",
            "times",
            0,
        ]
    assert path.read_bytes() == before


def _external(container: str = "mp4") -> dict[str, Any]:
    return {
        "mode": "external",
        "model_name": "Synthetic restoration",
        "model_version": None,
        "declared_container": container,
        "operator_frame_order_confirmed": True,
    }


@pytest.mark.parametrize("container", ["mp4", "mov", "mkv"])
def test_optional_mr_expands_before_external_result_exists_and_preserves_original_source(
    tmp_path: Path, container: str
) -> None:
    """分析后切换前处理无需重跑；计划绑定原片，外部结果只有普通边，没有未来 UUID。"""
    app, request, path = _request_data(tmp_path)
    initial = app.preview_source_aligned(request)
    runs_before = RuntimeRepository.open(path).list_runs()
    request["processing"]["mr"] = _external(container)
    before = path.read_bytes()
    preview = app.preview_source_aligned(request)
    assert path.read_bytes() == before
    assert preview.plan == initial.plan
    assert preview.contexts == initial.contexts
    assert preview.node_count == initial.node_count + 1
    assert preview.edge_count == initial.edge_count + 2
    assert "MR Enhanced" in preview.publication.output_target_path
    Draft202012Validator(SourceAlignedFullRequest.model_json_schema()).validate(request)
    Draft202012Validator(SourceAlignedFullEnvelope.model_json_schema()).validate(
        preview.model_dump(mode="json")
    )
    result = app.expand_source_aligned(request)
    assert result.snapshot is not None
    graph = ProjectStore.open(path).load().project.graph
    nodes = {node.node_id: node for node in graph.nodes}
    mr = nodes["source-aligned.mr"]
    assert mr.type_id == f"zniku.source_aligned.external.{container}"
    assert mr.definition_version == "0.3.3"
    split = nodes["overlap.split"]
    assert set(split.parameters) == {"plan", "source"}
    source = SourceExpectation.model_validate(split.model_dump(mode="json")["parameters"]["source"])
    assert source.original_video_artifact_id == preview.plan.source.artifact_id
    assert "effective_video_artifact_id" not in json.dumps(graph.model_dump(mode="json"))
    assert [
        (edge.source_node_id, edge.target_port_id)
        for edge in graph.edges
        if edge.target_node_id == mr.node_id
    ] == [("source.program", "video"), ("admission", "gate")]
    assert (
        next(
            edge
            for edge in graph.edges
            if edge.target_node_id == split.node_id and edge.target_port_id == "videos"
        ).source_node_id
        == mr.node_id
    )
    assert (
        next(
            edge
            for edge in graph.edges
            if edge.target_node_id == "overlap.final" and edge.target_port_id == "sources"
        ).source_node_id
        == "source.program"
    )
    assert all(
        node.definition_version == "0.3.3"
        for node in graph.nodes
        if node.type_id.startswith("zniku.overlap.")
    )
    assert RuntimeRepository.open(path).list_runs() == runs_before


@pytest.mark.parametrize(
    "mr",
    [
        {"mode": "external", "model_name": "Synthetic"},
        {**_external(), "operator_frame_order_confirmed": False},
        {**_external(), "operator_frame_order_confirmed": 1},
        {**_external(), "operator_frame_order_confirmed": "true"},
        {**_external(), "declared_container": "avi"},
        {**_external(), "model_name": " "},
        {**_external(), "model_version": ""},
        {**_external(), "effective_video_artifact_id": str(uuid4())},
        {"mode": "off", "model_name": "Synthetic"},
        {"mode": "command", "executable": "anything"},
    ],
)
def test_mr_unknown_or_unconfirmed_intent_fails_closed(mr: dict[str, Any]) -> None:
    with pytest.raises(SourceAlignedError) as captured:
        processing_preview(
            {
                "contract_version": "0.3.3",
                "processing": {
                    "mr": mr,
                    "enhancement": {"model_name": "Synthetic"},
                    "program_encode": {"encoder": "cpu"},
                },
            }
        )
    assert captured.value.envelope.error.code == "E_SOURCE_ALIGNED_REQUEST_INVALID"
    assert captured.value.envelope.error.field_path[:2] == ("processing", "mr")


def test_processing_defaults_and_source_facts_cannot_be_sent_by_client(tmp_path: Path) -> None:
    app, request, path = _request_data(tmp_path)
    result = app.preview_source_aligned_processing(
        {
            "contract_version": "0.3.3",
            "processing": request["processing"],
        }
    )
    assert result.processing.mr.mode == "off"
    before = path.read_bytes()
    changes: tuple[dict[str, Any], ...] = (
        {"source": {}},
        {"frame_count": 12},
        {"contract_version": "0.3.2"},
    )
    for extra in changes:
        with pytest.raises(SourceAlignedError):
            app.expand_source_aligned({**request, **extra})
    assert path.read_bytes() == before


@pytest.mark.parametrize("target", ["source.mkv", "admission.json"])
@pytest.mark.parametrize("missing", [False, True])
def test_changed_analysis_inputs_do_not_create_graph_or_run(
    tmp_path: Path, target: str, missing: bool
) -> None:
    app, request, path = _request_data(tmp_path)
    (item,) = tmp_path.rglob(target)
    if missing:
        item.unlink()
    else:
        item.write_bytes(b"changed synthetic source")
    before = path.read_bytes()
    with pytest.raises(SourceAlignedError, match="E_SOURCE_ALIGNED_INPUT_CHANGED"):
        app.expand_source_aligned(request)
    assert path.read_bytes() == before


def test_old_mr_preparation_gets_explicit_migration_error_even_without_completed_run(
    tmp_path: Path,
) -> None:
    app = ProjectServiceApplication(work_root=tmp_path / "work")
    prepare = _prepare_payload(tmp_path, mr_mode="external")
    authoring_command(app, {"operation": "create_av_enhance_v27", "request": prepare})
    status = app.inspect()
    path = Path(cast(str, prepare["project_path"]))
    before = path.read_bytes()
    with pytest.raises(SourceAlignedError, match="E_SOURCE_ALIGNED_LEGACY_PREPARATION"):
        app.preview_source_aligned(
            {
                "contract_version": "0.3.3",
                "project_session_id": status.project_session_id,
                "expected_storage_revision": status.storage_revision,
                "preparation_run_id": str(uuid4()),
                "processing": {
                    "enhancement": {"model_name": "Synthetic"},
                    "program_encode": {"encoder": "cpu"},
                },
                "publication": {
                    "output_root": str(tmp_path),
                    "title": "Example",
                    "year": "2026",
                    "overwrite": False,
                },
            }
        )
    assert path.read_bytes() == before
    assert RuntimeRepository.open(path).list_runs() == ()


def test_hand_edited_graph_is_not_overwritten_by_template(tmp_path: Path) -> None:
    app, request, path = _request_data(tmp_path)
    store = ProjectStore.open(path)
    loaded = store.load_authoring()
    graph = loaded.snapshot.project.graph
    extra = graph.nodes[0].model_copy(update={"node_id": "custom.source"})
    changed = loaded.snapshot.project.model_copy(
        update={
            "graph": Graph(nodes=(*graph.nodes, extra), edges=graph.edges),
        }
    )
    store.save(
        changed, loaded.snapshot.definitions, expected_storage_revision=loaded.storage_revision
    )
    request["expected_storage_revision"] = app.inspect().storage_revision
    before = path.read_bytes()
    with pytest.raises(SourceAlignedError):
        app.expand_source_aligned(request)
    assert path.read_bytes() == before


def test_http_expand_accepts_mr_then_rejects_second_expand_and_old_version(tmp_path: Path) -> None:
    app, request, path = _request_data(tmp_path)
    request["processing"]["mr"] = _external()
    route = "/api/studio/templates/source-aligned-overlap/expand"
    with _serve(app) as (url, _server):
        status, result, _ = _request(url, route, method="POST", payload=request)
        assert status == 200 and result["contract_version"] == "0.3.0"
        request["expected_storage_revision"] = result["storage_revision"]
        before = path.read_bytes()
        status, failure, _ = _request(url, route, method="POST", payload=request)
        assert status == 409
        checked = SourceAlignedFailureEnvelope.model_validate_json(json.dumps(failure))
        assert checked.error.code == "E_SOURCE_ALIGNED_GRAPH_ALREADY_EXPANDED"
        status, failure, _ = _request(
            url, route, method="POST", payload={**request, "contract_version": "0.3.2"}
        )
        assert status == 422 and failure["contract_version"] == "0.3.3"
        assert path.read_bytes() == before


@pytest.mark.parametrize("expand", [False, True])
def test_source_audio_priming_is_rejected_before_expensive_graph_or_run_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, expand: bool
) -> None:
    """模型/分章意图检查无需媒体，完整预览与展开必须在同一原片上早期失败关闭。"""
    app, request, path = _request_data(tmp_path)
    calls: list[Path] = []

    def unsupported(source: Path) -> None:
        calls.append(source)
        raise Av27MediaError(
            "E_SOURCE_ALIGNED_AUDIO_PRIMING_UNSUPPORTED", "synthetic unsupported audio priming"
        )

    monkeypatch.setattr(source_aligned_application, "require_supported_audio_origins", unsupported)
    before = path.read_bytes()
    app.preview_source_aligned_processing(
        {
            "contract_version": "0.3.3",
            "processing": request["processing"],
        }
    )
    assert not calls
    with pytest.raises(SourceAlignedError) as captured:
        full_source_aligned(app, request, expand=expand)
    assert captured.value.envelope.error.code == "E_SOURCE_ALIGNED_AUDIO_PRIMING_UNSUPPORTED"
    assert captured.value.envelope.error.field_path == ("source",)
    assert calls == [tmp_path / "source.mkv"]
    assert path.read_bytes() == before
    assert len(RuntimeRepository.open(path).list_runs()) == 1


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("chapter_count", [1, 3])
def test_initial_layout_preserves_preparation_and_reserves_non_overlapping_cards(
    tmp_path: Path, external: bool, chapter_count: int
) -> None:
    """只测试新模板初始位置；MR独占列，单章多叶也不以180间距叠住较高卡片。"""
    app, request, path = _request_data(tmp_path, count=chapter_count)
    before = ProjectStore.open(path).load().project.graph
    request["processing"]["mr"] = _external() if external else {"mode": "off"}
    app.expand_source_aligned(request)
    graph = ProjectStore.open(path).load().project.graph
    nodes = {node.node_id: node for node in graph.nodes}
    assert tuple(nodes[node.node_id] for node in before.nodes) == before.nodes
    assert all(node.ui_position is not None for node in graph.nodes)
    # 卡宽与Studio正式CSS一致；220高为初始普通卡保守预算，32通道避免边/端口贴住相邻卡。
    width, height, gap = 248, 220, 32
    for left, right in combinations(graph.nodes, 2):
        assert left.ui_position is not None and right.ui_position is not None
        a, b = left.ui_position, right.ui_position
        assert (
            a.x + width + gap <= b.x
            or b.x + width + gap <= a.x
            or a.y + height + gap <= b.y
            or b.y + height + gap <= a.y
        ), (left.node_id, right.node_id)
    ordered = ["source.program", "admission"]
    if external:
        ordered.append("source-aligned.mr")
    ordered.append("overlap.split")
    positions = [nodes[node].ui_position for node in ordered]
    assert all(position is not None for position in positions)
    xs = [position.x for position in positions if position is not None]
    assert all(start + width + gap <= end for start, end in pairwise(xs))
