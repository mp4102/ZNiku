"""锁定 Studio authoring 所面对的内建 JSON Schema 现实边界。

语料库只是 Python ``NodeDefinition`` 的确定性投影，不获得参数语义权威。本门禁会在 definition
identity、Schema、关键字、JSON 类型或表单控制形状发生未审阅漂移时失败关闭；Phase 1 才会实现表单。
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

from zniku.avenhance_v27 import built_in_av27_definitions
from zniku.graph import NodeDefinition
from zniku.media import built_in_media_definitions

ROOT = Path(__file__).parents[1]
CORPUS_PATH = ROOT / "docs" / "architecture" / "studio-schema-corpus.json"
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

ROOT_KEYS = {
    "catalogs",
    "contract_kind",
    "contract_version",
    "description",
    "dynamic_variants",
    "feature_inventory",
    "schema_dialect",
}
SCHEMA_CHILD_KEYS = ("additionalProperties", "items", "not", "if", "then", "else")
SCHEMA_ARRAY_CHILD_KEYS = ("allOf", "oneOf", "prefixItems")
TYPE_CONTROL_SHAPES = {
    "array": "array_editor",
    "boolean": "boolean_toggle",
    "integer": "integer_input",
    "object": "object_group",
    "string": "string_input",
}
EXPECTED_SCHEMA_KEYWORDS = {
    "$schema",
    "additionalProperties",
    "allOf",
    "const",
    "default",
    "description",
    "else",
    "enum",
    "if",
    "items",
    "maxItems",
    "maxLength",
    "maximum",
    "minItems",
    "minLength",
    "minimum",
    "not",
    "oneOf",
    "pattern",
    "prefixItems",
    "properties",
    "required",
    "then",
    "type",
    "uniqueItems",
}
EXPECTED_JSON_TYPES = {"array", "boolean", "integer", "object", "string"}
EXPECTED_CONTROL_SHAPES = {
    "all_of_rules",
    "array_editor",
    "boolean_schema",
    "boolean_toggle",
    "conditional_branch",
    "constant_value",
    "declared_default",
    "enum_choice",
    "fixed_tuple_array",
    "homogeneous_array",
    "integer_input",
    "negative_rule",
    "numeric_bounds",
    "object_group",
    "one_of_choice",
    "pattern_constraint",
    "string_input",
    "text_length_bounds",
    "unique_array",
}


def _load_corpus() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(CORPUS_PATH.read_text("utf-8")))


def _iter_string_values(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for child in value.values():
            yield from _iter_string_values(child)
    elif isinstance(value, list | tuple):
        for child in value:
            yield from _iter_string_values(child)


def _schema_features(schema: object) -> dict[str, list[str]]:
    """提取当前 renderer 必须显式处理的关键字、JSON 类型和控制形状。"""

    keywords: set[str] = set()
    json_types: set[str] = set()
    control_shapes: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, bool):
            control_shapes.add("boolean_schema")
            return
        assert isinstance(value, Mapping), "Schema 节点只能是 object 或 boolean"
        node = cast(Mapping[str, Any], value)

        keywords.update(node)
        schema_type = node.get("type")
        if isinstance(schema_type, str):
            assert schema_type in TYPE_CONTROL_SHAPES, f"未登记的 JSON Schema type：{schema_type}"
            json_types.add(schema_type)
            control_shapes.add(TYPE_CONTROL_SHAPES[schema_type])
        if "enum" in node:
            control_shapes.add("enum_choice")
        if "const" in node:
            control_shapes.add("constant_value")
        if "default" in node:
            control_shapes.add("declared_default")
        if "pattern" in node:
            control_shapes.add("pattern_constraint")
        if any(key in node for key in ("minimum", "maximum")):
            control_shapes.add("numeric_bounds")
        if any(key in node for key in ("minLength", "maxLength")):
            control_shapes.add("text_length_bounds")
        if schema_type == "array" and "items" in node:
            control_shapes.add("homogeneous_array")
        if "prefixItems" in node:
            control_shapes.add("fixed_tuple_array")
        if node.get("uniqueItems") is True:
            control_shapes.add("unique_array")
        if "oneOf" in node:
            control_shapes.add("one_of_choice")
        if "allOf" in node:
            control_shapes.add("all_of_rules")
        if "if" in node:
            control_shapes.add("conditional_branch")
        if "not" in node:
            control_shapes.add("negative_rule")

        properties = node.get("properties", {})
        assert isinstance(properties, Mapping), "properties 必须是 object"
        for child in properties.values():
            visit(child)
        for key in SCHEMA_CHILD_KEYS:
            if key in node:
                visit(node[key])
        for key in SCHEMA_ARRAY_CHILD_KEYS:
            children = node.get(key, ())
            assert isinstance(children, list | tuple), f"{key} 必须是 array"
            for child in children:
                visit(child)

    visit(schema)
    return {
        "control_shapes": sorted(control_shapes),
        "json_types": sorted(json_types),
        "schema_keywords": sorted(keywords),
    }


def _definition_entry(definition: NodeDefinition) -> dict[str, Any]:
    schema = cast(dict[str, Any], definition.model_dump(mode="json")["parameter_schema"])
    return {
        "features": _schema_features(schema),
        "identity": {"type_id": definition.type_id, "version": definition.version},
        "parameter_schema": schema,
    }


def _iter_entries(document: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    for catalog in document["catalogs"]:
        for entry in catalog["definitions"]:
            yield cast(dict[str, Any], entry)
    for variant in document["dynamic_variants"]:
        yield cast(dict[str, Any], variant["definition"])


def _aggregate_features(entries: list[dict[str, Any]]) -> dict[str, list[str]]:
    return {
        feature_name: sorted(
            {
                value
                for entry in entries
                for value in cast(dict[str, list[str]], entry["features"])[feature_name]
            }
        )
        for feature_name in ("control_shapes", "json_types", "schema_keywords")
    }


def _assert_feature_inventory(document: Mapping[str, Any]) -> None:
    entries = list(_iter_entries(document))
    for entry in entries:
        assert entry["features"] == _schema_features(entry["parameter_schema"]), (
            f"{entry['identity']} 的 features 与 Schema 不一致"
        )
    assert document["feature_inventory"] == _aggregate_features(entries), (
        "聚合 feature_inventory 与 definition Schema 不一致"
    )


def test_corpus_has_strict_versioned_metadata_and_canonical_json() -> None:
    raw = CORPUS_PATH.read_bytes().decode("utf-8")
    corpus = cast(dict[str, Any], json.loads(raw))

    assert set(corpus) == ROOT_KEYS
    assert corpus["contract_kind"] == "studio_schema_authoring_corpus"
    assert corpus["contract_version"] == "0.3.0"
    assert corpus["schema_dialect"] == SCHEMA_DIALECT
    assert raw == json.dumps(corpus, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    assert not any(
        re.match(r"(?i)^(?:[a-z]:[\\/]|\\\\)", value) for value in _iter_string_values(corpus)
    ), "语料库不得携带真实本机路径"


def test_static_catalogs_are_exact_python_definition_projections() -> None:
    corpus = _load_corpus()
    expected_catalogs: list[dict[str, Any]] = [
        {
            "catalog_id": "generic_media",
            "definitions": [_definition_entry(item) for item in built_in_media_definitions()],
            "factory": "zniku.media:built_in_media_definitions",
            "factory_arguments": {},
        },
        {
            "catalog_id": "avenhance_v27",
            "definitions": [_definition_entry(item) for item in built_in_av27_definitions()],
            "factory": "zniku.avenhance_v27:built_in_av27_definitions",
            "factory_arguments": {"leaf_count": 1},
        },
    ]

    assert corpus["catalogs"] == expected_catalogs
    identities = [
        (entry["identity"]["type_id"], entry["identity"]["version"])
        for catalog in expected_catalogs
        for entry in catalog["definitions"]
    ]
    assert len(identities) == 23
    assert len(identities) == len(set(identities))


def test_leaf_count_two_is_the_minimal_dynamic_split_shape_variant() -> None:
    corpus = _load_corpus()
    default_definitions = built_in_av27_definitions()
    leaf_two_definitions = built_in_av27_definitions(2)
    split_two = leaf_two_definitions[3]
    expected_variant = {
        "catalog_id": "avenhance_v27",
        "definition": _definition_entry(split_two),
        "factory_arguments": {"leaf_count": 2},
        "replaces_identity": {
            "type_id": default_definitions[3].type_id,
            "version": default_definitions[3].version,
        },
        "variant_id": "avenhance_v27_leaf_count_2",
    }

    assert corpus["dynamic_variants"] == [expected_variant]
    assert len(default_definitions) == len(leaf_two_definitions) == 9
    for index, (base, variant) in enumerate(
        zip(default_definitions, leaf_two_definitions, strict=True)
    ):
        if index == 3:
            assert _definition_entry(variant) == expected_variant["definition"]
        else:
            assert _definition_entry(variant) == _definition_entry(base)

    split_schema = cast(dict[str, Any], split_two.model_dump(mode="json")["parameter_schema"])
    properties = cast(dict[str, Any], split_schema["properties"])
    segments_schema = cast(dict[str, Any], properties["segments"])
    prefix_items = cast(list[dict[str, Any]], segments_schema["prefixItems"])
    assert segments_schema["minItems"] == segments_schema["maxItems"] == 2
    assert [
        cast(dict[str, Any], cast(dict[str, Any], item["properties"])["port_id"])["const"]
        for item in prefix_items
    ] == ["leaf-0001", "leaf-0002"]


def test_inventory_covers_every_actual_keyword_type_and_control_shape() -> None:
    corpus = _load_corpus()
    _assert_feature_inventory(corpus)

    inventory = corpus["feature_inventory"]
    assert set(inventory["schema_keywords"]) == EXPECTED_SCHEMA_KEYWORDS
    assert set(inventory["json_types"]) == EXPECTED_JSON_TYPES
    assert set(inventory["control_shapes"]) == EXPECTED_CONTROL_SHAPES
    for entry in _iter_entries(corpus):
        schema = entry["parameter_schema"]
        assert schema["$schema"] == SCHEMA_DIALECT
        Draft202012Validator.check_schema(schema)


def test_unknown_schema_keyword_drift_fails_closed() -> None:
    corpus = copy.deepcopy(_load_corpus())
    first_schema = corpus["catalogs"][0]["definitions"][0]["parameter_schema"]
    first_schema["futureAuthoringKeyword"] = True

    with pytest.raises(AssertionError, match="features"):
        _assert_feature_inventory(corpus)
