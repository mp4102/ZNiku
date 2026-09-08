"""提供 Studio 的 Windows loopback HostBridge 能力与严格 wire 合同。

HostBridge 只代理原生路径选择和两个固定系统动作，不参与 Project、Graph 或 Runtime
mutation。所有有副作用动作必须经过精确 Origin、launcher session token、短时一次性
票据与闭合 capability 校验；系统动作只接受后端可解引用的 authority reference，绝不
接受浏览器提交的任意路径、executable、argv 或 shell 字符串。
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from http import HTTPStatus
from ipaddress import ip_address
from pathlib import Path
from time import monotonic
from typing import Annotated, Any, Final, Literal, Protocol
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from zniku.media.probe import MediaNodeError, probe_media, require_media_kind
from zniku.runtime import NodeRunState

HOST_BRIDGE_CONTRACT_VERSION: Literal["0.3.0"] = "0.3.0"
HOST_TOKEN_HEADER: Final = "X-ZNIKU-Host-Token"
HOST_ACTION_TTL_SECONDS: Final = 5
MAX_PENDING_HOST_ACTIONS: Final = 64
MAX_PICKER_SELECTIONS: Final = 256

type HostCapability = Literal[
    "open_file",
    "open_files",
    "select_directory",
    "save_file",
    "reveal_in_file_manager",
    "open_with_system_player",
]

HOST_CAPABILITIES: Final[tuple[HostCapability, ...]] = (
    "open_file",
    "open_files",
    "select_directory",
    "save_file",
    "reveal_in_file_manager",
    "open_with_system_player",
)
_HOST_CAPABILITY_SET: Final = frozenset(HOST_CAPABILITIES)
_DIALOG_CAPABILITIES: Final = frozenset(
    {"open_file", "open_files", "select_directory", "save_file"}
)
_SYSTEM_CAPABILITIES: Final = frozenset({"reveal_in_file_manager", "open_with_system_player"})
_TOKEN_PATTERN: Final = re.compile(r"^[A-Za-z0-9_-]{43,256}$")
_PLAYER_MEDIA_SUFFIXES: Final = frozenset(
    {
        ".3gp",
        ".aac",
        ".ac3",
        ".aiff",
        ".alac",
        ".avi",
        ".flac",
        ".flv",
        ".m2ts",
        ".m4a",
        ".m4v",
        ".mka",
        ".mkv",
        ".mov",
        ".mp3",
        ".mp4",
        ".mpeg",
        ".mpg",
        ".mts",
        ".ogg",
        ".ogv",
        ".opus",
        ".ts",
        ".wav",
        ".webm",
        ".wma",
        ".wmv",
    }
)

HostIdentifier = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=160,
        strip_whitespace=False,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$",
    ),
]
HostRandomId = Annotated[
    str,
    StringConstraints(
        min_length=36,
        max_length=36,
        strip_whitespace=False,
        pattern=(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        ),
    ),
]
HostOpaqueId = Annotated[
    str,
    StringConstraints(
        min_length=24,
        max_length=256,
        strip_whitespace=False,
        pattern=r"^[A-Za-z0-9_-]+$",
    ),
]
HostLocalPath = Annotated[str, StringConstraints(min_length=1, max_length=32767)]


class HostBridgeModel(BaseModel):
    """HostBridge DTO 的严格、冻结基类。"""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        strict=True,
        validate_default=True,
    )


class HostCapabilityState(HostBridgeModel):
    """声明一个闭集 capability 在当前 launcher session 是否可用。"""

    capability: HostCapability
    available: bool
    unavailable_reason: Annotated[str, StringConstraints(min_length=1, max_length=4096)] | None = (
        None
    )

    @model_validator(mode="after")
    def validate_availability(self) -> HostCapabilityState:
        """可用能力不携带失败原因，不可用能力必须解释原因。"""

        if self.available == (self.unavailable_reason is not None):
            raise ValueError("E_HOST_BRIDGE_AVAILABILITY: available 与原因不一致")
        return self


class HostCapabilitiesEnvelope(HostBridgeModel):
    """返回固定顺序的完整 capability 闭集。"""

    contract_version: Literal["0.3.0"] = HOST_BRIDGE_CONTRACT_VERSION
    capabilities: tuple[HostCapabilityState, ...]

    @field_validator("capabilities", mode="before")
    @classmethod
    def normalize_capabilities(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("capabilities")
    @classmethod
    def validate_closed_set(
        cls,
        value: tuple[HostCapabilityState, ...],
    ) -> tuple[HostCapabilityState, ...]:
        if tuple(item.capability for item in value) != HOST_CAPABILITIES:
            raise ValueError("E_HOST_BRIDGE_CAPABILITY_SET: capabilities 必须是完整固定闭集")
        return value


class HostUserActionRequest(HostBridgeModel):
    """请求为一次显式点击签发绑定 capability 的短时票据。"""

    capability: HostCapability


class HostUserActionEnvelope(HostBridgeModel):
    """返回不含路径或 token 的一次性用户动作票据。"""

    contract_version: Literal["0.3.0"] = HOST_BRIDGE_CONTRACT_VERSION
    user_action_id: HostOpaqueId
    capability: HostCapability
    expires_in_seconds: Literal[5] = HOST_ACTION_TTL_SECONDS


class HostDialogArguments(HostBridgeModel):
    """四类 picker 共享的有限纯文本提示。"""

    title: Annotated[str, StringConstraints(min_length=1, max_length=240)] | None = None
    extensions: Annotated[tuple[str, ...], Field(min_length=1, max_length=32)] | None = None
    suggested_name: Annotated[str, StringConstraints(min_length=1, max_length=255)] | None = None

    @field_validator("extensions", mode="before")
    @classmethod
    def normalize_extensions(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("title", "suggested_name")
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        if value is not None and (value.strip() != value or "\x00" in value):
            raise ValueError("E_HOST_BRIDGE_ARGUMENTS: 文本含 NUL 或边界空白")
        return value

    @field_validator("extensions")
    @classmethod
    def validate_extensions(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is None:
            return None
        normalized: list[str] = []
        for item in value:
            if (
                not 2 <= len(item) <= 16
                or not item.startswith(".")
                or not item[1:].isascii()
                or not item[1:].isalnum()
            ):
                raise ValueError(
                    "E_HOST_BRIDGE_ARGUMENTS: extension 必须是 .mkv 形式的 ASCII 扩展名"
                )
            lowered = item.casefold()
            if lowered in normalized:
                raise ValueError("E_HOST_BRIDGE_ARGUMENTS: extensions 不得重复")
            normalized.append(lowered)
        return tuple(normalized)


class PickerSelectionReference(HostBridgeModel):
    """引用当前 launcher session 中一次 picker 明确返回的路径。"""

    kind: Literal["picker_selection"]
    selection_handle: HostOpaqueId


class ArtifactPathReference(HostBridgeModel):
    """引用 Project Service 已投影到明确 Run 闭包中的 Artifact。"""

    kind: Literal["artifact"]
    run_id: HostRandomId
    artifact_id: HostRandomId


class HandoffWorkDirectorySelector(HostBridgeModel):
    """选择 waiting attempt 的受控工作目录。"""

    role: Literal["work_directory"]


class HandoffInputArtifactSelector(HostBridgeModel):
    """选择 handoff 明确绑定的一个输入 Artifact。"""

    role: Literal["input_artifact"]
    artifact_id: HostRandomId


class HandoffOutputTargetSelector(HostBridgeModel):
    """选择 handoff server-declared 的一个输出目标。"""

    role: Literal["output_target"]
    port_id: HostIdentifier
    ordinal: Annotated[int, Field(ge=0)] | None = None


type HandoffPathSelector = Annotated[
    HandoffWorkDirectorySelector | HandoffInputArtifactSelector | HandoffOutputTargetSelector,
    Field(discriminator="role"),
]


class HandoffPathReference(HostBridgeModel):
    """引用最新 waiting NodeRun 的精确 handoff identity 与路径角色。"""

    kind: Literal["handoff"]
    run_id: HostRandomId
    node_run_id: HostRandomId
    handoff_id: HostRandomId
    selector: HandoffPathSelector


type HostPathReference = Annotated[
    PickerSelectionReference | ArtifactPathReference | HandoffPathReference,
    Field(discriminator="kind"),
]


class HostSystemArguments(HostBridgeModel):
    """系统动作只接受 authority reference，不接受 raw path。"""

    reference: HostPathReference


class HostInvokeRequest(HostBridgeModel):
    """描述一次票据消费；arguments 在消费票据后按 capability 严格解析。"""

    user_action_id: HostOpaqueId
    capability: HostCapability
    arguments: object


class HostSelection(HostBridgeModel):
    """返回用户明确选择的绝对路径及其 session-local authority handle。"""

    selection_handle: HostOpaqueId
    path: HostLocalPath

    @field_validator("path")
    @classmethod
    def validate_absolute_path(cls, value: str) -> str:
        if value.strip() != value or "\x00" in value or not Path(value).is_absolute():
            raise ValueError("E_HOST_BRIDGE_SELECTION_PATH: path 必须是无 NUL 的绝对路径")
        return value


class HostInvokeEnvelope(HostBridgeModel):
    """返回 picker 取消、选择或固定系统动作启动结果。"""

    contract_version: Literal["0.3.0"] = HOST_BRIDGE_CONTRACT_VERSION
    status: Literal["selected", "cancelled", "launched"]
    selections: tuple[HostSelection, ...] = ()

    @field_validator("selections", mode="before")
    @classmethod
    def normalize_selections(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_status(self) -> HostInvokeEnvelope:
        if (self.status == "selected") != bool(self.selections):
            raise ValueError("E_HOST_BRIDGE_RESULT: selected 与 selections 不一致")
        return self


@dataclass(frozen=True, slots=True)
class HostLaunchCommand:
    """保存由后端闭集映射生成的固定进程调用。"""

    executable: str
    argv: tuple[str, ...]
    shell: Literal[False] = False


class HostBridgeFailure(RuntimeError):
    """表示 HostBridge 拒绝或宿主动作失败，并携带稳定错误码。"""

    def __init__(self, code: str, message: str, *, http_status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


class HostPlatform(Protocol):
    """隔离原生对话框与系统启动，测试使用无窗口实现。"""

    def capability_states(self) -> Mapping[HostCapability, str | None]:
        """返回全部 capability；``None`` 表示可用，文本表示不可用原因。"""

    def choose_paths(
        self,
        capability: HostCapability,
        arguments: HostDialogArguments,
    ) -> Sequence[str] | None:
        """返回用户明确选择的宿主路径；取消时返回空序列或 ``None``。"""

    def launch(self, command: HostLaunchCommand) -> None:
        """使用后端固定 executable/argv 且 ``shell=False`` 启动系统动作。"""


class HostProjectReadAuthority(Protocol):
    """描述 HostBridge 解引用所需的 Project Service 只读投影。"""

    @property
    def work_root(self) -> Path:
        """返回 launcher 固定的 attempt 根。"""

    def inspect_run_detail(self, run_id: str) -> Any:
        """返回精确 Run detail；不存在或绑定错误时失败关闭。"""


class HostPathResolver(Protocol):
    """把非 picker authority reference 解引用为宿主路径。"""

    def resolve(self, reference: ArtifactPathReference | HandoffPathReference) -> Path:
        """解析并验证 reference；不得信任浏览器路径。"""


type HostPlayerMediaValidator = Callable[[Path], None]


class ProjectServiceHostPathResolver:
    """仅通过 Project Service 只读投影解析 Artifact/handoff 路径。

    该适配器位于 HostBridge 边界，不向 ``ProjectServiceApplication`` 添加 capability
    或 mutation。Run、Artifact 和 handoff 的绑定结论仍来自现有 Python authority。
    """

    def __init__(self, application: HostProjectReadAuthority) -> None:
        self._application = application

    def resolve(self, reference: ArtifactPathReference | HandoffPathReference) -> Path:
        """要求 Artifact 属于引用 Run，handoff 属于最新 waiting attempt。"""

        try:
            detail = self._application.inspect_run_detail(reference.run_id)
        except Exception as error:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_REFERENCE",
                (str(error) or type(error).__name__)[:4096],
                http_status=HTTPStatus.CONFLICT,
            ) from error
        if isinstance(reference, ArtifactPathReference):
            matches = tuple(
                item for item in detail.artifacts if item.artifact_id == reference.artifact_id
            )
            if len(matches) != 1:
                raise HostBridgeFailure(
                    "E_HOST_BRIDGE_REFERENCE",
                    "Artifact 不属于引用 Run 的 Project Service 投影",
                    http_status=HTTPStatus.CONFLICT,
                )
            return _existing_absolute_path(matches[0].path)

        node_runs = tuple(
            item for item in detail.run.node_runs if item.node_run_id == reference.node_run_id
        )
        if len(node_runs) != 1:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_REFERENCE",
                "NodeRun 不属于引用 Run",
                http_status=HTTPStatus.CONFLICT,
            )
        node_run = node_runs[0]
        latest_attempt = max(
            (item.attempt for item in detail.run.node_runs if item.node_id == node_run.node_id),
            default=0,
        )
        handoff = node_run.external_handoff
        if (
            node_run.state is not NodeRunState.WAITING_EXTERNAL
            or node_run.attempt != latest_attempt
            or handoff is None
            or handoff.handoff_id != reference.handoff_id
        ):
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_REFERENCE",
                "handoff 不是引用 Run 的最新 waiting attempt",
                http_status=HTTPStatus.CONFLICT,
            )

        selector = reference.selector
        if isinstance(selector, HandoffWorkDirectorySelector):
            root = self._application.work_root.resolve(strict=True)
            path = _existing_absolute_path(node_run.work_dir)
            try:
                path.relative_to(root)
            except ValueError as error:
                raise HostBridgeFailure(
                    "E_HOST_BRIDGE_REFERENCE",
                    "handoff work directory 不在 launcher 固定工作根内",
                    http_status=HTTPStatus.CONFLICT,
                ) from error
            if not path.is_dir():
                raise HostBridgeFailure(
                    "E_HOST_BRIDGE_PATH_KIND",
                    "handoff work directory 不是目录",
                    http_status=HTTPStatus.UNPROCESSABLE_ENTITY,
                )
            return path

        if isinstance(selector, HandoffInputArtifactSelector):
            if selector.artifact_id not in handoff.input_artifact_ids:
                raise HostBridgeFailure(
                    "E_HOST_BRIDGE_REFERENCE",
                    "Artifact 不属于 handoff 输入",
                    http_status=HTTPStatus.CONFLICT,
                )
            matches = tuple(
                item for item in detail.artifacts if item.artifact_id == selector.artifact_id
            )
            if len(matches) != 1:
                raise HostBridgeFailure(
                    "E_HOST_BRIDGE_REFERENCE",
                    "handoff 输入 Artifact 不在 Run detail 中",
                    http_status=HTTPStatus.CONFLICT,
                )
            return _existing_absolute_path(matches[0].path)

        matches = tuple(
            target
            for target in handoff.output_targets
            if target.port_id == selector.port_id and target.ordinal == selector.ordinal
        )
        if len(matches) != 1:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_REFERENCE",
                "handoff output target 不存在或不唯一",
                http_status=HTTPStatus.CONFLICT,
            )
        return _absolute_path(matches[0].path)


def _validate_player_media_path(path: Path) -> None:
    """在调用 Windows 文件关联前拒绝 ADS、主动内容和未知媒体后缀。"""

    if ":" in path.name or path.suffix.casefold() not in _PLAYER_MEDIA_SUFFIXES:
        raise HostBridgeFailure(
            "E_HOST_BRIDGE_PLAYER_MEDIA",
            "系统播放器只接受闭合音视频后缀且不得包含 alternate data stream",
            http_status=HTTPStatus.UNPROCESSABLE_ENTITY,
        )


def _probe_player_media(path: Path) -> None:
    """使用 Runtime 已有轻量 FFprobe 证明目标至少含一路音频或视频流。"""

    info = probe_media(path, count_frames=False)
    require_media_kind(info, "MediaFile")


@dataclass(frozen=True, slots=True)
class _UserAction:
    capability: HostCapability
    expires_at: float


class HostBridgeSession:
    """管理一次 launcher 生命周期内的 token、票据和 picker handles。"""

    def __init__(
        self,
        platform: HostPlatform,
        *,
        studio_origin: str,
        path_resolver: HostPathResolver,
        clock: Callable[[], float] = monotonic,
        player_media_validator: HostPlayerMediaValidator = _probe_player_media,
    ) -> None:
        self.platform = platform
        self.studio_origin = validate_studio_origin(studio_origin)
        self._token = secrets.token_urlsafe(32)
        if _TOKEN_PATTERN.fullmatch(self._token) is None:
            raise AssertionError("secrets.token_urlsafe(32) 未产生 URL-safe token")
        if len(self._token) < 43:  # pragma: no cover - secrets 合同防御
            raise AssertionError("secrets.token_urlsafe(32) 未产生 256-bit token")
        self._path_resolver = path_resolver
        self._clock = clock
        self._player_media_validator = player_media_validator
        self._actions: dict[str, _UserAction] = {}
        self._selections: OrderedDict[str, Path] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def token(self) -> str:
        """仅供 launcher 注入页面内存；不得写入 URL、日志、Project 或持久偏好。"""

        return self._token

    def authorize(self, *, client_host: str, origin: str | None, token: str | None) -> None:
        """在所有 HostBridge HTTP route 前验证 loopback、Origin 和 token。"""

        try:
            loopback = ip_address(client_host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_LAN_FORBIDDEN",
                "HostBridge 只接受当前主机 loopback 请求",
                http_status=HTTPStatus.FORBIDDEN,
            )
        if origin != self.studio_origin:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_ORIGIN",
                "Origin 与 launcher 固定的 Studio Origin 不一致",
                http_status=HTTPStatus.FORBIDDEN,
            )
        if token is None or not secrets.compare_digest(token, self._token):
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_SESSION",
                "HostBridge session token 无效",
                http_status=HTTPStatus.FORBIDDEN,
            )

    def inspect_capabilities(self) -> HostCapabilitiesEnvelope:
        """返回平台报告的完整闭集；未知或缺失状态失败关闭。"""

        try:
            states = self.platform.capability_states()
            state_keys = set(states)
        except Exception as error:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_UNAVAILABLE",
                (str(error) or type(error).__name__)[:4096],
                http_status=HTTPStatus.SERVICE_UNAVAILABLE,
            ) from error
        if state_keys != _HOST_CAPABILITY_SET:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_UNAVAILABLE",
                "宿主未报告完整 capability 闭集",
                http_status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
        try:
            return HostCapabilitiesEnvelope(
                capabilities=tuple(
                    HostCapabilityState(
                        capability=capability,
                        available=states[capability] is None,
                        unavailable_reason=states[capability],
                    )
                    for capability in HOST_CAPABILITIES
                )
            )
        except (KeyError, ValidationError) as error:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_UNAVAILABLE",
                "宿主 capability 状态无效",
                http_status=HTTPStatus.SERVICE_UNAVAILABLE,
            ) from error

    def issue_user_action(self, payload: object) -> HostUserActionEnvelope:
        """签发 5 秒、绑定 capability、单次消费的有限票据。"""

        try:
            request = HostUserActionRequest.model_validate(payload, strict=True)
        except ValidationError as error:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_REQUEST_INVALID",
                "HostBridge user-action request 字段或类型无效",
                http_status=HTTPStatus.UNPROCESSABLE_ENTITY,
            ) from error
        availability = {item.capability: item for item in self.inspect_capabilities().capabilities}[
            request.capability
        ]
        if not availability.available:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_CAPABILITY_UNAVAILABLE",
                availability.unavailable_reason or "宿主 capability 不可用",
                http_status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
        action_id = secrets.token_urlsafe(24)
        with self._lock:
            now = self._clock()
            self._actions = {
                key: action for key, action in self._actions.items() if action.expires_at > now
            }
            if len(self._actions) >= MAX_PENDING_HOST_ACTIONS:
                raise HostBridgeFailure(
                    "E_HOST_BRIDGE_ACTION_LIMIT",
                    "待消费用户动作票据已达到上限",
                    http_status=HTTPStatus.TOO_MANY_REQUESTS,
                )
            self._actions[action_id] = _UserAction(
                capability=request.capability,
                expires_at=now + HOST_ACTION_TTL_SECONDS,
            )
        return HostUserActionEnvelope(
            user_action_id=action_id,
            capability=request.capability,
        )

    def invoke(self, payload: object) -> HostInvokeEnvelope:
        """先消费票据，再校验参数并执行闭集动作；失败和取消均不可重放。"""

        try:
            request = HostInvokeRequest.model_validate(payload, strict=True)
        except ValidationError as error:
            action_id = payload.get("user_action_id") if isinstance(payload, Mapping) else None
            if isinstance(action_id, str):
                with self._lock:
                    self._actions.pop(action_id, None)
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_REQUEST_INVALID",
                "HostBridge invoke request 字段或类型无效",
                http_status=HTTPStatus.UNPROCESSABLE_ENTITY,
            ) from error

        with self._lock:
            action = self._actions.pop(request.user_action_id, None)
        if action is None:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_ACTION_REQUIRED",
                "必须先通过显式用户动作取得一次性票据",
                http_status=HTTPStatus.CONFLICT,
            )
        if action.expires_at <= self._clock():
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_ACTION_EXPIRED",
                "用户动作票据已过期",
                http_status=HTTPStatus.CONFLICT,
            )
        if action.capability != request.capability:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_ACTION_MISMATCH",
                "用户动作票据与 capability 不匹配",
                http_status=HTTPStatus.CONFLICT,
            )
        if request.capability in _DIALOG_CAPABILITIES:
            return self._invoke_dialog(request.capability, request.arguments)
        if request.capability in _SYSTEM_CAPABILITIES:
            return self._invoke_system_action(request.capability, request.arguments)
        raise AssertionError("Host capability 闭集漏掉处理分支")

    def _invoke_dialog(
        self,
        capability: HostCapability,
        arguments_value: object,
    ) -> HostInvokeEnvelope:
        try:
            arguments = HostDialogArguments.model_validate(arguments_value, strict=True)
        except ValidationError as error:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_ARGUMENTS",
                "HostBridge picker arguments 字段或类型无效",
                http_status=HTTPStatus.UNPROCESSABLE_ENTITY,
            ) from error
        if capability == "select_directory" and (
            arguments.extensions is not None or arguments.suggested_name is not None
        ):
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_ARGUMENTS",
                "select_directory 只接受 title",
                http_status=HTTPStatus.UNPROCESSABLE_ENTITY,
            )
        if capability in {"open_file", "open_files"} and arguments.suggested_name is not None:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_ARGUMENTS",
                "open picker 不接受 suggested_name",
                http_status=HTTPStatus.UNPROCESSABLE_ENTITY,
            )
        try:
            selected = self.platform.choose_paths(capability, arguments)
        except HostBridgeFailure:
            raise
        except Exception as error:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_DIALOG",
                (str(error) or type(error).__name__)[:4096],
                http_status=HTTPStatus.INTERNAL_SERVER_ERROR,
            ) from error
        if not selected:
            return HostInvokeEnvelope(status="cancelled")
        paths = _normalize_selected_paths(capability, selected)
        selections: list[HostSelection] = []
        with self._lock:
            for path in paths:
                handle = secrets.token_urlsafe(24)
                self._selections[handle] = path
                selections.append(HostSelection(selection_handle=handle, path=str(path)))
            while len(self._selections) > MAX_PICKER_SELECTIONS:
                self._selections.popitem(last=False)
        return HostInvokeEnvelope(status="selected", selections=tuple(selections))

    def _invoke_system_action(
        self,
        capability: HostCapability,
        arguments_value: object,
    ) -> HostInvokeEnvelope:
        try:
            arguments = HostSystemArguments.model_validate(arguments_value, strict=True)
        except ValidationError as error:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_ARGUMENTS",
                "HostBridge system-action arguments 字段或类型无效",
                http_status=HTTPStatus.UNPROCESSABLE_ENTITY,
            ) from error
        path = self.resolve_path_reference(arguments.reference)
        if capability == "open_with_system_player":
            if not path.is_file():
                raise HostBridgeFailure(
                    "E_HOST_BRIDGE_PATH_KIND",
                    "系统播放器只能打开普通文件",
                    http_status=HTTPStatus.UNPROCESSABLE_ENTITY,
                )
            _validate_player_media_path(path)
            try:
                self._player_media_validator(path)
            except HostBridgeFailure:
                raise
            except MediaNodeError as error:
                raise HostBridgeFailure(
                    "E_HOST_BRIDGE_PLAYER_MEDIA",
                    "文件未通过系统播放器媒体流校验",
                    http_status=HTTPStatus.UNPROCESSABLE_ENTITY,
                ) from error
            except Exception as error:
                raise HostBridgeFailure(
                    "E_HOST_BRIDGE_PLAYER_MEDIA",
                    "系统播放器媒体校验不可用",
                    http_status=HTTPStatus.UNPROCESSABLE_ENTITY,
                ) from error
        command = _launch_command(capability, path)
        try:
            self.platform.launch(command)
        except Exception as error:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_LAUNCH",
                (str(error) or type(error).__name__)[:4096],
                http_status=HTTPStatus.INTERNAL_SERVER_ERROR,
            ) from error
        return HostInvokeEnvelope(status="launched")

    def resolve_path_reference(self, reference: HostPathReference) -> Path:
        """只读解析当前 session 的路径引用，供系统动作与静帧预览共享。

        HTTP 调用者必须先执行 ``authorize``；此方法不签发票据、不启动进程，也不修改
        Project。无效 picker handle、错误 Run/handoff 绑定仍沿用同一失败语义。
        """

        if isinstance(reference, PickerSelectionReference):
            with self._lock:
                path = self._selections.get(reference.selection_handle)
            if path is None:
                raise HostBridgeFailure(
                    "E_HOST_BRIDGE_REFERENCE",
                    "picker selection handle 未知或已淘汰",
                    http_status=HTTPStatus.CONFLICT,
                )
            path = _existing_absolute_path(path)
        else:
            path = self._path_resolver.resolve(reference)
            path = _existing_absolute_path(path)
        return path


def _running_on_windows() -> bool:
    """保留运行时平台判断，避免类型检查器按自身宿主裁剪另一平台分支。"""

    return sys.platform == "win32"


class WindowsHostPlatform:
    """使用临时置顶 owner 的 Windows Tk picker 与固定系统命令，不接管浏览器窗口。"""

    def __init__(self) -> None:
        self._dialog_lock = threading.Lock()

    def capability_states(self) -> Mapping[HostCapability, str | None]:
        """在不创建窗口的前提下报告 Windows/Tk 能力。"""

        if not _running_on_windows():
            reason = "v0.3.0 HostBridge 只正式支持 Windows"
            return dict.fromkeys(HOST_CAPABILITIES, reason)
        picker_reason: str | None = None
        try:
            import tkinter  # noqa: F401
            from tkinter import filedialog

            _ = filedialog.askopenfilename
        except (ImportError, RuntimeError) as error:
            picker_reason = (str(error) or type(error).__name__)[:4096]
        return {
            capability: picker_reason if capability in _DIALOG_CAPABILITIES else None
            for capability in HOST_CAPABILITIES
        }

    def choose_paths(
        self,
        capability: HostCapability,
        arguments: HostDialogArguments,
    ) -> Sequence[str] | None:
        """串行打开置顶 picker，取消或失败都销毁 owner 并释放互斥。

        隐藏 root 不等于前台窗口；Windows 原生对话框只保证在 owner 上方，不保证高于
        浏览器。临时透明 toolwindow 必须实际映射，使其 native topmost 属性生效，并由
        owned dialog 继承。只在用户点击时请求一次焦点，不循环抢焦点或更改系统前台策略。
        """

        if not self._dialog_lock.acquire(blocking=False):
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_DIALOG_BUSY",
                "另一个原生路径选择器仍在打开",
                http_status=HTTPStatus.CONFLICT,
            )
        try:
            import tkinter as tk
            from tkinter import filedialog

            filetypes: list[tuple[str, str]] = [("所有文件", "*.*")]
            if arguments.extensions:
                filetypes = [("允许的文件", " ".join(f"*{item}" for item in arguments.extensions))]
            root = tk.Tk()
            try:
                root.withdraw()
                root.title("ZNIKU Studio")
                root.geometry(
                    f"1x1+{max(0, root.winfo_screenwidth() // 2)}"
                    f"+{max(0, root.winfo_screenheight() // 2)}"
                )
                root.attributes("-alpha", 0.0)
                root.attributes("-toolwindow", True)
                root.attributes("-topmost", True)
                root.deiconify()
                root.update_idletasks()
                root.lift()
                # Windows 可拒绝前台请求；这不能取消已生效的置顶或扩大为强制抢焦点。
                with suppress(tk.TclError):
                    root.focus_force()
                if capability == "open_file":
                    value = filedialog.askopenfilename(
                        parent=root,
                        title=arguments.title,
                        filetypes=filetypes,
                    )
                    return (value,) if value else None
                if capability == "open_files":
                    values = filedialog.askopenfilenames(
                        parent=root,
                        title=arguments.title,
                        filetypes=filetypes,
                    )
                    return tuple(values) or None
                if capability == "select_directory":
                    value = filedialog.askdirectory(
                        parent=root,
                        title=arguments.title,
                        mustexist=True,
                    )
                    return (value,) if value else None
                if capability == "save_file":
                    value = filedialog.asksaveasfilename(
                        parent=root,
                        title=arguments.title,
                        initialfile=arguments.suggested_name,
                        filetypes=filetypes,
                    )
                    return (value,) if value else None
                raise AssertionError("非 picker capability 进入 choose_paths")
            finally:
                # 撤销置顶失败也必须尝试销毁；销毁失败仍由最外层 finally 释放选择器锁。
                try:
                    root.attributes("-topmost", False)
                finally:
                    root.destroy()
        finally:
            self._dialog_lock.release()

    def launch(self, command: HostLaunchCommand) -> None:
        """以 argv 数组和 ``shell=False`` 启动后端固定系统命令。"""

        subprocess.Popen(
            [command.executable, *command.argv],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=command.shell,
            close_fds=True,
        )


def create_project_service_host_bridge_session(
    application: HostProjectReadAuthority,
    *,
    studio_origin: str,
    platform: HostPlatform | None = None,
    player_media_validator: HostPlayerMediaValidator = _probe_player_media,
) -> HostBridgeSession:
    """构造正式 Project Service HostBridge session，供 Phase 5 launcher 注入。

    本函数只把现有只读 Project Service 投影接到 HostBridge resolver。launcher 仍负责生成
    精确 Studio Origin，并把返回 session 的 token 仅注入页面内存；此处不写 URL、环境、
    localStorage、Project 或日志。
    """

    return HostBridgeSession(
        platform or WindowsHostPlatform(),
        studio_origin=studio_origin,
        path_resolver=ProjectServiceHostPathResolver(application),
        player_media_validator=player_media_validator,
    )


def validate_studio_origin(value: str) -> str:
    """只接受带显式非特权端口的 canonical IPv4 loopback HTTP Origin。"""

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("Studio Origin 格式无效") from error
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or port is None
        or not 1024 <= port <= 65_535
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or value != f"http://127.0.0.1:{port}"
    ):
        raise ValueError("Studio Origin 必须是 http://127.0.0.1:<port> 的精确形式")
    return value


def _absolute_path(value: str | Path) -> Path:
    raw = str(value)
    if not raw or raw.strip() != raw or "\x00" in raw or len(raw) > 32767:
        raise HostBridgeFailure(
            "E_HOST_BRIDGE_PATH",
            "宿主路径必须是无 NUL、无边界空白的非空路径",
            http_status=HTTPStatus.UNPROCESSABLE_ENTITY,
        )
    path = Path(raw)
    if not path.is_absolute():
        raise HostBridgeFailure(
            "E_HOST_BRIDGE_PATH",
            "宿主路径必须是绝对路径",
            http_status=HTTPStatus.UNPROCESSABLE_ENTITY,
        )
    try:
        return path.resolve(strict=False)
    except OSError as error:
        raise HostBridgeFailure(
            "E_HOST_BRIDGE_PATH",
            str(error),
            http_status=HTTPStatus.UNPROCESSABLE_ENTITY,
        ) from error


def _existing_absolute_path(value: str | Path) -> Path:
    path = _absolute_path(value)
    try:
        return path.resolve(strict=True)
    except OSError as error:
        raise HostBridgeFailure(
            "E_HOST_BRIDGE_PATH",
            str(error),
            http_status=HTTPStatus.UNPROCESSABLE_ENTITY,
        ) from error


def _normalize_selected_paths(
    capability: HostCapability,
    values: Sequence[str],
) -> tuple[Path, ...]:
    if not 1 <= len(values) <= MAX_PICKER_SELECTIONS:
        raise HostBridgeFailure(
            "E_HOST_BRIDGE_DIALOG_RESULT",
            f"picker 必须返回 1..{MAX_PICKER_SELECTIONS} 个路径",
            http_status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )
    if capability != "open_files" and len(values) != 1:
        raise HostBridgeFailure(
            "E_HOST_BRIDGE_DIALOG_RESULT",
            "单选 picker 必须返回一个路径",
            http_status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )
    paths: list[Path] = []
    identities: set[str] = set()
    for value in values:
        path = _absolute_path(value)
        try:
            if capability == "save_file":
                if not path.parent.resolve(strict=True).is_dir():
                    raise OSError("save target 父目录不存在")
            else:
                path = path.resolve(strict=True)
                if capability == "select_directory" and not path.is_dir():
                    raise OSError("选择结果不是目录")
                if capability in {"open_file", "open_files"} and not path.is_file():
                    raise OSError("选择结果不是普通文件")
        except OSError as error:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_DIALOG_RESULT",
                str(error),
                http_status=HTTPStatus.INTERNAL_SERVER_ERROR,
            ) from error
        identity = os.path.normcase(str(path))
        if identity in identities:
            raise HostBridgeFailure(
                "E_HOST_BRIDGE_DIALOG_RESULT",
                "多选结果不得重复",
                http_status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        identities.add(identity)
        paths.append(path)
    return tuple(paths)


def _launch_command(capability: HostCapability, path: Path) -> HostLaunchCommand:
    if capability == "reveal_in_file_manager":
        return HostLaunchCommand(
            executable="explorer.exe",
            argv=(str(path),) if path.is_dir() else ("/select,", str(path)),
        )
    if capability == "open_with_system_player":
        return HostLaunchCommand(
            executable="rundll32.exe",
            argv=("url.dll,FileProtocolHandler", str(path)),
        )
    raise AssertionError("非系统 capability 进入固定 launch command")


HOST_PATH_REFERENCE_ADAPTER: Final[TypeAdapter[HostPathReference]] = TypeAdapter(HostPathReference)

__all__ = [
    "HOST_ACTION_TTL_SECONDS",
    "HOST_BRIDGE_CONTRACT_VERSION",
    "HOST_CAPABILITIES",
    "HOST_PATH_REFERENCE_ADAPTER",
    "HOST_TOKEN_HEADER",
    "ArtifactPathReference",
    "HandoffInputArtifactSelector",
    "HandoffOutputTargetSelector",
    "HandoffPathReference",
    "HandoffPathSelector",
    "HandoffWorkDirectorySelector",
    "HostBridgeFailure",
    "HostBridgeSession",
    "HostCapabilitiesEnvelope",
    "HostCapability",
    "HostCapabilityState",
    "HostDialogArguments",
    "HostInvokeEnvelope",
    "HostInvokeRequest",
    "HostLaunchCommand",
    "HostPathReference",
    "HostPathResolver",
    "HostPlatform",
    "HostPlayerMediaValidator",
    "HostProjectReadAuthority",
    "HostSelection",
    "HostSystemArguments",
    "HostUserActionEnvelope",
    "HostUserActionRequest",
    "PickerSelectionReference",
    "ProjectServiceHostPathResolver",
    "WindowsHostPlatform",
    "create_project_service_host_bridge_session",
    "validate_studio_origin",
]
