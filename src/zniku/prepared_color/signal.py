"""逐输出保留实际读数与继承解释；明确冲突、变化或未知 side data 一律失败关闭。

输出的 FFprobe 合并读数不是源容器/SPS 原始声明。缺失读数不会被替换成 BT.709，
只单独列出 inherited_interpretation；工作解释始终直接追溯已完成准入。
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from zniku.avenhance_v27.probe import Av27MediaHeader, probe_header
from zniku.runtime import NodeValidatorContext, ValidatedOutput
from zniku.runtime.progress import ProgressReporter
from zniku.source_color.bitstream import observe_bitstream_signal
from zniku.source_color.container import observe_container_signal
from zniku.source_color.declarations import absent_field
from zniku.source_color.frames import observe_frame_signals
from zniku.source_color.models import SIGNAL_NAMES, SignalName
from zniku.source_preparation.progress import progress_log

from .node_contracts import (
    CodecSignalObservation,
    HeaderSignal,
    OutputSignalObservation,
    OverlapMetadata,
    SourceColorInterpretation,
    _decode,
    fail,
)

_MISSING = {None, "unknown", "unspecified", "N/A", ""}


def header_signal(media: Av27MediaHeader) -> HeaderSignal:
    return HeaderSignal(**{name: getattr(media.video, name) for name in SIGNAL_NAMES})


def check_output_observation(
    observed: OutputSignalObservation,
    interpretation: SourceColorInterpretation,
    count: int,
) -> tuple[SignalName, ...]:
    """缺失 header 或帧读数均记继承；不能把另一层已知值写回缺失层。"""
    frames = observed.frames
    if frames.frame_count != count or frames.changes or frames.unsupported:
        fail("COLOR_OUTPUT", "实际全帧观察计数错误、动态变化或含不支持 side data")
    if observed.container.unsupported or observed.codec.changes or observed.codec.unsupported:
        fail("COLOR_OUTPUT", "原始容器/码流含未支持声明或全片变化")
    if observed.codec.codec == "prores" and observed.codec.records != count:
        fail("COLOR_OUTPUT", "ProRes 包头观察未完整覆盖所有帧")
    inherited: list[SignalName] = []
    for name in SIGNAL_NAMES:
        values = [getattr(observed.header, name), *(getattr(v, name) for v in frames.variants)]
        raw_fields = [
            next(field for field in layer.fields if field.field == name)
            for layer in (observed.container, observed.codec)
        ]
        if any(field.state == "unavailable" for field in raw_fields):
            fail("COLOR_OUTPUT", "原始声明不可解析，不能当作缺失")
        values.extend(field.effective_value for field in raw_fields)
        if any(
            value not in _MISSING and value != getattr(interpretation.working_signal, name)
            for value in values
        ):
            fail("COLOR_CONFLICT", f"输出 {name} 明确声明与本工程工作解释冲突")
        if all(field.effective_value is None for field in raw_fields) or any(
            value in _MISSING for value in values[: 1 + len(frames.variants)]
        ):
            inherited.append(name)
    return tuple(inherited)


def observe_output(
    path: Path, *, progress: ProgressReporter | None = None
) -> OutputSignalObservation:
    from .hevc import observe_hevc
    from .prores import observe_prores

    media = probe_header(path)
    codec_name = media.video.codec
    if codec_name == "h264":
        bitstream = observe_bitstream_signal(path, progress=progress)
        codec = CodecSignalObservation(
            codec="h264",
            scope="all-sps-sei-eof",
            fields=bitstream.signal.fields,
            records=bitstream.sps_count,
            changes=bitstream.changes,
            unsupported=bitstream.unsupported,
        )
    elif codec_name == "hevc":
        codec = observe_hevc(path, progress=progress)
    elif codec_name == "prores":
        codec = observe_prores(path, progress=progress)
    elif codec_name == "ffv1":
        codec = CodecSignalObservation(
            codec="ffv1",
            scope="ffv1-no-color-fields",
            fields=tuple(absent_field(name) for name in SIGNAL_NAMES),
            records=0,
        )
    else:
        fail("COLOR_CODEC", "此 codec 没有完整原始色彩声明检查器")
    container = observe_container_signal(
        path, sample_entry_types=frozenset({b"avc1", b"avc3", b"hvc1", b"hev1", b"apch", b"apcn"})
    )
    return OutputSignalObservation(
        header=header_signal(media),
        frames=observe_frame_signals(path, progress=progress),
        container=container,
        codec=codec,
    )


def validate_producer(output: ValidatedOutput, count: int) -> None:
    """自动全帧扫描事实绑定实际文件 stat；人工节点不可提交此事实。"""
    facts = output.producer_metadata
    if set(facts) != {"output_frames", "color_observation", "color_size", "color_mtime_ns"}:
        fail("COLOR_PRODUCER", "自动输出必须提供严格完整扫描事实")
    if type(facts["output_frames"]) is not int or facts["output_frames"] != count:
        fail("COLOR_PRODUCER", "自动输出计数错误")
    if facts["color_size"] != output.size or facts["color_mtime_ns"] != output.mtime_ns:
        fail("COLOR_PRODUCER", "扫描后输出文件变化，禁止复用过期事实")
    observed = cast(
        OutputSignalObservation, _decode(OutputSignalObservation, facts["color_observation"])
    )
    if observed.frames.frame_count != count:
        fail("COLOR_PRODUCER", "自动完整扫描未覆盖预期所有帧")


def validate_color_output(
    context: NodeValidatorContext,
    role: str,
    output: ValidatedOutput,
    metadata: OverlapMetadata,
    media: Av27MediaHeader,
) -> tuple[OverlapMetadata, tuple[str, ...]]:
    if role in {"enhancement", "fi"}:
        with progress_log(context.work_dir / "logs" / "stdout.log"):
            observed = observe_output(output.path)
    else:
        validate_producer(output, metadata.frame_count)
        observed = cast(
            OutputSignalObservation,
            _decode(OutputSignalObservation, output.producer_metadata["color_observation"]),
        )
        if observed.header != header_signal(media):
            fail("COLOR_OUTPUT_CHANGED", "自动扫描与独立输出 header 不一致")
    inherited = check_output_observation(observed, metadata.interpretation, metadata.frame_count)
    if role in {"split", "program", "final"} and any(
        getattr(observed.header, name) != getattr(metadata.interpretation.working_signal, name)
        for name in SIGNAL_NAMES
    ):
        fail("COLOR_DECLARATION", "受控重编码/成片必须实际写入全部工作色彩声明")
    expected_pixel = "yuv420p10le" if role in {"split", "program", "final"} else "yuv422p10le"
    for variant in observed.frames.variants:
        if (
            (variant.width, variant.height)
            != (
                metadata.geometry.width,
                metadata.geometry.height,
            )
            or variant.interlaced
            or variant.sample_aspect_ratio not in {"1:1", "1/1"}
            or variant.pixel_format != expected_pixel
        ):
            fail("COLOR_GEOMETRY", "完整帧观察的几何或场序不满足输出合同")
    result = OverlapMetadata.model_validate(
        {
            **metadata.model_dump(),
            "observed_signal": observed,
            "inherited_interpretation": inherited,
        }
    )
    notices = (
        ()
        if not inherited
        else ("输出部分色彩读数未确定；沿用已明确的本工程工作解释，不视为实测声明。",)
    )
    return result, notices
