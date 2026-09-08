"""把原生选择的外部产物安全复制到精确人工任务，绝不移动、提交或登记 Artifact。

预览只读绑定选择句柄、工程/交接身份和文件观察；确认使用短时一次性票据。复制只写当前
attempt 的独立 staging，复用 Runtime 媒体及节点 validator，全部通过后才原子替换目标。
源文件、旧目标和会话变化默认失败关闭；指纹只关闭本机常见变化窗口，不是内容摘要 authority。
"""

from __future__ import annotations

import os
import secrets
import shutil
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

from zniku.runtime import ExternalOutputTarget, NodeRun, RunnerError, RuntimeService

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

if TYPE_CHECKING:
    from .service import ProjectServiceApplication

IMPORT_TTL_SECONDS: Final = 300
MAX_PENDING_IMPORTS: Final = 32


class HandoffImportBinding(HostBridgeModel):
    """只携带 Python 可解引用的精确工程、任务及输出身份。"""

    contract_version: Literal["0.3.0"]
    project_session_id: HostRandomId
    run_id: HostRandomId
    node_run_id: HostRandomId
    handoff_id: HostRandomId
    port_id: HostIdentifier
    ordinal: Annotated[int, Field(ge=0)] | None


class HandoffImportPreviewRequest(HandoffImportBinding):
    """浏览器不能提交源或目标路径，只能使用此次原生选择的句柄。"""

    selection_handle: HostOpaqueId


class HandoffImportPreviewEnvelope(HandoffImportPreviewRequest):
    """返回明确的源文件摘要、目标和覆盖提示；预览不会创建目录或文件。"""

    import_id: HostOpaqueId
    source_name: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    source_size: Annotated[int, Field(gt=0)]
    target_path: HostLocalPath
    replace_existing: bool
    expires_in_seconds: Literal[300] = IMPORT_TTL_SECONDS


class HandoffImportConfirmRequest(HostBridgeModel):
    """确认消费一次性预览；已有目标只有显式 overwrite=true 才可替换。"""

    contract_version: Literal["0.3.0"]
    import_id: HostOpaqueId
    overwrite: bool


class HandoffImportConfirmEnvelope(HandoffImportBinding):
    """只证明本次复制已完成；Runtime 仍 waiting，用户须另行检查与提交。"""

    import_id: HostOpaqueId
    source_name: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    source_size: Annotated[int, Field(gt=0)]
    target_path: HostLocalPath
    status: Literal["imported"] = "imported"


@dataclass(frozen=True, slots=True)
class ImportAuthority:
    """仅在宿主内部存在的真实 Runtime 与路径引用，不序列化给浏览器。"""

    runtime: RuntimeService
    node_run: NodeRun
    target: ExternalOutputTarget
    work_root: Path
    project_path: Path


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True, slots=True)
class _Ticket:
    request: HandoffImportPreviewRequest
    session: HostBridgeSession
    source: Path
    source_identity: _FileIdentity
    target: Path
    target_identity: _FileIdentity | None
    expires: float


def _failure(code: str, message: str, status: int = 409) -> HostBridgeFailure:
    return HostBridgeFailure(f"E_HANDOFF_IMPORT_{code}", message, http_status=status)


def _safe_path(path: Path, *, allow_missing_leaf: bool = False) -> Path:
    """拒绝链接/reparse/ADS；只在每个已存在路径组件都验证后使用 canonical 路径。"""

    if not path.is_absolute() or ":" in path.name or "\x00" in str(path):
        raise _failure("PATH", "导入路径必须是普通本机文件路径", 422)
    for component in (*reversed(path.parents), path):
        try:
            information = component.lstat()
        except FileNotFoundError:
            if component == path and allow_missing_leaf:
                continue
            raise _failure("PATH", "导入路径或父目录不存在", 422) from None
        except OSError as error:
            raise _failure("PATH", "无法读取导入路径或父目录", 422) from error
        if stat.S_ISLNK(information.st_mode) or (
            getattr(information, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise _failure("PATH", "导入不接受符号链接、junction 或其他重解析路径", 422)
    try:
        return path.resolve(strict=not allow_missing_leaf)
    except OSError as error:
        raise _failure("PATH", "导入路径已变化或无法解析，请重新选择", 422) from error


def _identity(
    path: Path, *, allow_missing: bool = False, target: bool = False
) -> _FileIdentity | None:
    _safe_path(path, allow_missing_leaf=allow_missing)
    try:
        value = path.stat()
    except FileNotFoundError:
        if allow_missing:
            return None
        raise _failure("CHANGED", "选择的文件已不存在，请重新选择") from None
    except OSError as error:
        raise _failure("IO", "无法读取源文件或任务目标，请检查文件占用和权限", 422) from error
    if not stat.S_ISREG(value.st_mode) or (not target and value.st_size <= 0):
        raise _failure("FILE", "请选择非空的普通文件；目标也必须是普通文件", 422)
    if target and value.st_nlink != 1:
        raise _failure("PATH", "目标与其他文件共享硬链接，禁止覆盖", 422)
    return _FileIdentity(
        value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns
    )


def _target_path(authority: ImportAuthority) -> Path:
    root = _safe_path(authority.work_root)
    work = _safe_path(Path(authority.node_run.work_dir))
    target = Path(authority.target.path)
    try:
        work.relative_to(root)
        target.relative_to(work)
    except ValueError as error:
        raise _failure("PATH", "任务目标不在已绑定 attempt 内", 422) from error
    if work == root or work.name != authority.node_run.node_run_id.replace("-", ""):
        raise _failure("PATH", "任务工作目录身份不匹配", 422)
    target = _safe_path(target, allow_missing_leaf=True)
    try:
        target.relative_to(work)
    except ValueError as error:
        raise _failure("PATH", "解析后的任务目标逃逸 attempt", 422) from error
    if target == authority.project_path.resolve(strict=True):
        raise _failure("PATH", "禁止覆盖工程文件", 422)
    return target


class HandoffImportManager:
    """在一次宿主生命周期内保留有限、短时、不可重放的导入意图。"""

    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._tickets: OrderedDict[str, _Ticket] = OrderedDict()
        self._lock = threading.Lock()

    def preview(
        self, payload: object, *, session: HostBridgeSession, application: ProjectServiceApplication
    ) -> HandoffImportPreviewEnvelope:
        """只读解析来源与目标，创建内存票据；过期或换代文件不会自动重选。"""

        try:
            request = HandoffImportPreviewRequest.model_validate(payload, strict=True)
        except ValidationError as error:
            raise _failure("REQUEST", "导入预览字段或类型无效", 422) from error
        source = session.resolve_path_reference(
            PickerSelectionReference(
                kind="picker_selection", selection_handle=request.selection_handle
            )
        )
        with application.handoff_import_authority(request) as authority:
            target = _target_path(authority)
            source_identity = _identity(source)
            assert source_identity is not None
            target_identity = _identity(target, allow_missing=True, target=True)
            if source == target or (
                target_identity
                and (source_identity.device, source_identity.inode)
                == (target_identity.device, target_identity.inode)
            ):
                raise _failure("SAME_FILE", "选中的文件已是此任务目标，请直接检查，不必导入")
            ticket = _Ticket(
                request,
                session,
                source,
                source_identity,
                target,
                target_identity,
                self._clock() + IMPORT_TTL_SECONDS,
            )
            identifier = secrets.token_urlsafe(24)
            with self._lock:
                self._tickets = OrderedDict(
                    (key, value)
                    for key, value in self._tickets.items()
                    if value.expires > self._clock()
                )
                self._tickets[identifier] = ticket
                while len(self._tickets) > MAX_PENDING_IMPORTS:
                    self._tickets.popitem(last=False)
        return HandoffImportPreviewEnvelope(
            **request.model_dump(),
            import_id=identifier,
            source_name=source.name,
            source_size=source_identity.size,
            target_path=str(target),
            replace_existing=target_identity is not None,
        )

    def confirm(
        self, payload: object, *, session: HostBridgeSession, application: ProjectServiceApplication
    ) -> HandoffImportConfirmEnvelope:
        """消费一次性意图并复制；任何失败均不提交任务，也不会删除原文件或旧目标。"""

        try:
            request = HandoffImportConfirmRequest.model_validate(payload, strict=True)
        except ValidationError as error:
            raise _failure("REQUEST", "导入确认字段或类型无效", 422) from error
        with self._lock:
            ticket = self._tickets.pop(request.import_id, None)
        if ticket is None or ticket.session is not session or ticket.expires <= self._clock():
            raise _failure("EXPIRED", "导入预览已过期或已使用，请重新选择文件")
        if ticket.target_identity is not None and not request.overwrite:
            raise _failure("OVERWRITE_REQUIRED", "任务已有目标文件，必须明确确认替换")
        try:
            with application.handoff_import_authority(ticket.request, importing=True) as authority:
                self._copy_and_publish(ticket, authority)
        except RunnerError as error:
            raise HostBridgeFailure(error.code, str(error), http_status=422) from error
        except OSError as error:
            raise _failure(
                "IO", "导入文件失败，原文件和已有目标仍保留。" + str(error), 422
            ) from error
        return HandoffImportConfirmEnvelope(
            **ticket.request.model_dump(exclude={"selection_handle"}),
            import_id=request.import_id,
            source_name=ticket.source.name,
            source_size=ticket.source_identity.size,
            target_path=str(ticket.target),
        )

    def _copy_and_publish(self, ticket: _Ticket, authority: ImportAuthority) -> None:
        self._assert_unchanged(ticket, authority)
        work = _safe_path(Path(authority.node_run.work_dir))
        staging = Path(tempfile.mkdtemp(prefix=".handoff-import-", dir=work))
        staging_identity = (staging.stat().st_dev, staging.stat().st_ino)
        candidate = staging / ticket.target.name
        try:
            # 不采用 move/copy2，不改源 metadata；固定 basename 供同一 validator 检查。
            with ticket.source.open("rb") as source, candidate.open("xb") as destination:
                source_stat = os.fstat(source.fileno())
                # Windows 的 stat/fstat 对 ctime 可能分别报告 change/birth time；打开句柄只比较
                # 一致的 dev/inode/size/mtime，路径观察之间仍额外比较同一 stat 的 ctime。
                if (
                    source_stat.st_dev,
                    source_stat.st_ino,
                    source_stat.st_size,
                    source_stat.st_mtime_ns,
                ) != (
                    ticket.source_identity.device,
                    ticket.source_identity.inode,
                    ticket.source_identity.size,
                    ticket.source_identity.mtime_ns,
                ):
                    raise _failure("CHANGED", "源文件已被替换或正在写入，请重新选择")
                shutil.copyfileobj(source, destination, length=1024 * 1024)
                destination.flush()
                os.fsync(destination.fileno())
            self._assert_unchanged(ticket, authority)
            candidate_identity = _identity(candidate)
            if candidate_identity is None or candidate_identity.size != ticket.source_identity.size:
                raise _failure("CHANGED", "复制期间源文件发生变化，未发布目标")
            authority.runtime.inspect_external_import_candidate(
                ticket.request.run_id,
                ticket.request.node_run_id,
                handoff_id=ticket.request.handoff_id,
                port_id=ticket.request.port_id,
                candidate=candidate,
            )
            self._assert_unchanged(ticket, authority)
            authority.runtime.inspect_external_handoff(
                ticket.request.run_id,
                ticket.request.node_run_id,
                handoff_id=ticket.request.handoff_id,
            )
            if _identity(candidate) != candidate_identity:
                raise _failure("CHANGED", "验证期间候选文件发生变化，未发布目标")
            if ticket.target_identity is None:
                # hard-link publication 是同盘原子 no-replace；并发出现目标时绝不隐式覆盖。
                os.link(candidate, ticket.target)
                candidate.unlink()
            else:
                os.replace(candidate, ticket.target)
        finally:
            # 不做递归删除；只清理本方法创建、仍在原 attempt 下的唯一候选与空目录。
            try:
                if (
                    _safe_path(work) == work
                    and _safe_path(staging) == staging
                    and (staging.stat().st_dev, staging.stat().st_ino) == staging_identity
                ):
                    staging.relative_to(work)
                    if candidate.exists() and not candidate.is_symlink():
                        candidate.unlink()
                    staging.rmdir()
            except (OSError, ValueError, HostBridgeFailure):
                pass

    @staticmethod
    def _assert_unchanged(ticket: _Ticket, authority: ImportAuthority) -> None:
        if (
            _target_path(authority) != ticket.target
            or _identity(ticket.source) != ticket.source_identity
            or _identity(ticket.target, allow_missing=True, target=True) != ticket.target_identity
        ):
            raise _failure("CHANGED", "源文件或任务目标已变化，请重新选择并确认；没有覆盖已有文件")
