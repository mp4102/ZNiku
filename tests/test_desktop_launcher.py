"""验证桌面随机端口、单实例、关闭准入、纯本机偏好与 production 资源安全。"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from pydantic import ValidationError

from zniku.desktop.contracts import (
    DesktopCloseRequest,
    DesktopPreferences,
    DesktopPreferencesRequest,
    DesktopRecentProject,
)
from zniku.desktop.instance import InstanceLock, InstanceRecord, check_health
from zniku.desktop.preferences import DesktopPreferenceStore
from zniku.desktop.server import DesktopServer, build_desktop_application
from zniku.project_service import ProjectServiceError
from zniku.project_service.host_bridge import HOST_TOKEN_HEADER


@pytest.fixture
def desktop(tmp_path: Path) -> Iterator[DesktopServer]:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.html").write_text(
        '<html><head><script type="module" src="/main.js"></script></head><body></body></html>',
        encoding="utf-8",
    )
    (assets / "main.js").write_text("export const production = true", encoding="utf-8")
    server = DesktopServer(
        build_desktop_application(tmp_path / "attempts"), assets, data_root=tmp_path / "settings"
    )
    server.start()
    try:
        yield server
    finally:
        server.close()


def request(
    desktop: DesktopServer,
    path: str,
    *,
    payload: object | None = None,
    headers: dict[str, str] | None = None,
    authorized: bool = True,
) -> tuple[int, Any, dict[str, str]]:
    """只操作当前合成 desktop fixture，不打印或保存 bootstrap token。"""

    actual = (
        {"Origin": desktop.origin, HOST_TOKEN_HEADER: desktop.host_bridge.token}
        if authorized
        else {}
    )
    actual.update(headers or {})
    data = None if payload is None else json.dumps(payload).encode()
    if data is not None:
        actual.setdefault("Content-Type", "application/json")
    message = Request(desktop.origin + path, data=data, headers=actual)
    try:
        response = urlopen(message, timeout=5)
    except HTTPError as error:
        response = error
    with response:
        raw = response.read()
        body = (
            json.loads(raw)
            if "application/json" in response.headers.get("Content-Type", "")
            else raw.decode()
        )
        return response.status, body, dict(response.headers.items())


def test_random_port_bootstrap_health_and_no_token_record(
    desktop: DesktopServer, tmp_path: Path
) -> None:
    assert desktop.http.server_address[0] == "127.0.0.1"
    assert 1024 <= desktop.http.server_port <= 65535
    status, html, headers = request(desktop, "/", authorized=False)
    assert status == 200
    assert desktop.host_bridge.token in html
    assert "__ZNIKU_DESKTOP__" in html and "__ZNIKU_STUDIO_API_BASE__" in html
    assert headers["Cache-Control"] == "no-store"
    assert "'unsafe-eval'" in headers["Content-Security-Policy"]  # 已有 AJV runtime compiler。
    record = InstanceRecord(instance_id=desktop.instance_id, origin=desktop.origin)
    assert check_health(record)
    lock = InstanceLock(tmp_path / "discovery")
    assert lock.acquire()
    try:
        lock.publish(record)
        text = lock.record_path.read_text(encoding="utf-8")
        assert desktop.host_bridge.token not in text
        assert lock.existing(timeout=0.5) == record
    finally:
        lock.close()


@pytest.mark.parametrize(
    "path", ["/../secret.txt", "/%2e%2e/secret.txt", "/%5csecret.txt", "/main.js?token=wrong"]
)
def test_static_routes_do_not_escape_assets(desktop: DesktopServer, path: str) -> None:
    assert request(desktop, path)[0] == 404


def test_other_origin_host_and_fetch_site_cannot_read_bootstrap(desktop: DesktopServer) -> None:
    for headers in (
        {"Origin": "http://127.0.0.1:4173"},
        {"Host": "evil.example"},
        {"Sec-Fetch-Site": "cross-site"},
    ):
        status, body, _ = request(desktop, "/", headers=headers, authorized=False)
        assert status == 403
        assert desktop.host_bridge.token not in str(body)


def test_same_origin_get_requires_token_and_fetch_metadata(desktop: DesktopServer) -> None:
    assert request(desktop, "/api/desktop/session", authorized=False)[0] == 403
    assert (
        request(
            desktop,
            "/api/desktop/session",
            authorized=False,
            headers={HOST_TOKEN_HEADER: desktop.host_bridge.token},
        )[0]
        == 403
    )
    status, body, _ = request(
        desktop,
        "/api/desktop/session",
        authorized=False,
        headers={
            HOST_TOKEN_HEADER: desktop.host_bridge.token,
            "Sec-Fetch-Site": "same-origin",
        },
    )
    assert status == 200 and body["busy"] is False
    assert (
        request(
            desktop,
            "/api/host-bridge/capabilities",
            authorized=False,
            headers={
                HOST_TOKEN_HEADER: desktop.host_bridge.token,
                "Sec-Fetch-Site": "same-origin",
            },
        )[0]
        == 200
    )


@pytest.mark.parametrize("confirm", [False, 1, 0, "true", None])
def test_close_confirmation_is_exact_true(desktop: DesktopServer, confirm: object) -> None:
    with pytest.raises(ValidationError):
        DesktopCloseRequest.model_validate(
            {
                "contract_version": "0.3.0",
                "instance_id": desktop.instance_id,
                "confirm": confirm,
            }
        )
    assert desktop.application.desktop_lifecycle() == (False, False)


def test_close_unknown_fields_and_get_fail_without_side_effect(desktop: DesktopServer) -> None:
    assert request(desktop, "/api/desktop/close")[0] == 404
    status, _, _ = request(
        desktop,
        "/api/desktop/close",
        payload={
            "contract_version": "0.3.0",
            "instance_id": desktop.instance_id,
            "confirm": True,
            "shell": "unexpected",
        },
    )
    assert status == 422
    assert desktop.application.desktop_lifecycle() == (False, False)


def test_busy_close_preserves_worker_then_idle_close_seals_commands(desktop: DesktopServer) -> None:
    running, release = threading.Event(), threading.Event()

    def work() -> None:
        running.set()
        release.wait(timeout=5)

    desktop.application._begin_worker("run_all", desktop.instance_id, work)
    assert running.wait(timeout=2)
    payload = {"contract_version": "0.3.0", "instance_id": desktop.instance_id, "confirm": True}
    try:
        status, body, _ = request(desktop, "/api/desktop/close", payload=payload)
        assert status == 409 and body["error"]["code"] == "E_DESKTOP_BUSY"
        assert desktop.application.desktop_lifecycle() == (True, False)
    finally:
        release.set()
    assert desktop.application.wait_until_idle(timeout=2)
    assert request(desktop, "/api/desktop/close", payload=payload)[0] == 200
    with pytest.raises(ProjectServiceError, match="正在关闭"):
        desktop.application.command({"operation": "open_project", "path": "missing.zniku"})


def test_single_instance_lock_never_overwrites_other_owner(tmp_path: Path) -> None:
    first, second = InstanceLock(tmp_path), InstanceLock(tmp_path)
    assert first.acquire()
    try:
        assert not second.acquire()
        with pytest.raises(RuntimeError, match="非锁所有者"):
            second.publish(
                InstanceRecord(
                    instance_id="ce668e55-8c9d-4b3c-8599-9d687b37a5c0",
                    origin="http://127.0.0.1:17000",
                )
            )
    finally:
        first.close()
    assert second.acquire()
    second.close()


def test_preferences_restart_and_html_escape(desktop: DesktopServer) -> None:
    values = [
        {
            "path": "C:/synthetic/project.zniku",
            "name": "</script><script>bad</script>",
            "opened_at": "2026-09-05T00:00:00Z",
        }
    ]
    payload = {
        "contract_version": "0.3.0",
        "instance_id": desktop.instance_id,
        "density": "advanced",
        "recent_projects": values,
    }
    assert request(desktop, "/api/desktop/preferences", payload=payload)[0] == 200
    assert desktop.preferences.path is not None
    restarted = DesktopPreferenceStore(desktop.preferences.path.parent)
    assert restarted.read().density == "advanced"
    assert restarted.read().recent_projects[0].name == values[0]["name"]
    _, html, _ = request(desktop, "/")
    assert "</script><script>bad" not in html
    assert "\\u003c/script\\u003e" in html
    assert desktop.host_bridge.token not in desktop.preferences.path.read_text(encoding="utf-8")
    before = desktop.preferences.path.read_bytes()
    assert (
        request(desktop, "/api/desktop/preferences", payload={**payload, "token": "bad"})[0] == 422
    )
    assert desktop.preferences.path.read_bytes() == before


def test_preference_limits_and_corrupt_file_defaults(tmp_path: Path) -> None:
    item = DesktopRecentProject(path="C:/sample.zniku", name="合成工程", opened_at="2026-09-05")
    with pytest.raises(ValidationError):
        DesktopPreferences(recent_projects=(item, item))
    with pytest.raises(ValidationError):
        DesktopPreferencesRequest.model_validate(
            {"contract_version": "0.3.0", "instance_id": "ce668e55-8c9d-4b3c-8599-9d687b37a5c0"}
        )
    (tmp_path / "desktop-preferences.json").write_text("invalid", encoding="utf-8")
    assert DesktopPreferenceStore(tmp_path).read() == DesktopPreferences()


def test_preferences_preserve_existing_temp_and_old_value_on_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = DesktopPreferenceStore(tmp_path)
    old_temp = tmp_path / "desktop-preferences.tmp"
    old_temp.write_text("existing user file", encoding="utf-8")
    store.save(DesktopPreferences())
    assert store.path is not None
    before = store.path.read_bytes()

    def reject_replace(path: Path, target: Path) -> Path:
        del path, target
        raise OSError("synthetic replace unavailable")

    monkeypatch.setattr(Path, "replace", reject_replace)
    with pytest.raises(OSError, match="unavailable"):
        store.save(DesktopPreferences(density="advanced"))
    assert store.path.read_bytes() == before
    assert store.read() == DesktopPreferences()
    assert old_temp.read_text(encoding="utf-8") == "existing user file"
    assert not tuple(tmp_path.glob(".desktop-*.tmp"))


def test_close_and_command_share_atomic_admission(desktop: DesktopServer, tmp_path: Path) -> None:
    """无论并发顺序如何，关闭准入之后的 create 都不能创建工程。"""

    with ThreadPoolExecutor(max_workers=2) as executor:
        future = executor.submit(desktop.application.prepare_desktop_close)
        future.result()
        with pytest.raises(ProjectServiceError, match="正在关闭"):
            executor.submit(
                desktop.application.command,
                {
                    "operation": "create_project",
                    "path": str(tmp_path / "late.zniku"),
                    "name": "迟到工程",
                },
            ).result()
    assert not (tmp_path / "late.zniku").exists()
