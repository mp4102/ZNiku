"""执行新色彩合同的原件、完整诊断、保内容准备与显式工作解释准入。"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import Literal, cast

from zniku.runtime import ProducedOutput, PythonAdapterContext, PythonAdapterResult, RunnerInput
from zniku.source_preparation.adapters import single_input
from zniku.source_preparation.inspection import checked_file, file_stat, read_header
from zniku.source_preparation.models import (
    AudioBinding,
    AudioTrackBinding,
    DiagnosticParameters,
    SourceGeometry,
    SourceParameters,
    rational,
)
from zniku.source_preparation.process import fail
from zniku.source_preparation.progress import progress_log, stage

from . import models
from .contracts import check_diagnosis, namespace
from .definitions import require_definition_role
from .inspection import inspect_source
from .models import (
    AdmissionParameters,
    DiagnosticReport,
    PrepareParameters,
    SourceGate,
    summary_for_report,
)
from .policy import resolve_interpretation
from .preservation import create_t1_candidate, verify_preservation


def source(context: PythonAdapterContext) -> PythonAdapterResult:
    """登记当前实际原件路径；不复制、不要求它先满足工作源合同。"""
    require_definition_role(context.definition, "source")
    params = SourceParameters.model_validate(dict(context.node.parameters))
    path = checked_file(Path(params.source_path))
    read_header(path, progress=context.progress)
    if context.inputs or len(context.outputs) != 1:
        raise fail("SOURCE_SHAPE", "原件入口 shape 无效")
    return PythonAdapterResult(
        outputs=(ProducedOutput("media", path, allow_external=True),),
        media_summary={"original_preserved": True},
    )


def diagnostics(context: PythonAdapterContext) -> PythonAdapterResult:
    """全部完整观察完成后排他写新报告；问题报告不等于工作源准入。"""
    require_definition_role(context.definition, "diagnostics")
    params = DiagnosticParameters.model_validate(dict(context.node.parameters))
    original = single_input(context.inputs, "original_media")
    with progress_log(context.stdout_log_path):
        report = inspect_source(
            original.path,
            original.artifact_id,
            target_frame_rate=params.target_frame_rate,
            progress=context.progress,
        )
    target = next(item.path for item in context.outputs if item.port_id == "diagnosis")
    with target.open("x", encoding="utf-8") as stream:
        stream.write(report.model_dump_json(indent=2))
    return PythonAdapterResult(media_summary=summary_for_report(report))


def video_prepare(context: PythonAdapterContext) -> PythonAdapterResult:
    """晋级前不写候选；晋级后也必须在 staging 内完成独立保内容验证。"""
    require_definition_role(context.definition, "builtin")
    params = PrepareParameters.model_validate(dict(context.node.parameters))
    if not models.T1_PROMOTED:
        raise fail("STRATEGY_NOT_PROMOTED", "新色彩 T1 尚未完成独立代表及留出验收")
    original = single_input(context.inputs, "original_media")
    diagnosis = single_input(context.inputs, "diagnosis")
    report = check_diagnosis(original, diagnosis)
    staging = context.work_dir / "staging"
    staging.mkdir(exist_ok=False)
    candidate = staging / "source-prepared.mkv"
    with progress_log(context.stdout_log_path), stage("remux"):
        create_t1_candidate(
            original.path,
            candidate,
            report,
            target_frame_rate=params.target_frame_rate,
            progress=context.progress,
        )
        verify_preservation(
            original.path,
            candidate,
            report,
            target_frame_rate=params.target_frame_rate,
            progress=context.progress,
        )
    target = next(item.path for item in context.outputs if item.port_id == "media")
    if target.exists():
        raise fail("OUTPUT_CONFLICT", "正式候选输出已存在")
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


def _audio_bindings(report: DiagnosticReport) -> tuple[AudioTrackBinding, ...]:
    assert report.video.first_pts is not None
    origin = Fraction(report.video.first_pts)
    result = []
    for audio in report.audio:
        if audio.start_time is None or audio.end_time is None:
            raise fail("AUDIO_BINDING", "音轨缺少实际首尾时刻")
        result.append(
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
    return tuple(result)


def make_gate(
    inputs: tuple[RunnerInput, ...],
    params: AdmissionParameters,
    *,
    context: PythonAdapterContext | None = None,
) -> SourceGate:
    """先核对真实参考/音频，再应用受限解释；不能将猜测写回原始观察。"""
    original, reference, diagnosis = (
        single_input(inputs, name) for name in ("original_media", "reference_media", "diagnosis")
    )
    report = check_diagnosis(original, diagnosis)
    rate = params.target_frame_rate or report.target_frame_rate
    if rate is None or rate != report.target_frame_rate:
        raise fail("INPUT_BINDING", "准入目标率必须来自当前完整诊断")
    if any("_AUDIO_" in issue.code for issue in report.findings):
        raise fail("AUDIO_ADMISSION", "原件音频存在未解决的问题")
    strategy: Literal["original", "t1-clock-quantization/2", "external-preservation/2"] = "original"
    if reference.artifact_id == original.artifact_id:
        if reference.path != original.path:
            raise fail("INPUT_BINDING", "同一原件身份路径不同")
        fresh = report
    else:
        data = namespace(reference)
        if (
            data.get("role") != "prepared"
            or data.get("original_media_artifact_id") != original.artifact_id
            or data.get("diagnosis_artifact_id") != diagnosis.artifact_id
            or data.get("verified_stat") != file_stat(reference.path).model_dump(mode="json")
        ):
            raise fail("INPUT_BINDING", "参考不是当前原件/诊断的已验证新工作副本")
        value = data.get("strategy_id")
        if value not in {"t1-clock-quantization/2", "external-preservation/2"}:
            raise fail("INPUT_BINDING", "未知新版准备策略")
        strategy = cast(Literal["t1-clock-quantization/2", "external-preservation/2"], value)
        fresh = inspect_source(
            reference.path,
            original.artifact_id,
            target_frame_rate=rate,
            progress=None if context is None else context.progress,
        )
    blocking = [
        issue for issue in fresh.findings if issue.code != "E_SOURCE_PREPARATION_COLOR_UNSPECIFIED"
    ]
    if blocking:
        raise fail("ADMISSION_REJECTED", blocking[0].message)
    if report.video.frame_count != fresh.video.frame_count:
        raise fail("INPUT_BINDING", "参考不再保持原件帧数")
    audio = tuple(item for item in inputs if item.port_id == "audio_sources")
    if (
        len(audio) != 1
        or audio[0].artifact_id != original.artifact_id
        or audio[0].path != original.path
        or audio[0].ordinal != 0
    ):
        raise fail("AUDIO_BINDING", "当前版本只接受明确绑定的原件音频载体")
    if (params.audio_policy == "none") != (not report.audio):
        raise fail("AUDIO_POLICY", "无音频策略不能丢弃实际音轨")
    working, basis = resolve_interpretation(fresh.observed_signal, params.interpretation_policy)
    assert report.video.first_pts is not None and fresh.video.first_pts is not None
    return SourceGate(
        original_media_artifact_id=original.artifact_id,
        reference_media_artifact_id=reference.artifact_id,
        diagnosis_artifact_id=diagnosis.artifact_id,
        source_frame_count=fresh.video.frame_count,
        frame_rate=rate,
        original_video_start=report.video.first_pts,
        reference_start=fresh.video.first_pts,
        geometry=SourceGeometry(width=fresh.video.width, height=fresh.video.height),
        working_signal=working,
        observed_signal=fresh.observed_signal,
        interpretation_policy=params.interpretation_policy,
        basis=basis,
        preparation_strategy=strategy,
        audio_policy=params.audio_policy,
        audio_bindings=(
            AudioBinding(
                artifact_id=original.artifact_id, ordinal=0, tracks=_audio_bindings(report)
            ),
        ),
    )


def admission(context: PythonAdapterContext) -> PythonAdapterResult:
    """准入只生成引用和 /2 gate，操作者解释不会改写媒体。"""
    require_definition_role(context.definition, "admission")
    params = AdmissionParameters.model_validate(dict(context.node.parameters))
    with progress_log(context.stdout_log_path), stage("admission"):
        gate = make_gate(context.inputs, params, context=context)
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
            "interpretation_policy": gate.interpretation_policy,
        },
    )
