"""流式检查完整 H.264 SPS/SEI 声明；不将首包检查冒充全片覆盖。

FFmpeg 先保留 SPS/PPS/SEI 再 trace，避免完整 slice header 日志；仍扫描至 EOF 并可取消。
首版只接受已识别的无色彩副作用 SEI，其他类型保留为不支持，不能默默忽略 HDR 或重映射。
"""

from __future__ import annotations

import re
from fractions import Fraction
from pathlib import Path
from typing import cast

from zniku.media.probe import resolve_media_tool
from zniku.runtime.progress import ProgressReporter
from zniku.source_preparation.inspection import checked_file
from zniku.source_preparation.models import rational
from zniku.source_preparation.process import fail, stream_process
from zniku.source_preparation.progress import sample, stage

from .declarations import cicp_field, known_field
from .models import BitstreamSummary, ColorField, SignalLayer, SignalName

_TRACE = re.compile(r"^\[trace_headers @ [0-9a-fA-Fx]+\]\s+(.*)$")
_VALUE = re.compile(r"^\d+\s+([a-zA-Z0-9_\[\]]+)\s+[01]+\s+=\s+([0-9]+)$")
_X264_UUID = bytes.fromhex("dc45e9bde6d948b7962cd820d923eeef")


def _sps_signal(data: dict[str, int]) -> SignalLayer:
    if "vui_parameters_present_flag" not in data:
        raise fail("COLOR_SPS", "SPS 缺少可验证 VUI presence 条件")
    vui = data["vui_parameters_present_flag"] == 1
    signal_present = vui and data.get("video_signal_type_present_flag") == 1
    described = signal_present and data.get("colour_description_present_flag") == 1
    required: tuple[str, ...] = (
        ("video_full_range_flag", "colour_description_present_flag") if signal_present else ()
    )
    required += (
        ("colour_primaries", "transfer_characteristics", "matrix_coefficients") if described else ()
    )
    if any(key not in data for key in required) or (
        vui
        and (
            "video_signal_type_present_flag" not in data
            or "chroma_loc_info_present_flag" not in data
        )
    ):
        raise fail("COLOR_SPS", "SPS 色彩语法未完整观察")
    color = tuple(
        cicp_field(
            cast(SignalName, name), data.get(key) if described else None, h264_default=not described
        )
        for name, key in (
            ("color_primaries", "colour_primaries"),
            ("color_transfer", "transfer_characteristics"),
            ("color_space", "matrix_coefficients"),
        )
    )
    range_field = (
        known_field(
            "color_range",
            data["video_full_range_flag"],
            "pc" if data["video_full_range_flag"] else "tv",
        )
        if signal_present
        else ColorField(
            field="color_range", state="absent", effective_value="tv", basis="h264-default"
        )
    )
    chroma_present = vui and data.get("chroma_loc_info_present_flag") == 1
    if chroma_present:
        if (
            "chroma_sample_loc_type_top_field" not in data
            or "chroma_sample_loc_type_bottom_field" not in data
        ):
            raise fail("COLOR_SPS", "chroma location 条件存在但语法值不可读")
        top, bottom = (
            data["chroma_sample_loc_type_top_field"],
            data["chroma_sample_loc_type_bottom_field"],
        )
        chroma = known_field(
            "chroma_location",
            f"{top}:{bottom}",
            "left" if top == bottom == 0 else f"h264:{top}:{bottom}",
        )
    else:
        chroma = ColorField(
            field="chroma_location", state="absent", effective_value="left", basis="h264-default"
        )
    return SignalLayer(
        source_layer="h264-sps", scope="all-sps", fields=(*color, range_field, chroma)
    )


def observe_bitstream_signal(
    path: Path, *, progress: ProgressReporter | None = None
) -> BitstreamSummary:
    """检查全部相关参数集和 SEI；只保留有限不同值，不积累全片 trace 文本。"""
    path = checked_file(path)
    sps: dict[str, int] | None = None
    signals: list[SignalLayer] = []
    sps_count = sei_count = 0
    sei_types: set[int] = set()
    clocks: set[str] = set()
    unsupported: set[str] = set()
    current_sei: int | None = None
    uuid: dict[int, int] = {}
    extension = 0

    def finish_sps() -> None:
        nonlocal sps, sps_count
        if sps is None:
            return
        observation = _sps_signal(sps)
        if observation not in signals:
            if len(signals) >= 8:
                raise fail("COLOR_BUDGET", "全片不同 SPS 色彩声明超过预算")
            signals.append(observation)
        if sps.get("fixed_frame_rate_flag") == 1:
            tick, scale = sps.get("num_units_in_tick", 0), sps.get("time_scale", 0)
            if tick <= 0 or scale <= 0 or sps.get("frame_mbs_only_flag") != 1:
                unsupported.add("h264:invalid-fixed-clock")
            else:
                clocks.add(rational(Fraction(scale, 2 * tick)))
        if len(clocks) > 8:
            raise fail("COLOR_BUDGET", "全片 SPS 时钟声明超过预算")
        sps_count += 1
        sample(sps_count)
        sps = None

    def finish_sei() -> None:
        nonlocal current_sei, uuid
        if current_sei == 5 and (
            set(uuid) != set(range(16)) or bytes(uuid[i] for i in range(16)) != _X264_UUID
        ):
            unsupported.add("h264:unrecognized-user-data-sei")
        current_sei, uuid = None, {}

    def consume(raw: bytes) -> None:
        nonlocal sps, sei_count, current_sei, extension
        match = _TRACE.fullmatch(raw.decode("utf-8", errors="replace").strip())
        if match is None:
            return
        text = match[1]
        value = _VALUE.fullmatch(text)
        if value:
            key, integer = value[1], int(value[2])
            if sps is not None:
                if len(sps) > 256:
                    raise fail("COLOR_BUDGET", "SPS 语法字段数量超过预算")
                sps[key] = integer
            if key == "nal_unit_type" and integer not in {6, 7, 8}:
                unsupported.add(f"h264:unsupported-nal:{integer}")
            if key == "ff_byte":
                extension += integer
            elif key == "last_payload_type_byte":
                finish_sei()
                current_sei, extension = extension + integer, 0
                sei_count += 1
                sei_types.add(current_sei)
                if current_sei not in {0, 1, 5, 6}:
                    unsupported.add(f"h264:sei:{current_sei}")
            elif key == "last_payload_size_byte":
                # 同名 ff_byte 也扩展 payload_size；大小扩展不能污染下一条 payload_type。
                extension = 0
            elif key.startswith("uuid_iso_iec_11578[") and current_sei == 5:
                uuid[int(key.split("[")[1][:-1])] = integer
            if len(unsupported) > 32 or len(sei_types) > 32:
                raise fail("COLOR_BUDGET", "全片 SEI 类型超过预算")
        elif text == "Sequence Parameter Set":
            finish_sps()
            finish_sei()
            sps = {}
        elif text in {
            "Picture Parameter Set",
            "Supplemental Enhancement Information",
            "Access Unit Delimiter",
        } or text.startswith("Packet:"):
            finish_sps()
            finish_sei()

    with stage("color_bitstream"):
        stream_process(
            [
                resolve_media_tool("ffmpeg"),
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "info",
                "-protocol_whitelist",
                "file",
                "-format_whitelist",
                "mov,matroska",
                "-f",
                "matroska" if path.suffix.lower() == ".mkv" else "mov",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-c:v",
                "copy",
                "-bsf:v",
                "filter_units=remove_types=1|2|3|4|5|9|10|11|12,trace_headers",
                "-an",
                "-f",
                "null",
                "-",
            ],
            consume=consume,
            progress=progress,
            merge_stderr=True,
        )
    finish_sps()
    finish_sei()
    if not signals:
        raise fail("COLOR_SPS", "未完整观察到任何有效 SPS")
    changes = tuple(
        name
        for name in (f.field for f in signals[0].fields)
        if len(
            {
                next(f for f in signal.fields if f.field == name).model_dump_json()
                for signal in signals
            }
        )
        > 1
    )
    return BitstreamSummary(
        signal=signals[0],
        sps_count=sps_count,
        sei_messages=sei_count,
        sei_types=tuple(sorted(sei_types)),
        clock_rates=tuple(sorted(clocks)),
        changes=changes,
        unsupported=tuple(sorted(unsupported)),
    )
