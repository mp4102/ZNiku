"""提供仅监听 loopback 的 ZNIKU Studio Project Service HTTP host。

HTTP 层只负责严格 JSON、有限 body、CORS 与错误状态映射；Project、Graph 和 Runtime 语义全部委托给
``ProjectServiceApplication``。服务不接受 shell 字符串、attempt 工作根或 handoff 输出路径。
"""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from typing import Any, Final, Protocol
from urllib.parse import urlsplit

from .service import ProjectServiceApplication, ProjectServiceError

_MAX_BODY_BYTES: Final = 4 * 1024 * 1024
_DEFAULT_PORT: Final = 18_765


class _ResponseWriter(Protocol):
    """描述 HTTP handler 写出响应体所需的最小接口。"""

    def write(self, data: bytes, /) -> object:
        """写出完整或部分响应体。"""


def _write_response_body(writer: _ResponseWriter, data: bytes) -> bool:
    """写出 JSON body；客户端主动断连只终止当前传输，不重放已经执行的命令。

    Studio 轮询关闭、页面刷新或进程退出都可能让 socket 在响应序列化完成后消失。此时
    Project/Runtime mutation 已有自己的事务边界，HTTP host 只能放弃这次响应，不能把传输错误冒充
    领域失败或再次执行命令。
    """

    try:
        writer.write(data)
    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
        return False
    return True


class _JsonPayloadError(ValueError):
    """表示 JSON 不是 Project Service 接受的闭合数据。"""


def _is_http_loopback_origin(value: str) -> bool:
    """只接受无凭据、无路径的 HTTP loopback Origin 序列化值。"""

    if not value or value == "null" or value.strip() != value:
        return False
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        # 读取 ``port`` 会主动拒绝越界、非数字或多冒号端口。
        _ = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "http"
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.netloc.endswith(":")
    ):
        return False
    if hostname == "localhost":
        return True
    try:
        return ip_address(hostname).is_loopback
    except ValueError:
        return False


def _load_json(payload: bytes) -> object:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise _JsonPayloadError(f"重复字段：{key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise _JsonPayloadError(f"非法 JSON number：{value}")

    try:
        return json.loads(
            payload,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise _JsonPayloadError("request body 不是合法 UTF-8 JSON") from error


def make_project_service_handler(
    application: ProjectServiceApplication,
) -> type[BaseHTTPRequestHandler]:
    """把一个 process-local Project session 绑定到 HTTP handler。"""

    class Handler(BaseHTTPRequestHandler):
        server_version = "ZNIKUProjectService/0.2.0"

        def do_OPTIONS(self) -> None:
            if not self._origin_allowed():
                return
            self.send_response(HTTPStatus.NO_CONTENT)
            self._cors()
            self.end_headers()

        def do_GET(self) -> None:
            if not self._origin_allowed():
                return
            if self.path != "/api/studio/status":
                self._error(HTTPStatus.NOT_FOUND, "E_PROJECT_SERVICE_ROUTE", "未知 route")
                return
            try:
                envelope = application.inspect()
            except ProjectServiceError as error:
                self._error(HTTPStatus(error.http_status), error.code, error.message)
                return
            self._json(HTTPStatus.OK, envelope.model_dump(mode="json"))

        def do_POST(self) -> None:
            if not self._origin_allowed():
                return
            if self.path != "/api/studio/command":
                self._error(HTTPStatus.NOT_FOUND, "E_PROJECT_SERVICE_ROUTE", "未知 route")
                return
            content_types = self.headers.get_all("Content-Type", [])
            if (
                len(content_types) != 1
                or content_types[0].partition(";")[0].strip().casefold() != "application/json"
            ):
                self._error(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    "E_PROJECT_SERVICE_CONTENT_TYPE",
                    "POST request 必须使用 application/json",
                )
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if not 1 <= length <= _MAX_BODY_BYTES:
                self._error(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    "E_PROJECT_SERVICE_BODY_SIZE",
                    "request body 大小非法",
                )
                return
            try:
                payload = _load_json(self.rfile.read(length))
                envelope = application.command(payload)
            except _JsonPayloadError as error:
                self._error(HTTPStatus.BAD_REQUEST, "E_PROJECT_SERVICE_JSON", str(error))
                return
            except ProjectServiceError as error:
                self._error(HTTPStatus(error.http_status), error.code, error.message)
                return
            self._json(HTTPStatus.OK, envelope.model_dump(mode="json"))

        def log_message(self, format: str, *args: object) -> None:
            # Project/Artifact 路径和 query 不进入默认 access log。
            del format, args

        def _json(self, status: HTTPStatus, payload: object) -> None:
            data = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self._cors()
            self.end_headers()
            if not _write_response_body(self.wfile, data):
                self.close_connection = True

        def _error(self, status: HTTPStatus, code: str, message: str) -> None:
            self._json(status, {"error": {"code": code, "message": message[:4096]}})

        def _origin_allowed(self) -> bool:
            origins = self.headers.get_all("Origin", [])
            if not origins:
                return True
            if len(origins) == 1 and _is_http_loopback_origin(origins[0]):
                return True
            self._error(
                HTTPStatus.FORBIDDEN,
                "E_PROJECT_SERVICE_ORIGIN",
                "Origin 必须是 HTTP loopback origin",
            )
            return False

        def _cors(self) -> None:
            origins = self.headers.get_all("Origin", [])
            if len(origins) == 1 and _is_http_loopback_origin(origins[0]):
                self.send_header("Access-Control-Allow-Origin", origins[0])
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    return Handler


def serve_project_service(
    application: ProjectServiceApplication,
    *,
    port: int = _DEFAULT_PORT,
) -> ThreadingHTTPServer:
    """构造只绑定 ``127.0.0.1`` 的 server；调用方管理生命周期。"""

    if not 1024 <= port <= 65_535:
        raise ProjectServiceError(
            "E_PROJECT_SERVICE_PORT", "port 必须位于 1024..65535", http_status=400
        )
    return ThreadingHTTPServer(
        ("127.0.0.1", port),
        make_project_service_handler(application),
    )


__all__ = ["make_project_service_handler", "serve_project_service"]
