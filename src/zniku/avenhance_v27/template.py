"""实现 AVEnhanceFlow v2.7.0 两段模板的严格请求、确定性计划与普通 Graph builder。

本模块只把操作者请求和已经由 Project Service 从 Repository 解析出的 Artifact binding 转换为
普通 ``Project``、``NodeDefinition``、``NodeInstance`` 与 ``Edge``。它不读取数据库、不执行媒体
probe、不创建文件，也不保存 template phase 或第二套运行状态。所有章节、leaf、动态 Split shape、
布局与 canonical publication path 都由 Python 唯一生成；未知字段和宽松类型默认失败关闭。
"""

from __future__ import annotations

import re
import stat
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from itertools import pairwise
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema

from zniku.graph import Edge, Graph, GraphValidator, NodeDefinition, NodeInstance, UiPosition
from zniku.media import output_file_definition
from zniku.project import Project, ProjectSnapshot
from zniku.project.models import ProjectId, ProjectName
from zniku.runtime import Artifact
from zniku.runtime.models import RandomId

from .definitions import (
    AV27_PROFILE_VERSION,
    ENHANCEMENT_TYPE_ID,
    FINAL_MUX_TYPE_ID,
    FRAME_INTERPOLATION_TYPE_ID,
    MERGE_VIDEO_TYPE_ID,
    MOSAIC_RESTORATION_TYPE_ID,
    PROGRAM_ENCODE_TYPE_ID,
    SOURCE_ADMISSION_TYPE_ID,
    SOURCE_PROGRAM_TYPE_ID,
    atomic_split_definition,
    enhancement_definition,
    final_mux_definition,
    frame_interpolation_definition,
    merge_video_definition,
    mosaic_restoration_definition,
    program_encode_definition,
    source_admission_definition,
    source_program_definition,
)
from .probe import metadata_frame_count, metadata_rate, namespace_from_media_info

type SourceMode = Literal["program", "pre_chaptered"]
type MrMode = Literal["off", "external"]
type TemplatePhase = Literal["preparation", "expanded"]

_LOCAL_PATH = Annotated[str, StringConstraints(min_length=1, max_length=32767)]
_NONBLANK = Annotated[str, StringConstraints(min_length=1, max_length=256)]
_YEAR_PATTERN = re.compile(r"^[0-9]{4}$", re.ASCII)
_WINDOWS_FORBIDDEN = frozenset('<>:"/\\|?*')
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)
_FINAL_RATE_LABELS: Mapping[Fraction, str] = {
    Fraction(30, 1): "30p",
    Fraction(30_000, 1_001): "29p97",
    Fraction(60, 1): "60p",
    Fraction(60_000, 1_001): "59p94",
}
_SIGNAL: dict[str, JsonValue] = {
    "color_primaries": "bt709",
    "color_transfer": "bt709",
    "color_space": "bt709",
    "color_range": "tv",
    "chroma_location": "left",
    "field_order": "progressive",
    "rotation": 0,
}


class Av27TemplateError(RuntimeError):
    """表示 template request、planning 或 publication 的稳定失败。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class Av27TemplateModel(BaseModel):
    """冻结模板输入、内部 binding 与 preview DTO 的共同严格语义。"""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        strict=True,
        validate_default=True,
    )


class SourceSpec(Av27TemplateModel):
    """描述一个只读物理 Source 及其连续零基 ordinal。"""

    source_path: _LOCAL_PATH
    source_ordinal: Annotated[int, Field(ge=0)]
    chapter_label: _NONBLANK | SkipJsonSchema[None] = None

    @field_validator("source_path")
    @classmethod
    def validate_source_path_text(cls, value: str) -> str:
        if "\x00" in value or value.strip() != value:
            raise ValueError("E_AV27_TEMPLATE_SOURCE_PATH: source_path 含 NUL 或边界空白")
        return value

    @field_validator("chapter_label")
    @classmethod
    def validate_chapter_label(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or value.strip() != value):
            raise ValueError("E_AV27_TEMPLATE_CHAPTER_LABEL: chapter_label 必须是无边界空白文本")
        return value

    @model_validator(mode="after")
    def reject_explicit_null_label(self) -> SourceSpec:
        if "chapter_label" in self.model_fields_set and self.chapter_label is None:
            raise ValueError("E_AV27_TEMPLATE_CHAPTER_LABEL: chapter_label 不接受 null")
        return self


class MrOff(Av27TemplateModel):
    """声明 preparation 不生成 MR 节点。"""

    mode: Literal["off"]


class MrExternal(Av27TemplateModel):
    """声明每个 Source 一个 external MR 节点及操作者模型记录。"""

    mode: Literal["external"]
    model_name: _NONBLANK
    model_version: Annotated[str, StringConstraints(min_length=1, max_length=128)]

    @field_validator("model_name", "model_version")
    @classmethod
    def validate_model_text(cls, value: str) -> str:
        if not value.strip() or value.strip() != value:
            raise ValueError("E_AV27_TEMPLATE_MODEL: model declaration 含边界空白")
        return value


type MrDeclaration = Annotated[MrOff | MrExternal, Field(discriminator="mode")]


class PrepareRequest(Av27TemplateModel):
    """严格描述只生成 preparation Graph 的全部操作者输入。"""

    profile_version: Literal["2.7.0"]
    project_path: _LOCAL_PATH
    project_id: ProjectId
    project_name: ProjectName
    source_mode: SourceMode
    sources: tuple[SourceSpec, ...]
    mr: MrDeclaration

    @field_validator("sources", mode="before")
    @classmethod
    def normalize_sources(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("project_path")
    @classmethod
    def validate_project_path_text(cls, value: str) -> str:
        if "\x00" in value or value.strip() != value:
            raise ValueError("E_AV27_CREATE_PATH: project_path 含 NUL 或边界空白")
        return value

    @model_validator(mode="after")
    def validate_source_shape(self) -> PrepareRequest:
        if not self.sources:
            raise ValueError("E_AV27_TEMPLATE_SOURCE_COUNT: 至少需要一个 Source")
        if tuple(item.source_ordinal for item in self.sources) != tuple(range(len(self.sources))):
            raise ValueError("E_AV27_TEMPLATE_SOURCE_ORDER: source_ordinal 必须从 0 连续")
        if self.source_mode == "program":
            if len(self.sources) != 1:
                raise ValueError("E_AV27_TEMPLATE_SOURCE_COUNT: program 必须恰好一个 Source")
            if any("chapter_label" in item.model_fields_set for item in self.sources):
                raise ValueError("E_AV27_TEMPLATE_CHAPTER_LABEL: program 禁止 chapter_label")
        elif any(item.chapter_label is None for item in self.sources):
            raise ValueError("E_AV27_TEMPLATE_CHAPTER_LABEL: pre_chaptered 必须声明 chapter_label")
        return self


class SingleChapterSelector(Av27TemplateModel):
    """声明 program 的唯一 ``[0,N)`` 章。"""

    mode: Literal["single"]


class ExactFramesChapterSelector(Av27TemplateModel):
    """声明每个后续章的零基首帧。"""

    mode: Literal["exact_frames"]
    frames: tuple[Annotated[int, Field(gt=0)], ...]

    @field_validator("frames", mode="before")
    @classmethod
    def normalize_frames(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("frames")
    @classmethod
    def validate_frames(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value or any(right <= left for left, right in pairwise(value)):
            raise ValueError("E_AV27_TEMPLATE_SELECTOR_ORDER: frames 必须非空且严格递增")
        return value


class ExactTimesChapterSelector(Av27TemplateModel):
    """声明 canonical rational 秒形式的 program 章边界。"""

    mode: Literal["exact_times"]
    times: tuple[Annotated[str, StringConstraints(min_length=1, max_length=128)], ...]

    @field_validator("times", mode="before")
    @classmethod
    def normalize_times(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("times")
    @classmethod
    def validate_times(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("E_AV27_TEMPLATE_SELECTOR_ORDER: times 必须非空")
        parsed = tuple(parse_canonical_time(item) for item in value)
        if any(right <= left for left, right in pairwise(parsed)):
            raise ValueError("E_AV27_TEMPLATE_SELECTOR_ORDER: times 必须严格递增")
        return value


type ChapterSelector = Annotated[
    SingleChapterSelector | ExactFramesChapterSelector | ExactTimesChapterSelector,
    Field(discriminator="mode"),
]


class StageDeclaration(Av27TemplateModel):
    """保存 external stage 的唯一操作者模型声明。"""

    model_name: _NONBLANK
    model_version: (
        Annotated[str, StringConstraints(min_length=1, max_length=128)] | SkipJsonSchema[None]
    ) = None

    @field_validator("model_name", "model_version")
    @classmethod
    def validate_model_text(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or value.strip() != value):
            raise ValueError("E_AV27_TEMPLATE_MODEL: model declaration 含边界空白")
        return value

    @model_validator(mode="after")
    def reject_explicit_null_version(self) -> StageDeclaration:
        if "model_version" in self.model_fields_set and self.model_version is None:
            raise ValueError("E_AV27_TEMPLATE_MODEL: model_version 不接受 null")
        return self


class EnhancementDeclaration(StageDeclaration):
    """声明全节目 Enhancement 模型和规范化整数 scale。"""

    actual_scale_factor: Annotated[int, Field(gt=0)] = 1


class FrameInterpolationDeclaration(StageDeclaration):
    """声明全节目 Frame Interpolation 模型。"""


class ProgramEncodeDeclaration(Av27TemplateModel):
    """选择冻结的 GPU 或 CPU Program encoder profile。"""

    encoder: Literal["gpu", "cpu"]


class PublicationRequest(Av27TemplateModel):
    """声明发布目录、可选片名子目录及显式覆盖；省略 layout 时直接写入所选目录。"""

    output_root: _LOCAL_PATH
    title: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    year: Annotated[str, StringConstraints(min_length=4, max_length=4)]
    overwrite: bool
    layout: Literal["direct", "title_subdirectory"] = "direct"

    @field_validator("output_root", "title", "year")
    @classmethod
    def reject_boundary_whitespace_or_nul(cls, value: str) -> str:
        if "\x00" in value or value.strip() != value:
            raise ValueError("E_AV27_NAMING_TEXT: publication 文本含 NUL 或边界空白")
        return value


class ExpandRequest(Av27TemplateModel):
    """严格描述基于明确 preparation Run 展开后半图的操作者输入。"""

    profile_version: Literal["2.7.0"]
    preparation_run_id: RandomId
    chapter_selector: ChapterSelector | SkipJsonSchema[None] = None
    leaf_duration_minutes: Annotated[int, Field(gt=0)]
    enhancement: EnhancementDeclaration
    frame_interpolation: FrameInterpolationDeclaration
    program_encode: ProgramEncodeDeclaration
    publication: PublicationRequest

    @model_validator(mode="after")
    def reject_explicit_null_selector(self) -> ExpandRequest:
        if "chapter_selector" in self.model_fields_set and self.chapter_selector is None:
            raise ValueError("E_AV27_TEMPLATE_SELECTOR_UNSUPPORTED: chapter_selector 不接受 null")
        return self


class PrepareTemplatePreviewRequest(Av27TemplateModel):
    """包装 preparation preview，不接受客户端 Graph 或 definition。"""

    action: Literal["prepare"]
    request: PrepareRequest


class ExpandTemplatePreviewRequest(Av27TemplateModel):
    """包装 expanded preview，不接受客户端 Artifact metadata。"""

    action: Literal["expand"]
    request: ExpandRequest


type TemplatePreviewRequest = Annotated[
    PrepareTemplatePreviewRequest | ExpandTemplatePreviewRequest,
    Field(discriminator="action"),
]


class PreparationSourceBinding(Av27TemplateModel):
    """保存 Project Service 从 Repository 解析出的一个 Source 精确 binding。"""

    source_ordinal: Annotated[int, Field(ge=0)]
    source_node_id: str
    source_media_artifact: Artifact
    effective_video_artifact: Artifact
    mr_node_id: str | None = None
    chapter_label: str | None = None


class PreparationBinding(Av27TemplateModel):
    """保存 expand 所需的只读 Repository facts；它不进入公开 request。"""

    project_id: ProjectId
    preparation_run_id: RandomId
    source_mode: SourceMode
    mr_mode: MrMode
    admission_node_id: str
    admission_artifact: Artifact
    sources: tuple[PreparationSourceBinding, ...]

    @field_validator("sources", mode="before")
    @classmethod
    def normalize_sources(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_sources(self) -> PreparationBinding:
        if not self.sources or tuple(item.source_ordinal for item in self.sources) != tuple(
            range(len(self.sources))
        ):
            raise ValueError("E_AV27_EXPAND_SOURCE_ORDER: binding sources 必须从 0 连续")
        if self.source_mode == "program" and len(self.sources) != 1:
            raise ValueError("E_AV27_EXPAND_SOURCE_COUNT: program binding 必须恰好一个 Source")
        has_all_mr_nodes = all(item.mr_node_id is not None for item in self.sources)
        if (self.mr_mode == "external") != has_all_mr_nodes:
            raise ValueError("E_AV27_EXPAND_MR_BINDING: MR mode 与 binding 不一致")
        if self.source_mode == "pre_chaptered" and any(
            item.chapter_label is None for item in self.sources
        ):
            raise ValueError("E_AV27_EXPAND_CHAPTER_LABEL: pre_chaptered binding 缺少 label")
        return self


class ResolvedChapterPlan(Av27TemplateModel):
    """表示一个 server-derived half-open Chapter range。"""

    chapter_id: str
    chapter_ordinal: Annotated[int, Field(ge=0)]
    label: str
    source_ordinal: Annotated[int, Field(ge=0)]
    start_frame: Annotated[int, Field(ge=0)]
    end_frame: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def validate_range(self) -> ResolvedChapterPlan:
        if self.end_frame <= self.start_frame:
            raise ValueError("E_AV27_PLAN_CHAPTER_RANGE: Chapter range 必须非空")
        return self

    @property
    def frame_count(self) -> int:
        """返回当前章的 source N。"""

        return self.end_frame - self.start_frame


class DerivedLeafPlan(Av27TemplateModel):
    """表示一个与动态 Split output port 一一对应的 half-open leaf。"""

    port_id: str
    leaf_id: str
    leaf_ordinal: Annotated[int, Field(ge=0)]
    chapter_id: str
    chapter_ordinal: Annotated[int, Field(ge=0)]
    source_ordinal: Annotated[int, Field(ge=0)]
    start_frame: Annotated[int, Field(ge=0)]
    end_frame: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def validate_range(self) -> DerivedLeafPlan:
        if self.end_frame <= self.start_frame:
            raise ValueError("E_AV27_PLAN_LEAF_RANGE: Leaf range 必须非空")
        return self

    @property
    def frame_count(self) -> int:
        """返回当前 leaf 的 N。"""

        return self.end_frame - self.start_frame


class LeafPlanProjection(Av27TemplateModel):
    """向 Studio 投影 Python 已计算的 leaf frame/time，不让 TS 复制 planner。"""

    leaf_id: str
    leaf_ordinal: int
    port_id: str
    start_frame: int
    end_frame: int
    start_time_seconds: str
    end_time_seconds: str
    start_timecode: str
    end_timecode: str


class ChapterPlanProjection(Av27TemplateModel):
    """向 Studio 投影一个 Chapter 及其有序 leaves。"""

    chapter_id: str
    chapter_ordinal: int
    label: str
    source_ordinal: int
    start_frame: int
    end_frame: int
    start_time_seconds: str
    end_time_seconds: str
    start_timecode: str
    end_timecode: str
    leaves: tuple[LeafPlanProjection, ...]

    @field_validator("leaves", mode="before")
    @classmethod
    def normalize_leaves(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


class ManualStageSummary(Av27TemplateModel):
    """告诉 Studio 人工节点数量与固定交接容器。"""

    stage: Literal["mosaic_restoration", "enhancement", "frame_interpolation"]
    node_count: Annotated[int, Field(ge=0)]
    output_container: Literal[".mkv", ".mov"]


class TemplatePlanSummary(Av27TemplateModel):
    """保存 preview 所需的 display-ready server projection。"""

    source_count: Annotated[int, Field(gt=0)]
    chapter_count: Annotated[int, Field(ge=0)]
    leaf_count: Annotated[int, Field(ge=0)]
    mr_mode: MrMode
    preparation_run_id: RandomId | None = None
    effective_video_artifact_ids: tuple[str, ...] = ()
    chapters: tuple[ChapterPlanProjection, ...] = ()
    manual_stages: tuple[ManualStageSummary, ...] = ()
    output_target_path: str | None = None
    output_directory_to_create: str | None = None

    @field_validator("effective_video_artifact_ids", "chapters", "manual_stages", mode="before")
    @classmethod
    def normalize_sequences(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


class TemplateBuild(Av27TemplateModel):
    """返回一次纯 builder 生成的普通 Project 内容与 preview plan。"""

    phase: TemplatePhase
    project: Project
    definitions: tuple[NodeDefinition, ...]
    plan: TemplatePlanSummary

    @field_validator("definitions", mode="before")
    @classmethod
    def normalize_definitions(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


@dataclass(frozen=True, slots=True)
class _SourceFacts:
    ordinal: int
    node_id: str
    source_artifact: Artifact
    effective_artifact: Artifact
    frame_count: int
    frame_rate: Fraction
    geometry: tuple[int, int]
    signal: dict[str, JsonValue]
    chapter_label: str | None
    mr_node_id: str | None


def parse_canonical_time(value: str) -> Fraction:
    """解析 canonical ``str(Fraction)`` 正秒值；拒绝时间码、float 与未约分文本。"""

    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError("E_AV27_TEMPLATE_SELECTOR_TIME: time 必须是 canonical rational string")
    try:
        parsed = Fraction(value)
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError("E_AV27_TEMPLATE_SELECTOR_TIME: time 不是 rational") from error
    if parsed <= 0 or str(parsed) != value:
        raise ValueError("E_AV27_TEMPLATE_SELECTOR_TIME: time 必须是正 canonical str(Fraction)")
    return parsed


def excel_chapter_label(ordinal: int) -> str:
    """按零基 ordinal 生成 A..Z/AA.. 的稳定 program 显示 label。"""

    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
        raise Av27TemplateError("E_AV27_PLAN_CHAPTER_ORDER", "chapter ordinal 必须是非负整数")
    value = ordinal + 1
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def resolve_chapter_plan(
    sources: Sequence[_SourceFacts],
    *,
    source_mode: SourceMode,
    selector: ChapterSelector | None,
) -> tuple[ResolvedChapterPlan, ...]:
    """只从 admitted N/FPS 和 strict selector 生成稳定 Chapter ranges。"""

    if not sources:
        raise Av27TemplateError("E_AV27_PLAN_SOURCE_COUNT", "planning sources 不能为空")
    if source_mode == "pre_chaptered":
        if selector is not None:
            raise Av27TemplateError(
                "E_AV27_TEMPLATE_SELECTOR_UNSUPPORTED",
                "pre_chaptered 禁止 chapter_selector",
            )
        return tuple(
            ResolvedChapterPlan(
                chapter_id=f"chapter-{source.ordinal + 1:04d}",
                chapter_ordinal=source.ordinal,
                label=source.chapter_label or "",
                source_ordinal=source.ordinal,
                start_frame=0,
                end_frame=source.frame_count,
            )
            for source in sources
        )

    if len(sources) != 1 or selector is None:
        raise Av27TemplateError(
            "E_AV27_TEMPLATE_SELECTOR_REQUIRED",
            "program 必须有一个 Source 和显式 chapter_selector",
        )
    source = sources[0]
    boundaries: tuple[int, ...]
    if isinstance(selector, SingleChapterSelector):
        boundaries = ()
    elif isinstance(selector, ExactFramesChapterSelector):
        boundaries = selector.frames
    elif isinstance(selector, ExactTimesChapterSelector):
        mapped = tuple(
            _round_half_up(parse_canonical_time(value) * source.frame_rate)
            for value in selector.times
        )
        if any(right <= left for left, right in pairwise(mapped)):
            raise Av27TemplateError(
                "E_AV27_PLAN_BOUNDARY_COLLISION",
                "exact times 映射后 chapter frames 不再严格递增",
            )
        boundaries = mapped
    else:  # pragma: no cover - strict discriminated union 的封闭分支
        raise Av27TemplateError("E_AV27_TEMPLATE_SELECTOR_UNSUPPORTED", "未知 selector")
    if any(boundary <= 0 or boundary >= source.frame_count for boundary in boundaries):
        raise Av27TemplateError(
            "E_AV27_PLAN_BOUNDARY_RANGE",
            "chapter boundary 必须满足 0 < F < N",
        )
    all_boundaries = (0, *boundaries, source.frame_count)
    return tuple(
        ResolvedChapterPlan(
            chapter_id=f"chapter-{ordinal + 1:04d}",
            chapter_ordinal=ordinal,
            label=excel_chapter_label(ordinal),
            source_ordinal=0,
            start_frame=start,
            end_frame=end,
        )
        for ordinal, (start, end) in enumerate(pairwise(all_boundaries))
    )


def derive_leaf_plan(
    chapters: Sequence[ResolvedChapterPlan],
    *,
    frame_rate: Fraction,
    leaf_duration_minutes: int,
) -> tuple[DerivedLeafPlan, ...]:
    """用 exact Fraction 与 Python ties-to-even round 唯一派生全节目 leaves。"""

    if (
        not chapters
        or isinstance(leaf_duration_minutes, bool)
        or not isinstance(leaf_duration_minutes, int)
        or leaf_duration_minutes <= 0
    ):
        raise Av27TemplateError("E_AV27_PLAN_LEAF_DURATION", "leaf duration 必须是正整数")
    frames_per_leaf = round(frame_rate * leaf_duration_minutes * 60)
    if frames_per_leaf <= 0:
        raise Av27TemplateError("E_AV27_PLAN_LEAF_DURATION", "leaf duration 没有产生正帧数")
    leaves: list[DerivedLeafPlan] = []
    for chapter in chapters:
        start = chapter.start_frame
        while start < chapter.end_frame:
            end = min(start + frames_per_leaf, chapter.end_frame)
            ordinal = len(leaves)
            leaves.append(
                DerivedLeafPlan(
                    port_id=f"leaf-{ordinal + 1:04d}",
                    leaf_id=f"leaf-{ordinal + 1:04d}",
                    leaf_ordinal=ordinal,
                    chapter_id=chapter.chapter_id,
                    chapter_ordinal=chapter.chapter_ordinal,
                    source_ordinal=chapter.source_ordinal,
                    start_frame=start,
                    end_frame=end,
                )
            )
            start = end
    return tuple(leaves)


def format_timecode(seconds: Fraction) -> str:
    """把 exact 秒只为展示做 half-up 毫秒格式化，不反馈到计划。"""

    if seconds < 0:
        raise Av27TemplateError("E_AV27_PLAN_TIME", "展示时间不得为负")
    milliseconds = _round_half_up(seconds * 1000)
    total_seconds, millis = divmod(milliseconds, 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, second = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{second:02d}.{millis:03d}"


def chapter_plan_projection(
    chapters: Sequence[ResolvedChapterPlan],
    leaves: Sequence[DerivedLeafPlan],
    *,
    source_rates: Mapping[int, Fraction],
) -> tuple[ChapterPlanProjection, ...]:
    """生成 Studio 可直接显示的 frame/rational/timecode projection。"""

    projected: list[ChapterPlanProjection] = []
    for chapter in chapters:
        try:
            rate = source_rates[chapter.source_ordinal]
        except KeyError as error:
            raise Av27TemplateError("E_AV27_PLAN_SOURCE_RATE", "Chapter 缺少 Source FPS") from error
        chapter_leaves = tuple(item for item in leaves if item.chapter_id == chapter.chapter_id)
        projected.append(
            ChapterPlanProjection(
                chapter_id=chapter.chapter_id,
                chapter_ordinal=chapter.chapter_ordinal,
                label=chapter.label,
                source_ordinal=chapter.source_ordinal,
                start_frame=chapter.start_frame,
                end_frame=chapter.end_frame,
                start_time_seconds=str(Fraction(chapter.start_frame, 1) / rate),
                end_time_seconds=str(Fraction(chapter.end_frame, 1) / rate),
                start_timecode=format_timecode(Fraction(chapter.start_frame, 1) / rate),
                end_timecode=format_timecode(Fraction(chapter.end_frame, 1) / rate),
                leaves=tuple(
                    LeafPlanProjection(
                        leaf_id=leaf.leaf_id,
                        leaf_ordinal=leaf.leaf_ordinal,
                        port_id=leaf.port_id,
                        start_frame=leaf.start_frame,
                        end_frame=leaf.end_frame,
                        start_time_seconds=str(Fraction(leaf.start_frame, 1) / rate),
                        end_time_seconds=str(Fraction(leaf.end_frame, 1) / rate),
                        start_timecode=format_timecode(Fraction(leaf.start_frame, 1) / rate),
                        end_timecode=format_timecode(Fraction(leaf.end_frame, 1) / rate),
                    )
                    for leaf in chapter_leaves
                ),
            )
        )
    return tuple(projected)


def publication_directory(request: PublicationRequest) -> Path:
    """只读检查输出位置；允许待创建的直属片名目录，不执行 mkdir 或媒体分析。"""
    title = unicodedata.normalize("NFC", request.title)
    if not 1 <= len(title) <= 120 or title.strip() != title or title.endswith((".", " ")):
        raise Av27TemplateError("E_AV27_NAMING_TITLE", "title 长度或边界字符无效")
    if title in {".", ".."} or any(
        ord(character) < 32 or character in _WINDOWS_FORBIDDEN for character in title
    ):
        raise Av27TemplateError("E_AV27_NAMING_TITLE", "title 含 Windows 禁止字符")
    stem = title.partition(".")[0]
    if stem.upper() in _WINDOWS_RESERVED:
        raise Av27TemplateError("E_AV27_NAMING_RESERVED", "title 使用 Windows reserved stem")
    if _YEAR_PATTERN.fullmatch(request.year) is None:
        raise Av27TemplateError("E_AV27_NAMING_YEAR", "year 必须是 ASCII 四位数字")
    root_raw = Path(request.output_root)
    if not root_raw.is_absolute():
        raise Av27TemplateError("E_AV27_NAMING_ROOT", "请选择输出文件夹，不能使用相对位置。")
    try:
        root = root_raw.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise Av27TemplateError(
            "E_AV27_NAMING_ROOT", "输出目录不存在或无法访问，请重新选择。"
        ) from error
    if not root.is_dir():
        raise Av27TemplateError(
            "E_AV27_NAMING_ROOT", "所选输出位置不是文件夹，请重新选择一个已有文件夹。"
        )
    if request.layout == "direct":
        return root
    parent_raw = root / f"{title} ({request.year})"
    if _is_reparse_or_symlink(parent_raw):
        raise Av27TemplateError("E_AV27_NAMING_PARENT", "成片子文件夹不得是链接或 reparse point。")
    try:
        parent = parent_raw.resolve(strict=True)
    except FileNotFoundError as error:
        # 只有目录项确实缺失才允许将来的 OutputFile 创建；预览本身始终无副作用。
        try:
            parent_raw.lstat()
        except FileNotFoundError:
            return parent_raw
        except OSError:
            message = f"无法访问成片子文件夹：{parent_raw}。请检查权限或该位置的链接，再重试。"
        else:
            message = f"无法访问成片子文件夹：{parent_raw}。请检查该位置的失效链接，再重试。"
        raise Av27TemplateError("E_AV27_NAMING_PARENT", message) from error
    except (OSError, RuntimeError) as error:
        raise Av27TemplateError(
            "E_AV27_NAMING_PARENT",
            f"无法访问成片子文件夹：{parent_raw}。请检查权限或该位置的链接，再重试。",
        ) from error
    try:
        parent.relative_to(root)
    except ValueError as error:
        # 只显示用户所选根内的预期位置，不把解析后的根外链接目标泄露到诊断。
        raise Av27TemplateError(
            "E_AV27_NAMING_PARENT",
            f"成片子文件夹越出所选输出根目录：{parent_raw}。"
            "请检查该位置的链接，或选择其他输出根目录；未修改任何目录。",
        ) from error
    if not parent.is_dir():
        raise Av27TemplateError(
            "E_AV27_NAMING_PARENT",
            f"成片位置不是文件夹：{parent_raw}。"
            "请检查同名文件或选择其他输出根目录；未覆盖或删除现有内容。",
        )
    if parent == root:
        raise Av27TemplateError(
            "E_AV27_NAMING_PARENT",
            f"成片位置不是输出根目录内的独立子文件夹：{parent_raw}。"
            "请检查该位置的链接，或选择其他输出根目录。",
        )
    return parent


def canonical_publication_target(
    request: PublicationRequest,
    *,
    mr_mode: MrMode,
    final_frame_rate: Fraction,
    height: int,
) -> Path:
    """只读生成 canonical 文件名；目录布局不改变媒体命名，既有目标仍须显式覆盖。"""

    parent = publication_directory(request)
    title = unicodedata.normalize("NFC", request.title)
    if isinstance(height, bool) or not isinstance(height, int) or height <= 0:
        raise Av27TemplateError("E_AV27_NAMING_HEIGHT", "output height 必须是正整数")
    rate_label = _FINAL_RATE_LABELS.get(final_frame_rate)
    if rate_label is None:
        raise Av27TemplateError("E_AV27_NAMING_RATE", "最终 FPS 没有冻结的 rate label")

    marker = "MR Enhanced" if mr_mode == "external" else "Enhanced"
    filename = f"{title} ({request.year}) - {marker} FI{rate_label} {height}p.mkv"
    target = parent / filename
    if target.exists() or target.is_symlink():
        if _is_reparse_or_symlink(target):
            raise Av27TemplateError("E_AV27_NAMING_TARGET", "target 不得是 symlink/reparse point")
        try:
            resolved_target = target.resolve(strict=True)
            resolved_target.relative_to(parent)
        except (OSError, ValueError) as error:
            raise Av27TemplateError("E_AV27_NAMING_TARGET", "target containment 无效") from error
        if not resolved_target.is_file():
            raise Av27TemplateError("E_AV27_NAMING_TARGET", "既有 target 必须是普通文件")
        if not request.overwrite:
            raise Av27TemplateError("E_AV27_NAMING_EXISTS", "target 已存在且 overwrite=false")
        return resolved_target
    candidate = target.resolve(strict=False)
    try:
        candidate.relative_to(parent)
    except ValueError as error:  # pragma: no cover - parent/filename 由上方闭合，保留防御
        raise Av27TemplateError("E_AV27_NAMING_TARGET", "target 逃逸 canonical parent") from error
    return candidate


def validate_prepare_paths(request: PrepareRequest) -> tuple[Path, tuple[Path, ...]]:
    """只做 target/source 的 cheap path gate，不执行 packet traversal。"""

    project_raw = Path(request.project_path)
    if project_raw.suffix != ".zniku":
        raise Av27TemplateError("E_AV27_CREATE_EXTENSION", "Project 扩展名必须精确为 .zniku")
    if project_raw.exists():
        raise Av27TemplateError("E_AV27_CREATE_EXISTS", "Project target 已存在")
    try:
        parent = project_raw.parent.resolve(strict=True)
    except OSError as error:
        raise Av27TemplateError("E_AV27_CREATE_PARENT", str(error)) from error
    if not parent.is_dir():
        raise Av27TemplateError("E_AV27_CREATE_PARENT", "Project parent 必须是现有目录")
    project_path = parent / project_raw.name
    sources: list[Path] = []
    for source in request.sources:
        raw = Path(source.source_path)
        if not raw.is_absolute():
            raise Av27TemplateError("E_AV27_TEMPLATE_SOURCE_PATH", "source_path 必须是绝对路径")
        try:
            resolved = raw.resolve(strict=True)
            stat = resolved.stat()
        except OSError as error:
            raise Av27TemplateError("E_AV27_TEMPLATE_SOURCE_PATH", str(error)) from error
        if not resolved.is_file() or stat.st_size <= 0:
            raise Av27TemplateError(
                "E_AV27_TEMPLATE_SOURCE_FILE",
                "Source 必须是非空普通文件",
            )
        sources.append(resolved)
    return project_path, tuple(sources)


def build_preparation(request: PrepareRequest) -> TemplateBuild:
    """确定性生成 Source/Admission/optional MR preparation Graph。"""

    _, source_paths = validate_prepare_paths(request)
    source_definition = source_program_definition()
    admission_definition = source_admission_definition()
    mr_definition = mosaic_restoration_definition()
    source_nodes: list[NodeInstance] = []
    edges: list[Edge] = []
    for source, resolved in zip(request.sources, source_paths, strict=True):
        label = excel_chapter_label(source.source_ordinal)
        node_id = "source.program" if request.source_mode == "program" else f"source.{label}"
        parameters: dict[str, JsonValue] = {
            "source_path": str(resolved),
            "source_ordinal": source.source_ordinal,
        }
        if source.chapter_label is not None:
            parameters["label"] = source.chapter_label
        source_nodes.append(
            NodeInstance(
                node_id=node_id,
                type_id=SOURCE_PROGRAM_TYPE_ID,
                definition_version=source_definition.version,
                parameters=parameters,
                ui_position=UiPosition(x=40, y=80 + source.source_ordinal * 180),
            )
        )
        edges.append(
            Edge(
                source_node_id=node_id,
                source_port_id="source_media",
                target_node_id="admission",
                target_port_id="sources",
                ordinal=source.source_ordinal,
            )
        )

    admission = NodeInstance(
        node_id="admission",
        type_id=SOURCE_ADMISSION_TYPE_ID,
        definition_version=admission_definition.version,
        parameters={
            "source_mode": request.source_mode,
            "sources": [
                {"source_ordinal": index, "source_node_id": node.node_id}
                for index, node in enumerate(source_nodes)
            ],
        },
        ui_position=UiPosition(x=330, y=80 + (len(source_nodes) - 1) * 90),
    )
    nodes: list[NodeInstance] = [*source_nodes, admission]
    definitions: list[NodeDefinition] = [source_definition, admission_definition]
    manual: tuple[ManualStageSummary, ...] = ()
    if isinstance(request.mr, MrExternal):
        definitions.append(mr_definition)
        manual = (
            ManualStageSummary(
                stage="mosaic_restoration",
                node_count=len(source_nodes),
                output_container=".mkv",
            ),
        )
        for source_node in source_nodes:
            label = excel_chapter_label(cast(int, source_node.parameters["source_ordinal"]))
            mr_node_id = f"mr.{label}"
            nodes.append(
                NodeInstance(
                    node_id=mr_node_id,
                    type_id=MOSAIC_RESTORATION_TYPE_ID,
                    definition_version=mr_definition.version,
                    parameters={
                        "model_name": request.mr.model_name,
                        "model_version": request.mr.model_version,
                    },
                    ui_position=UiPosition(
                        x=620,
                        y=80 + cast(int, source_node.parameters["source_ordinal"]) * 180,
                    ),
                )
            )
            edges.extend(
                (
                    Edge(
                        source_node_id=source_node.node_id,
                        source_port_id="video",
                        target_node_id=mr_node_id,
                        target_port_id="video",
                    ),
                    Edge(
                        source_node_id="admission",
                        source_port_id="gate",
                        target_node_id=mr_node_id,
                        target_port_id="gate",
                    ),
                )
            )

    graph = Graph(nodes=tuple(nodes), edges=tuple(edges))
    GraphValidator(definitions).validate(graph)
    project = Project(project_id=request.project_id, name=request.project_name, graph=graph)
    return TemplateBuild(
        phase="preparation",
        project=project,
        definitions=tuple(definitions),
        plan=TemplatePlanSummary(
            source_count=len(source_nodes),
            chapter_count=0,
            leaf_count=0,
            mr_mode=request.mr.mode,
            manual_stages=manual,
        ),
    )


def build_expanded(
    current: ProjectSnapshot,
    request: ExpandRequest,
    binding: PreparationBinding,
) -> TemplateBuild:
    """从当前 preparation Project 与 Repository binding 生成完整普通 expanded DAG。"""

    if current.project.project_id != binding.project_id:
        raise Av27TemplateError("E_AV27_EXPAND_PROJECT", "preparation binding 不属于当前 Project")
    if request.preparation_run_id != binding.preparation_run_id:
        raise Av27TemplateError("E_AV27_EXPAND_RUN", "preparation_run_id binding 不一致")
    if binding.source_mode == "program" and request.chapter_selector is None:
        raise Av27TemplateError("E_AV27_TEMPLATE_SELECTOR_REQUIRED", "program 必须提供 selector")
    if binding.source_mode == "pre_chaptered" and request.chapter_selector is not None:
        raise Av27TemplateError(
            "E_AV27_TEMPLATE_SELECTOR_UNSUPPORTED",
            "pre_chaptered 禁止 selector",
        )
    sources = _source_facts(binding)
    common_rate = sources[0].frame_rate
    if any(source.frame_rate != common_rate for source in sources):
        raise Av27TemplateError("E_AV27_EXPAND_RATE", "effective videos FPS 不一致")
    chapters = resolve_chapter_plan(
        sources,
        source_mode=binding.source_mode,
        selector=request.chapter_selector,
    )
    leaves = derive_leaf_plan(
        chapters,
        frame_rate=common_rate,
        leaf_duration_minutes=request.leaf_duration_minutes,
    )
    split_definition = atomic_split_definition(len(leaves))
    enhancement_def = enhancement_definition()
    merge_def = merge_video_definition()
    fi_def = frame_interpolation_definition()
    program_def = program_encode_definition()
    final_def = final_mux_definition()
    output_def = output_file_definition("MediaFile")
    scale = request.enhancement.actual_scale_factor
    common_geometry = sources[0].geometry
    common_signal = sources[0].signal
    if any(
        source.geometry != common_geometry or source.signal != common_signal for source in sources
    ):
        raise Av27TemplateError(
            "E_AV27_EXPAND_MEDIA_MISMATCH",
            "effective videos 的 geometry/signal 不一致",
        )
    input_geometry: dict[str, JsonValue] = {
        "width": 1920,
        "height": 1080,
        "sample_aspect_ratio": "1/1",
    }
    output_geometry: dict[str, JsonValue] = {
        "width": 1920 * scale,
        "height": 1080 * scale,
        "sample_aspect_ratio": "1/1",
    }
    final_rate = common_rate * 2
    target = canonical_publication_target(
        request.publication,
        mr_mode=binding.mr_mode,
        final_frame_rate=final_rate,
        height=cast(int, output_geometry["height"]),
    )
    protected_paths = list(dict.fromkeys(str(source.source_artifact.path) for source in sources))
    for protected in protected_paths:
        protected_path = Path(protected)
        if target == protected_path.resolve(strict=False) or (
            target.exists() and protected_path.exists() and target.samefile(protected_path)
        ):
            raise Av27TemplateError("E_AV27_NAMING_SOURCE", "成品目标不得覆盖源媒体或其硬链接。")

    prep_nodes = list(current.project.graph.nodes)
    prep_edges = list(current.project.graph.edges)
    split_parameters: dict[str, JsonValue] = {
        "source_mode": binding.source_mode,
        "leaf_duration_minutes": request.leaf_duration_minutes,
        "planned_admission_artifact_id": binding.admission_artifact.artifact_id,
        "planned_effective_video_artifact_ids": [
            source.effective_artifact.artifact_id for source in sources
        ],
        "chapters": [
            {
                "chapter_id": chapter.chapter_id,
                "chapter_ordinal": chapter.chapter_ordinal,
                "label": chapter.label,
                "source_ordinal": chapter.source_ordinal,
                "start_frame": chapter.start_frame,
                "end_frame": chapter.end_frame,
            }
            for chapter in chapters
        ],
        "segments": [
            {
                "port_id": leaf.port_id,
                "source_ordinal": leaf.source_ordinal,
                "planned_effective_video_artifact_id": sources[
                    leaf.source_ordinal
                ].effective_artifact.artifact_id,
                "chapter_id": leaf.chapter_id,
                "chapter_ordinal": leaf.chapter_ordinal,
                "leaf_id": leaf.leaf_id,
                "leaf_ordinal": leaf.leaf_ordinal,
                "start_frame": leaf.start_frame,
                "end_frame": leaf.end_frame,
            }
            for leaf in leaves
        ],
    }
    if request.chapter_selector is not None:
        split_parameters["chapter_selector"] = cast(
            JsonValue,
            request.chapter_selector.model_dump(mode="json", exclude_none=True),
        )
    split_node = NodeInstance(
        node_id="split.atomic",
        type_id=split_definition.type_id,
        definition_version=split_definition.version,
        parameters=split_parameters,
        ui_position=UiPosition(x=930, y=80 + (len(sources) - 1) * 90),
    )
    nodes: list[NodeInstance] = [*prep_nodes, split_node]
    edges: list[Edge] = [*prep_edges]
    for source in sources:
        edges.append(
            Edge(
                source_node_id=source.mr_node_id or source.node_id,
                source_port_id="video",
                target_node_id=split_node.node_id,
                target_port_id="videos",
                ordinal=source.ordinal,
            )
        )
    edges.append(
        Edge(
            source_node_id=binding.admission_node_id,
            source_port_id="gate",
            target_node_id=split_node.node_id,
            target_port_id="gate",
        )
    )

    enhancement_nodes: dict[int, NodeInstance] = {}
    for chapter in chapters:
        chapter_leaves = [leaf for leaf in leaves if leaf.chapter_id == chapter.chapter_id]
        for local_ordinal, leaf in enumerate(chapter_leaves):
            chapter_label = excel_chapter_label(chapter.chapter_ordinal)
            node = NodeInstance(
                node_id=f"enhance.{chapter_label}.{local_ordinal + 1:03d}",
                type_id=ENHANCEMENT_TYPE_ID,
                definition_version=enhancement_def.version,
                parameters={
                    **_stage_parameters(request.enhancement),
                    "actual_scale_factor": scale,
                    "expected_input_geometry": input_geometry,
                    "expected_output_geometry": output_geometry,
                    "expected_frames": leaf.frame_count,
                    "expected_fps": _fraction_text(common_rate),
                    "chapter_id": chapter.chapter_id,
                    "chapter_ordinal": chapter.chapter_ordinal,
                    "leaf_id": leaf.leaf_id,
                    "leaf_ordinal": leaf.leaf_ordinal,
                },
                ui_position=UiPosition(
                    x=1240,
                    y=80 + leaf.leaf_ordinal * 150,
                ),
            )
            enhancement_nodes[leaf.leaf_ordinal] = node
            nodes.append(node)
            edges.append(
                Edge(
                    source_node_id=split_node.node_id,
                    source_port_id=leaf.port_id,
                    target_node_id=node.node_id,
                    target_port_id="video",
                )
            )

    fi_nodes: list[NodeInstance] = []
    for chapter in chapters:
        chapter_label = excel_chapter_label(chapter.chapter_ordinal)
        chapter_leaves = [leaf for leaf in leaves if leaf.chapter_id == chapter.chapter_id]
        merge = NodeInstance(
            node_id=f"merge.{chapter_label}",
            type_id=MERGE_VIDEO_TYPE_ID,
            definition_version=merge_def.version,
            parameters={
                **_stage_parameters(request.enhancement),
                "chapter_id": chapter.chapter_id,
                "chapter_ordinal": chapter.chapter_ordinal,
                "expected_frames": chapter.frame_count,
                "expected_fps": _fraction_text(common_rate),
                "expected_geometry": output_geometry,
                "actual_scale_factor": scale,
            },
            ui_position=UiPosition(x=1540, y=80 + chapter.chapter_ordinal * 220),
        )
        fi = NodeInstance(
            node_id=f"fi.{chapter_label}",
            type_id=FRAME_INTERPOLATION_TYPE_ID,
            definition_version=fi_def.version,
            parameters={
                **_stage_parameters(request.frame_interpolation),
                "chapter_id": chapter.chapter_id,
                "chapter_ordinal": chapter.chapter_ordinal,
                "expected_input_frames": chapter.frame_count,
                "expected_output_frames": chapter.frame_count * 2 - 1,
                "source_fps": _fraction_text(common_rate),
                "expected_geometry": output_geometry,
                "expected_signal": common_signal,
            },
            ui_position=UiPosition(x=1810, y=80 + chapter.chapter_ordinal * 220),
        )
        nodes.extend((merge, fi))
        fi_nodes.append(fi)
        for local_ordinal, leaf in enumerate(chapter_leaves):
            edges.append(
                Edge(
                    source_node_id=enhancement_nodes[leaf.leaf_ordinal].node_id,
                    source_port_id="video",
                    target_node_id=merge.node_id,
                    target_port_id="videos",
                    ordinal=local_ordinal,
                )
            )
        edges.append(
            Edge(
                source_node_id=merge.node_id,
                source_port_id="video",
                target_node_id=fi.node_id,
                target_port_id="video",
            )
        )

    program = NodeInstance(
        node_id="program",
        type_id=PROGRAM_ENCODE_TYPE_ID,
        definition_version=program_def.version,
        parameters={
            "encoder": request.program_encode.encoder,
            "source_fps": _fraction_text(common_rate),
            "chapters": [
                {
                    "chapter_id": chapter.chapter_id,
                    "chapter_ordinal": chapter.chapter_ordinal,
                    "source_frames": chapter.frame_count,
                    "expected_fi_frames": chapter.frame_count * 2 - 1,
                    "encoded_frames": chapter.frame_count * 2,
                }
                for chapter in chapters
            ],
            "expected_geometry": output_geometry,
            "expected_signal": common_signal,
        },
        ui_position=UiPosition(x=2080, y=80 + (len(chapters) - 1) * 110),
    )
    final = NodeInstance(
        node_id="final",
        type_id=FINAL_MUX_TYPE_ID,
        definition_version=final_def.version,
        parameters={
            "source_mode": binding.source_mode,
            "sources": [
                {
                    "source_ordinal": source.ordinal,
                    "source_frames": metadata_frame_count(source.source_artifact.media_info),
                    "source_fps": _fraction_text(metadata_rate(source.source_artifact.media_info)),
                }
                for source in sources
            ],
            "expected_program_frames": sum(chapter.frame_count * 2 for chapter in chapters),
            "expected_geometry": output_geometry,
            "expected_signal": common_signal,
            "mr_mode": binding.mr_mode,
        },
        ui_position=UiPosition(x=2350, y=80 + (len(chapters) - 1) * 110),
    )
    output = NodeInstance(
        node_id="output",
        type_id=output_def.type_id,
        definition_version=output_def.version,
        parameters={
            "mode": "copy",
            "target_path": str(target),
            "overwrite": request.publication.overwrite,
            "output_root": str(Path(request.publication.output_root).resolve(strict=True)),
            "create_parent": request.publication.layout == "title_subdirectory",
            "protected_paths": cast(list[JsonValue], protected_paths),
        },
        ui_position=UiPosition(x=2620, y=80 + (len(chapters) - 1) * 110),
    )
    nodes.extend((program, final, output))
    for chapter, fi in zip(chapters, fi_nodes, strict=True):
        edges.append(
            Edge(
                source_node_id=fi.node_id,
                source_port_id="video",
                target_node_id=program.node_id,
                target_port_id="chapters",
                ordinal=chapter.chapter_ordinal,
            )
        )
    edges.extend(
        (
            Edge(
                source_node_id=program.node_id,
                source_port_id="video",
                target_node_id=final.node_id,
                target_port_id="video",
            ),
            Edge(
                source_node_id=binding.admission_node_id,
                source_port_id="gate",
                target_node_id=final.node_id,
                target_port_id="gate",
            ),
            Edge(
                source_node_id=final.node_id,
                source_port_id="media",
                target_node_id=output.node_id,
                target_port_id="in",
            ),
        )
    )
    for source in sources:
        edges.append(
            Edge(
                source_node_id=source.node_id,
                source_port_id="source_media",
                target_node_id=final.node_id,
                target_port_id="sources",
                ordinal=source.ordinal,
            )
        )

    definitions = (
        source_program_definition(),
        source_admission_definition(),
        *((mosaic_restoration_definition(),) if binding.mr_mode == "external" else ()),
        split_definition,
        enhancement_def,
        merge_def,
        fi_def,
        program_def,
        final_def,
        output_def,
    )
    graph = Graph(nodes=tuple(nodes), edges=tuple(edges))
    GraphValidator(definitions).validate(graph)
    project = Project(
        project_id=current.project.project_id,
        name=current.project.name,
        graph=graph,
    )
    return TemplateBuild(
        phase="expanded",
        project=project,
        definitions=definitions,
        plan=TemplatePlanSummary(
            source_count=len(sources),
            chapter_count=len(chapters),
            leaf_count=len(leaves),
            mr_mode=binding.mr_mode,
            preparation_run_id=binding.preparation_run_id,
            effective_video_artifact_ids=tuple(
                source.effective_artifact.artifact_id for source in sources
            ),
            chapters=chapter_plan_projection(
                chapters,
                leaves,
                source_rates={source.ordinal: source.frame_rate for source in sources},
            ),
            manual_stages=(
                ManualStageSummary(
                    stage="enhancement",
                    node_count=len(leaves),
                    output_container=".mov",
                ),
                ManualStageSummary(
                    stage="frame_interpolation",
                    node_count=len(chapters),
                    output_container=".mov",
                ),
            ),
            output_target_path=str(target),
            output_directory_to_create=str(target.parent) if not target.parent.exists() else None,
        ),
    )


def _source_facts(binding: PreparationBinding) -> tuple[_SourceFacts, ...]:
    values: list[_SourceFacts] = []
    for item in binding.sources:
        source_namespace = namespace_from_media_info(item.source_media_artifact.media_info)
        effective_namespace = namespace_from_media_info(item.effective_video_artifact.media_info)
        source_ordinal = source_namespace.get("source_ordinal")
        if isinstance(source_ordinal, bool) or source_ordinal != item.source_ordinal:
            raise Av27TemplateError(
                "E_AV27_EXPAND_SOURCE_METADATA",
                "Source metadata ordinal 不匹配",
            )
        source_count = metadata_frame_count(item.source_media_artifact.media_info)
        effective_count = metadata_frame_count(item.effective_video_artifact.media_info)
        source_rate = metadata_rate(item.source_media_artifact.media_info)
        effective_rate = metadata_rate(item.effective_video_artifact.media_info)
        if source_count != effective_count or source_rate != effective_rate:
            raise Av27TemplateError(
                "E_AV27_EXPAND_EFFECTIVE_VIDEO",
                "effective video 与 Source N/FPS 不闭合",
            )
        source_geometry = _metadata_geometry(source_namespace)
        effective_geometry = _metadata_geometry(effective_namespace)
        source_signal = _metadata_signal(source_namespace)
        effective_signal = _metadata_signal(effective_namespace)
        if source_geometry != effective_geometry or source_signal != effective_signal:
            raise Av27TemplateError(
                "E_AV27_EXPAND_EFFECTIVE_VIDEO",
                "effective video 与 Source geometry/signal 不闭合",
            )
        values.append(
            _SourceFacts(
                ordinal=item.source_ordinal,
                node_id=item.source_node_id,
                source_artifact=item.source_media_artifact,
                effective_artifact=item.effective_video_artifact,
                frame_count=effective_count,
                frame_rate=effective_rate,
                geometry=effective_geometry,
                signal=effective_signal,
                chapter_label=item.chapter_label,
                mr_node_id=item.mr_node_id,
            )
        )
    return tuple(values)


def _metadata_geometry(namespace: Mapping[str, object]) -> tuple[int, int]:
    value = namespace.get("geometry")
    if not isinstance(value, Mapping) or set(value) != {"width", "height"}:
        raise Av27TemplateError(
            "E_AV27_EXPAND_EFFECTIVE_METADATA",
            "video metadata geometry 字段集合无效",
        )
    width = value.get("width")
    height = value.get("height")
    if (
        isinstance(width, bool)
        or not isinstance(width, int)
        or width <= 0
        or isinstance(height, bool)
        or not isinstance(height, int)
        or height <= 0
    ):
        raise Av27TemplateError(
            "E_AV27_EXPAND_EFFECTIVE_METADATA",
            "video metadata geometry 必须是正整数",
        )
    return width, height


def _metadata_signal(namespace: Mapping[str, object]) -> dict[str, JsonValue]:
    value = namespace.get("signal")
    if not isinstance(value, Mapping) or set(value) != set(_SIGNAL):
        raise Av27TemplateError(
            "E_AV27_EXPAND_EFFECTIVE_METADATA",
            "video metadata signal 字段集合无效",
        )
    normalized = {str(key): cast(JsonValue, item) for key, item in value.items()}
    if normalized != _SIGNAL:
        raise Av27TemplateError(
            "E_AV27_EXPAND_EFFECTIVE_METADATA",
            "video metadata signal 必须是冻结的 BT.709 limited progressive 形状",
        )
    return normalized


def _stage_parameters(stage: StageDeclaration) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {"model_name": stage.model_name}
    if stage.model_version is not None:
        result["model_version"] = stage.model_version
    return result


def _fraction_text(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"


def _round_half_up(value: Fraction) -> int:
    return (value.numerator * 2 + value.denominator) // (value.denominator * 2)


def _is_reparse_or_symlink(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
    return bool(attributes & reparse_flag)


__all__ = [
    "AV27_PROFILE_VERSION",
    "Av27TemplateError",
    "Av27TemplateModel",
    "ChapterPlanProjection",
    "ChapterSelector",
    "DerivedLeafPlan",
    "EnhancementDeclaration",
    "ExactFramesChapterSelector",
    "ExactTimesChapterSelector",
    "ExpandRequest",
    "ExpandTemplatePreviewRequest",
    "FrameInterpolationDeclaration",
    "LeafPlanProjection",
    "ManualStageSummary",
    "MrDeclaration",
    "MrExternal",
    "MrOff",
    "PreparationBinding",
    "PreparationSourceBinding",
    "PrepareRequest",
    "PrepareTemplatePreviewRequest",
    "ProgramEncodeDeclaration",
    "PublicationRequest",
    "ResolvedChapterPlan",
    "SingleChapterSelector",
    "SourceSpec",
    "TemplateBuild",
    "TemplatePhase",
    "TemplatePlanSummary",
    "TemplatePreviewRequest",
    "build_expanded",
    "build_preparation",
    "canonical_publication_target",
    "chapter_plan_projection",
    "derive_leaf_plan",
    "excel_chapter_label",
    "format_timecode",
    "parse_canonical_time",
    "publication_directory",
    "resolve_chapter_plan",
    "validate_prepare_paths",
]
