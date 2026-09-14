"""独立审核素材准备的 exact 身份、时钟和取消边界；全部使用合成输入。"""

from __future__ import annotations

import json
import sys
import threading
from dataclasses import replace
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from test_source_preparation_kernel import _diagnose, _input, _request, _runner, _tool
from test_source_preparation_kernel import media as media
from zniku.graph import ExecutionMode, ManualExternalExecutorSpec
from zniku.media.probe import MediaNodeError
from zniku.runtime import ManualSubmission, RunnerError
from zniku.source_preparation import diagnostics_definition, external_repair_definition
from zniku.source_preparation.inspection import inspect_source, read_header
from zniku.source_preparation.models import SourceGate
from zniku.source_preparation.preservation import create_t1_candidate, verify_preservation
from zniku.source_preparation.process import stream_process
from zniku.source_preparation.progress import cancellation_scope


def test_manual_definition_cannot_mint_trusted_diagnosis_metadata(
    media: Path, tmp_path: Path
) -> None:
    """可自由编辑 Graph 不等于可借正式 validator 给人工伪造报告签发语义。"""
    runner = _runner(tmp_path / "attempts")
    original, diagnosis = _diagnose(runner, media)
    official = diagnostics_definition()
    forged_definition = official.model_copy(
        update={
            "type_id": "synthetic.manual_diagnosis",
            "execution_mode": ExecutionMode.MANUAL_EXTERNAL,
            "executor": ManualExternalExecutorSpec(
                output_paths=official.executor.output_paths,
                instructions="合成测试专用人工报告",
            ),
        }
    )
    request = _request(forged_definition, {}, (_input("original_media", original),))
    handoff = runner.prepare_manual(request)
    report = json.loads(diagnosis.path.read_text(encoding="utf-8"))
    report["video"]["frame_count"] = 900
    report["video"]["packet_count"] = 900
    Path(handoff.outputs[0].path).write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(RunnerError, match="DEFINITION"):
        runner.submit_manual(request, handoff, ManualSubmission())


def test_direct_diagnosis_validator_rejects_renamed_definition(media: Path, tmp_path: Path) -> None:
    """即使实际自动 adapter 相同，新增 type/version 也不得沿用冻结语义 namespace。"""
    runner = _runner(tmp_path / "attempts")
    original, _ = _diagnose(runner, media)
    official = diagnostics_definition()
    renamed = official.model_copy(update={"type_id": "synthetic.renamed_diagnosis"})
    request = _request(official, {}, (_input("original_media", original),))
    with pytest.raises(RunnerError, match="DEFINITION"):
        runner.run_automatic(replace(request, definition=renamed))


def test_eof_does_not_disable_cancellation() -> None:
    """工具提前关闭 stdout 后仍须等到真正退出，并在等待期继续响应取消。"""
    event = threading.Event()
    timer = threading.Timer(0.25, event.set)
    timer.start()
    started = monotonic()
    try:
        with pytest.raises(MediaNodeError, match="CANCELLED"), cancellation_scope(event):
            stream_process(
                [sys.executable, "-c", "import os,time; os.close(1); time.sleep(1.5)"],
                consume=lambda _: None,
            )
    finally:
        timer.cancel()
    assert monotonic() - started < 1.25


def test_eof_does_not_disable_timeout() -> None:
    """输出 EOF 不应把原执行预算改为无条件的额外十秒。"""
    started = monotonic()
    with pytest.raises(MediaNodeError, match="TIMEOUT"):
        stream_process(
            [sys.executable, "-c", "import os,time; os.close(1); time.sleep(1.5)"],
            consume=lambda _: None,
            timeout=0.25,
        )
    assert monotonic() - started < 1.25


def _gate() -> dict[str, Any]:
    original = str(uuid4())
    return {
        "original_media_artifact_id": original,
        "reference_media_artifact_id": original,
        "diagnosis_artifact_id": str(uuid4()),
        "source_frame_count": 30,
        "frame_rate": "30/1",
        "original_video_start": "0/1",
        "reference_start": "0/1",
        "geometry": {"width": 64, "height": 48},
        "signal": {},
        "preparation_strategy": "original",
        "audio_policy": "none",
        "audio_bindings": [{"artifact_id": original, "ordinal": 0, "tracks": []}],
    }


@pytest.mark.parametrize("problem", ["strategy_identity", "empty_audio", "relative_audio"])
def test_gate_rejects_internally_contradictory_binding(problem: str) -> None:
    """局部绑定自身必须自洽，不能只依赖生产路径碰巧写对字段。"""
    data = _gate()
    if problem == "strategy_identity":
        data["reference_media_artifact_id"] = str(uuid4())
    elif problem == "empty_audio":
        data["audio_bindings"] = []
    else:
        data["audio_policy"] = "original"
        data["audio_bindings"][0]["tracks"] = [
            {
                "stream_index": 1,
                "codec": "aac",
                "sample_rate": 48000,
                "channels": 1,
                "channel_layout": "mono",
                "start_time": "0/1",
                "end_time": "1/1",
                "relative_start": "9/1",
                "relative_end": "10/1",
                "sample_count": 48000,
            }
        ]
    with pytest.raises(ValidationError):
        SourceGate.model_validate_json(json.dumps(data))


def test_t1_preserves_complete_b_frame_tail(media: Path, tmp_path: Path) -> None:
    """真实 B 帧和非完整 GOP 尾部必须完整比较，不能以显示顺序猜包顺序。"""
    assert read_header(media)["streams"][0]["has_b_frames"] > 0
    encoded = tmp_path / "b-frame-complete.mkv"
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-stream_loop",
            "1",
            "-i",
            str(media),
            "-frames:v",
            "37",
            "-an",
            "-c:v",
            "libx264",
            "-vf",
            "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
            "-x264-params",
            "colorprim=bt709:transfer=bt709:colormatrix=bt709:force-cfr=1",
            str(encoded),
        ]
    )
    original = tmp_path / "b-frame-clock.mkv"
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(encoded),
            "-c",
            "copy",
            "-bsf:v",
            "setts=pts=PTS*1.004:dts=DTS*1.004",
            str(original),
        ]
    )
    report = inspect_source(original, str(uuid4()), target_frame_rate="30/1")
    assert report.video.frame_count == 37
    assert report.candidate_strategies == ("t1-clock-quantization/1",)
    candidate = tmp_path / "b-frame-fixed.mkv"
    create_t1_candidate(original, candidate, report, target_frame_rate="30/1")
    verified = verify_preservation(original, candidate, report, target_frame_rate="30/1")
    assert not verified.findings and verified.video.frame_count == 37


def test_fixed_h264_clock_conflict_is_not_a_healthy_work_reference(
    media: Path, tmp_path: Path
) -> None:
    """展示 PTS 为 30fps，但明确 fixed VUI 为 25fps，不能当成无冲突参考。"""
    encoded = tmp_path / "fixed-clock.mkv"
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(media),
            "-an",
            "-c:v",
            "libx264",
            "-vf",
            "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
            "-x264-params",
            "colorprim=bt709:transfer=bt709:colormatrix=bt709:force-cfr=1",
            str(encoded),
        ]
    )
    conflicting = tmp_path / "conflicting-clock.mkv"
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(encoded),
            "-c",
            "copy",
            "-bsf:v",
            "h264_metadata=tick_rate=50/1",
            str(conflicting),
        ]
    )
    report = inspect_source(conflicting, str(uuid4()), target_frame_rate="30/1")
    assert report.findings, report.video.model_dump()


def test_external_target_rate_must_match_bound_diagnosis(media: Path, tmp_path: Path) -> None:
    """不得把无声原件变速后的同像素副本登记为当前诊断的保内容修复。"""
    runner = _runner(tmp_path / "attempts")
    original, diagnosis = _diagnose(runner, media)
    request = _request(
        external_repair_definition("mkv"),
        {"target_frame_rate": "60/1"},
        (_input("original_media", original), _input("diagnosis", diagnosis)),
    )
    handoff = runner.prepare_manual(request)
    candidate = Path(handoff.outputs[0].path)
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(media),
            "-c",
            "copy",
            "-bsf:v",
            "setts=pts=PTS/2:dts=DTS/2",
            "-r",
            "60",
            str(candidate),
        ]
    )
    with pytest.raises(RunnerError, match="INPUT_BINDING"):
        runner.submit_manual(request, handoff, ManualSubmission())
