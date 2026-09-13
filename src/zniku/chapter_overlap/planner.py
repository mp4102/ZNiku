"""按已准入的精确时间轴纯计算章节与均衡叶，禁止媒体 I/O 和执行副作用。

使用整数/Fraction 生成连续半开区间，先检查结果规模再分配叶。结果只是可重算投影，不是
Compiler、Core scope 或可运行 Graph；外部 FI 的上下文、相位与裁边均不在本阶段实现。
"""

from __future__ import annotations

from fractions import Fraction
from itertools import pairwise
from typing import Any

from .models import (
    MAX_PLANNED_LEAVES,
    AdmittedTimeline,
    AverageChapterSelector,
    ChapterLeafPlan,
    ChapterProjection,
    ChapterSettings,
    ExactFramesChapterSelector,
    FrameSpanProjection,
    LeafProjection,
    MappedCutPoint,
    canonical_seconds,
    parse_timecode,
)


class ChapterPlanningError(ValueError):
    """携带稳定错误码和字段路径；调用者无需解析中文消息来定位行。"""

    def __init__(self, code: str, message: str, field_path: tuple[str | int, ...]) -> None:
        self.code = code
        self.message = message
        self.field_path = field_path
        super().__init__(f"{code}: {message}")


def _half_up(value: Fraction) -> int:
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def _display_time(value: Fraction) -> str:
    milliseconds = _half_up(value * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, fraction = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{fraction:03d}"


def _label(ordinal: int) -> str:
    value = ordinal + 1
    parts: list[str] = []
    while value:
        value, remainder = divmod(value - 1, 26)
        parts.append(chr(ord("A") + remainder))
    return "".join(reversed(parts))


def _span(start: int, end: int, rate: Fraction) -> dict[str, Any]:
    seconds = (Fraction(start, 1) / rate, Fraction(end, 1) / rate, Fraction(end - start, 1) / rate)
    return {
        "start_frame": start,
        "end_frame": end,
        "frame_count": end - start,
        "start_seconds": canonical_seconds(seconds[0]),
        "end_seconds": canonical_seconds(seconds[1]),
        "duration_seconds": canonical_seconds(seconds[2]),
        "start_timecode": _display_time(seconds[0]),
        "end_timecode": _display_time(seconds[1]),
        "duration_timecode": _display_time(seconds[2]),
    }


def _boundaries(
    source: AdmittedTimeline, settings: ChapterSettings
) -> tuple[tuple[int, ...], tuple[dict[str, Any], ...]]:
    selector = settings.chapter_selector
    rate = Fraction(source.frame_rate)
    frames = source.frame_count
    cuts: list[int] = []
    requests: list[dict[str, Any]] = []
    if isinstance(selector, AverageChapterSelector):
        if selector.count > frames:
            raise ChapterPlanningError(
                "E_CHAPTER_COUNT_EXCEEDS_FRAMES",
                "章数不能超过源帧数，否则会产生空章",
                ("chapter_selector", "count"),
            )
        quotient, remainder = divmod(frames, selector.count)
        for index in range(1, selector.count):
            cuts.append(index * quotient + min(index, remainder))
            requests.append({"requested_time": None, "requested_frame": None})
    else:
        is_frames = isinstance(selector, ExactFramesChapterSelector)
        if isinstance(selector, ExactFramesChapterSelector):
            raw_values = selector.frames
            input_times: tuple[str, ...] = ()
        else:
            raw_values = tuple(parse_timecode(t) for t in selector.times)
            input_times = selector.times
        for index, raw in enumerate(raw_values):
            frame = raw if is_frames else _half_up(Fraction(raw, 1) * rate)
            path = ("chapter_selector", "frames" if is_frames else "times", index)
            if not 0 < frame < frames:
                raise ChapterPlanningError(
                    "E_CHAPTER_CUT_RANGE", "切分点必须映射到源首尾之间的实际帧边界", path
                )
            if cuts and frame <= cuts[-1]:
                raise ChapterPlanningError(
                    "E_CHAPTER_CUT_COLLISION", "相邻切分时间映射到了同一帧，不能创建空章", path
                )
            cuts.append(frame)
            requests.append(
                {
                    "requested_frame": raw if is_frames else None,
                    "requested_time": None if is_frames else input_times[index],
                }
            )
    mapped = tuple(
        {
            "ordinal": index,
            **request,
            "actual_frame": frame,
            "actual_seconds": canonical_seconds(Fraction(frame, 1) / rate),
            "actual_timecode": _display_time(Fraction(frame, 1) / rate),
        }
        for index, (frame, request) in enumerate(zip(cuts, requests, strict=True))
    )
    return (0, *cuts, frames), mapped


def _geometry(
    source: AdmittedTimeline, settings: ChapterSettings
) -> tuple[tuple[int, ...], tuple[dict[str, Any], ...], int, tuple[int, ...]]:
    boundaries, mapped = _boundaries(source, settings)
    cap_fraction = Fraction(source.frame_rate) * 60 * settings.leaf_max_minutes
    cap = cap_fraction.numerator // cap_fraction.denominator
    if cap < 1:
        raise ChapterPlanningError(
            "E_CHAPTER_LEAF_CAP_EMPTY",
            "当前帧率下最大叶时长不足容纳一帧",
            ("leaf_max_minutes",),
        )
    counts = tuple((end - start + cap - 1) // cap for start, end in pairwise(boundaries))
    # 在构造任何 LeafProjection 或逐叶容器之前拒绝超预算；不允许静默截断或重分章。
    if sum(counts) > MAX_PLANNED_LEAVES:
        raise ChapterPlanningError(
            "E_CHAPTER_PLAN_RESOURCE_LIMIT",
            f"只读规划最多容纳 {MAX_PLANNED_LEAVES} 叶，请调整章数或叶时长",
            ("leaf_max_minutes",),
        )
    return boundaries, mapped, cap, counts


def plan_chapters_and_leaves(
    timeline: AdmittedTimeline, settings: ChapterSettings
) -> ChapterLeafPlan:
    """从当前已准入直接输入规划章节与叶；不接受未绑定 dict 或伪造模型实例绕过校验。

    分叶先取 cap=floor(FPS*60*L)、m=ceil(n/cap)，再前余数优先均分 n。即使最短章节只有
    一帧也保留一个叶，不移动章节边界，不生成空叶或跨章叶；资源超限在分配媒体范围前失败。
    """

    if not isinstance(timeline, AdmittedTimeline) or not isinstance(settings, ChapterSettings):
        raise ChapterPlanningError(
            "E_CHAPTER_TYPED_INPUT", "规划入口必须接收已绑定的严格时间轴和设置模型", ()
        )
    source = AdmittedTimeline.model_validate(timeline, strict=True)
    checked = ChapterSettings.model_validate(settings, strict=True)
    boundaries, mapped, cap, counts = _geometry(source, checked)
    rate = Fraction(source.frame_rate)
    chapters: list[ChapterProjection] = []
    global_ordinal = 0
    for ordinal, ((start, end), count) in enumerate(zip(pairwise(boundaries), counts, strict=True)):
        quotient, remainder = divmod(end - start, count)
        leaves: list[LeafProjection] = []
        cursor = start
        for leaf_ordinal in range(count):
            next_frame = cursor + quotient + (leaf_ordinal < remainder)
            leaves.append(
                LeafProjection(
                    leaf_id=f"leaf-{global_ordinal + 1:04d}",
                    global_ordinal=global_ordinal,
                    chapter_ordinal=ordinal,
                    ordinal=leaf_ordinal,
                    **_span(cursor, next_frame, rate),
                )
            )
            cursor = next_frame
            global_ordinal += 1
        chapters.append(
            ChapterProjection(
                chapter_id=f"chapter-{ordinal + 1:04d}",
                ordinal=ordinal,
                label=_label(ordinal),
                leaves=tuple(leaves),
                **_span(start, end, rate),
            )
        )
    return ChapterLeafPlan(
        source=source,
        settings=checked,
        chapter_count=len(chapters),
        leaf_count=global_ordinal,
        leaf_frame_cap=cap,
        cut_points=tuple(MappedCutPoint(**point) for point in mapped),
        chapters=tuple(chapters),
    )


def validate_plan_projection(plan: ChapterLeafPlan) -> None:
    """重算完整投影以拒绝等总帧数的错序/换位/伪造时间；不把持久化投影当作新权威。"""

    boundaries, mapped, cap, counts = _geometry(plan.source, plan.settings)
    rate = Fraction(plan.source.frame_rate)

    def require(condition: bool, path: tuple[str | int, ...]) -> None:
        if not condition:
            raise ChapterPlanningError(
                "E_CHAPTER_PROJECTION_INVALID", "投影与直接源及设置不一致", path
            )

    require(plan.leaf_frame_cap == cap, ("leaf_frame_cap",))
    require(plan.chapter_count == len(counts) == len(plan.chapters), ("chapter_count",))
    require(plan.leaf_count == sum(counts), ("leaf_count",))
    require(tuple(cut.model_dump() for cut in plan.cut_points) == mapped, ("cut_points",))
    global_ordinal = 0
    for ordinal, chapter in enumerate(plan.chapters):
        start, end = boundaries[ordinal : ordinal + 2]
        path = ("chapters", ordinal)
        require(
            chapter.ordinal == ordinal
            and chapter.chapter_id == f"chapter-{ordinal + 1:04d}"
            and chapter.label == _label(ordinal)
            and len(chapter.leaves) == counts[ordinal],
            path,
        )
        require(
            chapter.model_dump(include=set(FrameSpanProjection.model_fields))
            == _span(start, end, rate),
            path,
        )
        quotient, remainder = divmod(end - start, counts[ordinal])
        cursor = start
        for leaf_ordinal, leaf in enumerate(chapter.leaves):
            next_frame = cursor + quotient + (leaf_ordinal < remainder)
            leaf_path = (*path, "leaves", leaf_ordinal)
            require(
                leaf.global_ordinal == global_ordinal
                and leaf.chapter_ordinal == ordinal
                and leaf.ordinal == leaf_ordinal
                and leaf.leaf_id == f"leaf-{global_ordinal + 1:04d}",
                leaf_path,
            )
            require(
                leaf.model_dump(include=set(FrameSpanProjection.model_fields))
                == _span(cursor, next_frame, rate),
                leaf_path,
            )
            cursor = next_frame
            global_ordinal += 1
