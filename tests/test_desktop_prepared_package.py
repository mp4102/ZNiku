"""独立核对新准备节点打包注册、隔离与声明；不启动真实桌面或媒体进程。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from test_desktop_build import build_resources as build_resources
from test_desktop_package_audit import _package, _SyntheticArchive
from test_desktop_package_audit import resources as resources
from zniku.desktop import __main__ as desktop_main
from zniku.desktop.server import build_desktop_application
from zniku.prepared_color import definitions as prepared_color
from zniku.prepared_source import definitions as prepared
from zniku.prepared_source import work_definitions as prepared_work
from zniku.source_color import definitions as source_color
from zniku.source_color import models as color_models
from zniku.source_preparation import source_preparation_definitions
from zniku.source_preparation import work_definitions as work_source
from zniku.source_preparation.models import T1_PROMOTED, T1_STRATEGY

if TYPE_CHECKING:
    import audit_desktop_package as audit
    import build_desktop
else:
    from test_desktop_build import build_desktop
    from test_desktop_package_audit import audit


def test_desktop_registers_all_preparation_definitions_and_callables(tmp_path: Path) -> None:
    app = build_desktop_application(tmp_path / "synthetic-attempts")
    expected = (
        *source_preparation_definitions(),
        *prepared.built_in_overlap_definitions(),
        prepared.external_definition("mp4"),
        prepared.external_definition("mov"),
        prepared.external_definition("mkv"),
        *source_color.source_preparation_definitions(),
        *prepared_color.built_in_overlap_definitions(),
        prepared_color.external_definition("mp4"),
        prepared_color.external_definition("mov"),
        prepared_color.external_definition("mkv"),
        *work_source.source_preparation_definitions(),
        *prepared_work.built_in_overlap_definitions(),
        prepared_work.external_definition("mp4"),
        prepared_work.external_definition("mov"),
        prepared_work.external_definition("mkv"),
    )
    for definition in expected:
        assert definition in app._definition_catalog
        if definition.executor.kind == "python":
            assert definition.executor.adapter in app._python_adapters
        assert definition.validator is not None
        assert definition.validator.adapter in app._validators
    assert not T1_PROMOTED
    assert not color_models.T1_PROMOTED


def test_color_strategy_promotion_is_independent_and_requires_bundled_tool(
    build_resources: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/1 的发布与 /2 的资格不能互相替代；新版工具仍须随包可核对。"""
    media, _ = build_resources
    monkeypatch.setattr(build_desktop, "T1_PROMOTED", False)
    monkeypatch.setattr(build_desktop, "COLOR_T1_PROMOTED", True)
    with pytest.raises(ValueError, match="MKVToolNix"):
        build_desktop.build_arguments(media, tmp_path / "color-candidate")
    assert not (tmp_path / "color-candidate").exists()


def test_promoted_t1_build_requires_explicit_bundled_tool(
    build_resources: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media, _ = build_resources
    monkeypatch.setattr(build_desktop, "T1_PROMOTED", True)
    with pytest.raises(ValueError, match="MKVToolNix"):
        build_desktop.build_arguments(media, tmp_path / "candidate")
    assert not (tmp_path / "candidate").exists()


def test_incomplete_frozen_media_bundle_fails_without_using_system_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setenv("PATH", "synthetic-system-path")
    with pytest.raises(RuntimeError, match="FFmpeg/FFprobe"):
        desktop_main.configure_bundled_media_tools()
    assert os.environ["PATH"] == "synthetic-system-path"


@pytest.mark.parametrize("field", ["enabled_preparation_strategies", "mkvmerge_bundled"])
def test_audit_rejects_false_preparation_capability_claim(
    resources: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    """BUILD-INFO 不能声称源码未晋级的策略可用，或声称包内存在未提供的工具。"""
    repository, media = resources
    for name in (
        "src/zniku/__init__.py",
        "VERSION",
        "pyproject.toml",
        "uv.lock",
        "apps/studio/package.json",
        "apps/studio/package-lock.json",
    ):
        path = repository / name
        path.write_text(
            path.read_text(encoding="utf-8").replace("0.3.2", "0.3.4"), encoding="utf-8"
        )
    preparation = repository / "src/zniku/source_preparation"
    preparation.mkdir()
    (preparation / "__init__.py").write_text("# synthetic\n", encoding="utf-8")
    (preparation / "models.py").write_text(
        'T1_PROMOTED = False\nT1_STRATEGY = "t1-clock-quantization/1"\n', encoding="utf-8"
    )
    package = _package(repository, media, tmp_path / "candidate")
    metadata = package / "_internal/zniku-0.3.2.dist-info"
    metadata = metadata.rename(package / "_internal/zniku-0.3.4.dist-info")
    (metadata / "METADATA").write_text("Name: zniku\nVersion: 0.3.4\n", encoding="utf-8")
    info_path = package / "BUILD-INFO.json"
    info = json.loads(info_path.read_text(encoding="utf-8"))
    info.update(product_version="0.3.4", enabled_preparation_strategies=[], mkvmerge_bundled=False)
    info[field] = [T1_STRATEGY] if field == "enabled_preparation_strategies" else True
    info_path.write_text(json.dumps(info), encoding="utf-8")
    archive = _SyntheticArchive(repository)
    monkeypatch.setattr(audit, "_archive_reader", lambda _: archive)
    with pytest.raises(audit.PackageAuditError):
        audit.audit_desktop(package, repository, media)
