"""验证 ZNIKU Studio HostBridge 的 loopback 原生能力代理边界。

本模块只用于 v0.3.0 Phase 0 技术选型，不接入 Project Service、Graph 或 Runtime。它用可注入的
``HostPlatform`` 隔离真实对话框与系统启动动作，使 HTTP、授权、取消和路径规则都能在不弹窗的测试中
验证。该工具随 Phase 0 证据保留但不属于稳定 API，正式实现不得直接依赖它。
"""

from __future__ import annotations

import json
import secrets
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from time import monotonic
from typing import Final, Literal, Protocol, cast
from urllib.parse import urlsplit

_MAX_BODY_BYTES: Final = 64 * 1024
_ACTION_TTL_SECONDS: Final = 5.0
_MAX_PENDING_ACTIONS: Final = 64
_TOKEN_HEADER: Final = "X-ZNIKU-Host-Token"


class HostBridgeFailure(RuntimeError):
    """表示 HostBridge 拒绝请求或宿主动作失败，并保留稳定错误码。"""

    def __init__(self, code: str, message: str, *, http_status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


type HostCapability = Literal[
    "open_file",
    "open_files",
    "select_directory",
    "save_file",
    "reveal_in_file_manager",
    "open_with_system_player",
]

HOST_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {
        "open_file",
        "open_files",
        "select_directory",
        "save_file",
        "reveal_in_file_manager",
        "open_with_system_player",
    }
)
_DIALOG_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {"open_file", "open_files", "select_directory", "save_file"}
)
_SYSTEM_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {"reveal_in_file_manager", "open_with_system_player"}
)


@dataclass(frozen=True, slots=True)
class LaunchCommand:
    """描述由后端固定生成的进程调用；调用方不能提供 executable 或额外 argv。"""

    executable: str
    argv: tuple[str, ...]
    shell: Literal[False] = False


@dataclass(frozen=True, slots=True)
class HostBridgeResult:
    """表示一次系统动作的有限结果，不携带媒体内容或 Project mutation。"""

    status: Literal["selected", "cancelled", "launched"]
    paths: tuple[str, ...] = ()

    def to_json(self) -> dict[str, object]:
        """返回稳定、闭合的 HTTP JSON 结果。"""

        return {"status": self.status, "paths": list(self.paths)}


class HostPlatform(Protocol):
    """抽象对话框和系统启动动作，测试可注入无窗口实现。"""

    def choose_paths(
        self,
        capability: HostCapability,
        arguments: Mapping[str, object],
    ) -> Sequence[str] | None:
        """返回宿主选择路径；取消时返回 ``None`` 或空序列。"""

    def launch(self, command: LaunchCommand) -> None:
        """执行后端生成的固定 argv 命令。"""


@dataclass(frozen=True, slots=True)
class _UserAction:
    capability: HostCapability
    expires_at: float


class HostBridgeSession:
    """管理单次 launcher session 的 token、精确 Origin 和一次性用户动作票据。"""

    def __init__(
        self,
        platform: HostPlatform,
        *,
        studio_origin: str,
        clock: Callable[[], float] = monotonic,
        token: str | None = None,
    ) -> None:
        self.platform = platform
        self.studio_origin = _validate_studio_origin(studio_origin)
        self.token = token or secrets.token_urlsafe(32)
        if len(self.token) < 32 or any(character.isspace() for character in self.token):
            raise ValueError("HostBridge session token 必须是至少 32 字符的无空白随机值")
        self._clock = clock
        self._actions: dict[str, _UserAction] = {}
        self._lock = threading.Lock()

    def authorize(self, *, client_host: str, origin: str | None, token: str | None) -> None:
        """在任何 Host capability 执行前验证 loopback、精确 Origin 与 session token。"""

        try:
            is_loopback = ip_address(client_host).is_loopback
        except ValueError:
            is_loopback = False
        if not is_loopback:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_LAN_FORBIDDEN",
                "HostBridge 只接受当前主机 loopback 请求",
                http_status=HTTPStatus.FORBIDDEN,
            )
        if origin != self.studio_origin:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_ORIGIN",
                "Origin 与 launcher 固定的 Studio Origin 不一致",
                http_status=HTTPStatus.FORBIDDEN,
            )
        if token is None or not secrets.compare_digest(token, self.token):
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_SESSION",
                "HostBridge session token 无效",
                http_status=HTTPStatus.FORBIDDEN,
            )

    def issue_user_action(self, capability_value: object) -> tuple[str, HostCapability]:
        """签发短时、单 capability、一次性票据；只能由显式 POST 用户动作流程调用。"""

        capability = _parse_capability(capability_value)
        action_id = secrets.token_urlsafe(24)
        with self._lock:
            now = self._clock()
            self._actions = {
                key: action for key, action in self._actions.items() if action.expires_at > now
            }
            if len(self._actions) >= _MAX_PENDING_ACTIONS:
                raise HostBridgeFailure(
                    "E_HOST_BRIDGE_ACTION_LIMIT",
                    "待消费用户动作票据已达到上限",
                    http_status=HTTPStatus.TOO_MANY_REQUESTS,
                )
            self._actions[action_id] = _UserAction(
                capability=capability,
                expires_at=now + _ACTION_TTL_SECONDS,
            )
        return action_id, capability

    def invoke(
        self,
        *,
        action_id: object,
        capability_value: object,
        arguments_value: object,
    ) -> HostBridgeResult:
        """消费一次性用户动作并执行闭集 capability；失败或取消都不会产生 Project 副作用。"""

        capability = _parse_capability(capability_value)
        if not isinstance(action_id, str) or not action_id or len(action_id) > 256:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_ACTION_INVALID",
                "user_action_id 格式无效",
                http_status=HTTPStatus.BAD_REQUEST,
            )
        with self._lock:
            action = self._actions.pop(action_id, None)
        if action is None:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_ACTION_REQUIRED",
                "必须先通过显式用户动作取得一次性票据",
                http_status=HTTPStatus.CONFLICT,
            )
        if action.expires_at <= self._clock():
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_ACTION_EXPIRED",
                "用户动作票据已过期",
                http_status=HTTPStatus.CONFLICT,
            )
        if action.capability != capability:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_ACTION_MISMATCH",
                "用户动作票据与 capability 不匹配",
                http_status=HTTPStatus.CONFLICT,
            )

        arguments = _validate_arguments(capability, arguments_value)
        if capability in _DIALOG_CAPABILITIES:
            return self._invoke_dialog(capability, arguments)
        if capability in _SYSTEM_CAPABILITIES:
            return self._invoke_system_action(capability, arguments)
        raise AssertionError("closed capability set 漏掉处理分支")

    def _invoke_dialog(
        self,
        capability: HostCapability,
        arguments: Mapping[str, object],
    ) -> HostBridgeResult:
        try:
            selected = self.platform.choose_paths(capability, arguments)
        except Exception as error:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_DIALOG",
                (str(error) or type(error).__name__)[:4096],
                http_status=HTTPStatus.INTERNAL_SERVER_ERROR,
            ) from error
        if not selected:
            return HostBridgeResult(status="cancelled")
        paths = _normalize_selected_paths(capability, selected)
        return HostBridgeResult(status="selected", paths=paths)

    def _invoke_system_action(
        self,
        capability: HostCapability,
        arguments: Mapping[str, object],
    ) -> HostBridgeResult:
        raw_path = cast(str, arguments["path"])
        path = _existing_absolute_path(raw_path)
        if capability == "open_with_system_player" and not path.is_file():
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_PATH_KIND",
                "系统播放器只能打开普通文件",
                http_status=HTTPStatus.UNPROCESSABLE_ENTITY,
            )
        command = _launch_command(capability, path)
        try:
            self.platform.launch(command)
        except Exception as error:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_LAUNCH",
                (str(error) or type(error).__name__)[:4096],
                http_status=HTTPStatus.INTERNAL_SERVER_ERROR,
            ) from error
        return HostBridgeResult(status="launched", paths=(str(path),))


class WindowsTkPlatform:
    """Windows 候选 B 的最小宿主实现；测试不会实例化真实对话框或启动进程。"""

    def __init__(self) -> None:
        self._dialog_lock = threading.Lock()

    def choose_paths(
        self,
        capability: HostCapability,
        arguments: Mapping[str, object],
    ) -> Sequence[str] | None:
        """在同一 handler 线程创建并销毁隐藏 Tk root，且串行化所有原生对话框。"""

        import tkinter as tk
        from tkinter import filedialog

        title = cast(str | None, arguments.get("title"))
        extensions = cast(tuple[str, ...], arguments.get("extensions", ()))
        filetypes = [("允许的文件", " ".join(f"*{item}" for item in extensions))]
        if not filetypes[0][1]:
            filetypes = [("所有文件", "*.*")]
        with self._dialog_lock:
            root = tk.Tk()
            root.withdraw()
            try:
                if capability == "open_file":
                    value = filedialog.askopenfilename(
                        parent=root,
                        title=title,
                        filetypes=filetypes,
                    )
                    return (value,) if value else None
                if capability == "open_files":
                    values = filedialog.askopenfilenames(
                        parent=root,
                        title=title,
                        filetypes=filetypes,
                    )
                    return tuple(values) or None
                if capability == "select_directory":
                    value = filedialog.askdirectory(parent=root, title=title, mustexist=True)
                    return (value,) if value else None
                if capability == "save_file":
                    value = filedialog.asksaveasfilename(
                        parent=root,
                        title=title,
                        initialfile=cast(str | None, arguments.get("suggested_name")),
                        filetypes=filetypes,
                    )
                    return (value,) if value else None
                raise AssertionError("非对话框 capability 进入 choose_paths")
            finally:
                root.destroy()

    def launch(self, command: LaunchCommand) -> None:
        """使用固定 executable 与 argv 数组启动系统动作，明确禁止 shell。"""

        # executable/argv 由闭集 capability 在后端固定生成，前端无注入入口。
        subprocess.Popen(
            [command.executable, *command.argv],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=command.shell,
            close_fds=True,
        )


def make_host_bridge_handler(session: HostBridgeSession) -> type[BaseHTTPRequestHandler]:
    """构造严格 HostBridge HTTP handler；所有有副作用动作只接受授权 POST。"""

    class Handler(BaseHTTPRequestHandler):
        server_version = "ZNIKUHostBridgePrototype/0.3.0"

        def do_OPTIONS(self) -> None:
            try:
                self._authorize_preflight()
            except HostBridgeFailure as error:
                self._error(error)
                return
            self.send_response(HTTPStatus.NO_CONTENT)
            self._cors()
            self.end_headers()

        def do_GET(self) -> None:
            try:
                self._authorize()
            except HostBridgeFailure as error:
                self._error(error)
                return
            self._error(
                HostBridgeFailure(
                    "E_HOST_BRIDGE_METHOD",
                    "HostBridge capability 只接受 POST",
                    http_status=HTTPStatus.METHOD_NOT_ALLOWED,
                )
            )

        def do_POST(self) -> None:
            try:
                self._authorize()
                payload = self._read_payload()
                if self.path == "/api/host-bridge/user-actions":
                    _require_exact_keys(payload, frozenset({"capability"}))
                    action_id, capability = session.issue_user_action(payload["capability"])
                    self._json(
                        HTTPStatus.CREATED,
                        {
                            "user_action_id": action_id,
                            "capability": capability,
                            "expires_in_seconds": _ACTION_TTL_SECONDS,
                        },
                    )
                    return
                if self.path == "/api/host-bridge/invoke":
                    _require_exact_keys(
                        payload,
                        frozenset({"user_action_id", "capability", "arguments"}),
                    )
                    result = session.invoke(
                        action_id=payload["user_action_id"],
                        capability_value=payload["capability"],
                        arguments_value=payload["arguments"],
                    )
                    self._json(HTTPStatus.OK, result.to_json())
                    return
                raise HostBridgeFailure(
                    "E_HOST_BRIDGE_ROUTE",
                    "未知 HostBridge route",
                    http_status=HTTPStatus.NOT_FOUND,
                )
            except HostBridgeFailure as error:
                self._error(error)

        def log_message(self, format: str, *args: object) -> None:
            # 路径、token 与请求 body 不进入默认 access log。
            del format, args

        def _authorize_preflight(self) -> None:
            origins = self.headers.get_all("Origin", [])
            origin = origins[0] if len(origins) == 1 else None
            try:
                is_loopback = ip_address(self.client_address[0]).is_loopback
            except ValueError:
                is_loopback = False
            if not is_loopback:
                raise HostBridgeFailure(
                    "E_HOST_BRIDGE_LAN_FORBIDDEN",
                    "HostBridge 只接受当前主机 loopback 请求",
                    http_status=HTTPStatus.FORBIDDEN,
                )
            if origin != session.studio_origin:
                raise HostBridgeFailure(
                    "E_HOST_BRIDGE_ORIGIN",
                    "Origin 与 launcher 固定的 Studio Origin 不一致",
                    http_status=HTTPStatus.FORBIDDEN,
                )

        def _authorize(self) -> None:
            origins = self.headers.get_all("Origin", [])
            tokens = self.headers.get_all(_TOKEN_HEADER, [])
            session.authorize(
                client_host=self.client_address[0],
                origin=origins[0] if len(origins) == 1 else None,
                token=tokens[0] if len(tokens) == 1 else None,
            )

        def _read_payload(self) -> dict[str, object]:
            if self.path not in {
                "/api/host-bridge/user-actions",
                "/api/host-bridge/invoke",
            }:
                raise HostBridgeFailure(
                    "E_HOST_BRIDGE_ROUTE",
                    "未知 HostBridge route",
                    http_status=HTTPStatus.NOT_FOUND,
                )
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
            if not 1 <= length <= _MAX_BODY_BYTES:
                raise HostBridgeFailure(
                    "E_HOST_BRIDGE_BODY_SIZE",
                    "HostBridge request body 大小非法",
                    http_status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                )
            return _load_json_object(self.rfile.read(length))

        def _json(self, status: HTTPStatus, payload: Mapping[str, object]) -> None:
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
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                self.close_connection = True

        def _error(self, error: HostBridgeFailure) -> None:
            self._json(
                HTTPStatus(error.http_status),
                {"error": {"code": error.code, "message": error.message[:4096]}},
            )

        def _cors(self) -> None:
            origins = self.headers.get_all("Origin", [])
            if len(origins) == 1 and origins[0] == session.studio_origin:
                self.send_header("Access-Control-Allow-Origin", session.studio_origin)
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", f"Content-Type, {_TOKEN_HEADER}")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")

    return Handler


def serve_host_bridge(
    session: HostBridgeSession,
    *,
    port: int = 0,
) -> ThreadingHTTPServer:
    """构造仅绑定 IPv4 loopback 的技术验证 server；``0`` 只用于自动选择测试端口。"""

    if port != 0 and not 1024 <= port <= 65_535:
        raise ValueError("HostBridge port 必须是 0 或位于 1024..65535")
    return ThreadingHTTPServer(("127.0.0.1", port), make_host_bridge_handler(session))


def probe_windows_tk_without_window() -> dict[str, str | bool]:
    """探测候选 B 的 Tk 对话框依赖；只创建 Tcl interpreter，不创建窗口。"""

    import tkinter
    from tkinter import filedialog

    interpreter = tkinter.Tcl()
    return {
        "windows": sys.platform == "win32",
        "tk_version": str(tkinter.TkVersion),
        "tcl_patchlevel": str(interpreter.eval("info patchlevel")),
        "filedialog_importable": callable(filedialog.askopenfilename),
        "window_manager_loaded": bool(interpreter.eval("info commands wm")),
    }


def _validate_studio_origin(value: str) -> str:
    """要求 launcher 提供带显式 port 的 canonical IPv4 loopback HTTP Origin。"""

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("Studio Origin 格式无效") from error
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or port is None
        or not 1024 <= port <= 65_535
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or value != f"http://127.0.0.1:{port}"
    ):
        raise ValueError("Studio Origin 必须是 http://127.0.0.1:<port> 的精确形式")
    return value


def _parse_capability(value: object) -> HostCapability:
    if not isinstance(value, str) or value not in HOST_CAPABILITIES:
        raise HostBridgeFailure(
            "E_HOST_BRIDGE_CAPABILITY",
            "capability 未知或不受支持",
            http_status=HTTPStatus.UNPROCESSABLE_ENTITY,
        )
    return cast(HostCapability, value)


def _validate_arguments(
    capability: HostCapability,
    value: object,
) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise HostBridgeFailure(
            "E_HOST_BRIDGE_ARGUMENTS",
            "arguments 必须是 JSON object",
            http_status=HTTPStatus.BAD_REQUEST,
        )
    arguments = cast(dict[str, object], value)
    if capability in {"reveal_in_file_manager", "open_with_system_player"}:
        _require_exact_keys(arguments, frozenset({"path"}))
        _require_nonempty_string(arguments["path"], field="path")
        return arguments

    allowed = {"title"}
    if capability in {"open_file", "open_files", "save_file"}:
        allowed.add("extensions")
    if capability == "save_file":
        allowed.add("suggested_name")
    if not set(arguments).issubset(allowed):
        unknown = sorted(set(arguments).difference(allowed))
        raise HostBridgeFailure(
            "E_HOST_BRIDGE_ARGUMENTS",
            "arguments 含未知字段：" + ", ".join(unknown),
            http_status=HTTPStatus.BAD_REQUEST,
        )
    for field in ("title", "suggested_name"):
        if field in arguments:
            _require_nonempty_string(arguments[field], field=field)
    if "extensions" in arguments:
        arguments["extensions"] = _validate_extensions(arguments["extensions"])
    return arguments


def _require_nonempty_string(value: object, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or "\x00" in value
        or len(value) > 4096
    ):
        raise HostBridgeFailure(
            "E_HOST_BRIDGE_ARGUMENTS",
            f"{field} 必须是无 NUL 的非空 string",
            http_status=HTTPStatus.BAD_REQUEST,
        )
    return value


def _validate_extensions(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= 32:
        raise HostBridgeFailure(
            "E_HOST_BRIDGE_ARGUMENTS",
            "extensions 必须是 1..32 个扩展名",
            http_status=HTTPStatus.BAD_REQUEST,
        )
    result: list[str] = []
    for item in value:
        if (
            not isinstance(item, str)
            or len(item) not in range(2, 17)
            or not item.startswith(".")
            or not item[1:].isascii()
            or not item[1:].isalnum()
        ):
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_ARGUMENTS",
                "extension 必须是 .mkv 形式的 ASCII 字母数字扩展名",
                http_status=HTTPStatus.BAD_REQUEST,
            )
        normalized = item.casefold()
        if normalized in result:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_ARGUMENTS",
                "extensions 不得重复",
                http_status=HTTPStatus.BAD_REQUEST,
            )
        result.append(normalized)
    return tuple(result)


def _normalize_selected_paths(
    capability: HostCapability,
    values: Sequence[str],
) -> tuple[str, ...]:
    if capability != "open_files" and len(values) != 1:
        raise HostBridgeFailure(
            "E_HOST_BRIDGE_DIALOG_RESULT",
            "单选对话框必须返回一个路径",
            http_status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )
    paths: list[str] = []
    for value in values:
        _require_nonempty_string(value, field="dialog path")
        path = Path(value)
        if not path.is_absolute():
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_DIALOG_RESULT",
                "原生对话框必须返回绝对路径",
                http_status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        try:
            if capability == "save_file":
                resolved = path.resolve(strict=False)
                if not resolved.parent.is_dir():
                    raise OSError("save target 父目录不存在")
            else:
                resolved = path.resolve(strict=True)
                if capability == "select_directory" and not resolved.is_dir():
                    raise OSError("选择结果不是目录")
                if capability in {"open_file", "open_files"} and not resolved.is_file():
                    raise OSError("选择结果不是普通文件")
        except OSError as error:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_DIALOG_RESULT",
                str(error),
                http_status=HTTPStatus.INTERNAL_SERVER_ERROR,
            ) from error
        normalized = str(resolved)
        if normalized in paths:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_DIALOG_RESULT",
                "多选结果不得重复",
                http_status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        paths.append(normalized)
    return tuple(paths)


def _existing_absolute_path(value: str) -> Path:
    _require_nonempty_string(value, field="path")
    path = Path(value)
    if not path.is_absolute():
        raise HostBridgeFailure(
            "E_HOST_BRIDGE_PATH",
            "系统动作路径必须是绝对路径",
            http_status=HTTPStatus.UNPROCESSABLE_ENTITY,
        )
    try:
        return path.resolve(strict=True)
    except OSError as error:
        raise HostBridgeFailure(
            "E_HOST_BRIDGE_PATH",
            str(error),
            http_status=HTTPStatus.UNPROCESSABLE_ENTITY,
        ) from error


def _launch_command(capability: HostCapability, path: Path) -> LaunchCommand:
    if capability == "reveal_in_file_manager":
        argv = (str(path),) if path.is_dir() else ("/select,", str(path))
        return LaunchCommand(executable="explorer.exe", argv=argv)
    if capability == "open_with_system_player":
        return LaunchCommand(
            executable="rundll32.exe",
            argv=("url.dll,FileProtocolHandler", str(path)),
        )
    raise AssertionError("非系统 capability 进入 launch command")


def _load_json_object(payload: bytes) -> dict[str, object]:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise HostBridgeFailure(
                    "E_HOST_BRIDGE_JSON",
                    f"JSON 重复字段：{key}",
                    http_status=HTTPStatus.BAD_REQUEST,
                )
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise HostBridgeFailure(
            "E_HOST_BRIDGE_JSON",
            f"非法 JSON number：{value}",
            http_status=HTTPStatus.BAD_REQUEST,
        )

    try:
        value = json.loads(
            payload,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise HostBridgeFailure(
            "E_HOST_BRIDGE_JSON",
            "request body 不是合法 UTF-8 JSON",
            http_status=HTTPStatus.BAD_REQUEST,
        ) from error
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise HostBridgeFailure(
            "E_HOST_BRIDGE_JSON",
            "request body 必须是 JSON object",
            http_status=HTTPStatus.BAD_REQUEST,
        )
    return cast(dict[str, object], value)


def _require_exact_keys(value: Mapping[str, object], expected: frozenset[str]) -> None:
    actual = frozenset(value)
    if actual != expected:
        raise HostBridgeFailure(
            "E_HOST_BRIDGE_FIELDS",
            "字段必须精确为：" + ", ".join(sorted(expected)),
            http_status=HTTPStatus.BAD_REQUEST,
        )


__all__ = [
    "HOST_CAPABILITIES",
    "HostBridgeFailure",
    "HostBridgeResult",
    "HostBridgeSession",
    "HostCapability",
    "HostPlatform",
    "LaunchCommand",
    "WindowsTkPlatform",
    "make_host_bridge_handler",
    "probe_windows_tk_without_window",
    "serve_host_bridge",
]
