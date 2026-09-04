"""以纯合成 snapshot 验证人工交付说明只读、精确绑定且缺事实不猜测。"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import JsonValue, ValidationError

from zniku.avenhance_v27.definitions import (
    enhancement_definition,
    frame_interpolation_definition,
    mosaic_restoration_definition,
)
from zniku.graph import (
    Edge,
    ExecutionMode,
    Graph,
    NodeDefinition,
    NodeInstance,
    PortSpec,
    PythonExecutorSpec,
)
from zniku.project_service.av27_handoff import project_av27_handoff_contracts
from zniku.project_service.models import RunDetailEnvelope
from zniku.runtime import (
    Artifact,
    ExternalHandoff,
    ExternalOutputTarget,
    NodeRun,
    NodeRunState,
    Run,
    RunState,
)


def _fixture(stage: str) -> tuple[Run, Artifact]:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    geometry: dict[str, JsonValue] = {"width": 1920, "height": 1080, "sample_aspect_ratio": "1/1"}
    signal: dict[str, JsonValue] = {
        "color_primaries": "bt709",
        "color_transfer": "bt709",
        "color_space": "bt709",
        "color_range": "tv",
        "chroma_location": "left",
        "field_order": "progressive",
        "rotation": 0,
    }
    parameters: dict[str, JsonValue]
    if stage == "mr":
        definition = mosaic_restoration_definition()
        parameters = {"model_name": "Synthetic MR", "model_version": "1"}
    elif stage == "enhancement":
        definition = enhancement_definition()
        parameters = {
            "model_name": "Synthetic Enhancement",
            "actual_scale_factor": 1,
            "expected_input_geometry": geometry,
            "expected_output_geometry": geometry,
            "expected_frames": 100,
            "expected_fps": "30000/1001",
            "chapter_id": "chapter-0001",
            "chapter_ordinal": 0,
            "leaf_id": "leaf-0001",
            "leaf_ordinal": 0,
        }
    else:
        definition = frame_interpolation_definition()
        parameters = {
            "model_name": "Synthetic FI",
            "chapter_id": "chapter-0001",
            "chapter_ordinal": 0,
            "expected_input_frames": 100,
            "expected_output_frames": 199,
            "source_fps": "30000/1001",
            "expected_geometry": geometry,
            "expected_signal": signal,
        }
    source_definition = NodeDefinition(
        type_id="test.source",
        version="0.2.1",
        output_ports=(
            PortSpec(port_id="video", data_type="VideoFile"),
            PortSpec(port_id="gate", data_type="DataFile"),
        ),
        parameter_schema={"type": "object", "properties": {}, "additionalProperties": False},
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="synthetic:source"),
    )
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="source", type_id=source_definition.type_id, definition_version="0.2.1"
            ),
            NodeInstance(
                node_id="manual",
                type_id=definition.type_id,
                definition_version="0.2.1",
                parameters=parameters,
            ),
        ),
        edges=tuple(
            Edge(
                source_node_id="source",
                source_port_id=port.port_id,
                target_node_id="manual",
                target_port_id=port.port_id,
            )
            for port in definition.input_ports
        ),
    )
    run_id, node_run_id, artifact_id = str(uuid4()), str(uuid4()), str(uuid4())
    artifact = Artifact(
        artifact_id=artifact_id,
        kind="VideoFile",
        path="synthetic/input.mkv",
        producer_node_run_id=str(uuid4()),
        producer_port_id="video",
        media_info={
            "zniku.avenhance.v27": {
                "frame_count": 100,
                "frame_rate": "30000/1001",
                "geometry": {"width": 1920, "height": 1080},
                "signal": signal,
            }
        },
    )
    handoff = ExternalHandoff(
        handoff_id=str(uuid4()),
        node_run_id=node_run_id,
        input_artifact_ids=(artifact_id,),
        output_targets=(ExternalOutputTarget(port_id="video", path="synthetic/output.mov"),),
        created_at=now,
    )
    attempt = NodeRun(
        node_run_id=node_run_id,
        run_id=run_id,
        node_id="manual",
        definition_version="0.2.1",
        attempt=1,
        state=NodeRunState.WAITING_EXTERNAL,
        input_artifact_ids=(artifact_id,),
        work_dir="synthetic/attempt",
        created_at=now,
        started_at=now,
        external_handoff=handoff,
    )
    run = Run(
        run_id=run_id,
        project_id="test.handoff",
        graph_snapshot=graph,
        definitions_snapshot=(source_definition, definition),
        state=RunState.RUNNING,
        node_runs=(attempt,),
        created_at=now,
        started_at=now,
    )
    return run, artifact


@pytest.mark.parametrize("stage", ["mr", "enhancement", "fi"])
def test_contract_projects_exact_snapshot_and_input_without_mutation(stage: str) -> None:
    run, artifact = _fixture(stage)
    before = run.model_dump_json(), artifact.model_dump_json()
    contracts = project_av27_handoff_contracts(run, (artifact,))
    assert len(contracts) == 1
    projection = contracts[0]
    fields = {item.label: item.value for item in projection.fields}
    assert fields["输入 exact N"] == "100"
    assert fields["输出 exact N"] == ("199" if stage == "fi" else "100")
    assert fields["输入 canonical FPS"] == "30000/1001"
    assert fields["输出 canonical FPS"] == ("60000/1001" if stage == "fi" else "30000/1001")
    assert "1920" in fields["输出 geometry"]
    assert "bt709" in fields["输出 signal"]
    if stage != "mr":
        assert fields["视频 codec / pixel format"] == "ProRes 422 HQ / yuv422p10le"
    envelope = RunDetailEnvelope(run=run, artifacts=(artifact,), handoff_contracts=contracts)
    assert RunDetailEnvelope.model_validate_json(envelope.model_dump_json()) == envelope
    assert (run.model_dump_json(), artifact.model_dump_json()) == before


def test_missing_metadata_is_unavailable_and_old_attempt_not_projected() -> None:
    run, artifact = _fixture("mr")
    missing = artifact.model_copy(update={"media_info": {}})
    fields = project_av27_handoff_contracts(run, (missing,))[0].fields
    assert next(item.value for item in fields if item.label == "输入 exact N").startswith("不可用")
    first = run.node_runs[0]
    second = NodeRun.pending(
        run_id=run.run_id,
        node_id=first.node_id,
        definition_version="0.2.1",
        attempt=2,
        input_artifact_ids=(),
        work_dir="synthetic/attempt2",
        created_at=first.created_at,
    )
    updated = run.model_copy(update={"node_runs": (first, second)})
    assert project_av27_handoff_contracts(updated, (artifact,)) == ()


def test_wire_rejects_wrong_handoff_input_and_unknown_projection_fields() -> None:
    run, artifact = _fixture("fi")
    contracts = project_av27_handoff_contracts(run, (artifact,))
    for updates in ({"handoff_id": str(uuid4())}, {"input_artifact_id": str(uuid4())}):
        invalid = contracts[0].model_copy(update=updates)
        with pytest.raises(ValidationError, match="E_HANDOFF_CONTRACT_BINDING"):
            RunDetailEnvelope(run=run, artifacts=(artifact,), handoff_contracts=(invalid,))
    payload = RunDetailEnvelope(
        run=run, artifacts=(artifact,), handoff_contracts=contracts
    ).model_dump(mode="json")
    payload["handoff_contracts"][0]["unexpected"] = True
    with pytest.raises(ValidationError):
        RunDetailEnvelope.model_validate(payload)
