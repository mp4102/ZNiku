"""确保0.3.2候选的默认单实例和本机状态不碰旧Studio验收线。"""

from pathlib import Path

import pytest

from zniku.desktop.__main__ import application_data_root


def test_candidate_state_is_separate_from_legacy_studio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    root = application_data_root()
    assert root == tmp_path / "ZNIKU" / "Studio-v0.3.2-candidate"
    assert root != tmp_path / "ZNIKU" / "Studio"
    assert not root.exists()


@pytest.mark.parametrize("base", ["", "relative"])
def test_candidate_rejects_unknown_local_appdata(
    base: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", base)
    with pytest.raises(RuntimeError):
        application_data_root()
