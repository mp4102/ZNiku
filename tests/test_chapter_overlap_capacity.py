"""验证千章已实现上游子图的有界容量，不执行媒体或伪造未来 FI 节点。

使用正式新 planner 和现有通用媒体 executor；SQLite、待执行 Run 及请求体都完整验证。
HTTP 超限通过内存传输调用真实 handler，不启动服务、不访问真实路径、不自动放宽 4 MiB。
"""

from __future__ import annotations

import importlib
import io
import json
import sqlite3
from http.server import HTTPServer
from pathlib import Path
from socket import socket
from types import SimpleNamespace
from typing import Any, cast

import pytest

from zniku.graph import Graph, GraphValidator
from zniku.media import media_python_adapters, media_validators
from zniku.project import ProjectStore, ProjectValidationError
from zniku.project_service import ProjectServiceApplication, make_project_service_handler
from zniku.project_service.host import _MAX_BODY_BYTES

# tools 是可执行脚本目录而非 Python package；动态加载避免建立第二个 mypy 模块身份。
_capacity = importlib.import_module("tools.check_chapter_overlap_capacity")


@pytest.mark.parametrize("leaves_per_chapter", (1, 4))
def test_thousand_chapter_upstream_graph_round_trip_without_media_execution(
    tmp_path: Path, leaves_per_chapter: int
) -> None:
    """单叶/多叶完整建图并保留历史；不是完整重叠 FI 的规模验收。"""

    directory = tmp_path / "capacity"
    result = _capacity.measure_capacity(directory, leaves_per_chapter=leaves_per_chapter)

    assert result.chapter_count == 1000
    assert result.leaf_count == 1000 * leaves_per_chapter
    assert result.split_port_count == result.leaf_count
    assert result.node_count == 1002 + result.leaf_count
    assert result.edge_count == 1 + 2 * result.leaf_count
    assert result.http_body_limit_bytes == 4 * 1024 * 1024
    assert result.save_request_fits_http
    assert 0 < result.save_request_bytes < result.http_body_limit_bytes
    assert result.snapshot_json_bytes > result.save_request_bytes
    assert result.sqlite_bytes > result.snapshot_json_bytes
    assert result.unchanged_run_history
    assert result.pending_run_node_count == 1
    assert result.executed_media_nodes == 0
    assert result.complete_overlap_chain_verified is False
    path = directory / "capacity.zniku"
    store = ProjectStore.open(path)
    original = store.load()
    GraphValidator(original.definitions).validate(original.project.graph)
    assert {definition.version for definition in original.definitions} == {"0.2.0"}
    assert all("overlap" not in definition.type_id for definition in original.definitions)
    assert len(original.definitions) == 4
    split_node = original.project.graph.nodes[1]
    segments = cast(list[dict[str, Any]], split_node.parameters["segments"])
    assert len(segments) == result.leaf_count
    assert segments[0]["start_frame"] == 0
    assert segments[-1]["end_frame"] == 1000 * leaves_per_chapter * 30 * 60 * 5
    assert len({segment["port_id"] for segment in segments}) == result.leaf_count
    for chapter_index in (0, 999):
        merge_id = f"merge.chapter-{chapter_index + 1:04d}"
        incoming = [
            edge for edge in original.project.graph.edges if edge.target_node_id == merge_id
        ]
        assert tuple(edge.ordinal for edge in incoming) == tuple(range(leaves_per_chapter))

    # 畸形 ordinal 不能截断/修复大型图，也不能在保存失败时改变已有 Run 历史。
    before_bytes = path.read_bytes()
    edges = list(original.project.graph.edges)
    merge_edge_index = next(index for index, edge in enumerate(edges) if edge.ordinal == 0)
    edges[merge_edge_index] = edges[merge_edge_index].model_copy(update={"ordinal": 99})
    malformed = original.project.model_copy(
        update={"graph": Graph(nodes=original.project.graph.nodes, edges=tuple(edges))}
    )
    with pytest.raises(ProjectValidationError):
        store.save(malformed, original.definitions)
    assert path.read_bytes() == before_bytes
    assert store.load() == original
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM node_runs").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 0


class _BodyForbiddenReader(io.BytesIO):
    """HTTP 解析头可 readline，但超限请求若继续读取 body 就立即使测试失败。"""

    def read(self, size: int | None = -1) -> bytes:
        raise AssertionError(f"超限请求不得分配或读取 body: {size}")


class _MemoryTransport:
    """满足标准 handler 的最小内存传输；不创建 socket、端口或外部服务。"""

    def __init__(self, content_length: int) -> None:
        self.reader = _BodyForbiddenReader(
            b"POST /api/studio/command HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\n"
            b"Connection: close\r\n" + f"Content-Length: {content_length}\r\n\r\n".encode("ascii")
        )
        self.response = bytearray()

    def makefile(self, mode: str, buffering: int = -1) -> io.BytesIO:
        assert mode == "rb"
        assert buffering == -1
        return self.reader

    def sendall(self, data: bytes) -> None:
        self.response.extend(data)


def test_leaf_budget_can_exceed_http_budget_and_fails_before_body_read(tmp_path: Path) -> None:
    """一万叶合法 planner 不等于可传输；显式报告 4 MiB 限制，不静默删节点。"""

    fixture = _capacity.build_capacity_fixture(leaves_per_chapter=10)
    body = _capacity.save_request_bytes(fixture.project)
    assert fixture.plan.chapter_count == 1000
    assert fixture.plan.leaf_count == 10000
    assert len(body) > _MAX_BODY_BYTES
    original = _capacity.build_capacity_fixture(chapter_count=1)
    path = tmp_path / "unchanged.zniku"
    store = ProjectStore.create(path, original.project, original.definitions)
    application = ProjectServiceApplication(
        work_root=tmp_path / "work",
        python_adapters=media_python_adapters(),
        validators=media_validators(),
    )
    application.command({"operation": "open_project", "path": str(path)})
    before = path.read_bytes()
    transport = _MemoryTransport(len(body))
    handler = make_project_service_handler(application)
    handler(
        cast(socket, transport),
        ("127.0.0.1", 0),
        cast(HTTPServer, SimpleNamespace(server_name="memory-only", server_port=0)),
    )
    header, raw = bytes(transport.response).split(b"\r\n\r\n", 1)
    assert header.startswith(b"HTTP/1.0 413 ")
    assert json.loads(raw)["error"]["code"] == "E_PROJECT_SERVICE_BODY_SIZE"
    assert path.read_bytes() == before
    assert store.load().project == original.project
    assert not any((tmp_path / "work").iterdir())
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM node_runs").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("chapter_count", "leaves_per_chapter", "code"),
    (
        (1001, 1, "E_CAPACITY_CHAPTER_BUDGET"),
        (True, 1, "E_CAPACITY_CHAPTER_BUDGET"),
        (1000, 11, "E_CAPACITY_LEAF_BUDGET"),
        (1000, True, "E_CAPACITY_LEAF_BUDGET"),
    ),
)
def test_capacity_tool_refuses_unbounded_experiment(
    chapter_count: int, leaves_per_chapter: int, code: str
) -> None:
    with pytest.raises(ValueError, match=code):
        _capacity.build_capacity_fixture(
            chapter_count=chapter_count, leaves_per_chapter=leaves_per_chapter
        )


def test_capacity_tool_never_overwrites_existing_directory(tmp_path: Path) -> None:
    with pytest.raises(FileExistsError):
        _capacity.measure_capacity(tmp_path)
    assert not any(tmp_path.iterdir())
