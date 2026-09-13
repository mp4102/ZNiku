"""锁定新候选 Schema 的展示盘点，保持旧 23 条 corpus 不变并覆盖八类真实节点。"""

from __future__ import annotations

import json
import runpy
from collections.abc import Mapping
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from zniku.chapter_overlap.definitions import atomic_split_definition, built_in_overlap_definitions
from zniku.graph import PythonExecutorSpec
from zniku.presentation import PresentationCatalogError, build_builtin_presentation_catalog
from zniku.project_service.chapter_overlap import ChapterOverlapFullEnvelope

ROOT = Path(__file__).parents[1]


def test_generated_overlap_assets_match_python() -> None:
    generator = runpy.run_path(str(ROOT / "tools/generate_overlap_studio_corpus.py"))
    for path_name, function in (("CORPUS", "corpus_document"), ("PREVIEW", "preview_document")):
        raw = generator[path_name].read_text("utf-8")
        assert (
            raw
            == json.dumps(generator[function](), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        )
    corpus = generator["corpus_document"]()
    assert len(corpus["definitions"]) == 9
    for item in corpus["definitions"]:
        Draft202012Validator.check_schema(item["parameter_schema"])
        assert item["identity"]["version"] == "0.3.2"
        assert all(ref.startswith("#/$defs/") for ref in item["features"]["local_refs"])
    ChapterOverlapFullEnvelope.model_validate_json(generator["PREVIEW"].read_text("utf-8"))


def test_overlap_presentation_exact_coverage_and_media_roles() -> None:
    definitions = (*built_in_overlap_definitions(), atomic_split_definition(2))
    catalog = build_builtin_presentation_catalog(definitions)
    assert len(catalog.nodes) == 9
    by_type = {node.type_id: node for node in catalog.nodes}
    fi = by_type["zniku.overlap.frame_interpolation.external"]
    crop = by_type["zniku.overlap.fi_crop"]
    context = by_type["zniku.overlap.fi_context"]
    assert "待真实验收" in fi.description
    assert "等待" in context.description
    assert "外部原始" in next(port.label for port in fi.ports if port.direction == "output")
    assert "精确裁边" in next(port.label for port in crop.ports if port.direction == "output")
    for node, definition in zip(catalog.nodes, definitions, strict=True):
        assert node.category_id == "overlap"
        properties = definition.parameter_schema["properties"]
        assert isinstance(properties, Mapping)
        assert {item.parameter_pointer for item in node.parameters} == {
            f"/{key}" for key in properties
        }


def test_overlap_builtin_executor_drift_cannot_use_trusted_presentation() -> None:
    definition = built_in_overlap_definitions()[0].model_copy(
        update={
            "executor": PythonExecutorSpec(adapter="synthetic.changed:run"),
        }
    )
    with pytest.raises(PresentationCatalogError, match="DRIFT"):
        build_builtin_presentation_catalog((definition,))
