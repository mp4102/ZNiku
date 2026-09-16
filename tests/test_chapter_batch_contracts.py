"""纯合成十五叶/三章批量链：整章事务、真实生产者、旧合同隔离与半帧守恒。"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from test_source_aligned_node_contracts import Step, _header, pipeline
from zniku.avenhance_v27.probe import Av27MediaError
from zniku.chapter_batch import definitions, validators
from zniku.chapter_batch.contracts import (
    BATCH_PREFIX,
    NAMESPACE,
    BatchMetadata,
    BatchParameters,
    port_ids,
    preflight,
)
from zniku.graph import NodeInstance
from zniku.presentation import build_builtin_presentation_catalog
from zniku.project_service.source_aligned_presentation import source_aligned_output_paths
from zniku.runtime import NodeExecutionRequest, NodeValidatorContext, RunnerInput
from zniku.runtime.runner import ValidatedOutput
from zniku.source_admission import contracts as admitted
from zniku.source_aligned import validators as media_validators
from zniku.source_aligned.node_contracts import OverlapMetadata


def batch_pipeline(tmp_path: Path, n: int = 26001, chapters: int = 3) -> list[Step]:
    """只写合成 gate JSON，不读取或生成媒体；默认每章五叶，共十五叶。"""
    previous = pipeline(tmp_path, n=n, chapters=chapters, rate="30/1")
    split = previous[0]
    source, gate = split.inputs
    split_inputs = (
        replace(
            source,
            media_info={
                **source.media_info,
                admitted.SOURCE_NAMESPACE: {"contract_version": "0.3.5"},
            },
        ),
        gate,
    )
    split_contract = admitted.preflight("split", split_inputs, split.parameters)
    result: list[Step] = []

    def registered(metadata: OverlapMetadata, port: str, namespace: str = NAMESPACE) -> RunnerInput:
        return RunnerInput(
            "video",
            str(uuid4()),
            "VideoFile",
            tmp_path / f"{uuid4()}.mov",
            producer_node_run_id=str(uuid4()),
            producer_port_id=port,
            media_info={namespace: metadata.model_dump(mode="json")},
        )

    def add(
        role: str, inputs: tuple[RunnerInput, ...], params: dict[str, Any]
    ) -> tuple[RunnerInput, ...]:
        contract = preflight(role, inputs, params)
        result.append(Step(role, inputs, params, contract))
        return tuple(registered(output.metadata, output.port_id) for output in contract.outputs)

    merges: list[RunnerInput] = []
    source_data = split.parameters["source"]
    for index in range(chapters):
        leaves = [
            output
            for output in split_contract.outputs
            if output.metadata.chapter and output.metadata.chapter.ordinal == index
        ]
        chapter = leaves[0].metadata.chapter
        assert chapter is not None
        enhanced = add(
            "enhancement",
            tuple(
                replace(
                    registered(output.metadata, output.port_id, admitted.NAMESPACE),
                    port_id="videos",
                    ordinal=i,
                )
                for i, output in enumerate(leaves)
            ),
            {
                "source": source_data,
                "chapter": chapter.model_dump(),
                "leaves": [
                    output.metadata.leaf.model_dump() for output in leaves if output.metadata.leaf
                ],
                "expected_input_geometry": {"width": 1920, "height": 1080},
                "expected_output_geometry": {"width": 3840, "height": 2160},
                "model_name": "synthetic-enhancer",
                "actual_scale_factor": 2,
            },
        )
        merges.extend(
            add(
                "merge",
                tuple(
                    replace(item, port_id="videos", ordinal=i) for i, item in enumerate(enhanced)
                ),
                {"source": source_data, "chapter": chapter.model_dump()},
            )
        )
    crops: list[RunnerInput] = []
    for old in (step for step in previous if step.role == "context"):
        chapter = old.parameters["chapter"]
        neighbors = tuple(
            item
            for item in merges
            if (
                (m := BatchMetadata.model_validate(item.media_info[NAMESPACE])).chapter is not None
                and m.chapter.end_frame > max(0, chapter["start_frame"] - 1)
                and m.chapter.start_frame < min(n, chapter["end_frame"] + 1)
            )
        )
        # 使用同一既有profile中的左右上下文数量，不从文件名推断章节关系。
        context = add(
            "context",
            tuple(replace(item, port_id="chapters", ordinal=i) for i, item in enumerate(neighbors)),
            old.parameters,
        )
        raw = add("fi", context, old.parameters)
        crops.extend(add("crop", raw, old.parameters))
    program = add(
        "program",
        tuple(replace(item, port_id="chapters", ordinal=i) for i, item in enumerate(crops)),
        {"source": source_data, "chapter_count": chapters, "encoder": "cpu"},
    )
    original, final_gate = previous[-1].inputs[-2:]
    add("final", (*program, original, final_gate), {"source": source_data, "mr_mode": "off"})
    return result


def validator_context(step: Step, tmp_path: Path) -> tuple[NodeValidatorContext, dict[Path, Any]]:
    definition = definitions.definition(step.role, len(step.contract.outputs))
    names = {item.port_id: item.relative_path for item in definition.executor.output_paths}
    outputs, headers = [], {}
    for output in step.contract.outputs:
        path = tmp_path / names[output.port_id]
        outputs.append(
            ValidatedOutput(
                output.port_id,
                "MediaFile" if step.role == "final" else "VideoFile",
                path,
                100,
                1,
                {},
                {}
                if step.role in {"enhancement", "fi"}
                else {"output_frames": output.metadata.frame_count},
                None,
            )
        )
        headers[path] = _header(path, output.metadata)
    node = NodeInstance(
        node_id="test",
        type_id=definition.type_id,
        definition_version=definition.version,
        parameters=step.parameters,
    )
    return NodeValidatorContext(
        NodeExecutionRequest(str(uuid4()), 1, definition, node, step.inputs),
        tmp_path,
        tuple(outputs),
    ), headers


def test_fifteen_leaves_three_true_batches_and_full_new_metadata_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    steps = batch_pipeline(tmp_path)
    batches = [step for step in steps if step.role == "enhancement"]
    assert len(batches) == 3
    assert [len(step.contract.outputs) for step in batches] == [5, 5, 5]
    monkeypatch.setattr(media_validators, "verify_video_span", lambda *args, **kwargs: None)
    for step in steps:
        context, headers = validator_context(step, tmp_path)
        monkeypatch.setattr(media_validators, "probe_header", headers.__getitem__)
        checked = validators._validate(context, step.role)
        assert checked.passed, checked.message
        assert len(checked.media_info_extensions) == len(step.contract.outputs)
        for output in step.contract.outputs:
            metadata = output.metadata
            assert metadata.producer_type_id == context.request.definition.type_id
            assert BatchMetadata.model_validate_json(metadata.model_dump_json()) == metadata
            with pytest.raises((ValidationError, Av27MediaError)):
                admitted.OverlapMetadata.model_validate(metadata.model_dump())
    assert (
        sum(step.contract.outputs[0].metadata.frame_count for step in steps if step.role == "crop")
        == 2 * 26001 - 1
    )
    assert steps[-1].contract.outputs[0].metadata.frame_count == 2 * 26001


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "order", "other_source", "wrong_port", "old_namespace"]
)
def test_batch_rejects_input_misbindings(tmp_path: Path, mutation: str) -> None:
    step = batch_pipeline(tmp_path)[0]
    inputs = step.inputs
    if mutation == "missing":
        inputs = inputs[:-1]
    elif mutation == "duplicate":
        inputs = (inputs[0], *inputs[:-1])
    elif mutation == "order":
        inputs = tuple(reversed(inputs))
    else:
        first = inputs[0]
        data = json.loads(json.dumps(first.media_info))
        if mutation == "other_source":
            data[admitted.NAMESPACE]["source"]["effective_video_artifact_id"] = str(uuid4())
            first = replace(first, media_info=data)
        elif mutation == "old_namespace":
            first = replace(first, media_info={NAMESPACE: data[admitted.NAMESPACE]})
        else:
            first = replace(first, producer_port_id="leaf-9999")
        inputs = (first, *inputs[1:])
    with pytest.raises((ValidationError, Av27MediaError)):
        preflight(
            "enhancement",
            tuple(replace(item, ordinal=i) for i, item in enumerate(inputs)),
            step.parameters,
        )


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "order", "range", "unknown"])
def test_batch_parameters_fail_closed(tmp_path: Path, mutation: str) -> None:
    params = json.loads(json.dumps(batch_pipeline(tmp_path)[0].parameters))
    if mutation == "missing":
        params["leaves"].pop()
    elif mutation == "duplicate":
        params["leaves"][1] = params["leaves"][0]
    elif mutation == "order":
        params["leaves"].reverse()
    elif mutation == "range":
        params["leaves"][0]["end_frame"] += 1
    else:
        params["command"] = "never"
    with pytest.raises((ValidationError, Av27MediaError)):
        BatchParameters.model_validate(params)


@pytest.mark.parametrize("mutation", ["missing", "order", "wrong_port", "wrong_chapter"])
def test_batch_merge_revalidates_each_local_output_port_and_source_range(
    tmp_path: Path, mutation: str
) -> None:
    """B 章输出从本地 leaf-0001 重新编号，但仍绑定其非零全局源坐标。"""
    steps = batch_pipeline(tmp_path)
    step = [item for item in steps if item.role == "merge"][1]
    inputs = step.inputs
    assert inputs[0].producer_port_id == "leaf-0001"
    metadata = BatchMetadata.model_validate(inputs[0].media_info[NAMESPACE])
    assert metadata.leaf is not None and metadata.leaf.global_ordinal == 5
    if mutation == "missing":
        inputs = inputs[:-1]
    elif mutation == "order":
        inputs = tuple(reversed(inputs))
    elif mutation == "wrong_port":
        inputs = (replace(inputs[0], producer_port_id="leaf-0006"), *inputs[1:])
    else:
        other_chapter = next(item for item in steps if item.role == "merge")
        inputs = (other_chapter.inputs[0], *inputs[1:])
    with pytest.raises((ValidationError, Av27MediaError)):
        preflight(
            "merge",
            tuple(replace(item, ordinal=i) for i, item in enumerate(inputs)),
            step.parameters,
        )


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "wrong_frames", "wrong_geometry", "wrong_rate"]
)
def test_batch_failure_registers_no_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    context, headers = validator_context(batch_pipeline(tmp_path)[0], tmp_path)
    if mutation == "missing":
        context = replace(context, outputs=context.outputs[:-1])
    elif mutation == "duplicate":
        context = replace(context, outputs=(*context.outputs[:-1], context.outputs[0]))
    else:
        path = context.outputs[-1].path
        media = headers[path]
        variants = {
            "wrong_frames": {"frame_count": media.video.frame_count + 1},
            "wrong_geometry": {"width": 1920},
            "wrong_rate": {"frame_rate": 29},
        }
        headers[path] = replace(media, videos=(replace(media.video, **variants[mutation]),))
    monkeypatch.setattr(media_validators, "probe_header", headers.__getitem__)
    result = validators.validate_enhancement(context)
    assert not result.passed
    assert not result.media_info_extensions
    if mutation.startswith("wrong_"):
        assert result.summary["output_port"] == "leaf-0005"
        assert result.message is not None and "leaf-0005" in result.message


def test_one_leaf_still_batch_and_shared_fields_are_presented(tmp_path: Path) -> None:
    step = batch_pipeline(tmp_path, n=1801)[0]
    assert len(step.contract.outputs) == 1
    definition = definitions.definition("enhancement", 1)
    assert definition.type_id == BATCH_PREFIX + "1"
    assert definition.input_ports[0].required is True
    catalog = build_builtin_presentation_catalog((definition,))
    assert catalog.nodes[0].title == "章节批量增强"
    assert {item.parameter_pointer for item in catalog.nodes[0].parameters} >= {
        "/model_name",
        "/model_version",
        "/actual_scale_factor",
        "/leaves",
    }
    node = NodeInstance(
        node_id="batch",
        type_id=definition.type_id,
        definition_version=definition.version,
        parameters=step.parameters,
    )
    paths = source_aligned_output_paths(
        node, definition, media_basename="Example (2026)", role_reader=definitions.definition_role
    )
    assert paths[0].relative_path == "A/Example (2026).A.leaf-0001.enhancement.mov"
    schema = Draft202012Validator(definition.model_dump(mode="json")["parameter_schema"])
    assert not list(schema.iter_errors(step.parameters))
    assert list(schema.iter_errors(dict(step.parameters, unknown=1)))


@pytest.mark.parametrize("value", [True, False, 0, -1, 10001, 1.0, "1"])
def test_batch_shape_strict_before_cache(value: Any) -> None:
    definitions.definition("enhancement", 1)
    with pytest.raises(ValueError):
        port_ids(value)
    with pytest.raises(ValueError):
        definitions.definition("enhancement", value)
