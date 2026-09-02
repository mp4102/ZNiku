"""验证 AVEnhanceFlow v2.7 adapter 的计划闭合、单 producer 与失败关闭语义。

测试只使用合成 metadata、临时小文件和受控 monkeypatch，不解码真实媒体，也不依赖本机
FFmpeg/GPU。重点固定 Source 时间线回退、Split/Program/Final 的 producer 边界及其原始
``producer_metadata`` 只能在成功返回时出现。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import JsonValue

from zniku.avenhance_v27 import adapters
from zniku.avenhance_v27 import probe as avprobe
from zniku.avenhance_v27.definitions import source_program_definition
from zniku.avenhance_v27.probe import (
    AV27_NAMESPACE,
    Av27AudioHeader,
    Av27MediaError,
    Av27MediaHeader,
    Av27VideoHeader,
)
from zniku.graph import ExecutionMode, NodeDefinition, NodeInstance, PythonExecutorSpec
from zniku.runtime import PythonAdapterContext, RunnerInput
from zniku.runtime.runner import OutputTarget


def _video(
    *,
    avg_rate: Fraction = Fraction(25, 1),
    r_rate: Fraction = Fraction(25, 1),
    color_space: str | None = "bt709",
    hdr_side_data: tuple[str, ...] = (),
) -> Av27VideoHeader:
    """构造不依赖 FFprobe 的最小严格视频 header。"""

    return Av27VideoHeader(
        index=0,
        codec="h264",
        profile="High",
        codec_tag_string="avc1",
        width=1920,
        height=1080,
        pixel_format="yuv420p",
        frame_rate=avg_rate,
        avg_frame_rate=avg_rate,
        r_frame_rate=r_rate,
        time_base=Fraction(1, 1000),
        frame_count=None,
        sample_aspect_ratio="1:1",
        field_order="progressive",
        rotation=0,
        color_range="tv",
        color_space=color_space,
        color_transfer="bt709",
        color_primaries="bt709",
        chroma_location="left",
        hdr_side_data=hdr_side_data,
        duration_seconds=0.2,
    )


def _header(path: Path, video: Av27VideoHeader) -> Av27MediaHeader:
    return Av27MediaHeader(
        path=path,
        format_name="matroska,webm",
        duration_seconds=0.2,
        streams=("video",),
        videos=(video,),
        audios=(),
        others=(),
        chapter_count=0,
    )


def _namespace(
    frames: int,
    *,
    rate: str = "25/1",
    duration: float | None = None,
    source_ordinal: int = 0,
    audio_tracks: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    """构造 RunnerInput 唯一允许读取的 v2.7 namespaced metadata。"""

    return {
        AV27_NAMESPACE: {
            "frame_count": frames,
            "frame_rate": rate,
            "duration_seconds": duration if duration is not None else frames / 25,
            "source_ordinal": source_ordinal,
            "geometry": {"width": 1920, "height": 1080},
            "sample_aspect_ratio": "1:1",
            "signal": {
                "color_range": "tv",
                "color_space": "bt709",
                "color_transfer": "bt709",
                "color_primaries": "bt709",
            },
            "audio_tracks": [dict(item) for item in audio_tracks],
        }
    }


def _input(
    tmp_path: Path,
    port_id: str,
    artifact_id: str,
    *,
    ordinal: int | None = None,
    kind: str = "VideoFile",
    media_info: Mapping[str, object] | None = None,
) -> RunnerInput:
    path = tmp_path / f"{artifact_id}.bin"
    path.write_bytes(b"synthetic")
    return RunnerInput(
        port_id=port_id,
        artifact_id=artifact_id,
        kind=kind,
        path=path,
        ordinal=ordinal,
        media_info=media_info or {},
    )


def _context(
    tmp_path: Path,
    *,
    parameters: dict[str, JsonValue],
    inputs: tuple[RunnerInput, ...],
    outputs: tuple[tuple[str, str, str], ...],
) -> PythonAdapterContext:
    work_dir = tmp_path / "attempt"
    work_dir.mkdir()
    output_targets: list[OutputTarget] = []
    for port_id, kind, relative_path in outputs:
        path = work_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        output_targets.append(OutputTarget(port_id, kind, path))
    definition = NodeDefinition(
        type_id="test.av27.adapter",
        version="0.2.1",
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="tests.test_av27_adapters:synthetic"),
    )
    node = NodeInstance(
        node_id="test-node",
        type_id=definition.type_id,
        definition_version=definition.version,
        parameters=parameters,
    )
    return PythonAdapterContext(
        node_run_id="test-node-run",
        attempt=1,
        definition=definition,
        node=node,
        work_dir=work_dir,
        inputs=inputs,
        outputs=tuple(output_targets),
        stdout_log_path=work_dir / "stdout.log",
        stderr_log_path=work_dir / "stderr.log",
    )


def _audio_tracks() -> tuple[dict[str, object], ...]:
    return (
        {
            "codec": "aac",
            "profile": "LC",
            "extradata_hash": "SHA256:track-one",
            "sample_rate": 48000,
            "channels": 2,
            "channel_layout": "stereo",
            "language": "jpn",
            "title": "Main",
            "default": True,
            "forced": False,
        },
        {
            "codec": "aac",
            "profile": "LC",
            "extradata_hash": "SHA256:track-two",
            "sample_rate": 48000,
            "channels": 2,
            "channel_layout": "stereo",
            "language": "eng",
            "title": "Commentary",
            "default": False,
            "forced": False,
        },
    )


def _audio_headers() -> tuple[Av27AudioHeader, ...]:
    result: list[Av27AudioHeader] = []
    for index, item in enumerate(_audio_tracks(), start=1):
        result.append(
            Av27AudioHeader(
                index=index,
                codec=str(item["codec"]),
                profile=str(item["profile"]),
                extradata_hash=str(item["extradata_hash"]),
                sample_rate=48000,
                channels=2,
                channel_layout="stereo",
                language=str(item["language"]),
                title=str(item["title"]),
                default=item["default"] is True,
                forced=False,
            )
        )
    return tuple(result)


def test_source_program_uses_external_bindings_without_attempt_targets(tmp_path: Path) -> None:
    """Source 输出是同一只读外部引用，不要求伪造 attempt 内 output path。"""

    source = tmp_path / "source.mkv"
    source.write_bytes(b"synthetic")
    work_dir = tmp_path / "attempt"
    work_dir.mkdir()
    definition = source_program_definition()
    node = NodeInstance(
        node_id="source",
        type_id=definition.type_id,
        definition_version=definition.version,
        parameters={"source_path": str(source.resolve()), "source_ordinal": 0},
    )
    context = PythonAdapterContext(
        node_run_id="source-run",
        attempt=1,
        definition=definition,
        node=node,
        work_dir=work_dir,
        inputs=(),
        outputs=(),
        stdout_log_path=work_dir / "stdout.log",
        stderr_log_path=work_dir / "stderr.log",
    )

    result = adapters.source_program(context)

    assert tuple(output.port_id for output in result.outputs) == ("video", "source_media")
    assert all(output.path == source.resolve() for output in result.outputs)
    assert all(output.allow_external for output in result.outputs)


def test_source_timeline_falls_back_without_changing_packet_n(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"synthetic")
    scans = iter(
        (
            avprobe._TimelineScan(5, 4, 5, 4, 3, 3, 3, Fraction(1, 25), Fraction(25, 1)),
            avprobe._TimelineScan(5, 5, 0, 5, 4, 4, 4, Fraction(1, 25), Fraction(25, 1)),
        )
    )
    calls: list[str] = []

    def fake_scan(
        argv: list[str],
        **_kwargs: object,
    ) -> avprobe._TimelineScan:
        calls.append("frames" if "-show_frames" in argv else "packets")
        return next(scans)

    monkeypatch.setattr(avprobe, "_scan_timeline", fake_scan)

    timeline = avprobe.probe_source_timeline(
        source,
        header=_header(source, _video()),
        ffprobe_executable="synthetic-ffprobe",
    )

    assert calls == ["packets", "frames"]
    assert timeline.frame_count == 5
    assert timeline.authority == "decoded_presentation"
    assert timeline.dts_coverage == pytest.approx(0.8)
    assert timeline.confidence == "medium"


def test_source_timeline_rejects_decoded_count_conflicting_with_packet_n(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """decoded fallback 只能提供 cadence clock，不能与 packet N 形成矛盾样本。"""

    source = tmp_path / "source.mkv"
    source.write_bytes(b"synthetic")
    scans = iter(
        (
            avprobe._TimelineScan(5, 4, 5, 4, 3, 3, 3, Fraction(1, 25), Fraction(25, 1)),
            avprobe._TimelineScan(10, 10, 0, 10, 9, 9, 9, Fraction(1, 25), Fraction(25, 1)),
        )
    )
    monkeypatch.setattr(avprobe, "_scan_timeline", lambda *_args, **_kwargs: next(scans))

    with pytest.raises(Av27MediaError) as captured:
        avprobe.probe_source_timeline(
            source,
            header=_header(source, _video()),
            ffprobe_executable="synthetic-ffprobe",
        )

    assert captured.value.code == "E_AV27_SOURCE_FPS_AMBIGUOUS"


def test_source_timeline_rejects_header_count_conflicting_with_packet_n(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"synthetic")
    monkeypatch.setattr(
        avprobe,
        "_scan_timeline",
        lambda *_args, **_kwargs: avprobe._TimelineScan(
            5,
            5,
            5,
            5,
            4,
            4,
            4,
            Fraction(1, 25),
            Fraction(25, 1),
        ),
    )
    conflicting = replace(_video(), frame_count=6)

    with pytest.raises(Av27MediaError) as captured:
        avprobe.probe_source_timeline(
            source,
            header=_header(source, conflicting),
            ffprobe_executable="synthetic-ffprobe",
        )

    assert captured.value.code == "E_AV27_SOURCE_FPS_AMBIGUOUS"


def test_source_rejects_conflicting_avg_and_r_rate_before_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"synthetic")
    monkeypatch.setattr(
        avprobe,
        "_scan_timeline",
        lambda *_args, **_kwargs: pytest.fail("rate 冲突时不得开始 payload traversal"),
    )

    with pytest.raises(Av27MediaError) as captured:
        avprobe.probe_source_timeline(
            source,
            header=_header(source, _video(r_rate=Fraction(24, 1))),
            ffprobe_executable="synthetic-ffprobe",
        )

    assert captured.value.code == "E_AV27_SOURCE_FPS_AMBIGUOUS"


@pytest.mark.parametrize(
    ("video", "expected_code"),
    [
        (_video(color_space="bt2020nc"), "E_AV27_SIGNAL_CONFLICT"),
        (_video(hdr_side_data=("Mastering display metadata",)), "E_AV27_SIGNAL_CONFLICT"),
    ],
)
def test_signal_conflicts_fail_closed(video: Av27VideoHeader, expected_code: str) -> None:
    with pytest.raises(Av27MediaError) as captured:
        avprobe.resolve_bt709_signal(video, role="synthetic")
    assert captured.value.code == expected_code


def test_atomic_split_runs_once_per_physical_source_and_returns_source_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    videos = (
        _input(
            tmp_path,
            "videos",
            "source-video-0",
            ordinal=0,
            media_info=_namespace(4, duration=0.16, source_ordinal=0),
        ),
        _input(
            tmp_path,
            "videos",
            "source-video-1",
            ordinal=1,
            media_info=_namespace(3, duration=0.12, source_ordinal=1),
        ),
    )
    gate = _input(tmp_path, "gate", "admission", kind="DataFile")
    segments: list[JsonValue] = [
        {
            "port_id": "leaf-0001",
            "source_ordinal": 0,
            "planned_effective_video_artifact_id": "source-video-0",
            "chapter_id": "chapter-0001",
            "chapter_ordinal": 0,
            "leaf_id": "leaf-id-0001",
            "leaf_ordinal": 0,
            "start_frame": 0,
            "end_frame": 2,
        },
        {
            "port_id": "leaf-0002",
            "source_ordinal": 0,
            "planned_effective_video_artifact_id": "source-video-0",
            "chapter_id": "chapter-0001",
            "chapter_ordinal": 0,
            "leaf_id": "leaf-id-0002",
            "leaf_ordinal": 1,
            "start_frame": 2,
            "end_frame": 4,
        },
        {
            "port_id": "leaf-0003",
            "source_ordinal": 1,
            "planned_effective_video_artifact_id": "source-video-1",
            "chapter_id": "chapter-0002",
            "chapter_ordinal": 1,
            "leaf_id": "leaf-id-0003",
            "leaf_ordinal": 2,
            "start_frame": 0,
            "end_frame": 3,
        },
    ]
    context = _context(
        tmp_path,
        parameters={
            "planned_admission_artifact_id": "admission",
            "segments": segments,
        },
        inputs=(*videos, gate),
        outputs=(
            ("leaf-0001", "VideoFile", "leaves/leaf-0001.mkv"),
            ("leaf-0002", "VideoFile", "leaves/leaf-0002.mkv"),
            ("leaf-0003", "VideoFile", "leaves/leaf-0003.mkv"),
        ),
    )
    monkeypatch.setattr(
        "zniku.avenhance_v27.adapters.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=10**15),
    )
    calls: list[list[str]] = []

    def fake_run(
        _context: PythonAdapterContext,
        argv: Sequence[str],
        **kwargs: object,
    ) -> int:
        command = list(argv)
        calls.append(command)
        segment_list = Path(command[command.index("-segment_list") + 1])
        segment_count = len(command[command.index("-segment_frames") + 1].split(","))
        rows: list[str] = []
        for index in range(1, segment_count + 1):
            name = f"part-{index:06d}.mkv"
            (segment_list.parent / name).write_bytes(f"part-{index}".encode())
            rows.append(f"{name},0,1")
        segment_list.write_text("\n".join(rows) + "\n", encoding="utf-8")
        progress = kwargs["progress"]
        assert isinstance(progress, adapters._ProgressContract)
        assert progress.extent is not None
        return progress.extent

    monkeypatch.setattr(adapters, "_run_ffmpeg", fake_run)

    result = adapters.atomic_split(context)

    assert len(calls) == 2
    assert all("-c:v" in command and "ffv1" in command for command in calls)
    assert all("-fflags" in command and "+genpts" in command for command in calls)
    assert all("-fps_mode" in command and "passthrough" in command for command in calls)
    assert result.producer_metadata == {
        "leaf-0001": {"output_frames": 4},
        "leaf-0002": {"output_frames": 4},
        "leaf-0003": {"output_frames": 3},
    }
    frame_counts: list[int] = []
    for item in result.outputs:
        assert item.frame_range is not None
        frame_counts.append(item.frame_range.end_frame - item.frame_range.start_frame)
    assert frame_counts == [2, 2, 3]
    assert all(item.path.is_file() for item in result.outputs)


@pytest.mark.parametrize(
    "timestamps",
    ["not-a-number,1", "NaN,1", "0,Infinity", "2,1"],
)
def test_split_segment_list_rejects_invalid_timestamps(
    tmp_path: Path,
    timestamps: str,
) -> None:
    """FFmpeg CSV 的时间列也是 producer 完整性边界，不能只信任文件名。"""

    stage_dir = tmp_path / "stage"
    stage_dir.mkdir()
    (stage_dir / "part-000001.mkv").write_bytes(b"synthetic")
    segment_list = stage_dir / "segments.csv"
    segment_list.write_text(
        f"part-000001.mkv,{timestamps}\n",
        encoding="utf-8",
    )

    with pytest.raises(Av27MediaError) as captured:
        adapters._read_segment_list(segment_list, stage_dir, 1)

    assert captured.value.code == "E_AV27_SPLIT_SEGMENT_LIST"


def test_atomic_split_rejects_changed_plan_id_before_capacity_or_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = _input(
        tmp_path,
        "videos",
        "bound-video",
        ordinal=0,
        media_info=_namespace(4),
    )
    gate = _input(tmp_path, "gate", "admission", kind="DataFile")
    context = _context(
        tmp_path,
        parameters={
            "planned_admission_artifact_id": "admission",
            "segments": [
                {
                    "port_id": "leaf-0001",
                    "source_ordinal": 0,
                    "planned_effective_video_artifact_id": "stale-video",
                    "chapter_id": "chapter-0001",
                    "chapter_ordinal": 0,
                    "leaf_id": "leaf-id-0001",
                    "leaf_ordinal": 0,
                    "start_frame": 0,
                    "end_frame": 4,
                }
            ],
        },
        inputs=(video, gate),
        outputs=(("leaf-0001", "VideoFile", "leaves/leaf-0001.mkv"),),
    )
    monkeypatch.setattr(
        "zniku.avenhance_v27.adapters.shutil.disk_usage",
        lambda _path: pytest.fail("plan ID 失配时不得探测容量"),
    )
    monkeypatch.setattr(
        adapters,
        "_run_ffmpeg",
        lambda *_args, **_kwargs: pytest.fail("plan ID 失配时不得执行 payload"),
    )

    with pytest.raises(Av27MediaError) as captured:
        adapters.atomic_split(context)

    assert captured.value.code == "E_AV27_PLAN_INPUT_CHANGED"
    assert not context.outputs[0].path.exists()


def test_atomic_split_capacity_failure_is_before_payload_and_has_no_raw_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = _input(
        tmp_path,
        "videos",
        "source-video",
        ordinal=0,
        media_info=_namespace(4, duration=120.0),
    )
    gate = _input(tmp_path, "gate", "admission", kind="DataFile")
    context = _context(
        tmp_path,
        parameters={
            "planned_admission_artifact_id": "admission",
            "segments": [
                {
                    "port_id": "leaf-0001",
                    "source_ordinal": 0,
                    "planned_effective_video_artifact_id": "source-video",
                    "chapter_id": "chapter-0001",
                    "chapter_ordinal": 0,
                    "leaf_id": "leaf-id-0001",
                    "leaf_ordinal": 0,
                    "start_frame": 0,
                    "end_frame": 4,
                }
            ],
        },
        inputs=(video, gate),
        outputs=(("leaf-0001", "VideoFile", "leaves/leaf-0001.mkv"),),
    )
    monkeypatch.setattr(
        "zniku.avenhance_v27.adapters.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=1),
    )
    monkeypatch.setattr(
        adapters,
        "_run_ffmpeg",
        lambda *_args, **_kwargs: pytest.fail("容量失败后不得执行 payload"),
    )

    with pytest.raises(Av27MediaError) as captured:
        adapters.atomic_split(context)

    assert captured.value.code == "E_AV27_SPLIT_CAPACITY"
    assert not context.outputs[0].path.exists()


@pytest.mark.parametrize(
    ("encoder", "codec", "required_tokens"),
    [
        ("cpu", "libx265", ("slow", "16", "main10", "yuv420p10le")),
        ("gpu", "hevc_nvenc", ("p7", "uhq", "p010le", "fullres", "80M", "320M")),
    ],
)
def test_program_two_chapters_use_one_formal_ffmpeg_and_exact_frame_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    encoder: str,
    codec: str,
    required_tokens: tuple[str, ...],
) -> None:
    inputs = (
        _input(tmp_path, "chapters", "fi-0", ordinal=0, media_info=_namespace(3)),
        _input(tmp_path, "chapters", "fi-1", ordinal=1, media_info=_namespace(5)),
    )
    context = _context(
        tmp_path,
        parameters={
            "encoder": encoder,
            "source_fps": "25/1",
            "chapters": [
                {
                    "chapter_id": "chapter-0001",
                    "chapter_ordinal": 0,
                    "source_frames": 2,
                    "expected_fi_frames": 3,
                    "encoded_frames": 4,
                },
                {
                    "chapter_id": "chapter-0002",
                    "chapter_ordinal": 1,
                    "source_frames": 3,
                    "expected_fi_frames": 5,
                    "encoded_frames": 6,
                },
            ],
        },
        inputs=inputs,
        outputs=(("video", "VideoFile", "program/program.mp4"),),
    )
    capability_calls: list[str] = []
    formal_calls: list[list[str]] = []
    monkeypatch.setattr(
        adapters,
        "_probe_encoder_capability",
        lambda _context, selected: capability_calls.append(selected),
    )

    def fake_run(
        _context: PythonAdapterContext,
        argv: Sequence[str],
        **_kwargs: object,
    ) -> int:
        command = list(argv)
        formal_calls.append(command)
        Path(command[-1]).write_bytes(b"synthetic-program")
        return 10

    monkeypatch.setattr(adapters, "_run_ffmpeg", fake_run)

    result = adapters.program_encode(context)

    assert capability_calls == [encoder]
    assert len(formal_calls) == 1
    command = formal_calls[0]
    assert command.count("-i") == 2
    assert codec in command
    assert all(token in command for token in required_tokens)
    assert command[command.index("-tag:v") + 1] == "hvc1"
    assert command[command.index("-video_track_timescale") + 1] == "50"
    assert command[command.index("-force_key_frames") + 1] == ("0.000000000000,0.080000000000")
    filter_script = (context.work_dir / "program-filter.txt").read_text(encoding="utf-8")
    assert filter_script.count("tpad=stop_mode=clone:stop=1") == 2
    assert "concat=n=2:v=1:a=0" in filter_script
    assert result.producer_metadata == {"video": {"output_frames": 10}}
    assert result.validation_summary == {"single_program_producer": True}


def test_program_payload_failure_cleans_declared_output_and_returns_no_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(
        tmp_path,
        parameters={
            "encoder": "cpu",
            "source_fps": "25/1",
            "chapters": [
                {
                    "chapter_id": "chapter-0001",
                    "chapter_ordinal": 0,
                    "source_frames": 2,
                    "expected_fi_frames": 3,
                    "encoded_frames": 4,
                }
            ],
        },
        inputs=(_input(tmp_path, "chapters", "fi", ordinal=0, media_info=_namespace(3)),),
        outputs=(("video", "VideoFile", "program/program.mp4"),),
    )
    monkeypatch.setattr(adapters, "_probe_encoder_capability", lambda *_args: None)

    def fail_after_partial(
        _context: PythonAdapterContext,
        argv: Sequence[str],
        **_kwargs: object,
    ) -> int:
        Path(argv[-1]).write_bytes(b"partial")
        raise Av27MediaError("E_SYNTHETIC_PRODUCER", "synthetic failure")

    monkeypatch.setattr(adapters, "_run_ffmpeg", fail_after_partial)

    with pytest.raises(Av27MediaError) as captured:
        adapters.program_encode(context)

    assert captured.value.code == "E_SYNTHETIC_PRODUCER"
    assert not context.outputs[0].path.exists()


def test_final_program_mode_maps_both_ordered_audio_tracks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = _audio_tracks()
    program = _input(tmp_path, "video", "program", media_info=_namespace(10))
    source = _input(
        tmp_path,
        "sources",
        "source",
        ordinal=0,
        kind="MediaFile",
        media_info=_namespace(5, source_ordinal=0, audio_tracks=audio),
    )
    gate = _input(tmp_path, "gate", "gate", kind="DataFile")
    context = _context(
        tmp_path,
        parameters={
            "source_mode": "program",
            "sources": [{"source_ordinal": 0, "source_frames": 5, "source_fps": "25/1"}],
            "expected_program_frames": 10,
        },
        inputs=(program, source, gate),
        outputs=(("media", "MediaFile", "final/final.mkv"),),
    )
    calls: list[list[str]] = []

    def fake_run(
        _context: PythonAdapterContext,
        argv: Sequence[str],
        **_kwargs: object,
    ) -> int:
        command = list(argv)
        calls.append(command)
        Path(command[-1]).write_bytes(b"synthetic-final")
        return 10

    monkeypatch.setattr(adapters, "_run_ffmpeg", fake_run)

    result = adapters.final_mux(context)

    assert len(calls) == 1
    command = calls[0]
    assert command[command.index("-map", command.index("-map") + 1) + 1] == "1:a?"
    assert command[command.index("-c:a") + 1] == "copy"
    assert "language=jpn" in command and "language=eng" in command
    assert "title=Main" in command and "title=Commentary" in command
    assert result.producer_metadata == {"media": {"output_frames": 10}}
    assert result.media_summary["audio_stream_count"] == 2


def test_final_pre_chaptered_uses_exact_durations_and_staging_timestamp_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = _audio_tracks()
    program = _input(tmp_path, "video", "program", media_info=_namespace(10))
    sources = (
        _input(
            tmp_path,
            "sources",
            "source-0",
            ordinal=0,
            kind="MediaFile",
            media_info=_namespace(2, source_ordinal=0, audio_tracks=audio),
        ),
        _input(
            tmp_path,
            "sources",
            "source-1",
            ordinal=1,
            kind="MediaFile",
            media_info=_namespace(3, source_ordinal=1, audio_tracks=audio),
        ),
    )
    gate = _input(tmp_path, "gate", "gate", kind="DataFile")
    context = _context(
        tmp_path,
        parameters={
            "source_mode": "pre_chaptered",
            "sources": [
                {"source_ordinal": 0, "source_frames": 2, "source_fps": "25/1"},
                {"source_ordinal": 1, "source_frames": 3, "source_fps": "25/1"},
            ],
            "expected_program_frames": 10,
        },
        inputs=(program, *sources, gate),
        outputs=(("media", "MediaFile", "final/final.mkv"),),
    )
    calls: list[list[str]] = []

    def fake_run(
        _context: PythonAdapterContext,
        argv: Sequence[str],
        **_kwargs: object,
    ) -> int | None:
        command = list(argv)
        calls.append(command)
        target = Path(command[-1])
        target.write_bytes(b"synthetic-output")
        return 10 if target.suffix == ".mkv" else None

    def fake_probe(path: str | Path, **_kwargs: object) -> Av27MediaHeader:
        return Av27MediaHeader(
            path=Path(path),
            format_name="matroska,webm",
            duration_seconds=0.1,
            streams=("audio", "audio"),
            videos=(),
            audios=_audio_headers(),
            others=(),
            chapter_count=0,
        )

    monkeypatch.setattr(adapters, "_run_ffmpeg", fake_run)
    monkeypatch.setattr(adapters, "probe_header", fake_probe)

    result = adapters.final_mux(context)

    assert len(calls) == 3
    staging, final = calls[:2], calls[2]
    assert all(
        command[command.index("-avoid_negative_ts") + 1] == "make_zero" for command in staging
    )
    assert final[final.index("-avoid_negative_ts") + 1] == "disabled"
    assert "-f" in final and "concat" in final and "1:a?" in final
    concat = (context.work_dir / "audio.ffconcat").read_text(encoding="utf-8")
    assert "duration 0.080000000000" in concat
    assert "duration 0.120000000000" in concat
    assert result.producer_metadata == {"media": {"output_frames": 10}}


def test_final_payload_failure_cleans_declared_output_and_returns_no_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = _audio_tracks()
    context = _context(
        tmp_path,
        parameters={
            "source_mode": "program",
            "sources": [{"source_ordinal": 0, "source_frames": 5, "source_fps": "25/1"}],
            "expected_program_frames": 10,
        },
        inputs=(
            _input(tmp_path, "video", "program", media_info=_namespace(10)),
            _input(
                tmp_path,
                "sources",
                "source",
                ordinal=0,
                kind="MediaFile",
                media_info=_namespace(5, source_ordinal=0, audio_tracks=audio),
            ),
            _input(tmp_path, "gate", "gate", kind="DataFile"),
        ),
        outputs=(("media", "MediaFile", "final/final.mkv"),),
    )

    def fail_after_partial(
        _context: PythonAdapterContext,
        argv: Sequence[str],
        **_kwargs: object,
    ) -> int:
        Path(argv[-1]).write_bytes(b"partial")
        raise Av27MediaError("E_SYNTHETIC_PRODUCER", "synthetic failure")

    monkeypatch.setattr(adapters, "_run_ffmpeg", fail_after_partial)

    with pytest.raises(Av27MediaError) as captured:
        adapters.final_mux(context)

    assert captured.value.code == "E_SYNTHETIC_PRODUCER"
    assert not context.outputs[0].path.exists()
