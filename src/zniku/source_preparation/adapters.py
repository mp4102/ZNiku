"""执行原件入口、诊断、内置工作副本和输入型准入。

媒体验证先在独立 attempt 完成，候选失败保留但不登记输出。正常源只读引用，
原件及历史任务不改写。关闭的策略在写入任何候选之前拒绝。
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import Literal, cast

from zniku.runtime import ProducedOutput, PythonAdapterContext, PythonAdapterResult, RunnerInput

from . import models
from .contracts import check_diagnosis, namespace
from .definitions import require_definition_role
from .inspection import checked_file, file_stat, inspect_source, read_header
from .models import (
    AdmissionParameters,
    AudioBinding,
    AudioTrackBinding,
    DiagnosticParameters,
    DiagnosticReport,
    PrepareParameters,
    SourceGate,
    SourceGeometry,
    SourceParameters,
    SourceSignal,
    rational,
    summary_for_report,
)
from .preservation import create_t1_candidate, verify_preservation
from .process import fail
from .progress import progress_log, stage


def single_input(inputs: tuple[RunnerInput, ...], port: str) -> RunnerInput:
    """取得恰好一个直接输入；缺失或多值不猜测取首项。"""
    matches = [item for item in inputs if item.port_id == port]
    if len(matches) != 1:
        raise fail("INPUT_BINDING", f"{port} 必须恰好一个直接输入")
    return matches[0]


def source(context: PythonAdapterContext) -> PythonAdapterResult:
    """只读实体容器入口；限制协议先于首次 probe。"""
    require_definition_role(context.definition, "source")
    parameters = SourceParameters.model_validate(dict(context.node.parameters))
    path = checked_file(Path(parameters.source_path))
    read_header(path, progress=context.progress)
    if context.inputs or len(context.outputs) != 1:
        raise fail("SOURCE_SHAPE", "原件入口不接受输入，且只产生一个媒体引用")
    return PythonAdapterResult(
        outputs=(ProducedOutput("media", path, allow_external=True),),
        media_summary={"mode": "readonly_original", "original_preserved": True},
    )


def diagnostics(context: PythonAdapterContext) -> PythonAdapterResult:
    """只有完整、输入未变的观察才排他写出诊断 DataFile。"""
    require_definition_role(context.definition, "diagnostics")
    parameters = DiagnosticParameters.model_validate(dict(context.node.parameters))
    original = single_input(context.inputs, "original_media")
    with progress_log(context.stdout_log_path):
        report = inspect_source(
            original.path,
            original.artifact_id,
            target_frame_rate=parameters.target_frame_rate,
            progress=context.progress,
        )
    path = next(item.path for item in context.outputs if item.port_id == "diagnosis")
    with path.open("x", encoding="utf-8") as stream:
        stream.write(report.model_dump_json(indent=2))
    return PythonAdapterResult(media_summary=summary_for_report(report))


def video_prepare(context: PythonAdapterContext) -> PythonAdapterResult:
    """执行已晋级 T1；未晋级不生成输出，内部候选在完整验证后才发布。"""
    require_definition_role(context.definition, "builtin")
    parameters = PrepareParameters.model_validate(dict(context.node.parameters))
    if not models.T1_PROMOTED:
        raise fail("STRATEGY_NOT_PROMOTED", "T1 尚未完成真实代表及留出验收，不能作为内置策略运行")
    original = single_input(context.inputs, "original_media")
    diagnosis = single_input(context.inputs, "diagnosis")
    report = check_diagnosis(original, diagnosis)
    staging = context.work_dir / "staging"
    staging.mkdir(exist_ok=False)
    candidate = staging / "source-prepared.mkv"
    with progress_log(context.stdout_log_path):
        with stage("remux"):
            create_t1_candidate(
                original.path,
                candidate,
                report,
                target_frame_rate=parameters.target_frame_rate,
                progress=context.progress,
            )
        verify_preservation(
            original.path,
            candidate,
            report,
            target_frame_rate=parameters.target_frame_rate,
            progress=context.progress,
        )
    target = next(item.path for item in context.outputs if item.port_id == "media")
    if target.exists():
        raise fail("OUTPUT_CONFLICT", "正式工作副本已存在，不自动覆盖")
    candidate.rename(target)
    return PythonAdapterResult(
        producer_metadata={
            "media": {
                "verified_preservation": True,
                "source_stat": report.final_stat.model_dump(mode="json"),
                "candidate_stat": file_stat(target).model_dump(mode="json"),
                "original_media_artifact_id": original.artifact_id,
                "diagnosis_artifact_id": diagnosis.artifact_id,
            }
        }
    )


def make_gate(
    inputs: tuple[RunnerInput, ...],
    parameters: AdmissionParameters,
    *,
    progress: PythonAdapterContext | None = None,
) -> SourceGate:
    """使用实际直接输入构造准入，原件/工作参考/音频角色不会相互替代。"""
    original = single_input(inputs, "original_media")
    reference = single_input(inputs, "reference_media")
    diagnosis = single_input(inputs, "diagnosis")
    report = check_diagnosis(original, diagnosis)
    rate = parameters.target_frame_rate or report.target_frame_rate
    if rate is None:
        raise fail("RATE_SELECTION_REQUIRED", "请明确选择目标精确 FPS 后重新检查")
    if report.target_frame_rate != rate:
        raise fail("INPUT_BINDING", "准入目标率与诊断不同，需要重新诊断")
    audio_issues = [item for item in report.findings if "_AUDIO_" in item.code]
    if audio_issues:
        raise fail("AUDIO_ADMISSION", audio_issues[0].message)
    strategy: Literal["original", "t1-clock-quantization/1", "external-preservation/1"]
    if reference.artifact_id == original.artifact_id:
        if reference.path != original.path:
            raise fail("INPUT_BINDING", "同一原件身份不能指向不同路径")
        fresh = report
        strategy = "original"
    else:
        binding = namespace(reference)
        if (
            binding.get("role") != "prepared"
            or binding.get("original_media_artifact_id") != (original.artifact_id)
            or binding.get("diagnosis_artifact_id") != diagnosis.artifact_id
        ):
            raise fail("INPUT_BINDING", "修复参考没有绑定当前原件与诊断")
        if binding.get("verified_stat") != file_stat(reference.path).model_dump(mode="json"):
            raise fail("REFERENCE_CHANGED", "工作副本在验证后变化，请重新导入并验证")
        strategy_value = binding.get("strategy_id")
        if strategy_value not in {"t1-clock-quantization/1", "external-preservation/1"}:
            raise fail("INPUT_BINDING", "未知参考准备策略")
        strategy = cast(
            Literal["t1-clock-quantization/1", "external-preservation/1"], strategy_value
        )
        fresh = inspect_source(
            reference.path,
            original.artifact_id,
            target_frame_rate=rate,
            progress=None if progress is None else progress.progress,
        )
    if fresh.findings:
        raise fail("ADMISSION_REJECTED", fresh.findings[0].message)
    if report.video.frame_count != fresh.video.frame_count:
        raise fail("INPUT_BINDING", "工作参考不是保内容原件帧数，需作为新工作源重新导入")
    audio = tuple(item for item in inputs if item.port_id == "audio_sources")
    if len(audio) != 1 or audio[0].artifact_id != original.artifact_id or audio[0].ordinal != 0:
        raise fail("AUDIO_BINDING", "当前音频专项仅接受 ordinal=0 的原件载体")
    if audio[0].path != original.path:
        raise fail("AUDIO_BINDING", "原音频载体路径不一致")
    if (parameters.audio_policy == "none" and report.audio) or (
        parameters.audio_policy == "original" and not report.audio
    ):
        raise fail("AUDIO_POLICY", "无音频策略必须与原件实际音轨布局一致")
    assert report.video.first_pts is not None and fresh.video.first_pts is not None
    tracks = _audio_bindings(report)
    return SourceGate(
        original_media_artifact_id=original.artifact_id,
        reference_media_artifact_id=reference.artifact_id,
        diagnosis_artifact_id=diagnosis.artifact_id,
        source_frame_count=fresh.video.frame_count,
        frame_rate=rate,
        original_video_start=report.video.first_pts,
        reference_start=fresh.video.first_pts,
        geometry=SourceGeometry(width=fresh.video.width, height=fresh.video.height),
        signal=SourceSignal(),
        preparation_strategy=strategy,
        audio_policy=parameters.audio_policy,
        audio_bindings=(AudioBinding(artifact_id=original.artifact_id, ordinal=0, tracks=tracks),),
    )


def _audio_bindings(report: DiagnosticReport) -> tuple[AudioTrackBinding, ...]:
    assert report.video.first_pts is not None
    origin = Fraction(report.video.first_pts)
    tracks: list[AudioTrackBinding] = []
    for audio in report.audio:
        if audio.start_time is None or audio.end_time is None:
            raise fail("AUDIO_BINDING", "音轨缺少有效首尾时刻")
        tracks.append(
            AudioTrackBinding(
                stream_index=audio.stream_index,
                codec=audio.codec,
                sample_rate=audio.sample_rate,
                channels=audio.channels,
                channel_layout=audio.channel_layout,
                start_time=audio.start_time,
                end_time=audio.end_time,
                sample_count=audio.sample_count,
                relative_start=rational(Fraction(audio.start_time) - origin),
                relative_end=rational(Fraction(audio.end_time) - origin),
            )
        )
    return tuple(tracks)


def admission(context: PythonAdapterContext) -> PythonAdapterResult:
    """输出工作参考只读引用和 gate；正常源不为进入工作流复制整片。"""
    require_definition_role(context.definition, "admission")
    parameters = AdmissionParameters.model_validate(dict(context.node.parameters))
    with progress_log(context.stdout_log_path), stage("admission"):
        gate = make_gate(context.inputs, parameters, progress=context)
    reference = single_input(context.inputs, "reference_media")
    target = next(item.path for item in context.outputs if item.port_id == "gate")
    with target.open("x", encoding="utf-8") as stream:
        stream.write(gate.model_dump_json(indent=2))
    return PythonAdapterResult(
        outputs=(
            ProducedOutput("video", reference.path, allow_external=True),
            ProducedOutput("gate", target),
        ),
        media_summary={
            "source_frame_count": gate.source_frame_count,
            "frame_rate": gate.frame_rate,
            "audio_count": sum(len(item.tracks) for item in gate.audio_bindings),
        },
    )
