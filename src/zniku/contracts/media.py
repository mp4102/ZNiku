"""定义 Artifact、媒体分类以及完整有序 ArtifactSet。

Artifact 的核心字段只描述稳定领域身份和声明式属性，不提供文件路径或执行入口；开放 attributes
始终是惰性数据，其具体媒体约束由合同 Schema 与未来 Runtime policy 负责。ArtifactSet 是已经完整
形成的不可变值对象；部分集合、收集进度和 ready 状态属于未来 Runtime，不在此模型中表达。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, JsonValue, field_serializer, field_validator, model_validator

from .base import (
    ContractModel,
    JsonObject,
    ensure_no_executable_keys,
    freeze_json_object,
    thaw_json,
)
from .errors import fail
from .identifiers import StableId


class ArtifactType(StrEnum):
    """Artifact 的顶层语义类别。"""

    MEDIA = "media"
    METADATA = "metadata"
    CONTROL = "control"


class MediaKind(StrEnum):
    """媒体 Artifact 的首批受控媒体类别。"""

    PROGRAM_MEDIA = "program_media"
    VIDEO = "video"
    AUDIO = "audio"
    SUBTITLE = "subtitle"
    ATTACHMENT = "attachment"


class Scope(StrEnum):
    """Artifact 或 Engine 端口的处理范围。"""

    PROGRAM = "program"
    CHAPTER = "chapter"
    LEAF = "leaf"


class CoverageUnit(StrEnum):
    """集合覆盖区间使用的精确整数单位。"""

    FRAME = "frame"
    MICROSECOND = "microsecond"
    ITEM = "item"


class CoverageMode(StrEnum):
    """成员如何覆盖集合总体区间。"""

    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"


class CoverageSpan(ContractModel):
    """使用左闭右开整数区间表达一段精确覆盖。"""

    unit: CoverageUnit
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_bounds(self) -> CoverageSpan:
        if self.end < self.start:
            fail("E_COVERAGE_BOUNDS", "coverage.end 不得小于 coverage.start")
        return self


def _validate_classification(
    artifact_type: ArtifactType, media_kind: MediaKind | None, *, entity: str
) -> None:
    if artifact_type is ArtifactType.MEDIA and media_kind is None:
        fail("E_MEDIA_KIND_REQUIRED", f"{entity} 为 media 时必须声明 media_kind")
    if artifact_type is not ArtifactType.MEDIA and media_kind is not None:
        fail("E_MEDIA_KIND_FORBIDDEN", f"{entity} 非 media 时不得声明 media_kind")


class Artifact(ContractModel):
    """具有稳定身份、scope 与声明式属性的一份领域产物。"""

    artifact_id: StableId
    artifact_type: ArtifactType
    media_kind: MediaKind | None
    scope: Scope
    scope_id: StableId
    producer_stage_run_id: StableId | None = None
    attributes: JsonObject = Field(default_factory=dict)

    @field_validator("attributes")
    @classmethod
    def freeze_attributes(cls, value: JsonObject) -> JsonObject:
        ensure_no_executable_keys(value)
        return freeze_json_object(value)

    @field_serializer("attributes")
    def serialize_attributes(self, value: JsonObject) -> JsonValue:
        return thaw_json(value)

    @model_validator(mode="after")
    def validate_classification(self) -> Artifact:
        _validate_classification(self.artifact_type, self.media_kind, entity="Artifact")
        return self


class ArtifactSetMember(ContractModel):
    """将稳定成员身份、Artifact 身份和集合内覆盖位置绑定在一起。"""

    member_id: StableId
    artifact: Artifact
    coverage: CoverageSpan


class ArtifactSet(ContractModel):
    """成员唯一、顺序明确且覆盖完整的正式 Artifact 集合。"""

    artifact_set_id: StableId
    artifact_type: ArtifactType
    media_kind: MediaKind | None
    scope: Scope
    scope_id: StableId
    expected_member_ids: tuple[StableId, ...]
    members: tuple[ArtifactSetMember, ...]
    coverage: CoverageSpan
    coverage_mode: CoverageMode
    producer_stage_run_id: StableId

    @field_validator("expected_member_ids", "members", mode="before")
    @classmethod
    def normalize_sequences(cls, value: Any) -> Any:
        if isinstance(value, list):
            return tuple(value)
        return value

    @model_validator(mode="after")
    def validate_complete_set(self) -> ArtifactSet:
        _validate_classification(self.artifact_type, self.media_kind, entity="ArtifactSet")

        expected = self.expected_member_ids
        if len(expected) != len(set(expected)):
            fail("E_SET_EXPECTED_DUPLICATE", "expected_member_ids 不得重复")

        actual = tuple(member.member_id for member in self.members)
        if len(actual) != len(set(actual)):
            fail("E_SET_MEMBER_DUPLICATE", "members 中的 member_id 不得重复")

        artifact_ids = tuple(member.artifact.artifact_id for member in self.members)
        if len(artifact_ids) != len(set(artifact_ids)):
            fail("E_SET_ARTIFACT_DUPLICATE", "同一 Artifact 不得重复占据多个集合成员")

        missing = tuple(member_id for member_id in expected if member_id not in actual)
        if missing:
            fail("E_SET_MEMBER_MISSING", f"缺少预期成员：{', '.join(missing)}")

        unexpected = tuple(member_id for member_id in actual if member_id not in expected)
        if unexpected:
            fail("E_SET_MEMBER_UNEXPECTED", f"存在未声明成员：{', '.join(unexpected)}")

        if actual != expected:
            fail("E_SET_ORDER_MISMATCH", "members 顺序必须与 expected_member_ids 完全一致")

        for member in self.members:
            artifact = member.artifact
            if artifact.artifact_type is not self.artifact_type:
                fail("E_SET_ARTIFACT_TYPE_MISMATCH", "所有成员必须具有相同 artifact_type")
            if artifact.media_kind is not self.media_kind:
                fail("E_SET_MEDIA_KIND_MISMATCH", "所有成员必须具有相同 media_kind")
            if artifact.scope is not self.scope:
                fail("E_SET_SCOPE_MISMATCH", "所有成员必须具有相同 scope")
            if member.coverage.unit is not self.coverage.unit:
                fail("E_SET_COVERAGE_UNIT_MISMATCH", "成员与集合必须使用相同 coverage unit")

        self._validate_coverage()
        return self

    def _validate_coverage(self) -> None:
        if not self.members:
            if self.coverage.start != self.coverage.end:
                fail("E_SET_COVERAGE_INCOMPLETE", "空集合只能声明零长度 coverage")
            return
        if self.coverage.start == self.coverage.end:
            fail("E_SET_COVERAGE_EMPTY_MEMBER", "非空集合必须具有非零总体 coverage")
        if any(member.coverage.start == member.coverage.end for member in self.members):
            fail("E_SET_COVERAGE_EMPTY_MEMBER", "非空集合成员不得使用零长度 coverage")

        if self.coverage_mode is CoverageMode.PARALLEL:
            if any(member.coverage != self.coverage for member in self.members):
                fail("E_SET_COVERAGE_INCOMPLETE", "parallel 集合的每个成员必须覆盖完整区间")
            return

        first = self.members[0].coverage
        last = self.members[-1].coverage
        if first.start != self.coverage.start or last.end != self.coverage.end:
            fail("E_SET_COVERAGE_INCOMPLETE", "sequential 集合必须覆盖总体区间的首尾")

        for previous, current in zip(self.members, self.members[1:], strict=False):
            if previous.coverage.end != current.coverage.start:
                fail("E_SET_COVERAGE_INCOMPLETE", "sequential 成员 coverage 必须连续且无重叠")
