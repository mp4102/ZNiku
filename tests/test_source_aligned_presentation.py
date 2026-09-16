"""验证新版纯展示、命名与机器盘点，不读真实媒体或改写旧定义。"""

from __future__ import annotations

import json
import runpy
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest
from jsonschema import Draft202012Validator

from zniku.chapter_overlap import AdmittedTimeline, ChapterSettings, plan_chapters_and_leaves
from zniku.graph import NodeInstance, PythonExecutorSpec
from zniku.presentation import PresentationCatalogError, build_builtin_presentation_catalog
from zniku.project_service.source_aligned_presentation import source_aligned_output_paths
from zniku.source_aligned.definitions import (
    atomic_split_definition,
    built_in_overlap_definitions,
    external_definition,
)
from zniku.source_aligned.node_contracts import DeclaredContainer

ROOT = Path(__file__).parents[1]
SOURCE: dict[str, Any] = {
    "original_video_artifact_id": "00000000-0000-4000-8000-000000000001",
    "source_media_artifact_id": "00000000-0000-4000-8000-000000000002",
    "admission_artifact_id": "00000000-0000-4000-8000-000000000003",
    "frame_count": 18,
    "frame_rate": "30/1",
}


def test_new_corpus_matches_python_and_does_not_rewrite_old_assets() -> None:
    with patch.object(sys, "path", [str(ROOT / "tools"), *sys.path]):
        module = runpy.run_path(str(ROOT / "tools/generate_source_aligned_studio_corpus.py"))
        for path_key, fn in (("CORPUS", "corpus_document"), ("PREVIEW", "preview_document")):
            assert (
                module[path_key].read_text("utf-8")
                == json.dumps(module[fn](), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            )
        corpus = module["corpus_document"]()
    assert len(corpus["definitions"]) == 12
    for item in corpus["definitions"]:
        assert item["identity"]["version"] == "0.3.3"
        Draft202012Validator.check_schema(item["parameter_schema"])
    old = json.loads(
        (ROOT / "docs/architecture/studio-overlap-schema-corpus.json").read_text("utf-8")
    )
    assert old["contract_version"] == "0.3.2" and len(old["definitions"]) == 9


@pytest.mark.parametrize("container", ["mp4", "mov", "mkv"])
def test_external_format_controls_real_target_without_chapter_or_uuid(container: str) -> None:
    definition = external_definition(cast(DeclaredContainer, container))
    node = NodeInstance(
        node_id="arbitrary-node-id",
        type_id=definition.type_id,
        definition_version="0.3.3",
        parameters={
            "source": SOURCE,
            "declared_container": container,
            "model_name": "Synthetic",
            "operator_frame_order_confirmed": True,
        },
    )
    before = node.model_dump_json()
    paths = source_aligned_output_paths(node, definition, media_basename="Example (2026)")
    assert len(paths) == 1 and paths[0].relative_path == f"Example (2026).RM.{container}"
    assert node.model_dump_json() == before
    catalog = build_builtin_presentation_catalog((definition,))
    assert catalog.nodes[0].title == "外部马赛克修复"
    assert any(p.label == "外部输出封装" for p in catalog.nodes[0].parameters)


def test_split_naming_uses_original_plan_without_future_effective_identity() -> None:
    plan = plan_chapters_and_leaves(
        AdmittedTimeline(
            artifact_id=SOURCE["original_video_artifact_id"], frame_count=18, frame_rate="30/1"
        ),
        ChapterSettings(),
    )
    definition = atomic_split_definition(1)
    node = NodeInstance(
        node_id="split",
        type_id=definition.type_id,
        definition_version="0.3.3",
        parameters={"plan": plan.model_dump(mode="json"), "source": SOURCE},
    )
    paths = source_aligned_output_paths(node, definition, media_basename="Example")
    assert paths[0].relative_path == "A/Example.A.leaf-0001.mkv"
    assert "effective_video_artifact_id" not in node.model_dump_json()


def test_exact_drift_rejected_and_all_new_parameters_are_presented() -> None:
    definitions = (*built_in_overlap_definitions(), external_definition())
    catalog = build_builtin_presentation_catalog(definitions)
    for definition, presentation in zip(definitions, catalog.nodes, strict=True):
        properties = definition.parameter_schema["properties"]
        assert isinstance(properties, Mapping)
        assert {p.parameter_pointer for p in presentation.parameters} == {
            "/" + key for key in properties
        }
    altered = definitions[0].model_copy(
        update={"executor": PythonExecutorSpec(adapter="test:fake")}
    )
    with pytest.raises(PresentationCatalogError, match="DRIFT"):
        build_builtin_presentation_catalog((altered,))


@pytest.mark.parametrize("automatic", [False, True])
def test_only_new_exact_waiting_mr_enables_check_before_import(automatic: bool) -> None:
    from test_av27_handoff_projection import _fixture
    from zniku.project_service.source_aligned_presentation import project_source_aligned_handoffs
    from zniku.source_admission import definitions as admitted
    from zniku.source_admission.mosaic_restoration import definition as mosaic_definition

    run, artifact = _fixture("mr")
    definition = mosaic_definition() if automatic else admitted.external_definition("mp4")
    node = run.graph_snapshot.nodes[1].model_copy(
        update={
            "type_id": definition.type_id,
            "definition_version": definition.version,
            "parameters": {
                "source": {**SOURCE, "original_video_artifact_id": artifact.artifact_id},
                "declared_container": "mp4",
                "model_name": "Synthetic",
                "operator_frame_order_confirmed": True,
            },
        }
    )
    updated = run.model_copy(
        update={
            "graph_snapshot": run.graph_snapshot.model_copy(
                update={"nodes": (run.graph_snapshot.nodes[0], node)}
            ),
            "definitions_snapshot": (run.definitions_snapshot[0], definition),
            "node_runs": tuple(
                item.model_copy(update={"definition_version": definition.version})
                for item in run.node_runs
            ),
        }
    )
    before = updated.model_dump_json()
    projections = project_source_aligned_handoffs(
        updated, (artifact,), role_reader=admitted.definition_role, admitted_source=True
    )
    assert len(projections) == 1
    assert projections[0].intake_supported is automatic
    assert updated.model_dump_json() == before
