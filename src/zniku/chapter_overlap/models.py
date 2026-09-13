"""声明独立分章扩展的严格输入与只读投影，不持有媒体路径或执行权限。

章叶范围只是由直接 admitted Artifact 派生的预览数据，不是 Core scope、ExecutionPlan 或第二张
Graph。模型拒绝未知字段、宽松数值和未校验复制；源事实只能由服务从已完成产物绑定后注入。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from fractions import Fraction
from typing import Annotated, Any, Final, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from zniku.runtime.models import RandomId

PROFILE_ID: Final[Literal["zniku.chapter-overlap-fi"]] = "zniku.chapter-overlap-fi"
PROFILE_VERSION: Final[Literal["0.3.2"]] = "0.3.2"
CONTRACT_VERSION: Final[Literal["0.3.2"]] = "0.3.2"
MAX_PLANNED_LEAVES = 10_000


def canonical_seconds(value: Fraction) -> str:
    """将精确秒写成唯一 n/d 文本，包括整数与零；不使用浮点。"""

    return f"{value.numerator}/{value.denominator}"


def parse_timecode(value: str) -> int:
    """解析相对 HH:MM:SS，不接受小数、帧后缀、空白或隐式规范化。"""

    if len(value) > 128 or re.fullmatch(r"[0-9]{2,}:[0-5][0-9]:[0-5][0-9]", value) is None:
        raise PydanticCustomError(
            "E_CHAPTER_TIME_FORMAT", "时间必须为 HH:MM:SS，分秒范围 00-59，小时时码至少两位"
        )
    hours, minutes, seconds = (int(part) for part in value.split(":"))
    return hours * 3600 + minutes * 60 + seconds


def _validate_timecode(value: str) -> str:
    parse_timecode(value)
    return value


def _validate_rational(value: str) -> str:
    if re.fullmatch(r"(?:0|[1-9][0-9]*)/[1-9][0-9]*", value) is None:
        raise PydanticCustomError("E_CHAPTER_RATIONAL", "精确值必须为 canonical 非负 n/d 文本")
    fraction = Fraction(value)
    if canonical_seconds(fraction) != value:
        raise PydanticCustomError("E_CHAPTER_RATIONAL", "精确值必须为已约分的 n/d 文本")
    return value


type PositiveInt = Annotated[int, Field(gt=0)]
type Ordinal = Annotated[int, Field(ge=0)]
type ExactSeconds = Annotated[
    str,
    StringConstraints(min_length=3, max_length=512, pattern=r"^(?:0|[1-9][0-9]*)/[1-9][0-9]*$"),
    AfterValidator(_validate_rational),
]
type Timecode = Annotated[
    str,
    StringConstraints(min_length=8, max_length=128, pattern=r"^[0-9]{2,}:[0-5][0-9]:[0-5][0-9]$"),
    AfterValidator(_validate_timecode),
]
type DisplayTimecode = Annotated[
    str, StringConstraints(pattern=r"^[0-9]{2,}:[0-5][0-9]:[0-5][0-9]\.[0-9]{3}$")
]


class ChapterModel(BaseModel):
    """所有值冻结且完整重验；JSON array 只在明确的序列字段转为不可变 tuple。"""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        strict=True,
        validate_default=True,
    )

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        """未校验 model_copy 不得绕过字段或投影不变量；字段本身已深度不可变。"""

        del deep
        data = self.model_dump(mode="python", round_trip=True)
        if update:
            data.update(update)
        return type(self).model_validate(data, strict=True)


class AverageChapterSelector(ChapterModel):
    """只声明平均章数；N 相关非空校验留给 admitted 时间轴上的规划。"""

    mode: Literal["average"] = "average"
    count: Annotated[int, Field(ge=1, le=1000)] = 1


def _check_order(values: tuple[int, ...], field: str) -> None:
    for index, value in enumerate(values):
        if value <= 0 or (index > 0 and value <= values[index - 1]):
            raise ValidationError.from_exception_data(
                "ChapterSelector",
                [
                    {
                        "type": PydanticCustomError(
                            "E_CHAPTER_SELECTOR_ORDER", "切分点必须为正值且严格递增"
                        ),
                        "loc": (index,),
                        "input": value,
                        "ctx": {"field": field},
                    }
                ],
            )


class ExactFramesChapterSelector(ChapterModel):
    """每个值为下一章零基首帧；最多 999 个切点，不表示每隔该帧数切分。"""

    mode: Literal["exact_frames"]
    frames: Annotated[tuple[PositiveInt, ...], Field(min_length=1, max_length=999)]

    @field_validator("frames", mode="before")
    @classmethod
    def array_to_tuple(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("frames")
    @classmethod
    def check_order(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        _check_order(value, "frames")
        return value


class ExactTimesChapterSelector(ChapterModel):
    """相对 HH:MM:SS 时间点列表；旧 AV27 的有理秒 selector 不因此扩展。"""

    mode: Literal["exact_times"]
    times: Annotated[tuple[Timecode, ...], Field(min_length=1, max_length=999)]

    @field_validator("times", mode="before")
    @classmethod
    def array_to_tuple(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("times")
    @classmethod
    def check_order(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        _check_order(tuple(parse_timecode(item) for item in value), "times")
        return value


type ChapterSelector = Annotated[
    AverageChapterSelector | ExactFramesChapterSelector | ExactTimesChapterSelector,
    Field(discriminator="mode"),
]


class ChapterSettings(ChapterModel):
    """创作者分章与每叶最大时长意图；有效默认值无需额外手工填写。"""

    chapter_selector: ChapterSelector = Field(default_factory=AverageChapterSelector)
    leaf_max_minutes: Annotated[int, Field(ge=1, le=60)] = 5


class AdmittedTimeline(ChapterModel):
    """由服务注入的直接产物时间轴，不是允许客户端自报的 probe 或 admission。"""

    artifact_id: RandomId
    frame_count: PositiveInt
    frame_rate: ExactSeconds

    @field_validator("frame_rate")
    @classmethod
    def positive_rate(cls, value: str) -> str:
        if Fraction(value) <= 0:
            raise PydanticCustomError("E_CHAPTER_RATE", "已准入时间轴的帧率必须为正数")
        return value


class FrameSpanProjection(ChapterModel):
    """源坐标半开区间及精确秒；毫秒时码只用于展示，不参与任何边界计算。"""

    start_frame: Ordinal
    end_frame: PositiveInt
    frame_count: PositiveInt
    start_seconds: ExactSeconds
    end_seconds: ExactSeconds
    duration_seconds: ExactSeconds
    start_timecode: DisplayTimecode
    end_timecode: DisplayTimecode
    duration_timecode: DisplayTimecode

    @model_validator(mode="after")
    def check_span(self) -> Self:
        if self.end_frame - self.start_frame != self.frame_count:
            raise PydanticCustomError("E_CHAPTER_PROJECTION_RANGE", "区间必须非空且匹配帧数")
        if Fraction(self.duration_seconds) <= 0 or Fraction(self.end_seconds) - Fraction(
            self.start_seconds
        ) != Fraction(self.duration_seconds):
            raise PydanticCustomError("E_CHAPTER_PROJECTION_TIME", "时间区间必须匹配精确时长")
        return self


class LeafProjection(FrameSpanProjection):
    """叶身份全局有序，ordinal 在章内从零开始，区间仍是同一源坐标。"""

    leaf_id: Annotated[str, StringConstraints(pattern=r"^leaf-[0-9]{4,}$")]
    global_ordinal: Ordinal
    chapter_ordinal: Ordinal
    ordinal: Ordinal


class ChapterProjection(FrameSpanProjection):
    """正式章节只组织已规划叶，不持有执行状态、媒体路径或隐藏拓扑。"""

    chapter_id: Annotated[str, StringConstraints(pattern=r"^chapter-[0-9]{4}$")]
    ordinal: Ordinal
    label: Annotated[str, StringConstraints(pattern=r"^[A-Z]+$")]
    leaves: Annotated[tuple[LeafProjection, ...], Field(min_length=1, max_length=10_000)]

    @field_validator("leaves", mode="before")
    @classmethod
    def array_to_tuple(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


class MappedCutPoint(ChapterModel):
    """原始用户切点和实际帧边界的并列展示，平均模式没有伪造的用户输入。"""

    ordinal: Ordinal
    requested_time: Timecode | None = None
    requested_frame: PositiveInt | None = None
    actual_frame: PositiveInt
    actual_seconds: ExactSeconds
    actual_timecode: DisplayTimecode


class ChapterLeafPlan(ChapterModel):
    """可重算的严格只读预览；持久化/运行时仍须从当前直接输入重新检查。"""

    profile_id: Literal["zniku.chapter-overlap-fi"] = PROFILE_ID
    profile_version: Literal["0.3.2"] = PROFILE_VERSION
    source: AdmittedTimeline
    settings: ChapterSettings
    chapter_count: Annotated[int, Field(ge=1, le=1000)]
    leaf_count: Annotated[int, Field(ge=1, le=10_000)]
    leaf_frame_cap: PositiveInt
    cut_points: Annotated[tuple[MappedCutPoint, ...], Field(max_length=999)]
    chapters: Annotated[tuple[ChapterProjection, ...], Field(min_length=1, max_length=1000)]

    @field_validator("cut_points", "chapters", mode="before")
    @classmethod
    def array_to_tuple(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def check_derived_projection(self) -> Self:
        # 局部导入仅为共享纯计算函数，不能调用 I/O 或递归创建另一份 plan authority。
        from .planner import validate_plan_projection

        validate_plan_projection(self)
        return self
