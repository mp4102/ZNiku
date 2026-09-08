"""提供仅监听 loopback 的 ZNIKU Studio 0.3.0 Project Service HTTP host。

HTTP 层只负责严格 JSON、有限 body、CORS、固定身份路由和 query 解析；Project、Graph、Runtime 与
readiness 语义全部委托给 ``ProjectServiceApplication``。可选 HostBridge 使用独立的精确 Origin、
session token 与一次性动作票据，绝不复用普通 Project API 的宽松 loopback CORS。客户端不能提供日志
路径、attempt 工作根或 handoff target，所有未知路由、字段和 query 默认失败关闭。
"""

from __future__ import annotations

import json
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from typing import Any, Final, Protocol
from urllib.parse import SplitResult, parse_qsl, urlsplit

from pydantic import BaseModel

from .handoff_import import HandoffImportManager
from .handoff_inbox import HandoffInboxManager
from .host_bridge import HOST_TOKEN_HEADER, HostBridgeFailure, HostBridgeSession
from .preview import PreviewCache
from .service import ProjectServiceApplication, ProjectServiceError
from .storage_api import StorageApi

_MAX_BODY_BYTES: Final = 4 * 1024 * 1024
_MAX_HOST_BODY_BYTES: Final = 64 * 1024
_DEFAULT_PORT: Final = 18_765
_HOST_BRIDGE_PREFIX: Final = "/api/host-bridge"
_HOST_CAPABILITIES_ROUTE: Final = f"{_HOST_BRIDGE_PREFIX}/capabilities"
_HOST_ACTIONS_ROUTE: Final = f"{_HOST_BRIDGE_PREFIX}/user-actions"
_HOST_INVOKE_ROUTE: Final = f"{_HOST_BRIDGE_PREFIX}/invoke"
_HOST_PREVIEW_ROUTE: Final = f"{_HOST_BRIDGE_PREFIX}/preview"
_HANDOFF_IMPORT_PREFIX: Final = "/api/studio/handoff-import"
_HANDOFF_IMPORT_PREVIEW: Final = f"{_HANDOFF_IMPORT_PREFIX}/preview"
_HANDOFF_IMPORT_CONFIRM: Final = f"{_HANDOFF_IMPORT_PREFIX}/confirm"
_INBOX_PREFIX: Final = "/api/studio/handoff-inbox"
_STORAGE_PREFIX: Final = "/api/studio/storage"
_DATA_ROUTES: Final = frozenset(
    (
        *[f"{_INBOX_PREFIX}/{action}" for action in ("observe", "preview", "confirm")],
        *[
            f"{_STORAGE_PREFIX}/{action}"
            for action in ("inspect", "configure", "preview", "confirm")
        ],
    )
)
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
    *,
    host_bridge: HostBridgeSession | None = None,
    preview_cache: PreviewCache | None = None,
) -> type[BaseHTTPRequestHandler]:
    """把 Project session 与可选 launcher-local HostBridge 绑定到 HTTP handler。"""

    handoff_import = HandoffImportManager()
    handoff_inbox = HandoffInboxManager()
    storage_api = StorageApi()

    class Handler(BaseHTTPRequestHandler):
        server_version = "ZNIKUProjectService/0.3.0"

        def do_OPTIONS(self) -> None:
            if self._is_host_bridge_target():
                self._host_options()
                return
            if not self._origin_allowed():
                return
            self.send_response(HTTPStatus.NO_CONTENT)
            self._cors()
            self.end_headers()

        def do_GET(self) -> None:
            if self._is_host_bridge_target():
                self._host_get()
                return
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
            if path == "/api/studio/presentations":
                _strict_query(raw_query, allowed=frozenset())
                return application.inspect_presentations().model_dump(mode="json")
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
            if self._is_host_bridge_target():
                self._host_post()
                return
            if not self._origin_allowed():
                return
            parsed = urlsplit(self.path)
            if (
                parsed.query
                or parsed.fragment
                or parsed.scheme
                or parsed.netloc
                or parsed.path
                not in {
                    "/api/studio/command",
                    "/api/studio/templates/av-enhance-v27/preview",
                    "/api/studio/templates/av-enhance-v27/publication-preview",
                    "/api/studio/rerun-preview",
                }
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
                envelope: BaseModel
                if parsed.path == "/api/studio/templates/av-enhance-v27/preview":
                    envelope = application.preview_av_enhance_v27(payload)
                elif parsed.path == "/api/studio/templates/av-enhance-v27/publication-preview":
                    envelope = application.preview_av27_publication(payload)
                elif parsed.path == "/api/studio/rerun-preview":
                    envelope = application.preview_rerun(payload)
                else:
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

        def _host_options(self) -> None:
            """只为精确 launcher Origin 提供 HostBridge CORS preflight。"""

            if host_bridge is None:
                self._host_unavailable()
                return
            try:
                parsed = self._host_request_target()
                if parsed.path not in {
                    _HOST_CAPABILITIES_ROUTE,
                    _HOST_ACTIONS_ROUTE,
                    _HOST_INVOKE_ROUTE,
                    _HOST_PREVIEW_ROUTE,
                    _HANDOFF_IMPORT_PREVIEW,
                    _HANDOFF_IMPORT_CONFIRM,
                    *_DATA_ROUTES,
                }:
                    raise HostBridgeFailure(
                        "E_HOST_BRIDGE_ROUTE",
                        "未知 HostBridge route",
                        http_status=HTTPStatus.NOT_FOUND,
                    )
                self._authorize_host_preflight(host_bridge)
            except HostBridgeFailure as error:
                self._host_error(error, session=host_bridge)
                return
            self.send_response(HTTPStatus.NO_CONTENT)
            methods = "GET, OPTIONS" if parsed.path == _HOST_CAPABILITIES_ROUTE else "POST, OPTIONS"
            self._host_cors(host_bridge, methods=methods)
            self.end_headers()

        def _host_get(self) -> None:
            """HostBridge 唯一 GET 是无副作用 capability 状态。"""

            if host_bridge is None:
                self._host_unavailable()
                return
            try:
                parsed = self._host_request_target()
                self._authorize_host(host_bridge)
                if parsed.path != _HOST_CAPABILITIES_ROUTE:
                    if parsed.path in {
                        _HOST_ACTIONS_ROUTE,
                        _HOST_INVOKE_ROUTE,
                        _HOST_PREVIEW_ROUTE,
                        _HANDOFF_IMPORT_PREVIEW,
                        _HANDOFF_IMPORT_CONFIRM,
                        *_DATA_ROUTES,
                    }:
                        raise HostBridgeFailure(
                            "E_HOST_BRIDGE_METHOD",
                            "HostBridge 动作 route 只接受 POST",
                            http_status=HTTPStatus.METHOD_NOT_ALLOWED,
                        )
                    raise HostBridgeFailure(
                        "E_HOST_BRIDGE_ROUTE",
                        "未知 HostBridge route",
                        http_status=HTTPStatus.NOT_FOUND,
                    )
                envelope = host_bridge.inspect_capabilities()
            except HostBridgeFailure as error:
                self._host_error(error, session=host_bridge)
                return
            self._host_json(
                HTTPStatus.OK,
                envelope.model_dump(mode="json"),
                host_bridge,
                methods="GET, OPTIONS",
            )

        def _host_post(self) -> None:
            """授权、读取有限 JSON，并执行两步票据 HostBridge wire。"""

            if host_bridge is None:
                self._host_unavailable()
                return
            try:
                parsed = self._host_request_target()
                self._authorize_host(host_bridge)
                if parsed.path not in {
                    _HOST_ACTIONS_ROUTE,
                    _HOST_INVOKE_ROUTE,
                    _HOST_PREVIEW_ROUTE,
                    _HANDOFF_IMPORT_PREVIEW,
                    _HANDOFF_IMPORT_CONFIRM,
                    *_DATA_ROUTES,
                }:
                    raise HostBridgeFailure(
                        "E_HOST_BRIDGE_ROUTE",
                        "未知 HostBridge route",
                        http_status=HTTPStatus.NOT_FOUND,
                    )
                payload = self._read_host_payload()
                if parsed.path == _HOST_ACTIONS_ROUTE:
                    response_payload = host_bridge.issue_user_action(payload).model_dump(
                        mode="json"
                    )
                    status = HTTPStatus.CREATED
                elif parsed.path == _HOST_PREVIEW_ROUTE:
                    if preview_cache is None:
                        raise HostBridgeFailure(
                            "E_PREVIEW_UNAVAILABLE",
                            "当前宿主未启用媒体预览",
                            http_status=HTTPStatus.SERVICE_UNAVAILABLE,
                        )
                    response_payload = preview_cache.preview(
                        payload, session=host_bridge, application=application
                    ).model_dump(mode="json")
                    status = HTTPStatus.OK
                elif parsed.path in {_HANDOFF_IMPORT_PREVIEW, _HANDOFF_IMPORT_CONFIRM}:
                    operation = (
                        handoff_import.preview
                        if parsed.path == _HANDOFF_IMPORT_PREVIEW
                        else handoff_import.confirm
                    )
                    response_payload = operation(
                        payload, session=host_bridge, application=application
                    ).model_dump(mode="json")
                    status = HTTPStatus.OK
                elif parsed.path.startswith(f"{_INBOX_PREFIX}/"):
                    inbox_operation = {
                        "observe": handoff_inbox.observe,
                        "preview": handoff_inbox.preview,
                        "confirm": handoff_inbox.confirm,
                    }[parsed.path.rsplit("/", 1)[-1]]
                    response_payload = inbox_operation(
                        payload, session=host_bridge, application=application
                    ).model_dump(mode="json")
                    status = HTTPStatus.OK
                elif parsed.path.startswith(f"{_STORAGE_PREFIX}/"):
                    response_payload = storage_api.invoke(
                        parsed.path.rsplit("/", 1)[-1],
                        payload,
                        session=host_bridge,
                        application=application,
                    ).model_dump(mode="json")
                    status = HTTPStatus.OK
                else:
                    response_payload = host_bridge.invoke(payload).model_dump(mode="json")
                    status = HTTPStatus.OK
            except HostBridgeFailure as error:
                self._host_error(error, session=host_bridge)
                return
            except ProjectServiceError as error:
                self._host_error(
                    HostBridgeFailure(error.code, error.message, http_status=error.http_status),
                    session=host_bridge,
                )
                return
            self._host_json(status, response_payload, host_bridge, methods="POST, OPTIONS")

        def _read_host_payload(self) -> object:
            content_types = self.headers.get_all("Content-Type", [])
            if (
                len(content_types) != 1
                or content_types[0].partition(";")[0].strip().casefold() != "application/json"
            ):
                raise HostBridgeFailure(
                    "E_HOST_BRIDGE_CONTENT_TYPE",
                    "HostBridge POST 必须使用 application/json",
                    http_status=HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                )
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if not 1 <= length <= _MAX_HOST_BODY_BYTES:
                raise HostBridgeFailure(
                    "E_HOST_BRIDGE_BODY_SIZE",
                    "HostBridge request body 大小非法",
                    http_status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                )
            try:
                return _load_json(self.rfile.read(length))
            except _JsonPayloadError as error:
                raise HostBridgeFailure(
                    "E_HOST_BRIDGE_JSON",
                    str(error),
                    http_status=HTTPStatus.BAD_REQUEST,
                ) from error

        def _host_request_target(self) -> SplitResult:
            parsed = urlsplit(self.path)
            if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
                raise HostBridgeFailure(
                    "E_HOST_BRIDGE_ROUTE",
                    "HostBridge request target 必须是无 query 的本地 origin-form",
                    http_status=HTTPStatus.NOT_FOUND,
                )
            return parsed

        def _is_host_bridge_target(self) -> bool:
            """用 path 前缀隔离 HostBridge，畸形 target 也不得落入普通 CORS。"""

            return self.path.startswith(
                (_HOST_BRIDGE_PREFIX, _HANDOFF_IMPORT_PREFIX, _INBOX_PREFIX, _STORAGE_PREFIX)
            )

        def _authorize_host(self, session: HostBridgeSession) -> None:
            origins = self.headers.get_all("Origin", [])
            tokens = self.headers.get_all(HOST_TOKEN_HEADER, [])
            session.authorize(
                client_host=self.client_address[0],
                origin=origins[0] if len(origins) == 1 else None,
                token=tokens[0] if len(tokens) == 1 else None,
            )

        def _authorize_host_preflight(self, session: HostBridgeSession) -> None:
            origins = self.headers.get_all("Origin", [])
            try:
                loopback = ip_address(self.client_address[0]).is_loopback
            except ValueError:
                loopback = False
            if not loopback:
                raise HostBridgeFailure(
                    "E_HOST_BRIDGE_LAN_FORBIDDEN",
                    "HostBridge 只接受当前主机 loopback 请求",
                    http_status=HTTPStatus.FORBIDDEN,
                )
            if len(origins) != 1 or origins[0] != session.studio_origin:
                raise HostBridgeFailure(
                    "E_HOST_BRIDGE_ORIGIN",
                    "Origin 与 launcher 固定的 Studio Origin 不一致",
                    http_status=HTTPStatus.FORBIDDEN,
                )

        def _host_json(
            self,
            status: HTTPStatus,
            payload: object,
            session: HostBridgeSession,
            *,
            methods: str,
        ) -> None:
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
            self._host_cors(session, methods=methods)
            self.end_headers()
            if not _write_response_body(self.wfile, data):
                self.close_connection = True

        def _host_error(
            self,
            error: HostBridgeFailure,
            *,
            session: HostBridgeSession | None = None,
        ) -> None:
            if session is None:
                self._host_unavailable(error)
                return
            self._host_json(
                HTTPStatus(error.http_status),
                {"error": {"code": error.code, "message": error.message[:4096]}},
                session,
                methods=(
                    "GET, OPTIONS"
                    if urlsplit(self.path).path == _HOST_CAPABILITIES_ROUTE
                    else "POST, OPTIONS"
                ),
            )

        def _host_unavailable(self, error: HostBridgeFailure | None = None) -> None:
            """未注入 launcher session 时返回无 CORS 的显式失败且不暴露能力。"""

            failure = error or HostBridgeFailure(
                "E_HOST_BRIDGE_UNAVAILABLE",
                "当前 Project Service 未由桌面 launcher 注入 HostBridge session",
                http_status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
            data = json.dumps(
                {"error": {"code": failure.code, "message": failure.message[:4096]}},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            self.send_response(HTTPStatus(failure.http_status))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not _write_response_body(self.wfile, data):
                self.close_connection = True

        def _host_cors(self, session: HostBridgeSession, *, methods: str) -> None:
            origins = self.headers.get_all("Origin", [])
            if len(origins) == 1 and origins[0] == session.studio_origin:
                self.send_header("Access-Control-Allow-Origin", session.studio_origin)
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", f"Content-Type, {HOST_TOKEN_HEADER}")
            self.send_header("Access-Control-Allow-Methods", methods)

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
    host_bridge: HostBridgeSession | None = None,
    preview_cache: PreviewCache | None = None,
) -> ThreadingHTTPServer:
    """构造只绑定 ``127.0.0.1`` 的 server；调用方管理生命周期。"""

    if not 1024 <= port <= 65_535:
        raise ProjectServiceError(
            "E_PROJECT_SERVICE_PORT", "port 必须位于 1024..65535", http_status=400
        )
    return ThreadingHTTPServer(
        ("127.0.0.1", port),
        make_project_service_handler(
            application, host_bridge=host_bridge, preview_cache=preview_cache
        ),
    )


__all__ = ["make_project_service_handler", "serve_project_service"]
