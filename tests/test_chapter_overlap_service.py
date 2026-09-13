"""以纯合成已完成分析验证新版只读预览；不解码真实媒体、不制造执行入口。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

from test_av27_template_service import _create_application, _seed_completed_preparation
from test_project_service_host import _request, _serve
from zniku.project_service.chapter_overlap import (
    CHAPTER_OVERLAP_PREVIEW_ROUTE,
    ChapterOverlapFailureEnvelope,
    ChapterOverlapPreviewEnvelope,
    ChapterOverlapPreviewError,
    ChapterOverlapPreviewRequest,
)
from zniku.project_service.service import ProjectServiceApplication
from zniku.runtime.models import StaleReason
from zniku.runtime.repository import RuntimeRepository


def _ready(tmp_path: Path) -> tuple[ProjectServiceApplication, dict[str, Any], Path]:
    app, prepare = _create_application(tmp_path)
    path = Path(cast(str, prepare["project_path"]))
    run_id, _ids = _seed_completed_preparation(path, tmp_path)
    status = app.inspect()
    assert status.snapshot is not None
    return (
        app,
        {
            "contract_version": "0.3.2",
            "project_session_id": status.project_session_id,
            "expected_storage_revision": status.storage_revision,
            "preparation_run_id": run_id,
        },
        path,
    )


def _files(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def test_default_preview_is_bound_read_only_and_round_trips(tmp_path: Path) -> None:
    app, payload, path = _ready(tmp_path)
    before_bytes = path.read_bytes()
    before_files = _files(tmp_path)
    before_status = app.inspect().model_dump(mode="json")
    first = app.preview_chapter_overlap(payload)
    second = app.preview_chapter_overlap(payload)
    assert first == second
    assert first.execution_available is False
    assert first.plan.settings.leaf_max_minutes == 5
    assert first.plan.chapter_count == first.plan.leaf_count == 1
    assert first.plan.source.frame_count == 3600
    assert first.plan.source.frame_rate == "30/1"
    assert first.plan.chapters[0].frame_count == 3600
    assert ChapterOverlapPreviewEnvelope.model_validate_json(first.model_dump_json()) == first
    assert path.read_bytes() == before_bytes
    assert _files(tmp_path) == before_files
    assert app.inspect().model_dump(mode="json") == before_status
    for model, data in (
        (ChapterOverlapPreviewRequest, payload),
        (ChapterOverlapPreviewEnvelope, first.model_dump(mode="json")),
    ):
        Draft202012Validator(model.model_json_schema()).validate(data)


@pytest.mark.parametrize(
    ("settings", "chapter_sizes", "leaf_sizes"),
    [
        ({"chapter_selector": {"mode": "average", "count": 3}}, [1200] * 3, [1200] * 3),
        (
            {"chapter_selector": {"mode": "exact_frames", "frames": [899]}},
            [899, 2701],
            [899, 2701],
        ),
        (
            {"chapter_selector": {"mode": "exact_times", "times": ["00:01:00"]}},
            [1800, 1800],
            [1800, 1800],
        ),
        ({"leaf_max_minutes": 1}, [3600], [1800, 1800]),
    ],
)
def test_preview_uses_server_facts_and_new_leaf_rules(
    tmp_path: Path, settings: dict[str, Any], chapter_sizes: list[int], leaf_sizes: list[int]
) -> None:
    app, payload, _path = _ready(tmp_path)
    plan = app.preview_chapter_overlap({**payload, "settings": settings}).plan
    assert [chapter.frame_count for chapter in plan.chapters] == chapter_sizes
    assert [leaf.frame_count for chapter in plan.chapters for leaf in chapter.leaves] == leaf_sizes


@pytest.mark.parametrize(
    ("extra", "field"),
    [
        ({"contract_version": "0.3.0"}, "contract_version"),
        ({"frame_count": 1}, "frame_count"),
        ({"frame_rate": "60/1"}, "frame_rate"),
        ({"graph": {}}, "graph"),
        ({"executable": "anything"}, "executable"),
        ({"settings": {"leaf_max_minutes": 0}}, "settings"),
        ({"settings": {"leaf_max_minutes": 61}}, "settings"),
        ({"settings": {"leaf_max_minutes": True}}, "settings"),
        ({"settings": {"leaf_max_minutes": 5.0}}, "settings"),
        ({"settings": {"leaf_duration_minutes": 5}}, "settings"),
        ({"settings": {"chapter_selector": {"mode": "single"}}}, "settings"),
        (
            {"settings": {"chapter_selector": {"mode": "exact_times", "times": ["60/1"]}}},
            "settings",
        ),
    ],
)
def test_invalid_wire_fails_closed_without_project_change(
    tmp_path: Path, extra: dict[str, Any], field: str
) -> None:
    app, payload, path = _ready(tmp_path)
    before = path.read_bytes()
    with pytest.raises(ChapterOverlapPreviewError) as captured:
        app.preview_chapter_overlap({**payload, **extra})
    error = captured.value.envelope.error
    assert error.code == "E_OVERLAP_REQUEST_INVALID"
    assert error.field_path[0] == field
    assert path.read_bytes() == before


def test_semantic_failure_identifies_exact_cut_row(tmp_path: Path) -> None:
    app, payload, _path = _ready(tmp_path)
    with pytest.raises(ChapterOverlapPreviewError) as captured:
        app.preview_chapter_overlap(
            {
                **payload,
                "settings": {"chapter_selector": {"mode": "exact_frames", "frames": [3600]}},
            }
        )
    assert captured.value.envelope.error.code == "E_CHAPTER_CUT_RANGE"
    assert captured.value.envelope.error.field_path == ("settings", "chapter_selector", "frames", 0)


@pytest.mark.parametrize("timecode", ["aa:bb:cc", "00:60:00", "00:00:60", "30:00", "60/1"])
def test_generated_request_schema_rejects_bad_time_syntax(tmp_path: Path, timecode: str) -> None:
    _app, payload, _path = _ready(tmp_path)
    validator = Draft202012Validator(ChapterOverlapPreviewRequest.model_json_schema())
    with pytest.raises(JsonSchemaValidationError):
        validator.validate(
            {
                **payload,
                "settings": {"chapter_selector": {"mode": "exact_times", "times": [timecode]}},
            }
        )


@pytest.mark.parametrize(
    "field", ["project_session_id", "expected_storage_revision", "preparation_run_id"]
)
def test_stale_or_wrong_binding_cannot_preview(tmp_path: Path, field: str) -> None:
    app, payload, path = _ready(tmp_path)
    before = path.read_bytes()
    wrong = (
        payload["expected_storage_revision"] + 1
        if field == "expected_storage_revision"
        else str(uuid4())
    )
    with pytest.raises(ChapterOverlapPreviewError):
        app.preview_chapter_overlap({**payload, field: wrong})
    assert path.read_bytes() == before


@pytest.mark.parametrize("target", ["source.mkv", "admission.json"])
@pytest.mark.parametrize("missing", [False, True])
def test_changed_or_missing_input_rejects_old_analysis(
    tmp_path: Path, target: str, missing: bool
) -> None:
    app, payload, path = _ready(tmp_path)
    before = path.read_bytes()
    candidates = list(tmp_path.rglob(target))
    assert len(candidates) == 1
    if missing:
        candidates[0].unlink()
    else:
        candidates[0].write_bytes(b"changed synthetic fixture")
    with pytest.raises(ChapterOverlapPreviewError) as captured:
        app.preview_chapter_overlap(payload)
    assert captured.value.envelope.error.code == "E_OVERLAP_INPUT_CHANGED"
    assert captured.value.http_status == 409
    assert path.read_bytes() == before


def test_latest_stale_rejects_completed_run_without_repair(tmp_path: Path) -> None:
    app, payload, path = _ready(tmp_path)
    RuntimeRepository.open(path).mark_latest_stale(
        ("source.program",),
        StaleReason.OUTPUT_MISSING,
        updated_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    before = path.read_bytes()
    with pytest.raises(ChapterOverlapPreviewError) as captured:
        app.preview_chapter_overlap(payload)
    assert captured.value.envelope.error.code == "E_AV27_EXPAND_STALE"
    assert path.read_bytes() == before


def test_missing_analysis_does_not_create_a_run(tmp_path: Path) -> None:
    app, prepare = _create_application(tmp_path)
    path = Path(cast(str, prepare["project_path"]))
    status = app.inspect()
    before = path.read_bytes()
    with pytest.raises(ChapterOverlapPreviewError):
        app.preview_chapter_overlap(
            {
                "contract_version": "0.3.2",
                "project_session_id": status.project_session_id,
                "expected_storage_revision": status.storage_revision,
                "preparation_run_id": str(uuid4()),
            }
        )
    assert RuntimeRepository.open(path).list_runs() == ()
    assert path.read_bytes() == before


def test_http_phase1_preview_stays_read_only_and_cannot_expand_without_full_request(
    tmp_path: Path,
) -> None:
    app, payload, path = _ready(tmp_path)
    before = path.read_bytes()
    with _serve(app) as (url, _server):
        status, result, _headers = _request(
            url, CHAPTER_OVERLAP_PREVIEW_ROUTE, method="POST", payload=payload
        )
        assert status == 200
        assert result["contract_version"] == "0.3.2"
        assert result["execution_available"] is False
        ChapterOverlapPreviewEnvelope.model_validate_json(json.dumps(result))
        status, failure, _headers = _request(
            url,
            CHAPTER_OVERLAP_PREVIEW_ROUTE,
            method="POST",
            payload={**payload, "settings": {"leaf_max_minutes": 0}},
        )
        assert status == 422
        checked = ChapterOverlapFailureEnvelope.model_validate_json(json.dumps(failure))
        assert checked.error.field_path == ("settings", "leaf_max_minutes")
        status, _result, _headers = _request(
            url, "/api/studio/templates/chapter-overlap-fi/expand", method="POST", payload=payload
        )
        assert status == 422
        status, result, _headers = _request(url, "/api/studio/status")
        assert status == 200
        assert result["contract_version"] == "0.3.0"
    assert path.read_bytes() == before
