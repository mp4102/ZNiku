"""普通只读源、单次检查、显式 FFV1 重定时及轻量准入的受信适配器。

不接收命令文本，不改原件；生成先停留在 staging，验证失败不登记，取消只能新 attempt 重试。
"""

from __future__ import annotations

import shutil
from fractions import Fraction
from pathlib import Path
from typing import Literal, cast

from zniku.avenhance_v27.probe import probe_header
from zniku.runtime import ProducedOutput, PythonAdapterContext, PythonAdapterResult, RunnerInput
from zniku.runtime.progress import ProgressReporter

from .adapters import single_input
from .inspection import checked_file, file_stat, read_header
from .models import AudioBinding, AudioTrackBinding, SourceGeometry, rational
from .preservation import _ffmpeg_input
from .process import fail, stream_process
from .progress import progress_log, sample, stage
from .work_contracts import (
    check_diagnosis,
    namespace,
    read_reference_report,
    validate_audio_sources,
)
from .work_definitions import require_definition_role
from .work_inspection import inspect_source
from .work_models import (
    AdmissionParameters,
    DiagnosticParameters,
    PrepareParameters,
    SourceParameters,
    WorkDiagnosticReport,
    WorkSourceGate,
    observed_rate_matches,
    resolve_interpretation,
    summary_for_report,
    work_copy_estimate_bytes,
)


def source(context: PythonAdapterContext) -> PythonAdapterResult:
    """原件只读入口只检查容器可读，工作兼容性由后续普通诊断决定。"""
    require_definition_role(context.definition, "source")
    params = SourceParameters.model_validate(dict(context.node.parameters))
    path = checked_file(Path(params.source_path))
    read_header(path, progress=context.progress)
    if context.inputs or len(context.outputs) != 1:
        raise fail("SOURCE_SHAPE", "只读源必须无输入且只产生一个媒体引用")
    return PythonAdapterResult(
        outputs=(ProducedOutput("media", path, allow_external=True),),
        media_summary={"mode": "readonly_original", "original_preserved": True},
    )


def diagnostics(context: PythonAdapterContext) -> PythonAdapterResult:
    """正常、不支持、需转换均是完成的分类；工具失败不写成功报告。"""
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
    target = next(o.path for o in context.outputs if o.port_id == "diagnosis")
    with target.open("x", encoding="utf-8") as stream:
        stream.write(report.model_dump_json(indent=2))
    return PythonAdapterResult(media_summary=summary_for_report(report))


def create_work_candidate(
    original: Path,
    destination: Path,
    report: WorkDiagnosticReport,
    *,
    target_frame_rate: str,
    progress: ProgressReporter | None = None,
) -> WorkDiagnosticReport:
    """保留实际解码帧顺序和 N，重新建立所选时钟；不声称还原原时钟或像素恒等审计。"""
    if (
        report.status != "preparation_required"
        or report.candidate_strategies != ("frame-retime-ffv1/1",)
        or report.target_frame_rate != target_frame_rate
    ):
        raise fail("STRATEGY_NOT_APPLICABLE", "当前诊断不支持此显式保帧工作准备路线")
    video = report.video
    assert video is not None
    original = checked_file(original)
    if file_stat(original) != report.final_stat:
        raise fail("SOURCE_CHANGED", "原件已改变，需要新检查")
    if (
        destination.exists()
        or destination.suffix.lower() != ".mkv"
        or not destination.parent.is_dir()
    ):
        raise fail("OUTPUT_CONFLICT", "工作候选必须位于新的当前 attempt MKV 路径")
    # 先按原始像素体积给出保守容量门禁；压缩率不作为保证，不创建巨型 ProRes。
    estimate = work_copy_estimate_bytes(video)
    if shutil.disk_usage(destination.parent).free < estimate:
        raise fail("SPACE_INSUFFICIENT", "工作盘不足保守 FFV1 工作副本预算；原件保留")
    rate = Fraction(target_frame_rate)
    input_argv = _ffmpeg_input(original)
    input_argv.insert(input_argv.index("-i"), "-noautorotate")
    # 输入侧 -r 明确采用操作者选择的新时间解释，也更新解码/滤镜的声明 FPS。
    # 输出仍 passthrough；不使用会补删帧的输出侧 -r。实际 N/PTS 和声明另行验收。
    position = input_argv.index("-i")
    input_argv[position:position] = ["-r", target_frame_rate]
    count = 0

    def consume(raw: bytes) -> None:
        nonlocal count
        if raw.startswith(b"frame="):
            count = int(raw.split(b"=", 1)[1].strip())
            sample(count, video.frame_count)

    with stage("work_retime", "frames"):
        stream_process(
            [
                *input_argv,
                "-map",
                f"0:{video.stream_index}",
                "-an",
                "-sn",
                "-dn",
                "-map_metadata",
                "-1",
                "-map_chapters",
                "-1",
                "-vf",
                f"settb=expr={rate.denominator}/{rate.numerator},setpts=N",
                "-fps_mode",
                "passthrough",
                "-enc_time_base",
                f"{rate.denominator}/{rate.numerator}",
                "-c:v",
                "ffv1",
                "-level",
                "3",
                "-g",
                "1",
                "-slicecrc",
                "1",
                "-pix_fmt",
                video.pixel_format,
                "-progress",
                "pipe:1",
                "-nostats",
                "-n",
                str(destination),
            ],
            consume=consume,
            progress=progress,
        )
    if file_stat(original) != report.final_stat:
        raise fail("SOURCE_CHANGED", "准备期间原件变化，候选不登记")
    fresh = inspect_source(
        destination,
        report.original_media_artifact_id,
        target_frame_rate=target_frame_rate,
        progress=progress,
    )
    if (
        fresh.status != "direct"
        or fresh.video is None
        or fresh.video.frame_count != video.frame_count
        or fresh.audio
    ):
        raise fail("WORK_REFERENCE_INVALID", "候选未通过明确时钟、完整帧数和无音频输出检查")
    if fresh.video.codec != "ffv1" or (
        fresh.video.width,
        fresh.video.height,
        fresh.video.pixel_format,
    ) != (video.width, video.height, video.pixel_format):
        raise fail("WORK_REFERENCE_INVALID", "工作副本的编码/几何/像素表示不符合声明")
    header = probe_header(destination).video
    if not all(
        observed_rate_matches(rate, value)
        for value in (
            header.frame_rate,
            header.avg_frame_rate,
            header.r_frame_rate,
        )
    ):
        raise fail("WORK_REFERENCE_INVALID", "工作副本的声明 FPS 与所选实际时间轴不一致")
    return fresh


def video_prepare(context: PythonAdapterContext) -> PythonAdapterResult:
    """显式确认后才写 FFV1；旧 T1 开关和验收结果均不参与普通策略。"""
    require_definition_role(context.definition, "builtin")
    params = PrepareParameters.model_validate(dict(context.node.parameters))
    original, diagnosis = (
        single_input(context.inputs, "original_media"),
        single_input(context.inputs, "diagnosis"),
    )
    report = check_diagnosis(original, diagnosis)
    staging = context.work_dir / "staging"
    staging.mkdir(exist_ok=False)
    candidate = staging / "source-prepared.mkv"
    with progress_log(context.stdout_log_path):
        fresh = create_work_candidate(
            original.path,
            candidate,
            report,
            target_frame_rate=params.target_frame_rate,
            progress=context.progress,
        )
    target = next(o.path for o in context.outputs if o.port_id == "media")
    if target.exists():
        raise fail("OUTPUT_CONFLICT", "正式工作输出已存在，不覆盖")
    candidate.rename(target)
    return PythonAdapterResult(
        producer_metadata={
            "media": {
                "original_media_artifact_id": original.artifact_id,
                "diagnosis_artifact_id": diagnosis.artifact_id,
                "source_stat": report.final_stat.model_dump(mode="json"),
                "candidate_stat": file_stat(target).model_dump(mode="json"),
                "observation": fresh.model_dump(mode="json"),
                "frame_count_preserved": True,
            }
        },
        media_summary={
            "frame_count_preserved": True,
            "timing_changed": True,
            "real_acceptance": "pending_real_acceptance",
        },
    )


def make_gate(
    inputs: tuple[RunnerInput, ...],
    parameters: AdmissionParameters,
    *,
    progress: ProgressReporter | None = None,
) -> WorkSourceGate:
    """只消费已完成观察并轻量复核；外部新参考以自身实际 N/音频重建后续规划。"""
    original, reference, diagnosis = (
        single_input(inputs, port) for port in ("original_media", "reference_media", "diagnosis")
    )
    report = check_diagnosis(original, diagnosis)
    strategy: Literal["original", "frame-retime-ffv1/1", "external-new-reference/1"]
    if original.artifact_id == reference.artifact_id:
        if original.path != reference.path:
            raise fail("INPUT_BINDING", "同一原件身份不能绑定另一路径")
        fresh = report
        strategy = "original"
    else:
        fresh = read_reference_report(reference)
        data = namespace(reference)
        strategy_value = data.get("strategy_id")
        if (
            data.get("original_media_artifact_id") != original.artifact_id
            or data.get("diagnosis_artifact_id") != diagnosis.artifact_id
            or strategy_value not in {"frame-retime-ffv1/1", "external-new-reference/1"}
        ):
            raise fail("INPUT_BINDING", "工作参考不属于当前原件及诊断")
        strategy = cast(Literal["frame-retime-ffv1/1", "external-new-reference/1"], strategy_value)
    rate = parameters.target_frame_rate or fresh.target_frame_rate
    if rate is None:
        raise fail("RATE_SELECTION_REQUIRED", "请明确选择工作参考的精确 FPS")
    if rate != fresh.target_frame_rate:
        raise fail("INPUT_BINDING", "准入目标与实际参考观察不符")
    if (
        fresh.status != "direct"
        or fresh.video is None
        or fresh.observed_signal is None
        or fresh.video.first_pts is None
    ):
        raise fail(
            "ADMISSION_REJECTED",
            fresh.findings[0].message if fresh.findings else "参考不符合当前处理器要求",
        )
    working_signal, basis = resolve_interpretation(
        fresh.observed_signal, parameters.interpretation_policy
    )
    if strategy == "frame-retime-ffv1/1" and (
        report.video is None or report.video.frame_count != fresh.video.frame_count
    ):
        raise fail("INPUT_BINDING", "内置保帧路线的帧数改变")
    if strategy == "external-new-reference/1" and parameters.audio_policy != "reference":
        raise fail("AUDIO_POLICY", "外部新参考必须明确使用其自身音频，不能隐式沿用旧原件")
    if strategy == "frame-retime-ffv1/1" and parameters.audio_policy == "reference":
        raise fail("AUDIO_POLICY", "内置副本仅含视频；音频必须保持实际原件载体")
    carrier = reference if parameters.audio_policy == "reference" else original
    audio_report = fresh if parameters.audio_policy == "reference" else report
    if audio_report.video is None or audio_report.video.first_pts is None:
        raise fail("AUDIO_POLICY", "所选音频载体缺少可解释视频起点")
    if parameters.audio_policy == "none" and audio_report.audio:
        raise fail("AUDIO_POLICY", "none 仅适用真实无音轨载体，不可静默删除音轨")
    issues = [
        f for f in audio_report.findings if "_AUDIO_" in f.code or f.code.endswith("DECODE_ERRORS")
    ]
    if issues:
        raise fail("AUDIO_ADMISSION", issues[0].message)
    audio_inputs = tuple(i for i in inputs if i.port_id == "audio_sources")
    if (
        len(audio_inputs) != 1
        or audio_inputs[0].artifact_id != carrier.artifact_id
        or audio_inputs[0].path != carrier.path
        or audio_inputs[0].ordinal != 0
    ):
        raise fail("AUDIO_BINDING", "音频直接输入必须指向显式策略选择的实际载体")
    origin = Fraction(audio_report.video.first_pts)
    tracks = []
    for audio in audio_report.audio:
        assert audio.start_time is not None and audio.end_time is not None
        tracks.append(
            AudioTrackBinding(
                stream_index=audio.stream_index,
                codec=audio.codec,
                sample_rate=audio.sample_rate,
                channels=audio.channels,
                channel_layout=audio.channel_layout,
                start_time=audio.start_time,
                end_time=audio.end_time,
                relative_start=rational(Fraction(audio.start_time) - origin),
                relative_end=rational(Fraction(audio.end_time) - origin),
                sample_count=audio.sample_count,
            )
        )
    gate = WorkSourceGate(
        original_media_artifact_id=original.artifact_id,
        reference_media_artifact_id=reference.artifact_id,
        diagnosis_artifact_id=diagnosis.artifact_id,
        source_frame_count=fresh.video.frame_count,
        frame_rate=rate,
        original_video_start=None if report.video is None else report.video.first_pts,
        reference_start=fresh.video.first_pts,
        geometry=SourceGeometry(width=fresh.video.width, height=fresh.video.height),
        working_signal=working_signal,
        observed_signal=fresh.observed_signal,
        interpretation_policy=parameters.interpretation_policy,
        basis=basis,
        preparation_strategy=strategy,
        audio_policy=parameters.audio_policy,
        audio_video_start=rational(origin),
        audio_bindings=(
            AudioBinding(artifact_id=carrier.artifact_id, ordinal=0, tracks=tuple(tracks)),
        ),
        reference_stat=fresh.final_stat,
        audio_source_stat=audio_report.final_stat,
    )
    read_header(reference.path, progress=progress)
    if file_stat(reference.path) != gate.reference_stat:
        raise fail("REFERENCE_CHANGED", "轻量复核期间工作参考变化")
    validate_audio_sources(gate, audio_inputs, progress=progress)
    return gate


def admission(context: PythonAdapterContext) -> PythonAdapterResult:
    """引用已准备参考和本节点 Gate；正常源不复制、不重做完整诊断。"""
    require_definition_role(context.definition, "admission")
    params = AdmissionParameters.model_validate(dict(context.node.parameters))
    with progress_log(context.stdout_log_path), stage("admission"):
        gate = make_gate(context.inputs, params, progress=context.progress)
    reference = single_input(context.inputs, "reference_media")
    target = next(o.path for o in context.outputs if o.port_id == "gate")
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
        },
    )
