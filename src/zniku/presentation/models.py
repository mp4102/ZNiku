"""定义 ZNIKU Studio v0.3.0 的纯展示合同。

这些模型只描述中文标题、图标 token、参数布局和端口文案，不携带参数约束、执行器或回调。
所有字段严格解析并拒绝未知输入；它们不会进入 Graph、Run snapshot、reuse 或 stale 判定。
"""

from __future__ import annotations

import json
import re
from enum import StrEnum
from typing import Annotated, Any, Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

from zniku.graph.models import ExactVersion, Identifier, TypeId, _freeze_json

PRESENTATION_CONTRACT_VERSION: Literal["0.3.0"] = "0.3.0"
PRESENTATION_LOCALE: Literal["zh-CN"] = "zh-CN"

ShortText = Annotated[str, StringConstraints(min_length=1, max_length=160)]
LongText = Annotated[str, StringConstraints(min_length=1, max_length=4096)]
JsonPointer = Annotated[str, StringConstraints(min_length=1, max_length=1024)]
ExtensionHint = Annotated[
    str,
    StringConstraints(min_length=2, max_length=32, pattern=r"^\.[A-Za-z0-9][A-Za-z0-9._+-]*$"),
]
_JSON_POINTER_ESCAPE = re.compile(r"~(?:0|1)")


def decode_json_pointer(pointer: str) -> tuple[str, ...]:
    """严格解码非根 RFC 6901 JSON Pointer；非法 ``~`` 转义失败关闭。"""

    if not pointer.startswith("/"):
        raise ValueError("E_PRESENTATION_POINTER_INVALID: parameter pointer 必须以 / 开始")
    tokens: list[str] = []
    for raw_token in pointer[1:].split("/"):
        unmatched = _JSON_POINTER_ESCAPE.sub("", raw_token)
        if "~" in unmatched:
            raise ValueError("E_PRESENTATION_POINTER_INVALID: parameter pointer 含非法 ~ 转义")
        tokens.append(raw_token.replace("~1", "/").replace("~0", "~"))
    return tuple(tokens)


class PresentationModel(BaseModel):
    """Presentation 合同的严格、不可变值对象基类。"""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        strict=True,
        validate_default=True,
    )


class PaletteLevel(StrEnum):
    """控制节点在 Palette 中的渐进披露层级。"""

    PRIMARY = "primary"
    SECONDARY = "secondary"
    ADVANCED = "advanced"


class ParameterImportance(StrEnum):
    """区分默认表单与高级参数区。"""

    PRIMARY = "primary"
    ADVANCED = "advanced"


class ControlHint(StrEnum):
    """列出 Studio 内置且无执行能力的参数控件提示。"""

    AUTO = "auto"
    TEXT = "text"
    TEXTAREA = "textarea"
    INTEGER = "integer"
    NUMBER = "number"
    SLIDER = "slider"
    SWITCH = "switch"
    SELECT = "select"
    RADIO = "radio"
    FILE_PATH = "file_path"
    FILE_PATHS = "file_paths"
    DIRECTORY_PATH = "directory_path"
    SAVE_FILE = "save_file"


class IconToken(StrEnum):
    """Studio 内置图标 token 的闭合集合；不得注入 SVG、路径或 URL。"""

    SOURCE = "source"
    MEDIA = "media"
    VIDEO = "video"
    AUDIO = "audio"
    TRANSFORM = "transform"
    SPLIT = "split"
    MERGE = "merge"
    ENCODE = "encode"
    MUX = "mux"
    OUTPUT = "output"
    CHECK = "check"


class CategoryPresentation(PresentationModel):
    """声明 Palette 中一个纯展示分类。"""

    category_id: Identifier
    title: ShortText
    description: LongText | None = None
    order: Annotated[int, Field(ge=0, le=10_000)]


class ParameterGroupPresentation(PresentationModel):
    """声明 Inspector 中一个参数分组，不改变参数对象结构。"""

    group_id: Identifier
    title: ShortText
    description: LongText | None = None
    order: Annotated[int, Field(ge=0, le=10_000)]


class EnumLabel(PresentationModel):
    """为正式 Schema 已声明的一个 JSON enum 值提供人类标签。"""

    value: JsonValue
    label: ShortText

    @field_validator("value")
    @classmethod
    def freeze_value(cls, value: JsonValue) -> JsonValue:
        """冻结复合 enum，避免构造后修改展示绑定。"""

        return cast(JsonValue, _freeze_json(value))


class PickerPresentation(PresentationModel):
    """提供原生选择器的扩展名提示；它不是媒体 validator。"""

    extensions: tuple[ExtensionHint, ...] = ()

    @field_validator("extensions", mode="before")
    @classmethod
    def normalize_extensions(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_extensions(self) -> Self:
        normalized = tuple(item.casefold() for item in self.extensions)
        if len(normalized) != len(set(normalized)):
            raise ValueError("E_PRESENTATION_PICKER_EXTENSION_DUPLICATE: 扩展名提示不得重复")
        return self


class ParameterPresentation(PresentationModel):
    """把一个正式参数 JSON Pointer 映射为内置表单展示元数据。"""

    parameter_pointer: JsonPointer
    label: ShortText
    description: LongText | None = None
    group_id: Identifier
    order: Annotated[int, Field(ge=0, le=10_000)]
    importance: ParameterImportance = ParameterImportance.PRIMARY
    control_hint: ControlHint = ControlHint.AUTO
    unit: ShortText | None = None
    placeholder: ShortText | None = None
    enum_labels: tuple[EnumLabel, ...] = ()
    picker: PickerPresentation | None = None

    @field_validator("parameter_pointer")
    @classmethod
    def validate_parameter_pointer(cls, value: str) -> str:
        decode_json_pointer(value)
        return value

    @field_validator("enum_labels", mode="before")
    @classmethod
    def normalize_enum_labels(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_picker_binding(self) -> Self:
        picker_controls = {
            ControlHint.FILE_PATH,
            ControlHint.FILE_PATHS,
            ControlHint.DIRECTORY_PATH,
            ControlHint.SAVE_FILE,
        }
        if (self.picker is not None) != (self.control_hint in picker_controls):
            raise ValueError(
                "E_PRESENTATION_PICKER_BINDING: picker 只能且必须绑定路径选择 control_hint"
            )
        enum_keys = tuple(
            json.dumps(
                item.value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            for item in self.enum_labels
        )
        if len(enum_keys) != len(set(enum_keys)):
            raise ValueError("E_PRESENTATION_ENUM_LABEL_DUPLICATE: enum label value 不得重复")
        return self


class PortPresentation(PresentationModel):
    """为正式 input/output port 提供文案，不重复 data type、required 或 cardinality。"""

    direction: Literal["input", "output"]
    port_id: Identifier
    label: ShortText
    description: LongText | None = None


class NodePresentation(PresentationModel):
    """以 exact type/version 绑定一个 NodeDefinition 的纯展示说明。"""

    type_id: TypeId
    definition_version: ExactVersion
    title: ShortText
    description: LongText
    category_id: Identifier
    icon_token: IconToken
    palette_level: PaletteLevel
    keywords: tuple[ShortText, ...] = ()
    parameter_groups: tuple[ParameterGroupPresentation, ...] = ()
    parameters: tuple[ParameterPresentation, ...] = ()
    ports: tuple[PortPresentation, ...] = ()
    card_summary_paths: tuple[JsonPointer, ...] = ()

    @field_validator(
        "keywords",
        "parameter_groups",
        "parameters",
        "ports",
        "card_summary_paths",
        mode="before",
    )
    @classmethod
    def normalize_sequences(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("card_summary_paths")
    @classmethod
    def validate_summary_pointers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for pointer in value:
            decode_json_pointer(pointer)
        return value

    @model_validator(mode="after")
    def validate_local_uniqueness(self) -> Self:
        group_ids = tuple(item.group_id for item in self.parameter_groups)
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("E_PRESENTATION_GROUP_DUPLICATE: parameter group 不得重复")
        pointers = tuple(item.parameter_pointer for item in self.parameters)
        if len(pointers) != len(set(pointers)):
            raise ValueError("E_PRESENTATION_PARAMETER_DUPLICATE: parameter pointer 不得重复")
        port_keys = tuple((item.direction, item.port_id) for item in self.ports)
        if len(port_keys) != len(set(port_keys)):
            raise ValueError("E_PRESENTATION_PORT_DUPLICATE: port presentation 不得重复")
        if len(self.card_summary_paths) != len(set(self.card_summary_paths)):
            raise ValueError("E_PRESENTATION_SUMMARY_DUPLICATE: card summary pointer 不得重复")
        normalized_keywords = tuple(item.casefold() for item in self.keywords)
        if len(normalized_keywords) != len(set(normalized_keywords)):
            raise ValueError("E_PRESENTATION_KEYWORD_DUPLICATE: keyword 不得重复")
        return self


class PresentationCatalog(PresentationModel):
    """汇总一个 locale 下可安全消费的节点展示条目。"""

    contract_version: Literal["0.3.0"] = PRESENTATION_CONTRACT_VERSION
    locale: Literal["zh-CN"] = PRESENTATION_LOCALE
    categories: tuple[CategoryPresentation, ...] = ()
    nodes: tuple[NodePresentation, ...] = ()

    @field_validator("categories", "nodes", mode="before")
    @classmethod
    def normalize_sequences(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_catalog_uniqueness(self) -> Self:
        category_ids = tuple(item.category_id for item in self.categories)
        if len(category_ids) != len(set(category_ids)):
            raise ValueError("E_PRESENTATION_CATEGORY_DUPLICATE: category_id 不得重复")
        node_keys = tuple((item.type_id, item.definition_version) for item in self.nodes)
        if len(node_keys) != len(set(node_keys)):
            raise ValueError("E_PRESENTATION_NODE_DUPLICATE: type/version 不得重复")
        return self


class PresentationDiagnostic(PresentationModel):
    """记录被隔离的第三方展示条目；该 warning 永不改变 Graph/Runtime 结论。"""

    code: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    message: LongText
    type_id: TypeId | None = None
    definition_version: ExactVersion | None = None
    reference: Annotated[str, StringConstraints(min_length=1, max_length=1024)] | None = None


class PresentationCatalogResolution(PresentationModel):
    """返回已绑定 catalog 及非阻塞第三方隔离诊断。"""

    catalog: PresentationCatalog
    diagnostics: tuple[PresentationDiagnostic, ...] = ()

    @field_validator("diagnostics", mode="before")
    @classmethod
    def normalize_diagnostics(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


__all__ = [
    "PRESENTATION_CONTRACT_VERSION",
    "PRESENTATION_LOCALE",
    "CategoryPresentation",
    "ControlHint",
    "EnumLabel",
    "IconToken",
    "NodePresentation",
    "PaletteLevel",
    "ParameterGroupPresentation",
    "ParameterImportance",
    "ParameterPresentation",
    "PickerPresentation",
    "PortPresentation",
    "PresentationCatalog",
    "PresentationCatalogResolution",
    "PresentationDiagnostic",
    "PresentationModel",
    "decode_json_pointer",
]
