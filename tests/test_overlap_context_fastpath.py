"""有界上下文快路径只用合成 ProRes；逐包、逐帧、PTS oracle 与旧路径对照。"""

from __future__ import annotations

import importlib
import re
import shutil
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from time import perf_counter

import pytest

from test_overlap_media_io import Samples, _context
from test_source_aligned_node_contracts import pipeline
from zniku.chapter_overlap import media_io
from zniku.chapter_overlap.mov_index import indexed_mov_fallback_reason
from zniku.runtime.runner import OutputTarget
from zniku.source_aligned import adapters
from zniku.source_aligned.node_contracts import (
    OVERLAP_NAMESPACE,
    CandidateFiProfile,
    OverlapMetadata,
)

_remux = importlib.import_module("tools.check_overlap_remux")
TOOLS = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.mark.skipif(not TOOLS, reason="需要 FFmpeg/FFprobe")
@pytest.mark.parametrize(
    "rate",
    tuple(
        Fraction(value)
        for value in (
            "24",
            "30",
            "24000/1001",
            "30000/1001",
            "48",
            "60",
            "48000/1001",
            "60000/1001",
        )
    ),
)
@pytest.mark.parametrize("start,end", ((0, 1), (1, 5), (45, 49), (60, 61)))
def test_indexed_range_matches_legacy_packets_pixels_and_exact_clock(
    tmp_path: Path, rate: Fraction, start: int, end: int
) -> None:
    experiment = _remux.RemuxExperiment(tmp_path / "indexed")
    source = experiment.generate("source.mov", rate, 61, offset=0, step=1)
    before = source.read_bytes()
    oracle = experiment.copy_range(source, "oracle.mov", start, end, rate)
    assert indexed_mov_fallback_reason(source, rate, 61) is None
    target = experiment.output / "fast.mov"
    context = _context(experiment.output)
    media_io.copy_prores_range(context, source, target, start, end, rate, allow_indexed_seek=True)
    _remux.require_match(
        experiment.inspect(target, rate, end - start),
        experiment.inspect(oracle, rate, end - start),
    )
    assert source.read_bytes() == before
    assert "indexed-bounded" in context.stdout_log_path.read_text(encoding="utf-8")
    packets = re.findall(
        r"Input stream #0:0 \(video\): (\d+) packets read", context.stderr_log_path.read_text()
    )
    assert packets and int(packets[-1]) <= end - start + 8


@pytest.mark.skipif(not TOOLS, reason="需要 FFmpeg/FFprobe")
@pytest.mark.parametrize("variant", ("fragmented", "shifted", "timescale"))
def test_noncanonical_mov_explicitly_falls_back_without_changing_output(
    tmp_path: Path, variant: str
) -> None:
    experiment = _remux.RemuxExperiment(tmp_path / "fallback")
    rate = Fraction(30)
    original = experiment.generate("original.mov", rate, 18, offset=0, step=1)
    source = experiment.output / "external.mov"
    options = (
        ["-movflags", "frag_keyframe+empty_moov"]
        if variant == "fragmented"
        else ["-output_ts_offset", "2"]
        if variant == "shifted"
        else ["-video_track_timescale", "15360"]
    )
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
            *options,
            str(source),
        ],
        role="variant",
    )
    assert indexed_mov_fallback_reason(source, rate, 18) is not None
    expected = experiment.inspect(original, rate, 18)
    context = _context(experiment.output)
    target = experiment.output / "fallback.mov"
    media_io.copy_prores_range(
        context, source, target, 2, 5, rate, source_frame_count=18, allow_indexed_seek=True
    )
    _remux.require_match(
        experiment.inspect(target, rate, 3), _remux.slice_fingerprints(expected, 2, 5)
    )
    assert "sequential-fallback" in context.stdout_log_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("payload", (b"", b"bad", b"\0\0\0\1moov", b"\xff\xff\xff\xffmoov"))
def test_unknown_or_truncated_index_does_not_qualify(tmp_path: Path, payload: bytes) -> None:
    source = tmp_path / "unknown.mov"
    source.write_bytes(payload)
    assert indexed_mov_fallback_reason(source, Fraction(30), 3) is not None


@pytest.mark.skipif(not TOOLS, reason="需要 FFmpeg/FFprobe")
@pytest.mark.parametrize("ordinal", (0, 1, 2, 3, 4))
def test_context_references_full_chapters_and_materializes_only_partial_neighbors(
    tmp_path: Path, ordinal: int
) -> None:
    """含一帧短章及跨三个以上邻章；完整章不产生part，输出仍是实际完整MOV。"""
    experiment = _remux.RemuxExperiment(tmp_path / "context")
    rate = Fraction(30000, 1001)
    steps = pipeline(
        experiment.output,
        n=18,
        points=(1, 3, 7, 12),
        profile=CandidateFiProfile(left_context_frames=3, right_context_frames=4),
    )
    step = [item for item in steps if item.role == "context"][ordinal]
    original = experiment.generate("original.mov", rate, 18, offset=0, step=1)
    oracle = experiment.inspect(original, rate, 18)
    originals: dict[Path, bytes] = {}
    for item in step.inputs:
        metadata = OverlapMetadata.model_validate(item.media_info[OVERLAP_NAMESPACE])
        assert metadata.chapter is not None
        chapter = metadata.chapter
        experiment.copy_range(
            original, item.path.name, chapter.start_frame, chapter.end_frame, rate
        )
        originals[item.path] = item.path.read_bytes()
    target = experiment.output / "fi-input.mov"
    context = replace(
        _context(experiment.output, Samples()),
        inputs=step.inputs,
        outputs=(OutputTarget("video", "VideoFile", target),),
    )
    result = adapters.fi_context(context, _contract=step.contract)
    binding = step.contract.outputs[0].metadata.context
    assert binding is not None
    partial_count = sum(
        (part.start_frame, part.end_frame) != (part.chapter.start_frame, part.chapter.end_frame)
        for part in binding.parts
    )
    assert len(tuple((experiment.output / "context-parts").glob("*.mov"))) == partial_count
    _remux.require_match(
        experiment.inspect(target, rate, binding.input_frame_count),
        _remux.slice_fingerprints(oracle, binding.context_start_frame, binding.context_end_frame),
    )
    assert all(path.read_bytes() == content for path, content in originals.items())
    capacity = result.validation_summary["capacity"]
    assert isinstance(capacity, dict)
    assert capacity["estimated_staging_copy_bytes"] < capacity["estimated_output_bytes"]


@pytest.mark.skipif(not TOOLS, reason="需要 FFmpeg/FFprobe")
def test_three_equal_chapter_io_reduction_is_measured_not_inferred(tmp_path: Path) -> None:
    """三个 600 帧合成章，保留32帧上下文；AVIO逻辑量不是网卡或NAS物理吞吐。"""
    experiment = _remux.RemuxExperiment(tmp_path / "io-comparison")
    rate = Fraction(30)
    step = [
        item for item in pipeline(experiment.output, n=1800, rate="30/1") if item.role == "context"
    ][1]
    for item in step.inputs:
        experiment.run(
            experiment.ffmpeg,
            [
                "-hide_banner",
                "-nostdin",
                "-n",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x180:rate=30",
                "-frames:v",
                "600",
                "-an",
                "-c:v",
                "prores_ks",
                "-profile:v",
                "3",
                "-pix_fmt",
                "yuv422p10le",
                "-threads",
                "1",
                "-video_track_timescale",
                "30",
                str(item.path),
            ],
            role="synthetic-io",
        )
    binding = step.contract.outputs[0].metadata.context
    assert binding is not None
    old = _context(experiment.output / "old")
    paths = {item.artifact_id: item.path for item in step.inputs}
    old_parts = []
    started = perf_counter()
    for ordinal, part in enumerate(binding.parts):
        target = old.work_dir / f"part-{ordinal}.mov"
        media_io.copy_prores_range(
            old,
            paths[part.artifact_id],
            target,
            part.start_frame - part.chapter.start_frame,
            part.end_frame - part.chapter.start_frame,
            rate,
        )
        old_parts.append(target)
    media_io.concat_prores(
        old, old_parts, old.work_dir / "context.mov", rate, binding.input_frame_count
    )
    old_seconds = perf_counter() - started
    new = _context(experiment.output / "new")
    new = replace(
        new,
        inputs=step.inputs,
        outputs=(OutputTarget("video", "VideoFile", new.work_dir / "context.mov"),),
    )
    started = perf_counter()
    adapters.fi_context(new, _contract=step.contract)
    new_seconds = perf_counter() - started

    def metrics(path: Path) -> tuple[int, int]:
        log = path.read_text(encoding="utf-8")
        reads = sum(int(value) for value in re.findall(r"Statistics: (\d+) bytes read", log))
        writes = sum(int(value) for value in re.findall(r"Statistics: (\d+) bytes written", log))
        return reads, writes

    old_io, new_io = metrics(old.stderr_log_path), metrics(new.stderr_log_path)
    assert all(value > 0 for value in (*old_io, *new_io))
    assert sum(new_io) <= sum(old_io) // 2
    assert (new.work_dir / "context.mov").stat().st_size == (
        old.work_dir / "context.mov"
    ).stat().st_size
    payload_hashes = [
        experiment.run(
            experiment.ffmpeg,
            [
                "-v",
                "error",
                "-i",
                str(context.work_dir / "context.mov"),
                "-map",
                "0:v:0",
                "-c:v",
                "copy",
                "-f",
                "hash",
                "-hash",
                "sha256",
                "-",
            ],
            role="whole-context-packet-oracle",
        )
        for context in (old, new)
    ]
    assert payload_hashes[0] == payload_hashes[1]
    print(
        f"synthetic AVIO old={old_io}, new={new_io}; "
        f"seconds old={old_seconds:.3f}, new={new_seconds:.3f}; physical I/O unmeasured"
    )


def test_mov_index_atom_budget_is_explicit(tmp_path: Path) -> None:
    source = tmp_path / "atom-budget.mov"
    source.write_bytes(b"\0\0\0\x08free" * 4097)
    assert indexed_mov_fallback_reason(source, Fraction(30), 3) == "atom-budget-or-truncated"
    assert indexed_mov_fallback_reason(source, Fraction(1_000_000), 3) == "unverified-rate"


@pytest.mark.skipif(not TOOLS, reason="需要 FFmpeg/FFprobe")
@pytest.mark.parametrize("fragmented", (False, True))
def test_single_chapter_reference_preserves_legacy_missing_header_count_rejection(
    tmp_path: Path, fragmented: bool
) -> None:
    experiment = _remux.RemuxExperiment(tmp_path / "single")
    step = next(
        item
        for item in pipeline(experiment.output, n=6, chapters=1, rate="30/1")
        if item.role == "context"
    )
    rate = Fraction(30)
    original = experiment.generate("original.mov", rate, 6, offset=0, step=1)
    source = step.inputs[0].path
    experiment.run(
        experiment.ffmpeg,
        [
            "-v",
            "error",
            "-i",
            str(original),
            "-c:v",
            "copy",
            *(["-movflags", "frag_keyframe+empty_moov"] if fragmented else []),
            "-video_track_timescale",
            "30",
            str(source),
        ],
        role="single-source",
    )
    target = experiment.output / "context.mov"
    context = replace(
        _context(experiment.output),
        inputs=step.inputs,
        outputs=(OutputTarget("video", "VideoFile", target),),
    )
    if fragmented:
        # 老exact的context未接受无nb_frames增强章，不能借优化悄悄扩大集合。
        with pytest.raises(media_io.OverlapMediaError, match="E_OVERLAP_COPY_SOURCE"):
            adapters.fi_context(context, _contract=step.contract)
        assert not target.exists()
    else:
        adapters.fi_context(context, _contract=step.contract)
        assert not (experiment.output / "context-parts").exists()
        _remux.require_match(
            experiment.inspect(target, rate, 6), experiment.inspect(original, rate, 6)
        )
