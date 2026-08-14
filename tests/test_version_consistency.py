"""确保产品、Python package 与 Engine Contract Kernel 保持 0.1.0。"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import zniku

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
