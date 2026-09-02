"""解析 v2.7 节点参数中已经由 Python authoring 派生的媒体计划。

本模块不读取 Project、Run 或 AVEnhanceFlow 状态，也不在运行时生成动态端口。它只让
adapter/validator 对普通 NodeInstance 参数做同一套严格、确定性检查。Phase 4 的模板 builder
可以复用这些值对象，但计划权威仍是普通 Graph 参数与直接 Artifact binding。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction

from zniku.avenhance_v27.probe import Av27MediaError, parse_fraction


@dataclass(frozen=True, slots=True)
class SplitSegment:
    """AtomicSplit 一个固定 output port 对应的 half-open source range。"""

    port_id: str
    source_ordinal: int
    planned_effective_video_artifact_id: str
    chapter_id: str
    chapter_ordinal: int
    leaf_id: str
    leaf_ordinal: int
    start_frame: int
    end_frame: int

    @property
    def frame_count(self) -> int:
        """返回计划叶片 N。"""

        return self.end_frame - self.start_frame


@dataclass(frozen=True, slots=True)
class ProgramChapter:
    """ProgramEncode 一个章节的 FI 输入与补尾闭合合同。"""

    chapter_id: str
    chapter_ordinal: int
    source_frames: int
    expected_fi_frames: int
    encoded_frames: int


@dataclass(frozen=True, slots=True)
class FinalSource:
    """FinalMux 一个物理 Source 的 exact 原音轨时长依据。"""

    source_ordinal: int
    source_frames: int
    source_fps: Fraction

    @property
    def duration(self) -> Fraction:
        """返回由 N/FPS 唯一确定的 rational 秒。"""

        return Fraction(self.source_frames, 1) / self.source_fps


def parse_split_segments(
    value: object,
    *,
    expected_ports: Sequence[str],
) -> tuple[SplitSegment, ...]:
    """解析动态 Split shape 并验证全局顺序与逐 Source 连续覆盖形状。

    逐 Source 的最终 ``end_frame == input N`` 需要 direct RunnerInput metadata，留给
    adapter/validator 在 payload I/O 前闭合。
    """

    if not isinstance(value, list | tuple) or not value:
        raise Av27MediaError("E_AV27_SPLIT_SEGMENTS", "segments 必须是非空 array")
    if len(value) != len(expected_ports):
        raise Av27MediaError("E_AV27_SPLIT_SEGMENTS", "segments 数量必须匹配 output ports")
    exact_fields = {
        "port_id",
        "source_ordinal",
        "planned_effective_video_artifact_id",
        "chapter_id",
        "chapter_ordinal",
        "leaf_id",
        "leaf_ordinal",
        "start_frame",
        "end_frame",
    }
    segments: list[SplitSegment] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping) or set(raw) != exact_fields:
            raise Av27MediaError("E_AV27_SPLIT_SEGMENT_FIELDS", "segment 字段集合无效")
        port_id = _text(raw["port_id"], "port_id")
        if port_id != expected_ports[index]:
            raise Av27MediaError("E_AV27_SPLIT_ORDER", "segment 顺序必须匹配 output ports")
        leaf_ordinal = _ordinal(raw["leaf_ordinal"], "leaf_ordinal")
        if leaf_ordinal != index:
            raise Av27MediaError("E_AV27_SPLIT_ORDER", "leaf_ordinal 必须从 0 连续")
        start = _nonnegative_int(raw["start_frame"], "start_frame")
        end = _positive_int(raw["end_frame"], "end_frame")
        if end <= start:
            raise Av27MediaError("E_AV27_SPLIT_RANGE", "segment end_frame 必须大于 start_frame")
        segments.append(
            SplitSegment(
                port_id=port_id,
                source_ordinal=_ordinal(raw["source_ordinal"], "source_ordinal"),
                planned_effective_video_artifact_id=_text(
                    raw["planned_effective_video_artifact_id"],
                    "planned_effective_video_artifact_id",
                ),
                chapter_id=_text(raw["chapter_id"], "chapter_id"),
                chapter_ordinal=_ordinal(raw["chapter_ordinal"], "chapter_ordinal"),
                leaf_id=_text(raw["leaf_id"], "leaf_id"),
                leaf_ordinal=leaf_ordinal,
                start_frame=start,
                end_frame=end,
            )
        )

    grouped: dict[int, list[SplitSegment]] = defaultdict(list)
    for segment in segments:
        grouped[segment.source_ordinal].append(segment)
    if tuple(sorted(grouped)) != tuple(range(len(grouped))):
        raise Av27MediaError("E_AV27_SPLIT_SOURCE_ORDER", "source_ordinal 必须从 0 连续")
    for source_ordinal, source_segments in grouped.items():
        expected_start = 0
        artifact_id = source_segments[0].planned_effective_video_artifact_id
        for segment in source_segments:
            if segment.planned_effective_video_artifact_id != artifact_id:
                raise Av27MediaError(
                    "E_AV27_SPLIT_PLAN_ID",
                    f"source {source_ordinal} 的 planned Artifact ID 不一致",
                )
            if segment.start_frame != expected_start:
                raise Av27MediaError(
                    "E_AV27_SPLIT_COVERAGE",
                    f"source {source_ordinal} range 有重叠或缺口",
                )
            expected_start = segment.end_frame
    return tuple(segments)


def parse_program_chapters(value: object) -> tuple[ProgramChapter, ...]:
    """解析并逐章验证 ``N → 2N-1 → 2N``。"""

    if not isinstance(value, list | tuple) or not value:
        raise Av27MediaError("E_AV27_PROGRAM_CHAPTERS", "chapters 必须是非空 array")
    exact_fields = {
        "chapter_id",
        "chapter_ordinal",
        "source_frames",
        "expected_fi_frames",
        "encoded_frames",
    }
    chapters: list[ProgramChapter] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping) or set(raw) != exact_fields:
            raise Av27MediaError("E_AV27_PROGRAM_CHAPTER_FIELDS", "chapter 字段集合无效")
        ordinal = _ordinal(raw["chapter_ordinal"], "chapter_ordinal")
        if ordinal != index:
            raise Av27MediaError("E_AV27_PROGRAM_CHAPTER_ORDER", "chapter ordinal 必须从 0 连续")
        source_frames = _positive_int(raw["source_frames"], "source_frames")
        expected_fi = _positive_int(raw["expected_fi_frames"], "expected_fi_frames")
        encoded = _positive_int(raw["encoded_frames"], "encoded_frames")
        if expected_fi != source_frames * 2 - 1 or encoded != source_frames * 2:
            raise Av27MediaError(
                "E_AV27_PROGRAM_FRAME_RELATION",
                "每章必须满足 N → 2N-1 → 2N",
            )
        chapters.append(
            ProgramChapter(
                chapter_id=_text(raw["chapter_id"], "chapter_id"),
                chapter_ordinal=ordinal,
                source_frames=source_frames,
                expected_fi_frames=expected_fi,
                encoded_frames=encoded,
            )
        )
    return tuple(chapters)


def parse_final_sources(value: object) -> tuple[FinalSource, ...]:
    """解析 FinalMux Source slots；时长只从 exact N/FPS 派生。"""

    if not isinstance(value, list | tuple) or not value:
        raise Av27MediaError("E_AV27_FINAL_SOURCES", "sources 必须是非空 array")
    exact_fields = {"source_ordinal", "source_frames", "source_fps"}
    result: list[FinalSource] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping) or set(raw) != exact_fields:
            raise Av27MediaError("E_AV27_FINAL_SOURCE_FIELDS", "source 字段集合无效")
        ordinal = _ordinal(raw["source_ordinal"], "source_ordinal")
        if ordinal != index:
            raise Av27MediaError("E_AV27_FINAL_SOURCE_ORDER", "source ordinal 必须从 0 连续")
        result.append(
            FinalSource(
                source_ordinal=ordinal,
                source_frames=_positive_int(raw["source_frames"], "source_frames"),
                source_fps=parse_fraction(raw["source_fps"]),
            )
        )
    return tuple(result)


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise Av27MediaError("E_AV27_PLAN_FIELD", f"{name} 必须是非空 string")
    return value


def _ordinal(value: object, name: str) -> int:
    return _nonnegative_int(value, name)


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise Av27MediaError("E_AV27_PLAN_FIELD", f"{name} 必须是非负 integer")
    return value


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise Av27MediaError("E_AV27_PLAN_FIELD", f"{name} 必须是正 integer")
    return value
