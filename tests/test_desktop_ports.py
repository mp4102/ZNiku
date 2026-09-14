"""桌面 listener 先持有再检查浏览器端口，重试有界且只释放自己未发布的 socket。"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler

import pytest

from zniku.desktop import server as desktop_server


class SyntheticListener:
    """只记录 listener 生命周期；不访问本机既有服务。"""

    def __init__(self, port: int) -> None:
        self.server_port = port
        self.closed = False

    def server_close(self) -> None:
        assert not self.closed
        self.closed = True


@pytest.mark.parametrize("safe_port", [4173, 49152, 65535])
def test_blocked_assignment_is_closed_before_safe_listener_is_published(
    monkeypatch: pytest.MonkeyPatch, safe_port: int
) -> None:
    created: list[SyntheticListener] = []

    def factory(
        address: tuple[str, int], handler: type[BaseHTTPRequestHandler]
    ) -> SyntheticListener:
        assert address == ("127.0.0.1", 0)
        assert handler is BaseHTTPRequestHandler
        if created:
            assert created[0].closed
        listener = SyntheticListener(4045 if not created else safe_port)
        created.append(listener)
        return listener

    monkeypatch.setattr(desktop_server, "_DesktopHTTPServer", factory)
    selected = desktop_server._create_http_server()
    assert id(selected) == id(created[1])
    assert len(created) == 2
    assert created[0].closed
    assert not created[1].closed


def test_blocked_assignments_exhaust_exact_budget_and_release_every_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[SyntheticListener] = []

    def factory(
        _address: tuple[str, int], _handler: type[BaseHTTPRequestHandler]
    ) -> SyntheticListener:
        assert all(listener.closed for listener in created)
        listener = SyntheticListener(4045)
        created.append(listener)
        return listener

    monkeypatch.setattr(desktop_server, "_DesktopHTTPServer", factory)
    with pytest.raises(RuntimeError, match="E_DESKTOP_PORT"):
        desktop_server._create_http_server()
    assert len(created) == 32
    assert all(listener.closed for listener in created)


def test_socket_creation_error_is_not_relabelled_as_a_bad_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def factory(_address: tuple[str, int], _handler: type[BaseHTTPRequestHandler]) -> None:
        nonlocal calls
        calls += 1
        raise OSError("synthetic socket allocation failure")

    monkeypatch.setattr(desktop_server, "_DesktopHTTPServer", factory)
    with pytest.raises(OSError, match="synthetic socket allocation failure"):
        desktop_server._create_http_server()
    assert calls == 1
