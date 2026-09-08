"""验证原生 picker 自检工具的 opt-in、封闭目标与超时边界，普通 pytest 不弹窗。

本文件只替换测试工具的子进程入口，不调用 Windows API、Tk 或真实选择器。原生窗口置顶
是否成立仍必须由操作者显式运行 tools/run_windows_picker_smoke.py --native-windows 验证。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

import pytest

_PATH = Path(__file__).resolve().parents[1] / "tools" / "run_windows_picker_smoke.py"

if TYPE_CHECKING:
    import run_windows_picker_smoke as smoke
else:
    _SPEC = importlib.util.spec_from_file_location("zniku_windows_picker_smoke", _PATH)
    if _SPEC is None or _SPEC.loader is None:
        raise RuntimeError("无法加载封闭原生 picker 自检工具")
    smoke: ModuleType = importlib.util.module_from_spec(_SPEC)
    sys.modules[_SPEC.name] = smoke
    _SPEC.loader.exec_module(smoke)


def _success(capability: str = "open_file") -> smoke.PickerResult:
    return smoke.PickerResult(capability, True, True, True, True, True, True, True)


def test_default_and_invalid_capabilities_do_not_start_native_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(smoke, "run_matrix", lambda: calls.append("matrix"))
    monkeypatch.setattr(smoke, "run_native_child", lambda capability: calls.append(capability))
    for arguments in (
        [],
        ["--picker-child", "open_file"],
        ["--native-windows", "--picker-child", "reveal_in_file_manager"],
    ):
        with pytest.raises(SystemExit, check=lambda error: error.code == 2):
            smoke.main(arguments)
    assert calls == []


def test_non_windows_opt_in_is_rejected_without_native_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(smoke, "run_matrix", lambda: calls.append("matrix"))
    with pytest.raises(SystemExit, check=lambda error: error.code == 2):
        smoke.main(["--native-windows"])
    assert calls == []


@pytest.mark.parametrize(
    "field",
    (
        "dialog_observed",
        "own_process_verified",
        "owner_topmost",
        "dialog_topmost",
        "cancelled",
        "lock_released",
        "owner_destroyed",
    ),
)
def test_every_native_observation_is_required(field: str) -> None:
    value = asdict(_success())
    value[field] = False
    assert not smoke.parse_result(value, "open_file").passed
    assert not replace(_success(), error="发生异常").passed


@pytest.mark.parametrize("change", ("unknown", "wrong_capability", "integer_boolean", "error_type"))
def test_child_result_fails_closed(change: str) -> None:
    value = asdict(_success())
    if change == "unknown":
        value["hwnd"] = 123
    elif change == "wrong_capability":
        value["capability"] = "save_file"
    elif change == "integer_boolean":
        value["owner_topmost"] = 1
    else:
        value["error"] = {"message": "unexpected"}
    with pytest.raises(ValueError):
        smoke.parse_result(value, "open_file")


def test_parent_uses_only_four_fixed_children_with_hard_timeout_and_no_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command, 0, json.dumps(asdict(_success(command[-1]))), ""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    rows = smoke.run_matrix()
    assert len(rows) == 4 and all(row.passed for row in rows)
    assert [command[-1] for command, _ in calls] == list(smoke.PICKERS)
    for command, options in calls:
        assert command == [
            sys.executable,
            str(_PATH),
            "--native-windows",
            "--picker-child",
            command[-1],
        ]
        assert options["timeout"] == 15
        assert options["shell"] is False
        assert options["stdin"] == subprocess.DEVNULL
        assert options["capture_output"] is True
        assert options["env"]["PYTHONUTF8"] == "1"


@pytest.mark.parametrize("failure", ("timeout", "bad_json", "nonzero_success", "not_topmost"))
def test_parent_stops_after_failed_self_owned_child(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    calls = 0

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        row = _success(command[-1])
        if failure == "not_topmost":
            row = replace(row, owner_topmost=False)
        return subprocess.CompletedProcess(
            command,
            1 if failure == "nonzero_success" else 0,
            "invalid json" if failure == "bad_json" else json.dumps(asdict(row)),
            "",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    rows = smoke.run_matrix()
    assert calls == 1 and len(rows) == 1 and not rows[0].passed


def test_main_reports_only_native_boolean_results(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        smoke, "run_matrix", lambda: tuple(_success(item) for item in smoke.PICKERS)
    )
    assert smoke.main(["--native-windows"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["kind"] == "native_windows_picker_self_test"
    assert result["status"] == "passed" and len(result["results"]) == 4
    assert all("hwnd" not in row and "path" not in row for row in result["results"])
