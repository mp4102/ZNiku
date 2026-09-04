"""验证 Phase 5 合成 E2E 入口与产物保留边界。

完整用例执行真实 FFmpeg、Project Service 和人工输出 Submit，不注入 completed 状态。缺少媒体
工具的单元测试环境明确 skip；独立 ``tools/run_av27_smoke.py`` 自动门禁仍将缺工具视为失败。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPOSITORY = Path(__file__).resolve().parents[1]
_SCRIPT = _REPOSITORY / "tools" / "run_av27_smoke.py"


def _invoke(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *arguments],
        cwd=_REPOSITORY,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        check=False,
        timeout=300,
    )


def test_smoke_refuses_existing_keep_root(tmp_path: Path) -> None:
    """保留模式不得接管已有目录；拒绝发生在生成媒体和创建 Project 之前。"""

    sentinel = tmp_path / "operator-owned.txt"
    sentinel.write_text("keep", encoding="utf-8")
    result = _invoke("--keep-root", str(tmp_path))
    assert result.returncode == 2
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert list(tmp_path.iterdir()) == [sentinel]


def test_smoke_waiting_requires_explicit_keep_root() -> None:
    """不允许生成等待人工的工程后自动清理其 handoff。"""

    result = _invoke("--stop-at", "enhancement")
    assert result.returncode == 2
    assert "--keep-root" in result.stderr


def test_av27_full_synthetic_project_service_smoke(tmp_path: Path) -> None:
    """真实短媒体覆盖两段 authoring、MR/Enhancement/FI、双章单编码、原音频和复用。"""

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None or shutil.which("ffprobe") is None:
        pytest.skip("AV27 E2E 需要 FFmpeg 与 FFprobe")
    encoders = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        shell=False,
        check=False,
        timeout=15,
    )
    if encoders.returncode != 0 or any(
        encoder not in encoders.stdout for encoder in (b"ffv1", b"prores_ks", b"libx265")
    ):
        pytest.skip("AV27 E2E 需要 ffv1/prores_ks/libx265")
    retained = tmp_path / "synthetic-e2e"
    result = _invoke("--keep-root", str(retained))
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["evidence_kind"] == "synthetic_only"
    assert summary["state"] == "completed"
    assert summary["source_frame_count"] == 24
    assert summary["chapter_count"] == summary["leaf_count"] == 2
    assert summary["fi_frame_counts"] == [19, 27]
    assert summary["program_frame_count"] == 48
    assert summary["program_fps"] == "60000/1001"
    assert summary["fi_input_sars"] == [None, "1:1"]
    assert summary["program_sar"] == "1:1"
    assert summary["producer_calls"]["program"] == 1
    assert summary["original_audio_track_count"] == 2
    assert summary["same_production_run"] is True
    assert summary["config_stale_nodes"] == ["final", "output", "program"]
    assert len(summary["reused_nodes"]) == 13
    assert Path(summary["project_path"]).is_file()
