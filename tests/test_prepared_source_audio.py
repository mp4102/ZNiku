"""证明长音频比较可取消，Final validator 不重复完整扫描。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from time import monotonic
from typing import Any

import pytest

from test_prepared_source_node_contracts import pipeline, validator_context
from zniku.prepared_source import audio, validators
from zniku.runtime.runner import RunnerCancelled
from zniku.source_preparation import contracts, process


def test_audio_digest_cancellation_reaps_running_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真实受控子进程没有输出时仍能响应心跳，取消后不得留下后台处理。"""
    spawned: list[subprocess.Popen[bytes]] = []
    original_popen = subprocess.Popen

    def track(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        child = original_popen(*args, **kwargs)
        spawned.append(child)
        return child

    class Cancel:
        def report(self, fraction: float, **kwargs: Any) -> None:
            raise RunnerCancelled("synthetic cancellation")

    def waiting_tool(argv: Any, **kwargs: Any) -> bool:
        return process.stream_process(
            [sys.executable, "-c", "import time; time.sleep(15)"], **kwargs
        )

    monkeypatch.setattr(subprocess, "Popen", track)
    monkeypatch.setattr(audio, "stream_process", waiting_tool)
    monkeypatch.setattr(audio, "resolve_media_tool", lambda name: "unused-synthetic-tool")
    started = monotonic()
    with pytest.raises(RunnerCancelled):
        audio._audio_digest(tmp_path / "unused.mkv", 1, Cancel())
    assert monotonic() - started < 5
    assert len(spawned) == 1 and spawned[0].poll() is not None


def test_final_validator_never_runs_full_audio_or_video_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """完整验证由 automatic adapter 负责，validator 只做有界检查和已登记直接绑定。"""
    steps = pipeline(tmp_path)
    context, headers = validator_context(steps[-1], tmp_path)
    monkeypatch.setattr(
        validators, "probe_header", lambda path: headers.get(path, next(iter(headers.values())))
    )
    monkeypatch.setattr(validators, "verify_audio_origins", lambda source, output: None)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("validator attempted an uninterruptible full scan")

    monkeypatch.setattr(contracts, "validate_audio_sources", forbidden)
    monkeypatch.setattr(audio, "verify_audio_content", forbidden)
    monkeypatch.setattr(audio, "verify_video_span", forbidden)
    result = validators.validate_final_mux(context)
    assert result.passed, result.message
