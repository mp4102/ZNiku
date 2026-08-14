"""校验 Engine 使用的受限 JSON Schema 与声明式参数。

首版固定 Draft 2020-12，并采用有意收窄的关键字子集。参数对象及其嵌套对象必须显式关闭未知字段；
``default`` 只作为声明，不会在验证时静默写入实例。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal, TypeGuard

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .base import JsonObject, ensure_no_executable_keys, thaw_json
from .errors import ContractViolation

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

_ALLOWED_KEYWORDS = frozenset(
    {
        "$comment",
        "$schema",
        "additionalProperties",
        "const",
        "default",
        "description",
        "enum",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "items",
        "maxItems",
        "maxLength",
        "maxProperties",
        "maximum",
        "minItems",
        "minLength",
        "minProperties",
        "minimum",
        "multipleOf",
        "properties",
        "required",
        "title",
        "type",
        "uniqueItems",
    }
)
_JSON_TYPES = frozenset({"array", "boolean", "integer", "null", "number", "object", "string"})
_NUMERIC_KEYWORDS = frozenset(
    {"exclusiveMaximum", "exclusiveMinimum", "maximum", "minimum", "multipleOf"}
)
_STRING_KEYWORDS = frozenset({"maxLength", "minLength"})
_ARRAY_KEYWORDS = frozenset({"items", "maxItems", "minItems", "uniqueItems"})
_OBJECT_KEYWORDS = frozenset(
    {"additionalProperties", "maxProperties", "minProperties", "properties", "required"}
)
_MAX_SCHEMA_BYTES = 65_536
_MAX_SCHEMA_DEPTH = 16
_MAX_INSTANCE_BYTES = 1_048_576
_MAX_ARRAY_ITEMS = 256


def _is_json_number(value: Any) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _declared_types(schema: Mapping[str, Any], *, path: str) -> frozenset[str]:
    value = schema.get("type")
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, list):
        values = tuple(value)
    else:
        raise ContractViolation("E_SCHEMA_TYPE_REQUIRED", f"{path}.type 必须是字符串或字符串数组")

    if not values or any(not isinstance(item, str) or item not in _JSON_TYPES for item in values):
        raise ContractViolation("E_SCHEMA_TYPE_INVALID", f"{path}.type 含未知 JSON 类型")
    if len(values) != len(set(values)):
        raise ContractViolation("E_SCHEMA_TYPE_INVALID", f"{path}.type 不得重复")
    return frozenset(values)


def _walk_schema(
    schema: Mapping[str, Any],
    *,
    path: str,
    require_closed_objects: bool,
    root: bool,
    depth: int,
) -> None:
    if depth > _MAX_SCHEMA_DEPTH:
        raise ContractViolation("E_SCHEMA_TOO_DEEP", f"{path} 超过最大 Schema 深度")
    unknown = sorted(set(schema) - _ALLOWED_KEYWORDS)
    if unknown:
        raise ContractViolation(
            "E_SCHEMA_KEYWORD_UNKNOWN",
            f"{path} 含首版不支持的 Schema 关键字：{', '.join(unknown)}",
        )

    if not root and "$schema" in schema:
        raise ContractViolation("E_SCHEMA_DIALECT_NESTED", f"{path} 不得重新声明 $schema")

    types = _declared_types(schema, path=path)
    applicability = (
        (_NUMERIC_KEYWORDS, bool(types & {"integer", "number"}), "number"),
        (_STRING_KEYWORDS, "string" in types, "string"),
        (_ARRAY_KEYWORDS, "array" in types, "array"),
        (_OBJECT_KEYWORDS, "object" in types, "object"),
    )
    for keywords, applicable, expected_type in applicability:
        misplaced = sorted(set(schema) & keywords) if not applicable else []
        if misplaced:
            raise ContractViolation(
                "E_SCHEMA_KEYWORD_TYPE_MISMATCH",
                f"{path} 的 {', '.join(misplaced)} 只适用于 {expected_type} Schema",
            )

    _validate_local_constraints(schema, path=path, types=types)
    if "object" in types:
        additional = schema.get("additionalProperties")
        if not isinstance(additional, bool):
            raise ContractViolation(
                "E_SCHEMA_ADDITIONAL_PROPERTIES_REQUIRED",
                f"{path} 的 object Schema 必须显式声明布尔 additionalProperties",
            )
        if require_closed_objects and additional:
            raise ContractViolation(
                "E_SCHEMA_OBJECT_MUST_BE_CLOSED",
                f"{path} 的参数对象必须使用 additionalProperties: false",
            )

        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            raise ContractViolation("E_SCHEMA_PROPERTIES_INVALID", f"{path}.properties 必须是对象")
        for name, child in properties.items():
            if not isinstance(name, str) or not isinstance(child, Mapping):
                raise ContractViolation(
                    "E_SCHEMA_PROPERTIES_INVALID", f"{path}.properties 必须映射到对象 Schema"
                )
            _walk_schema(
                child,
                path=f"{path}.properties.{name}",
                require_closed_objects=require_closed_objects,
                root=False,
                depth=depth + 1,
            )

        required = schema.get("required", [])
        if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
            raise ContractViolation(
                "E_SCHEMA_REQUIRED_INVALID", f"{path}.required 必须是字符串数组"
            )
        unknown_required = sorted(set(required) - set(properties))
        if unknown_required:
            raise ContractViolation(
                "E_SCHEMA_REQUIRED_UNKNOWN",
                f"{path}.required 引用了未声明属性：{', '.join(unknown_required)}",
            )
        max_properties = schema.get("maxProperties")
        if isinstance(max_properties, int) and len(set(required)) > max_properties:
            raise ContractViolation(
                "E_SCHEMA_BOUNDS_INVALID",
                f"{path}.maxProperties 小于 required 属性数量",
            )
        min_properties = schema.get("minProperties")
        if (
            additional is False
            and isinstance(min_properties, int)
            and min_properties > len(properties)
        ):
            raise ContractViolation(
                "E_SCHEMA_BOUNDS_INVALID",
                f"{path}.minProperties 超过闭合对象可提供的属性数量",
            )

    if "array" in types:
        items = schema.get("items")
        if not isinstance(items, Mapping):
            raise ContractViolation("E_SCHEMA_ITEMS_REQUIRED", f"{path}.items 必须是对象 Schema")
        _walk_schema(
            items,
            path=f"{path}.items",
            require_closed_objects=require_closed_objects,
            root=False,
            depth=depth + 1,
        )


def _validate_local_constraints(
    schema: Mapping[str, Any], *, path: str, types: frozenset[str]
) -> None:
    lower_bounds: list[tuple[int | float, bool]] = []
    upper_bounds: list[tuple[int | float, bool]] = []
    for key, exclusive, destination in (
        ("minimum", False, lower_bounds),
        ("exclusiveMinimum", True, lower_bounds),
        ("maximum", False, upper_bounds),
        ("exclusiveMaximum", True, upper_bounds),
    ):
        value = schema.get(key)
        if _is_json_number(value):
            destination.append((value, exclusive))

    if lower_bounds and upper_bounds:
        lower_value = max(value for value, _exclusive in lower_bounds)
        upper_value = min(value for value, _exclusive in upper_bounds)
        lower_exclusive = any(
            exclusive for value, exclusive in lower_bounds if value == lower_value
        )
        upper_exclusive = any(
            exclusive for value, exclusive in upper_bounds if value == upper_value
        )
        if lower_value > upper_value or (
            lower_value == upper_value and (lower_exclusive or upper_exclusive)
        ):
            raise ContractViolation(
                "E_SCHEMA_BOUNDS_INVALID",
                f"{path} 的数值上下界不存在可满足的取值",
            )

    for minimum_key, maximum_key in (
        ("minLength", "maxLength"),
        ("minItems", "maxItems"),
        ("minProperties", "maxProperties"),
    ):
        lower = schema.get(minimum_key)
        upper = schema.get(maximum_key)
        if isinstance(lower, int) and isinstance(upper, int) and lower > upper:
            raise ContractViolation(
                "E_SCHEMA_BOUNDS_INVALID", f"{path}.{minimum_key} 不得大于 {maximum_key}"
            )

    if (
        "string" in types
        and "enum" not in schema
        and "const" not in schema
        and "maxLength" not in schema
    ):
        raise ContractViolation(
            "E_SCHEMA_STRING_UNBOUNDED", f"{path} 的开放 string 必须声明 maxLength"
        )
    if "array" in types and "maxItems" not in schema:
        raise ContractViolation("E_SCHEMA_ARRAY_UNBOUNDED", f"{path} 的 array 必须声明 maxItems")
    max_items = schema.get("maxItems")
    if (
        isinstance(max_items, int)
        and not isinstance(max_items, bool)
        and max_items > _MAX_ARRAY_ITEMS
    ):
        raise ContractViolation(
            "E_SCHEMA_ARRAY_TOO_LARGE",
            f"{path}.maxItems 不得超过 {_MAX_ARRAY_ITEMS}",
        )

    enum = schema.get("enum")
    if enum is not None and (not isinstance(enum, list) or not enum):
        raise ContractViolation("E_SCHEMA_ENUM_EMPTY", f"{path}.enum 必须是非空数组")


def validate_schema_document(
    schema: JsonObject, *, purpose: Literal["parameters", "media"]
) -> None:
    """检查方言、受支持关键字、闭合边界和 Schema 自身合法性。"""

    raw = thaw_json(schema)
    if not isinstance(raw, dict):
        raise ContractViolation("E_SCHEMA_ROOT_INVALID", "Schema 根必须是 JSON 对象")
    if raw.get("$schema") != JSON_SCHEMA_DIALECT:
        raise ContractViolation(
            "E_SCHEMA_DIALECT_UNSUPPORTED",
            f"$schema 必须精确为 {JSON_SCHEMA_DIALECT}",
        )
    if raw.get("type") != "object":
        raise ContractViolation("E_SCHEMA_ROOT_TYPE", "Schema 根 type 必须精确为 object")
    try:
        encoded = json.dumps(
            raw, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError) as error:
        raise ContractViolation("E_SCHEMA_ENCODING", f"Schema 无法确定编码：{error}") from error
    if len(encoded) > _MAX_SCHEMA_BYTES:
        raise ContractViolation("E_SCHEMA_TOO_LARGE", "Schema 超过 64 KiB 上限")

    ensure_no_executable_keys(raw)
    _walk_schema(
        raw,
        path="$",
        require_closed_objects=purpose == "parameters",
        root=True,
        depth=0,
    )
    try:
        Draft202012Validator.check_schema(raw)
    except SchemaError as error:
        raise ContractViolation("E_SCHEMA_INVALID", error.message) from error

    _validate_declared_defaults(raw, path="$")
    _validate_declared_values(raw, path="$")


def _validate_declared_defaults(schema: Mapping[str, Any], *, path: str) -> None:
    if "default" in schema:
        errors = tuple(Draft202012Validator(dict(schema)).iter_errors(schema["default"]))
        if errors:
            raise ContractViolation(
                "E_SCHEMA_DEFAULT_INVALID",
                f"{path}.default 不满足其所在 Schema：{errors[0].message}",
            )

    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        for name, child in properties.items():
            if isinstance(child, Mapping):
                _validate_declared_defaults(child, path=f"{path}.properties.{name}")
    items = schema.get("items")
    if isinstance(items, Mapping):
        _validate_declared_defaults(items, path=f"{path}.items")


def _validate_declared_values(schema: Mapping[str, Any], *, path: str) -> None:
    if "enum" in schema:
        base = dict(schema)
        candidates = base.pop("enum")
        if isinstance(candidates, list):
            for candidate in candidates:
                errors = tuple(Draft202012Validator(base).iter_errors(candidate))
                if errors:
                    raise ContractViolation(
                        "E_SCHEMA_ENUM_VALUE_INVALID",
                        f"{path}.enum 含不满足其余约束的值：{errors[0].message}",
                    )
    if "const" in schema:
        base = dict(schema)
        candidate = base.pop("const")
        errors = tuple(Draft202012Validator(base).iter_errors(candidate))
        if errors:
            raise ContractViolation(
                "E_SCHEMA_CONST_VALUE_INVALID",
                f"{path}.const 不满足其余约束：{errors[0].message}",
            )

    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        for name, child in properties.items():
            if isinstance(child, Mapping):
                _validate_declared_values(child, path=f"{path}.properties.{name}")
    items = schema.get("items")
    if isinstance(items, Mapping):
        _validate_declared_values(items, path=f"{path}.items")


def validate_json_instance(instance: JsonObject, schema: JsonObject, *, code: str) -> None:
    """按确定的 Schema 校验一个 JSON 对象，不注入默认值或进行类型强制转换。"""

    raw_instance = thaw_json(instance)
    raw_schema = thaw_json(schema)
    if not isinstance(raw_instance, dict) or not isinstance(raw_schema, dict):
        raise ContractViolation(code, "JSON instance 与 Schema 根必须是对象")
    ensure_no_executable_keys(raw_instance)
    try:
        encoded = json.dumps(
            raw_instance,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError) as error:
        raise ContractViolation(code, f"JSON 实例无法确定编码：{error}") from error
    if len(encoded) > _MAX_INSTANCE_BYTES:
        raise ContractViolation(code, "JSON 实例超过 1 MiB 上限")
    errors = sorted(
        Draft202012Validator(raw_schema).iter_errors(raw_instance),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        violation = errors[0]
        location = ".".join(str(part) for part in violation.absolute_path) or "$"
        raise ContractViolation(code, f"{location}: {violation.message}")
