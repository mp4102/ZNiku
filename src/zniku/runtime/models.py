"""定义 ZNIKU 0.2.0 Runtime 的最小持久化领域模型。

本模块只表达普通 Run snapshot、NodeRun attempt、外部 handoff、Artifact 与 NodeResult；不实现
Scheduler、进程执行、媒体 I/O、checkpoint、resume、Evidence、digest 或 ExecutionPlan。所有模型拒绝
未知字段并冻结嵌套 JSON，避免读取后的运行历史被调用方原地改写。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePath
from typing import Annotated, Any, Never, Self, cast
from uuid import UUID, uuid4

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

from zniku.graph import Graph, GraphValidator, NodeDefinition

Identifier = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    ),
]
ExactVersion = Annotated[
    str,
    StringConstraints(
        min_length=5,
        max_length=96,
        pattern=(
            r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
            r"(?:-(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
            r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?"
            r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
        ),
    ),
]
ArtifactKind = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z][A-Za-z0-9]*(?:[._:/-][A-Za-z0-9]+)*$",
    ),
]
ExternalPath = Annotated[str, StringConstraints(min_length=1, max_length=32767)]
type JsonObject = dict[str, JsonValue]


def _validate_random_id(value: str) -> str:
    """把持久身份限制为 canonical UUIDv4，明确排除内容摘要身份。"""

    try:
        parsed = UUID(value)
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("E_RUNTIME_ID_INVALID: identity 必须是 UUIDv4") from error
    if parsed.version != 4:
        raise ValueError("E_RUNTIME_ID_NOT_RANDOM: identity 必须是 UUIDv4，不得使用 digest")
    return str(parsed)


RandomId = Annotated[
    str,
    StringConstraints(min_length=36, max_length=36),
    AfterValidator(_validate_random_id),
]


def new_runtime_id() -> str:
    """生成一个不承载内容身份的随机 Runtime ID。"""

    return str(uuid4())


def utc_now() -> datetime:
    """返回便于注入替换的当前 UTC 时间。"""

    return datetime.now(UTC)


def _normalize_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


UtcTimestamp = Annotated[AwareDatetime, AfterValidator(_normalize_utc)]


class FrozenJsonDict(dict[str, Any]):
    """保留普通 JSON object 序列化语义，同时拒绝构造后原地修改。"""

    @staticmethod
    def _reject_mutation() -> Never:
        raise TypeError("Runtime JSON 值不可原地修改")

    def __setitem__(self, key: str, value: Any) -> None:
        del key, value
        self._reject_mutation()

    def __delitem__(self, key: str) -> None:
        del key
        self._reject_mutation()

    def clear(self) -> None:
        self._reject_mutation()

    def pop(self, key: str, default: Any = None) -> Any:
        del key, default
        self._reject_mutation()

    def popitem(self) -> tuple[str, Any]:
        self._reject_mutation()

    def setdefault(self, key: str, default: Any = None) -> Any:
        del key, default
        self._reject_mutation()

    def update(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self._reject_mutation()

    def __ior__(self, other: Any) -> Self:  # type: ignore[override,misc]
        del other
        self._reject_mutation()


class FrozenJsonList(list[Any]):
    """保留普通 JSON array 序列化语义，同时拒绝构造后原地修改。"""

    @staticmethod
    def _reject_mutation() -> Never:
        raise TypeError("Runtime JSON 值不可原地修改")

    def __setitem__(self, key: Any, value: Any) -> None:
        del key, value
        self._reject_mutation()

    def __delitem__(self, key: Any) -> None:
        del key
        self._reject_mutation()

    def append(self, value: Any) -> None:
        del value
        self._reject_mutation()

    def clear(self) -> None:
        self._reject_mutation()

    def extend(self, values: Any) -> None:
        del values
        self._reject_mutation()

    def insert(self, index: Any, value: Any) -> None:
        del index, value
        self._reject_mutation()

    def pop(self, index: Any = -1) -> Any:
        del index
        self._reject_mutation()

    def remove(self, value: Any) -> None:
        del value
        self._reject_mutation()

    def reverse(self) -> None:
        self._reject_mutation()

    def sort(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self._reject_mutation()

    def __iadd__(self, values: Any) -> Self:  # type: ignore[misc]
        del values
        self._reject_mutation()

    def __imul__(self, count: Any) -> Self:  # type: ignore[misc]
        del count
        self._reject_mutation()


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return FrozenJsonDict({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return FrozenJsonList(_freeze_json(item) for item in value)
    return value


def _normalize_tuple(value: Any) -> Any:
    return tuple(value) if isinstance(value, list) else value


def _validate_external_path(value: str) -> str:
    if not value.strip():
        raise ValueError("E_RUNTIME_PATH_BLANK: path 不得只包含空白")
    if "\x00" in value:
        raise ValueError("E_RUNTIME_PATH_NUL: path 不得包含 NUL")
    return str(PurePath(value))


class RuntimeModel(BaseModel):
    """Runtime 值对象共同的严格、冻结配置。"""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        revalidate_instances="never",
        strict=True,
        validate_default=True,
    )

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        """重验所有 update，避免 Pydantic 默认 model_copy 绕过状态不变量。"""

        del deep
        data = self.model_dump(mode="python", round_trip=True)
        if update:
            data.update(update)
        return type(self).model_validate(data)


class FrameRange(RuntimeModel):
    """表示首尾半开 ``[start_frame, end_frame)`` 帧区间。"""

    start_frame: Annotated[int, Field(ge=0)]
    end_frame: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def validate_order(self) -> FrameRange:
        if self.end_frame <= self.start_frame:
            raise ValueError("E_FRAME_RANGE_INVALID: end_frame 必须大于 start_frame")
        return self


class Artifact(RuntimeModel):
    """记录一个已通过节点轻量验收的外置文件。

    ``artifact_id`` 只允许随机 UUIDv4；路径、size 与 mtime 是本地索引和 dirty hint，不是内容摘要。
    """

    artifact_id: RandomId
    kind: ArtifactKind
    path: ExternalPath
    producer_node_run_id: RandomId
    producer_port_id: Identifier
    ordinal: Annotated[int, Field(ge=0)] | None = None
    frame_range: FrameRange | None = None
    media_info: JsonObject = Field(default_factory=dict)
    size: Annotated[int, Field(ge=0)] | None = None
    mtime_ns: Annotated[int, Field(ge=0)] | None = None

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _validate_external_path(value)

    @field_validator("media_info")
    @classmethod
    def freeze_media_info(cls, value: JsonObject) -> JsonObject:
        return cast(JsonObject, _freeze_json(value))


class ExternalOutputTarget(RuntimeModel):
    """声明 manual_external handoff 的一个预期输出位置。"""

    port_id: Identifier
    path: ExternalPath
    ordinal: Annotated[int, Field(ge=0)] | None = None

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _validate_external_path(value)


class ExternalHandoff(RuntimeModel):
    """持久化人工外部流程的输入身份、目标路径与操作提示。

    handoff 只表示等待操作者提交文件，不保存外部工具内部进度，因此可安全跨应用重启保留。
    """

    handoff_id: RandomId
    node_run_id: RandomId
    input_artifact_ids: tuple[RandomId, ...] = ()
    output_targets: tuple[ExternalOutputTarget, ...] = ()
    instructions: Annotated[str, StringConstraints(max_length=4096)] | None = None
    created_at: UtcTimestamp

    @field_validator("input_artifact_ids", "output_targets", mode="before")
    @classmethod
    def normalize_sequences(cls, value: Any) -> Any:
        return _normalize_tuple(value)

    @model_validator(mode="after")
    def validate_unique_bindings(self) -> ExternalHandoff:
        output_keys = tuple((item.port_id, item.ordinal) for item in self.output_targets)
        if len(output_keys) != len(set(output_keys)):
            raise ValueError("E_HANDOFF_OUTPUT_DUPLICATE: output port/ordinal 不得重复")
        return self


class NodeRunState(StrEnum):
    """NodeRun 唯一允许持久化的五种状态。"""

    PENDING = "pending"
    RUNNING = "running"
    WAITING_EXTERNAL = "waiting_external"
    COMPLETED = "completed"
    FAILED = "failed"


class RunState(StrEnum):
    """Run 聚合的最小持久状态。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class FailureReason(StrEnum):
    """失败原因决定展示与从头重跑行为，不创建恢复协议。"""

    EXECUTION_ERROR = "execution_error"
    VALIDATION_FAILED = "validation_failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"
    EXTERNAL_SUBMISSION_INVALID = "external_submission_invalid"


class RuntimeFailure(RuntimeModel):
    """保存失败类别和适合操作者阅读的简短信息。"""

    reason: FailureReason
    message: Annotated[str, StringConstraints(min_length=1, max_length=4096)]


class NodeRun(RuntimeModel):
    """记录一个节点在某个 Run 内不可恢复的一次完整 attempt。"""

    node_run_id: RandomId
    run_id: RandomId
    node_id: Identifier
    definition_version: ExactVersion
    attempt: Annotated[int, Field(ge=1)]
    state: NodeRunState
    input_artifact_ids: tuple[RandomId, ...] = ()
    output_artifact_ids: tuple[RandomId, ...] = ()
    created_at: UtcTimestamp
    work_dir: ExternalPath
    started_at: UtcTimestamp | None = None
    ended_at: UtcTimestamp | None = None
    progress: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    exit_code: int | None = None
    log_path: ExternalPath | None = None
    error: RuntimeFailure | None = None
    reused_from_result_id: RandomId | None = None
    external_handoff: ExternalHandoff | None = None

    @field_validator("input_artifact_ids", "output_artifact_ids", mode="before")
    @classmethod
    def normalize_artifact_ids(cls, value: Any) -> Any:
        return _normalize_tuple(value)

    @field_validator("work_dir", "log_path")
    @classmethod
    def validate_paths(cls, value: str | None) -> str | None:
        return None if value is None else _validate_external_path(value)

    @model_validator(mode="after")
    def validate_state_fields(self) -> NodeRun:
        if len(self.output_artifact_ids) != len(set(self.output_artifact_ids)):
            raise ValueError("E_NODE_RUN_OUTPUT_DUPLICATE: output Artifact ID 不得重复")
        if (
            self.external_handoff is not None
            and self.external_handoff.node_run_id != self.node_run_id
        ):
            raise ValueError("E_HANDOFF_NODE_RUN_MISMATCH: handoff 没有绑定当前 NodeRun")
        if self.external_handoff is not None and (
            self.external_handoff.created_at < self.created_at
            or (self.started_at is not None and self.external_handoff.created_at < self.started_at)
        ):
            raise ValueError("E_HANDOFF_TIME_INVALID: handoff created_at 不得早于 attempt 启动")
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("E_NODE_RUN_STARTED_AT_INVALID: started_at 不得早于 created_at")
        if self.ended_at is not None and (
            self.started_at is None or self.ended_at < self.started_at
        ):
            raise ValueError("E_NODE_RUN_ENDED_AT_INVALID: ended_at 不得早于 started_at")

        if self.state is NodeRunState.PENDING:
            if (
                any(
                    value is not None
                    for value in (
                        self.started_at,
                        self.ended_at,
                        self.progress,
                        self.exit_code,
                        self.log_path,
                        self.error,
                        self.reused_from_result_id,
                        self.external_handoff,
                    )
                )
                or self.output_artifact_ids
            ):
                raise ValueError("E_NODE_RUN_PENDING_FIELDS: pending 不得携带执行或结果字段")
        elif self.state is NodeRunState.RUNNING:
            if (
                self.started_at is None
                or any(
                    value is not None
                    for value in (
                        self.ended_at,
                        self.exit_code,
                        self.error,
                        self.reused_from_result_id,
                        self.external_handoff,
                    )
                )
                or self.output_artifact_ids
            ):
                raise ValueError("E_NODE_RUN_RUNNING_FIELDS: running 字段组合无效")
        elif self.state is NodeRunState.WAITING_EXTERNAL:
            if (
                self.started_at is None
                or self.external_handoff is None
                or any(
                    value is not None
                    for value in (
                        self.ended_at,
                        self.exit_code,
                        self.error,
                        self.reused_from_result_id,
                    )
                )
                or self.output_artifact_ids
            ):
                raise ValueError(
                    "E_NODE_RUN_WAITING_FIELDS: waiting_external 必须且只能携带有效 handoff"
                )
        elif self.state is NodeRunState.COMPLETED:
            if self.started_at is None or self.ended_at is None or self.error is not None:
                raise ValueError("E_NODE_RUN_COMPLETED_FIELDS: completed 时间或 error 字段无效")
            if self.progress is not None and self.progress != 1.0:
                raise ValueError("E_NODE_RUN_COMPLETED_PROGRESS: completed progress 必须为 1")
        elif self.state is NodeRunState.FAILED and (
            self.started_at is None
            or self.ended_at is None
            or self.error is None
            or self.output_artifact_ids
            or self.reused_from_result_id is not None
        ):
            raise ValueError("E_NODE_RUN_FAILED_FIELDS: failed 字段组合无效")
        return self

    @classmethod
    def pending(
        cls,
        *,
        run_id: str,
        node_id: str,
        definition_version: str,
        attempt: int,
        input_artifact_ids: tuple[str, ...],
        work_dir: str,
        node_run_id: str | None = None,
        created_at: datetime | None = None,
    ) -> NodeRun:
        """构造尚未被 Scheduler 启动的新 attempt。"""

        return cls(
            node_run_id=node_run_id or new_runtime_id(),
            run_id=run_id,
            node_id=node_id,
            definition_version=definition_version,
            attempt=attempt,
            state=NodeRunState.PENDING,
            input_artifact_ids=input_artifact_ids,
            created_at=created_at or utc_now(),
            work_dir=work_dir,
        )


class NodeResult(RuntimeModel):
    """一次成功 attempt 的普通输出集合和轻量校验摘要。"""

    result_id: RandomId
    node_run_id: RandomId
    outputs: tuple[Artifact, ...] = ()
    media_summary: JsonObject = Field(default_factory=dict)
    validation_summary: JsonObject = Field(default_factory=dict)
    created_at: UtcTimestamp

    @field_validator("outputs", mode="before")
    @classmethod
    def normalize_outputs(cls, value: Any) -> Any:
        return _normalize_tuple(value)

    @field_validator("media_summary", "validation_summary")
    @classmethod
    def freeze_summaries(cls, value: JsonObject) -> JsonObject:
        return cast(JsonObject, _freeze_json(value))

    @model_validator(mode="after")
    def validate_outputs(self) -> NodeResult:
        artifact_ids = tuple(item.artifact_id for item in self.outputs)
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("E_RESULT_ARTIFACT_DUPLICATE: output Artifact ID 不得重复")
        if any(item.producer_node_run_id != self.node_run_id for item in self.outputs):
            raise ValueError("E_RESULT_PRODUCER_MISMATCH: Artifact producer 必须是当前 NodeRun")
        return self


class StaleReason(StrEnum):
    """当前 latest result 失效的最小原因集合。"""

    GRAPH_CHANGED = "graph_changed"
    UPSTREAM_CHANGED = "upstream_changed"
    OUTPUT_MISSING = "output_missing"
    QUICK_PROBE_FAILED = "quick_probe_failed"
    RERUN_REQUESTED = "rerun_requested"


class LatestNodeResult(RuntimeModel):
    """把 Project 当前 NodeInstance 映射到最新结果及独立 stale 标志。"""

    node_id: Identifier
    result_id: RandomId
    stale: bool
    stale_reason: StaleReason | None = None
    updated_at: UtcTimestamp

    @model_validator(mode="after")
    def validate_stale_reason(self) -> LatestNodeResult:
        if self.stale != (self.stale_reason is not None):
            raise ValueError("E_LATEST_STALE_REASON: stale 与 stale_reason 必须同时出现或同时缺省")
        return self


class Run(RuntimeModel):
    """保存启动瞬间的普通 Graph/NodeDefinition snapshot 与 NodeRun 历史。"""

    run_id: RandomId
    project_id: Identifier
    graph_snapshot: Graph
    definitions_snapshot: tuple[NodeDefinition, ...]
    selected_targets: tuple[Identifier, ...] = ()
    state: RunState
    node_runs: tuple[NodeRun, ...] = ()
    created_at: UtcTimestamp
    started_at: UtcTimestamp | None = None
    ended_at: UtcTimestamp | None = None
    error: RuntimeFailure | None = None

    @field_validator("definitions_snapshot", "selected_targets", mode="before")
    @classmethod
    def normalize_sequences(cls, value: Any) -> Any:
        return _normalize_tuple(value)

    @model_validator(mode="after")
    def validate_snapshot_and_state(self) -> Run:
        definition_keys = tuple((item.type_id, item.version) for item in self.definitions_snapshot)
        if len(definition_keys) != len(set(definition_keys)):
            raise ValueError("E_RUN_DEFINITION_DUPLICATE: snapshot definition 必须唯一")
        GraphValidator(self.definitions_snapshot).validate(self.graph_snapshot)
        node_versions = {
            item.node_id: item.definition_version for item in self.graph_snapshot.nodes
        }
        if len(self.selected_targets) != len(set(self.selected_targets)):
            raise ValueError("E_RUN_TARGET_DUPLICATE: selected target 不得重复")
        if any(target not in node_versions for target in self.selected_targets):
            raise ValueError("E_RUN_TARGET_UNKNOWN: selected target 必须属于 graph snapshot")
        attempts: set[tuple[str, int]] = set()
        for node_run in self.node_runs:
            if node_run.run_id != self.run_id:
                raise ValueError("E_RUN_NODE_RUN_ID_MISMATCH: NodeRun 没有绑定当前 Run")
            if node_versions.get(node_run.node_id) != node_run.definition_version:
                raise ValueError("E_RUN_NODE_BINDING_MISMATCH: NodeRun definition binding 无效")
            if node_run.created_at < self.created_at:
                raise ValueError("E_RUN_NODE_RUN_TIME_INVALID: NodeRun 不得早于 Run 创建")
            key = (node_run.node_id, node_run.attempt)
            if key in attempts:
                raise ValueError("E_RUN_ATTEMPT_DUPLICATE: node attempt 不得重复")
            attempts.add(key)

        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("E_RUN_STARTED_AT_INVALID: started_at 不得早于 created_at")
        if self.ended_at is not None and (
            self.started_at is None or self.ended_at < self.started_at
        ):
            raise ValueError("E_RUN_ENDED_AT_INVALID: ended_at 不得早于 started_at")
        if self.state is RunState.PENDING:
            if self.started_at is not None or self.ended_at is not None or self.error is not None:
                raise ValueError("E_RUN_PENDING_FIELDS: pending Run 字段组合无效")
        elif self.state is RunState.RUNNING:
            if self.started_at is None or self.ended_at is not None or self.error is not None:
                raise ValueError("E_RUN_RUNNING_FIELDS: running Run 字段组合无效")
        elif self.state is RunState.COMPLETED:
            if self.started_at is None or self.ended_at is None or self.error is not None:
                raise ValueError("E_RUN_COMPLETED_FIELDS: completed Run 字段组合无效")
        elif self.state is RunState.FAILED and (
            self.started_at is None or self.ended_at is None or self.error is None
        ):
            raise ValueError("E_RUN_FAILED_FIELDS: failed Run 字段组合无效")
        return self

    @classmethod
    def pending(
        cls,
        *,
        project_id: str,
        graph_snapshot: Graph,
        definitions_snapshot: tuple[NodeDefinition, ...],
        selected_targets: tuple[str, ...] = (),
        run_id: str | None = None,
        created_at: datetime | None = None,
    ) -> Run:
        """从当前 Project 内容构造一个不含 Freeze/ExecutionPlan/digest 的普通 Run snapshot。"""

        return cls(
            run_id=run_id or new_runtime_id(),
            project_id=project_id,
            graph_snapshot=graph_snapshot,
            definitions_snapshot=definitions_snapshot,
            selected_targets=selected_targets,
            state=RunState.PENDING,
            created_at=created_at or utc_now(),
        )
