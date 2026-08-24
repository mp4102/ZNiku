"""区分 0.2.0 产品实现版本与仍待 Phase 5 清理的 0.1.0 legacy 合同版本。"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import zniku
from zniku.agent import AGENT_TOOL_CONTRACT_VERSION
from zniku.application import APPLICATION_CONTRACT_VERSION
from zniku.authoring import (
    AUTHORING_CONTRACT_VERSION,
    COMPILER_CONTRACT_VERSION,
    DIAGNOSTIC_CONTRACT_VERSION,
    PROJECTION_CONTRACT_VERSION,
    WORKFLOW_CONTRACT_VERSION,
)
from zniku.engines import ENGINE_SDK_CONTRACT_VERSION
from zniku.pipelines import DEFAULT_PIPELINE_CONTRACT_VERSION
from zniku.project_service import PROJECT_SERVICE_CONTRACT_VERSION
from zniku.studio import STUDIO_PROJECTION_CONTRACT_VERSION
from zniku.workflow import CORE_OPERATOR_CONTRACT_VERSION, EXECUTABLE_WORKFLOW_CONTRACT_VERSION
from zniku.workflow.execution import (
    EXECUTION_PLAN_CONTRACT_VERSION,
    PREFLIGHT_CONTRACT_VERSION,
    RUNTIME_CONTRACT_VERSION,
)

ROOT = Path(__file__).parents[1]


def test_phase_1_release_identity_is_0_2_0() -> None:
    pyproject: dict[str, Any] = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))

    assert (ROOT / "VERSION").read_text("utf-8").strip() == "0.2.0"
    assert pyproject["project"]["version"] == "0.2.0"
    assert zniku.__version__ == "0.2.0"
    assert PROJECT_SERVICE_CONTRACT_VERSION == "0.2.0"
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


def test_legacy_contract_versions_remain_explicitly_0_1_0() -> None:

    assert WORKFLOW_CONTRACT_VERSION == "0.1.0"
    assert COMPILER_CONTRACT_VERSION == "0.1.0"
    assert DIAGNOSTIC_CONTRACT_VERSION == "0.1.0"
    assert AUTHORING_CONTRACT_VERSION == "0.1.0"
    assert PROJECTION_CONTRACT_VERSION == "0.1.0"
    assert ENGINE_SDK_CONTRACT_VERSION == "0.1.0"
    assert CORE_OPERATOR_CONTRACT_VERSION == "0.1.0"
    assert EXECUTABLE_WORKFLOW_CONTRACT_VERSION == "0.2.0"
    assert PREFLIGHT_CONTRACT_VERSION == "0.1.0"
    assert EXECUTION_PLAN_CONTRACT_VERSION == "0.1.0"
    assert RUNTIME_CONTRACT_VERSION == "0.1.0"
    assert DEFAULT_PIPELINE_CONTRACT_VERSION == "0.1.0"
    assert APPLICATION_CONTRACT_VERSION == "0.1.0"
    assert AGENT_TOOL_CONTRACT_VERSION == "0.1.0"
    assert STUDIO_PROJECTION_CONTRACT_VERSION == "0.1.0"

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
