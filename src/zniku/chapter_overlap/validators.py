"""从直接输入与实际媒体 header 验收新重叠节点，只在整体通过后登记局部 metadata。

共享旧 AV27 已验收的低层容器、几何、色彩与音轨检查，不复用其旧逐章 FI 或 Program 补尾语义。
人工来件不得自带 producer facts；Aion/相位仍为候选声明，header 不能证明模型、内容相位或画质。
"""

from __future__ import annotations

from fractions import Fraction

from pydantic import ValidationError

from zniku.avenhance_v27 import validators as legacy
from zniku.avenhance_v27.probe import (
    Av27MediaError,
    audio_signatures_from_metadata,
    probe_header,
    rates_equivalent,
    require_only_av_streams,
    require_progressive_zero_rotation,
    require_square_sar,
)
from zniku.runtime import FrameRange, NodeValidatorContext, NodeValidatorResult

from .node_contracts import OVERLAP_NAMESPACE, OverlapMetadata, fail, preflight

_NAMES = {
    "enhancement": "enhancement.mov",
    "merge": "merge.mov",
    "context": "context.mov",
    "fi": "fi.raw.mov",
    "crop": "fi.crop.mov",
    "program": "program.mp4",
    "final": "final.mkv",
}


def _validate(context: NodeValidatorContext, role: str) -> NodeValidatorResult:
    """结果是一次轻量验收；失败时不返回任何 namespace，不能局部登记成功输出。"""
    try:
        contract = preflight(role, context.request.inputs, context.request.node.parameters)
        planned = {item.port_id: item.metadata for item in contract.outputs}
        definition = context.request.definition
        if definition.version != "0.3.2" or any(
            m.producer_type_id != definition.type_id for m in planned.values()
        ):
            fail("DEFINITION", "当前 definition identity 与局部输出职责不符")
        outputs = legacy._outputs(context, set(planned))
        if set(planned) != {port.port_id for port in definition.output_ports}:
            fail("OUTPUT_SHAPE", "声明的动态 output shape 与 planner 不符")
        extensions: dict[str, dict[str, dict[str, object]]] = {}
        warnings: list[str] = []
        for port, metadata in planned.items():
            output = outputs[port]
            expected_kind = "MediaFile" if role == "final" else "VideoFile"
            if output.kind != expected_kind or output.size <= 0:
                fail("OUTPUT_KIND", "输出类型错误或媒体为空")
            legacy._require_fixed_name(
                context, output, f"{port}.mkv" if role == "split" else _NAMES[role]
            )
            if role in {"enhancement", "fi"}:
                legacy._require_no_producer_metadata(output)
            else:
                legacy._required_producer_count(output, metadata.frame_count)
            if role == "split":
                leaf = metadata.leaf
                assert leaf is not None
                if output.frame_range != FrameRange(
                    start_frame=leaf.start_frame, end_frame=leaf.end_frame
                ):
                    fail("SPLIT_RANGE", "Split Artifact frame_range 与严格规划不符")
            elif output.frame_range is not None:
                fail("OUTPUT_RANGE", "非 Split 输出不得伪造源 frame_range")
            media = probe_header(output.path)
            video = media.video
            if role == "final":
                legacy._require_formats(media, {"matroska"}, role=role)
                require_only_av_streams(media, role=role)
                if len(media.videos) != 1 or media.chapter_count:
                    fail("FINAL_LAYOUT", "Final 必须唯一视频且无容器章节")
                source = next(item for item in context.request.inputs if item.port_id == "sources")
                if tuple(a.signature() for a in media.audios) != audio_signatures_from_metadata(
                    source.media_info
                ):
                    fail("FINAL_AUDIO", "Final 原音轨数量、顺序或格式签名不一致")
            else:
                legacy._require_formats(
                    media,
                    {"matroska"}
                    if role == "split"
                    else {"mov", "mp4"}
                    if role == "program"
                    else {"mov"},
                    role=role,
                )
                if role == "enhancement":
                    legacy._require_enhancement_layout(media)
                else:
                    legacy._require_video_only(media, role=role, chapters_forbidden=True)
            if role == "split":
                if video.codec != "ffv1" or video.pixel_format != "yuv420p10le":
                    fail("SPLIT_CODEC", "Split 必须为 FFV1 yuv420p10le")
            elif role in {"program", "final"}:
                if (
                    video.codec != "hevc"
                    or legacy._normalized_profile(video.profile) != "main10"
                    or video.pixel_format != "yuv420p10le"
                ):
                    fail("ENCODE_CODEC", "成片必须为 HEVC Main10 yuv420p10le")
                if role == "program" and (video.codec_tag_string or "").casefold() != "hvc1":
                    fail("ENCODE_TAG", "Program 必须为 hvc1")
            else:
                legacy._require_prores(video, role=role)
            require_square_sar(video, role=role)
            require_progressive_zero_rotation(video, role=role)
            signal, notices = legacy._resolved_signal(
                video,
                role=role,
                require_left=role in {"split", "program", "final"},
                require_explicit=role in {"split", "program", "final"},
            )
            warnings.extend(notices)
            legacy._compare_signal(signal, metadata.signal.model_dump(), role=role)
            rate = Fraction(metadata.frame_rate)
            geometry = (metadata.geometry.width, metadata.geometry.height)
            if role == "final":
                legacy._require_final_header_contract(video, canonical_rate=rate, geometry=geometry)
                original = legacy._MediaContract(
                    metadata.frame_count,
                    rate,
                    geometry,
                    signal,
                    float(Fraction(metadata.frame_count, 1) / rate),
                )
                legacy._validate_final_duration(media, original)
            elif role == "fi":
                if (video.width, video.height) != geometry or any(
                    not rates_equivalent(observed, rate, tolerance=Fraction(1, 500000))
                    for observed in (video.frame_rate, video.avg_frame_rate, video.r_frame_rate)
                ):
                    fail("FI_MEDIA", "FI 几何变化或 FPS 不等价 exact 2x")
            else:
                legacy._require_header_contract(video, rate=rate, geometry=geometry, role=role)
            if role in {"context", "crop", "program"} and video.time_base != Fraction(
                1, rate.numerator
            ):
                fail("TIME_BASE", "受控输出必须使用 exact CFR track timescale")
            if role in {"enhancement", "fi"}:
                count, _ = legacy._external_frame_count(media)
                if count != metadata.frame_count:
                    fail("EXTERNAL_COUNT", "外部帧数不满足原叶 N 或 raw 2M-1")
            else:
                legacy._optional_header_count_matches(video, metadata.frame_count, role=role)
            # 这里只复制已完整重验且由实际输出确认的纯数据，不使用 adapter 提交的来源 metadata。
            verified = OverlapMetadata.model_validate(metadata.model_dump())
            extensions[port] = {OVERLAP_NAMESPACE: verified.model_dump(mode="json")}
        return NodeValidatorResult(
            passed=True,
            summary={
                "role": role,
                "output_count": len(outputs),
                "frame_counts": {port: m.frame_count for port, m in planned.items()},
                "fi_acceptance": "pending_real_acceptance",
            },
            warnings=tuple(warnings),
            media_info_extensions=extensions,
        )
    except (Av27MediaError, ValidationError) as error:
        code = error.code if isinstance(error, Av27MediaError) else "E_OVERLAP_METADATA"
        return NodeValidatorResult(passed=False, summary={"code": code}, message=str(error))


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
