"""定义素材检查与工作参考的严格局部数据合同。

报告只记录当前节点观察，gate 只绑定直接输入；不生成未来 Artifact 身份或全局证明链。
本实现由 ZNIKU 独立编写，仅共享问题分类，不导入或复制外部 skill 的运行代码。
"""

from __future__ import annotations

from fractions import Fraction
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from zniku.runtime.models import RandomId

NAMESPACE = "zniku.source.prepared"
SOURCE_PREPARATION_VERSION = "0.3.4"
T1_STRATEGY = "t1-clock-quantization/1"
# 晋级属于产品发布决定，不能通过 Graph 参数或外部报告开启。
T1_PROMOTED = False
type Rational = Annotated[str, StringConstraints(pattern=r"^-?[0-9]+/[1-9][0-9]*$", max_length=64)]
type Positive = Annotated[int, Field(ge=1)]
type Count = Annotated[int, Field(ge=0)]


def rational(value: Fraction) -> str:
    """保留精确有理数，不使用浮点媒体时间。"""
    return f"{value.numerator}/{value.denominator}"


class PreparationModel(BaseModel):
    """闭合、不可变模型；JSON 使用严格解析而不容忍未知字段。"""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class FileStat(PreparationModel):
    size: Positive
    mtime_ns: Count


class Finding(PreparationModel):
    code: Annotated[str, StringConstraints(pattern=r"^E_SOURCE_PREPARATION_[A-Z_]+$")]
    message: Annotated[str, StringConstraints(min_length=1, max_length=2048)]


class AudioObservation(PreparationModel):
    stream_index: Count
    codec: str
    sample_rate: Positive
    channels: Positive
    channel_layout: str
    language: str
    profile: str = "unknown"
    title: str = ""
    default: bool = False
    forced: bool = False
    sample_count: Count
    start_time: Rational | None
    end_time: Rational | None
    first_packet_time: Rational | None
    skip_samples: Count
    discard_padding: Count
    missing_pts: Count
    discontinuities: Count
    decode_errors: bool


class InitialBitstreamClock(PreparationModel):
    """初始 SPS 观察范围明确，不冒充全片码流头恒定性证明。"""

    scope: Literal["initial-sps"] = "initial-sps"
    status: Literal["not_applicable", "not_fixed", "fixed", "unavailable", "ambiguous"]
    fixed_frame_rate: Rational | None = None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (self.status == "fixed") != (self.fixed_frame_rate is not None):
            raise ValueError("仅明确固定时钟可携带初始 VUI FPS")
        return self


class VideoObservation(PreparationModel):
    stream_index: Count
    codec: str
    width: Positive
    height: Positive
    pixel_format: str
    sample_aspect_ratio: str
    field_order: str
    color_range: str
    color_space: str
    color_transfer: str
    color_primaries: str
    chroma_location: str
    rotation: str
    hdr_side_data: bool = False
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
    bitstream_clock: InitialBitstreamClock = InitialBitstreamClock(status="unavailable")


class DiagnosticReport(PreparationModel):
    schema_version: Literal["zniku.source.prepared.diagnosis/1"] = (
        "zniku.source.prepared.diagnosis/1"
    )
    original_media_artifact_id: RandomId
    source_stat: FileStat
    final_stat: FileStat
    complete: Literal[True] = True
    check_scope: Literal["full-eof"] = "full-eof"
    ffprobe_version: str
    container: str
    container_duration: Rational | None
    extra_streams: Count
    chapter_count: Count
    target_frame_rate: Rational | None
    video: VideoObservation
    audio: tuple[AudioObservation, ...]
    findings: tuple[Finding, ...]
    candidate_strategies: tuple[Literal["t1-clock-quantization/1"], ...]
    elapsed_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]

    @model_validator(mode="after")
    def complete_input(self) -> Self:
        if self.source_stat != self.final_stat:
            raise ValueError("检查期间输入发生变化")
        if self.target_frame_rate is not None and Fraction(self.target_frame_rate) <= 0:
            raise ValueError("目标率必须为正")
        return self


class AudioTrackBinding(PreparationModel):
    stream_index: Count
    codec: str
    sample_rate: Positive
    channels: Positive
    channel_layout: str
    start_time: Rational
    end_time: Rational
    relative_start: Rational
    relative_end: Rational
    sample_count: Positive


class AudioBinding(PreparationModel):
    artifact_id: RandomId
    ordinal: Count
    tracks: tuple[AudioTrackBinding, ...]


class SourceGeometry(PreparationModel):
    width: Positive
    height: Positive
    sample_aspect_ratio: Literal["1/1"] = "1/1"


class SourceSignal(PreparationModel):
    color_primaries: Literal["bt709"] = "bt709"
    color_transfer: Literal["bt709"] = "bt709"
    color_space: Literal["bt709"] = "bt709"
    color_range: Literal["tv"] = "tv"
    chroma_location: Literal["left"] = "left"
    field_order: Literal["progressive"] = "progressive"
    rotation: Literal[0] = 0


class SourceGate(PreparationModel):
    schema_version: Literal["zniku.source.prepared.admission/1"] = (
        "zniku.source.prepared.admission/1"
    )
    original_media_artifact_id: RandomId
    reference_media_artifact_id: RandomId
    diagnosis_artifact_id: RandomId
    source_frame_count: Positive
    frame_rate: Rational
    original_video_start: Rational
    reference_start: Rational
    geometry: SourceGeometry
    signal: SourceSignal
    preparation_strategy: Literal["original", "t1-clock-quantization/1", "external-preservation/1"]
    audio_policy: Literal["original", "none"] = "original"
    audio_bindings: tuple[AudioBinding, ...]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        """数据自洽约束不替代媒体检查，但不能接受互相矛盾的身份和相对音频映射。"""
        rate = Fraction(self.frame_rate)
        if not 1 <= rate <= 240 or rational(rate) != self.frame_rate:
            raise ValueError("FPS 必须为 1..240 的约分后正有理数")
        original_reference = self.reference_media_artifact_id == self.original_media_artifact_id
        if original_reference != (self.preparation_strategy == "original"):
            raise ValueError("原件策略必须引用同一原件身份，准备策略必须引用独立工作副本")
        if original_reference and Fraction(self.reference_start) != Fraction(
            self.original_video_start
        ):
            raise ValueError("原件直接引用不能具有不同的起点")
        if len(self.audio_bindings) != 1 or self.audio_bindings[0].artifact_id != (
            self.original_media_artifact_id
        ):
            raise ValueError("当前音频策略必须显式绑定唯一原件载体")
        if tuple(item.ordinal for item in self.audio_bindings) != tuple(
            range(len(self.audio_bindings))
        ):
            raise ValueError("音频输入必须连续有序")
        ids = [item.artifact_id for item in self.audio_bindings]
        if len(set(ids)) != len(ids):
            raise ValueError("音频载体不能重复")
        for item in self.audio_bindings:
            indices = [track.stream_index for track in item.tracks]
            if (not indices and self.audio_policy != "none") or len(set(indices)) != len(indices):
                raise ValueError("音频轨映射必须非空且唯一")
            if indices and self.audio_policy == "none":
                raise ValueError("无音频策略不能携带音轨")
            for track in item.tracks:
                if Fraction(track.end_time) <= Fraction(track.start_time):
                    raise ValueError("有效音频轨结束时刻必须晚于开始时刻")
                origin = Fraction(self.original_video_start)
                if Fraction(track.relative_start) != Fraction(track.start_time) - origin or (
                    Fraction(track.relative_end) != Fraction(track.end_time) - origin
                ):
                    raise ValueError("相对音频时刻必须使用原件视频起点，不能使用修复后起点")
        return self


class SourceParameters(PreparationModel):
    source_path: Annotated[str, StringConstraints(min_length=1, max_length=32760)]


class DiagnosticParameters(PreparationModel):
    target_frame_rate: Rational | None = None

    @field_validator("target_frame_rate")
    @classmethod
    def rate(cls, value: str | None) -> str | None:
        if value is not None:
            rate = Fraction(value)
            if not 1 <= rate <= 240 or rational(rate) != value:
                raise ValueError("目标 FPS 必须为 1..240 的精确约分有理数")
        return value


class PrepareParameters(DiagnosticParameters):
    target_frame_rate: Rational
    strategy_id: Literal["t1-clock-quantization/1"] = "t1-clock-quantization/1"


class ExternalParameters(DiagnosticParameters):
    target_frame_rate: Rational


class AdmissionParameters(DiagnosticParameters):
    audio_policy: Literal["original", "none"] = "original"


def summary_for_report(report: DiagnosticReport) -> dict[str, object]:
    """返回 Studio 可直接呈现的摘要；未晋级策略绝不冒充可用。"""
    available = list(report.candidate_strategies) if T1_PROMOTED else []
    return {
        "schema_version": report.schema_version,
        "status": "compatible" if not report.findings else "needs_preparation",
        "recommended_action": (
            "direct" if not report.findings else "builtin" if available else "external"
        ),
        "findings": [item.model_dump(mode="json") for item in report.findings],
        "source_frame_count": report.video.frame_count,
        "frame_rate": report.target_frame_rate,
        "available_strategies": available,
        "candidate_strategies": list(report.candidate_strategies),
        "audio_count": len(report.audio),
        "target_frame_rate_choices": _frame_rate_choices(report.video),
        "original_preserved": True,
    }


def _frame_rate_choices(video: VideoObservation) -> list[str]:
    """只提供 header 真正声明且可用的有理数，不从近似数猜常见帧率。"""
    choices: set[str] = set()
    for value in (
        video.r_frame_rate,
        video.avg_frame_rate,
        video.bitstream_clock.fixed_frame_rate,
    ):
        if value is None:
            continue
        try:
            rate = Fraction(value)
        except (ValueError, ZeroDivisionError):
            continue
        if 1 <= rate <= 240:
            choices.add(rational(rate))
    return sorted(choices)
