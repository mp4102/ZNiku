"""短合成媒体检查源对齐时间轴、容器及原音轨偏移；不读取任何用户媒体。"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from test_source_aligned_node_contracts import pipeline
from zniku.avenhance_v27.probe import (
    AV27_NAMESPACE,
    Av27MediaError,
    audio_signatures_from_metadata,
    probe_header,
)
from zniku.graph import NodeInstance
from zniku.runtime import NodeExecutionRequest, NodeValidatorContext, PythonAdapterContext
from zniku.runtime.runner import OutputTarget, ValidatedOutput
from zniku.source_aligned import adapters, validators
from zniku.source_aligned.definitions import external_definition, final_mux_definition
from zniku.source_aligned.node_contracts import (
    OVERLAP_NAMESPACE,
    DeclaredContainer,
    ExternalParameters,
)
from zniku.source_aligned.timeline import (
    check_frame_timestamps,
    probe_audio_origins,
    probe_cfr,
    same_audio_signatures,
    verify_audio_origins,
    verify_video_span,
)

TOOLS = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.mark.parametrize("rate", [Fraction(30), Fraction(30000, 1001)])
@pytest.mark.parametrize("base", [Fraction(1, 30000), Fraction(1, 1000)])
@pytest.mark.parametrize("origin", [0, 7000, -9000])
def test_cfr_accepts_quantized_different_clocks_and_origins(
    rate: Fraction, base: Fraction, origin: int
) -> None:
    timestamps = [origin + round(index / rate / base) for index in range(216154)]
    result = check_frame_timestamps(timestamps, count=216154, rate=rate, time_base=base)
    assert result.frame_count == 216154 and result.first_pts == origin * base


@pytest.mark.parametrize("mode", ["drop", "extra", "duplicate", "reverse", "drift", "gap"])
def test_n_and_fps_header_cannot_hide_wrong_presentation(mode: str) -> None:
    stamps = [index * 1000 for index in range(20)]
    if mode == "drop":
        stamps.pop()
    elif mode == "extra":
        stamps.append(20000)
    elif mode == "duplicate":
        stamps[5] = stamps[4]
    elif mode == "reverse":
        stamps[5], stamps[6] = stamps[6], stamps[5]
    elif mode == "drift":
        stamps = [index * 1002 for index in range(20)]
    else:
        stamps[5] += 300
    with pytest.raises(Av27MediaError, match="TIMELINE"):
        check_frame_timestamps(stamps, count=20, rate=Fraction(30), time_base=Fraction(1, 30000))


@pytest.mark.parametrize("confirmation", [None, 1, "true", False])
def test_external_requires_explicit_boolean_confirmation(tmp_path: Path, confirmation: Any) -> None:
    source = pipeline(tmp_path)[0].parameters["source"]
    with pytest.raises((ValueError, Av27MediaError)):
        ExternalParameters.model_validate(
            {
                "source": source,
                "model_name": "test",
                "declared_container": "mp4",
                "operator_frame_order_confirmed": confirmation,
            }
        )


def media(path: Path, *, rate: str = "30", frames: int = 12, shift: str = "0") -> None:
    codec = (
        ["-c:v", "ffv1"]
        if path.suffix == ".mkv"
        else (
            ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"]
            if path.suffix == ".mov"
            else ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
        )
    )
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=64x36:rate={rate}",
            "-frames:v",
            str(frames),
            "-an",
            *codec,
            "-color_range",
            "tv",
            "-colorspace",
            "bt709",
            "-color_trc",
            "bt709",
            "-color_primaries",
            "bt709",
            "-chroma_sample_location",
            "left",
            "-output_ts_offset",
            shift,
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def external_context(
    tmp_path: Path, container: DeclaredContainer, *, shift: str = "0", rate: str = "30/1"
) -> NodeValidatorContext:
    step = pipeline(tmp_path, n=12, chapters=1, rate=rate)[0]
    source, gate = step.inputs
    source_path = tmp_path / "source.mp4"
    target = tmp_path / f"restoration.{container}"
    media(source_path, rate=rate)
    media(target, rate=rate, shift=shift)
    namespace = source.media_info[AV27_NAMESPACE]
    assert isinstance(namespace, dict)
    data = dict(namespace)
    data["geometry"] = {"width": 64, "height": 36}
    source = replace(
        source, path=source_path, port_id="video", ordinal=None, media_info={AV27_NAMESPACE: data}
    )
    definition = external_definition(container)
    node = NodeInstance(
        node_id="restore",
        type_id=definition.type_id,
        definition_version=definition.version,
        parameters={
            "source": step.parameters["source"],
            "declared_container": container,
            "model_name": "synthetic",
            "operator_frame_order_confirmed": True,
        },
    )
    output = ValidatedOutput(
        "video", "VideoFile", target, target.stat().st_size, target.stat().st_mtime_ns, {}
    )
    return NodeValidatorContext(
        NodeExecutionRequest(str(uuid4()), 1, definition, node, (source, gate)),
        tmp_path,
        (output,),
    )


@pytest.mark.skipif(not TOOLS, reason="requires ffmpeg/ffprobe")
@pytest.mark.parametrize("container", ["mp4", "mov", "mkv"])
@pytest.mark.parametrize("rate", ["30/1", "30000/1001"])
def test_external_accepts_actual_container_and_independent_timestamps(
    tmp_path: Path, container: DeclaredContainer, rate: str
) -> None:
    context = external_context(tmp_path, container, shift="5", rate=rate)
    result = validators.validate_external(context)
    assert result.passed, result.message
    assert result.summary["alignment"] == "relative-presentation-cfr"
    assert result.summary["content_correspondence"] == "operator-declared-not-proven"


@pytest.mark.skipif(not TOOLS, reason="requires ffmpeg/ffprobe")
def test_renaming_mp4_as_mkv_does_not_change_real_container(tmp_path: Path) -> None:
    context = external_context(tmp_path, "mkv")
    output = context.outputs[0]
    shutil.copyfile(context.request.inputs[0].path, output.path)
    result = validators.validate_external(context)
    assert not result.passed and not result.media_info_extensions


@pytest.mark.skipif(not TOOLS, reason="requires ffmpeg/ffprobe")
def test_actual_decoded_n_is_checked_not_only_header(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    media(source)
    assert probe_header(source).video.frame_count == 12
    with pytest.raises(Av27MediaError):
        probe_cfr(source, count=13, rate=Fraction(30))


@pytest.mark.skipif(not TOOLS, reason="requires ffmpeg/ffprobe")
@pytest.mark.parametrize(
    "offsets,origin",
    [
        (("0.2",), "2"),
        (("-0.15",), "2"),
        (("0.2", "-0.15"), "2"),
        (("0",), "0"),
    ],
)
def test_final_copy_preserves_aac_origins_or_fails_closed_on_unsupported_priming(
    tmp_path: Path, offsets: tuple[str, ...], origin: str
) -> None:
    source_path, program_path, target = (
        tmp_path / "original.mp4",
        tmp_path / "program.mp4",
        tmp_path / "final.mkv",
    )
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-copyts",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=64x36:rate=30:duration=1",
    ]
    filters = [f"[0:v]setpts=PTS+{origin}/TB[v]"]
    for index, offset in enumerate(offsets, start=1):
        command.extend(["-f", "lavfi", "-i", f"sine=frequency={440 * index}:duration=1"])
        filters.append(f"[{index}:a]asetpts=PTS+({origin}+({offset}))/TB[a{index}]")
    command.extend(["-filter_complex", ";".join(filters), "-map", "[v]"])
    for index in range(1, len(offsets) + 1):
        command.extend(["-map", f"[a{index}]"])
    command.extend(
        [
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-avoid_negative_ts",
            "disabled",
            str(source_path),
        ]
    )
    subprocess.run(command, check=True, capture_output=True)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x36:rate=60:duration=1",
            "-c:v",
            "libx265",
            "-x265-params",
            "colorprim=bt709:transfer=bt709:colormatrix=bt709:range=limited:chromaloc=0",
            "-pix_fmt",
            "yuv420p10le",
            "-tag:v",
            "hvc1",
            "-color_range",
            "tv",
            "-colorspace",
            "bt709",
            "-color_trc",
            "bt709",
            "-color_primaries",
            "bt709",
            "-chroma_sample_location",
            "left",
            "-an",
            str(program_path),
        ],
        check=True,
        capture_output=True,
    )
    steps = pipeline(tmp_path, n=30, chapters=1, rate="30/1")
    step = steps[-1]
    program, source, gate = step.inputs
    import json

    program_info = json.loads(json.dumps(program.media_info))
    program_info[OVERLAP_NAMESPACE]["geometry"] = {
        "width": 64,
        "height": 36,
        "sample_aspect_ratio": "1/1",
    }
    source_info = json.loads(json.dumps(source.media_info))
    source_info[AV27_NAMESPACE]["audio_tracks"] = [
        item.signature() for item in probe_header(source_path).audios
    ]
    source = replace(source, path=source_path, media_info=source_info)
    program = replace(program, path=program_path, media_info=program_info)
    definition = final_mux_definition()
    node = NodeInstance(
        node_id="final",
        type_id=definition.type_id,
        definition_version=definition.version,
        parameters=step.parameters,
    )
    context = PythonAdapterContext(
        str(uuid4()),
        1,
        definition,
        node,
        tmp_path,
        (program, source, gate),
        (OutputTarget("media", "MediaFile", target),),
        tmp_path / "stdout.log",
        tmp_path / "stderr.log",
    )
    if origin == "0":
        with pytest.raises(Av27MediaError, match="AUDIO_PRIMING_UNSUPPORTED"):
            adapters.final_mux(context)
        assert not target.exists()
        assert source_path.exists() and program_path.exists()
        return
    result = adapters.final_mux(context)
    assert result.producer_metadata["media"]["output_frames"] == 60
    verify_audio_origins(source_path, target)
    assert len(probe_audio_origins(target).offsets) == len(offsets)
    assert same_audio_signatures(
        tuple(item.signature() for item in probe_header(target).audios),
        audio_signatures_from_metadata(source.media_info),
    )
    verify_video_span(target, count=60, rate=Fraction(60))
    accepted = validators.validate_final_mux(
        NodeValidatorContext(
            NodeExecutionRequest(context.node_run_id, 1, definition, node, context.inputs),
            tmp_path,
            (
                ValidatedOutput(
                    "media",
                    "MediaFile",
                    target,
                    target.stat().st_size,
                    target.stat().st_mtime_ns,
                    {},
                    result.producer_metadata["media"],
                ),
            ),
        )
    )
    assert accepted.passed, accepted.message


@pytest.mark.skipif(not TOOLS, reason="requires ffmpeg/ffprobe")
def test_automatic_timeline_cancellation_reaps_probe_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from zniku.runtime import RunnerCancelled
    from zniku.runtime.progress import ProgressUnit

    class Cancel:
        def report(
            self,
            fraction: float,
            *,
            current: int | None = None,
            total: int | None = None,
            unit: ProgressUnit | None = None,
            stage: str | None = None,
        ) -> None:
            assert fraction == 0.0
            raise RunnerCancelled("synthetic cancellation")

    path = tmp_path / "probe.mp4"
    media(path)
    processes: list[subprocess.Popen[bytes]] = []
    original = subprocess.Popen

    def recording(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", recording)
    with pytest.raises(RunnerCancelled):
        probe_cfr(path, count=12, rate=Fraction(30), progress=Cancel())
    assert len(processes) >= 2 and all(process.poll() is not None for process in processes)


def test_unknown_audio_language_equivalence_does_not_hide_real_track_changes() -> None:
    assert same_audio_signatures(
        ({"language": None, "codec": "aac"},), ({"language": "und", "codec": "aac"},)
    )
    assert not same_audio_signatures(
        ({"language": "eng", "codec": "aac"},), ({"language": "und", "codec": "aac"},)
    )
    assert not same_audio_signatures(
        ({"language": "und", "codec": "aac"},), ({"language": "und", "codec": "flac"},)
    )
