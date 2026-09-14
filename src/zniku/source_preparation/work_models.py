"""普通工作源的闭合观察和准入模型；不声称完整码流审计或保内容恒等。

新 exact 与旧诊断/色彩政策独立；普通重定时明确改变时间，不受 T1 晋级开关影响。
"""

from __future__ import annotations

from fractions import Fraction
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from zniku.runtime.models import RandomId

from .models import (
    AudioBinding,
    AudioObservation,
    Count,
    FileStat,
    Finding,
    Positive,
    PreparationModel,
    Rational,
    SourceGeometry,
    SourceSignal,
    rational,
)
from .models import (
    DiagnosticParameters as RateParameters,
)
from .models import (
    SourceParameters as SourceParameters,
)

SOURCE_PREPARATION_VERSION = "0.3.4-work.1"
NAMESPACE = "zniku.source.prepared.work"
WORK_STRATEGY = "frame-retime-ffv1/1"
type InterpretationPolicy = Literal["declared_only", "operator_confirmed_bt709_limited_left"]
type AudioPolicy = Literal["original", "reference", "none"]
type Decision = Literal["direct", "preparation_required", "unsupported"]
SIGNAL_EXPECTED = {
    "color_primaries": "bt709",
    "color_transfer": "bt709",
    "color_space": "bt709",
    "color_range": "tv",
    "chroma_location": "left",
}


def clock_tolerance(rate: Fraction, time_base: Fraction) -> Fraction:
    """细时间基允许两 tick 量化；粗时间基只接受实测精确网格，不直接拒绝可用时钟。

    此边界选择的是如何解释量化误差，不是旧 T1 对原件漂移的晋级门槛。
    """
    if rate <= 0 or time_base <= 0:
        raise ValueError("FPS 和时间基必须为正")
    tolerance = max(2 * time_base, Fraction(1, 1_000_000))
    return Fraction(0) if tolerance >= 1 / rate / 4 else tolerance


def observed_rate_matches(expected: Fraction, observed: Fraction) -> bool:
    """只容纳 Matroska 整数纳秒周期的表示误差，不能将旧率或近似拍摄率冒充目标率。"""
    return expected > 0 and observed > 0 and abs(1 / expected - 1 / observed) <= Fraction(1, 10**9)


class ProbeSignal(PreparationModel):
    """仅记录 FFprobe 合并观察；null 不区分原始语法缺失与工具不可用。"""

    color_primaries: str | None = None
    color_transfer: str | None = None
    color_space: str | None = None
    color_range: str | None = None
    chroma_location: str | None = None


class FrameVariant(PreparationModel):
    width: Positive
    height: Positive
    pixel_format: str
    sample_aspect_ratio: str
    interlaced: bool
    signal: ProbeSignal
    count: Positive


class ObservedSignal(PreparationModel):
    scope: Literal["ffprobe-header-and-decoded-frames"] = "ffprobe-header-and-decoded-frames"
    header: ProbeSignal
    variants: Annotated[tuple[FrameVariant, ...], Field(max_length=16)]
    missing_fields: tuple[str, ...]
    conflicts: tuple[str, ...]
    changes: tuple[str, ...]
    unsupported: tuple[str, ...]


class SignalBasis(PreparationModel):
    field: str
    value: str
    source: Literal["probe_observation", "operator_confirmation"]


class WorkVideoObservation(PreparationModel):
    stream_index: Count
    codec: str
    width: Positive
    height: Positive
    pixel_format: str
    sample_aspect_ratio: str
    field_order: str
    rotation: str
    r_frame_rate: str
    avg_frame_rate: str
    time_base: Rational
    frame_count: Count
    packet_count: Count
    first_pts: Rational | None
    last_pts: Rational | None
    missing_pts: Count
    duplicate_pts: Count
    backward_pts: Count
    best_effort_differences: Count
    max_clock_error: Rational | None
    decode_errors: bool


def work_copy_estimate_bytes(video: WorkVideoObservation) -> int:
    """按实际采样和存储位深估计未压缩体积，加 10% 和小额封装余量。

    这是容量参考预算，不猜 FFV1 压缩率，也不保证任意编码文件必小于它。
    """
    storage = {
        "yuv420p": Fraction(3, 2),
        "yuv420p10le": Fraction(3),
        "yuv422p": Fraction(2),
        "yuv422p10le": Fraction(4),
        "yuv444p": Fraction(3),
        "yuv444p10le": Fraction(6),
    }
    if video.pixel_format not in storage or video.frame_count <= 0:
        raise ValueError("当前像素格式或帧数没有工作副本预算")
    raw = video.width * video.height * video.frame_count * storage[video.pixel_format]
    estimate = raw * Fraction(11, 10)
    return (estimate.numerator + estimate.denominator - 1) // estimate.denominator + 16 * 1024**2


class WorkDiagnosticReport(PreparationModel):
    schema_version: Literal["zniku.source.prepared.diagnosis/work-1"] = (
        "zniku.source.prepared.diagnosis/work-1"
    )
    original_media_artifact_id: RandomId
    source_stat: FileStat
    final_stat: FileStat
    complete: Literal[True] = True
    inspection_scope: Literal["header_only", "frames_eof"]
    status: Decision
    ffprobe_version: str
    container: str
    container_duration: Rational | None
    extra_streams: Count
    chapter_count: Count
    target_frame_rate: Rational | None
    target_frame_rate_choices: tuple[Rational, ...]
    video: WorkVideoObservation | None
    observed_signal: ObservedSignal | None
    audio: Annotated[tuple[AudioObservation, ...], Field(max_length=16)]
    findings: Annotated[tuple[Finding, ...], Field(max_length=64)]
    warnings: Annotated[tuple[Finding, ...], Field(max_length=64)] = ()
    candidate_strategies: tuple[Literal["frame-retime-ffv1/1"], ...]
    elapsed_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.source_stat != self.final_stat:
            raise ValueError("检查期间输入改变")
        RateParameters(target_frame_rate=self.target_frame_rate)
        if self.inspection_scope == "header_only":
            if self.status != "unsupported" or self.video is not None or self.observed_signal:
                raise ValueError("仅头检查不能声称已完成展示帧扫描")
        elif self.video is None or self.observed_signal is None:
            raise ValueError("全帧观察必须包含视频和色彩聚合")
        elif sum(item.count for item in self.observed_signal.variants) != self.video.frame_count:
            raise ValueError("色彩聚合帧数必须与完整扫描一致")
        return self


class DiagnosticParameters(RateParameters):
    """明确 FPS 或保留待选，不从近似小数猜摄像时钟。"""


class PrepareParameters(RateParameters):
    target_frame_rate: Rational
    strategy_id: Literal["frame-retime-ffv1/1"] = "frame-retime-ffv1/1"
    retime_confirmed: Literal[True]


class ExternalParameters(RateParameters):
    target_frame_rate: Rational
    reference_change_confirmed: Literal[True]


class AdmissionParameters(RateParameters):
    interpretation_policy: InterpretationPolicy = "declared_only"
    audio_policy: AudioPolicy = "original"


def resolve_interpretation(
    observed: ObservedSignal, policy: InterpretationPolicy
) -> tuple[SourceSignal, tuple[SignalBasis, ...]]:
    """仅解释工具未声明项；已观察的冲突、变化和不支持域不能由操作者勾选覆盖。"""
    from .process import fail

    # 摘要不是第二层权威；复核实际值，防止构造出清空 conflicts/missing 的矛盾 Gate。
    for name, expected in SIGNAL_EXPECTED.items():
        frames = {getattr(v.signal, name) for v in observed.variants}
        explicit = {value for value in frames if value is not None}
        head = getattr(observed.header, name)
        missing = not explicit and head is None
        conflict = head is not None and bool(explicit) and explicit != {head}
        changed = len(frames) > 1
        bad = any(value != expected for value in explicit | ({head} if head else set()))
        if (
            missing != (name in observed.missing_fields)
            or conflict != (name in observed.conflicts)
            or changed != (name in observed.changes)
            or bad != (name in observed.unsupported)
        ):
            raise fail("COLOR_REPORT", "工具观察摘要与实际值不自洽")
    if observed.conflicts or observed.changes or observed.unsupported:
        raise fail("COLOR_UNSUPPORTED", "工具观察存在变化、冲突或当前不支持的色彩域")
    if observed.missing_fields and policy != "operator_confirmed_bt709_limited_left":
        raise fail("COLOR_UNSPECIFIED", "请明确确认未声明项的 SDR BT.709 工作解释")
    if policy not in {"declared_only", "operator_confirmed_bt709_limited_left"}:
        raise fail("COLOR_POLICY", "未知工作解释")
    basis = tuple(
        SignalBasis(
            field=name,
            value=value,
            source="operator_confirmation"
            if name in observed.missing_fields
            else "probe_observation",
        )
        for name, value in SIGNAL_EXPECTED.items()
    )
    return SourceSignal(), basis


class WorkSourceGate(PreparationModel):
    """已观察工作参考的直接输入绑定；音频相对其真实载体视频首点定位。"""

    schema_version: Literal["zniku.source.prepared.admission/work-1"] = (
        "zniku.source.prepared.admission/work-1"
    )
    original_media_artifact_id: RandomId
    reference_media_artifact_id: RandomId
    diagnosis_artifact_id: RandomId
    source_frame_count: Positive
    frame_rate: Rational
    original_video_start: Rational | None
    reference_start: Rational
    geometry: SourceGeometry
    working_signal: SourceSignal
    observed_signal: ObservedSignal
    interpretation_policy: InterpretationPolicy
    basis: tuple[SignalBasis, ...]
    preparation_strategy: Literal["original", "frame-retime-ffv1/1", "external-new-reference/1"]
    audio_policy: AudioPolicy
    audio_video_start: Rational
    audio_bindings: Annotated[tuple[AudioBinding, ...], Field(min_length=1, max_length=1)]
    reference_stat: FileStat
    audio_source_stat: FileStat

    @property
    def signal(self) -> SourceSignal:
        """供现有纯规划助手读取工作解释；不序列化为额外实测事实。"""
        return self.working_signal

    @model_validator(mode="after")
    def consistent(self) -> Self:
        RateParameters(target_frame_rate=self.frame_rate)
        direct = self.original_media_artifact_id == self.reference_media_artifact_id
        if direct != (self.preparation_strategy == "original"):
            raise ValueError("直接策略必须引用原件，准备策略必须独立引用")
        if direct and self.original_video_start != self.reference_start:
            raise ValueError("直接策略原件和工作起点必须相同")
        signal, basis = resolve_interpretation(self.observed_signal, self.interpretation_policy)
        if signal != self.working_signal or basis != self.basis:
            raise ValueError("工作解释依据不一致")
        if sum(v.count for v in self.observed_signal.variants) != self.source_frame_count:
            raise ValueError("准入帧数与观察不符")
        for variant in self.observed_signal.variants:
            if (
                variant.width != self.geometry.width
                or variant.height != self.geometry.height
                or variant.sample_aspect_ratio not in {"1:1", "1/1"}
                or variant.interlaced
            ):
                raise ValueError("准入几何与帧观察不符")
        binding = self.audio_bindings[0]
        carrier = (
            self.reference_media_artifact_id
            if self.audio_policy == "reference"
            else (self.original_media_artifact_id)
        )
        if binding.artifact_id != carrier or binding.ordinal != 0:
            raise ValueError("音频载体必须匹配显式策略")
        origin = (
            self.reference_start if self.audio_policy == "reference" else self.original_video_start
        )
        if origin is None or self.audio_video_start != origin:
            raise ValueError("音频起点必须来自实际所选载体")
        if (
            self.preparation_strategy == "external-new-reference/1"
            and self.audio_policy != "reference"
        ):
            raise ValueError("新外部参考仅支持其自身音频关系，不暗中沿用原音轨")
        if self.preparation_strategy == "frame-retime-ffv1/1" and self.audio_policy == "reference":
            raise ValueError("内置视频工作副本不能替代原音频载体")
        if self.audio_policy == "none" and binding.tracks:
            raise ValueError("none 策略不得携带音轨")
        indices = tuple(track.stream_index for track in binding.tracks)
        if len(set(indices)) != len(indices):
            raise ValueError("音轨索引不能重复")
        for track in binding.tracks:
            if Fraction(track.end_time) <= Fraction(track.start_time) or (
                Fraction(track.relative_start) != Fraction(track.start_time) - Fraction(origin)
                or Fraction(track.relative_end) != Fraction(track.end_time) - Fraction(origin)
            ):
                raise ValueError("音频相对首尾必须来自真实载体起点")
        return self


def summary_for_report(report: WorkDiagnosticReport) -> dict[str, object]:
    """单一服务摘要；普通路线不冒称保内容能力，不自动授权转换。"""
    signal = report.observed_signal
    can_interpret = bool(
        signal
        and signal.missing_fields
        and not (signal.conflicts or signal.changes or signal.unsupported)
    )
    video = report.video
    duration = None
    if video and video.first_pts is not None and video.last_pts is not None:
        duration = rational(Fraction(video.last_pts) - Fraction(video.first_pts))
    working_duration = None
    if video and report.target_frame_rate:
        working_duration = rational(video.frame_count / Fraction(report.target_frame_rate))
    confirmations: list[str] = []
    if can_interpret:
        confirmations.append("color_interpretation")
    if report.target_frame_rate is None:
        confirmations.append("target_frame_rate")
    if report.status == "preparation_required":
        confirmations.append("retime")
    if report.status == "unsupported":
        confirmations.append("external_reference")
    return {
        "schema_version": report.schema_version,
        "status": report.status,
        "recommended_action": {
            "direct": "direct",
            "preparation_required": "builtin",
            "unsupported": "external",
        }[report.status],
        "findings": [f.model_dump(mode="json") for f in report.findings],
        "warnings": [f.model_dump(mode="json") for f in report.warnings],
        "source_frame_count": None if video is None else video.frame_count,
        "frame_rate": report.target_frame_rate,
        "target_frame_rate_choices": list(report.target_frame_rate_choices),
        "audio_count": len(report.audio),
        "available_strategies": list(report.candidate_strategies),
        "candidate_strategies": list(report.candidate_strategies),
        "original_preserved": True,
        "can_interpret": can_interpret,
        "can_preserve": False,
        "direct_eligible_with_interpretation": report.status == "direct",
        "inspection_scope": report.inspection_scope,
        "missing_color_fields": list(signal.missing_fields) if signal else [],
        "confirmations_required": confirmations,
        "preparation_impacts": {
            "frame_count_preserved": True,
            "timing_changed": True,
            "source_frame_span": duration,
            "working_duration": working_duration,
            "video_only": True,
            "audio_source": "original",
            "storage": "full_ffv1_work_copy",
            "real_acceptance": "pending_real_acceptance",
            "uncompressed_reference_budget_bytes": (
                work_copy_estimate_bytes(video)
                if video is not None and video.frame_count > 0
                else None
            ),
        },
    }
