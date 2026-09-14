"""定义色彩政策新版本的分层事实与工作解释，不改旧报告或准入含义。

原始声明、规范推导、操作者工作解释分别保存；所有聚合都有固定预算，不保存全片帧表。
"""

from __future__ import annotations

from fractions import Fraction
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from zniku.runtime.models import RandomId
from zniku.source_preparation import models as previous
from zniku.source_preparation.models import (
    AudioBinding,
    AudioObservation,
    Count,
    DiagnosticParameters,
    FileStat,
    Finding,
    Positive,
    PreparationModel,
    Rational,
    SourceGeometry,
    SourceSignal,
    VideoObservation,
)

SOURCE_PREPARATION_VERSION = "0.3.4-color.1"
NAMESPACE = "zniku.source.prepared.color"
T1_STRATEGY = "t1-clock-quantization/2"
T1_PROMOTED = False
POLICY_VERSION = "color-interpretation/1"
type InterpretationPolicy = Literal["declared_only", "operator_confirmed_bt709_limited_left"]
type SignalName = Literal[
    "color_primaries", "color_transfer", "color_space", "color_range", "chroma_location"
]
SIGNAL_NAMES: tuple[SignalName, ...] = (
    "color_primaries",
    "color_transfer",
    "color_space",
    "color_range",
    "chroma_location",
)
EXPECTED_SIGNAL: dict[SignalName, str] = dict(
    zip(SIGNAL_NAMES, ("bt709", "bt709", "bt709", "tv", "left"), strict=True)
)
type ShortText = Annotated[str, StringConstraints(max_length=256)]
type Issues = Annotated[tuple[ShortText, ...], Field(max_length=32)]


class ColorField(PreparationModel):
    """一个明确值域中的字段；raw_value 不跨容器/码流解释。"""

    field: SignalName
    state: Literal["explicit", "explicit_unspecified", "absent", "unavailable"]
    raw_value: int | ShortText | None = None
    effective_value: ShortText | None = None
    basis: Literal["declared", "h264-default", "unspecified", "unavailable"]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.state in {"absent", "unavailable"} and self.raw_value is not None:
            raise ValueError("未出现或不可观察的字段不能伪造 raw_value")
        if self.state == "unavailable" and self.effective_value is not None:
            raise ValueError("不可观察字段不能产生解释值")
        if self.state in {"explicit", "explicit_unspecified"} and self.raw_value is None:
            raise ValueError("明确出现的字段必须保留原始值")
        if self.state == "explicit_unspecified" and self.effective_value is not None:
            raise ValueError("明确未指定不能携带已知值")
        if self.state == "explicit" and (self.effective_value is None or self.basis != "declared"):
            raise ValueError("明确已知值必须来自声明")
        if (
            self.state == "absent"
            and self.effective_value is not None
            and self.basis != "h264-default"
        ):
            raise ValueError("缺失字段只能由已证明语法默认提供有效值")
        if self.basis == "h264-default" and self.state != "absent":
            raise ValueError("规范推导必须具有已证明缺省条件")
        return self


class SignalLayer(PreparationModel):
    source_layer: Literal["matroska", "iso-bmff", "h264-sps", "h265-sps", "prores-frame-header"]
    scope: Literal["container-header", "all-sps", "all-prores-frames"]
    fields: Annotated[tuple[ColorField, ...], Field(min_length=5, max_length=5)]
    unsupported: Issues = ()

    @model_validator(mode="after")
    def ordered_fields(self) -> Self:
        if tuple(item.field for item in self.fields) != SIGNAL_NAMES:
            raise ValueError("色彩字段必须按冻结顺序恰好出现一次")
        return self


class FrameSignal(PreparationModel):
    """FFprobe 实际逐帧读数；unknown 不被转换为任何工作解释。"""

    color_primaries: ShortText
    color_transfer: ShortText
    color_space: ShortText
    color_range: ShortText
    chroma_location: ShortText
    width: Positive
    height: Positive
    pixel_format: ShortText
    sample_aspect_ratio: ShortText
    interlaced: bool


class FrameSignalSummary(PreparationModel):
    scope: Literal["full-frames-eof"] = "full-frames-eof"
    complete: Literal[True] = True
    frame_count: Positive
    variants: Annotated[tuple[FrameSignal, ...], Field(min_length=1, max_length=8)]
    changes: Issues = ()
    unsupported: Issues = ()

    @model_validator(mode="after")
    def consistent(self) -> Self:
        distinct = {item.model_dump_json() for item in self.variants}
        expected_changes = tuple(
            key
            for key in FrameSignal.model_fields
            if len({getattr(item, key) for item in self.variants}) > 1
        )
        if len(distinct) != len(self.variants) or len(self.variants) > self.frame_count:
            raise ValueError("逐帧聚合必须是完整帧数量范围内的不重复事实")
        if self.changes != expected_changes:
            raise ValueError("逐帧变化摘要必须与已保存的实际变体一致")
        return self


class BitstreamSummary(PreparationModel):
    scope: Literal["all-sps-sei-eof"] = "all-sps-sei-eof"
    complete: Literal[True] = True
    signal: SignalLayer
    sps_count: Positive
    sei_messages: Count
    sei_types: Annotated[tuple[Annotated[int, Field(ge=0, le=65535)], ...], Field(max_length=32)]
    clock_rates: Annotated[tuple[Rational, ...], Field(max_length=8)] = ()
    changes: Issues = ()
    unsupported: Issues = ()


class ResolvedSignal(PreparationModel):
    color_primaries: ShortText | None = None
    color_transfer: ShortText | None = None
    color_space: ShortText | None = None
    color_range: ShortText | None = None
    chroma_location: ShortText | None = None


class ObservedSignal(PreparationModel):
    """完整范围的分层观察；缺声明与冲突/变化不可相互替代。"""

    container: SignalLayer
    bitstream: BitstreamSummary
    frames: FrameSignalSummary
    resolved: ResolvedSignal
    missing_fields: Annotated[tuple[SignalName, ...], Field(max_length=5)]
    conflicts: Issues = ()
    changes: Issues = ()
    unsupported: Issues = ()


class SignalBasis(PreparationModel):
    field: SignalName
    source: Literal["declared", "standard_default", "operator_confirmation"]
    provenance: Annotated[tuple[ShortText, ...], Field(min_length=1, max_length=8)]


class DiagnosticReport(PreparationModel):
    schema_version: Literal["zniku.source.prepared.diagnosis/2"] = (
        "zniku.source.prepared.diagnosis/2"
    )
    original_media_artifact_id: RandomId
    source_stat: FileStat
    final_stat: FileStat
    complete: Literal[True] = True
    check_scope: Literal["full-eof"] = "full-eof"
    ffprobe_version: ShortText
    container: ShortText
    container_duration: Rational | None
    extra_streams: Count
    chapter_count: Count
    target_frame_rate: Rational | None
    video: VideoObservation
    audio: Annotated[tuple[AudioObservation, ...], Field(max_length=16)]
    observed_signal: ObservedSignal
    findings: Annotated[tuple[Finding, ...], Field(max_length=32)]
    candidate_strategies: Annotated[
        tuple[Literal["t1-clock-quantization/2"], ...], Field(max_length=1)
    ]
    elapsed_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]

    @model_validator(mode="after")
    def complete_input(self) -> Self:
        if self.source_stat != self.final_stat:
            raise ValueError("检查期间输入已变化")
        DiagnosticParameters(target_frame_rate=self.target_frame_rate)
        if self.video.frame_count != self.observed_signal.frames.frame_count:
            raise ValueError("色彩与时钟扫描未覆盖相同展示帧数")
        from .policy import validate_observed_summary

        validate_observed_summary(self.observed_signal)
        return self


class SourceGate(PreparationModel):
    schema_version: Literal["zniku.source.prepared.admission/2"] = (
        "zniku.source.prepared.admission/2"
    )
    original_media_artifact_id: RandomId
    reference_media_artifact_id: RandomId
    diagnosis_artifact_id: RandomId
    source_frame_count: Positive
    frame_rate: Rational
    original_video_start: Rational
    reference_start: Rational
    geometry: SourceGeometry
    working_signal: SourceSignal
    observed_signal: ObservedSignal
    interpretation_policy: InterpretationPolicy = "declared_only"
    policy_version: Literal["color-interpretation/1"] = "color-interpretation/1"
    basis: Annotated[tuple[SignalBasis, ...], Field(min_length=5, max_length=5)]
    preparation_strategy: Literal["original", "t1-clock-quantization/2", "external-preservation/2"]
    audio_policy: Literal["original", "none"] = "original"
    audio_bindings: tuple[AudioBinding, ...]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        legacy_audio_gate(self)
        from .policy import resolve_interpretation

        working, resolved_basis = resolve_interpretation(
            self.observed_signal, self.interpretation_policy
        )
        if working != self.working_signal or resolved_basis != self.basis:
            raise ValueError("工作解释及依据必须严格来自当前完整观察")
        if tuple(item.field for item in self.basis) != SIGNAL_NAMES:
            raise ValueError("工作解释依据必须覆盖有序五字段")
        if (
            self.observed_signal.conflicts
            or self.observed_signal.changes
            or self.observed_signal.unsupported
        ):
            raise ValueError("冲突、变化或不支持观察不能登记工作解释")
        if self.observed_signal.frames.frame_count != self.source_frame_count:
            raise ValueError("工作参考帧数与完整色彩观察不一致")
        if any(
            frame.width != self.geometry.width
            or frame.height != self.geometry.height
            or frame.sample_aspect_ratio not in {"1:1", self.geometry.sample_aspect_ratio}
            or frame.pixel_format != "yuv420p"
            or frame.interlaced
            for frame in self.observed_signal.frames.variants
        ):
            raise ValueError("准入几何和逐行像素合同必须与全部展示帧一致")
        for basis in self.basis:
            observed = getattr(self.observed_signal.resolved, basis.field)
            if observed is None:
                if (
                    self.interpretation_policy != "operator_confirmed_bt709_limited_left"
                    or basis.source != "operator_confirmation"
                ):
                    raise ValueError("缺少声明时必须有当前工程的显式受限解释")
            elif (
                observed != EXPECTED_SIGNAL[basis.field] or basis.source == "operator_confirmation"
            ):
                raise ValueError("工作解释不能覆盖已有明确事实")
        return self


def legacy_audio_gate(gate: SourceGate) -> previous.SourceGate:
    """仅复用未改变的身份/音频纯约束，不产生旧 namespace Artifact 或旧准入事实。"""
    fields = gate.model_dump(
        exclude={
            "schema_version",
            "working_signal",
            "observed_signal",
            "interpretation_policy",
            "policy_version",
            "basis",
        }
    )
    fields["signal"] = gate.working_signal
    fields["preparation_strategy"] = gate.preparation_strategy.replace("/2", "/1")
    return previous.SourceGate.model_validate(fields)


class PrepareParameters(DiagnosticParameters):
    target_frame_rate: Rational
    strategy_id: Literal["t1-clock-quantization/2"] = "t1-clock-quantization/2"


class ExternalParameters(DiagnosticParameters):
    target_frame_rate: Rational


class AdmissionParameters(DiagnosticParameters):
    audio_policy: Literal["original", "none"] = "original"
    interpretation_policy: InterpretationPolicy = "declared_only"


def summary_for_report(report: DiagnosticReport) -> dict[str, object]:
    """提供服务摘要；缺声明不等于可直接准入，尚未晋级的候选不是可执行策略。"""
    choices: set[str] = set(report.observed_signal.bitstream.clock_rates)
    for value in (report.video.r_frame_rate, report.video.avg_frame_rate):
        try:
            rate = Fraction(value)
        except (ValueError, ZeroDivisionError):
            continue
        if 1 <= rate <= 240:
            choices.add(previous.rational(rate))
    available = list(report.candidate_strategies) if T1_PROMOTED else []
    return {
        "schema_version": report.schema_version,
        "status": "compatible" if not report.findings else "needs_preparation",
        "recommended_action": "direct"
        if not report.findings
        else "builtin"
        if available
        else "external",
        "findings": [item.model_dump(mode="json") for item in report.findings],
        "source_frame_count": report.video.frame_count,
        "frame_rate": report.target_frame_rate,
        "available_strategies": available,
        "candidate_strategies": list(report.candidate_strategies),
        "audio_count": len(report.audio),
        "target_frame_rate_choices": sorted(choices),
        "missing_color_fields": list(report.observed_signal.missing_fields),
        "can_preserve": all(
            item.code
            in {"E_SOURCE_PREPARATION_CLOCK_NOT_CFR", "E_SOURCE_PREPARATION_COLOR_UNSPECIFIED"}
            for item in report.findings
        ),
        "can_interpret": bool(report.observed_signal.missing_fields)
        and not report.observed_signal.conflicts
        and not report.observed_signal.changes
        and not report.observed_signal.unsupported,
        "direct_eligible_with_interpretation": all(
            item.code
            in {
                "E_SOURCE_PREPARATION_COLOR_UNSPECIFIED",
                "E_SOURCE_PREPARATION_RATE_SELECTION_REQUIRED",
            }
            for item in report.findings
        ),
        "original_preserved": True,
    }
