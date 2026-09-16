"""用正式临时工程验证报告只读、直接输入绑定、有界读取和失败提示，不读取用户媒体。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from authoring_helpers import authoring_command
from test_project_service import _project
from zniku.project import ProjectStore
from zniku.project_service import ProjectServiceApplication
from zniku.project_service.input_reports import project_input_reports
from zniku.project_service.models import ArtifactInputReport, RunDetailEnvelope
from zniku.runtime import Artifact, PythonAdapterContext, PythonAdapterResult, Run


def _report_run(tmp_path: Path, contents: str) -> tuple[ProjectServiceApplication, Run, Artifact]:
    project, definitions = _project(include_manual=True)
    graph = project.graph.model_copy(
        update={
            "nodes": tuple(
                node.model_copy(update={"parameters": {"text": contents}})
                if node.node_id == "source"
                else node
                for node in project.graph.nodes
            )
        }
    )
    store = ProjectStore.create(
        tmp_path / "report.zniku", project.model_copy(update={"graph": graph}), definitions
    )

    def produce(context: PythonAdapterContext) -> PythonAdapterResult:
        value = (
            context.inputs[0].path.read_text("utf-8")
            if context.inputs
            else str(context.node.parameters["text"])
        )
        context.outputs[0].path.write_text(value, encoding="utf-8")
        return PythonAdapterResult()

    app = ProjectServiceApplication(
        work_root=tmp_path / "work",
        python_adapters={
            "tests.phase3:source": produce,
            "tests.phase3:copy": produce,
        },
    )
    app.command({"operation": "open_project", "path": str(store.path)})
    status = authoring_command(app, {"operation": "run_all"})
    assert app.wait_until_idle(timeout=20) and status.active_run_id
    detail = app.inspect_run_detail(status.active_run_id)
    waiting = next(item for item in detail.run.node_runs if item.node_id == "manual")
    artifact = next(
        item for item in detail.artifacts if item.artifact_id == waiting.input_artifact_ids[0]
    )
    return app, detail.run, artifact


def test_admission_summary_and_formatted_document_are_readonly(tmp_path: Path) -> None:
    document = {
        "schema": "zniku.avenhance.v27.admission/1",
        "sources": [
            {
                "frame_count": 1801,
                "frame_rate": "30000/1001",
                "geometry": {"width": 1920, "height": 1080},
                "audio_tracks": [{"codec": "aac"}],
            }
        ],
    }
    app, run, artifact = _report_run(tmp_path, json.dumps(document))
    before = Path(artifact.path).read_bytes()
    result = app.inspect_run_detail(run.run_id)
    assert len(result.input_reports) == 1
    report = result.input_reports[0]
    assert report.title == "素材分析报告" and report.document == document
    assert {field.label: field.value for field in report.fields}["总帧数"] == "1801"
    assert result.run == run and Path(artifact.path).read_bytes() == before
    assert RunDetailEnvelope.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize(
    "contents",
    ["not JSON", "[]", '{"value":NaN}', "x" * (256 * 1024 + 1)],
    ids=["malformed", "array", "nonfinite", "oversize"],
)
def test_invalid_or_large_report_does_not_break_run_detail(tmp_path: Path, contents: str) -> None:
    app, run, _ = _report_run(tmp_path, contents)
    detail = app.inspect_run_detail(run.run_id)
    assert detail.input_reports[0].document is None
    assert detail.input_reports[0].message
    assert detail.run == run


def test_changed_missing_and_non_direct_files_are_not_read(tmp_path: Path) -> None:
    app, run, artifact = _report_run(tmp_path, '{"safe":true}')
    Path(artifact.path).write_text('{"safe":false}', encoding="utf-8")
    assert "变化" in (app.inspect_run_detail(run.run_id).input_reports[0].message or "")
    Path(artifact.path).unlink()
    assert app.inspect_run_detail(run.run_id).input_reports[0].document is None
    unrelated = artifact.model_copy(update={"artifact_id": str(uuid4()), "path": "forbidden"})
    assert project_input_reports(run, (unrelated,)) == ()
    assert project_input_reports(run, (artifact.model_copy(update={"kind": "VideoFile"}),)) == ()


def test_report_wire_rejects_unbound_and_duplicate_ids(tmp_path: Path) -> None:
    app, run, artifact = _report_run(tmp_path, '{"safe":true}')
    detail = app.inspect_run_detail(run.run_id)
    base: dict[str, Any] = {"run": run, "artifacts": detail.artifacts}
    foreign = ArtifactInputReport(artifact_id=str(uuid4()), title="外来报告")
    with pytest.raises(ValidationError, match="E_INPUT_REPORT_BINDING"):
        RunDetailEnvelope(**base, input_reports=(foreign,))
    report = ArtifactInputReport(artifact_id=artifact.artifact_id, title="重复报告")
    with pytest.raises(ValidationError, match="E_INPUT_REPORT_BINDING"):
        RunDetailEnvelope(**base, input_reports=(report, report))
