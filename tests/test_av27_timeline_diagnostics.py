"""验证原片时间轴拒绝的可解释性，不放宽旧 exact 媒体合同。

只使用合成扫描记录和 Python 输出的 compact 时间戳，不读取操作者媒体或执行转码。
覆盖稳定率边界与其他置信度门禁，避免把所有拒绝误称为单一比例不合格。
"""

from __future__ import annotations

import sys
from dataclasses import replace
from fractions import Fraction

import pytest

from zniku.avenhance_v27 import probe


def _scan() -> probe._TimelineScan:
    return probe._TimelineScan(301, 301, 301, 301, 300, 300, 300, Fraction(1, 25), Fraction(25))


def _confidence(scan: probe._TimelineScan) -> str:
    return probe._timeline_confidence(scan, packet_count=301, rate=Fraction(25), duration=301 / 25)


def test_compact_periodic_timing_anomalies_remain_rejected_with_measured_details() -> None:
    """重复的极短/双长间隔即使总时长正确，也不能靠改文案或降阈值放行。"""
    synthetic = (
        "t=0\n"
        "for i in range(301):\n"
        " print(f'dts_time={t / 1000000:.6f}|pts_time={t / 1000000:.6f}')\n"
        " t += 1 if i % 60 == 58 else 79999 if i % 60 == 59 else 40000\n"
    )
    scanned = probe._scan_timeline(
        [sys.executable, "-c", synthetic],
        expected_period=Fraction(1, 25),
        primary_field="dts_time",
        secondary_field="pts_time",
        time_base=Fraction(1, 1_000_000),
    )
    assert scanned.total == 301
    assert scanned.stable_count == 290
    assert scanned.timestamp_span_rate == Fraction(25)
    with pytest.raises(probe.Av27MediaError) as captured:
        _confidence(scanned)
    assert captured.value.code == "E_AV27_SOURCE_FPS_AMBIGUOUS"
    message = str(captured.value)
    assert "稳定间隔=96.6667% (要求>=98%)" in message
    assert "时间戳覆盖=100.0000%" in message
    assert "正向间隔=100.0000%" in message
    assert "最大间隔=2.0000帧周期" in message
    assert "全片跨度帧率偏差=0.0000%" in message
    assert "未修改素材，未执行时间轴校正或增删帧" in message


def test_cadence_medium_threshold_is_unchanged() -> None:
    assert _confidence(replace(_scan(), stable_count=294)) == "medium"
    with pytest.raises(probe.Av27MediaError, match="要求>=98%"):
        _confidence(replace(_scan(), stable_count=293))


@pytest.mark.parametrize(
    ("scan", "detail"),
    [
        (replace(_scan(), timestamp_count=297), "时间戳覆盖=98.6711%"),
        (replace(_scan(), positive_delta_count=296), "正向间隔=98.6667%"),
        (replace(_scan(), max_positive_delta=Fraction(6, 25)), "最大间隔=6.0000帧周期"),
        (replace(_scan(), max_positive_delta=None), "最大间隔=unknown"),
        (replace(_scan(), timestamp_span_rate=Fraction(26)), "全片跨度帧率偏差=4.0000%"),
        (replace(_scan(), timestamp_span_rate=None), "全片跨度帧率偏差=unknown"),
    ],
)
def test_other_confidence_failures_retain_the_actual_failing_metric(
    scan: probe._TimelineScan, detail: str
) -> None:
    with pytest.raises(probe.Av27MediaError) as captured:
        _confidence(scan)
    assert captured.value.code == "E_AV27_SOURCE_FPS_AMBIGUOUS"
    assert "稳定间隔=100.0000%" in str(captured.value)
    assert detail in str(captured.value)


def test_duration_and_packet_count_failures_keep_their_separate_meaning() -> None:
    with pytest.raises(probe.Av27MediaError, match="timeline 样本数矛盾"):
        _confidence(replace(_scan(), total=302))
    with pytest.raises(probe.Av27MediaError, match="N/FPS/duration 无法闭合"):
        probe._timeline_confidence(_scan(), packet_count=301, rate=Fraction(25), duration=20)
