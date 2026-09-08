"""验证预览只读身份、有限缓存与 HTTP 本机授权，全部使用临时合成媒体。

注入 renderer 验证错误和并发；一个有界真实 FFmpeg 用例证明静帧生成不更改 Source 或
工程。测试不打开 GUI、播放器或真实文件，不把预览当作 Artifact/QC。
"""

from __future__ import annotations

import base64
import binascii
import shutil
import struct
import subprocess
import threading
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from authoring_helpers import authoring_command
from test_project_service_host_bridge import RecordingPlatform, RecordingResolver, _request
from zniku.graph import Edge, Graph, NodeInstance
from zniku.media import (
    built_in_media_definitions,
    media_artifact_quick_probe,
    media_python_adapters,
    media_validators,
    runner_media_probe,
)
from zniku.project import Project, ProjectStore
from zniku.project_service import (
    HostBridgeFailure,
    HostBridgeSession,
    MediaPreviewEnvelope,
    MediaPreviewRequest,
    PreviewCache,
    ProjectServiceApplication,
    ProjectServiceError,
    create_project_service_host_bridge_session,
    make_project_service_handler,
)
from zniku.project_service import preview as preview_module

_ORIGIN = "http://127.0.0.1:4173"


def _png() -> bytes:
    """一个真实可解码的 RGB 单像素 PNG，避免以假 image bytes 验证成功。"""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", binascii.crc32(kind + data))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + chunk(b"IEND", b"")
    )


def _picker(session: HostBridgeSession) -> dict[str, object]:
    action = session.issue_user_action({"capability": "open_file"})
    selected = session.invoke(
        {
            "user_action_id": action.user_action_id,
            "capability": "open_file",
            "arguments": {},
        }
    )
    return {
        "contract_version": "0.3.0",
        "project_session_id": None,
        "reference": {
            "kind": "picker_selection",
            "selection_handle": selected.selections[0].selection_handle,
        },
    }


def _setup(
    tmp_path: Path,
) -> tuple[ProjectServiceApplication, HostBridgeSession, Path, dict[str, object]]:
    source = tmp_path / "synthetic.mkv"
    source.write_bytes(b"synthetic source")
    application = ProjectServiceApplication(work_root=tmp_path / "attempts")
    session = create_project_service_host_bridge_session(
        application,
        studio_origin=_ORIGIN,
        platform=RecordingPlatform({"open_file": (str(source),)}),
    )
    return application, session, source, _picker(session)


@contextmanager
def _serve(
    application: ProjectServiceApplication,
    session: HostBridgeSession | None,
    cache: PreviewCache | None,
) -> Iterator[str]:
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_project_service_handler(application, host_bridge=session, preview_cache=cache),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    "change",
    [
        {"contract_version": "0.3"},
        {"contract_version": "0.4.0"},
        {"time": 3},
        {"path": "arbitrary.mkv"},
        {"argv": ["ffmpeg"]},
        {"shell": "cmd"},
        {
            "reference": {
                "kind": "picker_selection",
                "selection_handle": "a" * 24,
                "path": "video.mkv",
            }
        },
        {"project_session_id": True},
    ],
)
def test_preview_request_fails_closed(tmp_path: Path, change: dict[str, object]) -> None:
    application, session, _, request = _setup(tmp_path)
    cache = PreviewCache(renderer=lambda _: pytest.fail("不得生成"))
    with pytest.raises(HostBridgeFailure, match="预览请求"):
        cache.preview({**request, **change}, session=session, application=application)
    with pytest.raises(ValidationError):
        MediaPreviewRequest.model_validate({**request, **change})


def test_response_round_trip_rejects_active_content_and_dimension_mismatch(tmp_path: Path) -> None:
    application, session, _, request = _setup(tmp_path)
    result = PreviewCache(renderer=lambda _: _png()).preview(
        request, session=session, application=application
    )
    assert MediaPreviewEnvelope.model_validate_json(result.model_dump_json()) == result
    assert result.width == result.height == 1
    for changes in (
        {"image_data_url": "data:image/svg+xml,<svg/>"},
        {"image_data_url": "https://example.com/"},
        {"width": 640},
        {"image_data_url": "data:image/png;base64," + base64.b64encode(b"not png").decode()},
        {"reference": {"kind": "artifact", "run_id": str(uuid4()), "artifact_id": str(uuid4())}},
    ):
        with pytest.raises(ValidationError):
            MediaPreviewEnvelope.model_validate({**result.model_dump(), **changes})


def test_cache_is_memory_only_bounded_and_invalidates_file_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application, session, source, request = _setup(tmp_path)
    calls: list[Path] = []

    def render(path: Path) -> bytes:
        calls.append(path)
        return _png()

    cache = PreviewCache(renderer=render)
    paths_before = set(tmp_path.rglob("*"))
    stat_before = source.stat()
    first = cache.preview(request, session=session, application=application)
    second = cache.preview(request, session=session, application=application)
    assert not first.cache_hit and second.cache_hit
    assert (source.stat().st_size, source.stat().st_mtime_ns) == (
        stat_before.st_size,
        stat_before.st_mtime_ns,
    )
    assert set(tmp_path.rglob("*")) == paths_before
    source.write_bytes(b"changed synthetic source")
    assert not cache.preview(request, session=session, application=application).cache_hit
    assert len(calls) == 2
    monkeypatch.setattr(preview_module, "PREVIEW_MAX_CACHE_ENTRIES", 2)
    monkeypatch.setattr(preview_module, "PREVIEW_MAX_CACHE_BYTES", len(_png()) * 2)
    for index in range(3):
        other = tmp_path / f"other-{index}.mkv"
        other.write_bytes(b"other synthetic source")
        other_session = HostBridgeSession(
            RecordingPlatform({"open_file": (str(other),)}),
            studio_origin=_ORIGIN,
            path_resolver=RecordingResolver(other),
        )
        cache.preview(_picker(other_session), session=other_session, application=application)
    assert not cache.preview(request, session=session, application=application).cache_hit
    cache.close()
    assert set(tmp_path.rglob("*.png")) == set()
    assert source.read_bytes() == b"changed synthetic source"
    with pytest.raises(HostBridgeFailure) as closed:
        cache.preview(request, session=session, application=application)
    assert closed.value.code == "E_PREVIEW_UNAVAILABLE"


def test_changed_source_or_project_during_generation_is_not_cached(tmp_path: Path) -> None:
    application, session, source, request = _setup(tmp_path)

    def changing_renderer(path: Path) -> bytes:
        if path.read_bytes() != b"changed source during preview":
            path.write_bytes(b"changed source during preview")
        return _png()

    cache = PreviewCache(renderer=changing_renderer)
    with pytest.raises(HostBridgeFailure) as changed:
        cache.preview(request, session=session, application=application)
    assert changed.value.code == "E_PREVIEW_CHANGED"
    assert not cache.preview(request, session=session, application=application).cache_hit

    def changing_project(_: Path) -> bytes:
        application.command(
            {
                "operation": "create_project",
                "path": str(tmp_path / "new.zniku"),
                "name": "预览工程",
            }
        )
        return _png()

    with pytest.raises(ProjectServiceError, match="E_PROJECT_SESSION_CONFLICT"):
        PreviewCache(renderer=changing_project).preview(
            request, session=session, application=application
        )
    assert source.is_file()


def test_single_generation_is_bounded_and_concurrent_request_fails_closed(tmp_path: Path) -> None:
    application, session, _, request = _setup(tmp_path)
    entered, release = threading.Event(), threading.Event()
    results: list[MediaPreviewEnvelope] = []

    def render(_: Path) -> bytes:
        entered.set()
        assert release.wait(5)
        return _png()

    cache = PreviewCache(renderer=render)
    worker = threading.Thread(
        target=lambda: results.append(
            cache.preview(request, session=session, application=application)
        )
    )
    worker.start()
    try:
        assert entered.wait(5)
        with pytest.raises(HostBridgeFailure) as busy:
            cache.preview(request, session=session, application=application)
        assert busy.value.code == "E_PREVIEW_BUSY"
    finally:
        release.set()
        worker.join(5)
    assert len(results) == 1


@pytest.mark.parametrize(
    "data",
    [b"", b"not an image", b"x" * (preview_module.PREVIEW_MAX_IMAGE_BYTES + 1)],
    ids=["empty", "invalid", "oversized"],
)
def test_invalid_renderer_output_is_not_success(tmp_path: Path, data: bytes) -> None:
    application, session, _, request = _setup(tmp_path)
    with pytest.raises(HostBridgeFailure) as failure:
        PreviewCache(renderer=lambda _: data).preview(
            request, session=session, application=application
        )
    assert failure.value.code == "E_PREVIEW_IMAGE"


def test_media_reference_and_project_binding_fail_before_decode(tmp_path: Path) -> None:
    application, session, source, request = _setup(tmp_path)
    cache = PreviewCache(renderer=lambda _: pytest.fail("非法引用不得解码"))
    bad_handle = {
        **request,
        "reference": {
            "kind": "picker_selection",
            "selection_handle": "unknown-handle-123456789012345",
        },
    }
    with pytest.raises(HostBridgeFailure) as unknown:
        cache.preview(bad_handle, session=session, application=application)
    assert unknown.value.code == "E_HOST_BRIDGE_REFERENCE"
    with pytest.raises(ProjectServiceError, match="E_PROJECT_SESSION_CONFLICT"):
        cache.preview(
            {**request, "project_session_id": str(uuid4())},
            session=session,
            application=application,
        )
    for path in (tmp_path / "script.html", tmp_path / "playlist.m3u8"):
        path.write_text("synthetic", encoding="utf-8")
        other = HostBridgeSession(
            RecordingPlatform({"open_file": (str(path),)}),
            studio_origin=_ORIGIN,
            path_resolver=RecordingResolver(path),
        )
        with pytest.raises(HostBridgeFailure) as invalid:
            cache.preview(_picker(other), session=other, application=application)
        assert invalid.value.code == "E_PREVIEW_MEDIA"
    source.write_bytes(b"")
    with pytest.raises(HostBridgeFailure, match="无法读取"):
        cache.preview(request, session=session, application=application)


def test_http_preview_requires_exact_host_authorization_and_post(tmp_path: Path) -> None:
    application, session, _, request = _setup(tmp_path)
    cache = PreviewCache(renderer=lambda _: _png())
    with _serve(application, session, cache) as url:
        for origin, token in (
            (None, session.token),
            ("http://127.0.0.1:4174", session.token),
            (_ORIGIN, None),
            (_ORIGIN, "bad"),
        ):
            status, body, _ = _request(
                url,
                "/api/host-bridge/preview",
                method="POST",
                origin=origin,
                token=token,
                payload=request,
            )
            assert status == 403
            assert session.token not in str(body)
        status, _, _ = _request(url, "/api/host-bridge/preview", method="GET", token=session.token)
        assert status == 405
        status, _, _ = _request(
            url,
            "/api/host-bridge/preview?path=video.mkv",
            method="POST",
            token=session.token,
            payload=request,
        )
        assert status == 404
        status, body, headers = _request(
            url, "/api/host-bridge/preview", method="POST", token=session.token, payload=request
        )
        assert status == 200 and body is not None
        assert body["reference"] == request["reference"]
        assert headers["Cache-Control"] == "no-store"
        assert headers["Access-Control-Allow-Origin"] == _ORIGIN
        status, _, _ = _request(
            url,
            "/api/host-bridge/preview",
            method="POST",
            token=session.token,
            payload={**request, "project_session_id": str(uuid4())},
        )
        assert status == 409
    for host, preview in ((None, cache), (session, None)):
        with _serve(application, host, preview) as url:
            status, _, _ = _request(
                url, "/api/host-bridge/preview", method="POST", token=session.token, payload=request
            )
            assert status == 503


def test_ffmpeg_preview_uses_fixed_shell_false_argv_and_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "synthetic ; quoted.mkv"
    seen: list[tuple[list[str], dict[str, object]]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        seen.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=_png())

    monkeypatch.setattr(preview_module, "resolve_media_tool", lambda _: "ffmpeg")
    monkeypatch.setattr("zniku.project_service.preview.subprocess.run", run)
    assert preview_module.render_representative_frame(path) == _png()
    argv, kwargs = seen[0]
    assert argv[argv.index("-i") + 1] == str(path)
    assert argv[argv.index("-frames:v") + 1] == "1"
    assert kwargs["shell"] is False and kwargs["timeout"] == 15
    assert "-protocol_whitelist" in argv and "file" in argv

    def timeout(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(argv, 15)

    monkeypatch.setattr("zniku.project_service.preview.subprocess.run", timeout)
    with pytest.raises(HostBridgeFailure) as failure:
        preview_module.render_representative_frame(path)
    assert failure.value.code == "E_PREVIEW_TIMEOUT" and str(path) not in failure.value.message


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="本机缺少 FFmpeg")
def test_real_synthetic_preview_keeps_source_project_and_runtime_unchanged(tmp_path: Path) -> None:
    application, session, source, request = _setup(tmp_path)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:size=80x48:rate=1",
            "-frames:v",
            "1",
            "-c:v",
            "ffv1",
            str(source),
        ],
        check=True,
        shell=False,
        timeout=15,
    )
    store = ProjectStore.create(
        tmp_path / "synthetic.zniku",
        Project(project_id="preview.synthetic", name="预览合成工程", graph=Graph()),
        definitions=(),
    )
    envelope = application.command({"operation": "open_project", "path": str(store.path)})
    request["project_session_id"] = envelope.project_session_id
    source_before = source.read_bytes(), source.stat().st_size, source.stat().st_mtime_ns
    project_before = store.path.read_bytes()
    before = application.inspect().model_dump()
    result = PreviewCache().preview(request, session=session, application=application)
    assert result.width <= 640 and result.height <= 360 and not result.cache_hit
    assert (source.read_bytes(), source.stat().st_size, source.stat().st_mtime_ns) == source_before
    assert store.path.read_bytes() == project_before
    after = application.inspect().model_dump()
    for key in ("runs", "artifacts", "latest_results", "project", "project_session_id"):
        assert before.get(key) == after.get(key)
    assert not tuple(tmp_path.rglob("*.png"))


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="本机缺少 FFmpeg/FFprobe",
)
def test_real_artifact_and_latest_handoff_references_are_bound_without_mutation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:size=80x48:rate=1",
            "-frames:v",
            "1",
            "-c:v",
            "ffv1",
            str(source),
        ],
        check=True,
        shell=False,
        timeout=15,
    )
    definitions = built_in_media_definitions()
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="source",
                type_id="zniku.media.source.video",
                definition_version="0.2.0",
                parameters={"source_path": str(source)},
            ),
            NodeInstance(
                node_id="external",
                type_id="zniku.media.video_transform.enhancement.external",
                definition_version="0.2.0",
                parameters={"tool": "synthetic", "model": "synthetic", "tool_version": "1.0"},
            ),
        ),
        edges=(
            Edge(
                source_node_id="source",
                source_port_id="out",
                target_node_id="external",
                target_port_id="video",
            ),
        ),
    )
    store = ProjectStore.create(
        tmp_path / "bound.zniku",
        Project(project_id="preview.bound", name="绑定预览", graph=graph),
        definitions,
    )
    application = ProjectServiceApplication(
        work_root=tmp_path / "attempts",
        definition_catalog=definitions,
        python_adapters=media_python_adapters(),
        validators=media_validators(),
        media_probe=runner_media_probe,
        artifact_quick_probe=media_artifact_quick_probe,
    )
    opened = application.command({"operation": "open_project", "path": str(store.path)})
    started = authoring_command(application, {"operation": "run_all"})
    assert started.active_run_id is not None and application.wait_until_idle()
    detail = application.inspect_run_detail(started.active_run_id)
    artifact = detail.artifacts[0]
    waiting = next(item for item in detail.run.node_runs if item.node_id == "external")
    assert waiting.external_handoff is not None
    session = create_project_service_host_bridge_session(
        application, studio_origin=_ORIGIN, platform=RecordingPlatform()
    )
    cache = PreviewCache()
    references = (
        {"kind": "artifact", "run_id": detail.run.run_id, "artifact_id": artifact.artifact_id},
        {
            "kind": "handoff",
            "run_id": detail.run.run_id,
            "node_run_id": waiting.node_run_id,
            "handoff_id": waiting.external_handoff.handoff_id,
            "selector": {"role": "input_artifact", "artifact_id": artifact.artifact_id},
        },
    )
    database_before = store.path.read_bytes()
    for reference in references:
        request = {
            "contract_version": "0.3.0",
            "project_session_id": opened.project_session_id,
            "reference": reference,
        }
        response = cache.preview(request, session=session, application=application)
        assert response.reference.model_dump() == reference
        wrong = {**request, "reference": {**reference, "run_id": str(uuid4())}}
        with pytest.raises(HostBridgeFailure) as denied:
            cache.preview(wrong, session=session, application=application)
        assert denied.value.code == "E_HOST_BRIDGE_REFERENCE"
    assert store.path.read_bytes() == database_before
    assert application.inspect_run_detail(detail.run.run_id) == detail
    application.command({"operation": "create_project", "path": str(tmp_path / "different.zniku")})
    with pytest.raises(ProjectServiceError, match="E_PROJECT_SESSION_CONFLICT"):
        cache.preview(request, session=session, application=application)
