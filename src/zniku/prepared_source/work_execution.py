"""普通工作链的薄执行/验收入口，复用媒体 helper 与精确章叶/FI 合同。

取消、失败和输出登记仍由普通 Runner 处理。默认不执行全片颜色码流审计或 PCM 恒等比较；
已验证的工作参考不再次完整解码，人工新来件仍检查它本身的必要帧时间轴。
"""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction

from pydantic import ValidationError

from zniku.avenhance_v27 import validators as legacy
from zniku.avenhance_v27.probe import (
    Av27MediaHeader,
    Av27VideoHeader,
    probe_header,
    rates_equivalent,
)
from zniku.media.probe import MediaNodeError
from zniku.runtime import (
    NodeValidatorContext,
    NodeValidatorResult,
    PythonAdapterContext,
    PythonAdapterResult,
    RunnerInput,
)
from zniku.runtime.runner import ValidatedOutput
from zniku.source_preparation.progress import progress_log
from zniku.source_preparation.work_contracts import read_gate, validate_audio_sources
from zniku.source_preparation.work_models import observed_rate_matches

from . import adapters as media
from .node_contracts import EXTERNAL_TYPE_PREFIX, Geometry, NodeContract, Signal, fail
from .validators import _require_container, validate_contract_outputs
from .work_audio import verify_audio_copy
from .work_contracts import (
    OVERLAP_NAMESPACE,
    WorkExternalMetadata,
    WorkOverlapMetadata,
    effective_contract,
    external_preflight,
    preflight,
)
from .work_definitions import definition_role, external_definition
from .work_timeline import probe_work_cfr


def _run(context: PythonAdapterContext, role: str) -> PythonAdapterResult:
    if definition_role(context.definition) != role:
        fail("WORK_DEFINITION", "不接受其他版本或改写后的工作节点定义")
    contract = preflight(role, context.inputs, context.node.parameters)
    with progress_log(context.stdout_log_path):
        if role == "split":
            result = media.atomic_split_media(
                context, contract, _split_filter, reference_validator=None
            )
        elif role == "final":
            active = replace(
                context,
                progress=None
                if context.progress is None
                else media._FinalProgress(context.progress),
            )
            result = media.final_mux_media(
                active,
                contract,
                read_gate,
                validate_audio_sources,
                audio_origin=lambda gate: Fraction(gate.audio_video_start),
                audio_integrity=verify_audio_copy,
            )
            if context.progress is not None:
                context.progress.report(1.0)
        else:
            helper = {
                "merge": media.merge_video_media,
                "context": media.fi_context_media,
                "crop": media.fi_crop_media,
                "program": media.program_encode_media,
            }[role]
            result = helper(context, contract)
    return replace(
        result,
        validation_summary={
            **result.validation_summary,
            "inspection_policy": "working-media",
            "content_identity_proven": False,
        },
    )


def atomic_split(context: PythonAdapterContext) -> PythonAdapterResult:
    return _run(context, "split")


def merge_video(context: PythonAdapterContext) -> PythonAdapterResult:
    return _run(context, "merge")


def fi_context(context: PythonAdapterContext) -> PythonAdapterResult:
    return _run(context, "context")


def fi_crop(context: PythonAdapterContext) -> PythonAdapterResult:
    return _run(context, "crop")


def program_encode(context: PythonAdapterContext) -> PythonAdapterResult:
    return _run(context, "program")


def final_mux(context: PythonAdapterContext) -> PythonAdapterResult:
    return _run(context, "final")


def _split_filter(source: RunnerInput, contract: NodeContract) -> str:
    width, height = effective_contract(source, contract.source.expectation()).geometry
    if width * 9 != height * 16:
        fail("WORK_GEOMETRY", "本处理链要求 16:9；请先明确准备适合的工作参考")
    scale = (
        ""
        if (width, height) == (1920, 1080)
        else "zscale=w=1920:h=1080:filter=spline36:chromalin=left:chromal=left,"
    )
    rate = Fraction(contract.source.frame_rate)
    return (
        "[0:v:0]setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709,"
        + scale
        + "format=pix_fmts=yuv420p10le,setsar=1/1,"
        + f"settb=expr={rate.denominator}/{rate.numerator},setpts=N[vsegment]"
    )


def _failure(error: Exception) -> NodeValidatorResult:
    code = error.code if isinstance(error, MediaNodeError) else "E_WORK_SOURCE_VALIDATION"
    return NodeValidatorResult(passed=False, summary={"code": code}, message=str(error))


def _color_output(
    context: NodeValidatorContext,
    role: str,
    output: ValidatedOutput,
    metadata: WorkOverlapMetadata,
    header: Av27MediaHeader,
) -> tuple[WorkOverlapMetadata, tuple[str, ...]]:
    """输出缺声明可继承已绑定工作解释，不能覆盖明确冲突或伪称完整源声明。"""
    actual, notices = legacy._resolved_signal(
        header.video,
        role=role,
        require_explicit=role in {"split", "program", "final"},
        require_left=role in {"split", "program", "final"},
    )
    legacy._compare_signal(actual, metadata.signal.model_dump(), role=role)
    inherited = tuple(
        field
        for field in (
            "color_primaries",
            "color_transfer",
            "color_space",
            "color_range",
            "chroma_location",
        )
        if getattr(header.video, field) in {None, "unknown", "unspecified"}
    )
    return metadata.model_copy(
        update={"observation_scope": "ffprobe-stream", "inherited_interpretation": inherited}
    ), notices


def _external_count(
    context: NodeValidatorContext,
    output: ValidatedOutput,
    metadata: WorkOverlapMetadata,
    header: Av27MediaHeader,
) -> None:
    """人工新来件只做一次必要 N/PTS 扫描；缺少 nb_frames 不是重扫或拒绝理由。"""
    legacy._optional_header_count_matches(header.video, metadata.frame_count, role=metadata.role)
    with progress_log(context.work_dir / "logs" / "stdout.log"):
        probe_work_cfr(output.path, count=metadata.frame_count, rate=Fraction(metadata.frame_rate))


def _validate(context: NodeValidatorContext, role: str) -> NodeValidatorResult:
    try:
        if definition_role(context.request.definition) != role:
            fail("WORK_DEFINITION", "完成检查不接受其他 exact 或修改过的定义")
        contract = preflight(role, context.request.inputs, context.request.node.parameters)
        return validate_contract_outputs(
            context,
            role,
            contract,
            OVERLAP_NAMESPACE,
            definition_role,
            read_gate,
            _color_output,
            external_count_validator=_external_count,
            header_validator=_working_header,
        )
    except (MediaNodeError, ValidationError, OSError, ValueError) as error:
        return _failure(error)


def _working_header(
    video: Av27VideoHeader,
    *,
    rate: Fraction,
    geometry: tuple[int, int],
    role: str,
) -> None:
    """普通 Split MKV 使用纳秒表示容差；其他角色和旧 exact 的头合同不变。"""
    if role != "split":
        legacy._require_header_contract(video, rate=rate, geometry=geometry, role=role)
    elif (video.width, video.height) != geometry or not all(
        observed_rate_matches(rate, value)
        for value in (video.frame_rate, video.avg_frame_rate, video.r_frame_rate)
    ):
        fail("WORK_SPLIT_HEADER", "Split 几何或声明 FPS 不符合所选工作时间轴")


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
    """普通工作源之后的 MR 仍要求 N→N；不要与允许新 N 的源准备外部节点混淆。"""
    try:
        params, _source, expected = external_preflight(
            context.request.inputs, context.request.node.parameters
        )
        if context.request.definition != external_definition(params.declared_container):
            fail("WORK_DEFINITION", "MR 声明容器或定义不匹配")
        output = legacy._single_output(context, "video")
        if output.kind != "VideoFile" or output.size <= 0 or output.frame_range is not None:
            fail("WORK_OUTPUT", "MR 必须为非空且无伪造源区间的 VideoFile")
        legacy._require_fixed_name(context, output, f"restoration.{params.declared_container}")
        legacy._require_no_producer_metadata(output)
        header = probe_header(output.path)
        legacy._require_mr_layout(header)
        _require_container(output.path, header, params.declared_container)
        notices = legacy._validate_video_against_contract(
            header.video, expected, role="工作参考前处理", exact_rate=False
        )
        rate = Fraction(params.source.frame_rate)
        if not all(
            rates_equivalent(observed, rate)
            for observed in (header.video.avg_frame_rate, header.video.r_frame_rate)
        ):
            fail("WORK_MR_RATE", "外部前处理不满足当前工作参考 FPS")
        with progress_log(context.work_dir / "logs" / "stdout.log"):
            probe_work_cfr(output.path, count=params.source.frame_count, rate=rate)
        metadata = WorkExternalMetadata(
            producer_type_id=EXTERNAL_TYPE_PREFIX + params.declared_container,
            source=params.source,
            declared_container=params.declared_container,
            model_name=params.model_name,
            model_version=params.model_version,
            geometry=Geometry(width=expected.geometry[0], height=expected.geometry[1]),
            signal=Signal.model_validate(dict(expected.signal)),
            inherited_interpretation=tuple(
                field
                for field in (
                    "color_primaries",
                    "color_transfer",
                    "color_space",
                    "color_range",
                    "chroma_location",
                )
                if getattr(header.video, field) in {None, "unknown", "unspecified"}
            ),
        )
        return NodeValidatorResult(
            passed=True,
            summary={
                "role": "external",
                "frame_count": params.source.frame_count,
                "content_correspondence": "operator-declared-not-proven",
            },
            warnings=notices,
            media_info_extensions={"video": {OVERLAP_NAMESPACE: metadata.model_dump(mode="json")}},
        )
    except (MediaNodeError, ValidationError, OSError, ValueError) as error:
        return _failure(error)
