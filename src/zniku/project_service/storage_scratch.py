"""提供有界存储分类及一次性内部中转清理；正式成果永不纳入删除候选。

只操作当前工程已绑定的独立 attempt。索引只是保守提示，旧未知文件保留；确认重验数据库、
路径、正式引用及文件身份，不更改 Graph、历史结果或复用状态，也不承诺 NAS 物理空间释放。
调用方须持有 Project Service 本机维护互斥；此模块不建立通用 GC 或后台删除任务。
"""

from __future__ import annotations

import os
import re
import shlex
import stat
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Literal
from uuid import uuid4

from zniku.graph import NodeDefinition, NodeInstance
from zniku.media.scratch import (
    INDEX_NAME,
    ScratchIdentity,
    ScratchIndex,
    allowed_path,
    exact_role,
    file_identity,
    read_index,
    safe_path,
    strict_json,
)
from zniku.project.models import ProjectModel
from zniku.project.storage import ProjectStorage, legacy_project_storage
from zniku.project.store import ProjectStore, ProjectStoreError
from zniku.runtime.models import NodeRun

from .storage import _assert_terminal, _failure, _owned_directories, _State, _state
from .storage_owner import verify_storage_owner

MAX_FILES = 20000
MAX_SCAN_ENTRIES = 20000
MAX_DEPTH = 12
MAX_REFERENCE_VALUES = 200000
TICKET_SECONDS: Literal[300] = 300
type ScratchCategory = Literal["archive", "registered_recreatable", "internal_scratch", "unknown"]
_CATEGORIES: tuple[ScratchCategory, ...] = (
    "archive",
    "registered_recreatable",
    "internal_scratch",
    "unknown",
)


class ScratchCategorySummary(ProjectModel):
    category: ScratchCategory
    file_count: int
    byte_count: int


class ScratchFileEntry(ProjectModel):
    path: str
    category: ScratchCategory
    byte_count: int
    node_id: str | None
    node_run_id: str | None
    attempt: int | None
    task_label: str
    chapter_label: str | None
    round_label: str
    role: str
    reason: str
    candidate_id: str | None = None


class ScratchPreview(ProjectModel):
    contract_version: Literal["0.3.0"] = "0.3.0"
    ticket_id: str
    project_session_id: str
    expected_storage_revision: int
    expires_in_seconds: Literal[300] = TICKET_SECONDS
    summary: tuple[ScratchCategorySummary, ...]
    entries: tuple[ScratchFileEntry, ...]
    warnings: tuple[str, ...]
    truncated: bool


class ScratchDeletionEntry(ProjectModel):
    candidate_id: str
    path: str
    byte_count: int
    status: Literal["deleted", "skipped", "failed"]
    message: str


class ScratchConfirmResult(ProjectModel):
    contract_version: Literal["0.3.0"] = "0.3.0"
    project_session_id: str
    expected_storage_revision: int
    ticket_id: str
    entries: tuple[ScratchDeletionEntry, ...]
    deleted_bytes: int
    deletion_count: int
    complete: bool
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class _Attempt:
    node_run: NodeRun
    node: NodeInstance
    definition: NodeDefinition
    path: Path
    role: str | None
    index: ScratchIndex | None
    index_identity: ScratchIdentity | None


@dataclass(frozen=True)
class _Candidate:
    entry: ScratchFileEntry
    identity: ScratchIdentity
    attempt: _Attempt
    directory_identity: tuple[int, int]


@dataclass(frozen=True)
class _Ticket:
    preview: ScratchPreview
    project_path: Path
    legacy_root: Path
    state: _State
    candidates: Mapping[str, _Candidate]
    expires_at: float


def _attempts(state: _State, storage: ProjectStorage) -> dict[Path, _Attempt]:
    owned = set(_owned_directories(state, storage))
    result: dict[Path, _Attempt] = {}
    for run in state.runs:
        nodes = {node.node_id: node for node in run.graph_snapshot.nodes}
        definitions = {(item.type_id, item.version): item for item in run.definitions_snapshot}
        for node_run in run.node_runs:
            path = Path(node_run.work_dir)
            if path not in owned or node_run.reused_from_result_id is not None:
                continue
            node = nodes[node_run.node_id]
            definition = definitions[node.type_id, node.definition_version]
            index = read_index(path)
            identity = None
            if index is not None:
                try:
                    identity = file_identity(path / INDEX_NAME)
                except (OSError, ValueError):
                    index = None
            if index is not None and (
                index.node_run_id != node_run.node_run_id
                or index.type_id != definition.type_id
                or index.definition_version != definition.version
            ):
                index = None
            result[path] = _Attempt(
                node_run, node, definition, path, exact_role(definition), index, identity
            )
    return result


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.absolute()))


def _references(store: ProjectStore, state: _State) -> set[Path]:
    """从全部正式历史与当前 Graph 取明确路径，并有限展开被正式引用的 JSON/ffconcat。"""

    references = {store.path.absolute(), *(Path(path).absolute() for _, path in state.artifacts)}
    pending: list[object] = []
    snapshot = store.load()
    for graph, definitions in [
        (snapshot.project.graph, snapshot.definitions),
        *((run.graph_snapshot, run.definitions_snapshot) for run in state.runs),
    ]:
        catalog = {(item.type_id, item.version): item for item in definitions}
        for node in graph.nodes:
            parameters = node.model_dump(mode="json")["parameters"]
            definition = catalog[node.type_id, node.definition_version]
            if exact_role(definition) == "final":
                # 已知发布 exact 的 output_root 只是父目录，不是读取整个子树的文件引用。
                # 未知插件不获得这项豁免：其任何明确目录引用仍保守保护整个子树。
                parameters.pop("output_root", None)
            pending.append(parameters)
        pending.extend(item.model_dump(mode="json") for item in definitions)
    for table, rows in state.tables:
        for row in rows:
            # work_dir 只是归属根，不能误认为对整个子树的媒体依赖；其余 JSON 路径均保留。
            for column, value in enumerate(row):
                if table == "runs" and column in {3, 4}:
                    continue  # 上方已按精确定义检查过 Graph/definitions，避免把输出父目录当输入。
                if isinstance(value, str) and value.startswith(("{", "[")):
                    pending.append(strict_json(value))
        if table == "node_runs":
            references.update(Path(row[14]).absolute() for row in rows if row[14])
    count = 0

    def collect() -> None:
        nonlocal count
        while pending:
            count += 1
            if count > MAX_REFERENCE_VALUES:
                raise _failure("SCRATCH_BOUND", "引用检查超过预算；未删除任何文件")
            value = pending.pop()
            if isinstance(value, dict):
                pending.extend(value.values())
            elif isinstance(value, tuple | list):
                pending.extend(value)
            elif isinstance(value, str) and "\x00" not in value and Path(value).is_absolute():
                references.add(Path(value).absolute())

    collect()
    inspected: set[Path] = set()
    metadata_bytes = 0
    while True:
        manifests = [
            p for p in references - inspected if p.suffix.lower() in {".json", ".ffconcat"}
        ]
        if not manifests:
            break
        for path in manifests:
            inspected.add(path)
            try:
                if len(inspected) > 256:
                    raise ValueError("正式引用文件数量超过预算")
                safe_path(path)
                if path.stat().st_size > 1024 * 1024:
                    raise ValueError("引用文件超过读取预算")
                with path.open("r", encoding="utf-8") as stream:
                    content = stream.read(1024 * 1024 + 1)
                if len(content) > 1024 * 1024:
                    raise ValueError("引用文件超过读取预算")
                metadata_bytes += len(content.encode("utf-8"))
                if metadata_bytes > 4 * 1024 * 1024:
                    raise ValueError("正式引用文件总量超过预算")
                if path.suffix.lower() == ".json":
                    pending.append(strict_json(content))
                else:
                    for line in content.splitlines():
                        if line.lstrip().startswith("#"):
                            continue
                        values = shlex.split(line, comments=False)
                        if not values:
                            continue
                        directive = values[0]
                        if directive == "file":
                            if len(values) != 2 or not values[1]:
                                raise ValueError("无法解析正式 concat 依赖")
                            target = Path(values[1])
                            references.add(target if target.is_absolute() else path.parent / target)
                        elif directive == "ffconcat":
                            if values != ["ffconcat", "version", "1.0"]:
                                raise ValueError("concat 版本未知")
                        elif (
                            directive not in {"duration", "inpoint", "outpoint"}
                            or len(values) != 2
                            or re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?", values[1]) is None
                        ):
                            # 未知指令可能携带额外路径，不把部分解析声称完整依赖覆盖。
                            raise ValueError("concat 指令超出维护读取的闭合语法")
            except (OSError, ValueError) as error:
                raise _failure(
                    "SCRATCH_REFERENCES", "正式引用无法完整读取；请先修复缺失依赖"
                ) from error
        collect()
    try:
        return {path.resolve(strict=False) for path in references}
    except (OSError, RuntimeError) as error:
        raise _failure("SCRATCH_REFERENCES", "正式引用路径无法解析；未删除任何文件") from error


def _referenced(path: Path, references: set[Path]) -> bool:
    return any(path == reference or path.is_relative_to(reference) for reference in references)


def _labels(attempt: _Attempt | None, storage: ProjectStorage) -> dict[str, object]:
    if attempt is None:
        return {
            "node_id": None,
            "node_run_id": None,
            "attempt": None,
            "task_label": "工程与外部依赖",
            "chapter_label": None,
            "round_label": "—",
        }
    node_run = attempt.node_run
    names = {
        "context": "FI 上下文准备",
        "crop": "FI 精确裁边",
        "merge": "增强章合并",
        "split": "分章分叶",
        "fi": "外部章节补帧",
        "enhancement": "外部增强",
    }
    chapter = attempt.node.parameters.get("chapter")
    ordinal = chapter.get("ordinal") if isinstance(chapter, Mapping) else None
    chapter_label = f"第 {ordinal + 1} 章" if type(ordinal) is int else None
    binding = storage.english_layout_state.attempts.get(node_run.node_run_id)
    return {
        "node_id": node_run.node_id,
        "node_run_id": node_run.node_run_id,
        "attempt": node_run.attempt,
        "task_label": names.get(attempt.role or "", node_run.node_id),
        "chapter_label": chapter_label,
        "round_label": f"round-{binding.round:03d}" if binding else f"attempt-{node_run.attempt}",
    }


def _enumerate(attempts: Mapping[Path, _Attempt]) -> tuple[list[Path], bool, list[str]]:
    files: list[Path] = []
    warnings: list[str] = []
    truncated = False
    scanned = 0
    for root in attempts:
        if scanned >= MAX_SCAN_ENTRIES:
            return files, True, warnings
        scanned += 1
        if not root.exists():
            warnings.append("部分已绑定 attempt 目录缺失；不会创建或认领替代位置。")
            continue
        safe_path(root)
        pending = [(root, 0)]
        while pending:
            if scanned >= MAX_SCAN_ENTRIES:
                return files, True, warnings
            directory, depth = pending.pop()
            scanned += 1
            with os.scandir(directory) as children:
                for child in children:
                    scanned += 1
                    if scanned > MAX_SCAN_ENTRIES:
                        return files, True, warnings
                    path = Path(child.path)
                    value = child.stat(follow_symlinks=False)
                    if stat.S_ISDIR(value.st_mode) and not (
                        stat.S_ISLNK(value.st_mode)
                        or getattr(value, "st_file_attributes", 0) & 0x400
                    ):
                        if depth >= MAX_DEPTH:
                            truncated = True
                        else:
                            pending.append((path, depth + 1))
                    else:
                        files.append(path)
                    if len(files) + len(pending) > MAX_FILES:
                        return files[:MAX_FILES], True, warnings
    return files, truncated, warnings


def _classify(
    path: Path,
    attempt: _Attempt | None,
    storage: ProjectStorage,
    registered: Mapping[str, str | None],
    references: set[Path],
) -> tuple[ScratchFileEntry, _Candidate | None]:
    category: ScratchCategory = "unknown"
    role = "保留文件"
    reason = "没有可核实的内部中转创建记录；保留未知文件。"
    size = 0
    candidate: _Candidate | None = None
    identity = None
    try:
        value = path.lstat()
        size = value.st_size if stat.S_ISREG(value.st_mode) else 0
        identity = file_identity(path)
    except (OSError, ValueError):
        reason = "文件缺失、不可读、链接或身份无法可靠核实；不允许清理。"
    registered_role = registered.get(_path_key(path))
    if _path_key(path) in registered:
        category = (
            "registered_recreatable"
            if registered_role in {"split", "merge", "context", "crop"}
            else "archive"
        )
        reason = "已登记正式成果，包含历史结果；本版不提供成果回收。" if identity else reason
        role = "已登记媒体"
    elif _referenced(path, references):
        category, reason = "archive", "被当前工程或历史正式记录引用；必须保留。"
    elif attempt is not None:
        relative = path.relative_to(attempt.path)
        if relative.parts[0] in {"incoming", "logs"}:
            category, reason = "archive", "外部来件或执行日志始终保留。"
        elif attempt.index is not None and identity is not None:
            index = attempt.index
            records = [item for item in index.entries if item.relative_path == relative.as_posix()]
            if str(path) in index.declared_outputs:
                category, reason = "archive", "声明输出位置，未登记也不属于内部中转。"
            elif len(records) == 1:
                record = records[0]
                if record.identity == identity and allowed_path(
                    record.relative_path, record.role, attempt.role
                ):
                    category, role = "internal_scratch", record.role
                    reason = "已知精确定义产生的独占内部中转，当前无正式引用；可显式确认清理。"
                    info = attempt.path.stat()
                    if info.st_ino <= 0:
                        return ScratchFileEntry.model_validate(
                            {
                                "path": str(path),
                                "category": "unknown",
                                "byte_count": size,
                                "role": role,
                                "reason": "attempt 目录身份无法可靠核实；必须保留。",
                                **_labels(attempt, storage),
                            }
                        ), None
                    entry = ScratchFileEntry.model_validate(
                        dict(
                            path=str(path),
                            category=category,
                            byte_count=size,
                            role=role,
                            reason=reason,
                            candidate_id=str(uuid4()),
                            **_labels(attempt, storage),
                        )
                    )
                    candidate = _Candidate(entry, identity, attempt, (info.st_dev, info.st_ino))
                    return entry, candidate
                reason = "内部提示与当前文件身份或精确定义不符；保留，不能重新认领。"
    return ScratchFileEntry.model_validate(
        dict(
            path=str(path),
            category=category,
            byte_count=size,
            role=role,
            reason=reason,
            **_labels(attempt, storage),
        )
    ), candidate


class ScratchManager:
    """一次性预览句柄不接收任意删除路径；缺失/过期或任何状态变化均要求重扫。"""

    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._tickets: dict[str, _Ticket] = {}
        self._lock = threading.Lock()

    def preview(
        self,
        store: ProjectStore,
        *,
        legacy_root: Path,
        project_session_id: str,
        expected_storage_revision: int,
    ) -> ScratchPreview:
        state = _state(store)
        store._check_storage_revision(state.revision, expected_storage_revision)
        _assert_terminal(state)
        storage = state.storage or legacy_project_storage(legacy_root)
        if storage.data_id is not None:
            verify_storage_owner(storage)
        attempts = _attempts(state, storage)
        references = _references(store, state)
        for attempt in attempts.values():
            if attempt.index is not None:
                references.update(Path(path) for path in attempt.index.declared_outputs)
        registered: dict[str, str | None] = {
            _path_key(Path(path)): None for _, path in state.artifacts
        }
        artifact_paths = dict(state.artifacts)
        for run in state.runs:
            for node_run in run.node_runs:
                owner = attempts.get(Path(node_run.work_dir))
                for artifact_id in node_run.output_artifact_ids:
                    raw_path = artifact_paths[artifact_id]
                    if owner is not None:
                        registered[_path_key(Path(raw_path))] = owner.role
        paths, truncated, warnings = _enumerate(attempts)
        # 只补充已登记根外依赖和工程，不扫描用户媒体目录。
        seen = {_path_key(path) for path in paths}
        for path in [store.path, *(Path(raw) for _, raw in state.artifacts)]:
            if _path_key(path) not in seen:
                if len(paths) >= MAX_FILES:
                    truncated = True
                    break
                paths.append(path)
                seen.add(_path_key(path))
        entries: list[ScratchFileEntry] = []
        candidates: dict[str, _Candidate] = {}
        for path in sorted(paths, key=str):
            owner = next(
                (attempt for root, attempt in attempts.items() if path.is_relative_to(root)), None
            )
            entry, candidate = _classify(path, owner, storage, registered, references)
            if truncated and candidate is not None:
                entry = ScratchFileEntry.model_validate(
                    {
                        **entry.model_dump(),
                        "category": "unknown",
                        "candidate_id": None,
                        "reason": "扫描达到预算；保留，未生成清理资格。",
                    }
                )
            elif candidate is not None:
                assert entry.candidate_id is not None
                candidates[entry.candidate_id] = candidate
            entries.append(entry)
        warnings.extend(
            (
                "逻辑文件长度不等于 NAS 物理占用；快照可能继续占用空间。",
                "仅检查已绑定 attempt 和明确正式引用；未知文件及旧无索引中转保留。",
                "所有文件默认保留；扫描不删除，确认只处理明确选中的内部中转。",
            )
        )
        if truncated:
            warnings.append("已达到扫描预算，本次汇总不完整且不能清理。")
        preview = ScratchPreview(
            ticket_id=str(uuid4()),
            project_session_id=project_session_id,
            expected_storage_revision=expected_storage_revision,
            summary=tuple(
                ScratchCategorySummary(
                    category=category,
                    file_count=sum(item.category == category for item in entries),
                    byte_count=sum(
                        item.byte_count for item in entries if item.category == category
                    ),
                )
                for category in _CATEGORIES
            ),
            entries=tuple(entries),
            warnings=tuple(warnings),
            truncated=truncated,
        )
        if _state(store) != state:
            raise _failure("SCRATCH_CHANGED", "扫描期间工程发生变化，请重新扫描")
        with self._lock:
            self._tickets = {
                key: value
                for key, value in self._tickets.items()
                if value.expires_at > self._clock()
                and value.preview.project_session_id != project_session_id
            }
            if len(self._tickets) >= 8:
                self._tickets.clear()
            self._tickets[preview.ticket_id] = _Ticket(
                preview, store.path, legacy_root, state, candidates, self._clock() + TICKET_SECONDS
            )
        return preview

    def confirm(
        self,
        store: ProjectStore,
        *,
        project_session_id: str,
        expected_storage_revision: int,
        ticket_id: str,
        candidate_ids: tuple[str, ...],
    ) -> ScratchConfirmResult:
        with self._lock:
            ticket = self._tickets.pop(ticket_id, None)
        if ticket is None or ticket.expires_at <= self._clock():
            raise _failure("SCRATCH_TICKET", "清理预览已失效，请重新扫描；不会自动重试")
        if (
            ticket.project_path != store.path
            or ticket.preview.project_session_id != project_session_id
            or ticket.preview.expected_storage_revision != expected_storage_revision
        ):
            raise _failure("SCRATCH_TICKET", "清理预览不属于当前工程会话或版本")
        if (
            not candidate_ids
            or len(candidate_ids) != len(set(candidate_ids))
            or any(identity not in ticket.candidates for identity in candidate_ids)
        ):
            raise _failure("SCRATCH_SELECTION", "请选择本次预览中不重复的内部中转候选")
        state = _state(store)
        _assert_terminal(state)
        if state != ticket.state:
            raise _failure("SCRATCH_CHANGED", "工程或正式引用发生变化，请重新扫描")
        storage = state.storage or legacy_project_storage(ticket.legacy_root)
        if storage.data_id is not None:
            verify_storage_owner(storage)
        _owned_directories(state, storage)
        references = _references(store, state)
        selected = [ticket.candidates[identity] for identity in candidate_ids]
        # 在第一项副作用之前验证整个选择，改变的文件不能以旧票据删除。
        for candidate in selected:
            self._validate_candidate(candidate, references)
        results: list[ScratchDeletionEntry] = []
        for candidate in selected:
            entry = candidate.entry
            assert entry.candidate_id is not None
            status: Literal["deleted", "skipped", "failed"] = "deleted"
            message = "内部中转已删除；未改动正式成果或工程记录。"
            try:
                self._validate_candidate(candidate, references)
                Path(entry.path).unlink()
            except (ValueError, ProjectStoreError):
                status, message = "skipped", "文件状态在清理期间变化，已保留；请重新扫描。"
            except OSError as error:
                status, message = "failed", f"文件删除未完成或结果待确认；不会重试：{error}"
            results.append(
                ScratchDeletionEntry(
                    candidate_id=entry.candidate_id,
                    path=entry.path,
                    byte_count=entry.byte_count,
                    status=status,
                    message=message,
                )
            )
        return ScratchConfirmResult(
            project_session_id=project_session_id,
            expected_storage_revision=expected_storage_revision,
            ticket_id=ticket_id,
            entries=tuple(results),
            deleted_bytes=sum(item.byte_count for item in results if item.status == "deleted"),
            deletion_count=sum(item.status == "deleted" for item in results),
            complete=all(item.status == "deleted" for item in results),
            warnings=(
                "只报告成功删除的逻辑字节；NAS 快照或去重可能使物理释放量不同。",
                "本次票据已消耗；部分失败请重新扫描，不要重放确认请求。",
            ),
        )

    @staticmethod
    def _validate_candidate(candidate: _Candidate, references: set[Path]) -> None:
        attempt = candidate.attempt
        path = Path(candidate.entry.path)
        try:
            safe_path(attempt.path)
            info = attempt.path.stat()
            if (info.st_dev, info.st_ino) != candidate.directory_identity:
                raise ValueError("attempt 已替换")
            if (
                not path.is_relative_to(attempt.path)
                or path == attempt.path
                or _referenced(path, references)
                or file_identity(path) != candidate.identity
                or file_identity(attempt.path / INDEX_NAME) != attempt.index_identity
                or read_index(attempt.path) != attempt.index
            ):
                raise ValueError("文件/索引/引用已变化")
        except (OSError, ValueError) as error:
            raise _failure("SCRATCH_CHANGED", "候选路径、身份或引用发生变化，请重新扫描") from error
