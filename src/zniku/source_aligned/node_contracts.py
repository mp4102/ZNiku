"""源对齐 0.3.3 节点的局部来源合同与执行前检查，不引入全局图语义或读取隐藏索引。

全部结论仅来自参数及直接 RunnerInput。新 namespace 与旧 AV27 隔离；外部 Aion 版本和
相位只是待实测声明，不是从媒体文件推断的事实。任何来源、顺序、范围或媒体合同冲突均失败关闭。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from fractions import Fraction
from typing import Annotated, Any, Literal, Never, Self, cast

from pydantic import (
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from zniku.avenhance_v27 import validators as legacy
from zniku.avenhance_v27.probe import Av27MediaError, namespace_from_media_info
from zniku.chapter_overlap.context import _context_geometry
from zniku.chapter_overlap.models import (
    ChapterLeafPlan,
    ChapterModel,
    ExactSeconds,
    Ordinal,
    PositiveInt,
)
from zniku.runtime import RunnerInput
from zniku.runtime.models import RandomId

OVERLAP_NAMESPACE = "zniku.source.aligned"
OVERLAP_NODE_VERSION = "0.3.3"
_ARTIFACT_ID = TypeAdapter(RandomId)
ATOMIC_SPLIT_TYPE_PREFIX = "zniku.overlap.split.leaves."
ROLE_TYPES = {
    "enhancement": "zniku.overlap.enhancement.external",
    "merge": "zniku.overlap.merge_video",
    "context": "zniku.overlap.fi_context",
    "fi": "zniku.overlap.frame_interpolation.external",
    "crop": "zniku.overlap.fi_crop",
    "program": "zniku.overlap.program_encode",
    "final": "zniku.overlap.final_mux",
}
type Role = Literal["split", "enhancement", "merge", "context", "fi", "crop", "program", "final"]
type Label = Annotated[str, StringConstraints(min_length=1, max_length=256)]


def fail(code: str, message: str) -> Never:
    """保留稳定扩展错误码，供 adapter 与 validator 共用失败语义。"""
    raise Av27MediaError(f"E_SOURCE_ALIGNED_{code}", message)


class SourceExpectation(ChapterModel):
    """设计时仅绑定已完成原片分析；不能预填未来外部结果的 UUID。"""

    original_video_artifact_id: RandomId
    admission_artifact_id: RandomId
    source_media_artifact_id: RandomId
    frame_count: PositiveInt
    frame_rate: ExactSeconds

    @model_validator(mode="after")
    def positive_rate(self) -> Self:
        if Fraction(self.frame_rate) <= 0:
            fail("SOURCE_RATE", "源 FPS 必须为正数")
        return self


class SourceBinding(SourceExpectation):
    """执行时由实际直接输入形成的有效视频绑定，不写回图参数或 Run snapshot。"""

    effective_video_artifact_id: RandomId

    def expectation(self) -> SourceExpectation:
        return SourceExpectation.model_validate(
            self.model_dump(exclude={"effective_video_artifact_id"})
        )


class ChapterBinding(ChapterModel):
    """章的源区间和稳定身份；count 是本计划章数，不是 Core scope。"""

    chapter_id: Annotated[str, StringConstraints(pattern=r"^chapter-[0-9]{4}$")]
    ordinal: Ordinal
    count: Annotated[int, Field(ge=1, le=1000)]
    start_frame: Ordinal
    end_frame: PositiveInt

    @model_validator(mode="after")
    def geometry(self) -> Self:
        if (
            self.ordinal >= self.count
            or self.chapter_id != f"chapter-{self.ordinal + 1:04d}"
            or self.end_frame <= self.start_frame
            or (self.ordinal == 0) != (self.start_frame == 0)
        ):
            fail("CHAPTER", "章身份、序号或非空源范围不一致")
        return self


class LeafBinding(ChapterModel):
    """全局 leaf 身份与章内连续序号分开保存，避免跨章重名被当作同一产物。"""

    leaf_id: Annotated[str, StringConstraints(pattern=r"^leaf-[0-9]{4,5}$")]
    global_ordinal: Annotated[int, Field(ge=0, lt=10000)]
    ordinal: Ordinal
    count: Annotated[int, Field(ge=1, le=10000)]
    start_frame: Ordinal
    end_frame: PositiveInt

    @model_validator(mode="after")
    def geometry(self) -> Self:
        if (
            self.ordinal >= self.count
            or self.leaf_id != f"leaf-{self.global_ordinal + 1:04d}"
            or self.end_frame <= self.start_frame
        ):
            fail("LEAF", "叶身份、序号或非空源范围不一致")
        return self


class Geometry(ChapterModel):
    """规范方形像素几何；不允许隐式缩放或 SAR 更改。"""

    width: Annotated[int, Field(ge=2, le=16384)]
    height: Annotated[int, Field(ge=2, le=16384)]
    sample_aspect_ratio: Literal["1/1"] = "1/1"


class Signal(ChapterModel):
    """继承已验收 BT.709 limited、逐行、零旋转媒体合同。"""

    color_primaries: Literal["bt709"] = "bt709"
    color_transfer: Literal["bt709"] = "bt709"
    color_space: Literal["bt709"] = "bt709"
    color_range: Literal["tv"] = "tv"
    chroma_location: Literal["left"] = "left"
    field_order: Literal["progressive"] = "progressive"
    rotation: Literal[0] = 0

    @field_validator("rotation", mode="before")
    @classmethod
    def strict_rotation(cls, value: Any) -> Any:
        if type(value) is not int:
            fail("SIGNAL", "rotation 必须为严格整数")
        return value


class CandidateFiProfile(ChapterModel):
    """可配置工程默认，不声称 32 帧或 minM=2 是 Aion 实测能力。"""

    software_version: Literal["v1.0"] = "v1.0"
    model_name: Literal["Aion"] = "Aion"
    status: Literal["pending_real_acceptance"] = "pending_real_acceptance"
    phase: Literal["even-input-2m-minus-1"] = "even-input-2m-minus-1"
    left_context_frames: Annotated[int, Field(ge=0, le=240)] = 32
    right_context_frames: Annotated[int, Field(ge=1, le=240)] = 32
    minimum_input_frames: Annotated[int, Field(ge=2, le=256)] = 2


class EnhancementDeclaration(ChapterModel):
    """增强软件声明与整数缩放，仅表示操作者声明。"""

    model_name: Label
    model_version: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None
    actual_scale_factor: PositiveInt


class ContextPart(ChapterModel):
    """上下文中的直接增强章交集；源和局部偏移可以相互重算。"""

    artifact_id: RandomId
    chapter: ChapterBinding
    start_frame: Ordinal
    end_frame: PositiveInt


class ContextBinding(ChapterModel):
    """精确上下文、raw、crop 与全局半帧责任；必须在整体 metadata 中重算。"""

    context_start_frame: Ordinal
    context_end_frame: PositiveInt
    input_frame_count: PositiveInt
    raw_fi_frame_count: PositiveInt
    crop_start_frame: Ordinal
    crop_end_frame: PositiveInt
    cropped_frame_count: PositiveInt
    global_start_half_frame: Ordinal
    global_end_half_frame: PositiveInt
    parts: Annotated[tuple[ContextPart, ...], Field(min_length=1, max_length=1000)]

    @field_validator("parts", mode="before")
    @classmethod
    def arrays(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


def context_binding(
    source: SourceExpectation,
    chapter: ChapterBinding,
    profile: CandidateFiProfile,
    parts: tuple[ContextPart, ...],
) -> ContextBinding:
    """重算裁边几何并检查所有直接邻章交集，缺少上下文不 clone 或扩宽。"""
    g = _context_geometry(
        source.frame_count,
        chapter.start_frame,
        chapter.end_frame,
        profile.left_context_frames,
        profile.right_context_frames,
    )
    if g.input_count < profile.minimum_input_frames:
        fail("INPUT_TOO_SHORT", "现有真实上下文不足 minimum_input_frames")
    cursor = g.context_start
    previous: ChapterBinding | None = None
    seen: set[str] = set()
    found = False
    for part in parts:
        c = part.chapter
        validate_chapter(source, c)
        if part.artifact_id in seen or c.count != chapter.count:
            fail("CONTEXT_INPUTS", "上下文来源重复或章数不符")
        seen.add(part.artifact_id)
        if previous is not None and (
            c.ordinal != previous.ordinal + 1 or c.start_frame != previous.end_frame
        ):
            fail("CONTEXT_INPUTS", "上下文来源章必须有序相邻完整")
        if c.ordinal == chapter.ordinal:
            if c != chapter:
                fail("CONTEXT_INPUTS", "正式章来源范围不匹配")
            found = True
        start, end = max(c.start_frame, g.context_start), min(c.end_frame, g.context_end)
        if start != cursor or end <= start or (part.start_frame, part.end_frame) != (start, end):
            fail("CONTEXT_OFFSETS", "上下文交集缺失、重叠或等长错位")
        cursor, previous = end, c
    if not found or cursor != g.context_end:
        fail("CONTEXT_INPUTS", "上下文必须覆盖正式章和全部真实邻章范围")
    return ContextBinding(
        context_start_frame=g.context_start,
        context_end_frame=g.context_end,
        input_frame_count=g.input_count,
        raw_fi_frame_count=g.raw_count,
        crop_start_frame=g.crop_start,
        crop_end_frame=g.crop_end,
        cropped_frame_count=g.crop_end - g.crop_start,
        global_start_half_frame=g.global_start,
        global_end_half_frame=g.global_end,
        parts=parts,
    )


def validate_chapter(source: SourceExpectation, chapter: ChapterBinding) -> None:
    if chapter.end_frame > source.frame_count or (
        (chapter.ordinal == chapter.count - 1) != (chapter.end_frame == source.frame_count)
    ):
        fail("CHAPTER_SOURCE", "章范围或最后章身份与有效源不符")


class OverlapMetadata(ChapterModel):
    """只有节点 validator 通过后才登记的新局部 namespace；旧 namespace 不等价。"""

    producer_type_id: str
    producer_version: Literal["0.3.3"] = "0.3.3"
    role: Role
    source: SourceBinding
    chapter: ChapterBinding | None = None
    leaf: LeafBinding | None = None
    context: ContextBinding | None = None
    fi_profile: CandidateFiProfile | None = None
    enhancement: EnhancementDeclaration | None = None
    frame_count: PositiveInt
    frame_rate: ExactSeconds
    geometry: Geometry
    signal: Signal

    @model_validator(mode="after")
    def all_bindings(self) -> Self:
        if self.role == "split":
            suffix = self.producer_type_id.removeprefix(ATOMIC_SPLIT_TYPE_PREFIX)
            if not suffix.isascii() or not suffix.isdecimal() or str(int(suffix)) != suffix:
                fail("PRODUCER", "不是规范的新 Split type identity")
            if (
                not self.producer_type_id.startswith(ATOMIC_SPLIT_TYPE_PREFIX)
                or not 1 <= int(suffix) <= 10000
            ):
                fail("PRODUCER", "Split producer shape 超界")
        elif self.producer_type_id != ROLE_TYPES[self.role]:
            fail("PRODUCER", "producer type/version 与新节点职责不匹配")
        chapter_roles = {"split", "enhancement", "merge", "context", "fi", "crop"}
        if (self.chapter is not None) != (self.role in chapter_roles):
            fail("METADATA_SHAPE", "chapter 字段与阶段不符")
        if (self.leaf is not None) != (self.role in {"split", "enhancement"}):
            fail("METADATA_SHAPE", "leaf 字段与阶段不符")
        if (self.context is not None) != (self.role in {"context", "fi", "crop"}):
            fail("METADATA_SHAPE", "context 字段与阶段不符")
        if (self.fi_profile is not None) != (
            self.role in {"context", "fi", "crop", "program", "final"}
        ):
            fail("METADATA_SHAPE", "FI profile 字段与阶段不符")
        if (self.enhancement is not None) != (
            self.role in {"enhancement", "merge", "context", "fi", "crop", "program", "final"}
        ):
            fail("METADATA_SHAPE", "增强声明与阶段不符")
        expected = 2 * self.source.frame_count
        rate = Fraction(self.source.frame_rate)
        if self.chapter is not None:
            validate_chapter(self.source, self.chapter)
            expected = self.chapter.end_frame - self.chapter.start_frame
        if self.leaf is not None and self.chapter is not None:
            current_leaf, current_chapter = self.leaf, self.chapter
            if (
                not current_chapter.start_frame
                <= current_leaf.start_frame
                < current_leaf.end_frame
                <= current_chapter.end_frame
                or (
                    (current_leaf.ordinal == 0)
                    != (current_leaf.start_frame == current_chapter.start_frame)
                    or (current_leaf.ordinal == current_leaf.count - 1)
                    != (current_leaf.end_frame == current_chapter.end_frame)
                )
            ):
                fail("LEAF_RANGE", "叶必须在本章内且首尾身份一致")
            expected = current_leaf.end_frame - current_leaf.start_frame
            if self.role == "split" and current_leaf.global_ordinal >= int(
                self.producer_type_id.removeprefix(ATOMIC_SPLIT_TYPE_PREFIX)
            ):
                fail("PRODUCER", "Split leaf identity 超出其声明 output shape")
        if self.context is not None and self.chapter is not None and self.fi_profile is not None:
            rebuilt = context_binding(
                self.source, self.chapter, self.fi_profile, self.context.parts
            )
            if self.context != rebuilt:
                fail("CONTEXT_GEOMETRY", "context/crop 投影与源坐标重算不符")
            expected = {
                "context": rebuilt.input_frame_count,
                "fi": rebuilt.raw_fi_frame_count,
                "crop": rebuilt.cropped_frame_count,
            }[self.role]
        if self.role in {"fi", "crop", "program", "final"}:
            rate *= 2
        if self.frame_count != expected or Fraction(self.frame_rate) != rate:
            fail("TIMELINE", "阶段帧数或 exact FPS 不符合源与责任区间")
        return self


class SplitParameters(ChapterModel):
    """一次全源 Split 的严格 planner 投影以及直接准入/原音轨绑定。"""

    plan: ChapterLeafPlan
    source: SourceExpectation

    @model_validator(mode="after")
    def original_plan(self) -> Self:
        if (
            self.plan.source.artifact_id != self.source.original_video_artifact_id
            or self.plan.source.frame_count != self.source.frame_count
            or self.plan.source.frame_rate != self.source.frame_rate
        ):
            fail("PLAN_SOURCE", "分章计划必须绑定原片分析，不得绑定未来修复结果")
        return self


class ChapterParameters(ChapterModel):
    """普通章节点的局部源与正式范围，不承载上下游隐藏路径。"""

    source: SourceExpectation
    chapter: ChapterBinding

    @model_validator(mode="after")
    def bound(self) -> Self:
        validate_chapter(self.source, self.chapter)
        return self


class EnhancementParameters(ChapterParameters):
    """一个完整叶的人工增强声明与受控整数缩放；不得由文件名推断。"""

    leaf: LeafBinding
    expected_input_geometry: Geometry
    expected_output_geometry: Geometry
    model_name: Label
    model_version: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None
    actual_scale_factor: PositiveInt | None = None


class FiParameters(ChapterParameters):
    """上下文、外部 FI 和裁边共用同一候选 profile，变更必须使下游失效。"""

    fi_profile: CandidateFiProfile = Field(default_factory=CandidateFiProfile)


class ProgramParameters(ChapterModel):
    """完整裁后章的单次连续编码，仅全局末尾补一帧。"""

    source: SourceExpectation
    chapter_count: Annotated[int, Field(ge=1, le=1000)]
    encoder: Literal["cpu", "gpu"]


class FinalParameters(ChapterModel):
    """Program 与原始音轨的绑定；不改变章节或帧率。"""

    source: SourceExpectation
    mr_mode: Literal["off", "external"]


PARAMETER_MODELS: dict[str, type[ChapterModel]] = {
    "split": SplitParameters,
    "enhancement": EnhancementParameters,
    "merge": ChapterParameters,
    "context": FiParameters,
    "fi": FiParameters,
    "crop": FiParameters,
    "program": ProgramParameters,
    "final": FinalParameters,
}


@dataclass(frozen=True, slots=True)
class OutputContract:
    port_id: str
    metadata: OverlapMetadata


@dataclass(frozen=True, slots=True)
class NodeContract:
    """执行前推导的局部结果，不是可持久化 ExecutionPlan 或媒体已成功的证明。"""

    params: ChapterModel
    source: SourceBinding
    outputs: tuple[OutputContract, ...]
    input_metadata: tuple[OverlapMetadata, ...] = ()


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    return value


def read_metadata(
    item: RunnerInput,
    *,
    metadata_model: type[OverlapMetadata] = OverlapMetadata,
    namespace: str = OVERLAP_NAMESPACE,
) -> OverlapMetadata:
    """严格重验新 namespace，不把 AV27 媒体类型或字段相似视为兼容。"""
    try:
        result = metadata_model.model_validate(_plain(item.media_info.get(namespace)))
    except ValidationError as exc:
        raise Av27MediaError("E_SOURCE_ALIGNED_METADATA", "缺少或非法的新节点 metadata") from exc
    expected_port = result.leaf.leaf_id if result.role == "split" and result.leaf else "video"
    if item.producer_port_id != expected_port or item.kind != "VideoFile":
        fail("INPUT_PRODUCER", "直接输入 output port/kind 与新 producer 不匹配")
    return result


def _inputs(
    inputs: tuple[RunnerInput, ...], port: str, *, many: bool, kind: str = "VideoFile"
) -> tuple[RunnerInput, ...]:
    values = tuple(item for item in inputs if item.port_id == port)
    if not values or (not many and len(values) != 1):
        fail("INPUT_COUNT", f"{port} 输入数量不符")
    if len({item.artifact_id for item in values}) != len(values):
        fail("INPUT_DUPLICATE", f"{port} 不允许同一 Artifact 重复输入")
    for ordinal, item in enumerate(values):
        try:
            _ARTIFACT_ID.validate_python(item.artifact_id, strict=True)
        except ValidationError as exc:
            raise Av27MediaError(
                "E_SOURCE_ALIGNED_INPUT_ID", "直接输入缺少 canonical Artifact UUID"
            ) from exc
        if (
            item.kind != kind
            or item.ordinal != (ordinal if many else None)
            or (many and type(item.ordinal) is not int)
        ):
            fail("INPUT_ORDER", f"{port} 类型或连续 ordinal 不符")
    return values


def _uniform(metadata: tuple[OverlapMetadata, ...]) -> None:
    first = metadata[0]
    if any(
        (m.source, m.frame_rate, m.geometry, m.signal, m.enhancement, m.fi_profile)
        != (
            first.source,
            first.frame_rate,
            first.geometry,
            first.signal,
            first.enhancement,
            first.fi_profile,
        )
        for m in metadata[1:]
    ):
        fail("INPUT_CONTRACT", "输入来源、FPS、几何、signal 或外部声明不统一")


def _check_admission(gate: RunnerInput, source: SourceExpectation) -> None:
    """只读取直接 gate 的有界 JSON，防止同长异源音轨被错误绑定；不检索其他 Artifact。"""

    def unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                fail("ADMISSION", "准入 JSON 含重复字段")
            result[key] = value
        return result

    try:
        with gate.path.open("rb") as stream:
            payload = stream.read(1048577)
        if len(payload) > 1048576:
            fail("ADMISSION", "单源准入 JSON 超过 1 MiB 读取预算")
        document = json.loads(
            payload,
            object_pairs_hook=unique_keys,
            parse_constant=lambda value: fail("ADMISSION", f"准入不允许 {value}"),
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise Av27MediaError("E_SOURCE_ALIGNED_ADMISSION", "无法读取严格准入 JSON") from exc
    if (
        not isinstance(document, dict)
        or set(document) != {"schema", "source_mode", "sources"}
        or document.get("schema") != "zniku.avenhance.v27.admission/1"
        or document.get("source_mode") != "program"
    ):
        fail("ADMISSION", "新节点要求既有单源 program 准入")
    rows = document.get("sources")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        fail("ADMISSION", "准入必须只有一个 Source")
    row = rows[0]
    if (
        set(row)
        != {
            "source_ordinal",
            "artifact_id",
            "frame_count",
            "frame_rate",
            "geometry",
            "signal",
            "audio_tracks",
        }
        or type(row.get("source_ordinal")) is not int
        or row["source_ordinal"] != 0
        or type(row.get("frame_count")) is not int
        or (row.get("artifact_id"), row.get("frame_count"), row.get("frame_rate"))
        != (source.source_media_artifact_id, source.frame_count, source.frame_rate)
    ):
        fail("ADMISSION_BINDING", "准入原始 Source 身份/N/FPS 不匹配")


def _metadata(
    role: Role,
    source: SourceBinding,
    *,
    count: int,
    geometry: Geometry,
    signal: Signal,
    chapter: ChapterBinding | None = None,
    leaf: LeafBinding | None = None,
    context: ContextBinding | None = None,
    fi_profile: CandidateFiProfile | None = None,
    enhancement: EnhancementDeclaration | None = None,
    split_count: int = 0,
    metadata_model: type[OverlapMetadata] = OverlapMetadata,
) -> OverlapMetadata:
    rate = Fraction(source.frame_rate) * (2 if role in {"fi", "crop", "program", "final"} else 1)
    return metadata_model(
        producer_type_id=f"{ATOMIC_SPLIT_TYPE_PREFIX}{split_count}"
        if role == "split"
        else ROLE_TYPES[role],
        role=role,
        source=source,
        chapter=chapter,
        leaf=leaf,
        context=context,
        fi_profile=fi_profile,
        enhancement=enhancement,
        frame_count=count,
        frame_rate=f"{rate.numerator}/{rate.denominator}",
        geometry=geometry,
        signal=signal,
    )


def preflight(
    role: str,
    inputs: tuple[RunnerInput, ...],
    parameters: Mapping[str, object],
    *,
    metadata_model: type[OverlapMetadata] = OverlapMetadata,
    namespace: str = OVERLAP_NAMESPACE,
    effective_reader: Callable[[RunnerInput, SourceExpectation], legacy._MediaContract]
    | None = None,
) -> NodeContract:
    """在媒体 I/O 前失败关闭；adapter 和完成 validator 都调用，不信任 producer 自报绑定。"""
    if role not in PARAMETER_MODELS:
        fail("ROLE", "未知节点职责")
    try:
        params = PARAMETER_MODELS[role].model_validate(_plain(parameters))
    except ValidationError as exc:
        raise Av27MediaError("E_SOURCE_ALIGNED_PARAMETERS", "节点参数不满足严格合同") from exc
    expected_ports = {
        "split": {"videos", "gate"},
        "merge": {"videos"},
        "context": {"chapters"},
        "program": {"chapters"},
        "final": {"video", "sources", "gate"},
    }.get(role, {"video"})
    if {item.port_id for item in inputs} != expected_ports:
        fail("INPUT_PORTS", "直接 input port 集合不符")
    if isinstance(params, SplitParameters):
        videos = _inputs(inputs, "videos", many=True)
        gate = _inputs(inputs, "gate", many=False, kind="DataFile")[0]
        if (
            len(videos) != 1
            or gate.producer_port_id != "gate"
            or gate.artifact_id != params.source.admission_artifact_id
        ):
            fail("SOURCE_BINDING", "Split 必须绑定一个原片或源对齐修复输入及原片准入")
        video = videos[0]
        old = (effective_reader or effective_contract)(video, params.source)
        source = SourceBinding(
            **params.source.model_dump(), effective_video_artifact_id=video.artifact_id
        )
        _check_admission(gate, params.source)
        signal = Signal.model_validate(dict(old.signal))
        outputs: list[OutputContract] = []
        for planned_chapter in params.plan.chapters:
            chapter_binding_value = ChapterBinding(
                chapter_id=planned_chapter.chapter_id,
                ordinal=planned_chapter.ordinal,
                count=params.plan.chapter_count,
                start_frame=planned_chapter.start_frame,
                end_frame=planned_chapter.end_frame,
            )
            for planned_leaf in planned_chapter.leaves:
                leaf_binding_value = LeafBinding(
                    leaf_id=planned_leaf.leaf_id,
                    global_ordinal=planned_leaf.global_ordinal,
                    ordinal=planned_leaf.ordinal,
                    count=len(planned_chapter.leaves),
                    start_frame=planned_leaf.start_frame,
                    end_frame=planned_leaf.end_frame,
                )
                outputs.append(
                    OutputContract(
                        planned_leaf.leaf_id,
                        _metadata(
                            "split",
                            source,
                            count=planned_leaf.frame_count,
                            geometry=Geometry(width=1920, height=1080),
                            signal=signal,
                            chapter=chapter_binding_value,
                            leaf=leaf_binding_value,
                            split_count=params.plan.leaf_count,
                            metadata_model=metadata_model,
                        ),
                    )
                )
        return NodeContract(params, source, tuple(outputs))
    port = (
        "videos" if role == "merge" else "chapters" if role in {"context", "program"} else "video"
    )
    items = _inputs(inputs, port, many=role in {"merge", "context", "program"})
    metadata = tuple(
        read_metadata(item, metadata_model=metadata_model, namespace=namespace) for item in items
    )
    _uniform(metadata)
    first = metadata[0]
    assert isinstance(params, ChapterParameters | ProgramParameters | FinalParameters)
    source = first.source
    if any(item.source.expectation() != params.source for item in metadata):
        fail("SOURCE_BINDING", "直接输入来自不同有效源或准入")
    wanted = {
        "enhancement": "split",
        "merge": "enhancement",
        "context": "merge",
        "fi": "context",
        "crop": "fi",
        "program": "crop",
        "final": "program",
    }[role]
    if any(m.role != wanted for m in metadata):
        fail("INPUT_STAGE", f"此节点必须直接消费新 {wanted} 产物")
    chapter = params.chapter if isinstance(params, ChapterParameters) else None
    if role not in {"context", "program", "final"} and any(m.chapter != chapter for m in metadata):
        fail("CHAPTER_BINDING", "直接输入章身份或范围不匹配")
    geometry, signal, declaration = first.geometry, first.signal, first.enhancement
    context, profile, leaf = first.context, first.fi_profile, first.leaf
    count = first.frame_count
    if isinstance(params, EnhancementParameters):
        if first.leaf != params.leaf or first.geometry != params.expected_input_geometry:
            fail("ENHANCEMENT_BINDING", "增强输入叶或几何与参数不符")
        scale = legacy._integer_scale(
            (first.geometry.width, first.geometry.height),
            (params.expected_output_geometry.width, params.expected_output_geometry.height),
        )
        if params.actual_scale_factor != scale and not (
            params.actual_scale_factor is None and scale == 1
        ):
            fail("ENHANCEMENT_SCALE", "增强整数倍缩放必须与显式声明一致")
        geometry = params.expected_output_geometry
        declaration = EnhancementDeclaration(
            model_name=params.model_name,
            model_version=params.model_version,
            actual_scale_factor=scale,
        )
    elif role == "merge":
        assert chapter is not None
        cursor, previous_global = chapter.start_frame, None
        for ordinal, m in enumerate(metadata):
            current_leaf = m.leaf
            if (
                current_leaf is None
                or current_leaf.ordinal != ordinal
                or current_leaf.count != len(metadata)
                or current_leaf.start_frame != cursor
                or (
                    previous_global is not None
                    and current_leaf.global_ordinal != previous_global + 1
                )
            ):
                fail("MERGE_COVERAGE", "Merge 必须按章内序号消费全部连续叶")
            cursor, previous_global = current_leaf.end_frame, current_leaf.global_ordinal
        if cursor != chapter.end_frame:
            fail("MERGE_COVERAGE", "Merge 叶没有完整覆盖本章")
        count, leaf = chapter.end_frame - chapter.start_frame, None
    elif isinstance(params, FiParameters):
        profile = params.fi_profile
        if role == "context":
            parts = tuple(
                ContextPart(
                    artifact_id=item.artifact_id,
                    chapter=m.chapter,
                    start_frame=max(
                        m.chapter.start_frame,
                        max(0, params.chapter.start_frame - profile.left_context_frames),
                    ),
                    end_frame=min(
                        m.chapter.end_frame,
                        min(
                            source.frame_count,
                            params.chapter.end_frame + profile.right_context_frames,
                        ),
                    ),
                )
                for item, m in zip(items, metadata, strict=True)
                if m.chapter is not None
            )
            context = context_binding(source, params.chapter, profile, parts)
        elif first.fi_profile != profile:
            fail("FI_PROFILE", "FI 配置与直接上游已登记候选 profile 不一致")
        assert context is not None
        count = {
            "context": context.input_frame_count,
            "fi": context.raw_fi_frame_count,
            "crop": context.cropped_frame_count,
        }[role]
    elif isinstance(params, ProgramParameters):
        cursor = 0
        if len(metadata) != params.chapter_count:
            fail("PROGRAM_COVERAGE", "Program 章数量不完整")
        for ordinal, m in enumerate(metadata):
            c, ctx = m.chapter, m.context
            if (
                c is None
                or ctx is None
                or c.ordinal != ordinal
                or c.count != params.chapter_count
                or ctx.global_start_half_frame != cursor
            ):
                fail("PROGRAM_COVERAGE", "Program 章重复、乱序或半帧责任缺口")
            cursor = ctx.global_end_half_frame
        if cursor != 2 * source.frame_count - 1:
            fail("PROGRAM_COVERAGE", "Program 必须精确覆盖 [0,2N-1)")
        count, context, leaf = 2 * source.frame_count, None, None
    elif isinstance(params, FinalParameters):
        if (params.mr_mode == "external") != (
            source.effective_video_artifact_id != source.original_video_artifact_id
        ):
            fail("FINAL_MR_MODE", "成片修复标识与实际有效输入来源不符")
        sources = _inputs(inputs, "sources", many=True, kind="MediaFile")
        gate = _inputs(inputs, "gate", many=False, kind="DataFile")[0]
        if (
            len(sources) != 1
            or sources[0].artifact_id != source.source_media_artifact_id
            or gate.artifact_id != source.admission_artifact_id
            or gate.producer_port_id != "gate"
            or sources[0].producer_port_id != "source_media"
        ):
            fail("FINAL_SOURCE", "Final 必须绑定原始 Source 音轨及同一准入 gate")
        _check_admission(gate, source)
        original = legacy._metadata_contract(sources[0])
        if original.frame_count != source.frame_count or original.frame_rate != Fraction(
            source.frame_rate
        ):
            fail("FINAL_SOURCE", "原始音轨 Source 时间轴与有效视频不符")
    result = _metadata(
        cast(Role, role),
        source,
        count=count,
        geometry=geometry,
        signal=signal,
        chapter=chapter,
        leaf=leaf,
        context=context,
        fi_profile=profile,
        enhancement=declaration,
        metadata_model=metadata_model,
    )
    return NodeContract(
        params, source, (OutputContract("media" if role == "final" else "video", result),), metadata
    )


type DeclaredContainer = Literal["mp4", "mov", "mkv"]
EXTERNAL_TYPE_PREFIX = "zniku.source_aligned.external."


class ExternalParameters(ChapterModel):
    """外部 N→N 修复；声明容器必须由对应 exact definition 的 const 约束。"""

    source: SourceExpectation
    declared_container: DeclaredContainer = "mp4"
    model_name: Label
    model_version: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None
    operator_frame_order_confirmed: Literal[True]

    @field_validator("operator_frame_order_confirmed", mode="before")
    @classmethod
    def strict_confirmation(cls, value: Any) -> Any:
        if value is not True:
            fail("OPERATOR_CONFIRMATION", "必须由操作者明确确认保持逐帧顺序")
        return value


class ExternalMetadata(ChapterModel):
    """仅表示节点验收的属性/时间轴及操作者声明，不证明逐帧内容或模型。"""

    producer_type_id: str
    producer_version: Literal["0.3.3"] = "0.3.3"
    role: Literal["external"] = "external"
    source: SourceExpectation
    declared_container: DeclaredContainer
    model_name: Label
    model_version: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None
    geometry: Geometry
    signal: Signal
    alignment: Literal["relative-presentation-cfr"] = "relative-presentation-cfr"
    content_correspondence: Literal["operator-declared-not-proven"] = "operator-declared-not-proven"

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.producer_type_id != EXTERNAL_TYPE_PREFIX + self.declared_container:
            fail("EXTERNAL_IDENTITY", "外部产物容器与精确生产者不匹配")
        return self


def effective_contract(item: RunnerInput, source: SourceExpectation) -> legacy._MediaContract:
    """只从直入身份及其严格 namespace 解析有效源；相同 N/FPS 不构成同源凭据。"""
    if item.kind != "VideoFile" or item.producer_port_id != "video":
        fail("SOURCE_BINDING", "有效输入必须来自声明的 video 端口")
    if item.artifact_id == source.original_video_artifact_id:
        namespace = namespace_from_media_info(item.media_info)
        if type(namespace.get("source_ordinal")) is not int or namespace["source_ordinal"] != 0:
            fail("SOURCE_STAGE", "原片输入必须为单 program Source")
        result = legacy._metadata_contract(item)
    else:
        try:
            metadata = ExternalMetadata.model_validate(
                _plain(item.media_info.get(OVERLAP_NAMESPACE))
            )
        except ValidationError as exc:
            raise Av27MediaError("E_SOURCE_ALIGNED_EXTERNAL", "不是源对齐修复产物") from exc
        if metadata.source != source:
            fail("SOURCE_BINDING", "修复产物绑定了其他原片或准入")
        result = legacy._MediaContract(
            source.frame_count,
            Fraction(source.frame_rate),
            (metadata.geometry.width, metadata.geometry.height),
            metadata.signal.model_dump(),
            float(Fraction(source.frame_count) / Fraction(source.frame_rate)),
        )
    if result.frame_count != source.frame_count or result.frame_rate != Fraction(source.frame_rate):
        fail("SOURCE_TIMELINE", "有效输入的 N/FPS 与原片不符")
    return result


def external_preflight(
    inputs: tuple[RunnerInput, ...], parameters: Mapping[str, object]
) -> tuple[ExternalParameters, RunnerInput, legacy._MediaContract]:
    """外部节点仅接明确原片与原准入；不接受旧 MR 或同长异源。"""
    params = ExternalParameters.model_validate(_plain(parameters))
    if {item.port_id for item in inputs} != {"video", "gate"}:
        fail("INPUT_PORTS", "外部修复必须直接连接原片和原准入")
    video = _inputs(inputs, "video", many=False)[0]
    gate = _inputs(inputs, "gate", many=False, kind="DataFile")[0]
    if (
        video.artifact_id != params.source.original_video_artifact_id
        or gate.artifact_id != params.source.admission_artifact_id
        or gate.producer_port_id != "gate"
    ):
        fail("SOURCE_BINDING", "外部修复原片/准入身份不匹配")
    expected = effective_contract(video, params.source)
    _check_admission(gate, params.source)
    return params, video, expected
