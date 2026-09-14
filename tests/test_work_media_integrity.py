"""普通 Final 音轨包检查的边界；不把缺少可选首包 duration 当作媒体损坏。"""

from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from zniku.media.probe import MediaNodeError
from zniku.prepared_source import work_audio


def test_first_packet_optional_duration_does_not_block_known_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def scan(*args: Any) -> bool:
        callback = args[4]
        callback({"pts_time": "0", "duration_time": "N/A", "size": "111"})
        callback({"pts_time": "0.021", "duration_time": "0.021", "size": "222"})
        return False

    monkeypatch.setattr(work_audio, "_scan", scan)
    assert work_audio.packet_span(tmp_path / "synthetic.mkv", 1) == work_audio.AudioPacketSpan(
        count=2, size=333, start=Fraction(0), end=Fraction(42, 1000)
    )


@pytest.mark.parametrize("missing", ["pts_time", "size", "duration_time"])
def test_missing_required_final_packet_evidence_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    def scan(*args: Any) -> bool:
        row = {"pts_time": "0", "duration_time": "0.021", "size": "111"}
        row.pop(missing)
        args[4](row)
        return False

    monkeypatch.setattr(work_audio, "_scan", scan)
    with pytest.raises((ValueError, MediaNodeError)):
        work_audio.packet_span(tmp_path / "synthetic.mkv", 1)


@pytest.mark.parametrize("change", ["count", "size", "span"])
def test_packet_copy_rejects_truncation_or_changed_payload_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    source, output = tmp_path / "source.mkv", tmp_path / "result.mkv"
    before = work_audio.AudioPacketSpan(30, 10000, Fraction(0), Fraction(64, 100))
    after = work_audio.AudioPacketSpan(
        29 if change == "count" else 30,
        9999 if change == "size" else 10000,
        Fraction(0),
        Fraction(60 if change == "span" else 64, 100),
    )
    monkeypatch.setattr(
        work_audio, "probe_header", lambda _: SimpleNamespace(audios=(SimpleNamespace(index=1),))
    )
    monkeypatch.setattr(
        work_audio, "packet_span", lambda path, *args: before if path == source else after
    )
    with pytest.raises(MediaNodeError, match="WORK_AUDIO_COPY"):
        work_audio.verify_audio_copy(source, output)
