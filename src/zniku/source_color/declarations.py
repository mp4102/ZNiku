"""将特定语法值域解释为局部观察；缺失、未指定和非法值不混用。"""

from __future__ import annotations

from .models import ColorField, SignalName


def cicp_field(name: SignalName, value: int | None, *, h264_default: bool = False) -> ColorField:
    """三个 CICP 字段的 2 为未指定；其他非白名单值仍保留为明确不支持值。"""
    if value is None:
        return ColorField(
            field=name, state="absent", basis="h264-default" if h264_default else "unspecified"
        )
    if value == 2:
        return ColorField(
            field=name, state="explicit_unspecified", raw_value=value, basis="unspecified"
        )
    return ColorField(
        field=name,
        state="explicit",
        raw_value=value,
        effective_value="bt709" if value == 1 else f"cicp:{value}",
        basis="declared",
    )


def absent_field(name: SignalName) -> ColorField:
    return ColorField(field=name, state="absent", basis="unspecified")


def known_field(name: SignalName, raw: int | str, value: str) -> ColorField:
    return ColorField(
        field=name, state="explicit", raw_value=raw, effective_value=value, basis="declared"
    )


def unspecified_field(name: SignalName, raw: int | str) -> ColorField:
    return ColorField(field=name, state="explicit_unspecified", raw_value=raw, basis="unspecified")
