"""纯色彩判定：合并不冲突的事实，工作解释只填已证明未指定的项。"""

from __future__ import annotations

from zniku.source_preparation.models import SourceSignal
from zniku.source_preparation.process import fail

from .models import (
    EXPECTED_SIGNAL,
    SIGNAL_NAMES,
    BitstreamSummary,
    FrameSignalSummary,
    InterpretationPolicy,
    ObservedSignal,
    ResolvedSignal,
    SignalBasis,
    SignalLayer,
)


def combine_observations(
    container: SignalLayer, bitstream: BitstreamSummary, frames: FrameSignalSummary
) -> ObservedSignal:
    """保留三层事实；解码器的 unknown 不能证明原始声明 absent。"""
    unsupported = (
        set(container.unsupported)
        | set(bitstream.unsupported)
        | set(bitstream.signal.unsupported)
        | set(frames.unsupported)
    )
    changes = set(bitstream.changes) | set(frames.changes)
    conflicts: set[str] = set()
    resolved: dict[str, str | None] = {}
    for name in SIGNAL_NAMES:
        declarations = [
            next(item for item in layer.fields if item.field == name)
            for layer in (container, bitstream.signal)
        ]
        if any(item.state == "unavailable" for item in declarations):
            unsupported.add(f"{name}:unavailable")
        known = {item.effective_value for item in declarations if item.effective_value is not None}
        actual = {getattr(frame, name) for frame in frames.variants} - {
            "unknown",
            "unspecified",
            "N/A",
            "",
        }
        if len(known) > 1 or (known and actual and known != actual):
            conflicts.add(name)
        if any(value != EXPECTED_SIGNAL[name] for value in known | actual):
            unsupported.add(f"{name}:outside-bt709-limited-left")
        # 帧读数不能为缺少两层原始依据的字段单独提供确定性。
        if actual and not known:
            unsupported.add(f"{name}:decoder-only-value")
        resolved[name] = next(iter(known)) if len(known) == 1 else None
    return ObservedSignal(
        container=container,
        bitstream=bitstream,
        frames=frames,
        resolved=ResolvedSignal.model_validate(resolved),
        missing_fields=tuple(name for name in SIGNAL_NAMES if resolved[name] is None),
        conflicts=tuple(sorted(conflicts)),
        changes=tuple(sorted(changes)),
        unsupported=tuple(sorted(unsupported)),
    )


def validate_observed_summary(observed: ObservedSignal) -> None:
    """摘要不是第二份权威；派生结果必须与所保存的实际层级事实完全一致。"""
    if combine_observations(observed.container, observed.bitstream, observed.frames) != observed:
        raise fail("COLOR_REPORT", "色彩摘要与实际层级事实不一致")


def resolve_interpretation(
    observed: ObservedSignal, policy: InterpretationPolicy
) -> tuple[SourceSignal, tuple[SignalBasis, ...]]:
    """不执行媒体 I/O；操作者确认只处理真正未指定，不能覆盖明确值或其他阻断。"""
    validate_observed_summary(observed)
    if policy not in {"declared_only", "operator_confirmed_bt709_limited_left"}:
        raise fail("COLOR_POLICY", "未知工作色彩解释策略")
    if observed.conflicts or observed.changes or observed.unsupported:
        raise fail("COLOR_UNSUPPORTED", "色彩观察有冲突、变化或不支持内容，不能用解释选项绕过")
    if observed.missing_fields and policy == "declared_only":
        raise fail("COLOR_UNSPECIFIED", "工作参考缺少完整色彩声明；必须明确采用本工程工作解释")
    basis: list[SignalBasis] = []
    for name in SIGNAL_NAMES:
        value = getattr(observed.resolved, name)
        provenance = tuple(
            f"{layer.source_layer}:{item.state}:{item.basis}"
            for layer in (observed.container, observed.bitstream.signal)
            for item in layer.fields
            if item.field == name
        )
        if value is None:
            basis.append(
                SignalBasis(
                    field=name,
                    source="operator_confirmation",
                    provenance=(*provenance, "operator:color-interpretation/1"),
                )
            )
        else:
            if value != EXPECTED_SIGNAL[name]:
                raise fail("COLOR_CONFLICT", "工作解释不能覆盖明确色彩值")
            explicit = any(
                item.field == name and item.effective_value is not None and item.basis == "declared"
                for layer in (observed.container, observed.bitstream.signal)
                for item in layer.fields
            )
            basis.append(
                SignalBasis(
                    field=name,
                    source="declared" if explicit else "standard_default",
                    provenance=provenance,
                )
            )
    return SourceSignal(), tuple(basis)


def assert_preserved_signal(original: ObservedSignal, candidate: ObservedSignal) -> None:
    """比较实际声明；允许的默认显式化必须已有码流规范依据，不能贴猜测标签。"""
    for observation in (original, candidate):
        validate_observed_summary(observation)
        if observation.conflicts or observation.changes or observation.unsupported:
            raise fail("COLOR_PRESERVATION", "冲突或变化的色彩事实不属于本次保内容例外")
    if original.bitstream.signal != candidate.bitstream.signal:
        raise fail("COLOR_PRESERVATION", "SPS 声明发生变化，像素相同不能替代色彩保持")
    for old, new in zip(original.container.fields, candidate.container.fields, strict=True):
        if old == new:
            continue
        if old.effective_value is not None:
            if new.effective_value != old.effective_value:
                raise fail("COLOR_PRESERVATION", "候选丢失或改变明确容器色彩声明")
        elif new.effective_value is not None:
            codec = next(
                item for item in original.bitstream.signal.fields if item.field == old.field
            )
            if codec.effective_value != new.effective_value or codec.basis not in {
                "declared",
                "h264-default",
            }:
                raise fail("COLOR_PRESERVATION", "候选为原先未指定的色彩强加了新标签")
        # 两种容器中的 absent/explicit unspecified 均仍未指定；真实层级差异保留在两个报告中。
    if original.resolved != candidate.resolved:
        raise fail("COLOR_PRESERVATION", "候选改变了已观察到的有效色彩语义")
