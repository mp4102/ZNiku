"""把当前外部任务收件箱中的明确候选收纳为规范产物，不自动提交或登记 Artifact。

观察只扫描精确绑定的单层目录，浏览器仅持有短时 opaque 句柄；收纳须再次预览并明确确认。
候选通过同一 Runtime validator 后才在同盘移动。临时硬链接只提供 validator 所需的正式
basename，不复制大文件、不作为归档副本；异常默认保留来件及旧目标，绝不递归清理用户目录。
"""

from __future__ import annotations

import os
import secrets
import stat
import tempfile
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Annotated, Final, Literal

from pydantic import Field, StringConstraints, ValidationError

from zniku.runtime import RunnerError
from zniku.runtime.paths import incoming_directory_name

from .handoff_import import (
    IMPORT_TTL_SECONDS,
    HandoffImportBinding,
    ImportAuthority,
    _FileIdentity,
    _identity,
    _safe_path,
    _target_path,
)
from .host_bridge import (
    HostBridgeFailure,
    HostBridgeModel,
    HostBridgeSession,
    HostLocalPath,
    HostOpaqueId,
)

if TYPE_CHECKING:
    from .service import ProjectServiceApplication

MAX_INBOX_CANDIDATES: Final = 64
MAX_INBOX_ENTRIES: Final = 256
MAX_INBOX_HANDLES: Final = 256
MAX_INBOX_TICKETS: Final = 32
FileName = Annotated[str, StringConstraints(min_length=1, max_length=255)]


class HandoffInboxObserveRequest(HandoffImportBinding):
    """只引用精确交接身份，不允许浏览器提交收件箱路径。"""


class HandoffInboxCandidate(HostBridgeModel):
    """一次目录观察的普通文件摘要；mtime 仅为变化提示，不是内容身份。"""

    candidate_handle: HostOpaqueId
    name: FileName
    size: Annotated[int, Field(gt=0)]
    mtime_ns: Annotated[int, Field(ge=0)]


class HandoffInboxObserveEnvelope(HandoffImportBinding):
    """零、一或多候选都只是投影；不猜选文件，不执行媒体检查或 Submit。"""

    inbox_path: HostLocalPath
    allowed_suffix: Annotated[str, StringConstraints(min_length=1, max_length=32)]
    candidates: Annotated[tuple[HandoffInboxCandidate, ...], Field(max_length=64)]
    rejected_count: Annotated[int, Field(ge=0, le=256)]
    expires_in_seconds: Literal[300] = IMPORT_TTL_SECONDS


class HandoffInboxPreviewRequest(HandoffImportBinding):
    """用户必须明确选择观察结果中的一个候选句柄。"""

    candidate_handle: HostOpaqueId


class HandoffInboxPreviewEnvelope(HandoffImportBinding):
    """说明受控移动的目标及覆盖行为，预览本身不创建文件。"""

    inbox_id: HostOpaqueId
    source_name: FileName
    source_size: Annotated[int, Field(gt=0)]
    target_path: HostLocalPath
    replace_existing: bool
    action: Literal["move"] = "move"
    expires_in_seconds: Literal[300] = IMPORT_TTL_SECONDS


class HandoffInboxConfirmRequest(HostBridgeModel):
    """一次性确认收纳；覆盖必须为显式 true，无法传入任意文件路径。"""

    contract_version: Literal["0.3.0"]
    inbox_id: HostOpaqueId
    overwrite: bool


class HandoffInboxConfirmEnvelope(HandoffImportBinding):
    """仅说明候选收纳完成；用户仍须通过原有检查与提交推进任务。"""

    inbox_id: HostOpaqueId
    source_name: FileName
    source_size: Annotated[int, Field(gt=0)]
    target_path: HostLocalPath
    status: Literal["collected"] = "collected"


@dataclass(frozen=True, slots=True)
class _Candidate:
    binding: HandoffImportBinding
    session: HostBridgeSession
    inbox: Path
    path: Path
    identity: _FileIdentity
    expires: float


@dataclass(frozen=True, slots=True)
class _InboxTicket:
    candidate: _Candidate
    target: Path
    target_identity: _FileIdentity | None
    expires: float


def _failure(code: str, message: str, status: int = 409) -> HostBridgeFailure:
    return HostBridgeFailure(f"E_HANDOFF_INBOX_{code}", message, http_status=status)


def _inbox_path(authority: ImportAuthority, binding: HandoffImportBinding) -> Path:
    _target_path(authority)
    work = _safe_path(Path(authority.node_run.work_dir))
    inbox = _safe_path(work / "incoming" / incoming_directory_name(binding.port_id))
    if not inbox.is_dir() or inbox.parent.parent != work:
        raise _failure("PATH", "当前任务收件目录无效，不能改用其他任务的目录", 422)
    return inbox


def _source_identity(path: Path) -> _FileIdentity:
    value = _identity(path)
    assert value is not None
    if path.stat().st_nlink != 1:
        raise _failure("PATH", "收件候选不得与其他文件共享硬链接", 422)
    return value


def _retains_identity(path: Path, identity: tuple[int, int] | None) -> bool:
    """仅在仍可证明其他正常路径保有同一文件时，才允许清理临时别名。"""

    try:
        _safe_path(path, allow_missing_leaf=True)
        value = path.lstat()
        return stat.S_ISREG(value.st_mode) and (value.st_dev, value.st_ino) == identity
    except (OSError, HostBridgeFailure):
        return False


class HandoffInboxManager:
    """提供有界、短时、精确绑定且不推进 Runtime 的收件观察与收纳。"""

    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._candidates: OrderedDict[str, _Candidate] = OrderedDict()
        self._tickets: OrderedDict[str, _InboxTicket] = OrderedDict()
        self._lock = threading.Lock()

    def observe(
        self, payload: object, *, session: HostBridgeSession, application: ProjectServiceApplication
    ) -> HandoffInboxObserveEnvelope:
        """只枚举单层、普通、非空且符合声明后缀的来件；不尝试完成仍在写入的文件。"""

        try:
            request = HandoffInboxObserveRequest.model_validate(payload, strict=True)
        except ValidationError as error:
            raise _failure("REQUEST", "收件观察字段或类型无效", 422) from error
        with application.handoff_import_authority(request) as authority:
            inbox = _inbox_path(authority, request)
            suffix = Path(authority.target.path).suffix.lower()
            if not suffix or len(suffix) > 32:
                raise _failure("SUFFIX", "当前输出未声明可观察的文件后缀，请使用原有导入", 422)
            observed: list[tuple[str, _Candidate]] = []
            rejected = 0
            try:
                with os.scandir(inbox) as entries:
                    for index, entry in enumerate(entries):
                        if index >= MAX_INBOX_ENTRIES:
                            raise _failure("LIMIT", "收件目录文件过多，请保留本次任务的来件", 422)
                        path = inbox / entry.name
                        if path.suffix.lower() != suffix or len(path.name) > 255:
                            rejected += 1
                            continue
                        try:
                            identity = _source_identity(path)
                        except HostBridgeFailure:
                            rejected += 1
                            continue
                        if len(observed) >= MAX_INBOX_CANDIDATES:
                            raise _failure("LIMIT", "可用候选超过 64 个，请整理后重新检查", 422)
                        observed.append(
                            (
                                secrets.token_urlsafe(24),
                                _Candidate(
                                    request,
                                    session,
                                    inbox,
                                    path,
                                    identity,
                                    self._clock() + IMPORT_TTL_SECONDS,
                                ),
                            )
                        )
            except OSError as error:
                raise _failure("IO", "无法读取当前任务收件目录，请检查磁盘和权限", 422) from error
            # 读取后再检查目录组件；目录被替换时不签发部分列表。
            if _inbox_path(authority, request) != inbox:
                raise _failure("CHANGED", "观察期间收件目录已变化，请刷新")
            observed.sort(key=lambda item: (item[1].path.name.casefold(), item[1].path.name))
            with self._lock:
                self._candidates = OrderedDict(
                    (key, value)
                    for key, value in self._candidates.items()
                    if value.expires > self._clock()
                    and not (value.session is session and value.binding == request)
                )
                self._candidates.update(observed)
                while len(self._candidates) > MAX_INBOX_HANDLES:
                    self._candidates.popitem(last=False)
        return HandoffInboxObserveEnvelope(
            **request.model_dump(),
            inbox_path=str(inbox),
            allowed_suffix=suffix,
            candidates=tuple(
                HandoffInboxCandidate(
                    candidate_handle=key,
                    name=value.path.name,
                    size=value.identity.size,
                    mtime_ns=value.identity.mtime_ns,
                )
                for key, value in observed
            ),
            rejected_count=rejected,
        )

    def preview(
        self, payload: object, *, session: HostBridgeSession, application: ProjectServiceApplication
    ) -> HandoffInboxPreviewEnvelope:
        """消费候选句柄，核对源和目标变化后签发一次性移动确认；此步不写媒体。"""

        try:
            request = HandoffInboxPreviewRequest.model_validate(payload, strict=True)
        except ValidationError as error:
            raise _failure("REQUEST", "收件预览字段或类型无效", 422) from error
        binding = HandoffImportBinding.model_validate(
            request.model_dump(exclude={"candidate_handle"}), strict=True
        )
        with self._lock:
            candidate = self._candidates.pop(request.candidate_handle, None)
        if (
            candidate is None
            or candidate.session is not session
            or candidate.expires <= self._clock()
            or candidate.binding.model_dump() != binding.model_dump()
        ):
            raise _failure("EXPIRED", "收件候选已过期、已选择或不属于当前任务，请刷新")
        with application.handoff_import_authority(binding) as authority:
            if (
                _inbox_path(authority, binding) != candidate.inbox
                or candidate.path.parent != candidate.inbox
                or _source_identity(candidate.path) != candidate.identity
            ):
                raise _failure("CHANGED", "候选已变化或仍在写入，请完成处理后重新检查")
            target = _target_path(authority)
            target_identity = _identity(target, allow_missing=True, target=True)
            if candidate.identity.device != target.parent.stat().st_dev:
                raise _failure("PATH", "收件与正式产物必须在同一磁盘，不能退回隐式复制", 422)
            identifier = secrets.token_urlsafe(24)
            with self._lock:
                self._tickets = OrderedDict(
                    (key, value)
                    for key, value in self._tickets.items()
                    if value.expires > self._clock()
                )
                self._tickets[identifier] = _InboxTicket(
                    candidate, target, target_identity, self._clock() + IMPORT_TTL_SECONDS
                )
                while len(self._tickets) > MAX_INBOX_TICKETS:
                    self._tickets.popitem(last=False)
        return HandoffInboxPreviewEnvelope(
            **binding.model_dump(),
            inbox_id=identifier,
            source_name=candidate.path.name,
            source_size=candidate.identity.size,
            target_path=str(target),
            replace_existing=target_identity is not None,
        )

    def confirm(
        self, payload: object, *, session: HostBridgeSession, application: ProjectServiceApplication
    ) -> HandoffInboxConfirmEnvelope:
        """消耗确认票据、验证并移动一个来件；失败不改运行记录，成功也不自动 Submit。"""

        try:
            request = HandoffInboxConfirmRequest.model_validate(payload, strict=True)
        except ValidationError as error:
            raise _failure("REQUEST", "收件确认字段或类型无效", 422) from error
        with self._lock:
            ticket = self._tickets.pop(request.inbox_id, None)
        if (
            ticket is None
            or ticket.candidate.session is not session
            or ticket.expires <= self._clock()
        ):
            raise _failure("EXPIRED", "收纳确认已过期或已使用，请重新检查来件")
        if ticket.target_identity is not None and not request.overwrite:
            raise _failure("OVERWRITE_REQUIRED", "任务已有产物，须明确确认替换才可收纳")
        try:
            with application.handoff_import_authority(
                ticket.candidate.binding, importing=True
            ) as authority:
                self._collect(ticket, authority)
        except RunnerError as error:
            raise HostBridgeFailure(error.code, str(error), http_status=422) from error
        except OSError as error:
            raise _failure(
                "IO", "收纳未能完成，请检查来件、正式目标和任务目录中的保留文件；不会自动提交", 422
            ) from error
        return HandoffInboxConfirmEnvelope(
            **ticket.candidate.binding.model_dump(),
            inbox_id=request.inbox_id,
            source_name=ticket.candidate.path.name,
            source_size=ticket.candidate.identity.size,
            target_path=str(ticket.target),
        )

    def _collect(self, ticket: _InboxTicket, authority: ImportAuthority) -> None:
        candidate = ticket.candidate
        self._assert_unchanged(ticket, authority, candidate.identity)
        work = _safe_path(Path(authority.node_run.work_dir))
        staging = Path(tempfile.mkdtemp(prefix=".handoff-inbox-", dir=work))
        staging_identity = (staging.stat().st_dev, staging.stat().st_ino)
        alias = staging / ticket.target.name
        alias_identity: tuple[int, int] | None = None
        try:
            # 临时同 inode 别名只满足既有 Runtime 的正式 basename 检查，不形成第二份媒体。
            os.link(candidate.path, alias)
            linked_alias = alias.lstat()
            alias_identity = (linked_alias.st_dev, linked_alias.st_ino)
            linked_identity = _identity(candidate.path)
            assert linked_identity is not None
            if (
                linked_identity.device,
                linked_identity.inode,
                linked_identity.size,
                linked_identity.mtime_ns,
            ) != (
                candidate.identity.device,
                candidate.identity.inode,
                candidate.identity.size,
                candidate.identity.mtime_ns,
            ) or _identity(alias) != linked_identity:
                raise _failure("CHANGED", "准备检查时来件已变化，请重新检查")
            self._assert_unchanged(ticket, authority, linked_identity)
            binding = candidate.binding
            authority.runtime.inspect_external_import_candidate(
                binding.run_id,
                binding.node_run_id,
                handoff_id=binding.handoff_id,
                port_id=binding.port_id,
                candidate=alias,
            )
            self._assert_unchanged(ticket, authority, linked_identity)
            if _identity(alias) != linked_identity:
                raise _failure("CHANGED", "检查期间候选已变化，未收纳或覆盖产物")
            authority.runtime.inspect_external_handoff(
                binding.run_id, binding.node_run_id, handoff_id=binding.handoff_id
            )
            self._assert_unchanged(ticket, authority, linked_identity)
            if ticket.target_identity is None:
                self._move_no_replace(candidate.path, ticket.target, linked_identity)
            else:
                # 已明确授权且最后观察仍一致；同盘原子 replace 的失败保留源和旧目标。
                os.replace(candidate.path, ticket.target)
        finally:
            # 只删除此方法创建的临时别名与空目录；失败/崩溃不递归清理收件目录。
            try:
                if (
                    _safe_path(work) == work
                    and _safe_path(staging) == staging
                    and (staging.stat().st_dev, staging.stat().st_ino) == staging_identity
                ):
                    staging.relative_to(work)
                    if alias.exists():
                        value = alias.lstat()
                        if (
                            stat.S_ISREG(value.st_mode)
                            and (value.st_dev, value.st_ino) == alias_identity
                            and any(
                                _retains_identity(path, alias_identity)
                                for path in (candidate.path, ticket.target)
                            )
                        ):
                            alias.unlink()
                    staging.rmdir()
            except (OSError, ValueError, HostBridgeFailure):
                pass

    @staticmethod
    def _move_no_replace(source: Path, target: Path, identity: _FileIdentity) -> None:
        """原子创建不覆盖的目标；删除来件名失败时只撤销仍指向同一 inode 的新目标。"""

        os.link(source, target)
        linked = target.stat()
        try:
            current_source = source.stat()
            # 发布前后的源路径都必须仍指向已检查的 inode，避免把刚被换名的其他来件删掉。
            expected = (identity.device, identity.inode, identity.size, identity.mtime_ns)
            if any(
                (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns) != expected
                for value in (linked, current_source)
            ):
                raise _failure("CHANGED", "发布时来件已被替换，未收纳其他文件")
            source.unlink()
        except (OSError, HostBridgeFailure):
            current = target.lstat()
            try:
                remaining = source.lstat()
            except OSError:
                remaining = None
            # unlink 可能已删除原名再报告 I/O 失败；此时目标是唯一可恢复副本，绝不能回滚删除。
            if remaining is None or (remaining.st_dev, remaining.st_ino) != (
                linked.st_dev,
                linked.st_ino,
            ):
                raise _failure(
                    "PARTIAL", f"来件原名称已变化，保留正式目标供检查：{target}；没有自动提交"
                ) from None
            if (current.st_dev, current.st_ino) == (linked.st_dev, linked.st_ino):
                target.unlink()
            raise

    @staticmethod
    def _assert_unchanged(
        ticket: _InboxTicket, authority: ImportAuthority, identity: _FileIdentity
    ) -> None:
        candidate = ticket.candidate
        if (
            _inbox_path(authority, candidate.binding) != candidate.inbox
            or _target_path(authority) != ticket.target
            or _identity(candidate.path) != identity
            or _identity(ticket.target, allow_missing=True, target=True) != ticket.target_identity
        ):
            raise _failure("CHANGED", "来件或目标已变化，请重新检查；没有覆盖已有产物")
