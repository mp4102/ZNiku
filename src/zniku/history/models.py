"""定义 ZNIKU 的 ZBaton vNext draft.2 开发期历史投影。

这些模型只验证 JSON 自洽性，不打开、探测或重新哈希媒体。正式 vNext Core/Profile、Schema 与 SDK
仍由 ZBatonProtocol-Media 发布；本模块不得被描述为生产协议实现。
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any, Literal, cast

from pydantic import Field, JsonValue, field_serializer, field_validator, model_validator

from zniku.contracts import AttributePath, ContractModel, JsonObject, StableId
from zniku.contracts.base import freeze_json_object, thaw_json
from zniku.contracts.errors import fail

ZBATON_DRAFT_VERSION: Literal["1.0.0-draft.2"] = "1.0.0-draft.2"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_FRAME_RATE_PATTERN = re.compile(r"^[1-9][0-9]*/[1-9][0-9]*$")
_DURATION_PATTERN = re.compile(r"^[0-9]+:[0-5][0-9]:[0-5][0-9]\.[0-9]{3}$")
_EFFECT_PATTERN = re.compile(r"^[a-z][a-z0-9-]*(?::[a-z][a-z0-9-]*)?$")


class HistoryProducer(ContractModel):
    """记录实际生成 draft 投影的软件身份。"""

    name: Literal["ZNIKU"]
    version: Literal["0.1.0"]


class HistorySubject(ContractModel):
    """面向阅读的作品信息，不参与媒体 identity。"""

    title: str = Field(min_length=1, max_length=240)
    release_year: int | None = Field(default=None, ge=1800, le=9999)


class ZBatonEnvelope(ContractModel):
    """开发期文档信封；精确 draft 版本禁止宽松路由。"""

    type: Literal["media-history"]
    version: Literal["1.0.0-draft.2"]
    id: StableId
    status: Literal["draft"]
    created_at: str
    producer: HistoryProducer
    subject: HistorySubject

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: str) -> str:
        if (
            re.fullmatch(
                r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})",
                value,
            )
            is None
        ):
            fail("E_ZBATON_TIME_FORMAT", "created_at 必须是带时区的 RFC 3339 时间")
        return value


class TimelineSnapshot(ContractModel):
    """节目时间轴的稳定可读摘要。"""

    duration: str

    @field_validator("duration")
    @classmethod
    def validate_duration(cls, value: str) -> str:
        if _DURATION_PATTERN.fullmatch(value) is None:
            fail("E_ZBATON_DURATION_FORMAT", "duration 必须使用 HH:MM:SS.mmm")
        return value


class VideoSnapshot(ContractModel):
    """首版确定帧率视频摘要。"""

    codec: str = Field(min_length=1, max_length=64)
    profile: str | None = Field(default=None, max_length=64)
    pixel_format: str = Field(min_length=1, max_length=64)
    bit_depth: int = Field(ge=1, le=32)
    width: int = Field(ge=1, le=65535)
    height: int = Field(ge=1, le=65535)
    sample_aspect_ratio: str = Field(pattern=r"^[1-9][0-9]*:[1-9][0-9]*$")
    frame_rate: str
    frame_count: int = Field(ge=1)
    scan: Literal["progressive", "interlaced", "unknown"]
    color: JsonObject = Field(default_factory=dict)

    @field_validator("frame_rate")
    @classmethod
    def validate_frame_rate(cls, value: str) -> str:
        if _FRAME_RATE_PATTERN.fullmatch(value) is None:
            fail("E_ZBATON_FRAME_RATE_FORMAT", "frame_rate 必须使用正整数有理数")
        numerator, denominator = (int(item) for item in value.split("/"))
        if math.gcd(numerator, denominator) != 1:
            fail("E_ZBATON_FRAME_RATE_REDUCED", "frame_rate 必须约分")
        return value

    @field_validator("color")
    @classmethod
    def freeze_color(cls, value: JsonObject) -> JsonObject:
        return freeze_json_object(value)

    @field_serializer("color")
    def serialize_color(self, value: JsonObject) -> JsonValue:
        return thaw_json(value)


class AudioTrackSnapshot(ContractModel):
    """使用稳定 track_id 的音轨摘要。"""

    track_id: StableId
    codec: str = Field(min_length=1, max_length=64)
    profile: str | None = Field(default=None, max_length=64)
    sample_rate: int = Field(ge=1, le=768000)
    channels: int = Field(ge=1, le=64)
    channel_layout: str = Field(min_length=1, max_length=128)
    language: str | None = Field(default=None, pattern=r"^[a-z]{3}$")
    default: bool


class MediaSnapshot(ContractModel):
    """current、final history 与 source 共用的统一媒体快照。"""

    media_id: StableId
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=0)
    sha256: str
    container: str = Field(min_length=1, max_length=64)
    timeline: TimelineSnapshot
    video: VideoSnapshot | None
    audio: tuple[AudioTrackSnapshot, ...]

    @field_validator("audio", mode="before")
    @classmethod
    def normalize_audio(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("filename")
    @classmethod
    def reject_path(cls, value: str) -> str:
        if "/" in value or "\\" in value or value in {".", ".."}:
            fail("E_ZBATON_FILENAME_PATH", "snapshot 只允许交付文件名，不允许路径")
        return value

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if _SHA256_PATTERN.fullmatch(value) is None:
            fail("E_ZBATON_SHA256_FORMAT", "snapshot sha256 必须为 64 位小写十六进制")
        return value

    @model_validator(mode="after")
    def validate_tracks(self) -> MediaSnapshot:
        track_ids = tuple(track.track_id for track in self.audio)
        if len(track_ids) != len(set(track_ids)):
            fail("E_ZBATON_TRACK_DUPLICATE", "同一 snapshot 的 track_id 不得重复")
        return self


class FinalMediaSnapshot(ContractModel):
    """一个 Workflow 的唯一 final checkpoint。"""

    workflow: StableId
    final_generation: int = Field(ge=1)
    snapshot: MediaSnapshot


class SourceMediaSnapshot(ContractModel):
    """处理图的一个根媒体快照。"""

    snapshot: MediaSnapshot


class ProcessingModel(ContractModel):
    """阶段实际使用的模型摘要；无模型阶段使用 null。"""

    name: str = Field(min_length=1, max_length=160)
    version: str | None = Field(default=None, max_length=128)


class ProcessingRecord(ContractModel):
    """不携带 Evidence 的通用媒体处理历史记录。"""

    record_id: StableId
    sequence: int = Field(ge=1)
    workflow: StableId
    final_generation: int = Field(ge=1)
    stage: str = Field(min_length=1, max_length=160)
    model: ProcessingModel | None
    inputs: tuple[StableId, ...]
    outputs: tuple[StableId, ...]
    effects: tuple[str, ...]
    changes: JsonObject = Field(default_factory=dict)
    preserved: tuple[AttributePath, ...] = ()

    @field_validator("inputs", "outputs", "effects", "preserved", mode="before")
    @classmethod
    def normalize_sequences(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("changes")
    @classmethod
    def validate_changes(cls, value: JsonObject) -> JsonObject:
        for path, change in value.items():
            if (
                not isinstance(path, str)
                or re.fullmatch(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$", path) is None
            ):
                fail("E_ZBATON_CHANGE_PATH", f"非法指标路径：{path}")
            if not isinstance(change, Mapping) or set(change) != {"from", "to"}:
                fail("E_ZBATON_CHANGE_SHAPE", "每个 change 必须恰好包含 from 与 to")
        return freeze_json_object(value)

    @field_serializer("changes")
    def serialize_changes(self, value: JsonObject) -> JsonValue:
        return thaw_json(value)

    @model_validator(mode="after")
    def validate_record(self) -> ProcessingRecord:
        if not self.inputs or not self.outputs:
            fail("E_ZBATON_RECORD_IO_EMPTY", "处理记录必须至少有一个 input 与 output")
        if len(self.inputs) != len(set(self.inputs)):
            fail("E_ZBATON_INPUT_DUPLICATE", "同一记录 input media_id 不得重复")
        if len(self.outputs) != len(set(self.outputs)):
            fail("E_ZBATON_OUTPUT_DUPLICATE", "同一记录 output media_id 不得重复")
        if set(self.inputs) & set(self.outputs):
            fail("E_ZBATON_NO_REPLACE", "处理记录不得原地复用 input media_id")
        if len(self.effects) != len(set(self.effects)):
            fail("E_ZBATON_EFFECT_DUPLICATE", "effects 不得重复")
        if any(_EFFECT_PATTERN.fullmatch(effect) is None for effect in self.effects):
            fail("E_ZBATON_EFFECT_FORMAT", "effect 必须是稳定 kebab-case 或命名空间 ID")
        changed = tuple(self.changes)
        for preserved in self.preserved:
            for path in changed:
                if (
                    path == preserved
                    or path.startswith(f"{preserved}.")
                    or preserved.startswith(f"{path}.")
                ):
                    fail("E_ZBATON_CHANGE_PRESERVED_CONFLICT", "changes 与 preserved 路径冲突")
        return self


class CompletedStageHistory(ContractModel):
    """投影前的已完成 Stage history authority；Evidence 字段不会进入 ZBaton。"""

    record_id: StableId
    workflow: StableId
    final_generation: int = Field(ge=1)
    stage: str = Field(min_length=1, max_length=160)
    model: ProcessingModel | None
    inputs: tuple[StableId, ...]
    outputs: tuple[StableId, ...]
    effects: tuple[str, ...]
    changes: JsonObject = Field(default_factory=dict)
    preserved: tuple[AttributePath, ...] = ()
    evidence_id: StableId
    verified: bool
    published: bool

    @field_validator("inputs", "outputs", "effects", "preserved", mode="before")
    @classmethod
    def normalize_sequences(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("changes")
    @classmethod
    def freeze_changes(cls, value: JsonObject) -> JsonObject:
        return freeze_json_object(value)

    @field_serializer("changes")
    def serialize_changes(self, value: JsonObject) -> JsonValue:
        return thaw_json(value)

    def to_record(self, sequence: int) -> ProcessingRecord:
        if not self.verified or not self.published:
            fail("E_ZBATON_STAGE_INCOMPLETE", "只有 verified 且 published 的 Stage 可进入历史")
        return ProcessingRecord(
            record_id=self.record_id,
            sequence=sequence,
            workflow=self.workflow,
            final_generation=self.final_generation,
            stage=self.stage,
            model=self.model,
            inputs=self.inputs,
            outputs=self.outputs,
            effects=self.effects,
            changes=cast(JsonObject, thaw_json(self.changes)),
            preserved=self.preserved,
        )


class ZBatonDocument(ContractModel):
    """ZBaton vNext draft.2 四区开发期投影与完整 DAG 一致性门。"""

    zbaton: ZBatonEnvelope
    current_media: FinalMediaSnapshot
    processing_history: tuple[ProcessingRecord, ...]
    final_history: tuple[FinalMediaSnapshot, ...]
    source_media: tuple[SourceMediaSnapshot, ...]

    @field_validator("processing_history", "final_history", "source_media", mode="before")
    @classmethod
    def normalize_sequences(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_document(self) -> ZBatonDocument:
        if not self.source_media:
            fail("E_ZBATON_SOURCE_EMPTY", "source_media 至少包含一个根媒体")
        record_ids = tuple(record.record_id for record in self.processing_history)
        sequences = tuple(record.sequence for record in self.processing_history)
        if len(record_ids) != len(set(record_ids)):
            fail("E_ZBATON_RECORD_DUPLICATE", "record_id 必须唯一")
        if sequences != tuple(range(1, len(sequences) + 1)):
            fail("E_ZBATON_SEQUENCE_ORDER", "sequence 必须按数组顺序从 1 连续递增")

        sources = tuple(item.snapshot.media_id for item in self.source_media)
        if len(sources) != len(set(sources)):
            fail("E_ZBATON_SOURCE_DUPLICATE", "source media_id 不得重复")
        available = set(sources)
        producer: dict[str, ProcessingRecord] = {}
        consumed: set[str] = set()
        for record in self.processing_history:
            unknown = set(record.inputs) - available
            if unknown:
                fail(
                    "E_ZBATON_INPUT_DANGLING",
                    "处理记录 input 必须引用 source 或此前 output：" + ", ".join(sorted(unknown)),
                )
            for output in record.outputs:
                if output in available:
                    fail("E_ZBATON_MEDIA_DUPLICATE", f"media_id 被重复生产：{output}")
                producer[output] = record
            consumed.update(record.inputs)
            available.update(record.outputs)

        current_id = self.current_media.snapshot.media_id
        if current_id not in producer:
            fail("E_ZBATON_CURRENT_NOT_PRODUCED", "current media 必须由处理记录产生")
        terminals = available - consumed
        if terminals != {current_id}:
            fail("E_ZBATON_CURRENT_NOT_UNIQUE_TERMINAL", "current media 必须是全图唯一终端")

        final_items = (*self.final_history, self.current_media)
        final_ids = tuple(item.snapshot.media_id for item in final_items)
        workflows = tuple(item.workflow for item in final_items)
        if len(final_ids) != len(set(final_ids)):
            fail("E_ZBATON_FINAL_DUPLICATE", "current 与 final_history 不得重复 final")
        if len(workflows) != len(set(workflows)):
            fail("E_ZBATON_WORKFLOW_FINAL_DUPLICATE", "每个 Workflow 只能对应一个 final")
        if any(media_id not in producer for media_id in final_ids):
            fail("E_ZBATON_FINAL_NOT_PRODUCED", "final snapshot 必须引用图中已生产媒体")
        generation_by_workflow = {item.workflow: item.final_generation for item in final_items}
        for record in self.processing_history:
            if generation_by_workflow.get(record.workflow) != record.final_generation:
                fail(
                    "E_ZBATON_FINAL_GENERATION_MISMATCH",
                    "record 与 Workflow final generation 不一致",
                )

        ancestor_records: set[str] = set()
        pending = [current_id]
        ancestor_media: set[str] = {current_id}
        while pending:
            media_id = pending.pop()
            ancestor_record = producer.get(media_id)
            if ancestor_record is None:
                continue
            if ancestor_record.record_id in ancestor_records:
                continue
            ancestor_records.add(ancestor_record.record_id)
            for input_id in ancestor_record.inputs:
                if input_id not in ancestor_media:
                    ancestor_media.add(input_id)
                    pending.append(input_id)
        if ancestor_records != set(record_ids):
            fail("E_ZBATON_UNRELATED_BRANCH", "processing_history 不得包含未通向 current 的旁支")
        if not set(final_ids) <= ancestor_media | {current_id}:
            fail("E_ZBATON_FINAL_NOT_ANCESTOR", "历史 final 必须位于 current 祖先路径")

        snapshots = (
            *(item.snapshot for item in self.source_media),
            *(item.snapshot for item in self.final_history),
            self.current_media.snapshot,
        )
        by_media: dict[str, MediaSnapshot] = {}
        for snapshot in snapshots:
            previous = by_media.get(snapshot.media_id)
            if previous is not None and previous != snapshot:
                fail("E_ZBATON_SNAPSHOT_ID_CONFLICT", "同一 media_id 不得对应不同 snapshot")
            by_media[snapshot.media_id] = snapshot
        return self


class ZBatonProjectionInput(ContractModel):
    """首个 Workflow 的 draft 投影输入。"""

    zbaton: ZBatonEnvelope
    current_media: FinalMediaSnapshot
    source_media: tuple[SourceMediaSnapshot, ...]
    stages: tuple[CompletedStageHistory, ...]

    @field_validator("source_media", "stages", mode="before")
    @classmethod
    def normalize_sequences(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


def project_zbaton(value: ZBatonProjectionInput) -> ZBatonDocument:
    """从已验证、已发布 Stage authority 确定生成首份 draft 历史。"""

    records = tuple(stage.to_record(index) for index, stage in enumerate(value.stages, 1))
    return ZBatonDocument(
        zbaton=value.zbaton,
        current_media=value.current_media,
        processing_history=records,
        final_history=(),
        source_media=value.source_media,
    )


def inherit_zbaton(
    parent: ZBatonDocument,
    *,
    zbaton: ZBatonEnvelope,
    current_media: FinalMediaSnapshot,
    stages: tuple[CompletedStageHistory, ...],
) -> ZBatonDocument:
    """保持父历史不可变并追加一个下游 Workflow 的 draft 投影。"""

    if current_media.workflow == parent.current_media.workflow:
        fail("E_ZBATON_DOWNSTREAM_WORKFLOW_REUSE", "下游 Workflow 必须使用新 identity")
    expected_generation = (
        max(item.final_generation for item in (*parent.final_history, parent.current_media)) + 1
    )
    if current_media.final_generation != expected_generation:
        fail("E_ZBATON_DOWNSTREAM_GENERATION", "线性下游 final_generation 必须递增一代")
    start = len(parent.processing_history) + 1
    records = parent.processing_history + tuple(
        stage.to_record(start + index) for index, stage in enumerate(stages)
    )
    return ZBatonDocument(
        zbaton=zbaton,
        current_media=current_media,
        processing_history=records,
        final_history=(*parent.final_history, parent.current_media),
        source_media=parent.source_media,
    )
