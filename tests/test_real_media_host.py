"""验证 loopback host 只通过 Runtime command 推进并保持 Studio Schema drift 门。"""

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from zniku.realmedia import RealHostEnvelope, RealMediaHostApplication
from zniku.realmedia.host import make_handler
from zniku.validation import generate_short_media


def test_host_application_starts_advances_and_submits_fixture(tmp_path: Path) -> None:
    reference = tmp_path / "reference.mkv"
    generate_short_media(reference)
    application = RealMediaHostApplication(
        root=tmp_path / "run",
        reference=reference,
        clip_start_seconds=0,
        clip_duration_seconds=1,
    )
    assert application.inspect().candidate_exists is False
    started = application.command({"operation": "start"})
    assert started.projection is not None
    advanced = application.command({"operation": "advance_automatic"})
    projection = advanced.projection
    assert projection is not None
    manual = next(
        item
        for item in projection.nodes
        if item.state == "ready" and item.execution_mode == "manual_external"
    )
    submitted = application.command(
        {"operation": "acceptance_fixture", "plan_node_id": manual.plan_node_id}
    )
    assert submitted.projection is not None
    completed = next(
        item for item in submitted.projection.nodes if item.plan_node_id == manual.plan_node_id
    )
    assert completed.state == "complete" and completed.evidence_id is not None


def test_loopback_http_status_returns_closed_envelope(tmp_path: Path) -> None:
    reference = tmp_path / "reference.mkv"
    generate_short_media(reference)
    application = RealMediaHostApplication(root=tmp_path / "run", reference=reference)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(application))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/api/real-media/status", timeout=5
        ) as response:
            payload = json.loads(response.read())
        envelope = RealHostEnvelope.from_data(payload)
        assert envelope.candidate_exists is False and envelope.projection is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_real_media_host_schema_has_no_drift() -> None:
    root = Path(__file__).resolve().parents[1]
    checked_in = json.loads(
        (root / "apps/studio/src/formal/real-media-host.schema.json").read_text("utf-8")
    )
    assert checked_in == RealHostEnvelope.model_json_schema()
