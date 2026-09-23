"""融合节点只扩大新 exact 职责；纯合成合同证明旧链不变与半帧覆盖唯一。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from test_chapter_batch_contracts import batch_pipeline
from test_final_publish import fixture
from zniku.avenhance_v27.probe import Av27MediaError
from zniku.chapter_batch import contracts, definitions, fused
from zniku.graph import NodeDefinition, NodeInstance
from zniku.presentation import build_builtin_presentation_catalog
from zniku.runtime import NodeExecutionRequest, NodeRunner, RunnerInput


def fused_inputs(
    tmp_path: Path, chapters: int = 3
) -> tuple[tuple[RunnerInput, ...], dict[str, object]]:
    steps = batch_pipeline(tmp_path, chapters=chapters)
    inputs = tuple(
        replace(step.inputs[0], port_id="videos", ordinal=i)
        for i, step in enumerate(step for step in steps if step.role == "crop")
    )
    params = next(step.parameters for step in steps if step.role == "program")
    return inputs, params


@pytest.mark.parametrize("chapters", (1, 2, 3, 26))
def test_raw_contract_has_true_new_producer_and_single_global_tail(
    tmp_path: Path, chapters: int
) -> None:
    inputs, params = fused_inputs(tmp_path, chapters)
    result = fused.preflight("program", inputs, params)
    output = fused.Metadata.model_validate(result.outputs[0].metadata.model_dump())
    assert output.producer_version == "0.3.6"
    assert result.outputs[0].metadata.producer_type_id == fused.PROGRAM_TYPE
    assert result.outputs[0].metadata.frame_count == 52002
    assert sum(m.context.cropped_frame_count for m in result.input_metadata if m.context) == 52001
    assert all(
        m.role == "fi"
        and contracts.BatchMetadata.model_validate(m.model_dump()).producer_version == "0.3.5"
        for m in result.input_metadata
    )
    with pytest.raises(ValidationError):
        contracts.BatchMetadata.model_validate(result.outputs[0].metadata.model_dump())


@pytest.mark.parametrize(
    "mutation", ("reverse", "duplicate", "missing", "port", "ordinal", "cropped", "shifted")
)
def test_bad_raw_bindings_fail_before_media_io(tmp_path: Path, mutation: str) -> None:
    inputs, params = fused_inputs(tmp_path)
    if mutation == "reverse":
        inputs = tuple(replace(item, ordinal=i) for i, item in enumerate(reversed(inputs)))
    elif mutation == "duplicate":
        inputs = (inputs[0], replace(inputs[0], ordinal=1), inputs[2])
    elif mutation == "missing":
        inputs = inputs[:-1]
    elif mutation == "port":
        inputs = (replace(inputs[0], port_id="chapters"), *inputs[1:])
    elif mutation == "ordinal":
        inputs = (replace(inputs[0], ordinal=1), *inputs[1:])
    else:
        marker = contracts.BatchMetadata.model_validate(
            inputs[0].media_info[contracts.NAMESPACE]
        ).model_dump()
        if mutation == "cropped":
            marker["role"] = "crop"
            marker["producer_type_id"] = contracts.ROLE_TYPES["crop"]
            marker["frame_count"] = marker["context"]["cropped_frame_count"]
        else:
            marker["context"]["crop_start_frame"] += 1
            marker["context"]["crop_end_frame"] += 1
        inputs = (replace(inputs[0], media_info={contracts.NAMESPACE: marker}), *inputs[1:])
    with pytest.raises((Av27MediaError, ValidationError)):
        fused.preflight("program", inputs, params)


def test_new_definitions_are_registered_with_presentation_and_reject_drift() -> None:
    catalog = build_builtin_presentation_catalog(definitions.built_in_definitions())
    for role in ("program", "final"):
        definition = fused.definition(role)
        assert definition in definitions.built_in_definitions()
        assert fused.definition_role(definition) == role
        assert any(node.type_id == definition.type_id for node in catalog.nodes)
        changed = NodeDefinition.model_validate({**definition.model_dump(), "version": "0.3.5"})
        assert fused.definition_role(changed) is None
    assert [port.port_id for port in fused.definition("program").input_ports] == ["videos"]
    assert [port.port_id for port in definitions.definition("program").input_ports] == ["chapters"]


def test_fused_final_reuses_checked_publication_without_old_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, target, events = fixture(tmp_path, monkeypatch)
    old_program = context.inputs[0]
    old_marker = contracts.BatchMetadata.model_validate(
        old_program.media_info[contracts.NAMESPACE]
    ).model_dump()
    marker = {**old_marker, "producer_type_id": fused.PROGRAM_TYPE, "producer_version": "0.3.6"}
    inputs = (replace(old_program, media_info={fused.NAMESPACE: marker}), *context.inputs[1:])
    definition = fused.definition("final")
    node = NodeInstance.model_validate(
        {
            **context.node.model_dump(),
            "type_id": definition.type_id,
            "definition_version": definition.version,
        }
    )
    runner = NodeRunner(
        tmp_path / "runner",
        python_adapters=definitions.python_adapters(),
        validators=definitions.validators(),
        media_probe=lambda _p, _k: {"synthetic": True},
    )
    result = runner.run_automatic(NodeExecutionRequest(str(uuid4()), 1, definition, node, inputs))
    assert target.read_bytes() == b"checked-final"
    assert events == ["mux", "candidate-check", "final-check"]
    assert result.artifacts[0].media_info[fused.NAMESPACE]["producer_type_id"] == fused.FINAL_TYPE  # type: ignore[index]
    assert contracts.NAMESPACE not in result.artifacts[0].media_info
