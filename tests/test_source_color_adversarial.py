"""以有限合成容器和码流反例检验声明来源、全片覆盖及预算；不读取真实媒体。"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import pytest

from test_source_color_kernel import _diagnose, _request, _runner
from test_source_preparation_kernel import _input, _tool
from test_source_preparation_kernel import media as media
from zniku.media.probe import MediaNodeError
from zniku.runtime import ManualSubmission, RunnerError
from zniku.source_color import external_repair_definition
from zniku.source_color.bitstream import _sps_signal, observe_bitstream_signal
from zniku.source_color.container import Reader, observe_container_signal
from zniku.source_color.frames import observe_frame_signals
from zniku.source_color.inspection import inspect_source
from zniku.source_color.policy import resolve_interpretation


def _box(key: bytes, value: bytes) -> bytes:
    return (len(value) + 8).to_bytes(4, "big") + key + value


def _iso(path: Path, children: bytes, entry: bytes = b"avc1") -> Path:
    sample = _box(entry, bytes(78) + children)
    stsd = _box(b"stsd", bytes(4) + (1).to_bytes(4, "big") + sample)
    media_data = _box(b"hdlr", bytes(8) + b"vide") + _box(b"minf", _box(b"stbl", stsd))
    path.write_bytes(_box(b"moov", _box(b"trak", _box(b"mdia", media_data))))
    return path


def _ebml(key: int, value: bytes) -> bytes:
    identifier = key.to_bytes((key.bit_length() + 7) // 8, "big")
    assert len(value) < 127
    return identifier + bytes((len(value) | 128,)) + value


def _matroska(path: Path, colors: bytes) -> Path:
    video = _ebml(0xE0, _ebml(0x55B0, colors))
    track = _ebml(0xAE, _ebml(0x83, b"\x01") + video)
    path.write_bytes(_ebml(0x18538067, _ebml(0x1654AE6B, track)))
    return path


@pytest.mark.parametrize("horizontal,vertical", [(2, None), (None, 99), (1, 1), (0, 3)])
def test_partial_matroska_chroma_never_hides_explicit_bad_axis(
    tmp_path: Path, horizontal: int | None, vertical: int | None
) -> None:
    fields = b"".join(
        _ebml(key, bytes((value,)))
        for key, value in ((0x55B7, horizontal), (0x55B8, vertical))
        if value is not None
    )
    signal = observe_container_signal(_matroska(tmp_path / "partial.mkv", fields))
    assert "matroska:partial-chroma-not-left" in signal.unsupported


def test_raw_zero_semantics_are_not_shared_across_formats(tmp_path: Path) -> None:
    container = observe_container_signal(_matroska(tmp_path / "zero.mkv", _ebml(0x55B9, b"\x00")))
    range_field = next(item for item in container.fields if item.field == "color_range")
    assert range_field.state == "explicit_unspecified" and range_field.effective_value is None
    sps = _sps_signal({"vui_parameters_present_flag": 0})
    sps_range = next(item for item in sps.fields if item.field == "color_range")
    assert sps_range.state == "absent" and sps_range.effective_value == "tv"
    assert sps_range.basis == "h264-default"
    with pytest.raises(MediaNodeError, match="COLOR_SPS"):
        _sps_signal({})


@pytest.mark.parametrize("box", [b"dvcC", b"dvvC", b"sv3d", b"mdcv", b"clli", b"gama", b"zzzz"])
def test_unknown_or_hdr_video_boxes_do_not_become_absent(tmp_path: Path, box: bytes) -> None:
    layer = observe_container_signal(_iso(tmp_path / "unsupported.mp4", _box(box, b"\x01")))
    assert layer.unsupported


@pytest.mark.parametrize("payload", [b"profICC", b"rICCunknown", b"unrecognized"])
def test_icc_and_unknown_colr_are_explicitly_unsupported(tmp_path: Path, payload: bytes) -> None:
    layer = observe_container_signal(_iso(tmp_path / "icc.mov", _box(b"colr", payload)))
    assert layer.unsupported == ("iso-bmff:icc-or-unknown-colr",)


def test_duplicate_or_truncated_color_does_not_become_unknown(tmp_path: Path) -> None:
    colr = _box(b"colr", b"nclx" + b"\x00\x01" * 3 + b"\x00")
    with pytest.raises(MediaNodeError, match="COLOR_CONTAINER"):
        observe_container_signal(_iso(tmp_path / "dup.mp4", colr + colr))
    with pytest.raises(MediaNodeError, match="COLOR_CONTAINER"):
        observe_container_signal(_iso(tmp_path / "bad.mp4", _box(b"colr", b"nclx")))
    with pytest.raises(MediaNodeError, match="COLOR_CONTAINER"):
        list(Reader(BytesIO(b"\x00\x00\x00\x40moov"), 8).boxes(0, 8))


def test_output_container_allowlist_does_not_expand_source_codec(tmp_path: Path) -> None:
    path = _iso(tmp_path / "prores.mov", b"", b"apch")
    with pytest.raises(MediaNodeError, match="COLOR_CONTAINER"):
        observe_container_signal(path)
    layer = observe_container_signal(path, sample_entry_types=frozenset({b"apch"}))
    assert layer.source_layer == "iso-bmff"
    assert all(item.state == "absent" for item in layer.fields)


@pytest.mark.parametrize("format", ["mp4", "mov"])
def test_real_external_formats_have_exact_native_clock_and_preserve_unknown(
    tmp_path: Path, format: Literal["mp4", "mov"]
) -> None:
    source = tmp_path / f"unknown.{format}"
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=64x48:r=30",
            "-frames:v",
            "12",
            "-c:v",
            "libx264",
            "-bf",
            "0",
            str(source),
        ]
    )
    runner = _runner(tmp_path / "attempts")
    original, diagnosis = _diagnose(runner, source)
    request = _request(
        external_repair_definition(format),
        {"target_frame_rate": "30/1"},
        (
            _input("original_media", original),
            _input("diagnosis", diagnosis),
        ),
    )
    handoff = runner.prepare_manual(request)
    Path(handoff.outputs[0].path).write_bytes(source.read_bytes())
    result = runner.submit_manual(request, handoff, ManualSubmission())
    assert len(result.artifacts) == 1


def test_hdr_sei_between_normal_gops_is_rejected(tmp_path: Path) -> None:
    clip = tmp_path / "normal.h264"
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=64x48:r=30",
            "-frames:v",
            "6",
            "-c:v",
            "libx264",
            "-bf",
            "0",
            "-x264-params",
            "colorprim=bt709:transfer=bt709:colormatrix=bt709",
            str(clip),
        ]
    )
    # 合法长度的 mastering_display_colour_volume SEI；插入中间而非文件首尾。
    sei = b"\x00\x00\x00\x01\x06\x89\x18" + bytes(range(1, 25)) + b"\x80"
    combined = tmp_path / "hdr-middle.h264"
    combined.write_bytes(clip.read_bytes() + sei + clip.read_bytes())
    path = tmp_path / "hdr-middle.mkv"
    _tool(["ffmpeg", "-v", "error", "-r", "30", "-i", str(combined), "-c", "copy", str(path)])
    observation = observe_bitstream_signal(path)
    assert 137 in observation.sei_types
    assert "h264:sei:137" in observation.unsupported
    report = inspect_source(path, str(uuid4()), target_frame_rate="30/1")
    with pytest.raises(MediaNodeError, match="COLOR_UNSUPPORTED"):
        resolve_interpretation(report.observed_signal, "operator_confirmed_bt709_limited_left")


@pytest.mark.parametrize(
    "change", [{"interlaced": True}, {"width": 66}, {"sample_aspect_ratio": "2:1"}]
)
def test_constant_wrong_frame_geometry_cannot_hide_behind_normal_header(
    media: Path, monkeypatch: pytest.MonkeyPatch, change: dict[str, Any]
) -> None:
    from zniku.source_color import inspection

    original = observe_frame_signals

    def mutated(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        return result.model_copy(
            update={"variants": tuple(frame.model_copy(update=change) for frame in result.variants)}
        )

    monkeypatch.setattr(inspection, "observe_frame_signals", mutated)
    report = inspect_source(media, str(uuid4()), target_frame_rate="30/1")
    assert any(
        item.code == "E_SOURCE_PREPARATION_VIDEO_PROFILE_UNSUPPORTED" for item in report.findings
    )


def test_interpretation_never_overrides_undeclared_audio_drop(media: Path, tmp_path: Path) -> None:
    from zniku.source_color import admission_definition

    source = tmp_path / "audio.mkv"
    adts = tmp_path / "synthetic.aac"
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=0.98",
            "-c:a",
            "aac",
            "-f",
            "adts",
            str(adts),
        ]
    )
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(media),
            "-i",
            str(adts),
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "copy",
            "-c:a",
            "copy",
            "-t",
            "1",
            str(source),
        ]
    )
    runner = _runner(tmp_path / "attempts")
    original, diagnosis = _diagnose(runner, source)
    with pytest.raises(RunnerError, match="AUDIO_POLICY"):
        runner.run_automatic(
            _request(
                admission_definition(),
                {
                    "audio_policy": "none",
                    "interpretation_policy": "operator_confirmed_bt709_limited_left",
                },
                (
                    _input("original_media", original),
                    _input("reference_media", original),
                    _input("audio_sources", original, 0),
                    _input("diagnosis", diagnosis),
                ),
            )
        )
