"""短合成逐帧 oracle：融合选帧不依赖外部时基，不读用户媒体或伪造 Aion 验收。"""

from __future__ import annotations

import importlib
import shutil
from fractions import Fraction
from pathlib import Path

import pytest

from test_overlap_media_io import _context
from zniku.avenhance_v27.probe import probe_header
from zniku.chapter_batch.fused_media import (
    RawRange,
    encode_fused,
    selected_filter,
    selection_expression,
)
from zniku.chapter_overlap.media_io import OverlapMediaError, write_ffconcat

_remux = importlib.import_module("tools.check_overlap_remux")
TOOLS = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.mark.skipif(not TOOLS, reason="需要 FFmpeg/FFprobe")
@pytest.mark.parametrize("rate_text", ("24/1", "30/1", "24000/1001", "30000/1001"))
@pytest.mark.parametrize(
    "variant", ("ordinary", "mixed-timescale", "fragmented", "nonzero-pts", "equivalent-decimal")
)
def test_fused_selected_pixels_and_exact_pts(tmp_path: Path, rate_text: str, variant: str) -> None:
    """跨章缺帧/重复即失败；允许不同 raw 容器时钟但不按时长猜帧。"""
    experiment = _remux.RemuxExperiment(tmp_path / "oracle")
    rate = Fraction(rate_text) * 2
    raw_rate = (
        {Fraction(48000, 1001): Fraction("47.952"), Fraction(60000, 1001): Fraction("59.94")}.get(
            rate, rate
        )
        if variant == "equivalent-decimal"
        else rate
    )
    left = experiment.generate("left.mov", raw_rate, 15, offset=0, step=1)
    right = experiment.generate("right.mov", raw_rate, 15, offset=12, step=1)
    left_hash = experiment.inspect(left, raw_rate, 15).decoded
    right_hash = experiment.inspect(right, raw_rate, 15).decoded
    if variant not in {"ordinary", "equivalent-decimal"}:
        altered = experiment.output / "altered.mov"
        args = ["-hide_banner", "-loglevel", "error", "-nostdin", "-n"]
        if variant == "nonzero-pts":
            args += ["-itsoffset", "5"]
        args += ["-i", str(right), "-map", "0:v:0", "-c:v", "copy"]
        if variant == "mixed-timescale":
            args += ["-video_track_timescale", "12000000"]
        if variant == "fragmented":
            args += ["-movflags", "+frag_keyframe+empty_moov", "-video_track_timescale", "1000000"]
        args += [str(altered)]
        experiment.run(experiment.ffmpeg, args, role="synthetic-container-variant")
        right = altered
    parts = (RawRange(left, 15, 0, 12), RawRange(right, 15, 2, 15))
    context = _context(experiment.output / "attempt")
    manifest = write_ffconcat(context, (left, right), context.work_dir / "input.ffconcat")
    data = experiment.run(
        experiment.ffmpeg,
        [
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-copyts",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(manifest),
            "-an",
            "-vf",
            selected_filter(parts, rate),
            "-fps_mode:v",
            "passthrough",
            "-enc_time_base:v",
            f"1/{rate.numerator}",
            "-f",
            "framehash",
            "-hash",
            "sha256",
            "-",
        ],
        role="fused-selection-oracle",
    ).decode()
    rows = [line.split(",") for line in data.splitlines() if line and not line.startswith("#")]
    actual = tuple(row[-1].strip() for row in rows)
    assert actual == left_hash[:12] + right_hash[2:] + right_hash[-1:]
    assert [int(row[2]) for row in rows] == [i * rate.denominator for i in range(26)]
    if variant == "ordinary":
        target = context.work_dir / "program.mp4"
        assert encode_fused(context, parts, target, rate, 26, "cpu") == 26
        header = probe_header(target).video
        assert header.frame_count == 26
        assert header.frame_rate == rate
        assert header.time_base == Fraction(1, rate.numerator)
        assert not list(context.work_dir.rglob("*.mov"))


def test_thousand_ranges_have_bounded_expression_depth_and_strict_coordinates() -> None:
    parts = tuple(RawRange(Path(f"{i}.mov"), 5, 1, 3) for i in range(1000))
    expression = selection_expression(parts)
    assert len(expression) < 70000
    assert expression.count("between") == 1000
    assert "between(n,4996,4997)" in expression
    with pytest.raises(OverlapMediaError):
        RawRange(Path("x.mov"), 5, True, 3)
    with pytest.raises(OverlapMediaError):
        selection_expression(())


@pytest.mark.skipif(not TOOLS, reason="需要 FFmpeg/FFprobe")
def test_thousand_inputs_execute_with_one_sequential_demuxer(tmp_path: Path) -> None:
    """实际解析千段平衡表达式；一个已知短片重复只用于压力测试，不冒充正式绑定。"""
    experiment = _remux.RemuxExperiment(tmp_path / "scale")
    rate = Fraction(60)
    source = experiment.generate("three.mov", rate, 3, offset=0, step=1)
    expected = experiment.inspect(source, rate, 3).decoded[1]
    parts = tuple(RawRange(source, 3, 1, 2) for _ in range(1000))
    context = _context(experiment.output / "attempt")
    manifest = write_ffconcat(
        context, tuple(p.path for p in parts), context.work_dir / "scale.ffconcat"
    )
    script = context.work_dir / "scale.filter"
    script.write_text(selected_filter(parts, rate), encoding="utf-8")
    output = experiment.run(
        experiment.ffmpeg,
        [
            "-v",
            "error",
            "-nostdin",
            "-copyts",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(manifest),
            "-filter_script:v",
            str(script),
            "-fps_mode:v",
            "passthrough",
            "-f",
            "framehash",
            "-hash",
            "sha256",
            "-",
        ],
        role="thousand-sequential-inputs",
    ).decode()
    rows = [line.split(",") for line in output.splitlines() if line and not line.startswith("#")]
    assert len(rows) == 1001
    assert all(row[-1].strip() == expected for row in rows)
    assert [int(row[2]) for row in rows] == list(range(1001))
