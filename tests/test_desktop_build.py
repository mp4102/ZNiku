"""验证桌面构建只读取显式资源、不覆盖旧包且完整保留媒体来源和许可。"""

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import build_desktop
else:
    _SPEC = importlib.util.spec_from_file_location(
        "zniku_desktop_build", Path(__file__).parents[1] / "tools" / "build_desktop.py"
    )
    if _SPEC is None or _SPEC.loader is None:
        raise RuntimeError("无法加载桌面构建工具")
    build_desktop = importlib.util.module_from_spec(_SPEC)
    _SPEC.loader.exec_module(build_desktop)


@pytest.fixture
def build_resources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    assets = root / "apps" / "studio" / "dist"
    assets.mkdir(parents=True)
    (assets / "index.html").write_text("synthetic production", encoding="utf-8")
    media = tmp_path / "media-distribution"
    (media / "bin").mkdir(parents=True)
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        (media / "bin" / name).write_bytes(b"synthetic non-executable fixture")
    for name in ("LICENSE", "README.txt"):
        (media / name).write_text("synthetic source metadata", encoding="utf-8")
    monkeypatch.setattr(build_desktop, "ROOT", root)
    return media, root


def test_builder_uses_fixed_argv_and_preserves_provenance(
    build_resources: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    media, root = build_resources
    output = tmp_path / "candidate"
    arguments = build_desktop.build_arguments(media, output)
    assert "--onedir" in arguments and "--windowed" in arguments
    assert f"{media / 'LICENSE'};licenses/ffmpeg" in arguments
    assert f"{media / 'README.txt'};licenses/ffmpeg" in arguments
    assert str(root / "tools" / "desktop_entry.py") == arguments[-1]
    assert not output.exists()


def test_builder_does_not_overwrite_existing_directory(
    build_resources: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    media, _ = build_resources
    output = tmp_path / "existing"
    output.mkdir()
    (output / "preserve.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="已存在"):
        build_desktop.build_arguments(media, output)
    assert (output / "preserve.txt").read_text(encoding="utf-8") == "keep"


def test_builder_rejects_missing_license(
    build_resources: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    media, _ = build_resources
    (media / "LICENSE").rename(media / "original-license-preserved")
    with pytest.raises(ValueError, match="LICENSE"):
        build_desktop.build_arguments(media, tmp_path / "candidate")
