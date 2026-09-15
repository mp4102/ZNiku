"""重跑 v0.3.5 准入的合成规则对照；可显式指定可信 AVEnhanceFlow 2.7.0 源码。

默认只运行仓库自包含的合成场景。外部参照只导入纯函数，不执行 CLI、不扫描媒体，
不写 AVEnhanceFlow 任务、缓存或文件；外部源码不是 CI 或 ZNIKU 运行依赖。
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Callable, Mapping
from fractions import Fraction
from pathlib import Path
from typing import Any

from zniku.avenhance_v27.probe import Av27MediaError, Av27MediaHeader, _parse_header
from zniku.source_admission.timeline import evaluate_timeline

_MODES = ("normal", "jitter", "missing-prefix", "swap", "gap", "drift-small", "drift-large")
_SUMMARY_KEYS = (
    "confidence",
    "packet_count",
    "clock_sample_count",
    "pts_ratio",
    "dts_ratio",
    "timestamp_ratio",
    "cadence_ratio",
    "positive_delta_ratio",
    "max_delta",
    "timestamp_span_fps",
)
type Reference = Callable[[Av27MediaHeader, list[Mapping[str, object]]], dict[str, object] | None]


def _header(count: int, rate: Fraction) -> Av27MediaHeader:
    """只构造内存中 header；路径标签不代表存在的媒体。"""
    payload = {
        "format": {"format_name": "mp4", "duration": count / float(rate)},
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "pix_fmt": "yuv420p",
                "avg_frame_rate": str(rate),
                "r_frame_rate": str(rate),
                "time_base": "1/90000",
                "duration": count / float(rate),
            }
        ],
    }
    return _parse_header(Path("synthetic.mp4"), payload, count_frames=False)


def _rows(count: int, rate: Fraction, mode: str) -> list[Mapping[str, object]]:
    values: list[float | None] = [index / float(rate) for index in range(count)]
    if mode == "jitter":
        values = [
            float(value or 0) + (0.005 if index % 2 else 0) for index, value in enumerate(values)
        ]
    elif mode == "missing-prefix":
        values[: max(1, count // 100)] = [None] * max(1, count // 100)
    elif mode == "swap":
        index = count // 2
        values[index], values[index + 1] = values[index + 1], values[index]
    elif mode == "gap":
        values[count // 2] = float(values[count // 2] or 0) + 8 / float(rate)
    elif mode.startswith("drift"):
        ratio = 1.000001 if mode == "drift-small" else 1.000003
        values = [float(value or 0) / ratio for value in values]
    return [
        {"dts_time": "N/A" if value is None else str(value), "pts_time": str(index / float(rate))}
        for index, value in enumerate(values)
    ]


def _load_reference(root: Path) -> Reference:
    """显式可信参照只在此次进程内导入；禁止自动寻找其他工作区或写入 pycache。"""
    package_root = root.resolve(strict=True) / "tools" / "AVSplitTool" / "src"
    if not (package_root / "avsplittool" / "node_validation.py").is_file():
        raise ValueError("参照目录缺少 AVSplitTool node_validation.py")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(package_root))
    package = importlib.import_module("avsplittool")
    if not Path(str(package.__file__)).resolve().is_relative_to(package_root):
        raise ValueError("当前进程已加载其他 AVSplitTool，拒绝混用参照目录")
    if package.__version__ != "2.7.0":
        raise ValueError("参照 package 必须为明确的 2.7.0，不自动采用其他版本")
    models = importlib.import_module("avsplittool.models")
    validation = importlib.import_module("avsplittool.node_validation")
    errors = importlib.import_module("avsplittool.errors")

    def reference(
        header: Av27MediaHeader,
        rows: list[Mapping[str, object]],
    ) -> dict[str, object] | None:
        video = header.video
        stream = models.StreamInfo(
            index=0,
            codec_type="video",
            codec_name="h264",
            width=1920,
            height=1080,
            r_frame_rate=str(video.r_frame_rate),
            avg_frame_rate=str(video.avg_frame_rate),
            time_base=str(video.time_base),
            duration=video.duration_seconds,
        )
        media = models.MediaInfo(header.path, "mp4", 0, header.duration_seconds, 1, None, [stream])
        timestamps = [float(str(row["dts_time"])) for row in rows if row["dts_time"] != "N/A"]
        try:
            result: dict[str, Any] = validation._source_cadence_summary(
                media,
                video.frame_rate,
                len(rows),
                timestamps,
                len(rows),
                len(timestamps),
                analysis_clock="dts",
            )
            validation.validate_source_media(media, len(rows), cadence=result)
        except errors.AVSplitError:
            return None
        return result

    return reference


def run_scenarios(reference: Reference | None = None) -> dict[str, object]:
    """验证63个规模/rate/异常组合；提供参照时另逐项比较准入与有限摘要。"""
    total = passed = rejected = 0
    for rate in (Fraction(30), Fraction(30000, 1001), Fraction(27)):
        for count in (100, 1000, 3000):
            for mode in _MODES:
                header, rows = _header(count, rate), _rows(count, rate, mode)
                try:
                    result = evaluate_timeline(header, rows)
                except Av27MediaError:
                    result = None
                expected_pass = (
                    mode not in {"jitter", "gap"}
                    and not (mode == "swap" and count == 100)
                    and not (mode == "drift-large" and rate == 27)
                )
                identity = f"rate={rate}, N={count}, mode={mode}"
                if (result is not None) != expected_pass:
                    raise AssertionError(f"合成回归结果不符：{identity}")
                if reference is not None:
                    actual = reference(header, rows)
                    if (actual is None) != (result is None):
                        raise AssertionError(f"与AV2.7准入结果不同：{identity}")
                    if actual is not None and result is not None:
                        for key in _SUMMARY_KEYS:
                            if actual[key] != result[key]:
                                raise AssertionError(f"AV2.7字段不符 {key}：{identity}")
                        if Fraction(str(actual["effective_frame_rate"])) != Fraction(
                            str(result["effective_frame_rate"])
                        ):
                            raise AssertionError(f"AV2.7 canonical cadence 不符：{identity}")
                total += 1
                passed += result is not None
                rejected += result is None
    return {
        "scenarios": total,
        "admitted": passed,
        "rejected": rejected,
        "external_reference": "AVEnhanceFlow 2.7.0" if reference is not None else None,
        "media_io": False,
        "result": "passed",
    }


def main() -> None:
    """显式选择可选参照；只把有界结果打印到 stdout，不写报告或修改仓库。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--avenhanceflow-root",
        type=Path,
        help="可选：操作者明确信任的AVEnhanceFlow 2.7.0源码仓库根目录",
    )
    args = parser.parse_args()
    reference = _load_reference(args.avenhanceflow_root) if args.avenhanceflow_root else None
    print(json.dumps(run_scenarios(reference), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
