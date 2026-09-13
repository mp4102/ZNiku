"""检查工程资产依赖并显式迁移该工程拥有的 attempt，源文件始终保留。

迁移是操作者主动触发的存储维护，不创建 Run、Artifact、receipt 或归档 authority。复制只操作
数据库精确列出的 UUID 或可读 attempt 子树；完成分块比对后，在一个 SQLite 事务内切换定位引用。
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
from zniku.runtime.models import ExternalHandoff, Run
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


class StoragePathMapping(ProjectModel):
    """预览一个已绑定 attempt 的实体位置变更，不授权其他目录。"""

    source: str
    target: str


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
    operation: Literal["relocate", "organize", "restore"] = "relocate"
    path_mappings: tuple[StoragePathMapping, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class _State:
    revision: int
    storage: ProjectStorage | None
    tables: tuple[tuple[str, tuple[tuple[Any, ...], ...]], ...]
    nodes: tuple[tuple[str, str, str | None, str | None], ...]
    artifacts: tuple[tuple[str, str], ...]
    runs: tuple[Run, ...]


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
    mappings: tuple[tuple[Path, Path], ...] = ()
    restored: tuple[tuple[Path, _Identity], ...] = ()
    source_available: bool = True
    restored_directories: tuple[tuple[Path, _Identity], ...] = ()


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


def _directory_identity(path: Path) -> _Identity:
    _safe_path(path)
    value = path.stat()
    if not stat.S_ISDIR(value.st_mode):
        raise _failure("ATTEMPT", "已绑定工作或日志位置不是目录")
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
        runs = tuple(
            repository._read_run(connection, row["run_id"])
            for row in connection.execute("SELECT run_id FROM runs ORDER BY rowid")
        )
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
        runs=runs,
    )


def _state(store: ProjectStore) -> _State:
    store.load()  # 正式 Store 拒绝未知格式和损坏 Graph；不靠 maintenance 绕过读取门禁。
    with closing(sqlite3.connect(store.path)) as connection:
        store._configure_connection(connection)
        connection.execute("BEGIN")
        return _read_state(connection, store)


def _owned_directories(state: _State, storage: ProjectStorage) -> tuple[Path, ...]:
    from zniku.project.storage_layout import safe_attempt_directory

    root = Path(storage.attempts_root)
    result: set[Path] = set()
    attempts = {item.node_run_id: item for run in state.runs for item in run.node_runs}
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
        if storage.layout == "uuid":
            if work != root / node_uuid.hex:
                raise _failure("ATTEMPT", "执行目录不属于工程已绑定的 UUID attempt 根")
        else:
            attempt = attempts[node_run_id]
            location = storage.layout_state.nodes.get(attempt.node_id)
            run_number = storage.layout_state.runs.get(attempt.run_id)
            if location is None or run_number is None:
                raise _failure("ATTEMPT", "可读执行目录缺少已保存的节点或运行定位")
            expected = root.joinpath(*location.relative_dir.split("/")) / (
                f"R{run_number:03d}-A{attempt.attempt:03d}"
            )
            if work != expected:
                raise _failure("ATTEMPT", "可读执行目录与工程精确定位记录不同")
        try:
            safe_attempt_directory(root, work)
        except ValueError as error:
            raise _failure("ATTEMPT", "执行目录不属于工程安全绑定的 attempt 根") from error
        result.add(work)
    ordered = tuple(sorted(result, key=str))
    if any(
        left.is_relative_to(right) or right.is_relative_to(left)
        for index, left in enumerate(ordered)
        for right in ordered[index + 1 :]
    ):
        raise _failure("ATTEMPT", "已绑定 attempt 目录不得相同或互相嵌套")
    return ordered


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


def _mapped_path(path: Path, mappings: tuple[tuple[Path, Path], ...]) -> Path:
    for source, target in mappings:
        if path.is_relative_to(source):
            return target / path.relative_to(source)
    return path


def _target_layout(
    state: _State, source: ProjectStorage, target: ProjectStorage, *, organize: bool
) -> tuple[ProjectStorage, tuple[tuple[Path, Path], ...]]:
    """历史整理复用 Runtime 的纯分配器；普通迁移保留既有布局和编号。"""

    from zniku.project.storage_layout import StorageLayoutState, allocate_attempt_path

    from .storage_naming import resolve_attempt_naming

    directories = _owned_directories(state, source)
    old_root, new_root = Path(source.attempts_root), Path(target.attempts_root)
    if not organize:
        payload = target.model_dump(mode="python")
        payload.update(layout=source.layout, layout_state=source.layout_state)
        if source.data_id is not None:
            payload["data_id"] = source.data_id
        target = ProjectStorage.model_validate(payload)
        return target, tuple((path, new_root / path.relative_to(old_root)) for path in directories)
    layout = source.layout_state if source.layout == "readable" else StorageLayoutState()
    mapped: dict[Path, Path] = {}
    for run in state.runs:
        nodes = {node.node_id: node for node in run.graph_snapshot.nodes}
        definitions = {(item.type_id, item.version): item for item in run.definitions_snapshot}
        for attempt in run.node_runs:
            old = Path(attempt.work_dir)
            if old == old_root:
                continue
            node = nodes[attempt.node_id]
            definition = definitions[(node.type_id, node.definition_version)]
            try:
                layout, new = allocate_attempt_path(
                    layout,
                    root=new_root,
                    node_id=attempt.node_id,
                    run_id=run.run_id,
                    attempt=attempt.attempt,
                    hint=resolve_attempt_naming(run, node, definition),
                )
            except (ValueError, OSError) as error:
                raise _failure(
                    "PATH", "可读目录无法安全分配，请使用更短且可用的数据位置"
                ) from error
            mapped[old] = new
    if set(mapped) != set(directories) or len(set(mapped.values())) != len(mapped):
        raise _failure("ATTEMPT", "整理后的执行目录绑定缺失或重复")
    payload = target.model_dump(mode="python")
    payload.update(contract_version="0.3.2", layout="readable", layout_state=layout)
    if source.data_id is not None:
        payload["data_id"] = source.data_id
    return ProjectStorage.model_validate(payload), tuple(
        sorted(mapped.items(), key=lambda x: str(x[0]))
    )


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
        organize: bool = False,
    ) -> StorageMigrationPreview:
        """只读核对固定来源与空目标，不建立任何媒体目录；预览不代表已执行迁移。"""

        target = ProjectStorage.model_validate(target)
        if target.mode == "legacy":
            raise _failure("TARGET", "迁移目标必须是工程旁或自定义数据位置")
        state = _state(store)
        store._check_storage_revision(state.revision, expected_storage_revision)
        _assert_terminal(state)
        source = state.storage or legacy_project_storage(legacy_root)
        target, mappings = _target_layout(state, source, target, organize=organize)
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
            operation="organize" if organize else "relocate",
            path_mappings=tuple(
                StoragePathMapping(source=str(old), target=str(new)) for old, new in mappings
            ),
        )
        with self._lock:
            self._tickets = {
                key: item for key, item in self._tickets.items() if item.expires_at > self._clock()
            }
            if len(self._tickets) >= 16:
                self._tickets.pop(next(iter(self._tickets)))
            self._tickets[preview.ticket_id] = _Ticket(
                preview,
                project,
                Path(legacy_root),
                state,
                directories,
                files,
                self._clock() + 900,
                mappings,
            )
        return preview

    def preview_restore(
        self,
        store: ProjectStore,
        *,
        legacy_root: str | Path,
        target: ProjectStorage,
        project_session_id: str,
        expected_storage_revision: int,
    ) -> StorageMigrationPreview:
        """核对操作者已复制的数据根；源缺失时只承诺登记文件的低成本属性匹配。

        未登记文件和插件隐含依赖没有可比较的历史清单，明确不把重新定位称为完整归档验收。
        不接受目录名相似作为绑定，也不重新定位数据根外的原素材与成片。
        """

        from .storage_owner import verify_storage_owner

        state = _state(store)
        store._check_storage_revision(state.revision, expected_storage_revision)
        _assert_terminal(state)
        source = state.storage or legacy_project_storage(legacy_root)
        target, mappings = _target_layout(state, source, target, organize=False)
        if source.data_id is None:
            payload = target.model_dump(mode="python")
            payload["data_id"] = None
            target = ProjectStorage.model_validate(payload)
        old_root = _safe_path(Path(source.attempts_root), missing=True)
        new_root = _safe_path(Path(target.attempts_root))
        data_root = _safe_path(Path(target.data_root))
        if (
            old_root.is_relative_to(data_root)
            or data_root.is_relative_to(old_root)
            or store.path.resolve(strict=True).is_relative_to(data_root)
        ):
            raise _failure("OVERLAP", "重新定位来源与目标必须独立，不能包含当前工程文件")
        if not new_root.is_dir():
            raise _failure("TARGET", "请选择已经完整复制的 .data 目录本体")
        directories = _owned_directories(state, source)
        source_available = old_root.exists()
        verify_storage_owner(target, required=target.data_id is not None or not source_available)
        original_files = _inventory(directories, old_root) if source_available else ()
        restored: list[tuple[Path, _Identity]] = []
        restored_directories: dict[Path, _Identity] = {}
        if source_available:
            # 仅枚举工程拥有的子树，不读取其他工程或目标根下未知目录。
            source_relatives = {item.source for item in original_files}
            target_files = _inventory(tuple(new for _, new in mappings), new_root)
            expected_targets = {_mapped_path(item, mappings) for item in source_relatives}
            if {item.source for item in target_files} != expected_targets:
                raise _failure("RESTORE_MISMATCH", "已复制的数据文件清单与当前绑定来源不同")
            for item in original_files:
                candidate = _mapped_path(item.source, mappings)
                restored.append((candidate, self._compare_existing(item, candidate)))
        else:
            repository = RuntimeRepository(store)
            for artifact_id, raw in state.artifacts:
                original = Path(raw)
                if not _inside_owned(original, directories):
                    continue
                artifact = repository.get_artifact(artifact_id)
                if artifact.size is None or artifact.mtime_ns is None:
                    raise _failure(
                        "RESTORE_UNPROVEN", "缺失源目录且登记文件没有尺寸/时间记录，不能自动匹配"
                    )
                candidate = _mapped_path(original, mappings)
                identity = _identity(candidate)
                if identity.size != artifact.size or identity.mtime_ns != artifact.mtime_ns:
                    raise _failure(
                        "RESTORE_MISMATCH", "副本尺寸或修改时间与工程登记值不同，不能重新定位"
                    )
                restored.append((candidate, identity))
        for old, new in mappings:
            should_exist = (
                old.exists()
                if source_available
                else any(log is not None and Path(work) == old for _, work, log, _ in state.nodes)
                or any(Path(raw).is_relative_to(old) for _, raw in state.artifacts)
            )
            if should_exist:
                restored_directories[new] = _directory_identity(new)
        for _, _, log_path, _ in state.nodes:
            if log_path is not None and _inside_owned(Path(log_path), directories):
                candidate = _mapped_path(Path(log_path), mappings)
                if _safe_path(candidate).is_dir():
                    restored_directories[candidate] = _directory_identity(candidate)
                else:
                    restored.append((candidate, _identity(candidate)))
        # 来源尚存但某个登记文件已被用户移走时，也不能把不完整副本当作可切换目标。
        for _, raw in state.artifacts:
            if _inside_owned(Path(raw), directories):
                _identity(_mapped_path(Path(raw), mappings))
        if not source_available and directories and not restored:
            raise _failure(
                "RESTORE_UNPROVEN", "源目录缺失且没有可核对的登记文件，不能仅凭目录名匹配"
            )
        inspection = inspect_storage(store, legacy_root)
        preview = StorageMigrationPreview(
            ticket_id=str(uuid4()),
            project_session_id=project_session_id,
            expected_storage_revision=expected_storage_revision,
            source=source,
            target=target,
            attempt_count=len(directories),
            file_count=len({path for path, _ in restored}),
            byte_count=sum(identity.size for _, identity in dict(restored).items()),
            external_dependencies=inspection.external_dependencies,
            operation="restore",
            path_mappings=tuple(
                StoragePathMapping(source=str(old), target=str(new)) for old, new in mappings
            ),
            warnings=(
                "此操作只重新定位已绑定目录；数据根外原素材与成片保持原路径，需分别保留。",
                "源目录可用：已逐文件比对当前绑定子树。"
                if source_available
                else (
                    "源目录缺失：仅核对已登记文件的尺寸/修改时间及日志存在；"
                    "这不是内容证明，未登记文件和隐含依赖未验收。"
                ),
            ),
        )
        with self._lock:
            self._tickets = {
                key: item for key, item in self._tickets.items() if item.expires_at > self._clock()
            }
            if len(self._tickets) >= 16:
                self._tickets.pop(next(iter(self._tickets)))
            self._tickets[preview.ticket_id] = _Ticket(
                preview,
                store.path.resolve(strict=True),
                Path(legacy_root),
                state,
                directories,
                original_files,
                self._clock() + 900,
                mappings,
                tuple(dict(restored).items()),
                source_available,
                tuple(restored_directories.items()),
            )
        return preview

    @staticmethod
    def _compare_existing(item: _File, target: Path) -> _Identity:
        identity = _identity(target)
        if identity.size != item.identity.size or _identity(item.source) != item.identity:
            raise _failure("RESTORE_MISMATCH", "已复制文件与当前来源不同")
        with item.source.open("rb") as original, target.open("rb") as copied:
            while True:
                chunk = original.read(_CHUNK_SIZE)
                if chunk != copied.read(_CHUNK_SIZE):
                    raise _failure("RESTORE_MISMATCH", "已复制文件与当前来源字节不同")
                if not chunk:
                    break
        if _identity(target) != identity or _identity(item.source) != item.identity:
            raise _failure("CHANGED", "核对期间来源或副本发生变化")
        return identity

    def _confirm_restore(self, store: ProjectStore, ticket: _Ticket) -> None:
        from .storage_owner import verify_storage_owner

        verify_storage_owner(ticket.preview.target, required=not ticket.source_available)
        if ticket.source_available:
            old_root = Path(ticket.preview.source.attempts_root)
            if _inventory(ticket.directories, old_root) != ticket.files:
                raise _failure("CHANGED", "来源已变化，请重新预览")
        for path, identity in ticket.restored:
            if _identity(path) != identity:
                raise _failure("CHANGED", "副本已变化，请重新预览")
        self._commit(store, ticket, ticket.restored)

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
            if ticket.preview.operation == "restore":
                self._confirm_restore(store, ticket)
                return inspect_storage(store, ticket.legacy_root)
            _safe_path(target_root, missing=True)
            target_root.mkdir(parents=False, exist_ok=False)
            target_attempts.mkdir()
            from .storage_owner import create_storage_owner

            create_storage_owner(ticket.preview.target)
            for directory in ticket.directories:
                if directory.exists():
                    _mapped_path(directory, ticket.mappings).mkdir(parents=True)
                    # 空的 outputs/incoming 也是任务目录的一部分，不能只复制有文件的路径。
                    for current, child_dirs, _ in os.walk(directory, followlinks=False):
                        for name in child_dirs:
                            child = _safe_path(Path(current) / name)
                            _mapped_path(child, ticket.mappings).mkdir(parents=True, exist_ok=True)
            for item in ticket.files:
                target = _mapped_path(item.source, ticket.mappings)
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
            from .storage_owner import verify_storage_owner

            # 目标逐文件完成验证后仍可能被外部替换；事务切引用前不能只信任最后一次 stat。
            _safe_path(new_root)
            verify_storage_owner(target, required=target.data_id is not None)
            for path, identity in ticket.restored_directories:
                if _directory_identity(path) != identity:
                    raise _failure("CHANGED", "已核对的工作或日志目录发生变化，未切换引用")
            for path, identity in copied:
                if _identity(path) != identity:
                    raise _failure(
                        "CHANGED", "已验证的目标副本又发生变化；未切换引用，源文件和副本均保留"
                    )

        def remap(raw: str | None) -> str | None:
            if raw is None:
                return None
            path = Path(raw)
            return str(new_root if path == old_root else _mapped_path(path, ticket.mappings))

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
