"""用本地短合成媒体检查新 helper；不伪造真实外部 Aion 验收或 Graph 完成。

覆盖 packetcopy 内容/时序、单输入千章 Program、取消时 producer 回收和磁盘前检。
所有测试文件都留在 pytest 独立目录，源与 raw 不删除、不覆盖。
"""

from __future__ import annotations

import importlib
import shutil
import subprocess
from dataclasses import replace
from fractions import Fraction
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

from zniku.avenhance_v27.probe import probe_header
from zniku.chapter_overlap import media_io
from zniku.graph import NodeInstance
from zniku.media import source_media_definition
from zniku.runtime import ProgressError, PythonAdapterContext
from zniku.runtime.progress import ProgressUnit
from zniku.runtime.runner import RunnerProcessCleanupError

_remux = importlib.import_module("tools.check_overlap_remux")
TOOLS = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


class Samples:
    """只记录真实测量值，模拟 reporter 对已取消 attempt 的拒绝。"""

    def __init__(self, *, cancel_after: int | None = None) -> None:
        self.values: list[tuple[float, int | None, int | None]] = []
        self.cancel_after = cancel_after

    def report(
        self,
        fraction: float,
        *,
        current: int | None = None,
        total: int | None = None,
        unit: ProgressUnit | None = None,
        stage: str | None = None,
    ) -> None:
        if self.cancel_after is not None and len(self.values) >= self.cancel_after:
            raise ProgressError("E_PROGRESS_TERMINAL", "测试取消")
        assert unit == "frames"
        assert not self.values or fraction >= self.values[-1][0]
        self.values.append((fraction, current, total))


def _context(path: Path, reporter: Samples | None = None) -> PythonAdapterContext:
    path.mkdir(parents=True, exist_ok=True)
    definition = source_media_definition()
    return PythonAdapterContext(
        node_run_id="synthetic-helper-only",
        attempt=1,
        definition=definition,
        node=NodeInstance(
            node_id="source", type_id=definition.type_id, definition_version=definition.version
        ),
        work_dir=path,
        inputs=(),
        outputs=(),
        stdout_log_path=path / "stdout.log",
        stderr_log_path=path / "stderr.log",
        progress=reporter,
    )


@pytest.mark.skipif(not TOOLS, reason="需要 FFmpeg/FFprobe")
@pytest.mark.parametrize("rate_text", ("30/1", "30000/1001"))
def test_controlled_copy_and_concat_preserve_packet_and_decoded_order(
    tmp_path: Path,
    rate_text: str,
) -> None:
    experiment = _remux.RemuxExperiment(tmp_path / "synthetic")
    rate = Fraction(rate_text)
    source = experiment.generate("source.mov", rate, 18, offset=0, step=2)
    before = experiment.inspect(source, rate, 18)
    samples = Samples()
    context = _context(experiment.output, samples)
    parts = (experiment.output / "left.mov", experiment.output / "right.mov")
    media_io.copy_prores_range(context, source, parts[0], 1, 3, rate, progress_total=12)
    media_io.copy_prores_range(
        context,
        source,
        parts[1],
        3,
        7,
        rate,
        progress_total=12,
        progress_offset=2,
    )
    target = experiment.output / "joined.mov"
    media_io.concat_prores(context, parts, target, rate, 6, progress_total=12, progress_offset=6)
    _remux.require_match(
        experiment.inspect(target, rate, 6), _remux.slice_fingerprints(before, 1, 7)
    )
    _remux.require_match(experiment.inspect(source, rate, 18), before)
    assert samples.values[-1] == (1.0, 12, 12)
    assert all(path.is_file() for path in parts)


@pytest.mark.skipif(not TOOLS, reason="需要 FFmpeg/FFprobe")
def test_thousand_short_chapter_program_uses_one_input_and_one_global_tail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """仅媒体 helper 容量：不是全图调度、画布或真实 Aion 通过声明。"""

    experiment = _remux.RemuxExperiment(tmp_path / "thousand")
    rate = Fraction(60)
    two = experiment.generate("two.mov", rate, 2, offset=0, step=1)
    one = experiment.generate("one.mov", rate, 1, offset=4, step=1)
    paths: list[Path] = []
    for ordinal in range(1000):
        target = experiment.output / f"chapter-{ordinal:04d}.mov"
        shutil.copyfile(one if ordinal == 999 else two, target)
        paths.append(target)
    real_run = media_io.run_ffmpeg
    calls: list[list[str]] = []

    def recording(context: PythonAdapterContext, argv: list[str], **kwargs: Any) -> int:
        calls.append(argv)
        return real_run(context, argv, **kwargs)

    monkeypatch.setattr(media_io, "run_ffmpeg", recording)
    context = _context(experiment.output, Samples())
    target = experiment.output / "program.mp4"
    assert media_io.encode_program(context, paths, target, rate, 2000, "cpu") == 2000
    video = probe_header(target).video
    assert video.frame_count == 2000
    assert video.avg_frame_rate == rate
    assert video.codec == "hevc"
    assert len(calls) == 1 and calls[0].count("-i") == 1
    assert len(calls[0]) < 100
    assert "tpad=stop_mode=clone:stop=1" in calls[0][calls[0].index("-vf") + 1]
    assert (target.with_suffix(".ffconcat")).read_text(encoding="utf-8").count("file '") == 1000


def test_capacity_failure_preserves_inputs_and_has_no_media_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    usage = shutil.disk_usage(tmp_path)
    monkeypatch.setattr(shutil, "disk_usage", lambda _: usage._replace(free=0))
    with pytest.raises(media_io.OverlapMediaError, match="E_OVERLAP_CAPACITY"):
        media_io.capacity_check(context, output_bytes=4096, staging_bytes=4096, input_bytes=8192)
    assert list(tmp_path.glob("*.mov")) == []
    assert '"estimated_additional_bytes": 8192' in context.stdout_log_path.read_text()


def test_output_rejects_existing_and_escape_before_write(tmp_path: Path) -> None:
    context = _context(tmp_path / "attempt")
    existing = context.work_dir / "keep.mov"
    existing.write_bytes(b"unchanged")
    for target in (existing, tmp_path / "outside.mov"):
        with pytest.raises(media_io.OverlapMediaError, match="E_OVERLAP_OUTPUT_PATH"):
            media_io.output_path(context, target)
    assert existing.read_bytes() == b"unchanged"


@pytest.mark.skipif(not TOOLS, reason="需要 FFmpeg/FFprobe")
def test_no_output_heartbeat_cancel_reaps_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """即使 producer 尚未给出可用 frame，也通过 reporter 的拒绝终止进程。"""

    context = _context(tmp_path, Samples(cancel_after=1))
    processes: list[subprocess.Popen[bytes]] = []
    real_popen = subprocess.Popen

    def recording(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", recording)
    with pytest.raises(ProgressError, match="E_PROGRESS_TERMINAL"):
        media_io.run_ffmpeg(
            context,
            [
                "-re",
                "-f",
                "lavfi",
                "-i",
                "color=size=64x48:rate=1",
                "-frames:v",
                "60",
                "-f",
                "null",
                "-",
            ],
            expected_frames=60,
        )
    assert len(processes) == 1 and processes[0].poll() is not None


def test_pipe_close_error_cannot_hide_unconfirmed_producer_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """读端关闭失败不能替换停机标记，否则 Runtime 会把仍可能活动的 producer 当作失败收口。"""

    class FaultyPipe(BytesIO):
        def close(self) -> None:
            super().close()
            raise OSError("synthetic pipe close failure")

    class UnconfirmedProcess:
        stdout = FaultyPipe(b"frame=invalid\n")

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

        def wait(self, *, timeout: float) -> int:
            raise subprocess.TimeoutExpired("synthetic", timeout)

    process = UnconfirmedProcess()
    monkeypatch.setattr(media_io, "resolve_media_tool", lambda _name: "synthetic")
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    with pytest.raises(RunnerProcessCleanupError, match="E_AV27_FFMPEG_CLEANUP"):
        media_io.run_ffmpeg(_context(tmp_path), ["-f", "null", "-"], expected_frames=3)
    assert process.stdout.closed


@pytest.mark.skipif(not TOOLS, reason="需要 FFmpeg/FFprobe")
def test_wrong_actual_frame_count_never_reports_success(tmp_path: Path) -> None:
    context = replace(_context(tmp_path), progress=None)
    with pytest.raises(media_io.OverlapMediaError, match="E_OVERLAP_FRAME_COUNT"):
        media_io.run_ffmpeg(
            context,
            ["-f", "lavfi", "-i", "color=size=64x48:rate=30", "-frames:v", "2", "-f", "null", "-"],
            expected_frames=3,
        )


def test_copy_packet_summary_reads_only_current_video_output(tmp_path: Path) -> None:
    """独立于FFmpeg版本验证日志解析，不以音频、输入包数或旧命令记录冒充本次输出。"""

    log = tmp_path / "stderr.log"
    previous = (
        b"[out#0/null @ previous]   Output stream #0:0 (video): 99 packets muxed (1 bytes);\n"
    )
    current = (
        b"[out#0/matroska @ current]   Output stream #0:0 (video): 7 packets muxed (1 bytes);\n"
        b"[out#0/matroska @ current]   Output stream #0:1 (audio): 15 packets muxed (1 bytes);\n"
        b"[in#0/matroska @ current]   Input stream #0:0 (video): 12 packets read (1 bytes);\n"
    )
    log.write_bytes(previous + current)
    assert media_io._muxed_video_packet_count(log, len(previous)) == 7


@pytest.mark.parametrize("content", (b"", b"frame=7\n", b"unexpected mux summary\n"))
def test_copy_missing_packet_summary_cannot_use_previous_or_expected_count(
    tmp_path: Path, content: bytes
) -> None:
    log = tmp_path / "stderr.log"
    old = b"[out#0/null @ old]   Output stream #0:0 (video): 7 packets muxed (1 bytes);\n"
    log.write_bytes(old + content)
    with pytest.raises(media_io.OverlapMediaError, match="E_OVERLAP_FRAME_COUNT"):
        media_io._muxed_video_packet_count(log, len(old))
    log.write_bytes(old + old)
    with pytest.raises(media_io.OverlapMediaError, match="E_OVERLAP_FRAME_COUNT"):
        media_io._muxed_video_packet_count(log, 0)


@pytest.mark.skipif(not TOOLS, reason="需要 FFmpeg/FFprobe")
def test_copy_actual_packet_count_mismatch_is_not_filled_from_plan(tmp_path: Path) -> None:
    """复制统计缺少progress.frame的旧FFmpeg也必须拒绝真实2帧与预期3帧的差异。"""

    experiment = _remux.RemuxExperiment(tmp_path / "wrong-copy-count")
    source = experiment.generate("source.mov", Fraction(30), 2, offset=0, step=1)
    original = source.read_bytes()
    with pytest.raises(media_io.OverlapMediaError, match="E_OVERLAP_FRAME_COUNT"):
        media_io.run_ffmpeg(
            _context(experiment.output),
            ["-i", str(source), "-map", "0:v:0", "-c:v", "copy", "-f", "null", "-"],
            expected_frames=3,
        )
    assert source.read_bytes() == original


@pytest.mark.skipif(not TOOLS, reason="需要 FFmpeg/FFprobe")
def test_accepted_decimal_fragmented_raw_crops_to_exact_clock(tmp_path: Path) -> None:
    """外部 59.94 fragmented MOV 无 nb_frames，已验收N允许裁边但不能改源或容忍错帧。"""
    experiment = _remux.RemuxExperiment(tmp_path / "fragmented-raw")
    raw_rate, exact_rate = Fraction(2997, 50), Fraction(60000, 1001)
    original = experiment.generate("generated.mov", raw_rate, 7, offset=0, step=1)
    raw = experiment.output / "raw.mov"
    experiment.run(
        experiment.ffmpeg,
        [
            "-hide_banner",
            "-nostdin",
            "-n",
            "-loglevel",
            "error",
            "-i",
            str(original),
            "-map",
            "0:v:0",
            "-c:v",
            "copy",
            "-movflags",
            "frag_keyframe+empty_moov",
            str(raw),
        ],
        role="fragment-synthetic-raw",
    )
    assert probe_header(raw).video.frame_count is None
    fingerprints = experiment.inspect(original, raw_rate, 7)
    raw_bytes = raw.read_bytes()
    context = _context(experiment.output)
    target = experiment.output / "crop.mov"
    assert (
        media_io.copy_prores_range(
            context,
            raw,
            target,
            1,
            5,
            exact_rate,
            source_frame_count=7,
            allow_equivalent_rate=True,
        )
        == 4
    )
    _remux.require_match(
        experiment.inspect(target, exact_rate, 4),
        _remux.slice_fingerprints(fingerprints, 1, 5),
    )
    assert raw.read_bytes() == raw_bytes
    with pytest.raises(media_io.OverlapMediaError, match="E_OVERLAP_COPY_FPS"):
        media_io.copy_prores_range(
            context,
            raw,
            experiment.output / "wrong-rate.mov",
            1,
            5,
            Fraction(60),
            source_frame_count=7,
            allow_equivalent_rate=True,
        )


@pytest.mark.skipif(not TOOLS, reason="需要 FFmpeg/FFprobe")
def test_merge_normalizes_mixed_external_timescales_without_reencoding(tmp_path: Path) -> None:
    """帧率相同但timescale不同的外部叶先复制规范化，最终包/像素/PTS仍严格保真。"""
    experiment = _remux.RemuxExperiment(tmp_path / "mixed-timescales")
    rate = Fraction(30)
    original = experiment.generate("original.mov", rate, 7, offset=0, step=2)
    expected = experiment.inspect(original, rate, 7)
    first = experiment.copy_range(original, "first.mov", 0, 2, rate)
    second = experiment.copy_range(original, "second.mov", 2, 7, rate)
    external = experiment.output / "external.mov"
    experiment.run(
        experiment.ffmpeg,
        [
            "-hide_banner",
            "-nostdin",
            "-n",
            "-loglevel",
            "error",
            "-i",
            str(second),
            "-map",
            "0:v:0",
            "-c:v",
            "copy",
            "-video_track_timescale",
            # FFprobe 6.1 对极低120 timescale的短fragmented夹具会猜成r=60、avg=30。
            # 使用常见MOV timescale构造合法外部输入，仍覆盖与目标30不同的时基及无nb_frames。
            "15360",
            "-movflags",
            "frag_keyframe+empty_moov",
            str(external),
        ],
        role="synthetic-mixed-timescale",
    )
    assert probe_header(external).video.frame_count is None
    assert probe_header(external).video.time_base == Fraction(1, 15360)
    raw_bytes = external.read_bytes()
    samples = Samples()
    context = _context(experiment.output, samples)
    target = experiment.output / "merged.mov"
    assert (
        media_io.concat_prores(
            context,
            (first, external),
            target,
            rate,
            7,
            source_frame_counts=(2, 5),
        )
        == 7
    )
    _remux.require_match(experiment.inspect(target, rate, 7), expected)
    assert samples.values[-1] == (1.0, 12, 12)
    assert external.read_bytes() == raw_bytes
    assert (experiment.output / "merged.input-0001.mov").is_file()
