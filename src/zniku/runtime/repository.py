"""在 SQLite ``.zniku`` authority 中持久化 Runtime 历史与最新结果投影。

Repository 只负责事务、严格反序列化、状态迁移、attempt 历史、普通 snapshot 与 result/Artifact
原子登记。它不会计算 ready 节点、启动进程、执行媒体 I/O、验证 FFprobe、删除 attempt 目录或实现
checkpoint/resume。
遗留 ``running`` attempt 在启动恢复时失败为 ``interrupted``；``waiting_external`` 原样保留。
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from itertools import combinations, pairwise
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ValidationError

from zniku.graph import (
    CommandExecutorSpec,
    ExecutionMode,
    Graph,
    GraphValidationError,
    GraphValidator,
    ManualExternalExecutorSpec,
    NodeDefinition,
    PythonExecutorSpec,
)
from zniku.project import (
    PROJECT_APPLICATION_ID,
    PROJECT_SCHEMA_VERSION,
    ProjectSnapshot,
    ProjectStore,
)
from zniku.runtime.models import (
    Artifact,
    ExternalHandoff,
    FailureReason,
    LatestNodeResult,
    NodeResult,
    NodeRun,
    NodeRunState,
    Run,
    RunState,
    RuntimeFailure,
    StaleReason,
)

type _InterruptedProgress = tuple[str, int, float]


class RuntimeRepositoryError(RuntimeError):
    """Runtime repository 无法安全完成操作时的公共错误。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class RuntimeNotFoundError(RuntimeRepositoryError):
    """请求的 Run、NodeRun、Result 或 latest head 不存在。"""


class RuntimeConflictError(RuntimeRepositoryError):
    """身份、attempt、状态或事务前提与已提交历史冲突。"""


class RuntimeDataError(RuntimeRepositoryError):
    """SQLite 内容无法严格重建为 Runtime 模型。"""


def _validated_progress(value: object) -> float:
    """在所有 ``model_copy(update=...)`` 前显式守住 strict finite fraction 合同。"""

    if type(value) is not float or not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise RuntimeConflictError(
            "E_NODE_RUN_PROGRESS_INVALID",
            "progress 必须是位于 0.0..1.0 的有限 float",
        )
    return value


def _dump_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise RuntimeDataError("E_RUNTIME_JSON_INVALID", str(error)) from error


def _load_json(payload: str, *, context: str) -> Any:
    """严格解析数据库 JSON 字段，拒绝重复键和非标准常量。"""

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"对象键 {key!r} 重复")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"不允许 JSON 常量 {value}")

    try:
        return json.loads(
            payload,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise RuntimeDataError(
            "E_RUNTIME_DATA_CORRUPT", f"{context} 不是有效 JSON：{error}"
        ) from error


def _decode_model[ModelT: BaseModel](
    model: type[ModelT], payload: dict[str, Any], *, context: str
) -> ModelT:
    try:
        return model.model_validate_json(_dump_json(payload))
    except (ValidationError, ValueError, TypeError) as error:
        raise RuntimeDataError("E_RUNTIME_DATA_CORRUPT", f"{context}: {error}") from error


def _normalize_write_timestamp(value: datetime, *, context: str) -> datetime:
    """在触碰 SQLite 前拒绝 naive 时间，并统一转换为 UTC。"""

    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise RuntimeConflictError(
            "E_RUNTIME_TIMESTAMP_INVALID",
            f"{context} 必须是包含时区的 datetime",
        )
    return value.astimezone(UTC)


def _timestamp(value: datetime, *, context: str = "timestamp") -> str:
    normalized = _normalize_write_timestamp(value, context=context)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: str, *, context: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as error:
        raise RuntimeDataError(
            "E_RUNTIME_DATA_CORRUPT", f"{context} 不是有效 ISO 8601 时间"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RuntimeDataError("E_RUNTIME_DATA_CORRUPT", f"{context} 必须包含时区")
    return parsed.astimezone(UTC)


def _downstream_node_ids(graph: Graph, node_id: str) -> tuple[str, ...]:
    adjacency: dict[str, set[str]] = {node.node_id: set() for node in graph.nodes}
    for edge in graph.edges:
        adjacency.setdefault(edge.source_node_id, set()).add(edge.target_node_id)
    visited: set[str] = set()
    pending = list(adjacency.get(node_id, ()))
    while pending:
        candidate = pending.pop()
        if candidate in visited:
            continue
        visited.add(candidate)
        pending.extend(adjacency.get(candidate, ()))
    return tuple(node.node_id for node in graph.nodes if node.node_id in visited)


def _selected_node_ids(graph: Graph, selected_targets: tuple[str, ...]) -> tuple[str, ...]:
    """按 Graph 节点顺序返回整图或 selected target 的祖先闭包。"""

    if not selected_targets:
        return tuple(node.node_id for node in graph.nodes)
    predecessors: dict[str, set[str]] = {node.node_id: set() for node in graph.nodes}
    for edge in graph.edges:
        predecessors[edge.target_node_id].add(edge.source_node_id)
    included = set(selected_targets)
    pending = list(selected_targets)
    while pending:
        node_id = pending.pop()
        for predecessor in predecessors[node_id]:
            if predecessor not in included:
                included.add(predecessor)
                pending.append(predecessor)
    return tuple(node.node_id for node in graph.nodes if node.node_id in included)


def _rerun_node_ids(graph: Graph, selected: Sequence[str], node_id: str) -> tuple[str, ...]:
    """返回选中执行闭包内的 source + downstream，保持 Graph 节点顺序。"""

    included = {node_id, *_downstream_node_ids(graph, node_id)}
    selected_set = set(selected)
    return tuple(
        node.node_id
        for node in graph.nodes
        if node.node_id in included and node.node_id in selected_set
    )


def _incoming_signature(graph: Graph, node_id: str) -> tuple[tuple[str, str, str, int], ...]:
    """返回忽略 edge 展示顺序、保留 ordered_many ordinal 的直接入边语义。"""

    return tuple(
        sorted(
            (
                edge.source_node_id,
                edge.source_port_id,
                edge.target_port_id,
                -1 if edge.ordinal is None else edge.ordinal,
            )
            for edge in graph.edges
            if edge.target_node_id == node_id
        )
    )


def _ordered_incoming_edges(graph: Graph, node_id: str) -> tuple[Any, ...]:
    """使用 Runtime input binding 的稳定顺序返回直接入边。"""

    return tuple(
        sorted(
            (edge for edge in graph.edges if edge.target_node_id == node_id),
            key=lambda edge: (
                edge.target_port_id,
                -1 if edge.ordinal is None else edge.ordinal,
                edge.source_node_id,
                edge.source_port_id,
            ),
        )
    )


def _path_is_strictly_within(path: str, work_dir: str) -> bool:
    """仅做路径解析与 containment 判断，不访问或创建媒体文件。"""

    try:
        resolved_work_dir = Path(work_dir).resolve(strict=False)
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = resolved_work_dir / candidate
        resolved_candidate = candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return False
    return resolved_candidate != resolved_work_dir and resolved_candidate.is_relative_to(
        resolved_work_dir
    )


def _work_dir_identity(work_dir: str) -> str | None:
    """生成用于 attempt 隔离校验的 resolved/normcase identity，不创建目录。"""

    try:
        return os.path.normcase(str(Path(work_dir).resolve(strict=False)))
    except (OSError, RuntimeError, ValueError):
        return None


def _work_dirs_overlap(first: str, second: str) -> bool:
    """判断两个规范化 attempt 目录是否相同或互为祖先，防止删除边界交叉。"""

    first_path = Path(first)
    second_path = Path(second)
    return (
        first_path == second_path
        or first_path.is_relative_to(second_path)
        or second_path.is_relative_to(first_path)
    )


def _work_dir_collection_overlaps(identities: Sequence[str]) -> bool:
    """验证一组规范化 work_dir 两两互不重叠。"""

    return any(_work_dirs_overlap(first, second) for first, second in combinations(identities, 2))


def _node_snapshot_matches(
    *,
    source_graph: Graph,
    source_definitions: tuple[NodeDefinition, ...],
    target_graph: Graph,
    target_definitions: tuple[NodeDefinition, ...],
    node_id: str,
) -> bool:
    """比较会影响结果复用的 node、精确定义和直接入边；UI 坐标不参与。"""

    source_node = next((item for item in source_graph.nodes if item.node_id == node_id), None)
    target_node = next((item for item in target_graph.nodes if item.node_id == node_id), None)
    if source_node is None or target_node is None:
        return False
    if (
        source_node.type_id != target_node.type_id
        or source_node.definition_version != target_node.definition_version
        or source_node.parameters != target_node.parameters
        or _incoming_signature(source_graph, node_id) != _incoming_signature(target_graph, node_id)
    ):
        return False
    source_definition = next(
        (
            item
            for item in source_definitions
            if item.type_id == source_node.type_id
            and item.version == source_node.definition_version
        ),
        None,
    )
    target_definition = next(
        (
            item
            for item in target_definitions
            if item.type_id == target_node.type_id
            and item.version == target_node.definition_version
        ),
        None,
    )
    return source_definition is not None and source_definition == target_definition


class RuntimeRepository:
    """提供单个 Project SQLite 文件内的 Runtime 持久化操作。"""

    def __init__(self, store: ProjectStore) -> None:
        self._store = ProjectStore.open(store.path)

    @classmethod
    def open(cls, path: str | Path) -> RuntimeRepository:
        """打开或迁移一个受支持的 0.2.0 Project Store。"""

        return cls(ProjectStore.open(path))

    @property
    def project_store(self) -> ProjectStore:
        """返回共享同一 SQLite authority 的 Project Store。"""

        return self._store

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._store.path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            application_id = cast(int, connection.execute("PRAGMA application_id").fetchone()[0])
            schema_version = cast(int, connection.execute("PRAGMA user_version").fetchone()[0])
            if application_id != PROJECT_APPLICATION_ID or schema_version not in {
                2,
                PROJECT_SCHEMA_VERSION,
            }:
                raise RuntimeRepositoryError(
                    "E_RUNTIME_SCHEMA_MISMATCH",
                    f"Project schema identity 无效：{application_id}/{schema_version}",
                )
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _read_connection(self) -> Iterator[sqlite3.Connection]:
        """为一次公共聚合读取固定 SQLite snapshot，避免并发提交造成 torn view。"""

        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                yield connection
            finally:
                if connection.in_transaction:
                    connection.rollback()

    def start_run(
        self,
        run: Run,
        node_runs: Sequence[NodeRun],
        *,
        started_at: datetime,
        expected_storage_revision: int | None = None,
        rerun_from_node_id: str | None = None,
    ) -> Run:
        """原子建立 running Run 与完整选中闭包的 attempt 1。

        调用方负责生成随机身份和独立工作目录；Repository 在同一写事务内重读当前 Project、校验
        snapshot、闭包成员、节点版本、attempt 与空输入绑定，任何一项冲突都不会留下半个 Run。
        """

        started_at = _normalize_write_timestamp(started_at, context="started_at")
        candidates = tuple(node_runs)
        if run.state is not RunState.PENDING or run.node_runs:
            raise RuntimeConflictError("E_RUN_CREATE_STATE", "新 Run 必须是 pending 且尚无 NodeRun")
        expected_node_ids = _selected_node_ids(run.graph_snapshot, run.selected_targets)
        candidate_node_ids = tuple(item.node_id for item in candidates)
        if len(candidate_node_ids) != len(set(candidate_node_ids)) or set(
            candidate_node_ids
        ) != set(expected_node_ids):
            raise RuntimeConflictError(
                "E_RUN_ATTEMPT_SET_INVALID",
                "attempt 1 必须精确覆盖 Run 的完整选中闭包",
            )
        if any(item.input_artifact_ids for item in candidates):
            raise RuntimeConflictError(
                "E_RUN_INITIAL_INPUT_BOUND",
                "attempt 1 的下游 Artifact 尚不存在，初始 inputs 必须为空",
            )
        if any(item.created_at > started_at for item in candidates):
            raise RuntimeConflictError(
                "E_RUN_ATTEMPT_TIME_INVALID",
                "attempt 1 必须在 Run started_at 当时已经创建",
            )
        try:
            running = run.model_copy(update={"state": RunState.RUNNING, "started_at": started_at})
        except ValidationError as error:
            raise RuntimeConflictError("E_RUN_START_FIELDS", str(error)) from error

        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._store._check_storage_revision(
                    self._store._read_storage_revision(connection), expected_storage_revision
                )
                self._validate_new_run_snapshot(connection, run)
                self._validate_bulk_node_runs(
                    connection,
                    run,
                    candidates,
                    expected_attempts=dict.fromkeys(expected_node_ids, 1),
                )
                if rerun_from_node_id is not None:
                    if rerun_from_node_id not in expected_node_ids:
                        raise RuntimeConflictError(
                            "E_RERUN_NODE_NOT_IN_CURRENT_GRAPH", "重跑节点不属于当前 Run"
                        )
                    # 重跑意图属于本 Run 的创建时刻；提交虽稍晚，也不能被 reuse 当成
                    # snapshot 之后的编辑而忽略。失效与 Run/attempt 仍在同一事务提交。
                    self._mark_latest_stale_in_connection(
                        connection,
                        (rerun_from_node_id,),
                        StaleReason.RERUN_REQUESTED,
                        run.created_at,
                    )
                    self._mark_latest_stale_in_connection(
                        connection,
                        _downstream_node_ids(run.graph_snapshot, rerun_from_node_id),
                        StaleReason.UPSTREAM_CHANGED,
                        run.created_at,
                    )
                self._insert_run(connection, running)
                for candidate in candidates:
                    self._insert_node_run(connection, candidate)
                connection.commit()
        except RuntimeRepositoryError:
            raise
        except (ValidationError, sqlite3.IntegrityError) as error:
            raise RuntimeConflictError("E_RUN_START_CONFLICT", str(error)) from error
        except sqlite3.Error as error:
            raise RuntimeRepositoryError("E_RUN_START_FAILED", str(error)) from error
        return self.get_run(run.run_id)

    def create_run(self, run: Run) -> Run:
        """持久化一个只含普通 Graph/definition snapshot 的 pending Run。"""

        if run.state is not RunState.PENDING or run.node_runs:
            raise RuntimeConflictError("E_RUN_CREATE_STATE", "新 Run 必须是 pending 且尚无 NodeRun")
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._validate_new_run_snapshot(connection, run)
                self._insert_run(connection, run)
                connection.commit()
        except sqlite3.IntegrityError as error:
            raise RuntimeConflictError("E_RUN_ID_CONFLICT", str(error)) from error
        except sqlite3.Error as error:
            raise RuntimeRepositoryError("E_RUN_CREATE_FAILED", str(error)) from error
        return self.get_run(run.run_id)

    def get_run(self, run_id: str) -> Run:
        """读取一个 Run 及其不可改写的完整 NodeRun 历史。"""

        with self._read_connection() as connection:
            return self._read_run(connection, run_id)

    def list_runs(self) -> tuple[Run, ...]:
        """按创建顺序读取全部 Run。"""

        with self._read_connection() as connection:
            run_ids = [
                cast(str, row[0])
                for row in connection.execute("SELECT run_id FROM runs ORDER BY rowid")
            ]
            return tuple(self._read_run(connection, run_id) for run_id in run_ids)

    def transition_run(
        self,
        run_id: str,
        state: RunState,
        *,
        occurred_at: datetime,
        error: RuntimeFailure | None = None,
    ) -> Run:
        """只允许完整 selected closure 从 running 原子终结为 completed。"""

        occurred_at = _normalize_write_timestamp(occurred_at, context="occurred_at")
        allowed = {
            RunState.PENDING: frozenset(),
            RunState.RUNNING: frozenset({RunState.COMPLETED}),
            RunState.COMPLETED: frozenset(),
            RunState.FAILED: frozenset(),
        }
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._read_run(connection, run_id)
            if state not in allowed[current.state]:
                connection.rollback()
                raise RuntimeConflictError(
                    "E_RUN_TRANSITION_INVALID", f"不允许 {current.state.value}→{state.value}"
                )
            if state is RunState.COMPLETED:
                selected = _selected_node_ids(current.graph_snapshot, current.selected_targets)
                latest: dict[str, NodeRun] = {}
                for node_run in current.node_runs:
                    previous = latest.get(node_run.node_id)
                    if previous is None or node_run.attempt > previous.attempt:
                        latest[node_run.node_id] = node_run
                incomplete = tuple(
                    node_id
                    for node_id in selected
                    if node_id not in latest or latest[node_id].state is not NodeRunState.COMPLETED
                )
                if incomplete:
                    raise RuntimeConflictError(
                        "E_RUN_COMPLETION_INCOMPLETE",
                        "selected closure 的最新 attempts 尚未全部 completed："
                        + ", ".join(incomplete),
                    )
                latest_ended = tuple(latest[node_id].ended_at for node_id in selected)
                if any(ended_at is None or ended_at > occurred_at for ended_at in latest_ended):
                    raise RuntimeConflictError(
                        "E_RUN_COMPLETION_TIME_INVALID",
                        "Run completed 时间不得早于 selected latest NodeRun ended_at",
                    )
            update: dict[str, Any] = {"state": state}
            if state is RunState.RUNNING:
                if error is not None:
                    connection.rollback()
                    raise RuntimeConflictError("E_RUN_ERROR_UNEXPECTED", "running 不接受 error")
                update["started_at"] = occurred_at
            else:
                update["ended_at"] = occurred_at
                update["error"] = error
            try:
                updated = current.model_copy(update=update)
            except ValidationError as model_error:
                connection.rollback()
                raise RuntimeConflictError(
                    "E_RUN_TRANSITION_FIELDS", str(model_error)
                ) from model_error
            changed = connection.execute(
                """
                UPDATE runs
                SET state = ?, started_at = ?, ended_at = ?, error_json = ?
                WHERE run_id = ? AND state = ?
                """,
                (
                    updated.state.value,
                    None if updated.started_at is None else _timestamp(updated.started_at),
                    None if updated.ended_at is None else _timestamp(updated.ended_at),
                    None
                    if updated.error is None
                    else _dump_json(updated.error.model_dump(mode="json")),
                    run_id,
                    current.state.value,
                ),
            ).rowcount
            if changed != 1:
                connection.rollback()
                raise RuntimeConflictError("E_RUN_TRANSITION_RACE", "Run 状态已被并发修改")
            connection.commit()
        return self.get_run(run_id)

    def abandon_run(
        self,
        run_id: str,
        *,
        abandoned_at: datetime,
        pending_node_runs: Sequence[NodeRun] = (),
    ) -> Run:
        """原子把非终态 Run 收敛为 ``failed(reason=cancelled)``。

        queued pending Run 的 attempt 1 由 Service 预生成，但只在本事务内插入并立即失败；Repository
        不创建 work_dir、日志或输出。running Run 只终结每个节点最高的 pending/waiting attempt，历史
        superseded attempt、completed 结果、handoff 与 Artifact 均保持不可改写。
        """

        abandoned_at = _normalize_write_timestamp(abandoned_at, context="abandoned_at")
        candidates = tuple(pending_node_runs)
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                current = self._read_run(connection, run_id)
                if current.state not in {RunState.PENDING, RunState.RUNNING}:
                    raise RuntimeConflictError(
                        "E_RUN_ABANDON_TERMINAL",
                        f"终态 Run {current.state.value!r} 不能再次 abandon",
                    )

                if current.state is RunState.PENDING:
                    selected = _selected_node_ids(
                        current.graph_snapshot,
                        current.selected_targets,
                    )
                    candidate_nodes = tuple(item.node_id for item in candidates)
                    if len(candidate_nodes) != len(set(candidate_nodes)) or set(
                        candidate_nodes
                    ) != set(selected):
                        raise RuntimeConflictError(
                            "E_RUN_ABANDON_ATTEMPT_SET",
                            "queued pending Run 的取消 attempts 必须精确覆盖执行闭包",
                        )
                    self._validate_bulk_node_runs(
                        connection,
                        current,
                        candidates,
                        expected_attempts=dict.fromkeys(selected, 1),
                    )
                    if any(item.created_at != abandoned_at for item in candidates):
                        raise RuntimeConflictError(
                            "E_RUN_ABANDON_ATTEMPT_TIME",
                            "queued pending Run 的取消 attempt 必须使用统一取消时刻",
                        )
                    for candidate in candidates:
                        self._insert_node_run(connection, candidate)
                    history = candidates
                else:
                    if candidates:
                        raise RuntimeConflictError(
                            "E_RUN_ABANDON_ATTEMPT_UNEXPECTED",
                            "running Run 不接受额外 attempt",
                        )
                    history = current.node_runs

                if any(item.state is NodeRunState.RUNNING for item in history):
                    raise RuntimeConflictError(
                        "E_RUN_ABANDON_ACTIVE",
                        "存在 automatic running attempt，不能 abandon",
                    )

                latest: dict[str, NodeRun] = {}
                for item in history:
                    previous = latest.get(item.node_id)
                    if previous is None or item.attempt > previous.attempt:
                        latest[item.node_id] = item
                observed_times = [current.created_at]
                observed_times.extend(
                    value for value in (current.started_at, current.ended_at) if value is not None
                )
                for item in latest.values():
                    observed_times.append(item.created_at)
                    observed_times.extend(
                        value for value in (item.started_at, item.ended_at) if value is not None
                    )
                if any(value > abandoned_at for value in observed_times):
                    raise RuntimeConflictError(
                        "E_RUN_ABANDON_TIME",
                        "取消时刻不得早于 Run 或最新 attempt 的既有时间",
                    )
                cancelled_error = RuntimeFailure(
                    reason=FailureReason.CANCELLED,
                    message="操作者放弃 Run；未完成节点只能从头创建新 attempt",
                )
                for item in latest.values():
                    if item.state not in {NodeRunState.PENDING, NodeRunState.WAITING_EXTERNAL}:
                        continue
                    updated = item.model_copy(
                        update={
                            "state": NodeRunState.FAILED,
                            "started_at": item.started_at or abandoned_at,
                            "ended_at": abandoned_at,
                            "error": cancelled_error,
                        }
                    )
                    self._update_node_run(connection, item, updated)

                started_at = current.started_at or abandoned_at
                updated_run = current.model_copy(
                    update={
                        "state": RunState.FAILED,
                        "started_at": started_at,
                        "ended_at": abandoned_at,
                        "error": cancelled_error,
                    }
                )
                assert updated_run.started_at is not None
                assert updated_run.ended_at is not None
                assert updated_run.error is not None
                changed = connection.execute(
                    """
                    UPDATE runs
                    SET state = ?, started_at = ?, ended_at = ?, error_json = ?
                    WHERE run_id = ? AND state = ?
                    """,
                    (
                        updated_run.state.value,
                        _timestamp(updated_run.started_at, context="Run.started_at"),
                        _timestamp(updated_run.ended_at, context="Run.ended_at"),
                        _dump_json(updated_run.error.model_dump(mode="json")),
                        current.run_id,
                        current.state.value,
                    ),
                ).rowcount
                if changed != 1:
                    raise RuntimeConflictError(
                        "E_RUN_ABANDON_RACE",
                        "Run 状态已被并发修改",
                    )
                connection.commit()
        except RuntimeRepositoryError:
            raise
        except (ValidationError, sqlite3.IntegrityError) as error:
            raise RuntimeConflictError("E_RUN_ABANDON_CONFLICT", str(error)) from error
        except sqlite3.Error as error:
            raise RuntimeRepositoryError("E_RUN_ABANDON_FAILED", str(error)) from error
        return self.get_run(run_id)

    def create_node_run(self, node_run: NodeRun) -> NodeRun:
        """新增 pending attempt；同一 node 的 attempt 必须从 1 连续递增。"""

        if node_run.state is not NodeRunState.PENDING:
            raise RuntimeConflictError("E_NODE_RUN_CREATE_STATE", "新 NodeRun 必须是 pending")
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                run = self._read_run(connection, node_run.run_id)
                if run.state is not RunState.RUNNING:
                    connection.rollback()
                    raise RuntimeConflictError(
                        "E_NODE_RUN_RUN_NOT_RUNNING", "只有 running Run 可以新增 attempt"
                    )
                if node_run.node_id not in _selected_node_ids(
                    run.graph_snapshot, run.selected_targets
                ):
                    raise RuntimeConflictError(
                        "E_NODE_RUN_OUTSIDE_SELECTION",
                        "NodeRun node 不属于 Run 的 selected closure",
                    )
                candidate_work_dir = _work_dir_identity(node_run.work_dir)
                existing_work_dirs = tuple(
                    _work_dir_identity(cast(str, row[0]))
                    for row in connection.execute("SELECT work_dir FROM node_runs")
                )
                normalized_existing = tuple(item for item in existing_work_dirs if item is not None)
                if (
                    candidate_work_dir is None
                    or any(item is None for item in existing_work_dirs)
                    or any(
                        _work_dirs_overlap(candidate_work_dir, item) for item in normalized_existing
                    )
                ):
                    raise RuntimeConflictError(
                        "E_NODE_RUN_WORK_DIR_CONFLICT",
                        "新 attempt 必须使用全历史独立且不重叠的 resolved/normcase work_dir",
                    )
                if node_run.attempt != 1 or node_run.input_artifact_ids:
                    raise RuntimeConflictError(
                        "E_NODE_RUN_AGGREGATE_REQUIRED",
                        "attempt 1 必须由 start_run 建立；rerun attempt 必须由聚合 API 建立",
                    )
                node_versions = {
                    item.node_id: item.definition_version for item in run.graph_snapshot.nodes
                }
                if node_versions.get(node_run.node_id) != node_run.definition_version:
                    connection.rollback()
                    raise RuntimeConflictError(
                        "E_NODE_RUN_BINDING_MISMATCH", "NodeRun 没有绑定 Run snapshot 节点版本"
                    )
                missing_inputs = [
                    artifact_id
                    for artifact_id in node_run.input_artifact_ids
                    if connection.execute(
                        "SELECT 1 FROM artifacts WHERE artifact_id = ?", (artifact_id,)
                    ).fetchone()
                    is None
                ]
                if missing_inputs:
                    connection.rollback()
                    raise RuntimeConflictError(
                        "E_NODE_RUN_INPUT_UNKNOWN", f"输入 Artifact 不存在：{missing_inputs}"
                    )
                previous = connection.execute(
                    """
                    SELECT max(attempt) FROM node_runs WHERE run_id = ? AND node_id = ?
                    """,
                    (node_run.run_id, node_run.node_id),
                ).fetchone()[0]
                expected_attempt = 1 if previous is None else cast(int, previous) + 1
                if node_run.attempt != expected_attempt:
                    connection.rollback()
                    raise RuntimeConflictError(
                        "E_NODE_RUN_ATTEMPT_NON_CONTIGUOUS",
                        f"attempt 必须为 {expected_attempt}",
                    )
                previous_rows = connection.execute(
                    "SELECT * FROM node_runs WHERE run_id = ? AND node_id = ?",
                    (node_run.run_id, node_run.node_id),
                ).fetchall()
                prior_times = tuple(
                    value
                    for row in previous_rows
                    for value in (
                        self._node_run_from_row(row).created_at,
                        self._node_run_from_row(row).started_at,
                        self._node_run_from_row(row).ended_at,
                    )
                    if value is not None
                )
                if prior_times and node_run.created_at < max(prior_times):
                    raise RuntimeConflictError(
                        "E_NODE_RUN_TIME_NON_MONOTONIC",
                        "新 attempt.created_at 不得早于既有 attempt 时间",
                    )
                self._insert_node_run(connection, node_run)
                connection.commit()
        except sqlite3.IntegrityError as error:
            raise RuntimeConflictError("E_NODE_RUN_ID_CONFLICT", str(error)) from error
        except sqlite3.Error as error:
            raise RuntimeRepositoryError("E_NODE_RUN_CREATE_FAILED", str(error)) from error
        return self.get_node_run(node_run.node_run_id)

    def create_rerun_attempts(
        self,
        run_id: str,
        source_node_id: str,
        node_runs: Sequence[NodeRun],
        *,
        updated_at: datetime,
        expected_storage_revision: int | None = None,
    ) -> tuple[NodeRun, ...]:
        """原子创建 source 与选中下游闭包的新 attempts，并失效 current projection。

        旧 attempt 与 handoff 始终保留。事务会重算 Run snapshot 中的闭包与下一连续 attempt，拒绝
        漏项、额外项、预绑定输入和仍在 running 的被取代 attempt；stale 写入与全部 INSERT 同成同败。
        """

        updated_at = _normalize_write_timestamp(updated_at, context="updated_at")
        candidates = tuple(node_runs)
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._store._check_storage_revision(
                    self._store._read_storage_revision(connection), expected_storage_revision
                )
                run = self._read_run(connection, run_id)
                if expected_storage_revision is not None:
                    self._validate_new_run_snapshot(connection, run, ignore_ui_position=True)
                if run.state is not RunState.RUNNING:
                    raise RuntimeConflictError(
                        "E_RERUN_RUN_NOT_RUNNING",
                        "只有 running Run 可以创建 rerun attempts",
                    )
                selected = _selected_node_ids(run.graph_snapshot, run.selected_targets)
                if source_node_id not in selected:
                    raise RuntimeConflictError(
                        "E_RERUN_SOURCE_OUTSIDE_SELECTION",
                        "source node 不属于 Run 的选中闭包",
                    )
                expected_node_ids = _rerun_node_ids(run.graph_snapshot, selected, source_node_id)
                candidate_node_ids = tuple(item.node_id for item in candidates)
                if len(candidate_node_ids) != len(set(candidate_node_ids)) or set(
                    candidate_node_ids
                ) != set(expected_node_ids):
                    raise RuntimeConflictError(
                        "E_RERUN_ATTEMPT_SET_INVALID",
                        "rerun attempts 必须精确覆盖 source 与选中下游闭包",
                    )
                if any(item.input_artifact_ids for item in candidates):
                    raise RuntimeConflictError(
                        "E_RERUN_INPUT_BOUND",
                        "rerun pending attempts 必须在 ready 时重新绑定 inputs",
                    )

                expected_attempts: dict[str, int] = {}
                latest_attempts: dict[str, NodeRun] = {}
                for node_id in expected_node_ids:
                    row = connection.execute(
                        """
                        SELECT * FROM node_runs
                        WHERE run_id = ? AND node_id = ?
                        ORDER BY attempt DESC LIMIT 1
                        """,
                        (run_id, node_id),
                    ).fetchone()
                    if row is None:
                        raise RuntimeDataError(
                            "E_RERUN_HISTORY_MISSING",
                            f"Run 缺少节点 {node_id!r} 的 attempt 1",
                        )
                    latest_attempt = self._node_run_from_row(row)
                    latest_attempts[node_id] = latest_attempt
                    if latest_attempt.state is NodeRunState.RUNNING:
                        raise RuntimeConflictError(
                            "E_RERUN_ATTEMPT_RUNNING",
                            f"不能取代仍在运行的节点 {node_id!r}",
                        )
                    expected_attempts[node_id] = latest_attempt.attempt + 1

                self._validate_bulk_node_runs(
                    connection,
                    run,
                    candidates,
                    expected_attempts=expected_attempts,
                )
                candidates_by_node = {item.node_id: item for item in candidates}
                for node_id, previous_attempt in latest_attempts.items():
                    prior_times = tuple(
                        value
                        for value in (
                            previous_attempt.created_at,
                            previous_attempt.started_at,
                            previous_attempt.ended_at,
                        )
                        if value is not None
                    )
                    if candidates_by_node[node_id].created_at < max(prior_times):
                        raise RuntimeConflictError(
                            "E_NODE_RUN_TIME_NON_MONOTONIC",
                            "rerun attempt.created_at 不得早于前一 attempt 时间",
                        )
                current_project = self._read_current_project(connection)
                if self._result_applies_to_current_project(
                    connection,
                    node_run=latest_attempts[source_node_id],
                    source_run=run,
                    current_graph=current_project.project.graph,
                    current_definitions=current_project.definitions,
                ):
                    self._mark_latest_stale_in_connection(
                        connection,
                        (source_node_id,),
                        StaleReason.RERUN_REQUESTED,
                        updated_at,
                    )
                    self._mark_latest_stale_in_connection(
                        connection,
                        _downstream_node_ids(current_project.project.graph, source_node_id),
                        StaleReason.UPSTREAM_CHANGED,
                        updated_at,
                    )
                for candidate in candidates:
                    self._insert_node_run(connection, candidate)
                connection.commit()
        except RuntimeRepositoryError:
            raise
        except (ValidationError, sqlite3.IntegrityError) as error:
            raise RuntimeConflictError("E_RERUN_CREATE_CONFLICT", str(error)) from error
        except sqlite3.Error as error:
            raise RuntimeRepositoryError("E_RERUN_CREATE_FAILED", str(error)) from error
        return tuple(self.get_node_run(item.node_run_id) for item in candidates)

    def get_node_run(self, node_run_id: str) -> NodeRun:
        """按随机身份读取一个历史 attempt。"""

        with self._read_connection() as connection:
            node_run = self._read_node_run(connection, node_run_id)
            run = self._read_run(connection, node_run.run_id)
            return next(item for item in run.node_runs if item.node_run_id == node_run_id)

    def list_node_runs(self, run_id: str) -> tuple[NodeRun, ...]:
        """按插入顺序读取一个 Run 的全部 attempt。"""

        with self._read_connection() as connection:
            return self._read_run(connection, run_id).node_runs

    def transition_node_run(
        self,
        node_run_id: str,
        state: NodeRunState,
        *,
        occurred_at: datetime,
        error: RuntimeFailure | None = None,
        external_handoff: ExternalHandoff | None = None,
        exit_code: int | None = None,
        log_path: str | None = None,
        progress: float | None = None,
    ) -> NodeRun:
        """迁移非成功终态；completed 只能经 result 原子登记或结果复用形成。"""

        if progress is not None:
            progress = _validated_progress(progress)
        occurred_at = _normalize_write_timestamp(occurred_at, context="occurred_at")
        allowed = {
            NodeRunState.PENDING: frozenset({NodeRunState.RUNNING, NodeRunState.WAITING_EXTERNAL}),
            NodeRunState.RUNNING: frozenset({NodeRunState.FAILED}),
            NodeRunState.WAITING_EXTERNAL: frozenset({NodeRunState.FAILED}),
            NodeRunState.COMPLETED: frozenset(),
            NodeRunState.FAILED: frozenset(),
        }
        if state is NodeRunState.COMPLETED:
            raise RuntimeConflictError(
                "E_NODE_RUN_COMPLETION_REQUIRES_RESULT", "completed 必须原子登记 result 或复用"
            )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._read_node_run(connection, node_run_id)
            run = self._read_run(connection, current.run_id)
            if run.state is not RunState.RUNNING:
                raise RuntimeConflictError(
                    "E_NODE_RUN_PARENT_NOT_RUNNING",
                    "只有 running Run 的 NodeRun 可以迁移状态",
                )
            self._assert_latest_attempt(connection, current)
            if run.started_at is None or occurred_at < run.started_at:
                raise RuntimeConflictError(
                    "E_NODE_RUN_TIME_BEFORE_RUN_START",
                    "NodeRun 状态时间不得早于 parent Run.started_at",
                )
            if state not in allowed[current.state]:
                connection.rollback()
                raise RuntimeConflictError(
                    "E_NODE_RUN_TRANSITION_INVALID",
                    f"不允许 {current.state.value}→{state.value}",
                )
            definition = self._definition_for_run_node(run, current.node_id)
            if definition is None:
                raise RuntimeDataError(
                    "E_NODE_RUN_DEFINITION_CORRUPT",
                    "Run snapshot 缺少 NodeRun 的 NodeDefinition",
                )
            if current.state is NodeRunState.PENDING:
                if progress is not None:
                    connection.rollback()
                    raise RuntimeConflictError(
                        "E_NODE_RUN_START_PROGRESS_UNEXPECTED",
                        "pending 启动或 handoff 不接受 progress；首个可信 sample 必须经 Reporter",
                    )
                if error is not None or exit_code is not None:
                    raise RuntimeConflictError(
                        "E_NODE_RUN_START_FIELDS",
                        "pending 启动或 handoff 不接受 terminal error/exit_code",
                    )
                expected_inputs = self._expected_run_input_artifact_ids(
                    connection, run, current.node_id
                )
                if current.input_artifact_ids != expected_inputs:
                    raise RuntimeConflictError(
                        "E_NODE_RUN_INPUT_BINDING_MISMATCH",
                        "NodeRun 尚未精确绑定当前 completed 上游 outputs",
                    )
                if state is NodeRunState.RUNNING:
                    if definition.execution_mode is not ExecutionMode.AUTOMATIC:
                        raise RuntimeConflictError(
                            "E_NODE_RUN_EXECUTION_MODE_MISMATCH",
                            "只有 automatic NodeDefinition 可以进入 running",
                        )
                else:
                    if (
                        definition.execution_mode is not ExecutionMode.MANUAL_EXTERNAL
                        or not isinstance(definition.executor, ManualExternalExecutorSpec)
                    ):
                        raise RuntimeConflictError(
                            "E_NODE_RUN_EXECUTION_MODE_MISMATCH",
                            "只有 manual_external NodeDefinition 可以进入 waiting_external",
                        )
                    if external_handoff is None:
                        raise RuntimeConflictError(
                            "E_HANDOFF_REQUIRED",
                            "waiting_external 必须携带 handoff",
                        )
                    if not self._handoff_contract_matches(
                        definition,
                        current,
                        external_handoff,
                        occurred_at,
                    ):
                        raise RuntimeConflictError(
                            "E_HANDOFF_CONTRACT_MISMATCH",
                            "handoff inputs/outputs/instructions/time/path 不符合 Run snapshot",
                        )
            update: dict[str, Any] = {"state": state}
            if current.state is NodeRunState.PENDING:
                update.update(
                    {
                        "started_at": occurred_at,
                        # 没有可信 reporter sample 时必须保持 indeterminate，不能伪造 0%。
                        "progress": progress,
                        "log_path": log_path,
                    }
                )
                if state is NodeRunState.WAITING_EXTERNAL:
                    update["external_handoff"] = external_handoff
                elif external_handoff is not None:
                    connection.rollback()
                    raise RuntimeConflictError(
                        "E_HANDOFF_UNEXPECTED", "automatic running 不接受 handoff"
                    )
            else:
                if current.state is NodeRunState.WAITING_EXTERNAL and progress is not None:
                    connection.rollback()
                    raise RuntimeConflictError(
                        "E_NODE_RUN_MANUAL_PROGRESS_UNEXPECTED",
                        "manual_external attempt 没有 Reporter，终态不得伪造 determinate progress",
                    )
                if (
                    progress is not None
                    and current.progress is not None
                    and progress < current.progress
                ):
                    connection.rollback()
                    raise RuntimeConflictError(
                        "E_NODE_RUN_PROGRESS_REGRESSION",
                        "终态事务不得把最后可信 progress 向后改写",
                    )
                update.update(
                    {
                        "ended_at": occurred_at,
                        "error": error,
                        "exit_code": exit_code,
                        "log_path": log_path or current.log_path,
                        "progress": current.progress if progress is None else progress,
                    }
                )
                if external_handoff is not None and external_handoff != current.external_handoff:
                    connection.rollback()
                    raise RuntimeConflictError("E_HANDOFF_IMMUTABLE", "已持久化 handoff 不得替换")
            try:
                updated = current.model_copy(update=update)
            except ValidationError as model_error:
                connection.rollback()
                raise RuntimeConflictError(
                    "E_NODE_RUN_TRANSITION_FIELDS", str(model_error)
                ) from model_error
            self._update_node_run(connection, current, updated)
            connection.commit()
        return self.get_node_run(node_run_id)

    def inspect_progress_target(
        self,
        run_id: str,
        node_run_id: str,
        attempt: int,
    ) -> NodeRun:
        """只读确认 reporter 仍绑定最新 ``running`` automatic attempt。"""

        with self._read_connection() as connection:
            current = self._read_node_run(connection, node_run_id)
            self._assert_progress_target(
                connection,
                current,
                run_id=run_id,
                attempt=attempt,
            )
            return current

    def update_progress(
        self,
        node_run_id: str,
        progress: float,
        *,
        run_id: str | None = None,
        attempt: int | None = None,
    ) -> NodeRun:
        """仅更新当前 running attempt 的单调进度，不创建额外持久状态。

        Runtime reporter 同时传入 ``run_id`` 与 ``attempt``，使 identity 与写入在同一事务校验；
        可选值只保留既有 Repository 调用兼容。
        """

        progress = _validated_progress(progress)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._read_node_run(connection, node_run_id)
            if (run_id is None) != (attempt is None):
                connection.rollback()
                raise RuntimeConflictError(
                    "E_PROGRESS_BINDING_PARTIAL",
                    "run_id 与 attempt 必须同时提供或同时省略",
                )
            if run_id is None or attempt is None:
                run = self._read_run(connection, current.run_id)
                if run.state is not RunState.RUNNING:
                    raise RuntimeConflictError(
                        "E_NODE_RUN_PARENT_NOT_RUNNING",
                        "只有 running Run 的 NodeRun 可以更新进度",
                    )
                self._assert_latest_attempt(connection, current)
                if current.state is not NodeRunState.RUNNING:
                    connection.rollback()
                    raise RuntimeConflictError("E_NODE_RUN_NOT_RUNNING", "只有 running 可更新进度")
            else:
                self._assert_progress_target(
                    connection,
                    current,
                    run_id=run_id,
                    attempt=attempt,
                )
            if current.progress is not None and progress < current.progress:
                connection.rollback()
                raise RuntimeConflictError("E_NODE_RUN_PROGRESS_REGRESSION", "progress 不得回退")
            try:
                updated = current.model_copy(update={"progress": progress})
            except ValidationError as model_error:
                connection.rollback()
                raise RuntimeConflictError(
                    "E_NODE_RUN_PROGRESS_INVALID", str(model_error)
                ) from model_error
            self._update_node_run(connection, current, updated)
            connection.commit()
        return self.get_node_run(node_run_id)

    def fail_node_run_before_start(
        self,
        node_run_id: str,
        *,
        failed_at: datetime,
        error: RuntimeFailure,
        log_path: str | None = None,
    ) -> NodeRun:
        """原子记录 adapter/handoff 准备失败，不伪造 manual_external running 状态。

        该入口只接受 running Run 中尚未启动的 pending attempt，并把 started/ended 统一记为失败时点。
        它不代表执行已开始，也不允许缺少结构化 ``RuntimeFailure``。
        """

        failed_at = _normalize_write_timestamp(failed_at, context="failed_at")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._read_node_run(connection, node_run_id)
            run = self._read_run(connection, current.run_id)
            if run.state is not RunState.RUNNING:
                raise RuntimeConflictError(
                    "E_NODE_RUN_PARENT_NOT_RUNNING",
                    "只有 running Run 的 pending NodeRun 可以记录准备失败",
                )
            self._assert_latest_attempt(connection, current)
            if run.started_at is None or failed_at < run.started_at:
                raise RuntimeConflictError(
                    "E_NODE_RUN_TIME_BEFORE_RUN_START",
                    "准备失败时间不得早于 parent Run.started_at",
                )
            if current.state is not NodeRunState.PENDING:
                raise RuntimeConflictError(
                    "E_NODE_RUN_PRESTART_FAILURE_STATE",
                    "准备失败入口只接受 pending NodeRun",
                )
            expected_inputs = self._expected_run_input_artifact_ids(
                connection, run, current.node_id
            )
            if current.input_artifact_ids != expected_inputs:
                raise RuntimeConflictError(
                    "E_NODE_RUN_INPUT_BINDING_MISMATCH",
                    "准备失败也只能记录在已 ready 且精确绑定 inputs 的 attempt",
                )
            try:
                updated = current.model_copy(
                    update={
                        "state": NodeRunState.FAILED,
                        "started_at": failed_at,
                        "ended_at": failed_at,
                        # prepare 尚未开始执行，也没有 reporter sample，必须保持 indeterminate。
                        "progress": None,
                        "log_path": log_path,
                        "error": error,
                    }
                )
            except ValidationError as model_error:
                raise RuntimeConflictError(
                    "E_NODE_RUN_PRESTART_FAILURE_FIELDS", str(model_error)
                ) from model_error
            self._update_node_run(connection, current, updated)
            connection.commit()
        return self.get_node_run(node_run_id)

    def bind_inputs(
        self,
        node_run_id: str,
        artifact_ids: tuple[str, ...],
    ) -> NodeRun:
        """在节点首次 ready 时一次性绑定有序直接输入。

        下游 attempt 可以先以空输入进入 pending；只有 pending、尚未绑定且所有 Artifact 已登记时
        才允许写入一次。tuple 保留端口/ordinal 展开后的业务顺序，并允许同一 Artifact 在不同
        ordinal 重复。
        """

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._read_node_run(connection, node_run_id)
            run = self._read_run(connection, current.run_id)
            if run.state is not RunState.RUNNING:
                raise RuntimeConflictError(
                    "E_NODE_RUN_PARENT_NOT_RUNNING",
                    "只有 running Run 的 NodeRun 可以绑定 inputs",
                )
            self._assert_latest_attempt(connection, current)
            if current.state is not NodeRunState.PENDING or current.input_artifact_ids:
                connection.rollback()
                raise RuntimeConflictError(
                    "E_NODE_RUN_INPUTS_ALREADY_BOUND",
                    "只有尚无输入的 pending attempt 可绑定 inputs",
                )
            expected = self._expected_run_input_artifact_ids(connection, run, current.node_id)
            if artifact_ids != expected:
                raise RuntimeConflictError(
                    "E_NODE_RUN_INPUT_BINDING_MISMATCH",
                    "inputs 必须按 Run snapshot 入边顺序精确绑定当前 completed 上游 outputs",
                )
            try:
                updated = current.model_copy(update={"input_artifact_ids": artifact_ids})
            except ValidationError as model_error:
                connection.rollback()
                raise RuntimeConflictError(
                    "E_NODE_RUN_INPUT_BINDING_INVALID", str(model_error)
                ) from model_error
            self._update_node_run(connection, current, updated, allow_input_binding=True)
            connection.commit()
        return self.get_node_run(node_run_id)

    def register_result(
        self,
        result: NodeResult,
        *,
        ended_at: datetime,
        exit_code: int | None = None,
    ) -> NodeRun:
        """在一个事务中登记 NodeResult、全部 Artifact、completed 和 latest head。

        任一 identity、外键或状态检查失败时整个事务回滚，partial output 永远不会成为 Artifact。
        """

        ended_at = _normalize_write_timestamp(ended_at, context="ended_at")
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                current = self._read_node_run(connection, result.node_run_id)
                source_run = self._read_run(connection, current.run_id)
                if source_run.state is not RunState.RUNNING:
                    raise RuntimeConflictError(
                        "E_NODE_RUN_PARENT_NOT_RUNNING",
                        "只有 running Run 的 NodeRun 可以登记结果",
                    )
                latest_attempt = connection.execute(
                    "SELECT max(attempt) FROM node_runs WHERE run_id = ? AND node_id = ?",
                    (current.run_id, current.node_id),
                ).fetchone()[0]
                if latest_attempt != current.attempt:
                    raise RuntimeConflictError(
                        "E_RESULT_ATTEMPT_SUPERSEDED",
                        "已被更高 attempt 取代的 handoff/result 不得晚到登记",
                    )
                current_project = self._read_current_project(connection)
                if current.state not in {
                    NodeRunState.RUNNING,
                    NodeRunState.WAITING_EXTERNAL,
                }:
                    connection.rollback()
                    raise RuntimeConflictError(
                        "E_RESULT_NODE_RUN_STATE", "只有 running/waiting_external 可登记结果"
                    )
                if result.created_at > ended_at:
                    connection.rollback()
                    raise RuntimeConflictError(
                        "E_RESULT_TIME_INVALID", "result created_at 不得晚于 ended_at"
                    )
                if current.started_at is None or result.created_at < current.started_at:
                    connection.rollback()
                    raise RuntimeConflictError(
                        "E_RESULT_TIME_INVALID", "result created_at 不得早于 NodeRun 启动"
                    )
                definition = self._definition_for_run_node(source_run, current.node_id)
                if definition is None:
                    raise RuntimeDataError(
                        "E_NODE_RUN_DEFINITION_CORRUPT",
                        "Run snapshot 缺少 NodeRun definition",
                    )
                if current.state is NodeRunState.RUNNING:
                    if isinstance(definition.executor, CommandExecutorSpec):
                        valid_exit_code = exit_code == 0
                    elif isinstance(definition.executor, PythonExecutorSpec):
                        valid_exit_code = exit_code is None
                    else:
                        valid_exit_code = False
                    if not valid_exit_code:
                        connection.rollback()
                        raise RuntimeConflictError(
                            "E_RESULT_EXIT_CODE",
                            "command 成功必须 exit_code=0；Python 成功必须 exit_code=None",
                        )
                if current.state is NodeRunState.WAITING_EXTERNAL and exit_code is not None:
                    connection.rollback()
                    raise RuntimeConflictError(
                        "E_RESULT_EXTERNAL_EXIT_CODE", "manual_external 结果不得伪造 exit_code"
                    )
                if not self._result_output_contract_matches(source_run, current, result):
                    raise RuntimeConflictError(
                        "E_RESULT_OUTPUT_CONTRACT",
                        "NodeResult outputs 必须精确匹配 Run snapshot "
                        "output ports/type/order/ordinal",
                    )
                output_ids = tuple(item.artifact_id for item in result.outputs)
                updated = current.model_copy(
                    update={
                        "state": NodeRunState.COMPLETED,
                        "output_artifact_ids": output_ids,
                        "ended_at": ended_at,
                        "progress": 1.0,
                        "exit_code": exit_code,
                    }
                )
                connection.execute(
                    """
                    INSERT INTO node_results(
                        result_id, node_run_id, output_artifact_ids_json,
                        media_summary_json, validation_summary_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result.result_id,
                        result.node_run_id,
                        _dump_json(list(output_ids)),
                        _dump_json(result.media_summary),
                        _dump_json(result.validation_summary),
                        _timestamp(result.created_at),
                    ),
                )
                for artifact in result.outputs:
                    self._insert_artifact(connection, result.result_id, artifact)
                self._update_node_run(connection, current, updated)
                if self._result_applies_to_current_project(
                    connection,
                    node_run=current,
                    source_run=source_run,
                    current_graph=current_project.project.graph,
                    current_definitions=current_project.definitions,
                ):
                    previous = connection.execute(
                        "SELECT * FROM latest_results WHERE node_id = ?",
                        (current.node_id,),
                    ).fetchone()
                    previous_latest = (
                        None
                        if previous is None
                        else self._validated_latest_from_row(connection, previous)
                    )
                    if previous_latest is None or ended_at >= previous_latest.updated_at:
                        previous_output_ids = (
                            ()
                            if previous is None
                            else self._result_output_ids(
                                connection, cast(str, previous["result_id"])
                            )
                        )
                        self._set_latest_fresh(
                            connection,
                            node_id=current.node_id,
                            result_id=result.result_id,
                            updated_at=ended_at,
                        )
                        if (
                            previous is not None
                            and previous_latest is not None
                            and not previous_latest.stale
                            and previous["result_id"] != result.result_id
                            and previous_output_ids != output_ids
                        ):
                            self._mark_latest_stale_in_connection(
                                connection,
                                _downstream_node_ids(
                                    current_project.project.graph, current.node_id
                                ),
                                StaleReason.UPSTREAM_CHANGED,
                                ended_at,
                            )
                connection.commit()
        except RuntimeRepositoryError:
            raise
        except (ValidationError, sqlite3.IntegrityError) as error:
            raise RuntimeConflictError("E_RESULT_REGISTRATION_CONFLICT", str(error)) from error
        except sqlite3.Error as error:
            raise RuntimeRepositoryError("E_RESULT_REGISTRATION_FAILED", str(error)) from error
        return self.get_node_run(result.node_run_id)

    def reuse_result(
        self,
        node_run_id: str,
        result_id: str,
        *,
        completed_at: datetime,
    ) -> NodeRun:
        """让 pending attempt 引用精确匹配的历史 NodeResult，不生成新 Artifact。

        active Run 的历史复用不依赖 current/latest projection。只有 current Project 仍适用且尚无任何
        head 时才建立 projection；既有 fresh/stale head 都不会被旧历史候选倒退覆盖。
        """

        completed_at = _normalize_write_timestamp(completed_at, context="completed_at")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._read_node_run(connection, node_run_id)
            if current.state is not NodeRunState.PENDING:
                connection.rollback()
                raise RuntimeConflictError("E_REUSE_NODE_RUN_STATE", "只有 pending 可复用结果")
            result = self._read_result(connection, result_id)
            source = self._read_node_run(connection, result.node_run_id)
            source_run = self._read_run(connection, source.run_id)
            current_run = self._read_run(connection, current.run_id)
            self._assert_latest_attempt(connection, current)
            if (
                current_run.state is not RunState.RUNNING
                or current_run.started_at is None
                or completed_at < current_run.started_at
                or current.attempt != 1
                or source_run.project_id != current_run.project_id
                or source.state is not NodeRunState.COMPLETED
                or source.reused_from_result_id is not None
                or source.ended_at is None
                or source.ended_at >= current_run.created_at
                or source.ended_at > completed_at
                or source.node_id != current.node_id
                or source.definition_version != current.definition_version
                or source.input_artifact_ids != current.input_artifact_ids
                or source.output_artifact_ids != tuple(item.artifact_id for item in result.outputs)
                or not _node_snapshot_matches(
                    source_graph=source_run.graph_snapshot,
                    source_definitions=source_run.definitions_snapshot,
                    target_graph=current_run.graph_snapshot,
                    target_definitions=current_run.definitions_snapshot,
                    node_id=current.node_id,
                )
            ):
                connection.rollback()
                raise RuntimeConflictError(
                    "E_REUSE_BINDING_MISMATCH", "既有结果与当前 node/version/direct inputs 不一致"
                )
            output_ids = tuple(item.artifact_id for item in result.outputs)
            try:
                updated = current.model_copy(
                    update={
                        "state": NodeRunState.COMPLETED,
                        "started_at": completed_at,
                        "ended_at": completed_at,
                        "progress": 1.0,
                        "output_artifact_ids": output_ids,
                        "reused_from_result_id": result_id,
                    }
                )
            except ValidationError as model_error:
                connection.rollback()
                raise RuntimeConflictError(
                    "E_REUSE_FIELDS_INVALID", str(model_error)
                ) from model_error
            self._update_node_run(connection, current, updated)
            current_project = self._read_current_project(connection)
            if self._result_applies_to_current_project(
                connection,
                node_run=current,
                source_run=current_run,
                current_graph=current_project.project.graph,
                current_definitions=current_project.definitions,
            ):
                latest_row = connection.execute(
                    "SELECT * FROM latest_results WHERE node_id = ?",
                    (current.node_id,),
                ).fetchone()
                if latest_row is None:
                    self._set_latest_fresh(
                        connection,
                        node_id=current.node_id,
                        result_id=result_id,
                        updated_at=completed_at,
                    )
                    if output_ids:
                        self._mark_latest_stale_in_connection(
                            connection,
                            _downstream_node_ids(current_project.project.graph, current.node_id),
                            StaleReason.UPSTREAM_CHANGED,
                            completed_at,
                        )
                else:
                    # 即使不写 projection，也严格解析，损坏的 stale 值必须 fail closed。
                    self._validated_latest_from_row(connection, latest_row)
            connection.commit()
        return self.get_node_run(node_run_id)

    def get_result(self, result_id: str) -> NodeResult:
        """读取一个 NodeResult 及其全部有序 Artifact。"""

        with self._read_connection() as connection:
            result = self._read_result(connection, result_id)
            self._validate_public_result(connection, result)
            return result

    def list_results_for_node(
        self,
        node_id: str,
        *,
        before: datetime | None = None,
    ) -> tuple[NodeResult, ...]:
        """按完成时间 newest-first 返回节点的历史实际执行结果。

        ``before`` 是严格上界，适合 active Run 只选择其创建前已经完成的候选。相同完成时间以
        ``node_results.rowid`` newest-first 打破平局；复用 attempt 不会复制 NodeResult，因此不会
        产生重复候选。
        """

        parameters: tuple[Any, ...]
        where = "nr.node_id = ? AND nr.state = 'completed'"
        if before is None:
            parameters = (node_id,)
        else:
            before = _normalize_write_timestamp(before, context="before")
            where += " AND nr.ended_at < ?"
            parameters = (node_id, _timestamp(before, context="before"))
        with self._read_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT result.result_id
                FROM node_results AS result
                JOIN node_runs AS nr ON nr.node_run_id = result.node_run_id
                WHERE {where}
                ORDER BY nr.ended_at DESC, result.rowid DESC
                """,
                parameters,
            ).fetchall()
            results = tuple(self._read_result(connection, cast(str, row[0])) for row in rows)
            for result in results:
                self._validate_public_result(connection, result)
            return results

    def get_artifact(self, artifact_id: str) -> Artifact:
        """按随机身份读取一个已原子登记的 Artifact。"""

        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            if row is None:
                raise RuntimeNotFoundError(
                    "E_ARTIFACT_NOT_FOUND", f"Artifact 不存在：{artifact_id}"
                )
            artifact = self._artifact_from_row(row)
            result = self._read_result(connection, cast(str, row["result_id"]))
            self._validate_public_result(connection, result)
            if artifact not in result.outputs:
                raise RuntimeDataError(
                    "E_ARTIFACT_RESULT_RELATION_CORRUPT",
                    "Artifact 不属于其声明的 NodeResult",
                )
            return artifact

    def get_latest(self, node_id: str) -> LatestNodeResult | None:
        """读取当前 Project node 的 latest head；尚无结果时返回 ``None``。"""

        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM latest_results WHERE node_id = ?", (node_id,)
            ).fetchone()
            return None if row is None else self._validated_latest_from_row(connection, row)

    def list_latest(self) -> tuple[LatestNodeResult, ...]:
        """按首次建立 head 的 SQLite row 顺序读取全部 current/latest 投影。

        已从当前 Graph 删除的 node head 仍会返回并保持 stale，供 Project Service 展示和审计
        失效范围。
        """

        with self._read_connection() as connection:
            rows = connection.execute("SELECT * FROM latest_results ORDER BY rowid").fetchall()
            return tuple(self._validated_latest_from_row(connection, row) for row in rows)

    def mark_latest_stale(
        self,
        node_ids: Iterable[str],
        reason: StaleReason,
        *,
        updated_at: datetime,
    ) -> tuple[LatestNodeResult, ...]:
        """只更新 latest 投影；历史 NodeRun 与 NodeResult 保持不可变。"""

        updated_at = _normalize_write_timestamp(updated_at, context="updated_at")
        ordered_ids = tuple(dict.fromkeys(node_ids))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._mark_latest_stale_in_connection(connection, ordered_ids, reason, updated_at)
            rows = [
                row
                for node_id in ordered_ids
                if (
                    row := connection.execute(
                        "SELECT * FROM latest_results WHERE node_id = ?", (node_id,)
                    ).fetchone()
                )
                is not None
            ]
            latest = tuple(self._validated_latest_from_row(connection, row) for row in rows)
            connection.commit()
            return latest

    def mark_downstream_stale(
        self,
        node_id: str,
        reason: StaleReason,
        *,
        updated_at: datetime,
        include_self: bool = False,
    ) -> tuple[LatestNodeResult, ...]:
        """在一个事务中读取 current Graph 并标记下游 latest heads stale。"""

        updated_at = _normalize_write_timestamp(updated_at, context="updated_at")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            graph = self._read_current_project(connection).project.graph
            node_ids = _downstream_node_ids(graph, node_id)
            if include_self:
                node_ids = (node_id, *node_ids)
            self._mark_latest_stale_in_connection(connection, node_ids, reason, updated_at)
            rows = [
                row
                for item in node_ids
                if (
                    row := connection.execute(
                        "SELECT * FROM latest_results WHERE node_id = ?", (item,)
                    ).fetchone()
                )
                is not None
            ]
            latest = tuple(self._validated_latest_from_row(connection, row) for row in rows)
            connection.commit()
            return latest

    def mark_rerun_stale(
        self,
        node_id: str,
        *,
        updated_at: datetime,
    ) -> tuple[LatestNodeResult, ...]:
        """原子区分显式 rerun 节点与因其变化而失效的下游 latest heads。"""

        updated_at = _normalize_write_timestamp(updated_at, context="updated_at")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            graph = self._read_current_project(connection).project.graph
            if node_id not in {node.node_id for node in graph.nodes}:
                raise RuntimeNotFoundError(
                    "E_RERUN_NODE_NOT_IN_CURRENT_GRAPH",
                    f"当前 Project 不含节点 {node_id!r}",
                )
            downstream = _downstream_node_ids(graph, node_id)
            self._mark_latest_stale_in_connection(
                connection,
                (node_id,),
                StaleReason.RERUN_REQUESTED,
                updated_at,
            )
            self._mark_latest_stale_in_connection(
                connection,
                downstream,
                StaleReason.UPSTREAM_CHANGED,
                updated_at,
            )
            node_ids = (node_id, *downstream)
            rows = [
                row
                for item in node_ids
                if (
                    row := connection.execute(
                        "SELECT * FROM latest_results WHERE node_id = ?", (item,)
                    ).fetchone()
                )
                is not None
            ]
            latest = tuple(self._validated_latest_from_row(connection, row) for row in rows)
            connection.commit()
            return latest

    def recover_interrupted(
        self,
        *,
        recovered_at: datetime,
        final_progress: Mapping[str, _InterruptedProgress] | None = None,
    ) -> tuple[NodeRun, ...]:
        """将遗留 running attempt 原子失败为 interrupted，保留 waiting_external。

        进程内 reporter 可能持有尚未达到限频持久化门槛的最后可信 fraction。调用方可按
        ``node_run_id -> (run_id, attempt, fraction)`` 传入这些 sample；Repository 会在同一事务中
        重新绑定完整 attempt identity、拒绝回退或未知目标，并把最后 fraction 与 failed 终态
        一起提交。
        """

        recovered_at = _normalize_write_timestamp(recovered_at, context="recovered_at")
        progress_samples = dict(final_progress or {})
        recovered_ids: list[str] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT * FROM node_runs WHERE state = 'running' ORDER BY rowid"
            ).fetchall()
            currents = tuple(self._node_run_from_row(row) for row in rows)
            parent_runs = {
                current.run_id: self._read_run(connection, current.run_id) for current in currents
            }
            if any(run.state is not RunState.RUNNING for run in parent_runs.values()):
                raise RuntimeDataError(
                    "E_NODE_RUN_PARENT_NOT_RUNNING",
                    "遗留 running NodeRun 的 parent Run 必须仍为 running",
                )
            currents_by_id = {current.node_run_id: current for current in currents}
            unknown_ids = set(progress_samples).difference(currents_by_id)
            if unknown_ids:
                connection.rollback()
                raise RuntimeConflictError(
                    "E_PROGRESS_RECOVERY_TARGET",
                    "恢复进度只允许绑定本事务中的遗留 running attempt",
                )
            for current in currents:
                recovered_progress = current.progress
                sample = progress_samples.get(current.node_run_id)
                if sample is not None:
                    if type(sample) is not tuple or len(sample) != 3:
                        connection.rollback()
                        raise RuntimeConflictError(
                            "E_PROGRESS_RECOVERY_BINDING",
                            "恢复进度必须携带完整 run_id/attempt/fraction",
                        )
                    run_id, attempt, fraction = sample
                    self._assert_progress_target(
                        connection,
                        current,
                        run_id=run_id,
                        attempt=attempt,
                    )
                    recovered_progress = _validated_progress(fraction)
                    if current.progress is not None and recovered_progress < current.progress:
                        connection.rollback()
                        raise RuntimeConflictError(
                            "E_NODE_RUN_PROGRESS_REGRESSION",
                            "恢复终态不得把最后可信 progress 向后改写",
                        )
                updated = current.model_copy(
                    update={
                        "state": NodeRunState.FAILED,
                        "ended_at": recovered_at,
                        "progress": recovered_progress,
                        "error": RuntimeFailure(
                            reason=FailureReason.INTERRUPTED,
                            message="应用重启时发现遗留 running attempt；只能从头重跑",
                        ),
                    }
                )
                self._update_node_run(connection, current, updated)
                recovered_ids.append(current.node_run_id)
            connection.commit()
        return tuple(self.get_node_run(item) for item in recovered_ids)

    def _read_current_project(self, connection: sqlite3.Connection) -> ProjectSnapshot:
        """在调用方事务中严格读取并验证 current Project authority。"""

        snapshot = self._store._read_snapshot(connection)
        return self._store._validated_snapshot(snapshot.project, snapshot.definitions)

    def _validate_new_run_snapshot(
        self,
        connection: sqlite3.Connection,
        run: Run,
        *,
        ignore_ui_position: bool = False,
    ) -> None:
        current = self._read_current_project(connection)
        if run.project_id != current.project.project_id:
            raise RuntimeConflictError("E_RUN_PROJECT_MISMATCH", "Run 没有绑定当前 Project")
        graph_matches = run.graph_snapshot == current.project.graph
        if ignore_ui_position:
            # 同一 Run 重跑不因画布位置改变分支；新 Run 默认仍精确复制整张 Graph。
            omitted = {"nodes": {"__all__": {"ui_position"}}}
            graph_matches = run.graph_snapshot.model_dump(
                exclude=omitted
            ) == current.project.graph.model_dump(exclude=omitted)
        if not graph_matches or run.definitions_snapshot != current.definitions:
            raise RuntimeConflictError(
                "E_RUN_SNAPSHOT_MISMATCH",
                "Run 必须精确复制当前 Project graph/definitions",
            )

    @staticmethod
    def _validate_bulk_node_runs(
        connection: sqlite3.Connection,
        run: Run,
        node_runs: Sequence[NodeRun],
        *,
        expected_attempts: dict[str, int],
    ) -> None:
        """在任何 INSERT 前完整校验一组 attempt，避免依赖中途约束回滚。"""

        node_versions = {item.node_id: item.definition_version for item in run.graph_snapshot.nodes}
        identities = tuple(item.node_run_id for item in node_runs)
        if len(identities) != len(set(identities)):
            raise RuntimeConflictError(
                "E_NODE_RUN_ID_CONFLICT", "批量 attempts 的 node_run_id 不得重复"
            )
        candidate_work_dirs = tuple(_work_dir_identity(item.work_dir) for item in node_runs)
        normalized_candidates = tuple(item for item in candidate_work_dirs if item is not None)
        if any(item is None for item in candidate_work_dirs) or _work_dir_collection_overlaps(
            normalized_candidates
        ):
            raise RuntimeConflictError(
                "E_NODE_RUN_WORK_DIR_CONFLICT",
                "批量 attempts 必须使用互不别名且互不嵌套的独立 work_dir",
            )
        existing_work_dir_values = tuple(
            _work_dir_identity(cast(str, row[0]))
            for row in connection.execute("SELECT work_dir FROM node_runs")
        )
        if any(item is None for item in existing_work_dir_values):
            raise RuntimeDataError(
                "E_NODE_RUN_WORK_DIR_RELATION_CORRUPT",
                "既有 NodeRun work_dir 无法规范化",
            )
        existing_work_dirs = tuple(cast(str, item) for item in existing_work_dir_values)
        if _work_dir_collection_overlaps(existing_work_dirs):
            raise RuntimeDataError(
                "E_NODE_RUN_WORK_DIR_RELATION_CORRUPT",
                "既有 NodeRun work_dir 相同或发生祖先目录重叠",
            )
        if any(
            _work_dirs_overlap(candidate, existing)
            for candidate in normalized_candidates
            for existing in existing_work_dirs
        ):
            raise RuntimeConflictError(
                "E_NODE_RUN_WORK_DIR_CONFLICT",
                "work_dir 与当前 Project 历史 attempt 相同或发生祖先目录重叠",
            )
        for node_run in node_runs:
            if node_run.state is not NodeRunState.PENDING:
                raise RuntimeConflictError("E_NODE_RUN_CREATE_STATE", "新 NodeRun 必须是 pending")
            if node_run.run_id != run.run_id:
                raise RuntimeConflictError("E_NODE_RUN_RUN_MISMATCH", "NodeRun 没有绑定目标 Run")
            if node_versions.get(node_run.node_id) != node_run.definition_version:
                raise RuntimeConflictError(
                    "E_NODE_RUN_BINDING_MISMATCH",
                    "NodeRun 没有绑定 Run snapshot 节点版本",
                )
            if node_run.created_at < run.created_at:
                raise RuntimeConflictError("E_NODE_RUN_TIME_INVALID", "NodeRun 不得早于 Run 创建")
            expected = expected_attempts.get(node_run.node_id)
            if expected is None or node_run.attempt != expected:
                raise RuntimeConflictError(
                    "E_NODE_RUN_ATTEMPT_NON_CONTIGUOUS",
                    f"{node_run.node_id!r} attempt 必须为 {expected}",
                )
            if (
                connection.execute(
                    "SELECT 1 FROM node_runs WHERE node_run_id = ?",
                    (node_run.node_run_id,),
                ).fetchone()
                is not None
            ):
                raise RuntimeConflictError(
                    "E_NODE_RUN_ID_CONFLICT",
                    f"NodeRun identity 已存在：{node_run.node_run_id}",
                )
            missing_inputs = [
                artifact_id
                for artifact_id in dict.fromkeys(node_run.input_artifact_ids)
                if connection.execute(
                    "SELECT 1 FROM artifacts WHERE artifact_id = ?", (artifact_id,)
                ).fetchone()
                is None
            ]
            if missing_inputs:
                raise RuntimeConflictError(
                    "E_NODE_RUN_INPUT_UNKNOWN",
                    f"输入 Artifact 不存在：{missing_inputs}",
                )

    @staticmethod
    def _insert_run(connection: sqlite3.Connection, run: Run) -> None:
        connection.execute(
            """
            INSERT INTO runs(
                run_id, project_id, state, graph_snapshot_json,
                definitions_snapshot_json, selected_targets_json,
                created_at, started_at, ended_at, error_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.run_id,
                run.project_id,
                run.state.value,
                _dump_json(run.graph_snapshot.model_dump(mode="json")),
                _dump_json([item.model_dump(mode="json") for item in run.definitions_snapshot]),
                _dump_json(list(run.selected_targets)),
                _timestamp(run.created_at, context="Run.created_at"),
                None
                if run.started_at is None
                else _timestamp(run.started_at, context="Run.started_at"),
                None if run.ended_at is None else _timestamp(run.ended_at, context="Run.ended_at"),
                None if run.error is None else _dump_json(run.error.model_dump(mode="json")),
            ),
        )

    def _result_applies_to_current_project(
        self,
        connection: sqlite3.Connection,
        *,
        node_run: NodeRun,
        source_run: Run,
        current_graph: Graph,
        current_definitions: tuple[NodeDefinition, ...],
    ) -> bool:
        """判断历史结果是否可投影为 current Project 的 fresh head。"""

        latest_attempt = connection.execute(
            "SELECT max(attempt) FROM node_runs WHERE run_id = ? AND node_id = ?",
            (node_run.run_id, node_run.node_id),
        ).fetchone()[0]
        if latest_attempt != node_run.attempt:
            return False
        if not _node_snapshot_matches(
            source_graph=source_run.graph_snapshot,
            source_definitions=source_run.definitions_snapshot,
            target_graph=current_graph,
            target_definitions=current_definitions,
            node_id=node_run.node_id,
        ):
            return False
        expected_inputs = self._current_fresh_input_artifact_ids(
            connection, current_graph, node_run.node_id
        )
        return expected_inputs is not None and expected_inputs == node_run.input_artifact_ids

    def _current_fresh_input_artifact_ids(
        self,
        connection: sqlite3.Connection,
        graph: Graph,
        node_id: str,
    ) -> tuple[str, ...] | None:
        """按每条 current 入边解析 fresh upstream source-port Artifact identity。"""

        artifact_ids: list[str] = []
        for edge in _ordered_incoming_edges(graph, node_id):
            latest_row = connection.execute(
                "SELECT * FROM latest_results WHERE node_id = ?",
                (edge.source_node_id,),
            ).fetchone()
            if latest_row is None:
                return None
            latest = self._validated_latest_from_row(connection, latest_row)
            if latest.stale:
                return None
            result = self._read_result(connection, latest.result_id)
            source = self._read_node_run(connection, result.node_run_id)
            if source.state is not NodeRunState.COMPLETED or source.node_id != edge.source_node_id:
                return None
            matches = tuple(
                artifact
                for artifact in result.outputs
                if artifact.producer_port_id == edge.source_port_id
            )
            if len(matches) != 1:
                return None
            artifact_ids.append(matches[0].artifact_id)
        return tuple(artifact_ids)

    def _result_output_ids(
        self,
        connection: sqlite3.Connection,
        result_id: str,
    ) -> tuple[str, ...]:
        return tuple(item.artifact_id for item in self._read_result(connection, result_id).outputs)

    @staticmethod
    def _definition_for_run_node(run: Run, node_id: str) -> NodeDefinition | None:
        node = next(
            (item for item in run.graph_snapshot.nodes if item.node_id == node_id),
            None,
        )
        if node is None:
            return None
        return next(
            (
                item
                for item in run.definitions_snapshot
                if item.type_id == node.type_id and item.version == node.definition_version
            ),
            None,
        )

    @staticmethod
    def _handoff_contract_matches(
        definition: NodeDefinition,
        node_run: NodeRun,
        handoff: ExternalHandoff,
        started_at: datetime,
    ) -> bool:
        """验证 manual handoff 的 snapshot authority 与 attempt 目录 containment。"""

        if definition.execution_mode is not ExecutionMode.MANUAL_EXTERNAL or not isinstance(
            definition.executor, ManualExternalExecutorSpec
        ):
            return False
        expected_outputs = tuple((port.port_id, None) for port in definition.output_ports)
        actual_outputs = tuple(
            (target.port_id, target.ordinal) for target in handoff.output_targets
        )
        return (
            handoff.node_run_id == node_run.node_run_id
            and handoff.input_artifact_ids == node_run.input_artifact_ids
            and actual_outputs == expected_outputs
            and handoff.instructions == definition.executor.instructions
            and handoff.created_at == started_at
            and all(
                _path_is_strictly_within(target.path, node_run.work_dir)
                for target in handoff.output_targets
            )
        )

    @staticmethod
    def _result_output_contract_matches(
        run: Run,
        node_run: NodeRun,
        result: NodeResult,
    ) -> bool:
        definition = RuntimeRepository._definition_for_run_node(run, node_run.node_id)
        if definition is None:
            return False
        expected = tuple((port.port_id, port.data_type, None) for port in definition.output_ports)
        actual = tuple(
            (artifact.producer_port_id, artifact.kind, artifact.ordinal)
            for artifact in result.outputs
        )
        return actual == expected

    def _expected_run_input_artifact_ids(
        self,
        connection: sqlite3.Connection,
        run: Run,
        node_id: str,
    ) -> tuple[str, ...]:
        """按 Run snapshot 入边稳定顺序解析当前 completed 上游 attempt 的 typed outputs。"""

        latest_attempts: dict[str, NodeRun] = {}
        for item in run.node_runs:
            previous = latest_attempts.get(item.node_id)
            if previous is None or item.attempt > previous.attempt:
                latest_attempts[item.node_id] = item
        target_definition = self._definition_for_run_node(run, node_id)
        if target_definition is None:
            raise RuntimeDataError(
                "E_NODE_RUN_INPUT_CONTRACT_CORRUPT",
                f"Run snapshot 缺少节点 {node_id!r} 的 NodeDefinition",
            )
        target_ports = {port.port_id: port for port in target_definition.input_ports}
        artifact_ids: list[str] = []
        for edge in _ordered_incoming_edges(run.graph_snapshot, node_id):
            source_attempt = latest_attempts.get(edge.source_node_id)
            if source_attempt is None or source_attempt.state is not NodeRunState.COMPLETED:
                raise RuntimeConflictError(
                    "E_NODE_RUN_INPUT_SOURCE_NOT_COMPLETED",
                    f"上游 {edge.source_node_id!r} 的当前 attempt 尚未 completed",
                )
            source_definition = self._definition_for_run_node(run, edge.source_node_id)
            if source_definition is None:
                raise RuntimeDataError(
                    "E_NODE_RUN_INPUT_CONTRACT_CORRUPT",
                    f"Run snapshot 缺少上游 {edge.source_node_id!r} 的 NodeDefinition",
                )
            source_port = next(
                (
                    port
                    for port in source_definition.output_ports
                    if port.port_id == edge.source_port_id
                ),
                None,
            )
            target_port = target_ports.get(edge.target_port_id)
            if source_port is None or target_port is None:
                raise RuntimeDataError(
                    "E_NODE_RUN_INPUT_CONTRACT_CORRUPT",
                    "Run snapshot edge 引用未知端口",
                )
            artifacts: list[Artifact] = []
            for artifact_id in source_attempt.output_artifact_ids:
                row = connection.execute(
                    "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
                ).fetchone()
                if row is None:
                    raise RuntimeDataError(
                        "E_NODE_RUN_INPUT_ARTIFACT_MISSING",
                        f"上游 Artifact 不存在：{artifact_id}",
                    )
                artifact = self._artifact_from_row(row)
                if artifact.producer_port_id == edge.source_port_id:
                    artifacts.append(artifact)
            if (
                len(artifacts) != 1
                or artifacts[0].kind != source_port.data_type
                or artifacts[0].kind != target_port.data_type
                or artifacts[0].ordinal is not None
            ):
                raise RuntimeDataError(
                    "E_NODE_RUN_INPUT_CONTRACT_CORRUPT",
                    "上游 source port 必须精确对应一个 typed、无 ordinal Artifact",
                )
            artifact_ids.append(artifacts[0].artifact_id)
        return tuple(artifact_ids)

    def _validate_result_output_contract(
        self,
        run: Run,
        node_run: NodeRun,
        result: NodeResult,
    ) -> None:
        if not self._result_output_contract_matches(run, node_run, result):
            raise RuntimeDataError(
                "E_RESULT_OUTPUT_CONTRACT_CORRUPT",
                "NodeResult outputs 不符合其 Run snapshot NodeDefinition",
            )

    @staticmethod
    def _read_run_snapshot(
        connection: sqlite3.Connection,
        run_id: str,
    ) -> tuple[str, Graph, tuple[NodeDefinition, ...]]:
        """不递归读取 NodeRun 关系，只严格重建一个 Run 的 snapshot 身份。"""

        row = connection.execute(
            "SELECT project_id, graph_snapshot_json, definitions_snapshot_json "
            "FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise RuntimeNotFoundError("E_RUN_NOT_FOUND", f"Run 不存在：{run_id}")
        graph_payload = _load_json(row["graph_snapshot_json"], context="graph_snapshot")
        definitions_payload = _load_json(
            row["definitions_snapshot_json"], context="definitions_snapshot"
        )
        if not isinstance(definitions_payload, list):
            raise RuntimeDataError(
                "E_RUNTIME_DATA_CORRUPT", "definitions_snapshot 必须是 JSON array"
            )
        definitions_items: list[NodeDefinition] = []
        for item in definitions_payload:
            if not isinstance(item, dict):
                raise RuntimeDataError(
                    "E_RUNTIME_DATA_CORRUPT",
                    "definition snapshot member 必须是 object",
                )
            definitions_items.append(
                _decode_model(NodeDefinition, item, context="definition snapshot")
            )
        definitions = tuple(definitions_items)
        graph = _decode_model(Graph, graph_payload, context="graph snapshot")
        try:
            GraphValidator(definitions).validate(graph)
        except GraphValidationError as error:
            raise RuntimeDataError(
                "E_RUNTIME_DATA_CORRUPT", f"Run snapshot graph 无效：{error}"
            ) from error
        return cast(str, row["project_id"]), graph, definitions

    def _validate_historical_input_binding(
        self,
        connection: sqlite3.Connection,
        run: Run,
        node_run: NodeRun,
    ) -> None:
        """按目标启动时点重建有序 typed inputs，允许后续上游 rerun 保留旧历史。"""

        incoming = _ordered_incoming_edges(run.graph_snapshot, node_run.node_id)
        if not node_run.input_artifact_ids:
            cancelled_before_ready = (
                node_run.state is NodeRunState.FAILED
                and node_run.error is not None
                and node_run.error.reason is FailureReason.CANCELLED
            )
            if (
                incoming
                and node_run.state is not NodeRunState.PENDING
                and not cancelled_before_ready
            ):
                raise RuntimeDataError(
                    "E_NODE_RUN_INPUT_RELATION_CORRUPT",
                    "已启动的 NodeRun 必须完整保存 snapshot 入边绑定",
                )
            return
        if len(node_run.input_artifact_ids) != len(incoming):
            raise RuntimeDataError(
                "E_NODE_RUN_INPUT_RELATION_CORRUPT",
                "NodeRun input 数量与 snapshot 入边不一致",
            )

        target_definition = self._definition_for_run_node(run, node_run.node_id)
        if target_definition is None:
            raise RuntimeDataError(
                "E_NODE_RUN_INPUT_RELATION_CORRUPT",
                "Run snapshot 缺少 target NodeDefinition",
            )
        target_ports = {item.port_id: item for item in target_definition.input_ports}
        for artifact_id, edge in zip(node_run.input_artifact_ids, incoming, strict=True):
            artifact_row = connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            if artifact_row is None:
                raise RuntimeDataError(
                    "E_NODE_RUN_INPUT_RELATION_CORRUPT",
                    f"输入 Artifact 不存在：{artifact_id}",
                )
            artifact = self._artifact_from_row(artifact_row)
            source_attempts = tuple(
                item for item in run.node_runs if item.node_id == edge.source_node_id
            )
            if node_run.started_at is None:
                eligible_sources = tuple(
                    item
                    for item in source_attempts
                    if item.state is NodeRunState.COMPLETED
                    and artifact_id in item.output_artifact_ids
                )
                source = (
                    None
                    if not eligible_sources
                    else max(eligible_sources, key=lambda item: item.attempt)
                )
            else:
                attempts_at_start = tuple(
                    item for item in source_attempts if item.created_at <= node_run.started_at
                )
                source = (
                    None
                    if not attempts_at_start
                    else max(attempts_at_start, key=lambda item: item.attempt)
                )
            source_definition = self._definition_for_run_node(run, edge.source_node_id)
            source_port = (
                None
                if source_definition is None
                else next(
                    (
                        item
                        for item in source_definition.output_ports
                        if item.port_id == edge.source_port_id
                    ),
                    None,
                )
            )
            target_port = target_ports.get(edge.target_port_id)
            if (
                source is None
                or source.state is not NodeRunState.COMPLETED
                or source.ended_at is None
                or (node_run.started_at is not None and source.ended_at > node_run.started_at)
                or source_port is None
                or target_port is None
                or artifact.producer_port_id != edge.source_port_id
                or artifact.kind != source_port.data_type
                or artifact.kind != target_port.data_type
                or artifact.ordinal is not None
                or artifact.artifact_id not in source.output_artifact_ids
            ):
                raise RuntimeDataError(
                    "E_NODE_RUN_INPUT_RELATION_CORRUPT",
                    "输入 Artifact 的 Run/source port/type/ordinal 关系无效",
                )
            result = self._read_result(connection, cast(str, artifact_row["result_id"]))
            if (
                artifact not in result.outputs
                or (
                    source.reused_from_result_id is None
                    and result.node_run_id != source.node_run_id
                )
                or (
                    source.reused_from_result_id is not None
                    and result.result_id != source.reused_from_result_id
                )
            ):
                raise RuntimeDataError(
                    "E_NODE_RUN_INPUT_RELATION_CORRUPT",
                    "输入 Artifact 不属于其 source attempt 的 own Result",
                )

    def _validate_public_result(
        self,
        connection: sqlite3.Connection,
        result: NodeResult,
    ) -> None:
        """让局部 Result/Artifact/latest 读取复用完整 Run 关系校验。"""

        owner = self._read_node_run(connection, result.node_run_id)
        run = self._read_run(connection, owner.run_id)
        if not any(
            item.node_run_id == owner.node_run_id and item.state is NodeRunState.COMPLETED
            for item in run.node_runs
        ):
            raise RuntimeDataError(
                "E_RESULT_OWNER_RELATION_CORRUPT",
                "NodeResult owner 必须是其 Run 中的 completed NodeRun",
            )

    def _validated_latest_from_row(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> LatestNodeResult:
        """严格重建 latest，并验证其 Result owner 确实属于同一 node。"""

        latest = self._latest_from_row(row)
        result = self._read_result(connection, latest.result_id)
        self._validate_public_result(connection, result)
        owner = self._read_node_run(connection, result.node_run_id)
        if (
            owner.state is not NodeRunState.COMPLETED
            or owner.node_id != latest.node_id
            or owner.ended_at is None
            or latest.updated_at < owner.ended_at
        ):
            raise RuntimeDataError(
                "E_LATEST_RESULT_RELATION_CORRUPT",
                "latest 必须引用同 node completed owner，且时间不得早于 producer ended_at",
            )
        return latest

    @staticmethod
    def _insert_node_run(connection: sqlite3.Connection, node_run: NodeRun) -> None:
        connection.execute(
            """
            INSERT INTO node_runs(
                node_run_id, run_id, node_id, definition_version, attempt, state,
                input_artifact_ids_json, output_artifact_ids_json, created_at, work_dir,
                started_at, ended_at, progress, exit_code, log_path, error_json,
                reused_from_result_id, external_handoff_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            RuntimeRepository._node_run_values(node_run),
        )

    @staticmethod
    def _assert_latest_attempt(
        connection: sqlite3.Connection,
        node_run: NodeRun,
    ) -> None:
        """阻止旧 pending/handoff 在更高 attempt 已建立后重新变成活动历史。"""

        latest = connection.execute(
            "SELECT max(attempt) FROM node_runs WHERE run_id = ? AND node_id = ?",
            (node_run.run_id, node_run.node_id),
        ).fetchone()[0]
        if latest != node_run.attempt:
            raise RuntimeConflictError(
                "E_NODE_RUN_ATTEMPT_SUPERSEDED",
                "已有更高 attempt，旧 NodeRun 不得再迁移、绑定、失败或复用",
            )

    def _assert_progress_target(
        self,
        connection: sqlite3.Connection,
        node_run: NodeRun,
        *,
        run_id: str,
        attempt: int,
    ) -> None:
        """在一个 SQLite snapshot/事务内验证 reporter 的完整 authority binding。"""

        if (
            type(run_id) is not str
            or not run_id
            or type(attempt) is not int
            or attempt < 1
            or node_run.run_id != run_id
            or node_run.attempt != attempt
        ):
            raise RuntimeConflictError(
                "E_PROGRESS_BINDING",
                "reporter 的 run_id/node_run_id/attempt 与持久 attempt 不一致",
            )
        run = self._read_run(connection, run_id)
        if run.state is not RunState.RUNNING:
            raise RuntimeConflictError(
                "E_PROGRESS_PARENT_NOT_RUNNING",
                "只有 running Run 的 attempt 可以接受 progress",
            )
        latest = connection.execute(
            "SELECT max(attempt) FROM node_runs WHERE run_id = ? AND node_id = ?",
            (node_run.run_id, node_run.node_id),
        ).fetchone()[0]
        if latest != node_run.attempt:
            raise RuntimeConflictError(
                "E_PROGRESS_ATTEMPT_SUPERSEDED",
                "reporter 绑定的 attempt 已被更高 attempt 取代",
            )
        if node_run.state is not NodeRunState.RUNNING:
            raise RuntimeConflictError(
                "E_PROGRESS_NOT_RUNNING",
                "只有最新 running attempt 可以接受 progress",
            )

    @staticmethod
    def _update_node_run(
        connection: sqlite3.Connection,
        current: NodeRun,
        updated: NodeRun,
        *,
        allow_input_binding: bool = False,
    ) -> None:
        immutable = (
            "node_run_id",
            "run_id",
            "node_id",
            "definition_version",
            "attempt",
            "created_at",
            "work_dir",
        )
        if any(getattr(current, field) != getattr(updated, field) for field in immutable):
            raise RuntimeConflictError(
                "E_NODE_RUN_IDENTITY_MUTATION", "NodeRun attempt identity 与输入不得改写"
            )
        if current.input_artifact_ids != updated.input_artifact_ids and not (
            allow_input_binding
            and current.state is NodeRunState.PENDING
            and updated.state is NodeRunState.PENDING
            and not current.input_artifact_ids
        ):
            raise RuntimeConflictError(
                "E_NODE_RUN_INPUT_MUTATION", "NodeRun inputs 只允许在 pending 时首次绑定"
            )
        values = RuntimeRepository._node_run_values(updated)
        changed = connection.execute(
            """
            UPDATE node_runs SET
                run_id = ?, node_id = ?, definition_version = ?, attempt = ?, state = ?,
                input_artifact_ids_json = ?, output_artifact_ids_json = ?, created_at = ?,
                work_dir = ?, started_at = ?, ended_at = ?, progress = ?, exit_code = ?,
                log_path = ?, error_json = ?, reused_from_result_id = ?, external_handoff_json = ?
            WHERE node_run_id = ? AND state = ?
            """,
            (*values[1:], current.node_run_id, current.state.value),
        ).rowcount
        if changed != 1:
            raise RuntimeConflictError("E_NODE_RUN_TRANSITION_RACE", "NodeRun 状态已被并发修改")

    @staticmethod
    def _node_run_values(node_run: NodeRun) -> tuple[Any, ...]:
        return (
            node_run.node_run_id,
            node_run.run_id,
            node_run.node_id,
            node_run.definition_version,
            node_run.attempt,
            node_run.state.value,
            _dump_json(list(node_run.input_artifact_ids)),
            _dump_json(list(node_run.output_artifact_ids)),
            _timestamp(node_run.created_at),
            node_run.work_dir,
            None if node_run.started_at is None else _timestamp(node_run.started_at),
            None if node_run.ended_at is None else _timestamp(node_run.ended_at),
            node_run.progress,
            node_run.exit_code,
            node_run.log_path,
            None if node_run.error is None else _dump_json(node_run.error.model_dump(mode="json")),
            node_run.reused_from_result_id,
            None
            if node_run.external_handoff is None
            else _dump_json(node_run.external_handoff.model_dump(mode="json")),
        )

    @staticmethod
    def _insert_artifact(
        connection: sqlite3.Connection,
        result_id: str,
        artifact: Artifact,
    ) -> None:
        frame_start = None
        frame_end = None
        if artifact.frame_range is not None:
            frame_start = artifact.frame_range.start_frame
            frame_end = artifact.frame_range.end_frame
        connection.execute(
            """
            INSERT INTO artifacts(
                artifact_id, result_id, kind, path, producer_node_run_id,
                producer_port_id, ordinal, frame_start, frame_end,
                media_info_json, size, mtime_ns
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact.artifact_id,
                result_id,
                artifact.kind,
                artifact.path,
                artifact.producer_node_run_id,
                artifact.producer_port_id,
                artifact.ordinal,
                frame_start,
                frame_end,
                _dump_json(artifact.media_info),
                artifact.size,
                artifact.mtime_ns,
            ),
        )

    @staticmethod
    def _set_latest_fresh(
        connection: sqlite3.Connection,
        *,
        node_id: str,
        result_id: str,
        updated_at: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO latest_results(node_id, result_id, stale, stale_reason, updated_at)
            VALUES (?, ?, 0, NULL, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                result_id = excluded.result_id,
                stale = 0,
                stale_reason = NULL,
                updated_at = excluded.updated_at
            """,
            (node_id, result_id, _timestamp(updated_at)),
        )

    @staticmethod
    def _set_latest_stale(
        connection: sqlite3.Connection,
        *,
        node_id: str,
        result_id: str,
        reason: StaleReason,
        updated_at: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO latest_results(node_id, result_id, stale, stale_reason, updated_at)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                result_id = excluded.result_id,
                stale = 1,
                stale_reason = excluded.stale_reason,
                updated_at = excluded.updated_at
            """,
            (node_id, result_id, reason.value, _timestamp(updated_at)),
        )

    def _mark_latest_stale_in_connection(
        self,
        connection: sqlite3.Connection,
        node_ids: Sequence[str],
        reason: StaleReason,
        updated_at: datetime,
    ) -> None:
        ordered_ids = tuple(dict.fromkeys(node_ids))
        for node_id in ordered_ids:
            row = connection.execute(
                "SELECT * FROM latest_results WHERE node_id = ?", (node_id,)
            ).fetchone()
            if row is not None:
                latest = self._validated_latest_from_row(connection, row)
                if updated_at < latest.updated_at:
                    raise RuntimeConflictError(
                        "E_LATEST_TIME_REGRESSION",
                        "stale updated_at 不得早于现有 latest.updated_at",
                    )
        connection.executemany(
            """
            UPDATE latest_results
            SET stale = 1, stale_reason = ?, updated_at = ?
            WHERE node_id = ? AND stale = 0
            """,
            ((reason.value, _timestamp(updated_at), node_id) for node_id in ordered_ids),
        )
        for node_id in ordered_ids:
            row = connection.execute(
                "SELECT * FROM latest_results WHERE node_id = ?", (node_id,)
            ).fetchone()
            if row is not None:
                self._validated_latest_from_row(connection, row)

    def _read_run(self, connection: sqlite3.Connection, run_id: str) -> Run:
        row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise RuntimeNotFoundError("E_RUN_NOT_FOUND", f"Run 不存在：{run_id}")
        node_rows = connection.execute(
            "SELECT * FROM node_runs WHERE run_id = ? ORDER BY rowid", (run_id,)
        ).fetchall()
        graph_payload = _load_json(row["graph_snapshot_json"], context="graph_snapshot")
        definitions_payload = _load_json(
            row["definitions_snapshot_json"], context="definitions_snapshot"
        )
        if not isinstance(definitions_payload, list):
            raise RuntimeDataError(
                "E_RUNTIME_DATA_CORRUPT", "definitions_snapshot 必须是 JSON array"
            )
        definitions: list[NodeDefinition] = []
        for item in definitions_payload:
            if not isinstance(item, dict):
                raise RuntimeDataError(
                    "E_RUNTIME_DATA_CORRUPT", "definition snapshot member 必须是 object"
                )
            definitions.append(_decode_model(NodeDefinition, item, context="definition snapshot"))
        graph = _decode_model(Graph, graph_payload, context="graph snapshot")
        selected_targets = _load_json(row["selected_targets_json"], context="selected_targets")
        if not isinstance(selected_targets, list):
            raise RuntimeDataError("E_RUNTIME_DATA_CORRUPT", "selected_targets 必须是 JSON array")
        error = None
        if row["error_json"] is not None:
            error_payload = _load_json(row["error_json"], context="Run.error")
            if not isinstance(error_payload, dict):
                raise RuntimeDataError("E_RUNTIME_DATA_CORRUPT", "Run.error 必须是 object")
            error = _decode_model(RuntimeFailure, error_payload, context="Run.error")
        node_runs = tuple(self._node_run_from_row(item) for item in node_rows)
        try:
            run = Run(
                run_id=row["run_id"],
                project_id=row["project_id"],
                graph_snapshot=graph,
                definitions_snapshot=tuple(definitions),
                selected_targets=tuple(selected_targets),
                state=RunState(row["state"]),
                node_runs=node_runs,
                created_at=_parse_timestamp(row["created_at"], context="Run.created_at"),
                started_at=None
                if row["started_at"] is None
                else _parse_timestamp(row["started_at"], context="Run.started_at"),
                ended_at=None
                if row["ended_at"] is None
                else _parse_timestamp(row["ended_at"], context="Run.ended_at"),
                error=error,
            )
        except (ValidationError, ValueError, TypeError) as model_error:
            raise RuntimeDataError("E_RUNTIME_DATA_CORRUPT", f"Run: {model_error}") from model_error
        self._validate_node_run_result_relationships(connection, run)
        return run

    def _validate_node_run_result_relationships(
        self,
        connection: sqlite3.Connection,
        run: Run,
    ) -> None:
        """交叉校验 attempt 状态、own/reused Result 与有序 outputs，拒绝关系篡改。"""

        work_dir_rows = connection.execute(
            "SELECT work_dir FROM node_runs ORDER BY rowid"
        ).fetchall()
        work_dir_identities = tuple(
            _work_dir_identity(cast(str, row["work_dir"])) for row in work_dir_rows
        )
        normalized_work_dirs = tuple(item for item in work_dir_identities if item is not None)
        if any(item is None for item in work_dir_identities) or _work_dir_collection_overlaps(
            normalized_work_dirs
        ):
            raise RuntimeDataError(
                "E_NODE_RUN_WORK_DIR_RELATION_CORRUPT",
                "Project 全历史 NodeRun 必须使用 resolved/normcase 独立且不重叠的 work_dir",
            )

        attempts_by_node: dict[str, list[int]] = {}
        for node_run in run.node_runs:
            attempts_by_node.setdefault(node_run.node_id, []).append(node_run.attempt)
            self._validate_historical_input_binding(connection, run, node_run)
            definition = self._definition_for_run_node(run, node_run.node_id)
            if definition is None:
                raise RuntimeDataError(
                    "E_NODE_RUN_DEFINITION_CORRUPT",
                    "NodeRun 缺少 snapshot NodeDefinition",
                )
            handoff = node_run.external_handoff
            if handoff is not None and (
                node_run.started_at is None
                or not self._handoff_contract_matches(
                    definition,
                    node_run,
                    handoff,
                    node_run.started_at,
                )
            ):
                raise RuntimeDataError(
                    "E_HANDOFF_RELATION_CORRUPT",
                    "已持久化 handoff 不符合 snapshot/attempt authority",
                )
            if node_run.state is NodeRunState.RUNNING and (
                definition.execution_mode is not ExecutionMode.AUTOMATIC or handoff is not None
            ):
                raise RuntimeDataError(
                    "E_NODE_RUN_EXECUTION_MODE_CORRUPT",
                    "running NodeRun 必须绑定 automatic definition 且无 handoff",
                )
            if node_run.state is NodeRunState.WAITING_EXTERNAL and (
                definition.execution_mode is not ExecutionMode.MANUAL_EXTERNAL or handoff is None
            ):
                raise RuntimeDataError(
                    "E_NODE_RUN_EXECUTION_MODE_CORRUPT",
                    "waiting_external NodeRun 必须绑定合法 manual handoff",
                )
            if (
                node_run.state is NodeRunState.COMPLETED
                and definition.execution_mode is ExecutionMode.MANUAL_EXTERNAL
                and node_run.reused_from_result_id is None
                and handoff is None
            ):
                raise RuntimeDataError(
                    "E_HANDOFF_RELATION_CORRUPT",
                    "实际完成的 manual_external NodeRun 必须保留 handoff",
                )
            if definition.execution_mode is ExecutionMode.AUTOMATIC and handoff is not None:
                raise RuntimeDataError(
                    "E_HANDOFF_RELATION_CORRUPT",
                    "automatic NodeRun 不得携带 handoff",
                )
            own_row = connection.execute(
                "SELECT result_id FROM node_results WHERE node_run_id = ?",
                (node_run.node_run_id,),
            ).fetchone()
            if node_run.state is not NodeRunState.COMPLETED:
                if own_row is not None:
                    raise RuntimeDataError(
                        "E_NODE_RUN_RESULT_RELATION_CORRUPT",
                        "非 completed NodeRun 不得拥有 NodeResult",
                    )
                continue

            if node_run.reused_from_result_id is not None:
                valid_exit_code = node_run.exit_code is None
            elif isinstance(definition.executor, CommandExecutorSpec):
                valid_exit_code = node_run.exit_code == 0
            else:
                valid_exit_code = node_run.exit_code is None
            if not valid_exit_code:
                raise RuntimeDataError(
                    "E_NODE_RUN_EXIT_CODE_RELATION_CORRUPT",
                    "completed exit_code 不符合 snapshot executor/reuse 语义",
                )

            if node_run.reused_from_result_id is None:
                if own_row is None:
                    raise RuntimeDataError(
                        "E_NODE_RUN_RESULT_RELATION_CORRUPT",
                        "completed NodeRun 缺少 own NodeResult",
                    )
                result = self._read_result(connection, cast(str, own_row["result_id"]))
                if (
                    node_run.started_at is None
                    or node_run.ended_at is None
                    or result.created_at < node_run.started_at
                    or result.created_at > node_run.ended_at
                ):
                    raise RuntimeDataError(
                        "E_NODE_RUN_RESULT_RELATION_CORRUPT",
                        "own Result created_at 必须位于 NodeRun 执行区间内",
                    )
            else:
                if own_row is not None:
                    raise RuntimeDataError(
                        "E_NODE_RUN_RESULT_RELATION_CORRUPT",
                        "reused NodeRun 不得同时拥有 own NodeResult",
                    )
                result = self._read_result(connection, node_run.reused_from_result_id)
                source = self._read_node_run(connection, result.node_run_id)
                (
                    source_project_id,
                    source_graph,
                    source_definitions,
                ) = self._read_run_snapshot(connection, source.run_id)
                source_own = connection.execute(
                    "SELECT result_id FROM node_results WHERE node_run_id = ?",
                    (source.node_run_id,),
                ).fetchone()
                if (
                    node_run.attempt != 1
                    or node_run.started_at is None
                    or node_run.started_at != node_run.ended_at
                    or node_run.external_handoff is not None
                    or source.state is not NodeRunState.COMPLETED
                    or source.reused_from_result_id is not None
                    or source.ended_at is None
                    or node_run.ended_at is None
                    or source.ended_at >= run.created_at
                    or source.ended_at > node_run.ended_at
                    or source_own is None
                    or source_own["result_id"] != result.result_id
                    or source_project_id != run.project_id
                    or not _node_snapshot_matches(
                        source_graph=source_graph,
                        source_definitions=source_definitions,
                        target_graph=run.graph_snapshot,
                        target_definitions=run.definitions_snapshot,
                        node_id=node_run.node_id,
                    )
                    or source.node_id != node_run.node_id
                    or source.definition_version != node_run.definition_version
                    or source.input_artifact_ids != node_run.input_artifact_ids
                    or source.output_artifact_ids
                    != tuple(item.artifact_id for item in result.outputs)
                    or source.started_at is None
                    or result.created_at < source.started_at
                    or result.created_at > source.ended_at
                ):
                    raise RuntimeDataError(
                        "E_NODE_RUN_RESULT_RELATION_CORRUPT",
                        "reused Result 与 NodeRun binding 不一致",
                    )
            result_output_ids = tuple(item.artifact_id for item in result.outputs)
            if result_output_ids != node_run.output_artifact_ids:
                raise RuntimeDataError(
                    "E_NODE_RUN_RESULT_RELATION_CORRUPT",
                    "NodeRun output IDs 与 own/reused Result 不一致",
                )
            self._validate_result_output_contract(run, node_run, result)
        for node_id, attempts in attempts_by_node.items():
            ordered = sorted(attempts)
            if ordered != list(range(1, ordered[-1] + 1)):
                raise RuntimeDataError(
                    "E_NODE_RUN_ATTEMPT_HISTORY_CORRUPT",
                    f"{node_id!r} 的 attempt 历史必须从 1 连续递增",
                )
            history = sorted(
                (item for item in run.node_runs if item.node_id == node_id),
                key=lambda item: item.attempt,
            )
            for previous, current in pairwise(history):
                previous_times = tuple(
                    value
                    for value in (
                        previous.created_at,
                        previous.started_at,
                        previous.ended_at,
                    )
                    if value is not None
                )
                if current.created_at < max(previous_times):
                    raise RuntimeDataError(
                        "E_NODE_RUN_TIME_RELATION_CORRUPT",
                        "attempt 时间必须按 attempt 单调递增",
                    )
        selected = set(_selected_node_ids(run.graph_snapshot, run.selected_targets))
        actual = set(attempts_by_node)
        if run.state is RunState.PENDING and actual:
            raise RuntimeDataError(
                "E_RUN_PENDING_ATTEMPTS_CORRUPT",
                "pending Run 不得已有 NodeRun",
            )
        if run.state is not RunState.PENDING and actual != selected:
            raise RuntimeDataError(
                "E_RUN_ATTEMPT_COVERAGE_CORRUPT",
                "非 pending Run 的 NodeRun 必须精确覆盖 selected closure",
            )
        if run.state is not RunState.PENDING and (
            run.started_at is None
            or any(
                min(item.created_at for item in run.node_runs if item.node_id == node_id)
                > run.started_at
                for node_id in selected
            )
        ):
            raise RuntimeDataError(
                "E_RUN_ATTEMPT_TIME_RELATION_CORRUPT",
                "Run started_at 不得早于 selected attempt 1 创建时间",
            )
        if run.started_at is not None and any(
            item.started_at is not None and item.started_at < run.started_at
            for item in run.node_runs
        ):
            raise RuntimeDataError(
                "E_NODE_RUN_TIME_RELATION_CORRUPT",
                "NodeRun.started_at 不得早于 parent Run.started_at",
            )
        latest = (
            {
                node_id: max(
                    (item for item in run.node_runs if item.node_id == node_id),
                    key=lambda item: item.attempt,
                )
                for node_id in selected
            }
            if run.state is not RunState.PENDING
            else {}
        )
        if run.state is RunState.COMPLETED:
            if any(item.state is not NodeRunState.COMPLETED for item in latest.values()):
                raise RuntimeDataError(
                    "E_RUN_COMPLETION_RELATION_CORRUPT",
                    "completed Run 的 selected closure 最新 attempts 必须全部 completed",
                )
            if run.ended_at is None or any(
                item.ended_at is None or item.ended_at > run.ended_at for item in latest.values()
            ):
                raise RuntimeDataError(
                    "E_RUN_COMPLETION_TIME_RELATION_CORRUPT",
                    "Run ended_at 不得早于 selected latest NodeRun ended_at",
                )
        if run.state is RunState.FAILED and (
            run.ended_at is None
            or any(
                item.state not in {NodeRunState.COMPLETED, NodeRunState.FAILED}
                or item.ended_at is None
                or item.ended_at > run.ended_at
                for item in latest.values()
            )
        ):
            raise RuntimeDataError(
                "E_RUN_FAILURE_RELATION_CORRUPT",
                "failed Run 的 selected closure 最新 attempts 必须全部终结",
            )

    @staticmethod
    def _read_node_run(connection: sqlite3.Connection, node_run_id: str) -> NodeRun:
        row = connection.execute(
            "SELECT * FROM node_runs WHERE node_run_id = ?", (node_run_id,)
        ).fetchone()
        if row is None:
            raise RuntimeNotFoundError("E_NODE_RUN_NOT_FOUND", f"NodeRun 不存在：{node_run_id}")
        return RuntimeRepository._node_run_from_row(row)

    @staticmethod
    def _node_run_from_row(row: sqlite3.Row) -> NodeRun:
        payload = {
            "node_run_id": row["node_run_id"],
            "run_id": row["run_id"],
            "node_id": row["node_id"],
            "definition_version": row["definition_version"],
            "attempt": row["attempt"],
            "state": row["state"],
            "input_artifact_ids": _load_json(
                row["input_artifact_ids_json"], context="input_artifact_ids"
            ),
            "output_artifact_ids": _load_json(
                row["output_artifact_ids_json"], context="output_artifact_ids"
            ),
            "created_at": row["created_at"],
            "work_dir": row["work_dir"],
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "progress": row["progress"],
            "exit_code": row["exit_code"],
            "log_path": row["log_path"],
            "error": None
            if row["error_json"] is None
            else _load_json(row["error_json"], context="NodeRun.error"),
            "reused_from_result_id": row["reused_from_result_id"],
            "external_handoff": None
            if row["external_handoff_json"] is None
            else _load_json(row["external_handoff_json"], context="ExternalHandoff"),
        }
        return _decode_model(NodeRun, payload, context="NodeRun")

    def _read_result(self, connection: sqlite3.Connection, result_id: str) -> NodeResult:
        row = connection.execute(
            "SELECT * FROM node_results WHERE result_id = ?", (result_id,)
        ).fetchone()
        if row is None:
            raise RuntimeNotFoundError("E_RESULT_NOT_FOUND", f"NodeResult 不存在：{result_id}")
        output_ids = _load_json(row["output_artifact_ids_json"], context="result outputs")
        if not isinstance(output_ids, list):
            raise RuntimeDataError("E_RUNTIME_DATA_CORRUPT", "result outputs 必须是 JSON array")
        artifact_rows = connection.execute(
            "SELECT * FROM artifacts WHERE result_id = ?", (result_id,)
        ).fetchall()
        artifacts = {item["artifact_id"]: self._artifact_from_row(item) for item in artifact_rows}
        if len(artifacts) != len(artifact_rows) or set(artifacts) != set(output_ids):
            raise RuntimeDataError(
                "E_RESULT_ARTIFACT_SET_CORRUPT", "NodeResult output IDs 与 Artifact rows 不一致"
            )
        payload = {
            "result_id": row["result_id"],
            "node_run_id": row["node_run_id"],
            "outputs": [artifacts[item].model_dump(mode="json") for item in output_ids],
            "media_summary": _load_json(row["media_summary_json"], context="media_summary"),
            "validation_summary": _load_json(
                row["validation_summary_json"], context="validation_summary"
            ),
            "created_at": row["created_at"],
        }
        return _decode_model(NodeResult, payload, context="NodeResult")

    @staticmethod
    def _artifact_from_row(row: sqlite3.Row) -> Artifact:
        frame_range = None
        if row["frame_start"] is not None or row["frame_end"] is not None:
            if row["frame_start"] is None or row["frame_end"] is None:
                raise RuntimeDataError("E_ARTIFACT_FRAME_RANGE_CORRUPT", "frame range 必须成对出现")
            frame_range = {
                "start_frame": row["frame_start"],
                "end_frame": row["frame_end"],
            }
        payload = {
            "artifact_id": row["artifact_id"],
            "kind": row["kind"],
            "path": row["path"],
            "producer_node_run_id": row["producer_node_run_id"],
            "producer_port_id": row["producer_port_id"],
            "ordinal": row["ordinal"],
            "frame_range": frame_range,
            "media_info": _load_json(row["media_info_json"], context="Artifact.media_info"),
            "size": row["size"],
            "mtime_ns": row["mtime_ns"],
        }
        return _decode_model(Artifact, payload, context="Artifact")

    @staticmethod
    def _latest_from_row(row: sqlite3.Row) -> LatestNodeResult:
        stale = row["stale"]
        if type(stale) is not int or stale not in (0, 1):
            raise RuntimeDataError(
                "E_LATEST_STALE_CORRUPT",
                "latest_results.stale 必须是 SQLite integer 0 或 1",
            )
        payload = {
            "node_id": row["node_id"],
            "result_id": row["result_id"],
            "stale": stale == 1,
            "stale_reason": row["stale_reason"],
            "updated_at": row["updated_at"],
        }
        return _decode_model(LatestNodeResult, payload, context="LatestNodeResult")
