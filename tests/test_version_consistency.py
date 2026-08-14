"""确保产品、Python package 与 Engine Contract Kernel 保持 0.1.0。"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import zniku
from zniku.authoring import (
    AUTHORING_CONTRACT_VERSION,
    COMPILER_CONTRACT_VERSION,
    DIAGNOSTIC_CONTRACT_VERSION,
    PROJECTION_CONTRACT_VERSION,
    WORKFLOW_CONTRACT_VERSION,
)

ROOT = Path(__file__).parents[1]


def test_all_local_version_authorities_are_0_1_0() -> None:
    pyproject: dict[str, Any] = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))

    assert (ROOT / "VERSION").read_text("utf-8").strip() == "0.1.0"
    assert pyproject["project"]["version"] == "0.1.0"
    assert zniku.__version__ == "0.1.0"
    studio_package: dict[str, Any] = json.loads(
        (ROOT / "apps" / "studio" / "package.json").read_text("utf-8")
    )
    assert studio_package["version"] == "0.1.0"

    assert WORKFLOW_CONTRACT_VERSION == "0.1.0"
    assert COMPILER_CONTRACT_VERSION == "0.1.0"
    assert DIAGNOSTIC_CONTRACT_VERSION == "0.1.0"
    assert AUTHORING_CONTRACT_VERSION == "0.1.0"
    assert PROJECTION_CONTRACT_VERSION == "0.1.0"

    projection_manifest: dict[str, Any] = json.loads(
        (ROOT / "apps" / "studio" / "src" / "generated" / "projection-manifest.json").read_text(
            "utf-8"
        )
    )
    assert projection_manifest["workflow_contract_version"] == "0.1.0"
    assert projection_manifest["compiler_contract_version"] == "0.1.0"
    assert projection_manifest["diagnostic_contract_version"] == "0.1.0"
    assert projection_manifest["authoring_contract_version"] == "0.1.0"
    assert projection_manifest["projection_contract_version"] == "0.1.0"
