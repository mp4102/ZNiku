"""独立验收 0.3.5 节点：新 Source 受控扫描事实、MR 同一准入和版本隔离的下游合同。"""

from __future__ import annotations

from collections.abc import Mapping
from fractions import Fraction
from pathlib import Path

from pydantic import ValidationError

from zniku.avenhance_v27 import validators as av27
from zniku.avenhance_v27.probe import AV27_NAMESPACE, Av27MediaError, probe_header
from zniku.runtime import NodeValidatorContext, NodeValidatorResult
from zniku.source_aligned import validators as shared
from zniku.source_aligned.node_contracts import EXTERNAL_TYPE_PREFIX, Geometry, Signal, _plain, fail

from .contracts import (
    NAMESPACE,
    SOURCE_NAMESPACE,
    VERSION,
    ExternalMetadata,
    OverlapMetadata,
    external_preflight,
    preflight,
)
from .definitions import definition_role, external_definition, source_program_definition
from .probe import admit_source, validate_source_header


def validate_source_program(context: NodeValidatorContext) -> NodeValidatorResult:
    """完整扫描只由新受控 Source adapter 做一次；这里重验绑定、stat、header 和合同事实。"""
    try:
        if context.request.definition != source_program_definition() or context.request.inputs:
            fail("SOURCE_DEFINITION", "Source exact definition/input 不符")
        outputs = av27._outputs(context, {"video", "source_media"})
        path = Path(av27._required_text(context.request.node.parameters, "source_path")).resolve(
            strict=True
        )
        if {item.path.resolve(strict=True) for item in outputs.values()} != {path}:
            fail("SOURCE_BINDING", "两路输出必须只引用同一 source_path")
        video, source = outputs["video"], outputs["source_media"]
        facts = video.producer_metadata
        if (
            facts != source.producer_metadata
            or set(facts)
            != {
                "contract_version",
                "namespace",
                "header",
                "size",
                "mtime_ns",
                "warnings",
                "cadence",
            }
            or facts.get("contract_version") != VERSION
        ):
            fail("SOURCE_FACTS", "缺少新受控 adapter 的完整扫描事实")
        stat = path.stat()
        if (facts.get("size"), facts.get("mtime_ns")) != (stat.st_size, stat.st_mtime_ns):
            fail("SOURCE_CHANGED", "扫描后源文件已变化")
        header = probe_header(path)
        actual_rate, actual_signal, _notices = validate_source_header(header)
        if _plain(facts.get("header")) != header.video.to_summary():
            fail("SOURCE_HEADER_CHANGED", "扫描后 header 不一致")
        namespace = _plain(facts.get("namespace"))
        if not isinstance(namespace, dict):
            fail("SOURCE_FACTS", "缺少准入 metadata")
        if (
            namespace.get("source_ordinal") != 0
            or type(namespace.get("source_ordinal")) is not int
            or type(namespace.get("frame_count")) is not int
            or namespace["frame_count"] <= 0
        ):
            fail("SOURCE_FACTS", "源 ordinal/帧数不合法")
        rate = Fraction(str(namespace.get("frame_rate")))
        if rate != actual_rate:
            fail("SOURCE_FACTS", "源 FPS 与实际 header 准入不符")
        signal = Signal.model_validate(namespace.get("signal"))
        geometry = Geometry.model_validate(namespace.get("geometry"))
        if (geometry.width, geometry.height) != (header.video.width, header.video.height):
            fail("SOURCE_FACTS", "扫描几何与实际 header 不符")
        if signal.model_dump(mode="json") != namespace.get("signal"):
            fail("SOURCE_FACTS", "信号解释不是规范完整字段")
        if signal.model_dump(mode="json") != av27._canonical_signal(actual_signal, role="Source"):
            fail("SOURCE_FACTS", "信号解释与实际 Source header 不符")
        if namespace.get("audio_tracks") != [audio.to_summary() for audio in header.audios]:
            fail("SOURCE_FACTS", "参考音轨与实际 Source header 不符")
        warnings = facts.get("warnings")
        if not isinstance(warnings, tuple | list) or not all(
            isinstance(item, str) for item in warnings
        ):
            fail("SOURCE_FACTS", "warnings 必须为文本数组")
        marker = {
            "contract_version": VERSION,
            "source_contract": "AVEnhanceFlow-2.7.0",
            "warnings": list(warnings),
            "cadence": _plain(facts.get("cadence")),
        }
        return NodeValidatorResult(
            passed=True,
            summary={
                "frame_count": namespace["frame_count"],
                "frame_rate": str(rate),
                "source_contract": "AVEnhanceFlow-2.7.0",
            },
            warnings=tuple(warnings),
            media_info_extensions={
                port: {AV27_NAMESPACE: namespace, SOURCE_NAMESPACE: marker} for port in outputs
            },
        )
    except (Av27MediaError, ValidationError, OSError, ValueError) as error:
        return _failure(error)


def validate_source_admission(context: NodeValidatorContext) -> NodeValidatorResult:
    if definition_role(context.request.definition) != "admission":
        return NodeValidatorResult(passed=False, message="新准入 definition 不匹配")
    for item in context.request.inputs:
        marker = item.media_info.get(SOURCE_NAMESPACE)
        if not isinstance(marker, Mapping) or marker.get("contract_version") != VERSION:
            return NodeValidatorResult(passed=False, message="gate 只接本版已准入 Source")
    return av27.validate_source_admission(context)


def _validate(context: NodeValidatorContext, role: str) -> NodeValidatorResult:
    return shared._validate(
        context,
        role,
        contract_reader=preflight,
        role_reader=definition_role,
        metadata_model=OverlapMetadata,
        namespace=NAMESPACE,
        verify_original_audio_origins=False,
    )


def validate_atomic_split(context: NodeValidatorContext) -> NodeValidatorResult:
    return _validate(context, "split")


def validate_enhancement(context: NodeValidatorContext) -> NodeValidatorResult:
    return _validate(context, "enhancement")


def validate_merge_video(context: NodeValidatorContext) -> NodeValidatorResult:
    return _validate(context, "merge")


def validate_fi_context(context: NodeValidatorContext) -> NodeValidatorResult:
    return _validate(context, "context")


def validate_frame_interpolation(context: NodeValidatorContext) -> NodeValidatorResult:
    return _validate(context, "fi")


def validate_fi_crop(context: NodeValidatorContext) -> NodeValidatorResult:
    return _validate(context, "crop")


def validate_program_encode(context: NodeValidatorContext) -> NodeValidatorResult:
    return _validate(context, "program")


def validate_final_mux(context: NodeValidatorContext) -> NodeValidatorResult:
    return _validate(context, "final")


def validate_external(context: NodeValidatorContext) -> NodeValidatorResult:
    """新 MR 保持 N→N 与选定参考属性，不再重扫原片或额外检查 audio priming。"""
    try:
        params, _source, expected = external_preflight(
            context.request.inputs, context.request.node.parameters
        )
        if context.request.definition != external_definition(params.declared_container):
            fail("DEFINITION", "MR exact definition 与声明容器不一致")
        output = av27._single_output(context, "video")
        av27._require_fixed_name(context, output, f"restoration.{params.declared_container}")
        av27._require_no_producer_metadata(output)
        if output.kind != "VideoFile" or output.size <= 0 or output.frame_range is not None:
            fail("EXTERNAL_OUTPUT", "MR 必须为完整非空 VideoFile")
        admitted = admit_source(output.path)
        shared._require_container(output.path, admitted.header, params.declared_container)
        if (
            admitted.timeline.frame_count != params.source.frame_count
            or admitted.frame_rate != Fraction(params.source.frame_rate)
            or (admitted.header.video.width, admitted.header.video.height) != expected.geometry
            or av27._canonical_signal(admitted.signal, role="MR") != dict(expected.signal)
        ):
            fail("EXTERNAL_CONTRACT", "MR 的 N/FPS/几何/信号与参考源不符；禁止重规划掩盖变化")
        metadata = ExternalMetadata(
            producer_type_id=EXTERNAL_TYPE_PREFIX + params.declared_container,
            source=params.source,
            declared_container=params.declared_container,
            model_name=params.model_name,
            model_version=params.model_version,
            geometry=Geometry(width=expected.geometry[0], height=expected.geometry[1]),
            signal=Signal.model_validate(dict(expected.signal)),
        )
        return NodeValidatorResult(
            passed=True,
            summary={"role": "external", "frame_count": params.source.frame_count},
            warnings=(*admitted.warnings, "帧数与时间轴不证明逐帧内容或 AI 模型身份。"),
            media_info_extensions={"video": {NAMESPACE: metadata.model_dump(mode="json")}},
        )
    except (Av27MediaError, ValidationError, OSError, ValueError) as error:
        return _failure(error)


def _failure(error: Exception) -> NodeValidatorResult:
    code = error.code if isinstance(error, Av27MediaError) else "E_SOURCE_ADMISSION_VALIDATION"
    return NodeValidatorResult(passed=False, summary={"code": code}, message=str(error))
