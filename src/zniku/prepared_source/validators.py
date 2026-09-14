"""从直接输入与实际媒体事实验收工作源绑定 0.3.4 节点，只在整体通过后登记局部 metadata。

共享旧 AV27 已验收的低层容器、几何、色彩与音轨检查，不复用其旧逐章 FI 或 Program 补尾语义。
人工来件不得自带 producer facts；Aion/相位仍为候选声明，header 不能证明模型、内容相位或画质。
"""

from __future__ import annotations

from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from zniku.avenhance_v27 import validators as legacy
from zniku.avenhance_v27.probe import (
    Av27MediaError,
    Av27MediaHeader,
    probe_header,
    rates_equivalent,
    require_only_av_streams,
    require_progressive_zero_rotation,
    require_square_sar,
)
from zniku.media.probe import MediaNodeError
from zniku.runtime import FrameRange, NodeValidatorContext, NodeValidatorResult
from zniku.source_aligned.timeline import (
    probe_cfr,
    same_audio_signatures,
    verify_audio_origins,
)

from .node_contracts import (
    EXTERNAL_TYPE_PREFIX,
    OVERLAP_NAMESPACE,
    ExternalMetadata,
    Geometry,
    NodeContract,
    Signal,
    external_preflight,
    fail,
    preflight,
)

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
    """旧 exact 入口固定使用原 reader 与色彩合同。"""
    from zniku.source_preparation.contracts import read_gate

    from .definitions import definition_role

    try:
        contract = preflight(role, context.request.inputs, context.request.node.parameters)
    except (Av27MediaError, MediaNodeError, ValidationError) as error:
        code = (
            error.code
            if isinstance(error, Av27MediaError | MediaNodeError)
            else "E_PREPARED_SOURCE_METADATA"
        )
        return NodeValidatorResult(passed=False, summary={"code": code}, message=str(error))
    return validate_contract_outputs(
        context, role, contract, OVERLAP_NAMESPACE, definition_role, read_gate
    )


def validate_contract_outputs(
    context: NodeValidatorContext,
    role: str,
    contract: NodeContract,
    namespace: str,
    definition_checker: Callable[..., str | None],
    gate_reader: Callable[..., Any],
    color_validator: Callable[..., tuple[Any, tuple[str, ...]]] | None = None,
    producer_validator: Callable[..., None] | None = None,
    external_count_validator: Callable[..., None] | None = None,
    header_validator: Callable[..., None] | None = None,
) -> NodeValidatorResult:
    """结果是一次轻量验收；失败时不返回任何 namespace，不能局部登记成功输出。"""
    try:
        planned = {item.port_id: item.metadata for item in contract.outputs}
        definition = context.request.definition

        if definition_checker(definition) != role or any(
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
                if producer_validator is None:
                    legacy._required_producer_count(output, metadata.frame_count)
                else:
                    producer_validator(output, metadata.frame_count)
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
                gate = gate_reader(
                    next(item for item in context.request.inputs if item.port_id == "gate")
                )
                sources = tuple(
                    item for item in context.request.inputs if item.port_id == "sources"
                )
                signatures: list[dict[str, object]] = []
                for source, binding in zip(sources, gate.audio_bindings, strict=True):
                    by_index = {track.index: track for track in probe_header(source.path).audios}
                    signatures.extend(
                        dict(by_index[track.stream_index].signature()) for track in binding.tracks
                    )
                if not same_audio_signatures(
                    tuple(a.signature() for a in media.audios),
                    signatures,
                ):
                    fail("FINAL_AUDIO", "Final 原音轨数量、顺序或格式签名不一致")
                # 首发准入仅允许原件作为显式音频载体；不是从修复/MR路径推断音轨来源。
                if len(sources) != 1:
                    fail("FINAL_AUDIO_MAPPING", "当前音频保持策略只支持单个原件载体")
                verify_audio_origins(sources[0].path, output.path)
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
            if color_validator is None:
                signal, notices = legacy._resolved_signal(
                    video,
                    role=role,
                    require_left=role in {"split", "program", "final"},
                    require_explicit=role in {"split", "program", "final"},
                )
                legacy._compare_signal(signal, metadata.signal.model_dump(), role=role)
                output_metadata = metadata
            else:
                output_metadata, notices = color_validator(context, role, output, metadata, media)
            warnings.extend(notices)
            rate = Fraction(metadata.frame_rate)
            geometry = (metadata.geometry.width, metadata.geometry.height)
            if role == "final":
                legacy._require_final_header_contract(video, canonical_rate=rate, geometry=geometry)
                # 完整 packet/span 和音频样本比较已由可取消 automatic adapter 执行。
                # validator 只做有界 header/首点/直接输入检查，不再次无进度遍历全片。
                legacy._optional_header_count_matches(video, metadata.frame_count, role=role)
            elif role == "fi":
                if (video.width, video.height) != geometry or any(
                    not rates_equivalent(observed, rate, tolerance=Fraction(1, 500000))
                    for observed in (video.frame_rate, video.avg_frame_rate, video.r_frame_rate)
                ):
                    fail("FI_MEDIA", "FI 几何变化或 FPS 不等价 exact 2x")
            else:
                if header_validator is None:
                    legacy._require_header_contract(video, rate=rate, geometry=geometry, role=role)
                else:
                    header_validator(video, rate=rate, geometry=geometry, role=role)
            if role in {"context", "crop", "program"} and video.time_base != Fraction(
                1, rate.numerator
            ):
                fail("TIME_BASE", "受控输出必须使用 exact CFR track timescale")
            if role in {"enhancement", "fi"}:
                if external_count_validator is None:
                    count, _ = legacy._external_frame_count(media)
                    if count != metadata.frame_count:
                        fail("EXTERNAL_COUNT", "外部帧数不满足原叶 N 或 raw 2M-1")
                else:
                    # 受信普通策略在此一次性检查 N/PTS；不随后回落到旧无取消全片计数。
                    # 该 hook 不来自 Graph 参数，旧 exact 仍使用原分支。
                    external_count_validator(context, output, metadata, media)
            else:
                legacy._optional_header_count_matches(video, metadata.frame_count, role=role)
            # 这里只复制已完整重验且由实际输出确认的纯数据，不使用 adapter 提交的来源 metadata。
            verified = type(output_metadata).model_validate(output_metadata.model_dump())
            extensions[port] = {namespace: verified.model_dump(mode="json")}
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
    except (Av27MediaError, MediaNodeError, ValidationError) as error:
        code = (
            error.code
            if isinstance(error, Av27MediaError | MediaNodeError)
            else "E_PREPARED_SOURCE_METADATA"
        )
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


def validate_external(context: NodeValidatorContext) -> NodeValidatorResult:
    """外部 N→N 修复只在真实容器、源/结果 CFR 与局部属性全部通过后登记。"""
    try:
        params, source, expected = external_preflight(
            context.request.inputs, context.request.node.parameters
        )
        from .definitions import external_definition

        if context.request.definition != external_definition(params.declared_container):
            fail("DEFINITION", "外部修复 exact definition 与声明容器不一致")
        output = legacy._single_output(context, "video")
        if output.kind != "VideoFile" or output.size <= 0:
            fail("OUTPUT_KIND", "外部修复必须为非空 VideoFile")
        legacy._require_fixed_name(context, output, f"restoration.{params.declared_container}")
        if output.path.suffix.casefold() != "." + params.declared_container:
            fail("CONTAINER_SUFFIX", "正式目标后缀必须对应声明容器")
        legacy._require_no_producer_metadata(output)
        if output.frame_range is not None:
            fail("OUTPUT_RANGE", "外部修复不得伪造局部源区间")
        media = probe_header(output.path)
        legacy._require_mr_layout(media)
        _require_container(output.path, media, params.declared_container)
        notices = legacy._validate_video_against_contract(
            media.video, expected, role="Source-aligned restoration", exact_rate=False
        )
        rate = Fraction(params.source.frame_rate)
        # 只允许 header 的同一 CFR rational 量化表示，最终依据完整逐帧展示时刻。
        if not all(
            rates_equivalent(value, rate)
            for value in (media.video.avg_frame_rate, media.video.r_frame_rate)
        ):
            fail("FPS", "外部修复 FPS 不等价原片精确 FPS")
        probe_cfr(source.path, count=params.source.frame_count, rate=rate)
        probe_cfr(output.path, count=params.source.frame_count, rate=rate)
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
            summary={
                "role": "external",
                "frame_count": params.source.frame_count,
                "alignment": "relative-presentation-cfr",
                "content_correspondence": "operator-declared-not-proven",
                "original_audio_used_for_final": True,
            },
            warnings=(*notices, "帧数和时间轴通过不证明逐帧内容对应或所用 AI 模型。"),
            media_info_extensions={"video": {OVERLAP_NAMESPACE: metadata.model_dump(mode="json")}},
        )
    except (Av27MediaError, MediaNodeError, ValidationError, OSError) as error:
        code = (
            error.code
            if isinstance(error, Av27MediaError | MediaNodeError)
            else "E_PREPARED_SOURCE_EXTERNAL"
        )
        return NodeValidatorResult(passed=False, summary={"code": code}, message=str(error))


def _require_container(path: Path, media: Av27MediaHeader, container: str) -> None:
    """检查实际 demuxer 与 ISO BMFF brand；不以改后缀伪装 MP4/MOV/MKV。"""
    if container == "mkv":
        legacy._require_formats(media, {"matroska"}, role="Source-aligned restoration")
        return
    legacy._require_formats(media, {"mov", "mp4"}, role="Source-aligned restoration")
    brand: bytes | None = None
    with path.open("rb") as stream:
        for _ in range(32):
            header = stream.read(8)
            if len(header) != 8:
                break
            size, kind = int.from_bytes(header[:4], "big"), header[4:]
            if size < 8 or size > 1048576 or stream.tell() + size - 8 > 1048576:
                break
            if kind == b"ftyp":
                brand = stream.read(4) if size >= 12 else None
                break
            stream.seek(size - 8, 1)
    if brand is None or ((container == "mov") != (brand == b"qt  ")):
        fail("CONTAINER", "实际容器 brand 与声明不一致或无法确定，禁止仅重命名扩展名")
