"""检查工程资产依赖并显式迁移该工程拥有的 attempt，源文件始终保留。

迁移是操作者主动触发的存储维护，不创建 Run、Artifact、receipt 或归档 authority。复制只操作
数据库精确列出的 UUID attempt 子树；完成分块比对后，在一个 SQLite 事务内切换定位引用。
调用方负责用 Project Service 的单操作互斥阻止同会话写入；事务仍重查 CAS 和全部运行记录。
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import stat
import threading
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any, Literal
from uuid import UUID, uuid4

from zniku.project.models import ProjectModel
from zniku.project.storage import ProjectStorage, legacy_project_storage
from zniku.project.store import ProjectStore, ProjectStoreError
from zniku.runtime.models import ExternalHandoff
from zniku.runtime.repository import RuntimeRepository

_RUNTIME_TABLES = ("runs", "node_runs", "node_results", "artifacts", "latest_results")
_CHUNK_SIZE = 1024 * 1024


class StorageDependency(ProjectModel):
    """列出已登记文件的缺失或外部依赖，不推断未知插件参数中的隐含文件。"""

    path: str
    artifact_ids: tuple[str, ...]
    state: Literal["present", "missing", "unreadable"]


class StorageInspection(ProjectModel):
    """只读归档前检查；覆盖范围明确不等于完整离线归档包。"""

    contract_version: Literal["0.3.0"] = "0.3.0"
    storage: ProjectStorage
    configured: bool
    attempt_count: int
    registered_file_count: int
    registered_bytes: int
    managed_file_count: int
    managed_bytes: int
    missing: tuple[StorageDependency, ...]
    external_dependencies: tuple[StorageDependency, ...]
    coverage: Literal["registered_artifacts"] = "registered_artifacts"
    warnings: tuple[str, ...] = (
        "此检查只覆盖已登记 Artifact；未运行节点和第三方插件的隐含文件依赖不在覆盖范围内。",
        "保存工程文件与工作数据不等于完整离线归档；请同时保留外部依赖中的源素材和发布结果。",
    )


class StorageMigrationPreview(ProjectModel):
    """绑定工程会话、CAS 与固定目标的短期内存迁移预览。"""

    contract_version: Literal["0.3.0"] = "0.3.0"
    ticket_id: str
    project_session_id: str
    expected_storage_revision: int
    source: ProjectStorage
    target: ProjectStorage
    attempt_count: int
    file_count: int
    byte_count: int
    external_dependencies: tuple[StorageDependency, ...]
    originals_retained: Literal[True] = True


@dataclass(frozen=True)
class _State:
    revision: int
    storage: ProjectStorage | None
    tables: tuple[tuple[str, tuple[tuple[Any, ...], ...]], ...]
    nodes: tuple[tuple[str, str, str | None, str | None], ...]
    artifacts: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _Identity:
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True)
class _File:
    source: Path
    relative: Path
    identity: _Identity


@dataclass(frozen=True)
class _Ticket:
    preview: StorageMigrationPreview
    project_path: Path
    legacy_root: Path
    state: _State
    directories: tuple[Path, ...]
    files: tuple[_File, ...]
    expires_at: float


def _failure(suffix: str, message: str) -> ProjectStoreError:
    return ProjectStoreError(f"E_PROJECT_STORAGE_{suffix}", message)


def _safe_path(path: Path, *, missing: bool = False) -> Path:
    """拒绝路径上任意链接或 Windows reparse point，缺失只允许在已知绝对定位下。"""

    if not path.is_absolute() or ".." in path.parts:
        raise _failure("PATH", "存储位置必须是无上跳的绝对路径")
    for candidate in (*reversed(path.parents), path):
        try:
            value = candidate.lstat()
        except FileNotFoundError:
            if missing:
                continue
            raise _failure("MISSING", f"路径已不存在：{path}") from None
        except OSError as error:
            raise _failure("PATH", f"无法读取存储位置：{candidate}") from error
        if stat.S_ISLNK(value.st_mode) or getattr(value, "st_file_attributes", 0) & 0x400:
            raise _failure("PATH", "数据迁移不接受符号链接、目录联接或其他 reparse point")
    return path.resolve(strict=not missing)


def _identity(path: Path) -> _Identity:
    _safe_path(path)
    value = path.stat()
    if not stat.S_ISREG(value.st_mode):
        raise _failure("FILE", "迁移只接受普通文件")
    return _Identity(
        value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns
    )


def _read_state(connection: sqlite3.Connection, store: ProjectStore) -> _State:
    """同一 SQLite snapshot 读取迁移所需记录；数据量仅在显式维护操作中展开。"""

    previous_factory = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        snapshot = store._read_snapshot(connection)
        store._validated_snapshot(snapshot.project, snapshot.definitions)
        repository = RuntimeRepository(store)
        for row in connection.execute("SELECT run_id FROM runs"):
            repository._read_run(connection, row["run_id"])
    finally:
        connection.row_factory = previous_factory
    return _State(
        revision=store._read_storage_revision(connection),
        storage=store._read_project_storage(connection),
        tables=tuple(
            (table, tuple(connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()))
            for table in _RUNTIME_TABLES
        ),
        nodes=tuple(
            connection.execute(
                "SELECT node_run_id, work_dir, log_path, external_handoff_json "
                "FROM node_runs ORDER BY node_run_id"
            ).fetchall()
        ),
        artifacts=tuple(
            connection.execute(
                "SELECT artifact_id, path FROM artifacts ORDER BY artifact_id"
            ).fetchall()
        ),
    )


def _state(store: ProjectStore) -> _State:
    store.load()  # 正式 Store 拒绝未知格式和损坏 Graph；不靠 maintenance 绕过读取门禁。
    with closing(sqlite3.connect(store.path)) as connection:
        store._configure_connection(connection)
        connection.execute("BEGIN")
        return _read_state(connection, store)


def _owned_directories(state: _State, storage: ProjectStorage) -> tuple[Path, ...]:
    root = Path(storage.attempts_root)
    result: set[Path] = set()
    for node_run_id, work_dir, _, _ in state.nodes:
        try:
            node_uuid = UUID(node_run_id)
        except ValueError as error:
            raise _failure("ATTEMPT", "执行记录包含非法 UUID") from error
        if node_uuid.version != 4:
            raise _failure("ATTEMPT", "执行记录身份必须是 UUIDv4")
        work = Path(work_dir)
        # 尚未开始的准备失败可以保存 root 本身，不把整个共享根视为工程所有物。
        if work == root:
            continue
        if work != root / node_uuid.hex:
            raise _failure("ATTEMPT", "执行目录不属于工程已绑定的 UUID attempt 根")
        result.add(work)
    return tuple(sorted(result, key=str))


def _inside_owned(path: Path, directories: tuple[Path, ...]) -> bool:
    return any(path.is_relative_to(directory) for directory in directories)


def inspect_storage(store: ProjectStore, legacy_root: str | Path) -> StorageInspection:
    """报告登记文件真实占用、缺失与根外依赖，不复制、清理或宣称完整离线归档。"""

    state = _state(store)
    storage = state.storage or legacy_project_storage(legacy_root)
    directories = _owned_directories(state, storage)
    grouped: dict[Path, list[str]] = {}
    for artifact_id, raw in state.artifacts:
        path = Path(raw).absolute()
        grouped.setdefault(path, []).append(artifact_id)
    missing: list[StorageDependency] = []
    external: list[StorageDependency] = []
    registered_bytes = managed_bytes = managed_count = 0
    for path, identities in sorted(grouped.items(), key=lambda item: str(item[0])):
        status: Literal["present", "missing", "unreadable"] = "present"
        size = 0
        try:
            value = path.stat()
            if not stat.S_ISREG(value.st_mode) or value.st_size == 0:
                status = "unreadable"
            else:
                size = value.st_size
        except FileNotFoundError:
            status = "missing"
        except OSError:
            status = "unreadable"
        dependency = StorageDependency(path=str(path), artifact_ids=tuple(identities), state=status)
        if status != "present":
            missing.append(dependency)
        registered_bytes += size
        if _inside_owned(path, directories):
            managed_count += 1
            managed_bytes += size
        else:
            external.append(dependency)
    return StorageInspection(
        storage=storage,
        configured=state.storage is not None,
        attempt_count=len(state.nodes),
        registered_file_count=len(grouped),
        registered_bytes=registered_bytes,
        managed_file_count=managed_count,
        managed_bytes=managed_bytes,
        missing=tuple(missing),
        external_dependencies=tuple(external),
    )


def _assert_terminal(state: _State) -> None:
    tables = dict(state.tables)
    # runs.state 在第三列，node_runs.state 在第六列，均沿用正式 Store 的固定列结构。
    if any(row[2] not in {"completed", "failed"} for row in tables["runs"]) or any(
        row[5] not in {"completed", "failed"} for row in tables["node_runs"]
    ):
        raise _failure("ACTIVE", "请先完成或放弃所有运行和外部等待任务，再迁移工程数据")


def _inventory(directories: tuple[Path, ...], root: Path) -> tuple[_File, ...]:
    files: list[_File] = []
    for directory in directories:
        if not directory.exists():
            continue
        _safe_path(directory)
        if not directory.is_dir():
            raise _failure("ATTEMPT", "attempt 位置不是目录")
        for current, child_dirs, child_files in os.walk(directory, followlinks=False):
            for name in child_dirs:
                _safe_path(Path(current) / name)
            for name in child_files:
                path = Path(current) / name
                files.append(_File(path, path.relative_to(root), _identity(path)))
    return tuple(sorted(files, key=lambda item: str(item.relative)))


class StorageMigrationManager:
    """短期票据绑定一次显式迁移；不接受客户端传入任意源路径或 SQL 更新。"""

    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._tickets: dict[str, _Ticket] = {}
        self._lock = threading.Lock()

    def preview(
        self,
        store: ProjectStore,
        *,
        legacy_root: str | Path,
        target: ProjectStorage,
        project_session_id: str,
        expected_storage_revision: int,
    ) -> StorageMigrationPreview:
        """只读核对固定来源与空目标，不建立任何媒体目录；预览不代表已执行迁移。"""

        target = ProjectStorage.model_validate(target)
        if target.mode == "legacy":
            raise _failure("TARGET", "迁移目标必须是工程旁或自定义数据位置")
        state = _state(store)
        store._check_storage_revision(state.revision, expected_storage_revision)
        _assert_terminal(state)
        source = state.storage or legacy_project_storage(legacy_root)
        old_root = _safe_path(Path(source.attempts_root), missing=True)
        target_root = _safe_path(Path(target.data_root), missing=True)
        if old_root.is_relative_to(target_root) or target_root.is_relative_to(old_root):
            raise _failure("OVERLAP", "迁移来源和目标不得相同、互相嵌套或包含工程文件")
        project = store.path.resolve(strict=True)
        if project.is_relative_to(target_root):
            raise _failure("OVERLAP", "数据目标不得包含当前工程文件")
        if target_root.exists():
            raise _failure("TARGET_EXISTS", "迁移目标必须是尚不存在的新目录，禁止覆盖旧数据")
        directories = _owned_directories(state, source)
        inspection = inspect_storage(store, legacy_root)
        if inspection.missing:
            raise _failure("MISSING", "已登记文件缺失或不可读，请修复归档检查中的项目后再迁移")
        files = _inventory(directories, old_root)
        preview = StorageMigrationPreview(
            ticket_id=str(uuid4()),
            project_session_id=project_session_id,
            expected_storage_revision=expected_storage_revision,
            source=source,
            target=target,
            attempt_count=len(directories),
            file_count=len(files),
            byte_count=sum(item.identity.size for item in files),
            external_dependencies=inspection.external_dependencies,
        )
        with self._lock:
            self._tickets = {
                key: item for key, item in self._tickets.items() if item.expires_at > self._clock()
            }
            if len(self._tickets) >= 16:
                self._tickets.pop(next(iter(self._tickets)))
            self._tickets[preview.ticket_id] = _Ticket(
                preview, project, Path(legacy_root), state, directories, files, self._clock() + 900
            )
        return preview

    def confirm(
        self,
        store: ProjectStore,
        *,
        ticket_id: str,
        project_session_id: str,
        expected_storage_revision: int,
    ) -> StorageInspection:
        """复制并验证全部文件后事务切引用；任何失败均保留源文件和原数据库引用。"""

        with self._lock:
            ticket = self._tickets.pop(ticket_id, None)
        if (
            ticket is None
            or ticket.expires_at <= self._clock()
            or ticket.project_path != store.path.resolve(strict=True)
            or ticket.preview.project_session_id != project_session_id
            or ticket.preview.expected_storage_revision != expected_storage_revision
        ):
            raise _failure("TICKET", "迁移预览已失效或工程会话已改变，请重新预览")
        if _state(store) != ticket.state:
            raise _failure("CHANGED", "工程记录已变化，请重新预览迁移")
        old_root = Path(ticket.preview.source.attempts_root)
        target_root = Path(ticket.preview.target.data_root)
        target_attempts = Path(ticket.preview.target.attempts_root)
        copied: list[tuple[Path, _Identity]] = []
        try:
            _safe_path(target_root, missing=True)
            target_root.mkdir(parents=False, exist_ok=False)
            target_attempts.mkdir()
            for directory in ticket.directories:
                if directory.exists():
                    (target_attempts / directory.name).mkdir()
                    # 空的 outputs/incoming 也是任务目录的一部分，不能只复制有文件的路径。
                    for current, child_dirs, _ in os.walk(directory, followlinks=False):
                        for name in child_dirs:
                            child = _safe_path(Path(current) / name)
                            (target_attempts / child.relative_to(old_root)).mkdir(
                                parents=True, exist_ok=True
                            )
            for item in ticket.files:
                target = target_attempts / item.relative
                target.parent.mkdir(parents=True, exist_ok=True)
                copied.append((target, self._copy_verified(item, target)))
            if _inventory(ticket.directories, old_root) != ticket.files:
                raise _failure("CHANGED", "复制期间源目录或文件发生变化，未切换工程引用")
            self._commit(store, ticket, tuple(copied))
        except ProjectStoreError:
            # 故障留下的副本不自动删除，避免清理与外部用户写入竞态；旧引用和源文件均保留。
            raise
        except (OSError, sqlite3.Error) as error:
            raise _failure("MIGRATION", "迁移未完成；源文件保留，目标副本也保留供检查") from error
        return inspect_storage(store, ticket.legacy_root)

    @staticmethod
    def _copy_verified(item: _File, target: Path) -> _Identity:
        if _identity(item.source) != item.identity:
            raise _failure("CHANGED", "源文件与预览时不同，请完成外部写入后重新迁移")
        with target.open("xb"):
            pass
        shutil.copy2(item.source, target)
        target_identity = _identity(target)
        with item.source.open("rb") as source_stream, target.open("rb") as target_stream:
            while True:
                source_chunk = source_stream.read(_CHUNK_SIZE)
                if source_chunk != target_stream.read(_CHUNK_SIZE):
                    raise _failure("COPY_VERIFY", "副本与来源字节不一致，未切换工程引用")
                if not source_chunk:
                    break
        if (
            _identity(item.source) != item.identity
            or _identity(target) != target_identity
            or target_identity.size != item.identity.size
        ):
            raise _failure("CHANGED", "复制期间源文件或目标副本发生变化，未切换工程引用")
        return target_identity

    @staticmethod
    def _commit(
        store: ProjectStore, ticket: _Ticket, copied: tuple[tuple[Path, _Identity], ...]
    ) -> None:
        target = ticket.preview.target
        old_root = Path(ticket.preview.source.attempts_root)
        new_root = Path(target.attempts_root)

        def assert_copies_unchanged() -> None:
            # 目标逐文件完成验证后仍可能被外部替换；事务切引用前不能只信任最后一次 stat。
            _safe_path(new_root)
            for path, identity in copied:
                if _identity(path) != identity:
                    raise _failure(
                        "CHANGED", "已验证的目标副本又发生变化；未切换引用，源文件和副本均保留"
                    )

        def remap(raw: str | None) -> str | None:
            if raw is None:
                return None
            path = Path(raw)
            if path == old_root or _inside_owned(path, ticket.directories):
                return str(new_root / path.relative_to(old_root))
            return raw

        with closing(sqlite3.connect(store.path)) as connection:
            store._configure_connection(connection)
            connection.execute("BEGIN IMMEDIATE")
            assert_copies_unchanged()
            current = _read_state(connection, store)
            if current != ticket.state:
                raise _failure("CHANGED", "工程记录在复制期间改变，未切换任何引用")
            _assert_terminal(current)
            store._upgrade_storage_schema(connection)
            for node_run_id, work_dir, log_path, handoff_json in current.nodes:
                if handoff_json is not None:
                    handoff = ExternalHandoff.model_validate_json(handoff_json)
                    handoff = handoff.model_copy(
                        update={
                            "output_targets": tuple(
                                item.model_copy(update={"path": remap(item.path)})
                                for item in handoff.output_targets
                            )
                        }
                    )
                    handoff_json = handoff.model_dump_json()
                connection.execute(
                    "UPDATE node_runs SET work_dir = ?, log_path = ?, external_handoff_json = ? "
                    "WHERE node_run_id = ?",
                    (remap(work_dir), remap(log_path), handoff_json, node_run_id),
                )
            for artifact_id, raw in current.artifacts:
                mapped = remap(raw)
                if mapped != raw:
                    assert mapped is not None
                    value = Path(mapped).stat()
                    connection.execute(
                        "UPDATE artifacts SET path = ?, size = ?, mtime_ns = ? "
                        "WHERE artifact_id = ?",
                        (mapped, value.st_size, value.st_mtime_ns, artifact_id),
                    )
            store._write_project_storage(connection, target, current.revision)
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise _failure("REFERENCE", "迁移后的工程引用校验失败")
            assert_copies_unchanged()
            connection.commit()
