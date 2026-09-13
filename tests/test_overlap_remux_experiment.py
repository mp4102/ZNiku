"""以短合成 ProRes 验证 copy context/crop 与时序 oracle，不代表真实 Aion 通过。

只使用测试新目录，保留所有原件、合成 raw 与错裁反例；不注册节点、不读取用户视频。
"""

from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path

import pytest

_remux = importlib.import_module("tools.check_overlap_remux")
TOOLS_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.mark.skipif(not TOOLS_AVAILABLE, reason="短合成 ProRes 实验需要 FFmpeg/FFprobe")
@pytest.mark.parametrize("frame_rate", ("30/1", "30000/1001"))
@pytest.mark.parametrize("source_frames", (18, 31))
def test_context_and_crop_preserve_packet_pixels_and_exact_timeline(
    tmp_path: Path, frame_rate: str, source_frames: int
) -> None:
    """逐帧保真、顺序、时间轴与错裁反例共同成立；灰阶 oracle 不是外部 FI。"""

    output = tmp_path / "合成 ProRes"
    report = _remux.run_experiment(output, source_frames=source_frames, frame_rate=frame_rate)

    assert report["pixel_and_packet_exact"]
    assert report["exact_fps_pts_dts_duration"]
    assert report["wrong_equal_length_crop_rejected"]
    assert report["external_AI_verified"] is False
    assert report["production_nodes_implemented"] is False
    assert report["cropped_sequence_frames"] == 2 * source_frames - 1
    assert report["chapter_ranges"][0]["chapter"] == [0, 1]
    assert report["chapter_ranges"][1]["context_piece_count"] == 3
    assert len(set(report["source_oracle"]["decoded"])) == source_frames
    assert all(size > 0 for size in report["files_bytes"].values())
    assert (output / "source.mov").is_file()
    assert (output / "wrong-equal-length-crop.mov").is_file()
    assert len(tuple(output.glob("synthetic-fi-raw-*.mov"))) == 3
    persisted = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert persisted["chapter_ranges"] == report["chapter_ranges"]
    for command in report["commands"]:
        argv = command["argv"]
        assert len(argv) <= 64
        assert command["seconds"] >= 0
        if command["role"].startswith("stream-copy"):
            assert argv[argv.index("-c:v") + 1] == "copy"
            assert "-vf" not in argv and "-filter_complex" not in argv
            assert "-ss" not in argv
            assert "-n" in argv
            assert argv.count("-i") == 1


def test_same_count_wrong_order_fails_both_oracles() -> None:
    expected = _remux.Fingerprints(("packet-0", "packet-1"), ("frame-0", "frame-1"))
    with pytest.raises(AssertionError, match="E_REMUX_CONTENT"):
        _remux.require_match(
            _remux.Fingerprints(("packet-1", "packet-0"), ("frame-1", "frame-0")), expected
        )


@pytest.mark.parametrize(("source_frames", "frame_rate"), ((32, "30/1"), (18, "29.97")))
def test_experiment_budget_rejection_has_no_files(
    tmp_path: Path, source_frames: int, frame_rate: str
) -> None:
    output = tmp_path / "not-created"
    with pytest.raises(ValueError, match="E_REMUX_EXPERIMENT_BUDGET"):
        _remux.run_experiment(output, source_frames=source_frames, frame_rate=frame_rate)
    assert not output.exists()


def test_existing_output_never_overwritten(tmp_path: Path) -> None:
    with pytest.raises(FileExistsError, match="E_REMUX_OUTPUT_EXISTS"):
        _remux.RemuxExperiment(tmp_path)
    assert not any(tmp_path.iterdir())


@pytest.mark.skipif(not TOOLS_AVAILABLE, reason="短合成 ProRes 实验需要 FFmpeg/FFprobe")
@pytest.mark.parametrize("name", ("../escape.mov", "escape\\outside.mov", "C:/escape.mov"))
def test_experiment_target_cannot_escape(tmp_path: Path, name: str) -> None:
    experiment = _remux.RemuxExperiment(tmp_path / "isolated")
    with pytest.raises(ValueError, match="E_REMUX_TARGET"):
        experiment.target(name)
    assert not any(experiment.output.iterdir())


@pytest.mark.skipif(not TOOLS_AVAILABLE, reason="短合成 ProRes 实验需要 FFmpeg/FFprobe")
def test_experiment_refuses_input_outside_output_directory(tmp_path: Path) -> None:
    outside = tmp_path / "not-media.mov"
    outside.write_bytes(b"synthetic-not-media")
    experiment = _remux.RemuxExperiment(tmp_path / "isolated")
    with pytest.raises(ValueError, match="E_REMUX_SOURCE"):
        experiment.source(outside)
    assert outside.read_bytes() == b"synthetic-not-media"
    assert experiment.commands == []
