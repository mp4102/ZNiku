"""为一个人工节点收取多份来件，并在全部齐全后复用完整节点校验。

预览只保存短时本机选择意图；确认把外部文件复制到当前 attempt 的 incoming，或原位收纳
该 attempt 统一收件根的文件，不产生 Artifact。
检查只在完整 validator 通过后发布声明目标，仍须用户另行 Submit。部分收件是普通文件，
不是 Runtime checkpoint；失败保留源、已有产物和可恢复来件，不读取其他任务的目录。
"""

from __future__ import annotations

import os
import secrets
import shutil
import tempfile
import threading
from collections import Counter, OrderedDict
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field, StringConstraints, ValidationError, field_validator

from zniku.runtime import RunnerError

from .handoff_import import (
    IMPORT_TTL_SECONDS,
    ImportAuthority,
    _FileIdentity,
    _identity,
    _incoming_path,
    _safe_path,
    _target_path,
)
from .handoff_inbox import HandoffInboxManager
from .host_bridge import (
    HostBridgeFailure,
    HostBridgeModel,
    HostBridgeSession,
    HostIdentifier,
    HostLocalPath,
    HostOpaqueId,
    HostRandomId,
    PickerSelectionReference,
)
from .models import ExternalHandoffReadiness

if TYPE_CHECKING:
    from .service import ProjectServiceApplication

MAX_BATCH_FILES = 256
MAX_BATCH_TARGETS = 10_000
MAX_DIRECTORY_ENTRIES = 1024
FileName = Annotated[str, StringConstraints(min_length=1, max_length=255)]


class _BatchModel(HostBridgeModel):
    @field_validator(
        "selection_handles",
        "items",
        "overwrite_ports",
        "rows",
        "candidates",
        "matches",
        "results",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def normalize_arrays(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class HandoffBatchBinding(_BatchModel):
    """绑定当前工程会话中一个最新 waiting handoff，不接受任何原始路径。"""

    contract_version: Literal["0.3.0"]
    project_session_id: HostRandomId
    run_id: HostRandomId
    node_run_id: HostRandomId
    handoff_id: HostRandomId


class HandoffBatchRow(HostBridgeModel):
    port_id: HostIdentifier
    ordinal: None = None
    target_name: FileName
    target_path: HostLocalPath
    incoming_path: HostLocalPath
    collected: bool
    target_exists: bool
    size: Annotated[int, Field(ge=0)] | None


class HandoffBatchObserveEnvelope(HandoffBatchBinding):
    inbox_path: HostLocalPath
    rows: Annotated[tuple[HandoffBatchRow, ...], Field(min_length=1, max_length=10_000)]
    complete: bool


class HandoffBatchPreviewRequest(HandoffBatchBinding):
    """空选择表示观察本任务 incoming 根；目录句柄只允许单层枚举。"""

    selection_handles: Annotated[tuple[HostOpaqueId, ...], Field(max_length=256)]


class HandoffBatchCandidate(HostBridgeModel):
    candidate_handle: HostOpaqueId
    name: FileName
    size: Annotated[int, Field(gt=0)]
    action: Literal["copy", "move"]


class HandoffBatchMatch(HostBridgeModel):
    port_id: HostIdentifier
    candidate_handle: HostOpaqueId | None
    state: Literal["matched", "missing", "ambiguous"]


class HandoffBatchPreviewEnvelope(HandoffBatchObserveEnvelope):
    batch_id: HostOpaqueId
    candidates: Annotated[tuple[HandoffBatchCandidate, ...], Field(max_length=256)]
    matches: Annotated[tuple[HandoffBatchMatch, ...], Field(min_length=1, max_length=10_000)]
    expires_in_seconds: Literal[300] = IMPORT_TTL_SECONDS


class HandoffBatchSelection(HostBridgeModel):
    port_id: HostIdentifier
    candidate_handle: HostOpaqueId
    overwrite: bool


class HandoffBatchConfirmRequest(_BatchModel):
    contract_version: Literal["0.3.0"]
    batch_id: HostOpaqueId
    items: Annotated[tuple[HandoffBatchSelection, ...], Field(min_length=1, max_length=256)]


class HandoffBatchItemResult(HostBridgeModel):
    port_id: HostIdentifier
    status: Literal["collected", "failed"]
    message: str | None


class HandoffBatchConfirmEnvelope(HandoffBatchObserveEnvelope):
    batch_id: HostOpaqueId
    results: tuple[HandoffBatchItemResult, ...]


class HandoffBatchCheckRequest(HandoffBatchBinding):
    overwrite_ports: Annotated[tuple[HostIdentifier, ...], Field(max_length=10_000)]


class HandoffBatchValidationError(HostBridgeModel):
    code: str
    message: str


class HandoffBatchCheckEnvelope(HandoffBatchObserveEnvelope):
    published: bool
    validation_error: HandoffBatchValidationError | None
    readiness: ExternalHandoffReadiness | None = None


@dataclass(frozen=True)
class _Candidate:
    path: Path
    identity: _FileIdentity
    action: Literal["copy", "move"]


@dataclass(frozen=True)
class _Ticket:
    binding: HandoffBatchBinding
    session: HostBridgeSession
    candidates: dict[str, _Candidate]
    incoming: dict[str, tuple[Path, _FileIdentity | None]]
    targets: dict[str, tuple[Path, _FileIdentity | None]]
    expires: float


def _failure(code: str, message: str, status: int = 409) -> HostBridgeFailure:
    return HostBridgeFailure(f"E_HANDOFF_BATCH_{code}", message, http_status=status)


def _parse[ModelT: HostBridgeModel](model: type[ModelT], payload: object) -> ModelT:
    try:
        return model.model_validate(payload, strict=True)
    except ValidationError as error:
        raise _failure("REQUEST", "批量交接字段或类型无效", 422) from error


def _binding(value: HandoffBatchBinding) -> HandoffBatchBinding:
    return HandoffBatchBinding.model_validate(
        {key: getattr(value, key) for key in HandoffBatchBinding.model_fields}
    )


def _inbox(authority: ImportAuthority) -> Path:
    work = _safe_path(Path(authority.node_run.work_dir))
    directory = _safe_path(work / "incoming")
    if not directory.is_dir() or directory.parent != work:
        raise _failure("PATH", "当前任务没有有效的收件目录", 422)
    return directory


def _incoming(authority: ImportAuthority) -> Path:
    target = _target_path(authority)
    directory = _incoming_path(authority)
    return _safe_path(directory / target.name, allow_missing_leaf=True)


def _observe(
    binding: HandoffBatchBinding, authorities: tuple[ImportAuthority, ...]
) -> HandoffBatchObserveEnvelope:
    rows = []
    for authority in authorities:
        target = _target_path(authority)
        incoming = _incoming(authority)
        received = _identity(incoming, allow_missing=True, target=True)
        published = _identity(target, allow_missing=True, target=True)
        rows.append(
            HandoffBatchRow(
                port_id=authority.target.port_id,
                target_name=target.name,
                target_path=str(target),
                incoming_path=str(incoming),
                collected=received is not None and received.size > 0,
                target_exists=published is not None and published.size > 0,
                size=received.size if received else published.size if published else None,
            )
        )
    return HandoffBatchObserveEnvelope(
        **binding.model_dump(),
        inbox_path=str(_inbox(authorities[0])),
        rows=tuple(rows),
        complete=all(row.collected or row.target_exists for row in rows),
    )


def _same_data(left: _FileIdentity | None, right: _FileIdentity | None) -> bool:
    """同盘移动可改变 ctime；仍必须固定 inode、大小与写入时间。"""
    if left is None or right is None:
        return left is right
    return (left.device, left.inode, left.size, left.mtime_ns) == (
        right.device,
        right.inode,
        right.size,
        right.mtime_ns,
    )


def _move_no_replace(source: Path, target: Path, identity: _FileIdentity) -> None:
    """Windows 用同盘不覆盖 rename，避免让 NAS 收件额外依赖硬链接。

    Windows os.rename 在目标存在时必定失败；不使用会跨盘复制的 shutil.move。
    其他平台继续复用已有 link/unlink 的 no-replace 机制。文件变化时保留实际文件，
    不把网络结果不明解释为成功，也不自动重试。
    """
    if _identity(source) != identity:
        raise _failure("CHANGED", "移动前文件已变化")
    _safe_path(target, allow_missing_leaf=True)
    if source.stat().st_dev != target.parent.stat().st_dev:
        raise _failure("PATH", "收纳和发布必须在同一文件系统，不能隐式复制", 422)
    if os.name != "nt":
        HandoffInboxManager._move_no_replace(source, target, identity)
        return
    os.rename(source, target)
    if not _same_data(_identity(target), identity):
        if not source.exists():
            os.rename(target, source)
        raise _failure("CHANGED", "移动时来件发生变化，实际文件已保留")


def _directory_candidates(directory: Path, suffixes: set[str]) -> tuple[Path, ...]:
    """只发现本次选定目录的普通非空候选；子目录与未写完的空文件留在原处。"""
    found = []
    try:
        with os.scandir(directory) as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_DIRECTORY_ENTRIES:
                    raise _failure("LIMIT", "目录条目过多，请选择本次处理的文件", 422)
                path = directory / entry.name
                if path.suffix.casefold() not in suffixes or not entry.is_file(
                    follow_symlinks=False
                ):
                    continue
                if entry.stat(follow_symlinks=False).st_size > 0:
                    found.append(path)
    except OSError as error:
        raise _failure("IO", "无法读取所选收件目录，请检查磁盘或文件占用", 422) from error
    _safe_path(directory)
    return tuple(found)


class HandoffBatchManager:
    """串行收件与整节点预检；票据只属于宿主会话，不是运行或恢复权威。"""

    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._tickets: OrderedDict[str, _Ticket] = OrderedDict()
        self._lock = threading.Lock()

    def observe(
        self, payload: object, *, session: HostBridgeSession, application: ProjectServiceApplication
    ) -> HandoffBatchObserveEnvelope:
        binding = _parse(HandoffBatchBinding, payload)
        with application.handoff_batch_authority(binding) as authorities:
            return _observe(binding, authorities)

    def preview(
        self, payload: object, *, session: HostBridgeSession, application: ProjectServiceApplication
    ) -> HandoffBatchPreviewEnvelope:
        request = _parse(HandoffBatchPreviewRequest, payload)
        binding = _binding(request)
        with application.handoff_batch_authority(binding) as authorities:
            observed = _observe(binding, authorities)
            paths = tuple(
                session.resolve_path_reference(
                    PickerSelectionReference(kind="picker_selection", selection_handle=handle)
                )
                for handle in request.selection_handles
            )
            paths = paths or (_inbox(authorities[0]),)
            candidates: dict[str, _Candidate] = {}
            identities: set[tuple[int, int]] = set()
            for selected in paths:
                selected = _safe_path(selected)
                if selected.is_dir():
                    sources = _directory_candidates(
                        selected, {Path(row.target_name).suffix.casefold() for row in observed.rows}
                    )
                else:
                    sources = (selected,)
                for source in sources:
                    identity = _identity(source)
                    assert identity is not None
                    if len(source.name) > 255:
                        raise _failure("PATH", "候选文件名过长", 422)
                    file_key = (identity.device, identity.inode)
                    if file_key in identities:
                        raise _failure("DUPLICATE", "同一个文件被重复选择，不能认领多个输出", 422)
                    identities.add(file_key)
                    if len(candidates) >= MAX_BATCH_FILES:
                        raise _failure("LIMIT", "一次最多选择 256 个候选", 422)
                    action: Literal["copy", "move"] = (
                        "move" if source.parent == _inbox(authorities[0]) else "copy"
                    )
                    if action == "move" and source.stat().st_nlink != 1:
                        raise _failure("PATH", "原位收纳候选不得与其他文件共享硬链接", 422)
                    candidates[secrets.token_urlsafe(24)] = _Candidate(source, identity, action)
            # 大小、时间和排序只供展示，自动建议只接受唯一规范名。
            target_names = Counter(row.target_name.casefold() for row in observed.rows)
            matches = []
            for row in observed.rows:
                exact = [
                    key
                    for key, candidate in candidates.items()
                    if candidate.path.name.casefold() == row.target_name.casefold()
                ]
                ambiguous = len(exact) > 1 or target_names[row.target_name.casefold()] > 1
                matches.append(
                    HandoffBatchMatch(
                        port_id=row.port_id,
                        candidate_handle=exact[0] if len(exact) == 1 and not ambiguous else None,
                        state="ambiguous" if ambiguous else "matched" if exact else "missing",
                    )
                )
            identifier = secrets.token_urlsafe(24)
            ticket = _Ticket(
                binding,
                session,
                candidates,
                {
                    a.target.port_id: (
                        _incoming(a),
                        _identity(_incoming(a), allow_missing=True, target=True),
                    )
                    for a in authorities
                },
                {
                    a.target.port_id: (
                        _target_path(a),
                        _identity(_target_path(a), allow_missing=True, target=True),
                    )
                    for a in authorities
                },
                self._clock() + IMPORT_TTL_SECONDS,
            )
            with self._lock:
                self._tickets = OrderedDict(
                    (key, item)
                    for key, item in self._tickets.items()
                    if item.expires > self._clock()
                )
                self._tickets[identifier] = ticket
                while len(self._tickets) > 32:
                    self._tickets.popitem(last=False)
            return HandoffBatchPreviewEnvelope(
                **observed.model_dump(),
                batch_id=identifier,
                candidates=tuple(
                    HandoffBatchCandidate(
                        candidate_handle=key,
                        name=value.path.name,
                        size=value.identity.size,
                        action=value.action,
                    )
                    for key, value in candidates.items()
                ),
                matches=tuple(matches),
            )

    def confirm(
        self, payload: object, *, session: HostBridgeSession, application: ProjectServiceApplication
    ) -> HandoffBatchConfirmEnvelope:
        """一次消费整批确认并逐件收件；长复制不会让同批后续行的意图过期。"""
        request = _parse(HandoffBatchConfirmRequest, payload)
        with self._lock:
            ticket = self._tickets.pop(request.batch_id, None)
        if ticket is None or ticket.session is not session or ticket.expires <= self._clock():
            raise _failure("EXPIRED", "批量预览已过期或已使用，请重新选择")
        ports = [item.port_id for item in request.items]
        handles = [item.candidate_handle for item in request.items]
        if len(set(ports)) != len(ports) or len(set(handles)) != len(handles):
            raise _failure("DUPLICATE", "同一个输出或候选不能重复分配", 422)
        if any(port not in ticket.incoming for port in ports) or any(
            handle not in ticket.candidates for handle in handles
        ):
            raise _failure("MAPPING", "映射引用了本次预览以外的输出或候选", 422)
        with application.handoff_batch_authority(ticket.binding, importing=True) as authorities:
            by_port = {a.target.port_id: a for a in authorities}
            # 平铺收件区可能同时充当来源和目的地。必须在任何写入前拒绝交叉映射，
            # 否则先处理 A→B 会覆盖尚待处理的 B→A；逐件重验不能挽回被覆盖的原件。
            selected_sources = {
                item.candidate_handle: ticket.candidates[item.candidate_handle]
                for item in request.items
            }
            for item in request.items:
                destination, received = ticket.incoming[item.port_id]
                if any(
                    handle != item.candidate_handle
                    and (
                        other.path == destination
                        or (received is not None and _same_data(other.identity, received))
                    )
                    for handle, other in selected_sources.items()
                ):
                    raise _failure(
                        "MAPPING", "收件目标同时是本批其他来件的来源，请先在独立目录整理文件名", 422
                    )
            for item in request.items:
                self._assert_item(ticket, item, by_port[item.port_id])
                incoming, received = ticket.incoming[item.port_id]
                if (
                    received is not None
                    and ticket.candidates[item.candidate_handle].path != incoming
                    and not item.overwrite
                ):
                    raise _failure("OVERWRITE_REQUIRED", "替换已有收件必须逐项明确允许")
            results = []
            for item in request.items:
                try:
                    self._copy(ticket, item, by_port[item.port_id])
                    results.append(
                        HandoffBatchItemResult(
                            port_id=item.port_id, status="collected", message=None
                        )
                    )
                except (OSError, HostBridgeFailure) as error:
                    results.append(
                        HandoffBatchItemResult(
                            port_id=item.port_id, status="failed", message=str(error)
                        )
                    )
            observed = _observe(ticket.binding, authorities)
        return HandoffBatchConfirmEnvelope(
            **observed.model_dump(), batch_id=request.batch_id, results=tuple(results)
        )

    @staticmethod
    def _assert_item(
        ticket: _Ticket, item: HandoffBatchSelection, authority: ImportAuthority
    ) -> None:
        candidate = ticket.candidates[item.candidate_handle]
        incoming, expected = ticket.incoming[item.port_id]
        target, published = ticket.targets[item.port_id]
        if (
            _incoming(authority) != incoming
            or _target_path(authority) != target
            or _identity(candidate.path) != candidate.identity
            or _identity(incoming, allow_missing=True, target=True) != expected
            or _identity(target, allow_missing=True, target=True) != published
        ):
            raise _failure("CHANGED", "来件、收件位置或正式目标已变化，请重新预览")
        if candidate.path != incoming and _same_data(candidate.identity, expected):
            raise _failure("SAME_FILE", "来件已在本输出收件位置，无需重复导入")

    def _copy(
        self, ticket: _Ticket, item: HandoffBatchSelection, authority: ImportAuthority
    ) -> None:
        self._assert_item(ticket, item, authority)
        source = ticket.candidates[item.candidate_handle]
        destination, old = ticket.incoming[item.port_id]
        if source.path == destination:
            # 平铺目录中已按规范名交回的同一文件只确认身份，不自我覆盖或额外复制。
            return
        if source.action == "move":
            # 只有当前 attempt 统一收件根的文件可原位收纳；外部任意目录仍复制保留原件。
            if (
                source.path.parent != _inbox(authority)
                or source.identity.device != destination.parent.stat().st_dev
                or source.path.stat().st_nlink != 1
            ):
                raise _failure("PATH", "原位收纳必须来自本任务同盘收件目录", 422)
            if old is None:
                _move_no_replace(source.path, destination, source.identity)
            else:
                os.replace(source.path, destination)
            return
        work = _safe_path(Path(authority.node_run.work_dir))
        staging = Path(tempfile.mkdtemp(prefix=".handoff-batch-copy-", dir=work))
        staging_identity = (staging.stat().st_dev, staging.stat().st_ino)
        temporary = staging / destination.name
        temporary_identity: tuple[int, int] | None = None
        try:
            with source.path.open("rb") as reader, temporary.open("xb") as writer:
                temporary_stat = os.fstat(writer.fileno())
                temporary_identity = (temporary_stat.st_dev, temporary_stat.st_ino)
                opened = os.fstat(reader.fileno())
                if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
                    source.identity.device,
                    source.identity.inode,
                    source.identity.size,
                    source.identity.mtime_ns,
                ):
                    raise _failure("CHANGED", "所选文件已变化")
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
                writer.flush()
                os.fsync(writer.fileno())
            self._assert_item(ticket, item, authority)
            if temporary.stat().st_size != source.identity.size:
                raise _failure("CHANGED", "复制结果大小不一致，未收件")
            if old is None:
                copied = _identity(temporary)
                assert copied is not None
                _move_no_replace(temporary, destination, copied)
            else:
                os.replace(temporary, destination)
        finally:
            # 仅清理本操作自己创建且仍在原 attempt 的文件；不递归删除用户目录。
            with suppress(OSError, HostBridgeFailure):
                if (
                    _safe_path(staging) == staging
                    and staging.parent == work
                    and (staging.stat().st_dev, staging.stat().st_ino) == staging_identity
                ):
                    if temporary.exists():
                        current = temporary.lstat()
                        if (current.st_dev, current.st_ino) == temporary_identity:
                            temporary.unlink()
                    staging.rmdir()

    def check(
        self, payload: object, *, session: HostBridgeSession, application: ProjectServiceApplication
    ) -> HandoffBatchCheckEnvelope:
        request = _parse(HandoffBatchCheckRequest, payload)
        binding = _binding(request)
        with application.handoff_batch_authority(binding, importing=True) as authorities:
            observed = _observe(binding, authorities)
            allowed = set(request.overwrite_ports)
            if len(allowed) != len(request.overwrite_ports) or not allowed <= {
                row.port_id for row in observed.rows
            }:
                raise _failure("MAPPING", "覆盖许可必须引用唯一的当前输出", 422)
            if not observed.complete:
                return HandoffBatchCheckEnvelope(
                    **observed.model_dump(),
                    published=False,
                    validation_error=HandoffBatchValidationError(
                        code="E_HANDOFF_BATCH_MISSING",
                        message="来件尚未齐全；已收文件保留，任务仍等待交付",
                    ),
                )
            if any(
                row.collected and row.target_exists and row.port_id not in allowed
                for row in observed.rows
            ):
                raise _failure("OVERWRITE_REQUIRED", "正式产物已存在，必须逐项明确允许替换")
            try:
                published = self._validate_and_publish(binding, authorities)
            except RunnerError as error:
                return HandoffBatchCheckEnvelope(
                    **_observe(binding, authorities).model_dump(),
                    published=False,
                    validation_error=HandoffBatchValidationError(
                        code=error.code, message=str(error)
                    ),
                )
            except OSError as error:
                raise _failure(
                    "IO", f"整批发布未完成，来件与可恢复文件已保留：{error}", 422
                ) from error
            # 校验使用的是刚刚发布的同 inode 文件；这里只刷新正式路径观察，避免重复昂贵 validator。
            readiness = application.inspect_external_readiness(
                run_id=binding.run_id, node_run_id=binding.node_run_id, probe=False
            )
            if any(
                not _same_data(_identity(path), expected) for path, expected in published.values()
            ) or any(target.state != "present" for target in readiness.targets):
                raise _failure("CHANGED", "发布后文件已变化，请重新检查；没有提交")
            readiness = readiness.model_copy(
                update={
                    "probe_requested": True,
                    "ready_for_submit": True,
                    "targets": tuple(
                        target.model_copy(update={"state": "probe_passed", "message": None})
                        for target in readiness.targets
                    ),
                }
            )
            return HandoffBatchCheckEnvelope(
                **_observe(binding, authorities).model_dump(),
                published=True,
                validation_error=None,
                readiness=readiness,
            )

    @staticmethod
    def _validate_and_publish(
        binding: HandoffBatchBinding, authorities: tuple[ImportAuthority, ...]
    ) -> dict[str, tuple[Path, _FileIdentity]]:
        work = _safe_path(Path(authorities[0].node_run.work_dir))
        staging = Path(tempfile.mkdtemp(prefix=".handoff-batch-check-", dir=work))
        staging_identity = (staging.stat().st_dev, staging.stat().st_ino)
        candidates: dict[str, Path] = {}
        sources: dict[str, tuple[Path, _FileIdentity]] = {}
        targets: dict[str, tuple[Path, _FileIdentity | None]] = {}
        backups: dict[str, Path] = {}
        moved: list[str] = []
        success = False
        try:
            for authority in authorities:
                port = authority.target.port_id
                target = _target_path(authority)
                incoming = _incoming(authority)
                source = incoming if incoming.exists() else target
                original = _identity(source)
                assert original is not None
                targets[port] = (target, _identity(target, allow_missing=True, target=True))
                sources[port] = (source, original)
                if source != target:
                    # 收件已经按规范名位于本 attempt；无需再建 hardlink 别名或复制媒体。
                    candidates[port] = source
            runtime = authorities[0].runtime
            if candidates:
                runtime.inspect_external_import_candidates(
                    binding.run_id,
                    binding.node_run_id,
                    handoff_id=binding.handoff_id,
                    candidates=candidates,
                )
            else:
                runtime.inspect_external_outputs(
                    binding.run_id, binding.node_run_id, handoff_id=binding.handoff_id
                )
            runtime.inspect_external_handoff(
                binding.run_id, binding.node_run_id, handoff_id=binding.handoff_id
            )
            for port, (source, expected) in sources.items():
                target, old = targets[port]
                if _identity(source) != expected or _identity(target, allow_missing=True) != old:
                    raise _failure("CHANGED", "完整检查期间文件发生变化，没有发布产物")
            # 文件系统无跨文件事务；保留旧目标到同一暂存目录，失败逐项撤回，不回写 Runtime。
            for port, (source, expected) in sources.items():
                target, old = targets[port]
                if source == target:
                    continue
                if not _same_data(_identity(source), expected) or not _same_data(
                    _identity(target, allow_missing=True), old
                ):
                    raise _failure("CHANGED", "发布前文件发生变化")
                if old is not None:
                    backup = staging / f".{secrets.token_urlsafe(16)}.previous"
                    os.rename(target, backup)
                    backups[port] = backup
                    if not _same_data(_identity(backup), old):
                        raise _failure("CHANGED", "发布时旧目标已变化，保留并恢复原产物")
                moved.append(port)
                _move_no_replace(source, target, expected)
            success = True
            return {port: (targets[port][0], expected) for port, (_, expected) in sources.items()}
        except (OSError, HostBridgeFailure, RunnerError):
            recovery_failed = False
            for port in reversed(moved):
                source, expected = sources[port]
                target = targets[port][0]
                try:
                    if not source.exists() and _same_data(
                        _identity(target, allow_missing=True), expected
                    ):
                        os.rename(target, source)
                    elif (
                        source.exists()
                        and _same_data(_identity(source), expected)
                        and _same_data(_identity(target, allow_missing=True), expected)
                    ):
                        target.unlink()
                    elif (
                        source.exists()
                        and _same_data(_identity(source), expected)
                        and not target.exists()
                    ):
                        pass  # no-replace 发布在创建目标前失败，原来件已经保留。
                    else:
                        recovery_failed = True
                except (OSError, HostBridgeFailure):
                    recovery_failed = True
            for port, backup in backups.items():
                target = targets[port][0]
                try:
                    if backup.exists() and not target.exists():
                        os.rename(backup, target)
                    elif backup.exists():
                        recovery_failed = True
                except OSError:
                    recovery_failed = True
            if recovery_failed:
                raise _failure(
                    "PARTIAL",
                    f"部分文件需要确认，原产物和来件保留在任务暂存目录：{staging}；没有提交",
                ) from None
            raise
        finally:
            # 不确定状态保留旧目标；只在整批成功时清理本操作保存的旧产物和空暂存目录。
            with suppress(OSError, HostBridgeFailure):
                if (
                    _safe_path(staging) == staging
                    and (staging.stat().st_dev, staging.stat().st_ino) == staging_identity
                ):
                    for port, old_backup in backups.items():
                        with suppress(OSError, HostBridgeFailure):
                            if (
                                success
                                and old_backup.exists()
                                and _same_data(_identity(old_backup), targets[port][1])
                            ):
                                old_backup.unlink()
                    with suppress(OSError):
                        staging.rmdir()
