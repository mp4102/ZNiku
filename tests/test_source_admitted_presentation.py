"""校验新节点展示精确身份、Schema 盘点及合成预览；不执行媒体或改写旧资产。"""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from jsonschema import Draft202012Validator

from zniku.graph import NodeDefinition
from zniku.presentation import PresentationCatalogError, build_builtin_presentation_catalog
from zniku.source_admission.definitions import built_in_definitions

ROOT = Path(__file__).parents[1]


def test_generated_assets_match_exact_python_contracts() -> None:
    with patch.object(sys, "path", [str(ROOT / "tools"), *sys.path]):
        module = runpy.run_path(str(ROOT / "tools/generate_source_admitted_studio_corpus.py"))
        for path_key, fn in (("CORPUS", "corpus_document"), ("PREVIEW", "preview_document")):
            assert module[path_key].read_text("utf-8") == (
                json.dumps(module[fn](), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            )
        corpus = module["corpus_document"]()
    assert len(corpus["definitions"]) == 14
    for item in corpus["definitions"]:
        assert item["identity"]["version"] == "0.3.5"
        Draft202012Validator.check_schema(item["parameter_schema"])


@pytest.mark.parametrize("definition", built_in_definitions())
def test_presentation_accepts_only_frozen_new_definition(definition: NodeDefinition) -> None:
    catalog = build_builtin_presentation_catalog((definition,))
    assert catalog.nodes[0].definition_version == "0.3.5"
    changed = definition.model_dump(mode="json")
    changed["parameter_schema"]["title"] = "drift"
    with pytest.raises(PresentationCatalogError, match="DRIFT"):
        build_builtin_presentation_catalog(
            (NodeDefinition.model_validate_json(json.dumps(changed)),)
        )
