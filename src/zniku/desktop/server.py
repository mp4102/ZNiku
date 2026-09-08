"""把同一 production Studio 与正式 Project Service 放在随机 loopback 端口。

这里只管理静态资源、内存 bootstrap 和应用退出。HostBridge 继续验证原有 token/Origin/
一次性票据，Project/Runtime 仍是唯一语义权威。退出拒绝活动操作，不终止或重写 attempt。
"""

from __future__ import annotations

import json
import mimetypes
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from pydantic import ValidationError

from zniku.avenhance_v27 import av27_python_adapters, av27_validators, built_in_av27_definitions
from zniku.media import (
    built_in_media_definitions,
    media_artifact_quick_probe,
    media_python_adapters,
    media_validators,
    runner_media_probe,
)
from zniku.project_service import ProjectServiceApplication, ProjectServiceError
from zniku.project_service.host import _load_json, make_project_service_handler
from zniku.project_service.host_bridge import (
    HOST_TOKEN_HEADER,
    HostBridgeFailure,
    HostPlatform,
    create_project_service_host_bridge_session,
)
from zniku.project_service.preview import PreviewCache

from .contracts import (
    DesktopCloseEnvelope,
    DesktopCloseRequest,
    DesktopPreferences,
    DesktopPreferencesEnvelope,
    DesktopPreferencesRequest,
    DesktopSessionEnvelope,
)
from .preferences import DesktopPreferenceStore


def build_desktop_application(work_root: Path) -> ProjectServiceApplication:
    """复用已验收的普通媒体与 AVEnhanceFlow 节点，不创建桌面专用执行合同。"""

    return ProjectServiceApplication(
        work_root=work_root,
        definition_catalog=(*built_in_media_definitions(), *built_in_av27_definitions()),
        python_adapters={**media_python_adapters(), **av27_python_adapters()},
        validators={**media_validators(), **av27_validators()},
        media_probe=runner_media_probe,
        artifact_quick_probe=media_artifact_quick_probe,
    )


class DesktopServer:
    """拥有随机端口、内存 session 与唯一 production assets 根的本机服务。"""

    def __init__(
        self,
        application: ProjectServiceApplication,
        assets_root: Path,
        *,
        platform: HostPlatform | None = None,
        data_root: Path | None = None,
    ) -> None:
        self.application = application
        self.assets_root = assets_root.resolve(strict=True)
        self.index = (self.assets_root / "index.html").read_text(encoding="utf-8")
        if "</head>" not in self.index or "<script" not in self.index:
            raise ValueError("Studio production index.html 缺少入口，不能启动")
        self.instance_id = str(uuid4())
        self.preferences = DesktopPreferenceStore(data_root)
        # 先占有 OS 分配的端口再生成 exact Origin，不进行存在抢占窗口的 free-port 探测。
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        self.origin = f"http://127.0.0.1:{self.http.server_port}"
        self.host_bridge = create_project_service_host_bridge_session(
            application, studio_origin=self.origin, platform=platform
        )
        self._nonce = secrets.token_urlsafe(24)
        self.preview_cache = PreviewCache()
        self._thread: threading.Thread | None = None
        self.http.RequestHandlerClass = self._make_handler()

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        owner = self
        parent = make_project_service_handler(
            self.application, host_bridge=self.host_bridge, preview_cache=self.preview_cache
        )

        class Handler(parent):  # type: ignore[valid-type,misc]
            def _desktop_target(self) -> bool:
                return bool(self.path.startswith("/api/desktop/"))

            def _same_host(self) -> bool:
                """连 DNS rebinding 形式的 Host 也拒绝，bootstrap 不提供跨 Origin 读取。"""

                hosts = self.headers.get_all("Host", [])
                origins = self.headers.get_all("Origin", [])
                fetch_site = self.headers.get("Sec-Fetch-Site")
                if (
                    hosts != [urlsplit(owner.origin).netloc]
                    or (origins and origins != [owner.origin])
                    or fetch_site == "cross-site"
                ):
                    self._desktop_error("E_DESKTOP_ORIGIN", "请求不是当前桌面 Origin", 403)
                    return False
                return True

            def _desktop_authorized(self) -> bool:
                try:
                    origins = self.headers.get_all("Origin", [])
                    tokens = self.headers.get_all(HOST_TOKEN_HEADER, [])
                    owner.host_bridge.authorize(
                        client_host=self.client_address[0],
                        origin=origins[0] if len(origins) == 1 else None,
                        token=tokens[0] if len(tokens) == 1 else None,
                    )
                except HostBridgeFailure as error:
                    self._desktop_error(error.code, error.message, error.http_status)
                    return False
                return True

            def do_GET(self) -> None:
                if not self._same_host():
                    return
                # 浏览器同源 GET 不携带 Origin。只在浏览器证明 same-origin、Host 已精确
                # 检查且仍必须提供 session token 时补齐等价授权上下文；same-site 不算。
                if (
                    not self.headers.get_all("Origin", [])
                    and self.headers.get("Sec-Fetch-Site") == "same-origin"
                ):
                    self.headers["Origin"] = owner.origin
                parsed = urlsplit(self.path)
                if parsed.scheme or parsed.netloc or parsed.fragment:
                    self._desktop_error("E_DESKTOP_ROUTE", "未知桌面路径", 404)
                    return
                if parsed.path == "/api/desktop/health" and not parsed.query:
                    self._desktop_json(
                        200,
                        {
                            "contract_version": "0.3.0",
                            "instance_id": owner.instance_id,
                            "status": "ready",
                        },
                    )
                elif self._desktop_target():
                    if (
                        parsed.path not in {"/api/desktop/session", "/api/desktop/preferences"}
                        or parsed.query
                    ):
                        self._desktop_error("E_DESKTOP_ROUTE", "未知桌面路径", 404)
                    elif self._desktop_authorized():
                        if parsed.path == "/api/desktop/preferences":
                            self._desktop_json(200, owner._preferences_envelope())
                            return
                        busy, closing = owner.application.desktop_lifecycle()
                        self._desktop_json(
                            200,
                            DesktopSessionEnvelope(
                                instance_id=owner.instance_id,
                                busy=busy,
                                closing=closing,
                            ).model_dump(mode="json"),
                        )
                elif parsed.path.startswith("/api/"):
                    super().do_GET()
                elif parsed.query:
                    self._desktop_error("E_DESKTOP_ROUTE", "静态资源不接受 query", 404)
                else:
                    self._static(parsed.path)

            def do_POST(self) -> None:
                if not self._same_host():
                    return
                if not self._desktop_target():
                    super().do_POST()
                    return
                if self.path == "/api/desktop/preferences":
                    self._save_preferences()
                    return
                if self.path != "/api/desktop/close":
                    self._desktop_error("E_DESKTOP_ROUTE", "未知桌面动作", 404)
                    return
                if not self._desktop_authorized():
                    return
                try:
                    if self.headers.get_all("Content-Type", []) != ["application/json"]:
                        raise ValueError("退出请求必须使用 application/json")
                    lengths = self.headers.get_all("Content-Length", [])
                    size = int(lengths[0]) if len(lengths) == 1 else 0
                    if not 1 <= size <= 1024:
                        raise ValueError("退出请求大小非法")
                    request = DesktopCloseRequest.model_validate(
                        _load_json(self.rfile.read(size)), strict=True
                    )
                    if request.instance_id != owner.instance_id:
                        raise ValueError("退出请求不是当前应用实例")
                    owner.application.prepare_desktop_close()
                except (ValueError, ValidationError):
                    self._desktop_error("E_DESKTOP_REQUEST", "退出请求字段或实例绑定无效", 422)
                    return
                except ProjectServiceError as error:
                    self._desktop_error(error.code, error.message, error.http_status)
                    return
                self._desktop_json(
                    200,
                    DesktopCloseEnvelope(
                        instance_id=owner.instance_id,
                    ).model_dump(mode="json"),
                )
                threading.Thread(target=owner.http.shutdown, daemon=True).start()

            def _save_preferences(self) -> None:
                if not self._desktop_authorized():
                    return
                try:
                    if self.headers.get_all("Content-Type", []) != ["application/json"]:
                        raise ValueError("偏好请求必须使用 application/json")
                    lengths = self.headers.get_all("Content-Length", [])
                    size = int(lengths[0]) if len(lengths) == 1 else 0
                    if not 1 <= size <= 65_536:
                        raise ValueError("偏好请求大小非法")
                    request = DesktopPreferencesRequest.model_validate(
                        _load_json(self.rfile.read(size)), strict=True
                    )
                    if request.instance_id != owner.instance_id:
                        raise ValueError("偏好请求不是当前应用实例")
                    owner.preferences.save(
                        DesktopPreferences(
                            density=request.density,
                            recent_projects=request.recent_projects,
                        )
                    )
                except (ValueError, ValidationError):
                    self._desktop_error(
                        "E_DESKTOP_PREFERENCES", "偏好数据无效，未修改已有设置", 422
                    )
                    return
                except OSError:
                    self._desktop_error("E_DESKTOP_PREFERENCES_WRITE", "本机偏好未能保存", 500)
                    return
                self._desktop_json(200, owner._preferences_envelope())

            def do_OPTIONS(self) -> None:
                if self._same_host():
                    if self._desktop_target():
                        self._desktop_error("E_DESKTOP_METHOD", "桌面入口只使用同源请求", 405)
                    else:
                        super().do_OPTIONS()

            def _static(self, raw_path: str) -> None:
                decoded = unquote(raw_path)
                relative = "index.html" if decoded in {"/", "/index.html"} else decoded[1:]
                if "\\" in decoded or "\x00" in decoded or not raw_path.startswith("/"):
                    self._desktop_error("E_DESKTOP_ROUTE", "未知静态资源", 404)
                    return
                try:
                    path = (owner.assets_root / relative).resolve(strict=True)
                    path.relative_to(owner.assets_root)
                    if not path.is_file():
                        raise ValueError("不是文件")
                    if relative == "index.html":
                        data = owner._index_html()
                        mime = "text/html; charset=utf-8"
                    else:
                        data = path.read_bytes()
                        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                        if path.suffix == ".js":
                            mime = "text/javascript; charset=utf-8"
                except (OSError, ValueError):
                    self._desktop_error("E_DESKTOP_ROUTE", "未知静态资源", 404)
                    return
                self._desktop_bytes(200, data, mime)

            def _desktop_error(self, code: str, message: str, status: int) -> None:
                self._desktop_json(status, {"error": {"code": code, "message": message}})

            def _desktop_json(self, status: int, payload: object) -> None:
                self._desktop_bytes(status, json.dumps(payload).encode(), "application/json")

            def _desktop_bytes(self, status: int, data: bytes, mime: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                self.send_header(
                    "Content-Security-Policy",
                    (
                        "default-src 'self'; "
                        f"script-src 'self' 'unsafe-eval' 'nonce-{owner._nonce}'; "
                        "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                        "connect-src 'self'; object-src 'none'; "
                        "base-uri 'none'; frame-ancestors 'none'"
                    ),
                )
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    self.close_connection = True

        return Handler

    def _index_html(self) -> bytes:
        bootstrap = (
            f"window.__ZNIKU_STUDIO_API_BASE__={json.dumps(self.origin)};"
            "window.__ZNIKU_HOST_BRIDGE__="
            + json.dumps(
                {
                    "baseUrl": self.origin,
                    "token": self.host_bridge.token,
                }
            )
            + ";window.__ZNIKU_DESKTOP__="
            + json.dumps(
                {
                    "contractVersion": "0.3.0",
                    "instanceId": self.instance_id,
                    "preferences": self.preferences.read().model_dump(mode="json"),
                }
            )
            + ";"
        )
        # 工程显示名是纯文本；嵌入 HTML 时额外转义 <，避免 </script> 提前终止 bootstrap。
        bootstrap = (
            bootstrap.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        )
        script = f'<script nonce="{self._nonce}">{bootstrap}</script>'
        return self.index.replace("</head>", f"{script}</head>", 1).encode("utf-8")

    def _preferences_envelope(self) -> dict[str, object]:
        return DesktopPreferencesEnvelope(
            instance_id=self.instance_id,
            **self.preferences.read().model_dump(mode="python"),
        ).model_dump(mode="json")

    def start(self) -> None:
        """启动唯一 HTTP 主循环，启动后才能执行有界健康检查。"""

        if self._thread is not None:
            raise RuntimeError("DesktopServer 已启动")
        self._thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self._thread.start()

    def wait(self) -> None:
        """等待显式退出；浏览器关页本身不表示应用退出。"""

        if self._thread is None:
            raise RuntimeError("DesktopServer 尚未启动")
        self._thread.join()

    def close(self) -> None:
        """只释放本实例 HTTP listener；不删除工程、输出或运行记录。"""

        if self._thread is not None and self._thread.is_alive():
            self.http.shutdown()
            self._thread.join(timeout=5)
        self.http.server_close()
        self.preview_cache.close()
