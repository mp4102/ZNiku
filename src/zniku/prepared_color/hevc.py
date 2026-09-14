"""完整 HEVC SPS/SEI 色彩观察；不靠容器覆盖后的解码读数判定码流声明。

只接受已知无色彩副作用的 SEI；其他类型、参数集扩展与 VUI 语法缺失失败关闭。
语法字段依据 FFmpeg cbs_h265_syntax_template.c；缺省未写字段仍记录 absent，
此输出观察不为工作源签发新的规范推导 authority。
"""

from __future__ import annotations

import re
from pathlib import Path

from zniku.media.probe import resolve_media_tool
from zniku.runtime.progress import ProgressReporter
from zniku.source_color.declarations import absent_field, cicp_field, known_field
from zniku.source_color.models import SIGNAL_NAMES, ColorField
from zniku.source_preparation.inspection import checked_file
from zniku.source_preparation.process import stream_process
from zniku.source_preparation.progress import sample, stage

from .node_contracts import CodecSignalObservation, fail

_TRACE = re.compile(r"^\[trace_headers @ [0-9a-fA-Fx]+\]\s+(.*)$")
_VALUE = re.compile(r"^\d+\s+([a-zA-Z0-9_\[\]]+)\s+[01]+\s+=\s+([0-9]+)$")
_X265_UUID = bytes.fromhex("2ca2de09b51747dbbb55a4fe7fc2fc4e")


def _fields(data: dict[str, int]) -> tuple[ColorField, ...]:
    if "vui_parameters_present_flag" not in data:
        fail("COLOR_HEVC", "未完整观察 HEVC SPS VUI presence")
    vui = data["vui_parameters_present_flag"] == 1
    if vui and any(
        key not in data
        for key in (
            "video_signal_type_present_flag",
            "chroma_loc_info_present_flag",
            "field_seq_flag",
        )
    ):
        fail("COLOR_HEVC", "VUI 条件语法缺失")
    if (
        data.get("field_seq_flag", 0)
        or data.get("neutral_chroma_indication_flag", 0)
        or data.get("default_display_window_flag", 0)
        or data.get("sps_extension_present_flag", 0)
    ):
        fail("COLOR_HEVC", "不支持的 HEVC 场、显示窗口或扩展语义")
    video = vui and data.get("video_signal_type_present_flag") == 1
    if video and any(
        key not in data for key in ("colour_description_present_flag", "video_full_range_flag")
    ):
        fail("COLOR_HEVC", "HEVC video signal 语法缺失")
    described = video and data.get("colour_description_present_flag") == 1
    keys = ("colour_primaries", "transfer_characteristics", "matrix_coefficients")
    if described and any(key not in data for key in keys):
        fail("COLOR_HEVC", "HEVC colour description 不完整")
    fields = tuple(
        cicp_field(name, data[key] if described else None)
        for name, key in zip(SIGNAL_NAMES[:3], keys, strict=True)
    )
    range_field = (
        known_field(
            "color_range",
            data["video_full_range_flag"],
            "pc" if data["video_full_range_flag"] else "tv",
        )
        if video
        else absent_field("color_range")
    )
    chroma = absent_field("chroma_location")
    if vui and data.get("chroma_loc_info_present_flag") == 1:
        if any(
            key not in data
            for key in ("chroma_sample_loc_type_top_field", "chroma_sample_loc_type_bottom_field")
        ):
            fail("COLOR_HEVC", "HEVC chroma location 不完整")
        top, bottom = (
            data["chroma_sample_loc_type_top_field"],
            data["chroma_sample_loc_type_bottom_field"],
        )
        chroma = known_field(
            "chroma_location",
            f"{top}:{bottom}",
            "left" if top == bottom == 0 else f"hevc:{top}:{bottom}",
        )
    return (*fields, range_field, chroma)


def observe_hevc(path: Path, *, progress: ProgressReporter | None = None) -> CodecSignalObservation:
    path = checked_file(path)
    sps: dict[str, int] | None = None
    variants: list[tuple[ColorField, ...]] = []
    count = 0
    sei: int | None = None
    uuid: dict[int, int] = {}
    extension = 0
    unsupported: set[str] = set()

    def finish() -> None:
        nonlocal sps, count, sei, uuid, extension
        if sps is not None:
            value = _fields(sps)
            if value not in variants:
                if len(variants) >= 8:
                    fail("COLOR_HEVC", "SPS 变化超过预算")
                variants.append(value)
            count += 1
            sample(count)
            sps = None
        if sei == 5 and (
            set(uuid) != set(range(16)) or bytes(uuid[i] for i in range(16)) != _X265_UUID
        ):
            unsupported.add("hevc:unrecognized-user-data-sei")
        sei, uuid = None, {}
        extension = 0

    def consume(raw: bytes) -> None:
        nonlocal sps, sei, extension
        match = _TRACE.fullmatch(raw.decode("utf-8", errors="replace").strip())
        if match is None:
            return
        text = match[1]
        value = _VALUE.fullmatch(text)
        if value is not None:
            key, number = value[1], int(value[2])
            if sps is not None:
                if len(sps) >= 2048:
                    fail("COLOR_HEVC", "SPS 字段超预算")
                sps[key] = number
            if key == "ff_byte":
                extension += number
            elif key == "last_payload_type_byte":
                payload = extension + number
                finish()
                sei, extension = payload, 0
                if sei not in {0, 1, 5, 129}:
                    unsupported.add(f"hevc:sei:{sei}")
            elif key == "last_payload_size_byte":
                extension = 0
            elif key.startswith("uuid_iso_iec_11578[") and sei == 5:
                uuid[int(key.split("[")[1][:-1])] = number
            if len(unsupported) > 32:
                fail("COLOR_HEVC", "SEI 类型超过预算")
        elif text == "Sequence Parameter Set":
            finish()
            sps = {}
        elif text in {
            "Video Parameter Set",
            "Picture Parameter Set",
            "Prefix Supplemental Enhancement Information",
            "Suffix Supplemental Enhancement Information",
            "Supplemental Enhancement Information",
            "Access Unit Delimiter",
        } or text.startswith("Packet:"):
            finish()

    with stage("color_hevc_sps_sei"):
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
                "filter_units=pass_types=32|33|34|39|40,trace_headers",
                "-an",
                "-f",
                "null",
                "-",
            ],
            consume=consume,
            progress=progress,
            merge_stderr=True,
        )
    finish()
    if not variants:
        fail("COLOR_HEVC", "没有完整 SPS 观察")
    changes = tuple(
        field.field
        for index, field in enumerate(variants[0])
        if len({variant[index].model_dump_json() for variant in variants}) > 1
    )
    return CodecSignalObservation(
        codec="hevc",
        scope="all-sps-sei-eof",
        fields=variants[0],
        records=count,
        changes=changes,
        unsupported=tuple(sorted(unsupported)),
    )
