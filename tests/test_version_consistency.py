"""锁定 ZNIKU 0.2.0 正式 Python、Studio 与锁文件版本一致性。"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import zniku
from zniku.media import MEDIA_NODE_VERSION
from zniku.project_service import PROJECT_SERVICE_CONTRACT_VERSION

ROOT = Path(__file__).parents[1]


def test_release_identity_is_0_2_0() -> None:
    pyproject: dict[str, Any] = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))

    assert (ROOT / "VERSION").read_text("utf-8").strip() == "0.2.0"
    assert pyproject["project"]["version"] == "0.2.0"
    assert zniku.__version__ == "0.2.0"
    assert PROJECT_SERVICE_CONTRACT_VERSION == "0.2.0"
    assert MEDIA_NODE_VERSION == "0.2.0"

    studio_package: dict[str, Any] = json.loads(
        (ROOT / "apps" / "studio" / "package.json").read_text("utf-8")
    )
    studio_lock: dict[str, Any] = json.loads(
        (ROOT / "apps" / "studio" / "package-lock.json").read_text("utf-8")
    )
    uv_lock: dict[str, Any] = tomllib.loads((ROOT / "uv.lock").read_text("utf-8"))
    zniku_packages = [package for package in uv_lock["package"] if package["name"] == "zniku"]

    assert studio_package["version"] == "0.2.0"
    assert studio_lock["version"] == "0.2.0"
    assert studio_lock["packages"][""]["version"] == "0.2.0"
    assert [package["version"] for package in zniku_packages] == ["0.2.0"]
