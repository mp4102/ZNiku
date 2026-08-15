"""提供仅监听 loopback 的真实媒体候选 Application host。

host 在进程启动时固定 reference 与 Runtime root；HTTP 请求不能提交路径或命令。所有 mutation 都在互斥
锁内调用 Runtime command，响应永远重新从持久 snapshot 生成。该服务不是公网 API、产品 CLI 或第二套
状态机。
"""

from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from zniku.contracts import ContractViolation

from .projection import RealHostEnvelope, build_host_envelope
from .runtime import RealMediaCandidateRuntime


class RealMediaHostApplication:
    """固定本机 policy 的 Runtime command facade。"""

    def __init__(
        self,
        *,
        root: Path,
        reference: Path,
        clip_start_seconds: int = 28,
        clip_duration_seconds: int = 4,
    ) -> None:
        self._root = root.resolve(strict=False)
        self._reference = reference.resolve(strict=True)
        self._clip_start_seconds = clip_start_seconds
        self._clip_duration_seconds = clip_duration_seconds
        self._lock = threading.Lock()

    def inspect(self) -> RealHostEnvelope:
        with self._lock:
            return build_host_envelope(self._load())

    def command(self, payload: dict[str, Any]) -> RealHostEnvelope:
        with self._lock:
            operation = payload.get("operation")
            allowed = {"operation", "plan_node_id"}
            if set(payload) - allowed:
                raise ContractViolation("E_REAL_HOST_UNKNOWN_FIELD", "host command 含未知字段")
            if operation == "start":
                if self._root.exists():
                    runtime = self._load()
                    if runtime is None:  # pragma: no cover - 防御性分支
                        raise ContractViolation("E_REAL_HOST_ROOT", "工作根存在但不是候选 Run")
                else:
                    runtime = RealMediaCandidateRuntime.create(
                        root=self._root,
                        reference=self._reference,
                        clip_start_seconds=self._clip_start_seconds,
                        clip_duration_seconds=self._clip_duration_seconds,
                    )
            else:
                runtime = self._load()
                if runtime is None:
                    raise ContractViolation("E_REAL_HOST_NOT_STARTED", "候选 Run 尚未启动")
                plan_node_id = payload.get("plan_node_id")
                if operation == "advance_automatic":
                    self._advance_automatic(runtime)
                elif operation == "acceptance_fixture" and isinstance(plan_node_id, str):
                    handoff = runtime.prepare_manual(plan_node_id)
                    candidate = runtime.root / handoff.candidate_relative_path
                    if not candidate.exists():
                        runtime.create_acceptance_fixture(handoff.handoff_id)
                    runtime.submit_manual(handoff.handoff_id)
                elif operation == "retry" and isinstance(plan_node_id, str):
                    runtime.retry(plan_node_id)
                else:
                    raise ContractViolation("E_REAL_HOST_OPERATION", "未知或不完整 host operation")
            return build_host_envelope(runtime)

    def _load(self) -> RealMediaCandidateRuntime | None:
        if not self._root.exists():
            return None
        return RealMediaCandidateRuntime.open(root=self._root, reference=self._reference)

    @staticmethod
    def _advance_automatic(runtime: RealMediaCandidateRuntime) -> None:
        while True:
            ready = runtime.ready_nodes()
            automatic = []
            for node_id in ready:
                planned = next(
                    item for item in runtime.authority.plan.nodes if item.plan_node_id == node_id
                )
                manifest = runtime.manifest_for(planned.plan_node_id)
                if manifest is None or manifest.execution_mode.value == "automatic":
                    automatic.append(node_id)
            if not automatic:
                return
            for node_id in automatic:
                runtime.execute(node_id)


def make_handler(application: RealMediaHostApplication) -> type[BaseHTTPRequestHandler]:
    """绑定单一 Application instance，避免 handler 持有自己的 authority。"""

    class Handler(BaseHTTPRequestHandler):
        server_version = "ZNIKURealMediaHost/0.1.0"

        def do_OPTIONS(self) -> None:
            self.send_response(HTTPStatus.NO_CONTENT)
            self._cors()
            self.end_headers()

        def do_GET(self) -> None:
            if self.path != "/api/real-media/status":
                self._json_error(HTTPStatus.NOT_FOUND, "E_REAL_HOST_ROUTE")
                return
            self._json(HTTPStatus.OK, application.inspect().to_data())

        def do_POST(self) -> None:
            if self.path != "/api/real-media/command":
                self._json_error(HTTPStatus.NOT_FOUND, "E_REAL_HOST_ROUTE")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 1 <= length <= 16_384:
                    raise ContractViolation("E_REAL_HOST_BODY_SIZE", "request body 大小非法")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ContractViolation("E_REAL_HOST_BODY", "request body 必须是 object")
                envelope = application.command(payload)
            except (ContractViolation, json.JSONDecodeError, UnicodeDecodeError) as error:
                code = error.code if isinstance(error, ContractViolation) else "E_REAL_HOST_JSON"
                self._json_error(HTTPStatus.BAD_REQUEST, code)
                return
            self._json(HTTPStatus.OK, envelope.to_data())

        def log_message(self, format: str, *args: object) -> None:
            # 媒体路径和请求 payload 不写默认 access log。
            return

        def _json(self, status: HTTPStatus, payload: object) -> None:
            data = json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self._cors()
            self.end_headers()
            self.wfile.write(data)

        def _json_error(self, status: HTTPStatus, code: str) -> None:
            self._json(status, {"error": code})

        def _cors(self) -> None:
            origin = self.headers.get("Origin", "")
            if origin.startswith(("http://127.0.0.1:", "http://localhost:")):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    return Handler


def serve_real_media_host(*, root: Path, reference: Path, port: int = 8765) -> ThreadingHTTPServer:
    """构造 loopback server；调用方决定 ``serve_forever`` 生命周期。"""

    if not 1024 <= port <= 65_535:
        raise ContractViolation("E_REAL_HOST_PORT", "host port 越界")
    application = RealMediaHostApplication(root=root, reference=reference)
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(application))
