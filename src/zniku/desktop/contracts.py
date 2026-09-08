"""声明桌面生命周期 wire；只管理本实例关闭，不增加 Runtime command 或状态。"""

from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, field_validator

from zniku.project_service.host_bridge import HostBridgeModel, HostRandomId


class DesktopSessionEnvelope(HostBridgeModel):
    """返回当前启动实例与只读关闭条件。"""

    contract_version: Literal["0.3.0"] = "0.3.0"
    instance_id: HostRandomId
    busy: bool
    closing: bool


class DesktopCloseRequest(HostBridgeModel):
    """要求显式确认并精确绑定当前启动实例。"""

    contract_version: Literal["0.3.0"]
    instance_id: HostRandomId
    confirm: Literal[True]

    @field_validator("confirm", mode="before")
    @classmethod
    def validate_confirmation(cls, value: object) -> bool:
        if value is not True:
            raise ValueError("必须显式确认退出")
        return True


class DesktopCloseEnvelope(HostBridgeModel):
    """表示已经原子封闭后续修改准入并开始关闭 listener。"""

    contract_version: Literal["0.3.0"] = "0.3.0"
    instance_id: HostRandomId
    status: Literal["closing"] = "closing"


class DesktopRecentProject(HostBridgeModel):
    """最近工程只保存工程路径/名字/时间，不携带运行或媒体身份。"""

    path: Annotated[str, StringConstraints(min_length=1, max_length=4096)]
    name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    opened_at: Annotated[str, StringConstraints(min_length=1, max_length=64)]

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if (
            not (PureWindowsPath(value).is_absolute() or Path(value).is_absolute())
            or not value.lower().endswith(".zniku")
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or any(part in {".", ".."} for part in value.replace("\\", "/").split("/"))
        ):
            raise ValueError("最近工程必须是无上溯的绝对 .zniku 路径")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if value.strip() != value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("最近工程名称含控制字符或边界空白")
        return value

    @field_validator("opened_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        datetime.fromisoformat(value)
        return value


class DesktopPreferences(HostBridgeModel):
    """跨随机端口保存纯本机 UI 偏好，不写入 Project 或影响执行。"""

    density: Literal["creator", "advanced"] = "creator"
    recent_projects: Annotated[tuple[DesktopRecentProject, ...], Field(max_length=8)] = ()

    @field_validator("recent_projects", mode="before")
    @classmethod
    def normalize_recent_projects(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("recent_projects")
    @classmethod
    def validate_unique_paths(
        cls, value: tuple[DesktopRecentProject, ...]
    ) -> tuple[DesktopRecentProject, ...]:
        if len({item.path.casefold() for item in value}) != len(value):
            raise ValueError("最近工程路径不得重复")
        return value


class DesktopPreferencesEnvelope(DesktopPreferences):
    """把偏好严格绑定到当前桌面 session。"""

    contract_version: Literal["0.3.0"] = "0.3.0"
    instance_id: HostRandomId


class DesktopPreferencesRequest(DesktopPreferences):
    """原子更新纯 UI 偏好；未知字段失败关闭且保留原偏好文件。"""

    contract_version: Literal["0.3.0"]
    instance_id: HostRandomId
    density: Literal["creator", "advanced"]
    recent_projects: Annotated[tuple[DesktopRecentProject, ...], Field(max_length=8)]
