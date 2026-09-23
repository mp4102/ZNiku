"""v0.3.6 融合裁边的独立精确节点，只消费显式 raw 输入，不改写旧定义。

局部帧范围从已验收批量族 FI metadata 重算，禁止以 raw 路径冒充裁后 Artifact。
新 Program/Final 使用真实 producer 身份与独立 namespace，仍由普通 Runtime 执行。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from fractions import Fraction
from functools import lru_cache
from typing import Literal, cast

from pydantic import JsonValue, model_validator

from zniku.chapter_overlap.media_io import capacity_check, source_path
from zniku.graph import NodeDefinition, PortSpec, PythonExecutorSpec, ValidatorSpec
from zniku.runtime import (
    NodeValidatorContext,
    NodeValidatorResult,
    PythonAdapterContext,
    PythonAdapterResult,
    RunnerInput,
)
from zniku.source_aligned import adapters as execution
from zniku.source_aligned import node_contracts as shared
from zniku.source_aligned import validators as media_validation

from . import contracts as batch
from . import final_publish
from .fused_media import RawRange, encode_fused

VERSION = "0.3.6"
NAMESPACE = "zniku.chapter.batch.fused"
PROGRAM_TYPE = "zniku.source-admitted.chapter-batch.program-fused"
FINAL_TYPE = "zniku.source-admitted.chapter-batch.final-publish-fused"
TEMPLATE_ID = "zniku.source-admitted.chapter-batch-fused"


class Metadata(shared.OverlapMetadata):
    """只允许 program/final 的新生产者，绝不接受伪装为新版本的旧章节成果。"""

    producer_version: Literal["0.3.6"] = "0.3.6"  # type: ignore[assignment]

    @model_validator(mode="after")
    def require_fused_role(self) -> Metadata:
        if self.role not in {"program", "final"}:
            raise ValueError("E_FUSED_ROLE: 融合 namespace 只接受 program/final")
        return self

    @classmethod
    def producer_type(
        cls, role: shared.Role, split_count: int = 0, leaf: shared.LeafBinding | None = None
    ) -> str:
        if role not in {"program", "final"}:
            shared.fail("FUSED_ROLE", "融合 namespace 仅保存实际 Program/Final 成果")
        return PROGRAM_TYPE if role == "program" else FINAL_TYPE


@lru_cache(maxsize=2)
def definition(role: str) -> NodeDefinition:
    """新职责独立 exact；原批量族全部定义继续保留且接受集合不变。"""
    from .definitions import definition as batch_definition

    if role not in {"program", "final"}:
        raise ValueError("融合族仅包含 program/final")
    base = batch_definition("program") if role == "program" else final_publish.definition()
    ports = base.input_ports
    if role == "program":
        ports = tuple(
            PortSpec.model_validate({**port.model_dump(), "port_id": "videos"}) for port in ports
        )
    return NodeDefinition(
        type_id=PROGRAM_TYPE if role == "program" else FINAL_TYPE,
        version=VERSION,
        input_ports=ports,
        output_ports=base.output_ports,
        parameter_schema=cast(dict[str, JsonValue], dict(base.parameter_schema)),
        execution_mode=base.execution_mode,
        executor=PythonExecutorSpec(
            adapter=f"zniku.chapter_batch.fused:{role}",
            output_paths=base.executor.output_paths,
        ),
        validator=ValidatorSpec(adapter=f"zniku.chapter_batch.fused:validate_{role}"),
    )


def definition_role(value: NodeDefinition) -> str | None:
    for role, type_id in (("program", PROGRAM_TYPE), ("final", FINAL_TYPE)):
        if value.type_id == type_id and value.version == VERSION:
            return role if value == definition(role) else None
    return None


def preflight(
    role: str, inputs: tuple[RunnerInput, ...], parameters: Mapping[str, object]
) -> shared.NodeContract:
    """只读直接输入并重建半帧责任，重复/乱序/异源/等长错裁均在媒体 I/O 前拒绝。"""
    if role == "final":
        params = final_publish.Parameters.model_validate(shared._plain(parameters))
        return shared.preflight(
            "final",
            inputs,
            {"source": params.source.model_dump(), "mr_mode": params.mr_mode},
            metadata_model=Metadata,
            namespace=NAMESPACE,
        )
    if role != "program" or {item.port_id for item in inputs} != {"videos"}:
        shared.fail("FUSED_INPUT_PORTS", "融合编码只接显式有序 videos")
    params_program = shared.ProgramParameters.model_validate(shared._plain(parameters))
    items = shared._inputs(inputs, "videos", many=True)
    metadata = tuple(
        shared.read_metadata(item, metadata_model=batch.BatchMetadata, namespace=batch.NAMESPACE)
        for item in items
    )
    shared._uniform(metadata)
    first = metadata[0]
    if len(metadata) != params_program.chapter_count:
        shared.fail("FUSED_COVERAGE", "缺少完整 FI 章")
    cursor = 0
    for ordinal, value in enumerate(metadata):
        chapter, context = value.chapter, value.context
        if (
            value.role != "fi"
            or value.source.expectation() != params_program.source
            or chapter is None
            or context is None
            or chapter.ordinal != ordinal
            or chapter.count != len(metadata)
            or context.global_start_half_frame != cursor
        ):
            shared.fail("FUSED_COVERAGE", "FI raw 必须同源、按正式章序连续且唯一")
        cursor = context.global_end_half_frame
    if cursor != 2 * first.source.frame_count - 1:
        shared.fail("FUSED_COVERAGE", "FI raw 责任区间未完整覆盖全片 2N-1")
    output = shared._metadata(
        "program",
        first.source,
        count=cursor + 1,
        geometry=first.geometry,
        signal=first.signal,
        fi_profile=first.fi_profile,
        enhancement=first.enhancement,
        metadata_model=Metadata,
    )
    return shared.NodeContract(
        params_program, first.source, (shared.OutputContract("video", output),), metadata
    )


def program(context: PythonAdapterContext) -> PythonAdapterResult:
    """不创建裁后媒体；仅一次连续编码，失败由 Runner 保持未登记。"""
    if definition_role(context.definition) != "program":
        shared.fail("FUSED_DEFINITION", "不是完整融合精确定义")
    contract = preflight("program", context.inputs, context.node.parameters)
    assert isinstance(contract.params, shared.ProgramParameters)
    parts: list[RawRange] = []
    for item, metadata in zip(context.inputs, contract.input_metadata, strict=True):
        assert metadata.context is not None
        binding = metadata.context
        parts.append(
            RawRange(
                source_path(item.path),
                metadata.frame_count,
                binding.crop_start_frame,
                binding.crop_end_frame,
            )
        )
    target = execution._targets(context, contract)["video"]
    size = sum(part.path.stat().st_size for part in parts)
    output = contract.outputs[0].metadata
    capacity = capacity_check(
        context,
        output_bytes=size,
        input_bytes=size,
        input_frames=sum(part.frame_count for part in parts),
        output_frames=output.frame_count,
    )
    actual = encode_fused(
        context,
        parts,
        target,
        Fraction(output.frame_rate),
        output.frame_count,
        contract.params.encoder,
    )
    return execution._result("video", target, actual, capacity)


def validate_program(context: NodeValidatorContext) -> NodeValidatorResult:
    return media_validation._validate(
        context,
        "program",
        contract_reader=preflight,
        role_reader=definition_role,
        metadata_model=Metadata,
        namespace=NAMESPACE,
        verify_original_audio_origins=False,
    )


def _check_final(context: NodeValidatorContext, expected_name: str) -> NodeValidatorResult:
    return media_validation._validate(
        replace(context, request=replace(context.request, output_paths=())),
        "final",
        contract_reader=preflight,
        role_reader=definition_role,
        metadata_model=Metadata,
        namespace=NAMESPACE,
        verify_original_audio_origins=False,
        output_name=lambda _role, _port: expected_name,
    )


def final(context: PythonAdapterContext) -> PythonAdapterResult:
    return final_publish.execute(
        context,
        _definition_checker=lambda value: definition_role(value) == "final",
        _contract_reader=preflight,
        _media_checker=_check_final,
    )


def validate_final(context: NodeValidatorContext) -> NodeValidatorResult:
    return final_publish.validate(context, _contract_reader=preflight, _media_checker=_check_final)
