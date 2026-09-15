"""新准入允许的精确有理 FPS 不受旧成片标签 allowlist 阻断，路径保护仍共享旧实现。"""

from fractions import Fraction
from pathlib import Path

from zniku.avenhance_v27.template import (
    _FINAL_RATE_LABELS,
    MrMode,
    PublicationRequest,
    _publication_target,
)


def publication_target(
    request: PublicationRequest, *, mr_mode: MrMode, final_frame_rate: Fraction, height: int
) -> Path:
    """已知历史标签保持不变；其他 FPS 使用无损数值标签，不舍入或更改媒体时钟。"""
    rate = final_frame_rate
    if rate <= 0:
        raise ValueError("最终 FPS 必须为正")
    label = _FINAL_RATE_LABELS.get(rate) or (
        str(rate.numerator) if rate.denominator == 1 else f"{rate.numerator}-{rate.denominator}"
    )
    return _publication_target(
        request, mr_mode=mr_mode, final_frame_rate=rate, height=height, rate_label_override=label
    )
