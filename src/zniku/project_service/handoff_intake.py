"""单视频先检查后收纳；候选选择无写入，检查通过才复制或在当前收件区规范命名。

短时内存票据仅约束本次用户操作，不是 Runtime 状态、checkpoint 或媒体证据。后台检查
复用节点媒体规则，复制报告实测字节；失败保留原文件和已有目标。发布与正式 Submit 分离，
即使浏览器中断也不自动推进节点。所有副作用只在已绑定 attempt，绝不接受浏览器 raw path。
"""

from __future__ import annotations

import os
import secrets
import tempfile
import threading
from collections import OrderedDict
from dataclasses import dataclass, replace
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ValidationError

from zniku.avenhance_v27.probe import Av27MediaError, probe_header
from zniku.source_admission import mosaic_restoration as mr

from .handoff_import import (
    HandoffImportBinding,
    ImportAuthority,
    _FileIdentity,
    _identity,
    _incoming_path,
    _safe_path,
)
from .host_bridge import HostBridgeFailure, HostBridgeSession, PickerSelectionReference
from .intake_contracts import (
    HandoffIntakeCandidate,
    HandoffIntakeCheckRequest,
    HandoffIntakeJobEnvelope,
    HandoffIntakeJobRequest,
    HandoffIntakeObserveEnvelope,
    HandoffIntakePublishEnvelope,
    HandoffIntakePublishRequest,
    HandoffIntakeSelectEnvelope,
    HandoffIntakeSelectRequest,
)
from .intake_runtime import intake_context

if TYPE_CHECKING:
    from .service import ProjectServiceApplication


def _parse[T: BaseModel](model: type[T], payload: object) -> T:
    try:
        return model.model_validate(payload, strict=True)
    except ValidationError as error:
        raise HostBridgeFailure(
            "E_HANDOFF_INTAKE_REQUEST", "交回请求字段无效", http_status=422
        ) from error


def _failure(message: str, code: str = "CHANGED") -> HostBridgeFailure:
    return HostBridgeFailure(f"E_HANDOFF_INTAKE_{code}", message, http_status=409)


@dataclass(frozen=True)
class _Choice:
    binding: HandoffImportBinding
    session: HostBridgeSession
    source: Path
    identity: _FileIdentity
    expires: float


@dataclass(frozen=True)
class _Ticket:
    choice: _Choice
    incoming: Path
    output: Path
    incoming_identity: _FileIdentity | None
    output_identity: _FileIdentity | None
    action: Literal["copy", "rename", "none"]


@dataclass(frozen=True)
class _Ready:
    ticket: _Ticket
    identity: _FileIdentity


@dataclass
class _Job:
    choice: _Choice
    envelope: HandoffIntakeJobEnvelope


def _publish_file(source: Path, target: Path, *, overwrite: bool) -> None:
    """Windows同盘rename不覆盖；POSIX用原子no-replace链接后移除别名，禁止暗中替换。"""
    if overwrite:
        os.replace(source, target)
    elif os.name == "nt":
        os.rename(source, target)
    else:
        os.link(source, target)
        source.unlink()


class HandoffIntakeManager:
    """一次宿主会话的有界候选和复制进度；重开后重新选择，不伪造已通过检查。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._choices: OrderedDict[str, _Choice] = OrderedDict()
        self._tickets: OrderedDict[str, _Ticket] = OrderedDict()
        self._jobs: OrderedDict[str, _Job] = OrderedDict()
        self._ready: OrderedDict[str, _Ready] = OrderedDict()

    def _choice(
        self, binding: HandoffImportBinding, session: HostBridgeSession, path: Path
    ) -> _Choice:
        identity = _identity(path)
        assert identity is not None
        return _Choice(binding, session, path, identity, monotonic() + 300)

    def observe(
        self, payload: object, *, session: HostBridgeSession, application: ProjectServiceApplication
    ) -> HandoffIntakeObserveEnvelope:
        binding = _parse(HandoffImportBinding, payload)
        candidates: list[HandoffIntakeCandidate] = []
        rejected = 0
        with application.handoff_import_authority(binding) as authority:
            intake_context(authority)
            inbox = _incoming_path(authority)
            choices: dict[str, _Choice] = {}
            with os.scandir(inbox) as entries:
                for index, entry in enumerate(entries):
                    if index >= 256:
                        raise _failure("交回目录内容过多，请仅保留本次来件", "LIMIT")
                    try:
                        path = inbox / entry.name
                        choice = self._choice(binding, session, path)
                        container = mr.detect_container(path, probe_header(path))
                        if _identity(path) != choice.identity:
                            raise _failure("文件仍在变化")
                    except (OSError, ValueError, HostBridgeFailure, Av27MediaError):
                        rejected += 1
                        continue
                    if len(candidates) >= 32:
                        raise _failure("视频候选超过32份，请先整理交回目录", "LIMIT")
                    handle = secrets.token_urlsafe(24)
                    choices[handle] = choice
                    candidates.append(
                        HandoffIntakeCandidate(
                            candidate_handle=handle,
                            name=path.name,
                            size=choice.identity.size,
                            container=container,
                        )
                    )
            candidates.sort(key=lambda value: value.name.casefold())
            application.assert_preview_session(binding.project_session_id)
            with self._lock:
                self._choices.update(choices)
                while len(self._choices) > 128:
                    self._choices.popitem(last=False)
        return HandoffIntakeObserveEnvelope(
            **binding.model_dump(),
            inbox_path=str(inbox),
            candidates=tuple(candidates),
            rejected_count=rejected,
            message="只发现候选，不代表检查通过；多个视频请明确选择。",
        )

    def select(
        self, payload: object, *, session: HostBridgeSession, application: ProjectServiceApplication
    ) -> HandoffIntakeSelectEnvelope:
        request = _parse(HandoffIntakeSelectRequest, payload)
        binding = HandoffImportBinding.model_validate(
            request.model_dump(exclude={"selection_handle", "candidate_handle"}), strict=True
        )
        if request.selection_handle is not None:
            path = session.resolve_path_reference(
                PickerSelectionReference(
                    kind="picker_selection", selection_handle=request.selection_handle
                )
            )
            choice = self._choice(binding, session, path)
        else:
            with self._lock:
                observed = self._choices.get(request.candidate_handle or "")
            if observed is None:
                raise _failure("目录观察已过期，请刷新文件列表", "EXPIRED")
            choice = observed
        self._assert_choice(choice, session, binding)
        # 选择是新的显式动作；已复核文件未变化后重新给足选择票据300秒。
        choice = replace(choice, expires=monotonic() + 300)
        with application.handoff_import_authority(binding) as authority:
            context = intake_context(authority)
            inbox = _incoming_path(authority)
            try:
                container = mr.detect_container(choice.source, probe_header(choice.source))
            except (OSError, Av27MediaError) as error:
                raise _failure(f"无法识别所选视频：{error}", "MEDIA") from error
            name = mr.archive_basename(context.inputs, container)
            incoming = _safe_path(inbox / name, allow_missing_leaf=True)
            output = _safe_path(
                Path(authority.node_run.work_dir) / "outputs" / name, allow_missing_leaf=True
            )
            if choice.source == output:
                raise _failure("该文件已在正式输出区，请使用正式检查与提交入口", "PUBLISHED")
            # 只有当前incoming直属文件可以移动；从其他位置选择一律保源复制。
            action: Literal["copy", "rename", "none"] = (
                "none"
                if choice.source == incoming
                else "rename"
                if choice.source.parent == inbox
                else "copy"
            )
            if action != "copy" and choice.source.stat().st_nlink != 1:
                raise _failure("交回文件与其他路径共享硬链接，不能移动", "PATH")
            ticket = _Ticket(
                choice,
                incoming,
                output,
                _identity(incoming, allow_missing=True, target=True),
                _identity(output, allow_missing=True, target=True),
                action,
            )
            self._assert_ticket(ticket, authority)
            identifier = secrets.token_urlsafe(24)
            with self._lock:
                self._tickets[identifier] = ticket
                while len(self._tickets) > 32:
                    self._tickets.popitem(last=False)
        return HandoffIntakeSelectEnvelope(
            **binding.model_dump(),
            ticket_id=identifier,
            source_name=choice.source.name,
            source_path=str(choice.source),
            source_size=choice.identity.size,
            container=container,
            archive_name=name,
            incoming_path=str(incoming),
            output_path=str(output),
            replace_existing=(ticket.incoming_identity is not None and action != "none")
            or ticket.output_identity is not None,
            action=action,
        )

    def check(
        self, payload: object, *, session: HostBridgeSession, application: ProjectServiceApplication
    ) -> HandoffIntakeJobRequest:
        request = _parse(HandoffIntakeCheckRequest, payload)
        with self._lock:
            ticket = self._tickets.pop(request.ticket_id, None)
        if ticket is None:
            raise _failure("选择已过期或已使用，请重新选择", "EXPIRED")
        self._assert_choice(ticket.choice, session, ticket.choice.binding)
        replacing = (
            ticket.incoming_identity is not None and ticket.action != "none"
        ) or ticket.output_identity is not None
        if replacing and not request.overwrite:
            raise _failure("已有同名文件，需要明确允许替换", "OVERWRITE")
        # 在返回job之前取得应用互斥；工作线程退出该scope，期间禁止换工程、Submit和退出。
        scope = application.handoff_import_authority(ticket.choice.binding, importing=True)
        authority = scope.__enter__()
        job_id = secrets.token_urlsafe(24)
        job = _Job(
            ticket.choice,
            HandoffIntakeJobEnvelope(
                contract_version="0.3.0",
                job_id=job_id,
                phase="checking",
                bytes_done=0,
                total_bytes=ticket.choice.identity.size,
            ),
        )
        with self._lock:
            self._jobs[job_id] = job
            while len(self._jobs) > 32:
                self._jobs.popitem(last=False)

        def work() -> None:
            changes: dict[str, object]
            try:
                self._check_and_collect(ticket, authority, job)
                ready_id = secrets.token_urlsafe(24)
                identity = _identity(ticket.incoming)
                assert identity is not None
                with self._lock:
                    self._ready[ready_id] = _Ready(ticket, identity)
                    while len(self._ready) > 32:
                        self._ready.popitem(last=False)
                changes = {
                    "phase": "ready",
                    "ready_id": ready_id,
                    "message": "检查通过并已收纳；仍需由你提交并继续。",
                }
            except Exception as error:
                changes = {
                    "phase": "failed",
                    "message": (str(error) or type(error).__name__)[:2000],
                }
            finally:
                scope.__exit__(None, None, None)
            # ready意味着用户已经可以提交，不先公开ready再释放应用互斥。
            self._update(job, **changes)

        try:
            threading.Thread(target=work, name="zniku-handoff-intake", daemon=True).start()
        except BaseException:
            scope.__exit__(None, None, None)
            raise
        return HandoffIntakeJobRequest(contract_version="0.3.0", job_id=job_id)

    def status(
        self, payload: object, *, session: HostBridgeSession, application: ProjectServiceApplication
    ) -> HandoffIntakeJobEnvelope:
        request = _parse(HandoffIntakeJobRequest, payload)
        with self._lock:
            job = self._jobs.get(request.job_id)
            if job is None or job.choice.session is not session:
                raise _failure("找不到本次导入进度，请重新选择", "EXPIRED")
            envelope = job.envelope
        application.assert_preview_session(job.choice.binding.project_session_id)
        return envelope

    def publish(
        self, payload: object, *, session: HostBridgeSession, application: ProjectServiceApplication
    ) -> HandoffIntakePublishEnvelope:
        """用户点击提交时转存已检查来件；不登记Artifact，正式Submit仍由原Runtime执行。"""
        request = _parse(HandoffIntakePublishRequest, payload)
        with self._lock:
            ready = self._ready.pop(request.ready_id, None)
        if ready is None or ready.ticket.choice.session is not session:
            raise _failure("检查结果已失效，请重新选择并检查", "EXPIRED")
        ticket = ready.ticket
        with application.handoff_import_authority(
            ticket.choice.binding, importing=True
        ) as authority:
            context = intake_context(authority)
            if (
                _incoming_path(authority) != ticket.incoming.parent
                or _identity(ticket.incoming) != ready.identity
                or _identity(ticket.output, allow_missing=True, target=True)
                != ticket.output_identity
            ):
                raise _failure("已检查文件或输出位置变化，未发布；请重新检查")
            output_root = _safe_path(Path(authority.node_run.work_dir) / "outputs")
            for container in mr.CONTAINERS:
                alternative = output_root / mr.archive_basename(context.inputs, container)
                if alternative != ticket.output and (
                    alternative.exists() or alternative.is_symlink()
                ):
                    raise _failure(
                        "正式输出区已有另一封装结果，不会猜选或删除，请先明确处理冲突", "AMBIGUOUS"
                    )
            _publish_file(
                ticket.incoming, ticket.output, overwrite=ticket.output_identity is not None
            )
        return HandoffIntakePublishEnvelope(output_path=str(ticket.output))

    def _check_and_collect(self, ticket: _Ticket, authority: ImportAuthority, job: _Job) -> None:
        self._assert_ticket(ticket, authority)
        context = intake_context(authority)
        result = mr.inspect_candidate(context.inputs, context.node.parameters, ticket.choice.source)
        if result.archive_name != ticket.incoming.name:
            raise _failure("实际媒体格式与选择时不同，请重新选择")
        self._assert_ticket(ticket, authority)
        if ticket.action == "none":
            return
        if ticket.action == "rename":
            _publish_file(
                ticket.choice.source,
                ticket.incoming,
                overwrite=ticket.incoming_identity is not None,
            )
            return
        self._update(job, phase="copying", message="检查通过，正在复制到交回目录；外部原文件保留。")
        work = _safe_path(Path(authority.node_run.work_dir))
        staging = Path(tempfile.mkdtemp(prefix=".handoff-intake-", dir=work))
        staging_stat = staging.stat()
        staged = staging / ticket.incoming.name
        staged_identity: tuple[int, int] | None = None
        try:
            with ticket.choice.source.open("rb") as source, staged.open("xb") as target:
                staged_stat = os.fstat(target.fileno())
                staged_identity = (staged_stat.st_dev, staged_stat.st_ino)
                opened = os.fstat(source.fileno())
                identity = ticket.choice.identity
                if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
                    identity.device,
                    identity.inode,
                    identity.size,
                    identity.mtime_ns,
                ):
                    raise _failure("开始复制时文件已变化")
                done = 0
                while block := source.read(8 * 1024 * 1024):
                    if done + len(block) > identity.size:
                        raise _failure("复制期间源文件仍在增长，未收纳；请写入完成后重新选择")
                    target.write(block)
                    done += len(block)
                    self._update(job, bytes_done=done)
                target.flush()
                os.fsync(target.fileno())
            self._assert_ticket(ticket, authority)
            if staged.stat().st_size != ticket.choice.identity.size:
                raise _failure("复制结果长度不一致，未收纳")
            _publish_file(staged, ticket.incoming, overwrite=ticket.incoming_identity is not None)
        finally:
            # 仅删除本次创建的单个临时复制文件和空目录，不递归处理用户文件夹。
            try:
                current_dir = _safe_path(staging).stat()
                if (current_dir.st_dev, current_dir.st_ino) == (
                    staging_stat.st_dev,
                    staging_stat.st_ino,
                ) and staging.parent == work:
                    if staged.exists() and staged_identity is not None:
                        current_file = _safe_path(staged).stat()
                        if (current_file.st_dev, current_file.st_ino) == staged_identity:
                            staged.unlink()
                    staging.rmdir()
            except (OSError, HostBridgeFailure):
                pass

    def _update(self, job: _Job, **changes: object) -> None:
        with self._lock:
            job.envelope = HandoffIntakeJobEnvelope.model_validate(
                {**job.envelope.model_dump(), **changes}, strict=True
            )

    @staticmethod
    def _assert_choice(
        choice: _Choice, session: HostBridgeSession, binding: HandoffImportBinding
    ) -> None:
        if (
            choice.session is not session
            or choice.binding != binding
            or monotonic() >= choice.expires
        ):
            raise _failure("选择已过期或属于其他任务，请重新选择", "EXPIRED")
        if _identity(choice.source) != choice.identity:
            raise _failure("文件正在写入或已变化，请处理完成后重新选择")

    @staticmethod
    def _assert_ticket(ticket: _Ticket, authority: ImportAuthority) -> None:
        intake_context(authority)
        if (
            _incoming_path(authority) != ticket.incoming.parent
            or _identity(ticket.choice.source) != ticket.choice.identity
            or _identity(ticket.incoming, allow_missing=True, target=True)
            != ticket.incoming_identity
            or _identity(ticket.output, allow_missing=True, target=True) != ticket.output_identity
        ):
            raise _failure("来源或目标已变化，请重新选择；已有文件没有被替换")
