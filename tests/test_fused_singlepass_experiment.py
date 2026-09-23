"""单轮短媒体时基实验，仅记录是否等价；未据此放宽正式双遍裁后导出。"""

from __future__ import annotations

import importlib
import json
import shutil
from fractions import Fraction
from pathlib import Path

import pytest

from test_overlap_media_io import _context
from zniku.chapter_overlap.media_io import clock_filter, copy_prores_range

_remux = importlib.import_module("tools.check_overlap_remux")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要FFmpeg")
@pytest.mark.parametrize("text", ("24", "30", "24000/1001", "30000/1001"))
def test_singlepass_timebase_experiment_keeps_safe_export(tmp_path: Path, text: str) -> None:
    rate = Fraction(text) * 2
    experiment = _remux.RemuxExperiment(tmp_path / "experiment")
    source = experiment.generate("raw.mov", rate, 16, offset=0, step=1)
    oracle = experiment.inspect(source, rate, 16)
    alternate = experiment.output / "alternate.mov"
    experiment.run(
        experiment.ffmpeg,
        [
            "-v",
            "error",
            "-nostdin",
            "-n",
            "-i",
            str(source),
            "-c:v",
            "copy",
            "-video_track_timescale",
            "12000000",
            str(alternate),
        ],
        role="alternate-clock",
    )
    direct = experiment.output / "single.mov"
    experiment.run(
        experiment.ffmpeg,
        [
            "-v",
            "error",
            "-nostdin",
            "-n",
            "-i",
            str(alternate),
            "-c:v",
            "copy",
            "-bsf:v",
            "noise=amount=0:drop='lt(n,2)+gte(n,14)'," + clock_filter(rate),
            "-video_track_timescale",
            str(rate.numerator),
            str(direct),
        ],
        role="single-pass-experiment",
    )
    try:
        actual = experiment.inspect(direct, rate, 12)
        _remux.require_match(
            actual, _remux.Fingerprints(oracle.packets[2:14], oracle.decoded[2:14])
        )
        singlepass_exact = True
    except AssertionError:
        singlepass_exact = False
    context = _context(experiment.output / "attempt")
    safe = context.work_dir / "safe.mov"
    assert copy_prores_range(context, alternate, safe, 2, 14, rate) == 12
    inspected = experiment.output / "safe-oracle.mov"
    shutil.copyfile(safe, inspected)
    safe_probe = experiment.inspect(inspected, rate, 12)
    _remux.require_match(
        safe_probe, _remux.Fingerprints(oracle.packets[2:14], oracle.decoded[2:14])
    )
    report = {
        "rate": str(rate),
        "singlepass_exact": singlepass_exact,
        "production_export": "safe-two-pass",
    }
    (experiment.output / "singlepass-report.json").write_text(json.dumps(report), encoding="utf-8")
    print(json.dumps(report))
