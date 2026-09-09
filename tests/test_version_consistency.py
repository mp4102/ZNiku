"""锁定 0.3.1 产品身份，并拒绝把产品版本扩散到 wire、节点定义或工程格式。"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import zniku
from zniku.avenhance_v27 import AV27_NODE_VERSION
from zniku.desktop.contracts import DesktopSessionEnvelope
from zniku.media import MEDIA_NODE_VERSION
from zniku.presentation import PRESENTATION_CONTRACT_VERSION
from zniku.project import PROJECT_SCHEMA_VERSION, StudioState
from zniku.project_service import PROJECT_SERVICE_CONTRACT_VERSION

ROOT = Path(__file__).parents[1]


def test_candidate_product_identity_is_0_3_1() -> None:
    pyproject: dict[str, Any] = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))

    assert (ROOT / "VERSION").read_text("utf-8").strip() == "0.3.1"
    assert pyproject["project"]["version"] == "0.3.1"
    assert zniku.__version__ == "0.3.1"
    # C 候选只收敛 package/product，已有 Graph/Run 的 exact binding 不跟随升级。
    assert PROJECT_SERVICE_CONTRACT_VERSION == "0.3.0"
    assert MEDIA_NODE_VERSION == "0.2.0"
    assert AV27_NODE_VERSION == "0.2.1"
    assert PROJECT_SCHEMA_VERSION == 4
    assert PRESENTATION_CONTRACT_VERSION == "0.3.0"
    assert StudioState().contract_version == "0.3.0"
    assert DesktopSessionEnvelope.model_fields["contract_version"].default == "0.3.0"

    studio_package: dict[str, Any] = json.loads(
        (ROOT / "apps" / "studio" / "package.json").read_text("utf-8")
    )
    studio_lock: dict[str, Any] = json.loads(
        (ROOT / "apps" / "studio" / "package-lock.json").read_text("utf-8")
    )
    uv_lock: dict[str, Any] = tomllib.loads((ROOT / "uv.lock").read_text("utf-8"))
    zniku_packages = [package for package in uv_lock["package"] if package["name"] == "zniku"]

    assert studio_package["version"] == "0.3.1"
    assert studio_lock["version"] == "0.3.1"
    assert studio_lock["packages"][""]["version"] == "0.3.1"
    assert [package["version"] for package in zniku_packages] == ["0.3.1"]
