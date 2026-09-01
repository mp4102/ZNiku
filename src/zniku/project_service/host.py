"""提供仅监听 loopback 的 ZNIKU Studio 0.2.1 Project Service HTTP host。

HTTP 层只负责严格 JSON、有限 body、CORS、固定身份路由和 query 解析；Project、Graph、Runtime 与
readiness 语义全部委托给 ``ProjectServiceApplication``。客户端不能提供日志路径、attempt 工作根或
handoff target，所有未知路由、字段和 query 默认失败关闭。
"""

from __future__ import annotations

import json
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from typing import Any, Final, Protocol
from urllib.parse import parse_qsl, urlsplit

from .service import ProjectServiceApplication, ProjectServiceError

_MAX_BODY_BYTES: Final = 4 * 1024 * 1024
_DEFAULT_PORT: Final = 18_765
_RUNTIME_ID = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
_RUN_DETAIL_ROUTE = re.compile(rf"^/api/studio/runs/(?P<run_id>{_RUNTIME_ID})$")
_NODE_LOG_ROUTE = re.compile(
    rf"^/api/studio/runs/(?P<run_id>{_RUNTIME_ID})/node-runs/"
    rf"(?P<node_run_id>{_RUNTIME_ID})/logs$"
)
_READINESS_ROUTE = re.compile(
    rf"^/api/studio/runs/(?P<run_id>{_RUNTIME_ID})/node-runs/"
    rf"(?P<node_run_id>{_RUNTIME_ID})/handoff-readiness$"
)


class _ResponseWriter(Protocol):
    """描述 HTTP handler 写出响应体所需的最小接口。"""

    def write(self, data: bytes, /) -> object:
        """写出完整或部分响应体。"""


def _write_response_body(writer: _ResponseWriter, data: bytes) -> bool:
    """写出 JSON body；客户端主动断连只终止当前传输，不重放 mutation。"""

    try:
        writer.write(data)
    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
        return False
    return True


class _JsonPayloadError(ValueError):
    """表示 JSON 不是 Project Service 接受的闭合数据。"""


class _QueryError(ValueError):
    """表示 URL query 含未知、重复或非法值。"""


def _is_http_loopback_origin(value: str) -> bool:
    """只接受无凭据、无路径的 HTTP loopback Origin 序列化值。"""

    if not value or value == "null" or value.strip() != value:
        return False
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
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


def _strict_query(raw_query: str, *, allowed: frozenset[str]) -> dict[str, str]:
    """解析唯一键 query；空值、未知键与重复键全部拒绝。"""

    try:
        pairs = parse_qsl(
            raw_query,
            keep_blank_values=True,
            strict_parsing=True,
            encoding="utf-8",
            errors="strict",
            max_num_fields=8,
        )
    except (UnicodeError, ValueError) as error:
        raise _QueryError("query 不是合法的 UTF-8 form encoding") from error
    result: dict[str, str] = {}
    for key, value in pairs:
        if key not in allowed:
            raise _QueryError(f"未知 query 字段：{key}")
        if key in result:
            raise _QueryError(f"重复 query 字段：{key}")
        if not value:
            raise _QueryError(f"query 字段 {key} 不得为空")
        result[key] = value
    return result


def make_project_service_handler(
    application: ProjectServiceApplication,
) -> type[BaseHTTPRequestHandler]:
    """把一个 process-local Project session 绑定到 HTTP handler。"""

    class Handler(BaseHTTPRequestHandler):
        server_version = "ZNIKUProjectService/0.2.1"

        def do_OPTIONS(self) -> None:
            if not self._origin_allowed():
                return
            self.send_response(HTTPStatus.NO_CONTENT)
            self._cors()
            self.end_headers()

        def do_GET(self) -> None:
            if not self._origin_allowed():
                return
            try:
                parsed = urlsplit(self.path)
                if parsed.scheme or parsed.netloc or parsed.fragment:
                    raise _QueryError("request target 必须是本地 origin-form")
                payload = self._route_get(parsed.path, parsed.query)
            except _QueryError as error:
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    "E_PROJECT_SERVICE_QUERY_INVALID",
                    str(error),
                )
                return
            except ProjectServiceError as error:
                self._error(
                    HTTPStatus(error.http_status),
                    error.code,
                    error.message,
                    related_run_ids=error.related_run_ids,
                )
                return
            if payload is None:
                self._error(HTTPStatus.NOT_FOUND, "E_PROJECT_SERVICE_ROUTE", "未知 route")
                return
            self._json(HTTPStatus.OK, payload)

        def _route_get(self, path: str, raw_query: str) -> object | None:
            if path == "/api/studio/status":
                query = _strict_query(raw_query, allowed=frozenset({"view_run_id"}))
                return application.inspect(query.get("view_run_id")).model_dump(mode="json")
            if path == "/api/studio/runs":
                query = _strict_query(raw_query, allowed=frozenset({"cursor", "limit"}))
                raw_limit = query.get("limit")
                if raw_limit is None:
                    limit = 20
                elif not raw_limit.isascii() or not raw_limit.isdecimal():
                    raise _QueryError("limit 必须是 1..100 的 decimal integer")
                else:
                    limit = int(raw_limit)
                return application.list_run_summaries(
                    cursor=query.get("cursor"),
                    limit=limit,
                ).model_dump(mode="json")

            detail_match = _RUN_DETAIL_ROUTE.fullmatch(path)
            if detail_match is not None:
                _strict_query(raw_query, allowed=frozenset())
                return application.inspect_run_detail(detail_match.group("run_id")).model_dump(
                    mode="json"
                )
            log_match = _NODE_LOG_ROUTE.fullmatch(path)
            if log_match is not None:
                _strict_query(raw_query, allowed=frozenset())
                return application.inspect_node_logs(
                    log_match.group("run_id"),
                    log_match.group("node_run_id"),
                ).model_dump(mode="json")
            readiness_match = _READINESS_ROUTE.fullmatch(path)
            if readiness_match is not None:
                query = _strict_query(raw_query, allowed=frozenset({"probe"}))
                raw_probe = query.get("probe", "false")
                if raw_probe not in {"false", "true"}:
                    raise _QueryError("probe 只能是 false 或 true")
                return application.inspect_external_readiness(
                    run_id=readiness_match.group("run_id"),
                    node_run_id=readiness_match.group("node_run_id"),
                    probe=raw_probe == "true",
                ).model_dump(mode="json")
            return None

        def do_POST(self) -> None:
            if not self._origin_allowed():
                return
            parsed = urlsplit(self.path)
            if (
                parsed.path != "/api/studio/command"
                or parsed.query
                or parsed.fragment
                or parsed.scheme
                or parsed.netloc
            ):
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
                self._error(
                    HTTPStatus(error.http_status),
                    error.code,
                    error.message,
                    related_run_ids=error.related_run_ids,
                )
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

        def _error(
            self,
            status: HTTPStatus,
            code: str,
            message: str,
            *,
            related_run_ids: tuple[str, ...] = (),
        ) -> None:
            self._json(
                status,
                {
                    "error": {
                        "code": code,
                        "message": message[:4096],
                        "related_run_ids": list(related_run_ids),
                    }
                },
            )

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
