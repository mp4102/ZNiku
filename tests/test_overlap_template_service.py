"""用合成已接纳分析验证新链普通图展开、会话CAS和用户图保护，不运行真实FI。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from test_chapter_overlap_service import _ready
from test_project_service_host import _request, _serve
from zniku.graph import GraphValidator
from zniku.project import ProjectStore
from zniku.project_service.chapter_overlap import (
    ChapterOverlapFullEnvelope,
    ChapterOverlapPreviewError,
)
from zniku.project_service.overlap_application import full_overlap, processing_preview
from zniku.runtime import RuntimeRepository


def _request_data(tmp_path: Path, *, count: int = 3) -> tuple[Any, dict[str, Any], Path]:
    app, request, path = _ready(tmp_path)
    request.update(
        {
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
    preview = full_overlap(app, request, expand=False)
    assert isinstance(preview, ChapterOverlapFullEnvelope)
    assert path.read_bytes() == before
    assert preview.plan.chapter_count == preview.plan.leaf_count == 3
    assert preview.contexts.encoded_frame_count == 7200
    assert preview.processing.fi_profile.software_version == "v1.0"
    assert preview.processing.fi_profile.model_name == "Aion"
    assert preview.status == "pending_real_acceptance"
    # 合成原片1280x720，Split规范为1080p后再2x增强，不能按原片尺寸误算为1440p。
    assert "2160p" in preview.publication.output_target_path
    assert ChapterOverlapFullEnvelope.model_validate_json(preview.model_dump_json()) == preview
    result = full_overlap(app, request, expand=True)
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
    with pytest.raises(ChapterOverlapPreviewError, match="E_OVERLAP_GRAPH_ALREADY_EXPANDED"):
        full_overlap(app, request, expand=True)
    assert path.read_bytes() == original


def test_all_intersecting_chapters_are_explicit_ordered_edges(tmp_path: Path) -> None:
    app, request, path = _request_data(tmp_path)
    request["processing"]["settings"]["chapter_selector"] = {
        "mode": "exact_frames",
        "frames": [1, 2],
    }
    preview = full_overlap(app, request, expand=False)
    assert isinstance(preview, ChapterOverlapFullEnvelope)
    assert len(preview.contexts.chapters[0].sources) == 3
    full_overlap(app, request, expand=True)
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
    with pytest.raises(ChapterOverlapPreviewError):
        full_overlap(app, request, expand=True)
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
    with pytest.raises(ChapterOverlapPreviewError):
        processing_preview(
            {
                "contract_version": "0.3.2",
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
            "/api/studio/templates/chapter-overlap-fi/processing-preview",
            method="POST",
            payload={
                "contract_version": "0.3.2",
                "processing": request["processing"],
            },
        )
        assert status == 200 and response["status"] == "pending_real_acceptance"
        status, response, _ = _request(
            url,
            "/api/studio/templates/chapter-overlap-fi/full-preview",
            method="POST",
            payload=request,
        )
        assert status == 200
        ChapterOverlapFullEnvelope.model_validate_json(json.dumps(response))
        request["processing"]["settings"]["chapter_selector"] = {
            "mode": "exact_times",
            "times": ["00:60:00"],
        }
        status, response, _ = _request(
            url, "/api/studio/templates/chapter-overlap-fi/expand", method="POST", payload=request
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
