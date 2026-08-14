"""提供不可变 JSON 值、确定性序列化与稳定摘要的共同实现。

对象键按 RFC 8785/JCS 规则排序，数组保持业务顺序，所有默认值和 ``null`` 都参与摘要。模型不会执行
任何字符串内容；含非 JSON 值、NaN、Infinity 或执行入口语义键的对象会失败关闭。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterator, Mapping
from types import MappingProxyType
from typing import Any, Self, cast

import rfc8785
from pydantic import BaseModel, ConfigDict, JsonValue, model_validator
from pydantic.config import ExtraValues

JsonObject = Mapping[str, JsonValue]

_EXECUTABLE_KEYS = frozenset(
    {
        "argv",
        "bash",
        "cmd",
        "code",
        "command",
        "commands",
        "cwd",
        "entrypoint",
        "env",
        "environment",
        "executable",
        "executable_path",
        "powershell",
        "python_code",
        "script",
        "script_body",
        "shell",
        "shell_command",
        "source_code",
        "working_directory",
    }
)


class FrozenDict(Mapping[str, Any]):
    """阻止常规原地修改的递归 JSON 对象容器。"""

    def __init__(self, data: Mapping[str, Any]) -> None:
        self._data = MappingProxyType(dict(data))

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __setitem__(self, _key: str, _value: Any) -> None:
        raise TypeError("合同 JSON 对象不可修改")


def freeze_json(value: Any) -> Any:
    """递归复制并冻结一个已经通过类型校验的 JSON 值。"""

    if isinstance(value, Mapping):
        return FrozenDict({str(key): freeze_json(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(freeze_json(item) for item in value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("E_JSON_NON_FINITE: JSON 数字不得为 NaN 或 Infinity")
    return value


def freeze_json_object(value: Mapping[str, JsonValue]) -> JsonObject:
    """冻结 JSON 对象，同时保留对外只读 Mapping 类型。"""

    return cast(JsonObject, freeze_json(value))


def thaw_json(value: Any) -> JsonValue:
    """将内部冻结 JSON 还原为可供序列化器处理的标准 JSON 容器。"""

    if isinstance(value, Mapping):
        return {str(key): thaw_json(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [thaw_json(item) for item in value]
    return cast(JsonValue, value)


def json_values_equal(left: JsonValue, right: JsonValue) -> bool:
    """按 JSON/JCS 类型与值语义比较，避免 Python 将布尔值等同于 0/1。"""

    try:
        left_bytes = cast(bytes, rfc8785.dumps(thaw_json(left)))
        right_bytes = cast(bytes, rfc8785.dumps(thaw_json(right)))
    except (rfc8785.CanonicalizationError, UnicodeError, ValueError) as error:
        raise ValueError(f"E_JSON_CANONICALIZATION: {error}") from error
    return left_bytes == right_bytes


def ensure_no_executable_keys(value: Any, *, path: str = "$") -> None:
    """拒绝能够把声明式合同退化为任意执行载荷的结构键。"""

    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower()).strip("_")
            if normalized in _EXECUTABLE_KEYS:
                raise ValueError(
                    f"E_EXECUTABLE_FIELD_FORBIDDEN: {path}.{key} 不得携带命令或可执行代码"
                )
            ensure_no_executable_keys(item, path=f"{path}.{key}")
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            ensure_no_executable_keys(item, path=f"{path}[{index}]")


class ContractModel(BaseModel):
    """所有正式合同值对象的严格、冻结基类。"""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        strict=True,
        validate_default=True,
    )

    @model_validator(mode="after")
    def validate_canonical_domain(self) -> Self:
        """保证任何已构造正式模型都能进入 RFC 8785 canonical domain。"""

        try:
            rfc8785.dumps(self.to_data())
        except (rfc8785.CanonicalizationError, UnicodeError, ValueError) as error:
            raise ValueError(f"E_JSON_CANONICALIZATION: {error}") from error
        return self

    def to_data(self) -> dict[str, JsonValue]:
        """返回包含默认值与 ``null`` 的标准 JSON 对象。"""

        data = self.model_dump(
            mode="json",
            exclude_defaults=False,
            exclude_none=False,
            exclude_unset=False,
        )
        return cast(dict[str, JsonValue], data)

    def to_canonical_json(self) -> str:
        """按 RFC 8785/JCS 生成跨语言稳定且保留数组业务顺序的 JSON 文本。"""

        return self.to_canonical_bytes().decode("utf-8")

    def to_canonical_bytes(self) -> bytes:
        """生成 RFC 8785 canonical JSON UTF-8 字节。"""

        try:
            return cast(bytes, rfc8785.dumps(self.to_data()))
        except (rfc8785.CanonicalizationError, UnicodeError, ValueError) as error:
            raise ValueError(f"E_JSON_CANONICALIZATION: {error}") from error

    def sha256_digest(self) -> str:
        """返回带算法前缀的 canonical JSON SHA-256 摘要。"""

        return f"sha256:{hashlib.sha256(self.to_canonical_bytes()).hexdigest()}"

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        """完整重验复制结果，禁止 Pydantic 默认的未校验 update 快捷路径。"""

        del deep  # 重建模型天然产生独立的冻结 JSON 容器。
        data = self.model_dump(mode="python", round_trip=True)
        if update:
            data.update(update)
        return type(self).model_validate(data)

    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *,
        strict: bool | None = None,
        extra: ExtraValues | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        """在 Pydantic 解析前拒绝重复对象键和非标准 JSON 常量。"""

        def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"E_JSON_DUPLICATE_KEY: 对象键 {key!r} 重复")
                result[key] = value
            return result

        def reject_constant(value: str) -> None:
            raise ValueError(f"E_JSON_NON_FINITE: 不允许 JSON 常量 {value}")

        json.loads(
            json_data,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
        return super().model_validate_json(
            json_data,
            strict=strict,
            extra=extra,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )

    @classmethod
    def from_json(cls, payload: str | bytes | bytearray) -> Self:
        """按当前精确模型严格解析 JSON；未知字段不会被忽略。"""

        return cls.model_validate_json(payload)

    @classmethod
    def from_data(cls, data: Mapping[str, JsonValue]) -> Self:
        """从标准 JSON 对象解析模型，并保持与 JSON 输入相同的严格枚举语义。"""

        try:
            payload = rfc8785.dumps(dict(data))
        except (rfc8785.CanonicalizationError, UnicodeError, ValueError) as error:
            raise ValueError(f"E_JSON_CANONICALIZATION: {error}") from error
        return cls.from_json(payload)
