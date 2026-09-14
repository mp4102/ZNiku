"""真实执行内置/外部素材准备到 Final 的普通 Graph；合成测试不晋级产品策略。"""

from __future__ import annotations

import runpy
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from zniku.media.probe import MediaNodeError
from zniku.source_preparation import models
from zniku.source_preparation.preservation import resolve_mkvmerge


@pytest.mark.parametrize("route", ["builtin", "external"])
def test_real_preparation_route_reaches_final_with_original_audio(
    route: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """仅 pytest 的内存覆盖允许实际内置执行；源码开关、Graph 参数与 CLI 都不变。"""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("真实短合成全链需要 FFmpeg/FFprobe")
    if route == "builtin":
        try:
            resolve_mkvmerge()
        except MediaNodeError:
            pytest.skip("内置真实候选生成需要 MKVToolNix")
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / "tools"))
    module = runpy.run_path(str(root / "tools/run_prepared_source_smoke.py"))
    # 先加载工具保留实际产品开关 false；运行时仅在本用例中模拟晋级，不把合成当真实门禁。
    assert module["T1_PROMOTED"] is False and models.T1_PROMOTED is False
    if route == "builtin":
        monkeypatch.setattr(models, "T1_PROMOTED", True)
    # Windows 收件目录还包含端口身份摘要；fixture 根用短名，避免测试命名消耗系统路径预算。
    output = root / "build" / f"p{route[0]}-{uuid4().hex[:8]}"
    result = module["run_smoke"](output, _preparation_route=route)
    assert result["state"] == "completed" and result["final_frames"] == 36
    assert result["reference_is_distinct_from_original"]
    assert result["original_video_clock_rejected"]
    assert result["actual_audio_samples_unchanged"] and result["source_unchanged"]
    assert result["t1_promoted"] is False
    assert result["test_only_t1_promotion_override"] is (route == "builtin")
    assert result["reused_nodes"] == 23
    assert result["repair_manual_submissions"] == (
        ["source-preparation-prepare"] if route == "external" else []
    )
    print(f"{route}: {output / 'report.json'}")
