"""定义 ZNIKU 0.2.0 自由媒体图的最小领域模型。

本模块只描述节点定义、节点实例、端口与边，不承担 Project 持久化、Runtime 状态、媒体 I/O 或
执行计划职责。所有模型拒绝未知字段；参数值保持为普通 JSON，由绑定的 ``NodeDefinition`` Schema
在图验证阶段校验。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum
from pathlib import PureWindowsPath
from typing import Annotated, Any, Literal, Never, Self, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

Identifier = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    ),
]
TypeId = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z][A-Za-z0-9]*(?:[._:/-][A-Za-z0-9]+)*$",
    ),
]
PortType = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z][A-Za-z0-9]*(?:[._:/-][A-Za-z0-9]+)*$",
    ),
]
ExactVersion = Annotated[
    str,
    StringConstraints(
        min_length=5,
        max_length=96,
        pattern=(
            r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
            r"(?:-(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
            r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?"
            r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
        ),
    ),
]
NonBlankText = Annotated[str, StringConstraints(min_length=1, max_length=4096)]
type JsonObject = dict[str, JsonValue]

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


class FrozenJsonDict(dict[str, Any]):
    """保留 JSON object 类型语义但拒绝构造后的原地修改。"""

    @staticmethod
    def _reject_mutation() -> Never:
        raise TypeError("Graph Core JSON 值不可原地修改")

    def __setitem__(self, key: str, value: Any) -> None:
        del key, value
        self._reject_mutation()

    def __delitem__(self, key: str) -> None:
        del key
        self._reject_mutation()

    def clear(self) -> None:
        self._reject_mutation()

    def pop(self, key: str, default: Any = None) -> Any:
        del key, default
        self._reject_mutation()

    def popitem(self) -> tuple[str, Any]:
        self._reject_mutation()

    def setdefault(self, key: str, default: Any = None) -> Any:
        del key, default
        self._reject_mutation()

    def update(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self._reject_mutation()

    def __ior__(self, other: Any) -> Self:  # type: ignore[override,misc]
        del other
        self._reject_mutation()


class FrozenJsonList(list[Any]):
    """保留 JSON array 类型语义但拒绝构造后的原地修改。"""

    @staticmethod
    def _reject_mutation() -> Never:
        raise TypeError("Graph Core JSON 值不可原地修改")

    def __setitem__(self, key: Any, value: Any) -> None:
        del key, value
        self._reject_mutation()

    def __delitem__(self, key: Any) -> None:
        del key
        self._reject_mutation()

    def append(self, value: Any) -> None:
        del value
        self._reject_mutation()

    def clear(self) -> None:
        self._reject_mutation()

    def extend(self, values: Any) -> None:
        del values
        self._reject_mutation()

    def insert(self, index: Any, value: Any) -> None:
        del index, value
        self._reject_mutation()

    def pop(self, index: Any = -1) -> Any:
        del index
        self._reject_mutation()

    def remove(self, value: Any) -> None:
        del value
        self._reject_mutation()

    def reverse(self) -> None:
        self._reject_mutation()

    def sort(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self._reject_mutation()

    def __iadd__(self, values: Any) -> Self:  # type: ignore[misc]
        del values
        self._reject_mutation()

    def __imul__(self, count: Any) -> Self:  # type: ignore[misc]
        del count
        self._reject_mutation()


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return FrozenJsonDict({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return FrozenJsonList(_freeze_json(item) for item in value)
    return value


def _empty_parameter_schema() -> JsonObject:
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }


def _ensure_non_blank(value: str, *, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"E_{field_name.upper()}_BLANK: {field_name} 不得只包含空白")
    return value


def _validate_parameter_schema(schema: JsonObject) -> JsonObject:
    if schema.get("$schema") not in (None, JSON_SCHEMA_DIALECT):
        raise ValueError(
            "E_PARAMETER_SCHEMA_DIALECT: parameter_schema 只支持 JSON Schema Draft 2020-12"
        )
    if schema.get("type") != "object":
        raise ValueError("E_PARAMETER_SCHEMA_ROOT: parameter_schema 根 type 必须为 object")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise ValueError(f"E_PARAMETER_SCHEMA_INVALID: {error.message}") from error
    return schema


class GraphModel(BaseModel):
    """Graph Core 的严格值对象基类。"""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
        revalidate_instances="always",
    )

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        """通过完整重验复制，避免 Pydantic 的未校验 update 引入可变 JSON。"""

        del deep
        data = self.model_dump(mode="python", round_trip=True)
        if update:
            data.update(update)
        return type(self).model_validate(data)


class Cardinality(StrEnum):
    """端口只支持单值或带显式顺序的多值输入。"""

    ONE = "one"
    ORDERED_MANY = "ordered_many"


class CorePortType(StrEnum):
    """列出 Core 首批内建类型；插件仍可声明其他开放字符串类型。"""

    MEDIA_FILE = "MediaFile"
    VIDEO_FILE = "VideoFile"
    AUDIO_FILE = "AudioFile"
    DATA_FILE = "DataFile"


class PortSpec(GraphModel):
    """声明节点端口的本地身份、精确数据类型、基数与输入必需性。"""

    port_id: Identifier
    data_type: PortType
    cardinality: Cardinality = Cardinality.ONE
    required: bool = False


class ExecutionMode(StrEnum):
    """区分 Runtime 自动启动与操作者提交外部结果。"""

    AUTOMATIC = "automatic"
    MANUAL_EXTERNAL = "manual_external"


class ExecutorOutputPathSpec(GraphModel):
    """声明一个 output port 在 attempt ``outputs`` 下的受控相对路径。

    该路径属于受信任的 executor definition，不是 Node 参数或客户端运行时输入。这里先拒绝
    Windows/UNC/绝对路径、NUL 与显式 ``.``/``..`` 段；Runner 仍须在实际 attempt 目录建立后
    再执行 resolve/containment 检查，以关闭 symlink/reparse-point 逃逸。
    """

    port_id: Identifier
    relative_path: NonBlankText

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        normalized = _ensure_non_blank(value, field_name="output_relative_path")
        if "\x00" in normalized:
            raise ValueError("E_EXECUTOR_OUTPUT_PATH_NUL: output relative_path 不得包含 NUL")
        windows_path = PureWindowsPath(normalized)
        raw_parts = re.split(r"[\\/]", normalized)
        if (
            windows_path.is_absolute()
            or bool(windows_path.drive)
            or bool(windows_path.root)
            or any(part in {".", ".."} for part in raw_parts)
        ):
            raise ValueError(
                "E_EXECUTOR_OUTPUT_PATH_INVALID: output relative_path 必须是无 . 或 .. 的相对路径"
            )
        return normalized


class _OutputPathExecutorSpec(GraphModel):
    """让三种 executor 使用同一 output path declaration 形状。"""

    output_paths: tuple[ExecutorOutputPathSpec, ...] = ()

    @field_validator("output_paths", mode="before")
    @classmethod
    def normalize_output_paths(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_unique_output_paths(self) -> Self:
        port_ids = tuple(item.port_id for item in self.output_paths)
        if len(port_ids) != len(set(port_ids)):
            raise ValueError(
                "E_EXECUTOR_OUTPUT_PATH_DUPLICATE: executor output_paths 的 port_id 不得重复"
            )
        return self


class PythonExecutorSpec(_OutputPathExecutorSpec):
    """引用由本地可信插件提供的 Python adapter。"""

    kind: Literal["python"] = "python"
    adapter: NonBlankText

    @field_validator("adapter")
    @classmethod
    def validate_adapter(cls, value: str) -> str:
        return _ensure_non_blank(value, field_name="adapter")


class CommandExecutorSpec(_OutputPathExecutorSpec):
    """声明必须以 ``shell=False`` 直接启动的 executable 与 argv。"""

    kind: Literal["command"] = "command"
    executable: NonBlankText
    argv: tuple[Annotated[str, StringConstraints(max_length=4096)], ...] = ()

    @field_validator("argv", mode="before")
    @classmethod
    def normalize_argv(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("argv")
    @classmethod
    def validate_argv(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any("\x00" in token for token in value):
            raise ValueError("E_COMMAND_ARGV_NUL: command argv 不得包含 NUL")
        return value

    @field_validator("executable")
    @classmethod
    def validate_executable(cls, value: str) -> str:
        normalized = _ensure_non_blank(value, field_name="executable")
        if "\x00" in normalized:
            raise ValueError("E_COMMAND_EXECUTABLE_NUL: command executable 不得包含 NUL")
        return normalized


class ManualExternalExecutorSpec(_OutputPathExecutorSpec):
    """声明由操作者在外部完成处理并提交输出的 handoff。"""

    kind: Literal["manual_external"] = "manual_external"
    instructions: Annotated[str, StringConstraints(max_length=4096)] | None = None


type ExecutorSpec = Annotated[
    PythonExecutorSpec | CommandExecutorSpec | ManualExternalExecutorSpec,
    Field(discriminator="kind"),
]


class ValidatorSpec(GraphModel):
    """引用本地可信插件提供的节点级轻量 validator。"""

    adapter: NonBlankText

    @field_validator("adapter")
    @classmethod
    def validate_adapter(cls, value: str) -> str:
        return _ensure_non_blank(value, field_name="adapter")


class NodeDefinition(GraphModel):
    """声明一种可精确版本绑定、可执行且可校验参数的节点类型。"""

    type_id: TypeId
    version: ExactVersion
    input_ports: tuple[PortSpec, ...] = ()
    output_ports: tuple[PortSpec, ...] = ()
    parameter_schema: JsonObject = Field(default_factory=_empty_parameter_schema)
    execution_mode: ExecutionMode
    executor: ExecutorSpec
    validator: ValidatorSpec | None = None

    @field_validator("input_ports", "output_ports", mode="before")
    @classmethod
    def normalize_ports(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("parameter_schema")
    @classmethod
    def validate_parameter_schema(cls, value: JsonObject) -> JsonObject:
        return cast(JsonObject, _freeze_json(_validate_parameter_schema(value)))

    @model_validator(mode="after")
    def validate_definition_relations(self) -> NodeDefinition:
        input_ids = tuple(port.port_id for port in self.input_ports)
        output_ids = tuple(port.port_id for port in self.output_ports)
        if len(input_ids) != len(set(input_ids)):
            raise ValueError("E_INPUT_PORT_DUPLICATE: input port_id 不得重复")
        if len(output_ids) != len(set(output_ids)):
            raise ValueError("E_OUTPUT_PORT_DUPLICATE: output port_id 不得重复")
        if any(port.cardinality is Cardinality.ORDERED_MANY for port in self.output_ports):
            raise ValueError("E_OUTPUT_ORDERED_MANY: ordered_many 只适用于输入端口")
        if any(port.required for port in self.output_ports):
            raise ValueError("E_OUTPUT_REQUIRED: required 只适用于输入端口")

        declared_outputs = set(output_ids)
        unknown_output_paths = tuple(
            item.port_id
            for item in self.executor.output_paths
            if item.port_id not in declared_outputs
        )
        if unknown_output_paths:
            raise ValueError(
                "E_EXECUTOR_OUTPUT_PORT_UNKNOWN: executor output_paths 只能引用已声明 output port："
                + ", ".join(repr(item) for item in unknown_output_paths)
            )

        is_manual = isinstance(self.executor, ManualExternalExecutorSpec)
        if self.execution_mode is ExecutionMode.MANUAL_EXTERNAL and not is_manual:
            raise ValueError(
                "E_EXECUTOR_MODE_MISMATCH: manual_external 必须使用 manual_external executor"
            )
        if self.execution_mode is ExecutionMode.AUTOMATIC and is_manual:
            raise ValueError(
                "E_EXECUTOR_MODE_MISMATCH: automatic 必须使用 python 或 command executor"
            )
        return self


class UiPosition(GraphModel):
    """保存 Studio 画布位置；该值不参与 Runtime 语义。"""

    x: float
    y: float


class NodeInstance(GraphModel):
    """将一个图节点绑定到精确 NodeDefinition 版本与参数。"""

    node_id: Identifier
    type_id: TypeId
    definition_version: ExactVersion
    parameters: JsonObject = Field(default_factory=dict)
    ui_position: UiPosition | None = None

    @field_validator("parameters")
    @classmethod
    def freeze_parameters(cls, value: JsonObject) -> JsonObject:
        return cast(JsonObject, _freeze_json(value))


class Edge(GraphModel):
    """连接一个已声明 output 与一个已声明 input。"""

    source_node_id: Identifier
    source_port_id: Identifier
    target_node_id: Identifier
    target_port_id: Identifier
    ordinal: Annotated[int, Field(ge=0)] | None = None


class Graph(GraphModel):
    """表示用户当前编辑的普通节点与有向边集合。"""

    nodes: tuple[NodeInstance, ...] = ()
    edges: tuple[Edge, ...] = ()

    @field_validator("nodes", "edges", mode="before")
    @classmethod
    def normalize_sequences(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value
