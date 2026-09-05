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

import pytest

from zniku.avenhance_v27 import atomic_split_definition
from zniku.graph import (
    ExecutionMode,
    Graph,
    NodeDefinition,
    NodeInstance,
    PortSpec,
    PythonExecutorSpec,
)
from zniku.project import Project, ProjectStore
from zniku.project_service import (
    ProjectServiceApplication,
    make_project_service_handler,
    serve_project_service,
)
from zniku.project_service.host import _write_response_body
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
    if isinstance(payload, dict) and payload.get("operation") in {
        "save_project",
        "run_all",
        "run_to",
        "expand_av_enhance_v27",
        "rerun_from_here",
    }:
        _, current, _ = _request(base_url, "/api/studio/status")
        payload = {
            "project_session_id": current["project_session_id"],
            "expected_storage_revision": current["storage_revision"],
            **payload,
        }
        if payload["operation"] == "save_project":
            payload.setdefault("studio_state", current["studio_state"])
    return _request(base_url, "/api/studio/command", method="POST", payload=payload)


def test_default_project_service_port_avoids_windows_reserved_range() -> None:
    signature = inspect.signature(serve_project_service)

    assert signature.parameters["port"].default == 18765


@pytest.mark.parametrize(
    "error_type",
    (BrokenPipeError, ConnectionAbortedError, ConnectionResetError),
)
def test_response_disconnect_does_not_escape_request_handler(
    error_type: type[OSError],
) -> None:
    """浏览器停止轮询时只丢弃当前响应，不向 server 线程打印 traceback。"""

    class DisconnectingWriter:
        def write(self, data: bytes, /) -> int:
            del data
            raise error_type("客户端已断开")

    assert _write_response_body(DisconnectingWriter(), b"{}") is False


def test_http_routes_cors_and_strict_json_fail_closed(tmp_path: Path) -> None:
    application = ProjectServiceApplication(work_root=tmp_path / "work")
    with _serve(application) as (base_url, server):
        status, envelope, headers = _request(
            base_url,
            "/api/studio/status",
            origin="http://127.0.0.1:4173",
        )
        assert status == 200
        assert envelope["contract_version"] == "0.3.0"
        assert envelope["project_path"] is None
        assert headers.get("Access-Control-Allow-Origin") == "http://127.0.0.1:4173"
        assert headers.get("Cache-Control") == "no-store"

        status, presentations, _ = _request(base_url, "/api/studio/presentations")
        assert status == 200
        assert presentations == {
            "contract_version": "0.3.0",
            "catalog": {
                "contract_version": "0.3.0",
                "locale": "zh-CN",
                "categories": [
                    {
                        "category_id": "input",
                        "title": "导入素材",
                        "description": "选择只读源媒体并建立工作流入口。",
                        "order": 10,
                    },
                    {
                        "category_id": "transform",
                        "title": "画面处理",
                        "description": "转换、增强和补帧等视频处理步骤。",
                        "order": 20,
                    },
                    {
                        "category_id": "structure",
                        "title": "拆分与合并",
                        "description": "按顺序拆分或汇合视频分支。",
                        "order": 30,
                    },
                    {
                        "category_id": "delivery",
                        "title": "编码与输出",
                        "description": "编码、封装并发布最终文件。",
                        "order": 40,
                    },
                    {
                        "category_id": "av27",
                        "title": "AVEnhanceFlow v2.7.0",
                        "description": "由模板生成的精确 AVEnhanceFlow v2.7.0 节点。",
                        "order": 50,
                    },
                ],
                "nodes": [],
            },
            "diagnostics": [],
        }

        status, failure, _ = _request(base_url, "/api/studio/presentations?unknown=1")
        assert status == 400
        assert failure["error"]["code"] == "E_PROJECT_SERVICE_QUERY_INVALID"

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


@pytest.mark.parametrize("leaf_count", (1, 2, 3))
def test_presentation_route_mirrors_current_dynamic_split_without_side_effects(
    tmp_path: Path,
    leaf_count: int,
) -> None:
    """只读 endpoint 必须从当前 exact definition 读取动态端口且不触碰 Project/Runtime。"""

    definition = atomic_split_definition(leaf_count)
    project_path = tmp_path / f"dynamic-{leaf_count}.zniku"
    ProjectStore.create(
        project_path,
        Project(
            project_id=f"dynamic.split.{leaf_count}",
            name=f"动态分段 {leaf_count}",
            graph=Graph(),
        ),
        (definition,),
    )
    application = ProjectServiceApplication(work_root=tmp_path / "work")
    application.command({"operation": "open_project", "path": str(project_path)})
    before_status = application.inspect()
    before_database = project_path.read_bytes()

    with _serve(application) as (base_url, _):
        status, envelope, _ = _request(base_url, "/api/studio/presentations")

    assert status == 200
    node = next(
        item for item in envelope["catalog"]["nodes"] if item["type_id"] == definition.type_id
    )
    assert [item["port_id"] for item in node["ports"] if item["direction"] == "output"] == [
        port.port_id for port in definition.output_ports
    ]
    assert application.inspect() == before_status
    assert project_path.read_bytes() == before_database


def test_presentation_route_isolates_bad_third_party_entries_and_stays_available(
    tmp_path: Path,
) -> None:
    """第三方坏展示只能形成 warning，不能污染 status、Graph 或 Run。"""

    definitions = tuple(
        NodeDefinition(
            type_id=f"third.party.{suffix}",
            version="1.0.0",
            output_ports=(PortSpec(port_id="video", data_type="VideoFile"),),
            parameter_schema={
                "type": "object",
                "properties": {"mode": {"type": "string"}},
                "additionalProperties": False,
            },
            execution_mode=ExecutionMode.AUTOMATIC,
            executor=PythonExecutorSpec(adapter=f"tests.third_party:{suffix}"),
        )
        for suffix in ("callback", "version", "pointer", "port")
    )

    def node_payload(definition: NodeDefinition) -> dict[str, object]:
        return {
            "type_id": definition.type_id,
            "definition_version": definition.version,
            "title": "第三方节点",
            "description": "合成 Presentation endpoint 隔离测试。",
            "category_id": "plugin",
            "icon_token": "transform",
            "palette_level": "advanced",
            "keywords": [],
            "parameter_groups": [{"group_id": "basic", "title": "基础设置", "order": 1}],
            "parameters": [
                {
                    "parameter_pointer": "/mode",
                    "label": "模式",
                    "group_id": "basic",
                    "order": 1,
                    "control_hint": "text",
                }
            ],
            "ports": [{"direction": "output", "port_id": "video", "label": "输出视频"}],
            "card_summary_paths": ["/mode"],
        }

    callback = {**node_payload(definitions[0]), "callback": "alert(1)"}
    wrong_version = {**node_payload(definitions[1]), "definition_version": "1.0.1"}
    bad_pointer = node_payload(definitions[2])
    cast(list[dict[str, object]], bad_pointer["parameters"])[0]["parameter_pointer"] = "/missing"
    bad_port = node_payload(definitions[3])
    cast(list[dict[str, object]], bad_port["ports"])[0]["port_id"] = "missing"
    third_party = {
        "contract_version": "0.3.0",
        "locale": "zh-CN",
        "categories": [{"category_id": "plugin", "title": "第三方", "order": 100}],
        "nodes": [callback, wrong_version, bad_pointer, bad_port],
    }
    application = ProjectServiceApplication(
        work_root=tmp_path / "work",
        definition_catalog=definitions,
        third_party_presentation_catalogs=(third_party,),
    )
    before = application.inspect()

    with _serve(application) as (base_url, _):
        status, envelope, _ = _request(base_url, "/api/studio/presentations")

    codes = {item["code"] for item in envelope["diagnostics"]}
    assert status == 200
    assert envelope["catalog"]["nodes"] == []
    assert {
        "W_PRESENTATION_NODE_INVALID",
        "W_PRESENTATION_DEFINITION_MISSING",
        "W_PRESENTATION_PARAMETER_POINTER",
        "W_PRESENTATION_PORT_BINDING",
        "W_PRESENTATION_MISSING",
    } <= codes
    assert application.inspect() == before
    assert before.snapshot is None
    assert before.run_summaries == ()


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


def test_av27_template_preview_http_route_is_read_only_and_strict(tmp_path: Path) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"synthetic source")
    target = tmp_path / "preview-only.zniku"
    request = {
        "action": "prepare",
        "request": {
            "profile_version": "2.7.0",
            "project_path": str(target.resolve()),
            "project_id": "project.http.av27",
            "project_name": "HTTP AV27 preview",
            "source_mode": "program",
            "sources": [
                {
                    "source_path": str(source.resolve()),
                    "source_ordinal": 0,
                }
            ],
            "mr": {"mode": "off"},
        },
    }
    application = ProjectServiceApplication(work_root=tmp_path / "work-preview")

    with _serve(application) as (base_url, _):
        status, preview, headers = _request(
            base_url,
            "/api/studio/templates/av-enhance-v27/preview",
            method="POST",
            payload=request,
            origin="http://127.0.0.1:4173",
        )
        assert status == 200
        assert preview["contract_version"] == "0.3.0"
        assert preview["phase"] == "preparation"
        assert preview["profile"]["status"] == "preparation-compatible"
        assert headers.get("Cache-Control") == "no-store"
        assert not target.exists()
        assert application.inspect().project_path is None

        invalid = cast(dict[str, Any], request["request"]).copy()
        invalid["graph"] = {"nodes": [], "edges": []}
        status, failure, _ = _request(
            base_url,
            "/api/studio/templates/av-enhance-v27/preview",
            method="POST",
            payload={"action": "prepare", "request": invalid},
        )
        assert status == 422
        assert failure["error"]["code"] == "E_AV27_TEMPLATE_REQUEST_INVALID"
        assert not target.exists()


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
        run_id = cast(str, started["active_run_id"])
        assert entered.wait(timeout=5)

        began = monotonic()
        status, running, _ = _request(base_url, "/api/studio/status", timeout=2)
        elapsed = monotonic() - began
        assert status == 200
        assert elapsed < 2
        assert running["active_operation"] == "run_all"
        summary = next(item for item in running["run_summaries"] if item["run_id"] == run_id)
        assert summary["state"] == "running"
        status, detail, _ = _request(base_url, f"/api/studio/runs/{run_id}")
        assert status == 200
        node_runs = detail["run"]["node_runs"]
        assert node_runs[0]["state"] == "running"

        status, failure, _ = _post_command(base_url, {"operation": "run_all"})
        assert status == 409
        assert failure["error"]["code"] == "E_PROJECT_SERVICE_BUSY"

        release.set()
        assert application.wait_until_idle(timeout=5)
        status, completed, _ = _request(base_url, "/api/studio/status")
        assert status == 200
        assert completed["active_operation"] is None
        summary = next(item for item in completed["run_summaries"] if item["run_id"] == run_id)
        assert summary["state"] == "completed"
        status, detail, _ = _request(base_url, f"/api/studio/runs/{run_id}")
        assert status == 200
        assert detail["run"]["state"] == "completed"


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
        run_id = cast(str, run_payload["active_run_id"])
        assert application.wait_until_idle(timeout=5)
        completed = application.inspect_run_detail(run_id).run
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

        status, envelope, _ = _request(
            base_url,
            f"/api/studio/runs/{run_id}/node-runs/{node_run.node_run_id}/logs",
        )
        assert status == 200
        assert envelope["contract_version"] == "0.3.0"
        assert envelope["run_id"] == run_id
        log = envelope["log"]
        assert log["node_run_id"] == node_run.node_run_id
        assert log["stdout"] == ""
        assert log["stderr"] == ""
        assert log["stdout_available"] is False
        assert log["stderr_available"] is False
