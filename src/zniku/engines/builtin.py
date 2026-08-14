"""提供不接触文件系统的 Demux/Mux 内建 Engine conformance package。

这些 adapter 只把纯合成 Artifact 转换为候选领域对象，用于验证 SDK、typed ports、音轨集合和
no-replace 边界；它们不是生产媒体实现，也不读取、编码、复用或发布真实文件。
"""

from __future__ import annotations

import hashlib
import inspect
from typing import cast

from pydantic import JsonValue

from zniku.contracts import (
    Artifact,
    ArtifactSet,
    ArtifactSetMember,
    ArtifactType,
    Cardinality,
    CoverageMode,
    CoverageSpan,
    CoverageUnit,
    EngineBinding,
    EngineInputContract,
    EngineLifecycleCapabilities,
    EngineManifest,
    EngineOutputContract,
    ExecutionMode,
    MediaKind,
    PortSpec,
    Scope,
)
from zniku.contracts.base import JsonObject
from zniku.contracts.schema import JSON_SCHEMA_DIALECT

from .catalog import EnginePackage
from .models import (
    ArtifactSetValue,
    ArtifactValue,
    EngineInvocationRequest,
    EngineInvocationResult,
    EnginePackageDescriptor,
)


def _object_schema(properties: JsonObject, required: tuple[str, ...] = ()) -> JsonObject:
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": True,
    }


def _empty_parameters() -> JsonObject:
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }


def _build_manifest(
    *,
    engine_id: str,
    display_name: str,
    lifecycle: EngineLifecycleCapabilities,
    inputs: tuple[EngineInputContract, ...],
    outputs: tuple[EngineOutputContract, ...],
    parameter_schema: JsonObject,
) -> EngineManifest:
    """通过标准 JSON data 组装嵌套合同，避免复用冻结容器形成歧义。"""

    return EngineManifest.from_data(
        cast(
            JsonObject,
            {
                "contract_version": "0.1.0",
                "engine_id": engine_id,
                "engine_version": "0.1.0",
                "display_name": display_name,
                "execution_mode": ExecutionMode.AUTOMATIC.value,
                "lifecycle": lifecycle.to_data(),
                "supported_scopes": [Scope.PROGRAM.value],
                "inputs": [contract.to_data() for contract in inputs],
                "outputs": [contract.to_data() for contract in outputs],
                "parameter_schema": dict(parameter_schema),
            },
        )
    )


def synthetic_demux_manifest() -> EngineManifest:
    """返回 Phase 1 纯合成 Demux manifest。"""

    return _build_manifest(
        engine_id="zniku.builtin.synthetic-demux",
        display_name="ZNIKU Synthetic Demux",
        lifecycle=EngineLifecycleCapabilities(
            supports_acceptance=True,
            supports_publication=False,
            supports_recovery=False,
        ),
        inputs=(
            EngineInputContract(
                port=PortSpec(
                    port_id="program_in",
                    artifact_type=ArtifactType.MEDIA,
                    media_kind=MediaKind.PROGRAM_MEDIA,
                    scope=Scope.PROGRAM,
                    cardinality=Cardinality.ONE,
                ),
                preconditions_schema=_object_schema(
                    {
                        "duration_frames": {"type": "integer", "minimum": 1},
                        "audio_stream_ids": {
                            "type": "array",
                            "items": {"type": "string", "maxLength": 64},
                            "minItems": 1,
                            "maxItems": 32,
                        },
                    },
                    ("duration_frames", "audio_stream_ids"),
                ),
            ),
        ),
        outputs=(
            EngineOutputContract(
                port=PortSpec(
                    port_id="video_out",
                    artifact_type=ArtifactType.MEDIA,
                    media_kind=MediaKind.VIDEO,
                    scope=Scope.PROGRAM,
                    cardinality=Cardinality.ONE,
                ),
                guarantees_schema=_object_schema(
                    {"duration_frames": {"type": "integer", "minimum": 1}},
                    ("duration_frames",),
                ),
            ),
            EngineOutputContract(
                port=PortSpec(
                    port_id="audio_out",
                    artifact_type=ArtifactType.MEDIA,
                    media_kind=MediaKind.AUDIO,
                    scope=Scope.PROGRAM,
                    cardinality=Cardinality.SET,
                ),
                guarantees_schema=_object_schema(
                    {
                        "duration_frames": {"type": "integer", "minimum": 1},
                        "stream_id": {"type": "string", "maxLength": 64},
                    },
                    ("duration_frames", "stream_id"),
                ),
            ),
        ),
        parameter_schema=_empty_parameters(),
    )


def synthetic_mux_manifest() -> EngineManifest:
    """返回 Phase 1 纯合成 Mux manifest。"""

    duration_schema = _object_schema(
        {"duration_frames": {"type": "integer", "minimum": 1}},
        ("duration_frames",),
    )
    return _build_manifest(
        engine_id="zniku.builtin.synthetic-mux",
        display_name="ZNIKU Synthetic Mux",
        lifecycle=EngineLifecycleCapabilities(
            supports_acceptance=True,
            supports_publication=False,
            supports_recovery=False,
        ),
        inputs=(
            EngineInputContract(
                port=PortSpec(
                    port_id="video_in",
                    artifact_type=ArtifactType.MEDIA,
                    media_kind=MediaKind.VIDEO,
                    scope=Scope.PROGRAM,
                    cardinality=Cardinality.ONE,
                ),
                preconditions_schema=duration_schema,
            ),
            EngineInputContract(
                port=PortSpec(
                    port_id="audio_in",
                    artifact_type=ArtifactType.MEDIA,
                    media_kind=MediaKind.AUDIO,
                    scope=Scope.PROGRAM,
                    cardinality=Cardinality.SET,
                ),
                preconditions_schema=_object_schema(
                    {
                        "duration_frames": {"type": "integer", "minimum": 1},
                        "stream_id": {"type": "string", "maxLength": 64},
                    },
                    ("duration_frames", "stream_id"),
                ),
            ),
        ),
        outputs=(
            EngineOutputContract(
                port=PortSpec(
                    port_id="program_out",
                    artifact_type=ArtifactType.MEDIA,
                    media_kind=MediaKind.PROGRAM_MEDIA,
                    scope=Scope.PROGRAM,
                    cardinality=Cardinality.ONE,
                ),
                guarantees_schema=_object_schema(
                    {
                        "duration_frames": {"type": "integer", "minimum": 1},
                        "audio_stream_ids": {
                            "type": "array",
                            "items": {"type": "string", "maxLength": 64},
                            "minItems": 1,
                            "maxItems": 32,
                        },
                        "container": {"type": "string", "enum": ["matroska"]},
                    },
                    ("duration_frames", "audio_stream_ids", "container"),
                ),
            ),
        ),
        parameter_schema={
            "$schema": JSON_SCHEMA_DIALECT,
            "type": "object",
            "properties": {"container": {"type": "string", "enum": ["matroska"]}},
            "required": ["container"],
            "additionalProperties": False,
        },
    )


class SyntheticDemuxAdapter:
    """把 ProgramMedia 声明拆成 video 和 parallel audio set 候选值。"""

    def invoke(self, request: EngineInvocationRequest) -> EngineInvocationResult:
        source_value = request.input_value("program_in")
        if not isinstance(source_value, ArtifactValue):
            raise TypeError("program_in 必须是 ArtifactValue")
        source = source_value.artifact
        duration = cast(int, source.attributes["duration_frames"])
        stream_ids = cast(tuple[str, ...], source.attributes["audio_stream_ids"])
        stage_run_id = request.stage_run.stage_run_id
        video = Artifact(
            artifact_id=f"artifact.{stage_run_id}.video",
            artifact_type=ArtifactType.MEDIA,
            media_kind=MediaKind.VIDEO,
            scope=Scope.PROGRAM,
            scope_id=request.stage_run.scope_id,
            producer_stage_run_id=stage_run_id,
            attributes={"duration_frames": duration},
        )
        coverage = CoverageSpan(unit=CoverageUnit.FRAME, start=0, end=duration)
        members = tuple(
            ArtifactSetMember(
                member_id=f"member.audio.{index:03d}",
                artifact=Artifact(
                    artifact_id=f"artifact.{stage_run_id}.audio.{index:03d}",
                    artifact_type=ArtifactType.MEDIA,
                    media_kind=MediaKind.AUDIO,
                    scope=Scope.PROGRAM,
                    scope_id=request.stage_run.scope_id,
                    producer_stage_run_id=stage_run_id,
                    attributes={"duration_frames": duration, "stream_id": stream_id},
                ),
                coverage=coverage,
            )
            for index, stream_id in enumerate(stream_ids, start=1)
        )
        audio_set = ArtifactSet(
            artifact_set_id=f"artifact_set.{stage_run_id}.audio",
            artifact_type=ArtifactType.MEDIA,
            media_kind=MediaKind.AUDIO,
            scope=Scope.PROGRAM,
            scope_id=request.stage_run.scope_id,
            expected_member_ids=tuple(member.member_id for member in members),
            members=members,
            coverage=coverage,
            coverage_mode=CoverageMode.PARALLEL,
            producer_stage_run_id=stage_run_id,
        )
        return EngineInvocationResult(
            sdk_contract_version="0.1.0",
            invocation_id=request.invocation_id,
            engine=request.stage_run.engine,
            outputs=(
                ArtifactValue(port_id="video_out", artifact=video),
                ArtifactSetValue(port_id="audio_out", artifact_set=audio_set),
            ),
        )


class SyntheticMuxAdapter:
    """把 program video 与完整有序 audio set 组合为 ProgramMedia 候选值。"""

    def invoke(self, request: EngineInvocationRequest) -> EngineInvocationResult:
        video_value = request.input_value("video_in")
        audio_value = request.input_value("audio_in")
        if not isinstance(video_value, ArtifactValue) or not isinstance(
            audio_value, ArtifactSetValue
        ):
            raise TypeError("Mux 输入形状不匹配")
        duration = cast(int, video_value.artifact.attributes["duration_frames"])
        stream_ids = [
            cast(str, member.artifact.attributes["stream_id"])
            for member in audio_value.artifact_set.members
        ]
        stage_run_id = request.stage_run.stage_run_id
        output = Artifact(
            artifact_id=f"artifact.{stage_run_id}.program",
            artifact_type=ArtifactType.MEDIA,
            media_kind=MediaKind.PROGRAM_MEDIA,
            scope=Scope.PROGRAM,
            scope_id=request.stage_run.scope_id,
            producer_stage_run_id=stage_run_id,
            attributes={
                "duration_frames": duration,
                "audio_stream_ids": cast(JsonValue, stream_ids),
                "container": request.stage_run.parameters["container"],
            },
        )
        return EngineInvocationResult(
            sdk_contract_version="0.1.0",
            invocation_id=request.invocation_id,
            engine=request.stage_run.engine,
            outputs=(ArtifactValue(port_id="program_out", artifact=output),),
        )


def _descriptor(
    package_id: str,
    manifest: EngineManifest,
    adapter_type: type[SyntheticDemuxAdapter] | type[SyntheticMuxAdapter],
) -> EnginePackageDescriptor:
    source = inspect.getsource(adapter_type).encode("utf-8")
    digest = hashlib.sha256(manifest.to_canonical_bytes() + b"\0" + source).hexdigest()
    return EnginePackageDescriptor(
        sdk_contract_version="0.1.0",
        package_id=package_id,
        package_version="0.1.0",
        implementation_digest=f"sha256:{digest}",
        engine=EngineBinding.from_manifest(manifest),
    )


def builtin_engine_packages() -> tuple[EnginePackage, EnginePackage]:
    """构造 Phase 1 的两个纯合成内建 Engine package。"""

    demux_manifest = synthetic_demux_manifest()
    mux_manifest = synthetic_mux_manifest()
    return (
        EnginePackage(
            descriptor=_descriptor(
                "zniku.builtin.synthetic-demux", demux_manifest, SyntheticDemuxAdapter
            ),
            manifest=demux_manifest,
            adapter=SyntheticDemuxAdapter(),
        ),
        EnginePackage(
            descriptor=_descriptor(
                "zniku.builtin.synthetic-mux", mux_manifest, SyntheticMuxAdapter
            ),
            manifest=mux_manifest,
            adapter=SyntheticMuxAdapter(),
        ),
    )
