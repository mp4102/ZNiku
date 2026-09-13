"""实验性重叠 FI 坐标数学，不声明任何模型能力，不创建节点或读取媒体。

本模块只研究已重验章节在 M→2M-1、输入时刻位于偶数输出位置这一显式假设下的上下文与裁边。
帧数和范围成立不证明外部工具遵循该相位，也不证明画质。全部设置必须显式提供；最短输入不足、
超出实验分配预算或投影被篡改均失败，不补帧凑数、不扩大上下文、不改变正式章节。
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from typing import Annotated, Any, Literal, NamedTuple, Self

from pydantic import Field, StringConstraints, field_validator, model_validator

from .models import ChapterLeafPlan, ChapterModel, ChapterProjection, Ordinal, PositiveInt

# 仅约束当前纯数学实验的投影分配，不是外部模型、Core 或未来产品支持规模的限制。
MAX_CONTEXT_SOURCE_SLICES = 10_000


class ContextPlanningError(ValueError):
    """提供稳定错误码与章节/设置路径，不让调用者从人类消息猜测问题位置。"""

    def __init__(self, code: str, message: str, field_path: tuple[str | int, ...]) -> None:
        self.code = code
        self.message = message
        self.field_path = field_path
        super().__init__(f"{code}: {message}")


class ExperimentalContextSettings(ChapterModel):
    """仅显式的数学实验输入，不提供 Aion 或其他外部模型的隐含生产默认值。"""

    left_context_frames: Ordinal
    right_context_frames: PositiveInt
    minimum_input_frames: PositiveInt


class ContextSourceSlice(ChapterModel):
    """来源增强章与 context 输入之间的精确交集，只保存坐标而非伪造 Artifact 身份。

    source_* 为有效视频全局帧坐标；chapter_local_* 为增强章媒体内部偏移；context_local_*
    为待组成的 context 输入内部偏移。后续媒体节点仍必须绑定真实增强产物，不能把本值当作验收。
    """

    chapter_id: Annotated[str, StringConstraints(pattern=r"^chapter-[0-9]{4}$")]
    chapter_ordinal: Ordinal
    source_start_frame: Ordinal
    source_end_frame: PositiveInt
    chapter_local_start_frame: Ordinal
    chapter_local_end_frame: PositiveInt
    context_local_start_frame: Ordinal
    context_local_end_frame: PositiveInt
    frame_count: PositiveInt

    @model_validator(mode="after")
    def validate_lengths(self) -> Self:
        if any(
            end - start != self.frame_count
            for start, end in (
                (self.source_start_frame, self.source_end_frame),
                (self.chapter_local_start_frame, self.chapter_local_end_frame),
                (self.context_local_start_frame, self.context_local_end_frame),
            )
        ):
            raise ContextPlanningError(
                "E_CONTEXT_SLICE_RANGE", "来源交集的三套坐标必须有相同正长度", ()
            )
        return self


class ExperimentalChapterContext(ChapterModel):
    """一章在假设 FI 输出中的责任范围；crop 和 global 坐标均以两倍帧率输出帧为单位。"""

    chapter_id: Annotated[str, StringConstraints(pattern=r"^chapter-[0-9]{4}$")]
    chapter_ordinal: Ordinal
    formal_start_frame: Ordinal
    formal_end_frame: PositiveInt
    context_start_frame: Ordinal
    context_end_frame: PositiveInt
    input_frame_count: PositiveInt
    raw_fi_frame_count: PositiveInt
    crop_start_frame: Ordinal
    crop_end_frame: PositiveInt
    cropped_frame_count: PositiveInt
    global_start_half_frame: Ordinal
    global_end_half_frame: PositiveInt
    sources: Annotated[tuple[ContextSourceSlice, ...], Field(min_length=1, max_length=1000)]

    @field_validator("sources", mode="before")
    @classmethod
    def array_to_tuple(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


class ExperimentalContextPlan(ChapterModel):
    """可重算的数学实验记录，不是可运行 profile、Graph authority 或外部工具验收。"""

    status: Literal["mathematical-only"] = "mathematical-only"
    assumption: Literal["even-input-2m-minus-1"] = "even-input-2m-minus-1"
    chapter_plan: ChapterLeafPlan
    settings: ExperimentalContextSettings
    chapters: Annotated[
        tuple[ExperimentalChapterContext, ...], Field(min_length=1, max_length=1000)
    ]
    unpadded_frame_count: PositiveInt
    final_tail_clone_frames: Literal[1] = 1
    encoded_frame_count: PositiveInt

    @field_validator("chapters", mode="before")
    @classmethod
    def array_to_tuple(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("final_tail_clone_frames", mode="before")
    @classmethod
    def strict_tail_integer(cls, value: Any) -> Any:
        # Literal[1] 本身可能接受 True/1.0；实验投影仍须保持精确 integer 类型。
        if type(value) is not int:
            raise ContextPlanningError("E_CONTEXT_TAIL_TYPE", "全局尾帧数必须为严格整数 1", ())
        return value

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        _validate_projection(self)
        return self


class _ContextGeometry(NamedTuple):
    """纯整数内核结果，便于穷举验证而不创建数百万份 Pydantic/来源投影。"""

    context_start: int
    context_end: int
    input_count: int
    raw_count: int
    crop_start: int
    crop_end: int
    global_start: int
    global_end: int


def _context_geometry(
    total_frames: int, start: int, end: int, left_frames: int, right_frames: int
) -> _ContextGeometry:
    """在已校验非空正式区间及非负左/正右宽度上计算，首尾只截短可用真实上下文。"""

    context_start = max(0, start - left_frames)
    context_end = min(total_frames, end + right_frames)
    last = end == total_frames
    return _ContextGeometry(
        context_start,
        context_end,
        context_end - context_start,
        2 * (context_end - context_start) - 1,
        2 * (start - context_start),
        2 * (end - context_start) - last,
        2 * start,
        2 * end - last,
    )


def _geometry_and_sources(
    plan: ChapterLeafPlan, settings: ExperimentalContextSettings
) -> tuple[tuple[_ContextGeometry, int, int], ...]:
    """先求有限数量几何和来源章索引，再检查总体投影预算，尚未分配 source slice 对象。"""

    starts = tuple(chapter.start_frame for chapter in plan.chapters)
    ends = tuple(chapter.end_frame for chapter in plan.chapters)
    rows: list[tuple[_ContextGeometry, int, int]] = []
    slice_count = 0
    for ordinal, chapter in enumerate(plan.chapters):
        geometry = _context_geometry(
            plan.source.frame_count,
            chapter.start_frame,
            chapter.end_frame,
            settings.left_context_frames,
            settings.right_context_frames,
        )
        if geometry.input_count < settings.minimum_input_frames:
            raise ContextPlanningError(
                "E_CONTEXT_INPUT_TOO_SHORT",
                f"第 {ordinal + 1} 章可用输入仅 {geometry.input_count} 帧，"
                f"低于显式最小值 {settings.minimum_input_frames}",
                ("chapters", ordinal),
            )
        first = bisect_right(ends, geometry.context_start)
        stop = bisect_left(starts, geometry.context_end)
        rows.append((geometry, first, stop))
        slice_count += stop - first
    if slice_count > MAX_CONTEXT_SOURCE_SLICES:
        raise ContextPlanningError(
            "E_CONTEXT_RESOURCE_LIMIT",
            f"实验投影超过 {MAX_CONTEXT_SOURCE_SLICES} 个来源交集预算，不截断或改变上下文",
            ("settings",),
        )
    return tuple(rows)


def _chapter_values(
    plan: ChapterLeafPlan,
    chapter: ChapterProjection,
    geometry: _ContextGeometry,
    first: int,
    stop: int,
) -> dict[str, Any]:
    slices: list[dict[str, Any]] = []
    for source in plan.chapters[first:stop]:
        start = max(geometry.context_start, source.start_frame)
        end = min(geometry.context_end, source.end_frame)
        slices.append(
            {
                "chapter_id": source.chapter_id,
                "chapter_ordinal": source.ordinal,
                "source_start_frame": start,
                "source_end_frame": end,
                "chapter_local_start_frame": start - source.start_frame,
                "chapter_local_end_frame": end - source.start_frame,
                "context_local_start_frame": start - geometry.context_start,
                "context_local_end_frame": end - geometry.context_start,
                "frame_count": end - start,
            }
        )
    return {
        "chapter_id": chapter.chapter_id,
        "chapter_ordinal": chapter.ordinal,
        "formal_start_frame": chapter.start_frame,
        "formal_end_frame": chapter.end_frame,
        "context_start_frame": geometry.context_start,
        "context_end_frame": geometry.context_end,
        "input_frame_count": geometry.input_count,
        "raw_fi_frame_count": geometry.raw_count,
        "crop_start_frame": geometry.crop_start,
        "crop_end_frame": geometry.crop_end,
        "cropped_frame_count": geometry.crop_end - geometry.crop_start,
        "global_start_half_frame": geometry.global_start,
        "global_end_half_frame": geometry.global_end,
        "sources": tuple(slices),
    }


def plan_experimental_contexts(
    chapter_plan: ChapterLeafPlan, settings: ExperimentalContextSettings
) -> ExperimentalContextPlan:
    """重验 ChapterLeafPlan 后计算实验上下文；不把合法源参数当作实际增强产物证据。

    三项设置必须由调用者显式提供。非末章跨界插值归左章，末章少一个插值时刻；返回的全局
    tail=1 只是最终编码数学描述，此函数没有添加、复制或修改任何媒体帧。
    """

    if not isinstance(chapter_plan, ChapterLeafPlan) or not isinstance(
        settings, ExperimentalContextSettings
    ):
        raise ContextPlanningError(
            "E_CONTEXT_TYPED_INPUT", "必须传入严格章节投影和显式实验设置", ()
        )
    plan = ChapterLeafPlan.model_validate(chapter_plan, strict=True)
    checked = ExperimentalContextSettings.model_validate(settings, strict=True)
    rows = _geometry_and_sources(plan, checked)
    chapters = tuple(
        ExperimentalChapterContext.model_validate(
            _chapter_values(plan, chapter, geometry, first, stop), strict=True
        )
        for chapter, (geometry, first, stop) in zip(plan.chapters, rows, strict=True)
    )
    return ExperimentalContextPlan(
        chapter_plan=plan,
        settings=checked,
        chapters=chapters,
        unpadded_frame_count=2 * plan.source.frame_count - 1,
        encoded_frame_count=2 * plan.source.frame_count,
    )


def _validate_projection(projection: ExperimentalContextPlan) -> None:
    plan = projection.chapter_plan
    rows = _geometry_and_sources(plan, projection.settings)
    if len(projection.chapters) != len(plan.chapters):
        raise ContextPlanningError(
            "E_CONTEXT_PROJECTION", "实验章节数与正式章节不一致", ("chapters",)
        )
    for chapter, actual, (geometry, first, stop) in zip(
        plan.chapters, projection.chapters, rows, strict=True
    ):
        if actual.model_dump() != _chapter_values(plan, chapter, geometry, first, stop):
            raise ContextPlanningError(
                "E_CONTEXT_PROJECTION",
                "上下文、来源或裁边与正式章节/设置不一致",
                ("chapters", chapter.ordinal),
            )
    if (
        projection.unpadded_frame_count != 2 * plan.source.frame_count - 1
        or projection.encoded_frame_count != 2 * plan.source.frame_count
    ):
        raise ContextPlanningError("E_CONTEXT_TOTAL", "只有全局末尾一次补尾符合目标帧数", ())


__all__ = [
    "ContextPlanningError",
    "ContextSourceSlice",
    "ExperimentalChapterContext",
    "ExperimentalContextPlan",
    "ExperimentalContextSettings",
    "plan_experimental_contexts",
]
