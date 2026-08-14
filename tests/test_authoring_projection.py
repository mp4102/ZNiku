"""验证 Python→Studio Schema、DTO、core node projection 与 drift gate。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from zniku.authoring import CoreNodeContractSet
from zniku.authoring.projection import (
    assert_projection_current,
    build_projection_files,
    build_projection_manifest,
    build_source_schemas,
    build_synthetic_studio_fixture,
    generate_projection,
)
from zniku.contracts import EngineManifest


def test_wire_schema_requires_versions_and_discriminators() -> None:
    schema = build_source_schemas()["authoring-wire.schema.json"]
    definitions = cast(dict[str, dict[str, Any]], schema["$defs"])

    assert "workflow_contract_version" in definitions["WorkflowSpec"]["required"]
    assert "kind" in definitions["SourceNodeSpec"]["required"]
    assert "intent_kind" in definitions["ConnectPortsIntent"]["required"]
    assert "result_kind" in definitions["WorkflowDraftSnapshot"]["required"]
    assert "authoring_contract_version" in definitions["AuthoringCommand"]["required"]


def test_projection_generation_is_deterministic_and_current(tmp_path: Path) -> None:
    first = generate_projection(tmp_path)
    first_payloads = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    second = generate_projection(tmp_path)
    second_payloads = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    assert first == second
    assert first_payloads == second_payloads
    assert_projection_current(tmp_path)


def test_projection_manifest_binds_all_source_and_file_digests() -> None:
    core = CoreNodeContractSet.phase_2a()
    files = build_projection_files(core)
    manifest = build_projection_manifest(core, files)

    assert manifest.core_node_contract_digest == core.sha256_digest()
    assert {item.name for item in manifest.source_schemas} == {
        "authoring-wire.schema.json",
        "core-node-contracts.schema.json",
        "engine-manifest.schema.json",
        "projection-manifest.schema.json",
    }
    assert {item.path for item in manifest.files} == {f"generated/{name}" for name in files}


def test_generated_types_are_schema_derived_and_marked_read_only() -> None:
    files = build_projection_files(CoreNodeContractSet.phase_2a())
    generated = files["authoring-wire.generated.ts"].decode("utf-8")

    assert "AUTO-GENERATED" in generated
    assert "export interface WorkflowDraftSnapshot" in generated
    assert 'readonly "result_kind": "draft_snapshot"' in generated
    assert "export type AuthoringWireDocument" in generated


def test_checked_in_projection_has_no_drift() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    output = repository_root / "apps" / "studio" / "src" / "generated"

    assert_projection_current(output)


def test_core_projection_is_exact_python_data(tmp_path: Path) -> None:
    manifest = generate_projection(tmp_path)
    core_payload = cast(
        dict[str, Any],
        json.loads((tmp_path / "core-node-contracts.json").read_text(encoding="utf-8")),
    )

    assert core_payload == CoreNodeContractSet.phase_2a().to_data()
    assert manifest.core_node_contract_digest == CoreNodeContractSet.phase_2a().sha256_digest()


def test_checked_in_studio_fixture_is_a_python_authority_transcript(
    program_manifest: EngineManifest,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    fixture_path = (
        repository_root / "apps" / "studio" / "src" / "test" / "fixtures" / "python-authority.json"
    )
    checked_in = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert checked_in == build_synthetic_studio_fixture(program_manifest)
