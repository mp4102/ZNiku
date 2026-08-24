"""验证 Phase 3 loopback Project Service 的严格 HTTP 与并发读取边界。

host 测试只操作临时合成 Project；请求不能替换 work root、提交任意日志路径或引入新的运行状态。
"""

from __future__ import annotations

import http.client
import inspect
import json
import sqlite3
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from time import monotonic
from typing import Any, cast

from zniku.graph import (
    ExecutionMode,
    Graph,
    NodeDefinition,
    NodeInstance,
    PythonExecutorSpec,
)
from zniku.project import Project, ProjectStore
from zniku.project_service import (
    ProjectServiceApplication,
    make_project_service_handler,
    serve_project_service,
)
from zniku.runtime import PythonAdapterContext, PythonAdapterResult


def _source_definition() -> NodeDefinition:
    return NodeDefinition(
        type_id="test.phase3.HttpSource",
        version="1.0.0",
        parameter_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="tests.phase3:http-source"),
    )


def _store(tmp_path: Path, *, filename: str = "http.zniku") -> ProjectStore:
    definition = _source_definition()
    project = Project(
        project_id="project.phase3.http",
        name="Phase 3 HTTP 合成工程",
        graph=Graph(
            nodes=(
                NodeInstance(
                    node_id="source",
                    type_id=definition.type_id,
                    definition_version=definition.version,
                ),
            )
        ),
    )
    return ProjectStore.create(tmp_path / filename, project, (definition,))


@contextmanager
def _serve(application: ProjectServiceApplication) -> Iterator[tuple[str, ThreadingHTTPServer]]:
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_project_service_handler(application),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: object | None = None,
    raw: bytes | None = None,
    origin: str | None = None,
    content_type: str | None = "application/json",
    timeout: float = 5.0,
) -> tuple[int, dict[str, Any], Any]:
    if payload is not None and raw is not None:
        raise ValueError("payload 与 raw 不能同时提供")
    body = raw
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {}
    if body is not None and content_type is not None:
        headers["Content-Type"] = content_type
    if origin is not None:
        headers["Origin"] = origin
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        data = cast(dict[str, Any], json.loads(response.read()))
        return response.status, data, response.headers


def _post_command(base_url: str, payload: object) -> tuple[int, dict[str, Any], Any]:
    return _request(base_url, "/api/studio/command", method="POST", payload=payload)


def test_default_project_service_port_avoids_windows_reserved_range() -> None:
    signature = inspect.signature(serve_project_service)

    assert signature.parameters["port"].default == 18765


def test_http_routes_cors_and_strict_json_fail_closed(tmp_path: Path) -> None:
    application = ProjectServiceApplication(work_root=tmp_path / "work")
    with _serve(application) as (base_url, server):
        status, envelope, headers = _request(
            base_url,
            "/api/studio/status",
            origin="http://127.0.0.1:4173",
        )
        assert status == 200
        assert envelope["contract_version"] == "0.2.0"
        assert envelope["project_path"] is None
        assert headers.get("Access-Control-Allow-Origin") == "http://127.0.0.1:4173"
        assert headers.get("Cache-Control") == "no-store"

        status, failure, headers = _request(
            base_url,
            "/api/studio/status",
            origin="https://example.invalid",
        )
        assert status == 403
        assert failure["error"]["code"] == "E_PROJECT_SERVICE_ORIGIN"
        assert headers.get("Access-Control-Allow-Origin") is None

        status, failure, _ = _request(
            base_url,
            "/api/studio/status",
            origin="null",
        )
        assert status == 403
        assert failure["error"]["code"] == "E_PROJECT_SERVICE_ORIGIN"

        status, failure, _ = _request(base_url, "/api/studio/unknown")
        assert status == 404
        assert failure["error"]["code"] == "E_PROJECT_SERVICE_ROUTE"

        status, failure, _ = _request(
            base_url,
            "/api/studio/command",
            method="POST",
            raw=b'{"operation":"run_all","operation":"run_to"}',
        )
        assert status == 400
        assert failure["error"]["code"] == "E_PROJECT_SERVICE_JSON"

        status, failure, _ = _post_command(
            base_url,
            {"operation": "run_all", "unknown": True},
        )
        assert status == 422
        assert failure["error"]["code"] == "E_PROJECT_SERVICE_COMMAND_INVALID"

        status, failure, _ = _request(
            base_url,
            "/api/studio/command",
            method="POST",
            raw=b'{"operation":"run_to","node_id":NaN}',
        )
        assert status == 400
        assert failure["error"]["code"] == "E_PROJECT_SERVICE_JSON"

        status, failure, _ = _request(
            base_url,
            "/api/studio/command",
            method="POST",
            raw=b"",
        )
        assert status == 413
        assert failure["error"]["code"] == "E_PROJECT_SERVICE_BODY_SIZE"

        connection = http.client.HTTPConnection(
            "127.0.0.1",
            server.server_port,
            timeout=5,
        )
        try:
            connection.putrequest("POST", "/api/studio/command")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(4 * 1024 * 1024 + 1))
            connection.putheader("Connection", "close")
            connection.endheaders()
            response = connection.getresponse()
            oversized = cast(dict[str, Any], json.loads(response.read()))
            assert response.status == 413
            assert oversized["error"]["code"] == "E_PROJECT_SERVICE_BODY_SIZE"
        finally:
            connection.close()


def test_cross_origin_and_non_json_posts_have_no_project_side_effect(tmp_path: Path) -> None:
    application = ProjectServiceApplication(work_root=tmp_path / "work")
    target = tmp_path / "blocked.zniku"
    command = {
        "operation": "create_project",
        "path": str(target),
        "project_id": "project.phase3.blocked",
        "name": "不得创建",
    }
    raw = json.dumps(command, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    with _serve(application) as (base_url, server):
        for origin in ("https://attacker.example", "null", "http://localhost.evil:4173"):
            status, failure, headers = _request(
                base_url,
                "/api/studio/command",
                method="POST",
                raw=raw,
                origin=origin,
                content_type="text/plain",
            )
            assert status == 403
            assert failure["error"]["code"] == "E_PROJECT_SERVICE_ORIGIN"
            assert headers.get("Access-Control-Allow-Origin") is None
            assert not target.exists()
            assert application.inspect().project_path is None

        status, failure, headers = _request(
            base_url,
            "/api/studio/command",
            method="POST",
            raw=raw,
            origin="http://localhost:4173",
            content_type="text/plain; charset=utf-8",
        )
        assert status == 415
        assert failure["error"]["code"] == "E_PROJECT_SERVICE_CONTENT_TYPE"
        assert headers.get("Access-Control-Allow-Origin") == "http://localhost:4173"
        assert not target.exists()
        assert application.inspect().project_path is None

        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request(
                "OPTIONS",
                "/api/studio/command",
                headers={
                    "Origin": "http://localhost:4173",
                    "Access-Control-Request-Method": "POST",
                },
            )
            response = connection.getresponse()
            assert response.status == 204
            assert response.read() == b""
            assert response.getheader("Access-Control-Allow-Origin") == "http://localhost:4173"

            connection.request(
                "OPTIONS",
                "/api/studio/command",
                headers={
                    "Origin": "https://attacker.example",
                    "Access-Control-Request-Method": "POST",
                },
            )
            response = connection.getresponse()
            options_failure = cast(dict[str, Any], json.loads(response.read()))
            assert response.status == 403
            assert options_failure["error"]["code"] == "E_PROJECT_SERVICE_ORIGIN"
            assert response.getheader("Access-Control-Allow-Origin") is None
        finally:
            connection.close()

        assert not target.exists()
        assert application.inspect().project_path is None


def test_get_remains_available_and_mutation_rejects_while_worker_is_busy(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    def blocking_source(context: PythonAdapterContext) -> PythonAdapterResult:
        context.stdout_log_path.write_text("adapter running", encoding="utf-8")
        entered.set()
        if not release.wait(timeout=10):
            raise RuntimeError("合成 adapter 等待超时")
        return PythonAdapterResult()

    application = ProjectServiceApplication(
        work_root=tmp_path / "work",
        python_adapters={"tests.phase3:http-source": blocking_source},
    )
    with _serve(application) as (base_url, _):
        status, _, _ = _post_command(
            base_url,
            {"operation": "open_project", "path": str(store.path)},
        )
        assert status == 200
        status, started, _ = _post_command(base_url, {"operation": "run_all"})
        assert status == 200
        assert started["active_operation"] == "run_all"
        assert entered.wait(timeout=5)

        began = monotonic()
        status, running, _ = _request(base_url, "/api/studio/status", timeout=2)
        elapsed = monotonic() - began
        assert status == 200
        assert elapsed < 2
        assert running["active_operation"] == "run_all"
        node_runs = running["runs"][-1]["node_runs"]
        assert node_runs[0]["state"] == "running"

        status, failure, _ = _post_command(base_url, {"operation": "run_all"})
        assert status == 409
        assert failure["error"]["code"] == "E_PROJECT_SERVICE_BUSY"

        release.set()
        assert application.wait_until_idle(timeout=5)
        status, completed, _ = _request(base_url, "/api/studio/status")
        assert status == 200
        assert completed["active_operation"] is None
        assert completed["runs"][-1]["state"] == "completed"


def test_http_log_projection_does_not_follow_tampered_paths_outside_work_root(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path, filename="containment.zniku")

    def source(context: PythonAdapterContext) -> PythonAdapterResult:
        context.stdout_log_path.write_text("允许展示", encoding="utf-8")
        return PythonAdapterResult()

    application = ProjectServiceApplication(
        work_root=tmp_path / "work",
        python_adapters={"tests.phase3:http-source": source},
    )
    with _serve(application) as (base_url, _):
        assert (
            _post_command(
                base_url,
                {"operation": "open_project", "path": str(store.path)},
            )[0]
            == 200
        )
        run_status, run_payload, _ = _post_command(base_url, {"operation": "run_all"})
        assert run_status == 200, run_payload
        assert application.wait_until_idle(timeout=5)
        completed = application.inspect().runs[-1]
        node_run = completed.node_runs[0]

        outside = tmp_path / "outside"
        outside_logs = outside / "logs"
        outside_logs.mkdir(parents=True)
        (outside_logs / "stdout.log").write_text("不得通过 HTTP 泄露", encoding="utf-8")
        (outside_logs / "stderr.log").write_text("不得通过 HTTP 泄露", encoding="utf-8")
        with sqlite3.connect(store.path) as connection:
            connection.execute(
                "UPDATE node_runs SET work_dir = ?, log_path = ? WHERE node_run_id = ?",
                (str(outside), str(outside_logs), node_run.node_run_id),
            )

        status, envelope, _ = _request(base_url, "/api/studio/status")
        assert status == 200
        log = next(item for item in envelope["logs"] if item["node_run_id"] == node_run.node_run_id)
        assert log["stdout"] == ""
        assert log["stderr"] == ""
        assert log["stdout_available"] is False
        assert log["stderr_available"] is False
