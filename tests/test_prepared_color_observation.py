"""短合成媒体验证真实容器/码流分层、未知继承与明确冲突，不操作真实素材。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from test_source_preparation_kernel import _tool
from test_source_preparation_kernel import media as media
from zniku.avenhance_v27.probe import Av27MediaError
from zniku.prepared_color.definitions import (
    built_in_overlap_definitions,
    definition_role,
    overlap_python_adapters,
    overlap_validators,
)
from zniku.prepared_color.node_contracts import SourceColorInterpretation
from zniku.prepared_color.signal import check_output_observation, observe_output, validate_producer
from zniku.prepared_source import definitions as old
from zniku.runtime import ValidatedOutput
from zniku.source_color.inspection import inspect_source
from zniku.source_color.policy import resolve_interpretation


def interpretation(media: Path) -> SourceColorInterpretation:
    report = inspect_source(media, str(uuid4()), target_frame_rate="30/1")
    working, basis = resolve_interpretation(report.observed_signal, "declared_only")
    return SourceColorInterpretation(
        working_signal=working,
        observed_signal=report.observed_signal,
        interpretation_policy="declared_only",
        policy_version="color-interpretation/1",
        basis=basis,
    )


def encode(tmp_path: Path, codec: str) -> Path:
    path = tmp_path / (
        "sample.mkv" if codec == "ffv1" else "sample.mov" if codec == "prores_ks" else "sample.mp4"
    )
    options = [
        "-c:v",
        codec,
        "-threads",
        "2",
        "-pix_fmt",
        "yuv422p10le" if codec == "prores_ks" else "yuv420p10le",
    ]
    if codec == "libx265":
        options += [
            "-preset",
            "ultrafast",
            "-x265-params",
            "pools=1:frame-threads=1:colorprim=bt709:transfer=bt709:colormatrix=bt709:range=limited:chromaloc=0",
            "-tag:v",
            "hvc1",
        ]
    if codec == "prores_ks":
        options += ["-profile:v", "3"]
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=320x180:r=30",
            "-frames:v",
            "3",
            "-vf",
            "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709,setsar=1/1",
            *options,
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-colorspace",
            "bt709",
            "-color_range",
            "tv",
            "-chroma_sample_location",
            "left",
            str(path),
        ]
    )
    return path


def test_exact_old_definitions_unchanged_new_are_independent() -> None:
    data = [
        definition.model_dump(mode="json")
        for definition in (
            *old.built_in_overlap_definitions(1),
            *old.built_in_overlap_definitions(3),
            old.external_definition("mp4"),
            old.external_definition("mov"),
            old.external_definition("mkv"),
        )
    ]
    assert (
        hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        == "ce6e440b80d07ecbf2463655098008950eae0b0f18a39ff10a5a0372924685e4"
    )
    adapters, validators = overlap_python_adapters(), overlap_validators()
    for definition in built_in_overlap_definitions(3):
        assert definition_role(definition) is not None
        assert definition_role(definition.model_copy(update={"version": "0.3.4"})) is None
        assert definition.validator is not None and definition.validator.adapter in validators
        if definition.executor.kind == "python":
            assert definition.executor.adapter in adapters
    assert all(definition_role(item) is None for item in old.built_in_overlap_definitions())


@pytest.mark.parametrize("codec", ["ffv1", "prores_ks", "libx265"])
def test_raw_output_observers_cover_eof(media: Path, tmp_path: Path, codec: str) -> None:
    observed = observe_output(encode(tmp_path, codec))
    assert observed.frames.frame_count == 3
    assert not observed.codec.changes and not observed.codec.unsupported
    inherited = check_output_observation(observed, interpretation(media), 3)
    assert "color_space" not in inherited
    if codec == "prores_ks":
        assert observed.codec.records == 3
        assert "chroma_location" in inherited


def test_container_cannot_mask_prores_frame_header_conflict(media: Path, tmp_path: Path) -> None:
    path = encode(tmp_path, "prores_ks")
    # 仅合成fixture：保留MOV colr709，把每帧实际matrix改成非709。
    data = bytearray(path.read_bytes())
    start = 0
    while (offset := data.find(b"icpf", start)) != -1:
        data[offset + 20] = 6
        start = offset + 4
    path.write_bytes(data)
    observed = observe_output(path)
    assert observed.container.fields[2].effective_value == "bt709"
    assert observed.codec.fields[2].effective_value == "cicp:6"
    with pytest.raises(Av27MediaError, match="COLOR_CONFLICT"):
        check_output_observation(observed, interpretation(media), 3)


def test_unknown_prores_color_kept_as_unknown(media: Path, tmp_path: Path) -> None:
    path = encode(tmp_path, "prores_ks")
    data = bytearray(path.read_bytes())
    start = 0
    while (offset := data.find(b"icpf", start)) != -1:
        data[offset + 18 : offset + 21] = bytes([2, 2, 2])
        start = offset + 4
    position = data.index(b"nclc")
    data[position + 4 : position + 10] = bytes([0, 2, 0, 2, 0, 2])
    path.write_bytes(data)
    observed = observe_output(path)
    inherited = check_output_observation(observed, interpretation(media), 3)
    assert "color_space" in inherited
    assert observed.codec.fields[2].state == "explicit_unspecified"
    assert observed.codec.fields[2].effective_value is None


def test_automatic_observation_cannot_reuse_after_file_changed(tmp_path: Path) -> None:
    path = encode(tmp_path, "prores_ks")
    observed = observe_output(path)
    stat = path.stat()
    output = ValidatedOutput(
        "video",
        "VideoFile",
        path,
        stat.st_size,
        stat.st_mtime_ns,
        {},
        {
            "output_frames": 3,
            "color_observation": observed.model_dump(mode="json"),
            "color_size": stat.st_size,
            "color_mtime_ns": stat.st_mtime_ns,
        },
    )
    validate_producer(output, 3)
    with pytest.raises(Av27MediaError, match="COLOR_PRODUCER"):
        validate_producer(replace(output, mtime_ns=stat.st_mtime_ns + 1), 3)


@pytest.mark.parametrize("codec", ["h264", "hevc"])
def test_merged_container_cannot_mask_sps_conflict(media: Path, tmp_path: Path, codec: str) -> None:
    source = media if codec == "h264" else encode(tmp_path, "libx265")
    path = tmp_path / "conflict.mp4"
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-c",
            "copy",
            "-bsf:v",
            f"{codec}_metadata=matrix_coefficients=6",
            "-colorspace",
            "bt709",
            str(path),
        ]
    )
    observed = observe_output(path)
    assert observed.container.fields[2].effective_value == "bt709"
    assert observed.codec.fields[2].effective_value == "cicp:6"
    with pytest.raises(Av27MediaError, match="COLOR_CONFLICT"):
        check_output_observation(observed, interpretation(media), observed.frames.frame_count)


def test_origin_summary_forgery_and_policy_substitution_rejected(media: Path) -> None:
    actual = interpretation(media)
    value = actual.model_dump(mode="json")
    value["observed_signal"]["resolved"]["color_space"] = None
    with pytest.raises((Av27MediaError, ValueError)):
        SourceColorInterpretation.model_validate_json(json.dumps(value))


def test_prores_middle_frame_change_not_hidden_by_initial_header(
    media: Path, tmp_path: Path
) -> None:
    path = encode(tmp_path, "prores_ks")
    data = bytearray(path.read_bytes())
    first = data.index(b"icpf")
    second = data.index(b"icpf", first + 4)
    data[second + 20] = 6
    path.write_bytes(data)
    observed = observe_output(path)
    assert observed.codec.changes == ("color_space",)
    with pytest.raises(Av27MediaError, match="COLOR_OUTPUT"):
        check_output_observation(observed, interpretation(media), 3)


def test_hevc_large_user_sei_size_does_not_hide_next_hdr_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """受控 trace stub 单独检查payload size扩展状态，不声称其为真实媒体验收。"""
    from zniku.prepared_color import hevc

    path = tmp_path / "synthetic.mp4"
    path.write_bytes(b"synthetic-parser-fixture")
    rows = ["Sequence Parameter Set"]

    def field(key: str, value: int) -> None:
        rows.append(f"0 {key} 1 = {value}")

    for key, value in {
        "vui_parameters_present_flag": 1,
        "video_signal_type_present_flag": 1,
        "chroma_loc_info_present_flag": 0,
        "field_seq_flag": 0,
        "colour_description_present_flag": 1,
        "video_full_range_flag": 0,
        "colour_primaries": 1,
        "transfer_characteristics": 1,
        "matrix_coefficients": 1,
    }.items():
        field(key, value)
    rows.append("Prefix Supplemental Enhancement Information")
    field("last_payload_type_byte", 5)
    field("ff_byte", 255)
    field("ff_byte", 255)
    field("last_payload_size_byte", 9)
    for index, value in enumerate(hevc._X265_UUID):
        field(f"uuid_iso_iec_11578[{index}]", value)
    field("last_payload_type_byte", 137)
    field("last_payload_size_byte", 24)
    rows.append("Packet: 1 bytes")

    def stream(_argv: object, *, consume: Any, **_kwargs: object) -> bool:
        for row in rows:
            consume(f"[trace_headers @ 0x123] {row}".encode())
        return False

    monkeypatch.setattr(hevc, "stream_process", stream)
    observed = hevc.observe_hevc(path)
    assert observed.unsupported == ("hevc:sei:137",)
