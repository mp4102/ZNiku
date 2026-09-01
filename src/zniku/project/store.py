"""实现单文件 SQLite-backed ``.zniku`` Project Store。

SQLite 文件是 Project、Graph 与精确 NodeDefinition 的唯一持久化 authority。保存先在内存完成模型和
Graph 校验，再通过单个事务替换 Core 表；任一步失败都会回滚，已有工程内容保持不变。读取会检查
SQLite header、应用标识、schema version、表结构、外键与模型内容，未知或损坏输入默认失败关闭。

schema v2 在同一文件中为 RuntimeRepository 保留 Run、NodeRun、Artifact、NodeResult 与 latest result
表；Project 保存不会删除运行历史。只允许将本仓库 0.2.0 Phase 1 schema v1 单向事务迁移到 v2，绝不
读取或迁移 0.1.0 legacy snapshot/Evidence。
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, cast

from pydantic import ValidationError

from zniku.graph import Graph, GraphValidationError, GraphValidator, NodeDefinition
from zniku.project.models import Project, ProjectSnapshot

PROJECT_SCHEMA_VERSION: Final = 2
PROJECT_APPLICATION_ID: Final = 0x5A4E494B  # ASCII "ZNIK"
_SQLITE_HEADER: Final = b"SQLite format 3\x00"
_PHASE_1_SCHEMA_VERSION: Final = 1
_CORE_TABLES: Final = frozenset({"project", "node_definitions", "graph_nodes", "graph_edges"})
_CORE_COLUMNS: Final[Mapping[str, tuple[str, ...]]] = {
    "project": ("singleton", "project_id", "name"),
    "node_definitions": ("definition_order", "type_id", "version", "payload_json"),
    "graph_nodes": (
        "node_order",
        "node_id",
        "type_id",
        "definition_version",
        "parameters_json",
        "ui_position_json",
    ),
    "graph_edges": (
        "edge_order",
        "source_node_id",
        "source_port_id",
        "target_node_id",
        "target_port_id",
        "ordinal",
    ),
}
_RUNTIME_COLUMNS: Final[Mapping[str, tuple[str, ...]]] = {
    "runs": (
        "run_id",
        "project_id",
        "state",
        "graph_snapshot_json",
        "definitions_snapshot_json",
        "selected_targets_json",
        "created_at",
        "started_at",
        "ended_at",
        "error_json",
    ),
    "node_runs": (
        "node_run_id",
        "run_id",
        "node_id",
        "definition_version",
        "attempt",
        "state",
        "input_artifact_ids_json",
        "output_artifact_ids_json",
        "created_at",
        "work_dir",
        "started_at",
        "ended_at",
        "progress",
        "exit_code",
        "log_path",
        "error_json",
        "reused_from_result_id",
        "external_handoff_json",
    ),
    "artifacts": (
        "artifact_id",
        "result_id",
        "kind",
        "path",
        "producer_node_run_id",
        "producer_port_id",
        "ordinal",
        "frame_start",
        "frame_end",
        "media_info_json",
        "size",
        "mtime_ns",
    ),
    "node_results": (
        "result_id",
        "node_run_id",
        "output_artifact_ids_json",
        "media_summary_json",
        "validation_summary_json",
        "created_at",
    ),
    "latest_results": ("node_id", "result_id", "stale", "stale_reason", "updated_at"),
}
_EXPECTED_TABLES: Final = _CORE_TABLES | frozenset(_RUNTIME_COLUMNS)
_EXPECTED_COLUMNS: Final[Mapping[str, tuple[str, ...]]] = {
    **_CORE_COLUMNS,
    **_RUNTIME_COLUMNS,
}

_SCHEMA_SQL: Final = """
CREATE TABLE project (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    project_id TEXT NOT NULL,
    name TEXT NOT NULL
);

CREATE TABLE node_definitions (
    definition_order INTEGER NOT NULL UNIQUE CHECK (definition_order >= 0),
    type_id TEXT NOT NULL,
    version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (type_id, version)
);

CREATE TABLE graph_nodes (
    node_order INTEGER NOT NULL UNIQUE CHECK (node_order >= 0),
    node_id TEXT PRIMARY KEY,
    type_id TEXT NOT NULL,
    definition_version TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    ui_position_json TEXT,
    FOREIGN KEY (type_id, definition_version)
        REFERENCES node_definitions(type_id, version)
        ON UPDATE RESTRICT ON DELETE RESTRICT
);

CREATE TABLE graph_edges (
    edge_order INTEGER PRIMARY KEY CHECK (edge_order >= 0),
    source_node_id TEXT NOT NULL,
    source_port_id TEXT NOT NULL,
    target_node_id TEXT NOT NULL,
    target_port_id TEXT NOT NULL,
    ordinal INTEGER,
    FOREIGN KEY (source_node_id) REFERENCES graph_nodes(node_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (target_node_id) REFERENCES graph_nodes(node_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
);
"""

_RUNTIME_SCHEMA_STATEMENTS: Final = (
    """
    CREATE TABLE runs (
        run_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('pending', 'running', 'completed', 'failed')),
        graph_snapshot_json TEXT NOT NULL,
        definitions_snapshot_json TEXT NOT NULL,
        selected_targets_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        started_at TEXT,
        ended_at TEXT,
        error_json TEXT
    )
    """,
    """
    CREATE TABLE node_runs (
        node_run_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        node_id TEXT NOT NULL,
        definition_version TEXT NOT NULL,
        attempt INTEGER NOT NULL CHECK (attempt >= 1),
        state TEXT NOT NULL CHECK (
            state IN ('pending', 'running', 'waiting_external', 'completed', 'failed')
        ),
        input_artifact_ids_json TEXT NOT NULL,
        output_artifact_ids_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        work_dir TEXT NOT NULL,
        started_at TEXT,
        ended_at TEXT,
        progress REAL CHECK (progress IS NULL OR (progress >= 0.0 AND progress <= 1.0)),
        exit_code INTEGER,
        log_path TEXT,
        error_json TEXT,
        reused_from_result_id TEXT,
        external_handoff_json TEXT,
        UNIQUE (run_id, node_id, attempt),
        FOREIGN KEY (run_id) REFERENCES runs(run_id)
            ON UPDATE RESTRICT ON DELETE RESTRICT,
        FOREIGN KEY (reused_from_result_id) REFERENCES node_results(result_id)
            ON UPDATE RESTRICT ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE node_results (
        result_id TEXT PRIMARY KEY,
        node_run_id TEXT NOT NULL UNIQUE,
        output_artifact_ids_json TEXT NOT NULL,
        media_summary_json TEXT NOT NULL,
        validation_summary_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (node_run_id) REFERENCES node_runs(node_run_id)
            ON UPDATE RESTRICT ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE artifacts (
        artifact_id TEXT PRIMARY KEY,
        result_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        path TEXT NOT NULL,
        producer_node_run_id TEXT NOT NULL,
        producer_port_id TEXT NOT NULL,
        ordinal INTEGER CHECK (ordinal IS NULL OR ordinal >= 0),
        frame_start INTEGER CHECK (frame_start IS NULL OR frame_start >= 0),
        frame_end INTEGER CHECK (frame_end IS NULL OR frame_end > frame_start),
        media_info_json TEXT NOT NULL,
        size INTEGER CHECK (size IS NULL OR size >= 0),
        mtime_ns INTEGER CHECK (mtime_ns IS NULL OR mtime_ns >= 0),
        FOREIGN KEY (result_id) REFERENCES node_results(result_id)
            ON UPDATE RESTRICT ON DELETE RESTRICT,
        FOREIGN KEY (producer_node_run_id) REFERENCES node_runs(node_run_id)
            ON UPDATE RESTRICT ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE latest_results (
        node_id TEXT PRIMARY KEY,
        result_id TEXT NOT NULL,
        stale INTEGER NOT NULL CHECK (stale IN (0, 1)),
        stale_reason TEXT,
        updated_at TEXT NOT NULL,
        CHECK (
            (stale = 0 AND stale_reason IS NULL)
            OR (stale = 1 AND stale_reason IS NOT NULL)
        ),
        FOREIGN KEY (result_id) REFERENCES node_results(result_id)
            ON UPDATE RESTRICT ON DELETE RESTRICT
    )
    """,
)


class ProjectStoreError(RuntimeError):
    """Project Store 无法安全完成操作时的公共失败类型。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class ProjectFormatError(ProjectStoreError):
    """文件扩展名、SQLite 身份或 schema 不属于当前 Project 格式。"""


class ProjectValidationError(ProjectStoreError):
    """持久化内容无法形成合法 Project、定义目录或 Graph。"""


def _format_error(code: str, message: str) -> ProjectFormatError:
    return ProjectFormatError(code, message)


def _validation_error(code: str, message: str) -> ProjectValidationError:
    return ProjectValidationError(code, message)


def _dump_json(value: Any) -> str:
    """生成稳定、紧凑的普通 JSON；它只是 SQLite 字段编码，不是第二套 authority。"""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise _validation_error("E_PROJECT_JSON_INVALID", str(error)) from error


def _load_json(payload: str, *, context: str) -> Any:
    """严格解析 SQLite JSON 字段，拒绝重复键和非标准数字常量。"""

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
        raise _validation_error(
            "E_PROJECT_MODEL_CORRUPT", f"{context} 不是有效 JSON：{error}"
        ) from error


def _assert_contiguous_order(values: Sequence[int], *, context: str) -> None:
    expected = list(range(len(values)))
    if list(values) != expected:
        raise _validation_error(
            "E_PROJECT_ORDER_INVALID",
            f"{context} 顺序必须从 0 开始连续且唯一",
        )


def _remove_owned_file(path: Path, *, identity: tuple[int, int]) -> None:
    """只清理仍指向本次排他创建 inode 的文件，避免删除竞态替换后的目标。"""

    with suppress(OSError):
        current = path.stat()
        if (current.st_dev, current.st_ino) == identity:
            path.unlink(missing_ok=True)


def _incoming_edge_signature(graph: Graph, node_id: str) -> tuple[tuple[str, str, str, int], ...]:
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


def _downstream_ids(graph: Graph, node_id: str) -> set[str]:
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
    return visited


def _graph_change_stale_ids(old: ProjectSnapshot, new: ProjectSnapshot) -> tuple[str, ...]:
    """找出语义变化节点及其新旧 Graph 下游；单纯 UI 坐标变化不失效结果。"""

    old_nodes = {node.node_id: node for node in old.project.graph.nodes}
    new_nodes = {node.node_id: node for node in new.project.graph.nodes}
    old_definitions = {(item.type_id, item.version): item for item in old.definitions}
    new_definitions = {(item.type_id, item.version): item for item in new.definitions}
    changed: set[str] = set(old_nodes) ^ set(new_nodes)
    for node_id in set(old_nodes) & set(new_nodes):
        old_node = old_nodes[node_id]
        new_node = new_nodes[node_id]
        if (
            old_node.type_id != new_node.type_id
            or old_node.definition_version != new_node.definition_version
            or old_node.parameters != new_node.parameters
            or _incoming_edge_signature(old.project.graph, node_id)
            != _incoming_edge_signature(new.project.graph, node_id)
            or old_definitions.get((old_node.type_id, old_node.definition_version))
            != new_definitions.get((new_node.type_id, new_node.definition_version))
        ):
            changed.add(node_id)
    stale = set(changed)
    for node_id in changed:
        stale.update(_downstream_ids(old.project.graph, node_id))
        stale.update(_downstream_ids(new.project.graph, node_id))
    order = [node.node_id for node in new.project.graph.nodes]
    order.extend(node.node_id for node in old.project.graph.nodes if node.node_id not in new_nodes)
    return tuple(node_id for node_id in order if node_id in stale)


class ProjectStore:
    """管理一个 SQLite-backed ``.zniku`` 工程文件。

    实例不持有长期数据库连接；每次操作独立打开连接，避免隐藏事务和跨线程连接所有权。``save`` 是
    Project Core 的唯一写入口，``load`` 返回 Project 与精确 NodeDefinition 的同一读取快照；Runtime
    历史由 RuntimeRepository 在 schema v2 附加表中独立维护。
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)
        self._assert_extension(self._path)

    @property
    def path(self) -> Path:
        """返回工程文件路径，不解析或改写用户提供的路径。"""

        return self._path

    @classmethod
    def create(
        cls,
        path: str | os.PathLike[str],
        project: Project,
        definitions: Iterable[NodeDefinition],
    ) -> ProjectStore:
        """创建并写入首个完整 ProjectSnapshot；已存在目标永不覆盖。

        目标先用 ``xb`` 排他创建，只有本调用取得文件所有权后才初始化 SQLite。任何初始化、schema
        自检或首次保存失败都会清理该占位文件；竞态中已存在的用户文件不会被打开、改写或删除。
        """

        store = cls(path)
        if not store.path.parent.exists():
            raise _format_error("E_PROJECT_PARENT_MISSING", f"父目录不存在：{store.path.parent}")
        snapshot = store._validated_snapshot(project, tuple(definitions))

        try:
            with store.path.open("xb") as owned_stream:
                owned_stat = os.fstat(owned_stream.fileno())
        except FileExistsError as error:
            raise _format_error("E_PROJECT_EXISTS", f"工程已存在：{store.path}") from error
        except OSError as error:
            raise _format_error("E_PROJECT_CREATE_FAILED", str(error)) from error

        owned_identity = (owned_stat.st_dev, owned_stat.st_ino)
        try:
            connection = sqlite3.connect(store.path)
            try:
                store._configure_connection(connection)
                connection.executescript(_SCHEMA_SQL)
                for statement in _RUNTIME_SCHEMA_STATEMENTS:
                    connection.execute(statement)
                connection.execute(f"PRAGMA application_id = {PROJECT_APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version = {PROJECT_SCHEMA_VERSION}")
                connection.commit()
            finally:
                connection.close()
            store._assert_file_and_schema()
            store.save(snapshot.project, snapshot.definitions)
        except ProjectStoreError:
            _remove_owned_file(store.path, identity=owned_identity)
            raise
        except (OSError, sqlite3.Error) as error:
            _remove_owned_file(store.path, identity=owned_identity)
            raise _format_error("E_PROJECT_CREATE_FAILED", str(error)) from error
        return store

    @classmethod
    def open(cls, path: str | os.PathLike[str]) -> ProjectStore:
        """打开并验证现有工程；只自动迁移受支持的 0.2.0 Phase 1 schema v1。"""

        store = cls(path)
        store._assert_file_and_schema()
        return store

    def save(
        self,
        project: Project,
        definitions: Iterable[NodeDefinition],
    ) -> None:
        """原子保存 Project、Graph 与 NodeDefinition 精确版本。

        Graph 校验和所有 JSON 编码均发生在 ``BEGIN IMMEDIATE`` 前。事务开始后的任何约束、I/O 或
        SQLite 错误都会 rollback，因而非法保存不会破坏之前成功提交的内容。
        """

        snapshot = self._validated_snapshot(project, tuple(definitions))
        definition_rows, node_rows, edge_rows = self._encode_snapshot(snapshot)
        self._assert_file_and_schema()

        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self.path)
            connection.row_factory = sqlite3.Row
            self._configure_connection(connection)
            connection.execute("BEGIN IMMEDIATE")
            previous: ProjectSnapshot | None = None
            if connection.execute("SELECT count(*) FROM project").fetchone()[0] == 1:
                loaded = self._read_snapshot(connection)
                previous = self._validated_snapshot(loaded.project, loaded.definitions)
                if previous.project.project_id != snapshot.project.project_id:
                    raise _validation_error(
                        "E_PROJECT_ID_IMMUTABLE",
                        "既有 .zniku authority 的 project_id 不得替换",
                    )
            connection.execute("DELETE FROM graph_edges")
            connection.execute("DELETE FROM graph_nodes")
            connection.execute("DELETE FROM node_definitions")
            connection.execute("DELETE FROM project")
            connection.execute(
                "INSERT INTO project(singleton, project_id, name) VALUES (1, ?, ?)",
                (snapshot.project.project_id, snapshot.project.name),
            )
            connection.executemany(
                """
                INSERT INTO node_definitions(
                    definition_order, type_id, version, payload_json
                ) VALUES (?, ?, ?, ?)
                """,
                definition_rows,
            )
            connection.executemany(
                """
                INSERT INTO graph_nodes(
                    node_order, node_id, type_id, definition_version,
                    parameters_json, ui_position_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                node_rows,
            )
            connection.executemany(
                """
                INSERT INTO graph_edges(
                    edge_order, source_node_id, source_port_id,
                    target_node_id, target_port_id, ordinal
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                edge_rows,
            )
            if previous is not None:
                stale_node_ids = _graph_change_stale_ids(previous, snapshot)
                if stale_node_ids:
                    changed_at = (
                        datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
                    )
                    connection.executemany(
                        """
                        UPDATE latest_results
                        SET stale = 1,
                            stale_reason = 'graph_changed',
                            updated_at = CASE WHEN updated_at > ? THEN updated_at ELSE ? END
                        WHERE node_id = ?
                        """,
                        ((changed_at, changed_at, node_id) for node_id in stale_node_ids),
                    )
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise _validation_error(
                    "E_PROJECT_FOREIGN_KEY_INVALID", "保存内容违反 SQLite 外键约束"
                )
            connection.commit()
        except ProjectStoreError:
            if connection is not None:
                connection.rollback()
            raise
        except sqlite3.Error as error:
            if connection is not None:
                connection.rollback()
            raise ProjectStoreError("E_PROJECT_SAVE_FAILED", str(error)) from error
        finally:
            if connection is not None:
                connection.close()

    def load(self) -> ProjectSnapshot:
        """读取并重建完整 ProjectSnapshot，随后再次运行 GraphValidator。"""

        self._assert_file_and_schema()
        try:
            connection = sqlite3.connect(self.path)
            connection.row_factory = sqlite3.Row
            try:
                self._configure_connection(connection)
                connection.execute("BEGIN")
                snapshot = self._read_snapshot(connection)
                connection.rollback()
            finally:
                connection.close()
        except ProjectStoreError:
            raise
        except (OSError, sqlite3.Error) as error:
            raise ProjectStoreError("E_PROJECT_LOAD_FAILED", str(error)) from error
        return self._validated_snapshot(snapshot.project, snapshot.definitions)

    def _assert_file_and_schema(self) -> None:
        if not self.path.exists() or not self.path.is_file():
            raise _format_error("E_PROJECT_NOT_FOUND", f"工程文件不存在：{self.path}")
        try:
            with self.path.open("rb") as stream:
                header = stream.read(len(_SQLITE_HEADER))
        except OSError as error:
            raise _format_error("E_PROJECT_READ_FAILED", str(error)) from error
        if header != _SQLITE_HEADER:
            raise _format_error("E_PROJECT_NOT_SQLITE", "文件没有 SQLite 3 header")

        try:
            connection = sqlite3.connect(self.path)
            connection.row_factory = sqlite3.Row
            try:
                self._configure_connection(connection)
                application_id = cast(
                    int, connection.execute("PRAGMA application_id").fetchone()[0]
                )
                schema_version = cast(int, connection.execute("PRAGMA user_version").fetchone()[0])
                if application_id != PROJECT_APPLICATION_ID:
                    raise _format_error(
                        "E_PROJECT_APPLICATION_ID_UNKNOWN",
                        f"未知 application_id：{application_id}",
                    )
                quick_check = connection.execute("PRAGMA quick_check").fetchall()
                if [row[0] for row in quick_check] != ["ok"]:
                    raise _format_error("E_PROJECT_SQLITE_CORRUPT", "SQLite quick_check 失败")
                if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise _format_error("E_PROJECT_FOREIGN_KEY_INVALID", "工程包含无效外键")
                if schema_version == _PHASE_1_SCHEMA_VERSION:
                    self._assert_schema_shape(
                        connection,
                        expected_tables=_CORE_TABLES,
                        expected_columns=_CORE_COLUMNS,
                    )
                    self._migrate_phase_1_to_phase_2(connection)
                    schema_version = PROJECT_SCHEMA_VERSION
                elif schema_version != PROJECT_SCHEMA_VERSION:
                    raise _format_error(
                        "E_PROJECT_SCHEMA_VERSION_UNKNOWN",
                        f"未知 schema version：{schema_version}",
                    )
                self._assert_schema_shape(
                    connection,
                    expected_tables=_EXPECTED_TABLES,
                    expected_columns=_EXPECTED_COLUMNS,
                )
                if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise _format_error("E_PROJECT_FOREIGN_KEY_INVALID", "工程包含无效外键")
            finally:
                connection.close()
        except ProjectStoreError:
            raise
        except sqlite3.Error as error:
            raise _format_error("E_PROJECT_SQLITE_INVALID", str(error)) from error

    @staticmethod
    def _assert_extension(path: Path) -> None:
        if path.suffix != ".zniku":
            raise _format_error("E_PROJECT_EXTENSION_INVALID", "工程扩展名必须精确为 .zniku")

    @staticmethod
    def _configure_connection(connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        enabled = cast(int, connection.execute("PRAGMA foreign_keys").fetchone()[0])
        if enabled != 1:
            raise ProjectStoreError("E_PROJECT_FOREIGN_KEYS_DISABLED", "无法启用 SQLite 外键")

    @staticmethod
    def _assert_schema_shape(
        connection: sqlite3.Connection,
        *,
        expected_tables: frozenset[str],
        expected_columns: Mapping[str, tuple[str, ...]],
    ) -> None:
        actual_tables = {
            cast(str, row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if actual_tables != expected_tables:
            raise _format_error(
                "E_PROJECT_SCHEMA_INVALID",
                f"工程表集合无效：{sorted(actual_tables)}",
            )
        for table, columns in expected_columns.items():
            actual_columns = tuple(
                cast(str, row[1]) for row in connection.execute(f"PRAGMA table_info({table})")
            )
            if actual_columns != columns:
                raise _format_error(
                    "E_PROJECT_SCHEMA_INVALID",
                    f"{table} 列结构无效：{actual_columns}",
                )

    def _migrate_phase_1_to_phase_2(self, connection: sqlite3.Connection) -> None:
        """只迁移本仓库 0.2.0 Phase 1 schema，不读取任何 0.1.0 legacy 数据。

        ``BEGIN IMMEDIATE`` 后先完整读取并验证旧 Project；在此之前绝不创建表或写 user_version，
        因而模型损坏只会拒绝打开，不会把坏 authority 标成 schema v2。
        """

        try:
            connection.execute("BEGIN IMMEDIATE")
            snapshot = self._read_snapshot(connection)
            self._validated_snapshot(snapshot.project, snapshot.definitions)
            for statement in _RUNTIME_SCHEMA_STATEMENTS:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {PROJECT_SCHEMA_VERSION}")
            self._assert_schema_shape(
                connection,
                expected_tables=_EXPECTED_TABLES,
                expected_columns=_EXPECTED_COLUMNS,
            )
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise _format_error(
                    "E_PROJECT_FOREIGN_KEY_INVALID",
                    "迁移后的工程包含无效外键",
                )
            connection.commit()
        except (ProjectStoreError, sqlite3.Error):
            connection.rollback()
            raise

    @staticmethod
    def _validated_snapshot(
        project: Project,
        definitions: tuple[NodeDefinition, ...],
    ) -> ProjectSnapshot:
        try:
            snapshot = ProjectSnapshot(project=project, definitions=definitions)
        except ValidationError as error:
            raise _validation_error("E_PROJECT_MODEL_INVALID", str(error)) from error

        keys = tuple((item.type_id, item.version) for item in snapshot.definitions)
        if len(keys) != len(set(keys)):
            raise _validation_error(
                "E_PROJECT_DEFINITION_DUPLICATE",
                "NodeDefinition 的 type_id/version 必须唯一",
            )
        try:
            GraphValidator(snapshot.definitions).validate(snapshot.project.graph)
        except GraphValidationError as error:
            raise _validation_error("E_PROJECT_GRAPH_INVALID", str(error)) from error
        except ValueError as error:
            raise _validation_error("E_PROJECT_GRAPH_INVALID", str(error)) from error
        return snapshot

    @staticmethod
    def _encode_snapshot(
        snapshot: ProjectSnapshot,
    ) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]], list[tuple[Any, ...]]]:
        definition_rows: list[tuple[Any, ...]] = []
        for order, definition in enumerate(snapshot.definitions):
            payload = definition.model_dump(mode="json")
            definition_rows.append(
                (order, definition.type_id, definition.version, _dump_json(payload))
            )

        node_rows: list[tuple[Any, ...]] = []
        for order, node in enumerate(snapshot.project.graph.nodes):
            payload = node.model_dump(mode="json")
            node_rows.append(
                (
                    order,
                    node.node_id,
                    node.type_id,
                    node.definition_version,
                    _dump_json(payload["parameters"]),
                    None if payload["ui_position"] is None else _dump_json(payload["ui_position"]),
                )
            )

        edge_rows = [
            (
                order,
                edge.source_node_id,
                edge.source_port_id,
                edge.target_node_id,
                edge.target_port_id,
                edge.ordinal,
            )
            for order, edge in enumerate(snapshot.project.graph.edges)
        ]
        return definition_rows, node_rows, edge_rows

    @staticmethod
    def _read_snapshot(connection: sqlite3.Connection) -> ProjectSnapshot:
        project_rows = connection.execute(
            "SELECT singleton, project_id, name FROM project ORDER BY singleton"
        ).fetchall()
        if len(project_rows) != 1 or project_rows[0]["singleton"] != 1:
            raise _validation_error("E_PROJECT_EMPTY", "工程必须精确包含一个 Project")

        definition_rows = connection.execute(
            """
            SELECT definition_order, type_id, version, payload_json
            FROM node_definitions ORDER BY definition_order
            """
        ).fetchall()
        _assert_contiguous_order(
            [cast(int, row["definition_order"]) for row in definition_rows],
            context="NodeDefinition",
        )
        definitions: list[NodeDefinition] = []
        try:
            for row in definition_rows:
                payload = _load_json(row["payload_json"], context="NodeDefinition")
                definition = NodeDefinition.model_validate_json(_dump_json(payload))
                if definition.type_id != row["type_id"] or definition.version != row["version"]:
                    raise _validation_error(
                        "E_PROJECT_DEFINITION_BINDING_MISMATCH",
                        "NodeDefinition payload 与索引 identity 不一致",
                    )
                definitions.append(definition)
        except ValidationError as error:
            raise _validation_error("E_PROJECT_MODEL_CORRUPT", str(error)) from error

        node_rows = connection.execute(
            """
            SELECT node_order, node_id, type_id, definition_version,
                   parameters_json, ui_position_json
            FROM graph_nodes ORDER BY node_order
            """
        ).fetchall()
        _assert_contiguous_order(
            [cast(int, row["node_order"]) for row in node_rows], context="NodeInstance"
        )
        nodes: list[dict[str, Any]] = []
        for row in node_rows:
            nodes.append(
                {
                    "node_id": row["node_id"],
                    "type_id": row["type_id"],
                    "definition_version": row["definition_version"],
                    "parameters": _load_json(row["parameters_json"], context="parameters"),
                    "ui_position": None
                    if row["ui_position_json"] is None
                    else _load_json(row["ui_position_json"], context="ui_position"),
                }
            )

        edge_rows = connection.execute(
            """
            SELECT edge_order, source_node_id, source_port_id,
                   target_node_id, target_port_id, ordinal
            FROM graph_edges ORDER BY edge_order
            """
        ).fetchall()
        _assert_contiguous_order(
            [cast(int, row["edge_order"]) for row in edge_rows], context="Edge"
        )
        edges = [
            {
                "source_node_id": row["source_node_id"],
                "source_port_id": row["source_port_id"],
                "target_node_id": row["target_node_id"],
                "target_port_id": row["target_port_id"],
                "ordinal": row["ordinal"],
            }
            for row in edge_rows
        ]

        try:
            graph = Graph.model_validate_json(_dump_json({"nodes": nodes, "edges": edges}))
            project = Project(
                project_id=project_rows[0]["project_id"],
                name=project_rows[0]["name"],
                graph=graph,
            )
            return ProjectSnapshot(project=project, definitions=tuple(definitions))
        except ValidationError as error:
            raise _validation_error("E_PROJECT_MODEL_CORRUPT", str(error)) from error
