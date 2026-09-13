"""完整千章新Graph容量与有界HTTP保存；仅合成Repository事实，不执行媒体。"""

from __future__ import annotations

import http.client
import json
from pathlib import Path
from typing import Any

import pytest

import test_av27_template_service as fixtures
from test_overlap_template_service import _request_data
from test_project_service_host import _request, _serve
from zniku.project import ProjectStore
from zniku.project_service.host import _MAX_BODY_BYTES, _MAX_GRAPH_SAVE_BYTES
from zniku.project_service.overlap_application import full_overlap
from zniku.runtime import RuntimeRepository


@pytest.mark.parametrize("leaves_per_chapter", [1, 4])
def test_full_thousand_chapter_graph_save_reopen_http_and_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    leaves_per_chapter: int,
) -> None:
    """全部增强/context/FI/crop/Program/Final均在普通Graph内，无截断或隐藏副本。"""

    original_media = fixtures._media_info
    frames = 1000 * leaves_per_chapter * 1800
    monkeypatch.setattr(fixtures, "_media_info", lambda: original_media(frames=frames))
    app, request, path = _request_data(tmp_path, count=1000)
    request["processing"]["fi_profile"] = {"left_context_frames": 1, "right_context_frames": 1}
    history = RuntimeRepository.open(path).list_runs()
    result = full_overlap(app, request, expand=True)
    snapshot = ProjectStore.open(path).load()
    assert len(snapshot.project.graph.nodes) == 4006 + 1000 * leaves_per_chapter
    nodes = list(snapshot.project.graph.nodes)
    nodes[-1] = nodes[-1].model_copy(update={"ui_position": {"x": 3210.0, "y": 120.0}})
    changed = snapshot.project.model_copy(
        update={"graph": snapshot.project.graph.model_copy(update={"nodes": tuple(nodes)})}
    )
    payload: dict[str, Any] = {
        "operation": "save_project",
        "project_session_id": result.project_session_id,
        "expected_storage_revision": result.storage_revision,
        "project": changed.model_dump(mode="json"),
        "studio_state": {
            "contract_version": "0.3.0",
            "viewport": None,
            "groups": [],
            "node_views": [],
        },
    }
    body = json.dumps(payload).encode("utf-8")
    assert len(body) < _MAX_GRAPH_SAVE_BYTES
    with _serve(app) as (url, _server):
        status, response, _ = _request(
            url, "/api/studio/graph-save", method="POST", payload=payload, timeout=180
        )
        assert status == 200, response
    assert ProjectStore.open(path).load().project == changed
    assert RuntimeRepository.open(path).list_runs() == history
    print(
        f"complete-overlap chapters=1000 leaves={1000 * leaves_per_chapter} nodes={len(nodes)} "
        f"edges={len(changed.graph.edges)} body={len(body)} sqlite={path.stat().st_size} "
        f"legacy_limit={_MAX_BODY_BYTES}"
    )


def test_large_save_route_rejects_other_operations_and_oversize_before_read(tmp_path: Path) -> None:
    app, _request_body, path = _request_data(tmp_path)
    before = path.read_bytes()
    with _serve(app) as (url, server):
        status, response, _ = _request(
            url, "/api/studio/graph-save", method="POST", payload={"operation": "run_all"}
        )
        assert status == 422
        assert response["error"]["code"] == "E_PROJECT_SERVICE_OPERATION"
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.putrequest("POST", "/api/studio/graph-save")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(_MAX_GRAPH_SAVE_BYTES + 1))
            connection.putheader("Connection", "close")
            connection.endheaders()
            assert connection.getresponse().status == 413
        finally:
            connection.close()
    assert path.read_bytes() == before
