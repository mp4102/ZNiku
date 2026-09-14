"""严格登记新版色彩输出：来源解释不变，当前实际色彩观察单独保存。

自动节点的完整观察在可取消 adapter 完成；人工输出只能现场扫描，不信任用户附带 facts。
"""

from __future__ import annotations

from fractions import Fraction

from pydantic import ValidationError

from zniku.avenhance_v27 import validators as legacy
from zniku.avenhance_v27.probe import (
    Av27MediaError,
    probe_header,
    rates_equivalent,
    require_progressive_zero_rotation,
    require_square_sar,
)
from zniku.media.probe import MediaNodeError
from zniku.prepared_source.validators import _require_container, validate_contract_outputs
from zniku.runtime import NodeValidatorContext, NodeValidatorResult
from zniku.source_aligned.timeline import probe_cfr
from zniku.source_color.contracts import read_gate
from zniku.source_preparation.progress import progress_log

from .definitions import definition_role, external_definition
from .node_contracts import (
    EXTERNAL_TYPE_PREFIX,
    OVERLAP_NAMESPACE,
    ExternalMetadata,
    Geometry,
    external_preflight,
    fail,
    preflight,
)
from .signal import (
    check_output_observation,
    observe_output,
    validate_color_output,
    validate_producer,
)


def _failure(error: Exception) -> NodeValidatorResult:
    code = (
        error.code
        if isinstance(error, Av27MediaError | MediaNodeError)
        else "E_PREPARED_COLOR_METADATA"
    )
    return NodeValidatorResult(passed=False, summary={"code": code}, message=str(error))


def _validate(context: NodeValidatorContext, role: str) -> NodeValidatorResult:
    try:
        if definition_role(context.request.definition) != role:
            fail("COLOR_DEFINITION", "不接受旧版、角色误配或修改过的新 definition")
        contract = preflight(role, context.request.inputs, context.request.node.parameters)
        return validate_contract_outputs(
            context,
            role,
            contract,
            OVERLAP_NAMESPACE,
            definition_role,
            read_gate,
            validate_color_output,
            validate_producer,
        )
    except (Av27MediaError, MediaNodeError, ValidationError, OSError) as error:
        return _failure(error)


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
    """N→N MR 仍只绑定参考视频；观察缺失可继承准入解释，明确冲突不能导入。"""
    try:
        if definition_role(context.request.definition) != "external":
            fail("COLOR_DEFINITION", "外部修复必须使用完整的新 exact 定义")
        params, source, expected, interpretation = external_preflight(
            context.request.inputs, context.request.node.parameters
        )
        if context.request.definition != external_definition(params.declared_container):
            fail("COLOR_DEFINITION", "实际声明容器与定义不符")
        output = legacy._single_output(context, "video")
        if output.kind != "VideoFile" or output.size <= 0 or output.frame_range is not None:
            fail("COLOR_EXTERNAL", "MR 必须为非空 VideoFile 且无伪造源区间")
        legacy._require_fixed_name(context, output, f"restoration.{params.declared_container}")
        legacy._require_no_producer_metadata(output)
        if output.path.suffix.casefold() != "." + params.declared_container:
            fail("COLOR_CONTAINER", "正式输出后缀不符")
        media = probe_header(output.path)
        legacy._require_mr_layout(media)
        _require_container(output.path, media, params.declared_container)
        video = media.video
        require_square_sar(video, role="MR")
        require_progressive_zero_rotation(video, role="MR")
        rate = Fraction(params.source.frame_rate)
        if (video.width, video.height) != expected.geometry or not all(
            rates_equivalent(value, rate)
            for value in (video.frame_rate, video.avg_frame_rate, video.r_frame_rate)
        ):
            fail("COLOR_EXTERNAL", "MR 几何或 FPS 与参考不一致")
        with progress_log(context.work_dir / "logs" / "stdout.log"):
            probe_cfr(source.path, count=params.source.frame_count, rate=rate)
            probe_cfr(output.path, count=params.source.frame_count, rate=rate)
            observed = observe_output(output.path)
        inherited = check_output_observation(observed, interpretation, params.source.frame_count)
        for frame in observed.frames.variants:
            if (
                (frame.width, frame.height) != expected.geometry
                or frame.interlaced
                or frame.sample_aspect_ratio not in {"1:1", "1/1"}
                or frame.pixel_format != video.pixel_format
            ):
                fail("COLOR_EXTERNAL", "MR 全帧几何/场序不符")
        metadata = ExternalMetadata(
            producer_type_id=EXTERNAL_TYPE_PREFIX + params.declared_container,
            source=params.source,
            declared_container=params.declared_container,
            model_name=params.model_name,
            model_version=params.model_version,
            geometry=Geometry(width=expected.geometry[0], height=expected.geometry[1]),
            interpretation=interpretation,
            observed_signal=observed,
            inherited_interpretation=inherited,
        )
        return NodeValidatorResult(
            passed=True,
            summary={
                "role": "external",
                "frame_count": params.source.frame_count,
                "original_audio_used_for_final": True,
            },
            warnings=("帧数和时间轴通过不证明逐帧内容对应或所用 AI 模型。",),
            media_info_extensions={"video": {OVERLAP_NAMESPACE: metadata.model_dump(mode="json")}},
        )
    except (Av27MediaError, MediaNodeError, ValidationError, OSError) as error:
        return _failure(error)
