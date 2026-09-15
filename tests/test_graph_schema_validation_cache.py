"""验证 Schema 成功检查缓存只减少 CPU，不改变 Graph Core 任一失败关闭边界。"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

import zniku.graph.models as models
from zniku.graph import (
    ExecutionMode,
    Graph,
    GraphValidationError,
    GraphValidator,
    NodeDefinition,
    NodeInstance,
    PortSpec,
    PythonExecutorSpec,
)


@pytest.fixture(autouse=True)
def isolated_cache() -> Iterator[None]:
    models._check_parameter_schema_cached.cache_clear()
    yield
    models._check_parameter_schema_cached.cache_clear()


def _schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"count": {"type": "integer", "minimum": 1}},
        "required": ["count"],
        "additionalProperties": False,
    }


def _definition(schema: dict[str, Any]) -> NodeDefinition:
    return NodeDefinition(
        type_id="test.schema_cache",
        version="1.0.0",
        parameter_schema=schema,
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="synthetic:adapter"),
    )


def test_repeated_success_reuses_syntax_but_validates_every_parameter_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = Draft202012Validator.check_schema
    calls: list[object] = []

    def count(schema: bool | Mapping[str, Any]) -> None:
        calls.append(schema)
        original(schema)

    monkeypatch.setattr(Draft202012Validator, "check_schema", staticmethod(count))
    first, second = _definition(_schema()), _definition(_schema())
    assert len(calls) == 1
    assert first == second and first is not second
    validator = GraphValidator((second,))
    cases: tuple[dict[str, Any], ...] = (
        {"count": True},
        {"count": 0},
        {},
        {"count": 1, "unknown": True},
    )
    for params in cases:
        graph = Graph(
            nodes=(
                NodeInstance(
                    node_id="one",
                    type_id=second.type_id,
                    definition_version=second.version,
                    parameters=params,
                ),
            )
        )
        with pytest.raises(GraphValidationError, match="E_PARAMETERS_INVALID"):
            validator.validate(graph)
    assert len(calls) == 1


def test_mutating_schema_after_hit_never_changes_existing_deep_frozen_definition() -> None:
    schema = _schema()
    first = _definition(schema)
    _definition(schema)
    schema["properties"]["count"]["minimum"] = 9
    second = _definition(schema)
    first_properties = first.parameter_schema["properties"]
    second_properties = second.parameter_schema["properties"]
    assert isinstance(first_properties, dict) and isinstance(second_properties, dict)
    first_count, second_count = first_properties["count"], second_properties["count"]
    assert isinstance(first_count, dict) and isinstance(second_count, dict)
    assert first_count["minimum"] == 1
    assert second_count["minimum"] == 9
    with pytest.raises(TypeError):
        second_count["minimum"] = 0
    schema["properties"]["count"]["type"] = "not-a-json-type"
    with pytest.raises(ValidationError, match="E_PARAMETER_SCHEMA_INVALID"):
        _definition(schema)


def test_invalid_schema_is_never_cached() -> None:
    invalid = _schema()
    invalid["required"] = True
    for _ in range(3):
        with pytest.raises(ValidationError, match="E_PARAMETER_SCHEMA_INVALID"):
            _definition(invalid)
    info = models._check_parameter_schema_cached.cache_info()
    assert info.hits == 0 and info.currsize == 0 and info.misses == 3


def test_root_dialect_definition_relationships_and_unknowns_still_checked_after_warm_hit() -> None:
    definition = _definition(_schema())
    _definition(_schema())
    for schema, code in (
        (dict(_schema(), type="array"), "E_PARAMETER_SCHEMA_ROOT"),
        (
            dict(_schema(), **{"$schema": "https://other.example/schema"}),
            "E_PARAMETER_SCHEMA_DIALECT",
        ),
    ):
        with pytest.raises(ValidationError, match=code):
            _definition(schema)
    with pytest.raises(ValidationError, match="E_INPUT_PORT_DUPLICATE"):
        definition.model_copy(
            update={"input_ports": (PortSpec(port_id="same", data_type="VideoFile"),) * 2}
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        definition.model_copy(update={"unknown": True})


def test_exact_json_distinguishes_boolean_integer_and_float_keys() -> None:
    for value in (True, 1, 1.0):
        _definition(
            {
                "type": "object",
                "properties": {"value": {"const": value}},
                "additionalProperties": False,
            }
        )
    info = models._check_parameter_schema_cached.cache_info()
    assert info.currsize == 3 and info.misses == 3
    # Schema 中 boolean minimum 与 integer minimum 不因 Python True == 1 而复用成功结果。
    _definition(_schema())
    invalid = _schema()
    invalid["properties"]["count"]["minimum"] = True
    with pytest.raises(ValidationError, match="E_PARAMETER_SCHEMA_INVALID"):
        _definition(invalid)


def test_utf8_byte_budget_not_character_count_and_large_schema_keeps_original_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = Draft202012Validator.check_schema
    calls: list[object] = []

    def count(schema: bool | Mapping[str, Any]) -> None:
        calls.append(schema)
        original(schema)

    monkeypatch.setattr(Draft202012Validator, "check_schema", staticmethod(count))
    schema = dict(_schema(), description="章" * 90000)
    serialized = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert len(serialized) < 256 * 1024 < len(serialized.encode("utf-8"))
    _definition(schema)
    _definition(schema)
    assert len(calls) == 2
    assert models._check_parameter_schema_cached.cache_info().currsize == 0


def test_non_utf8_annotation_preserves_old_validation_path() -> None:
    schema = dict(_schema(), description="\ud800")
    definition = _definition(schema)
    assert definition.parameter_schema["description"] == "\ud800"
    assert models._check_parameter_schema_cached.cache_info().currsize == 0


def test_cache_is_bounded_and_concurrent_results_remain_independent() -> None:
    barrier = Barrier(8)

    def worker(index: int) -> NodeDefinition:
        barrier.wait(timeout=10)
        return _definition(dict(_schema(), description=f"concurrent-{index % 2}"))

    with ThreadPoolExecutor(max_workers=8) as executor:
        values = tuple(executor.map(worker, range(8)))
    assert len({id(value) for value in values}) == 8
    assert models._check_parameter_schema_cached.cache_info().currsize == 2
    for ordinal in range(72):
        _definition(dict(_schema(), description=f"bounded-{ordinal}"))
    assert models._check_parameter_schema_cached.cache_info().currsize == 64
    assert models._check_parameter_schema_cached.cache_info().maxsize == 64


def test_complete_desktop_catalog_rebuild_has_no_warm_schema_misses(tmp_path: Path) -> None:
    """真实 catalog 轮转不能因容量不足重复检查；不以易受机器负载影响的毫秒数作门槛。"""
    from zniku.desktop.server import build_desktop_application

    application = build_desktop_application(tmp_path)
    definitions = application._definition_catalog
    payloads = tuple(definition.model_dump_json() for definition in definitions)
    models._check_parameter_schema_cached.cache_clear()
    reconstructed = tuple(NodeDefinition.model_validate_json(payload) for payload in payloads)
    assert reconstructed == definitions
    warm = models._check_parameter_schema_cached.cache_info()
    # v0.3.5 扩容后的正式 catalog 超过旧 32 项容量，回归必须覆盖这个真实触发条件。
    assert 32 < warm.currsize <= 64
    for _ in range(3):
        prior = models._check_parameter_schema_cached.cache_info()
        assert (
            tuple(NodeDefinition.model_validate_json(payload) for payload in payloads)
            == definitions
        )
        current = models._check_parameter_schema_cached.cache_info()
        assert current.misses == prior.misses
        assert current.hits - prior.hits == len(definitions)
