"""声明工程数据的持久位置与保留规则，不参与 Graph、Run 或结果失效语义。

路径仅是严格的本机定位值；本模块不创建目录、不访问媒体，也不以名字替代 Artifact 身份。
旧工程通过显式 legacy 投影继续使用原工作根，不能因升级或磁盘不可用静默换盘。
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import Field, StringConstraints, field_validator, model_validator

from .models import ProjectModel
from .paths import MEDIA_BASENAME_MAX_UNITS, validate_filename_component
from .storage_english import EnglishStorageState
from .storage_layout import StorageLayoutState

StoragePath = Annotated[str, StringConstraints(min_length=1, max_length=32767)]


def _absolute_path(value: str) -> PurePosixPath | PureWindowsPath:
    if not value.strip() or value != value.strip() or "\x00" in value:
        raise ValueError("E_PROJECT_STORAGE_PATH: 数据路径不得包含 NUL 或边界空白")
    path = PureWindowsPath(value) if PureWindowsPath(value).is_absolute() else PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("E_PROJECT_STORAGE_PATH: 数据路径必须是无上跳的绝对路径")
    return path


class ProjectStorage(ProjectModel):
    """保存工程资产位置；keep 表示完成、退出和重跑均不会自动删除已存资产。"""

    contract_version: Literal["0.3.0", "0.3.2", "0.3.5"] = "0.3.0"
    mode: Literal["adjacent", "custom", "legacy"]
    data_root: StoragePath
    attempts_root: StoragePath
    retention: Literal["keep"] = "keep"
    media_basename: Annotated[str, StringConstraints(min_length=1, max_length=180)] | None = None
    layout: Literal["uuid", "readable", "english"] = "uuid"
    layout_state: StorageLayoutState = Field(default_factory=StorageLayoutState)
    english_layout_state: EnglishStorageState = Field(default_factory=EnglishStorageState)
    data_id: str | None = None

    @field_validator("data_id")
    @classmethod
    def validate_data_id(cls, value: str | None) -> str | None:
        """数据根随机定位标识只用于跨盘找回，不是媒体摘要或 Graph 身份。"""

        if value is not None:
            parsed = UUID(value)
            if parsed.version != 4 or str(parsed) != value:
                raise ValueError("E_PROJECT_STORAGE_DATA_ID: data_id 必须是规范 UUIDv4")
        return value

    @field_validator("data_root", "attempts_root")
    @classmethod
    def validate_path(cls, value: str) -> str:
        _absolute_path(value)
        return value

    @field_validator("media_basename")
    @classmethod
    def validate_media_basename(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            validate_filename_component(value, max_units=MEDIA_BASENAME_MAX_UNITS)
        except ValueError as error:
            raise ValueError(
                "E_PROJECT_STORAGE_BASENAME: 媒体基础名必须是安全的单个文件名片段"
            ) from error
        return value

    @model_validator(mode="after")
    def validate_roots(self) -> ProjectStorage:
        if self.layout == "english" and self.contract_version != "0.3.5":
            raise ValueError("E_PROJECT_STORAGE_VERSION: 英文目录只允许 0.3.5 存储合同")
        if self.layout != "english" and (
            self.english_layout_state.nodes or self.english_layout_state.attempts
        ):
            raise ValueError("E_PROJECT_STORAGE_LAYOUT: 旧布局不得携带英文目录映射")
        if self.layout == "english" and (self.layout_state.nodes or self.layout_state.runs):
            raise ValueError("E_PROJECT_STORAGE_LAYOUT: 英文布局不得携带旧可读目录映射")
        if self.contract_version == "0.3.0" and (
            self.layout != "uuid"
            or self.layout_state.nodes
            or self.layout_state.runs
            or self.data_id is not None
        ):
            raise ValueError("E_PROJECT_STORAGE_VERSION: 可读目录及数据根身份仅允许 0.3.2 存储合同")
        if self.layout == "uuid" and (self.layout_state.nodes or self.layout_state.runs):
            raise ValueError("E_PROJECT_STORAGE_LAYOUT: UUID 布局不能携带可读目录映射")
        root, attempts = _absolute_path(self.data_root), _absolute_path(self.attempts_root)
        if self.layout == "english":
            if self.mode == "legacy" or root != attempts:
                raise ValueError("E_PROJECT_STORAGE_ROOT: 英文任务直接位于工程数据根")
            return self
        if (
            type(root) is not type(attempts)
            or attempts == root
            or not attempts.is_relative_to(root)
        ):
            raise ValueError("E_PROJECT_STORAGE_ROOT: attempts_root 必须严格位于 data_root 内")
        if self.mode != "legacy" and attempts != root / "attempts":
            raise ValueError("E_PROJECT_STORAGE_ROOT: 新工程 attempt 根固定为数据根下的 attempts")
        return self


def new_project_storage(
    project_path: str | Path,
    *,
    data_root: str | Path | None = None,
    media_basename: str | None = None,
) -> ProjectStorage:
    """按最终工程文件名计算默认 .data，不创建目录或使用临时工程名。"""

    project = Path(project_path).absolute()
    root = Path(data_root).absolute() if data_root is not None else project.with_suffix(".data")
    return ProjectStorage(
        contract_version="0.3.5",
        layout="english",
        data_id=str(uuid4()),
        mode="custom" if data_root is not None else "adjacent",
        data_root=str(root),
        attempts_root=str(root),
        media_basename=media_basename,
    )


def legacy_project_storage(legacy_root: str | Path) -> ProjectStorage:
    """仅投影原 launcher 根，不移动旧工程产物，也不持久化升级副作用。"""

    attempts = Path(legacy_root).absolute()
    return ProjectStorage(
        mode="legacy", data_root=str(attempts.parent), attempts_root=str(attempts)
    )


def bound_attempt_path(
    storage: ProjectStorage,
    *,
    node_id: str,
    run_id: str,
    attempt: int,
    node_run_id: str,
) -> Path:
    """读取唯一已持久化的存储定位；排队及复用任务仅保留未创建的内部 UUID 占位路径。"""

    root = Path(storage.attempts_root)
    if storage.layout == "uuid":
        return root / UUID(node_run_id).hex
    if storage.layout == "readable":
        location = storage.layout_state.nodes.get(node_id)
        number = storage.layout_state.runs.get(run_id)
        if location is None or number is None:
            raise ValueError("E_STORAGE_LAYOUT_BINDING: 缺少已有节点或 Run 存储映射")
        return root / location.relative_dir / f"R{number:03d}-A{attempt:03d}"
    english = storage.english_layout_state
    node = english.nodes.get(node_id)
    if node is None:
        raise ValueError("E_STORAGE_ENGLISH_BINDING: 缺少已有英文节点目录")
    binding = english.attempts.get(node_run_id)
    if binding is None:
        return root / UUID(node_run_id).hex
    if binding.node_id != node_id:
        raise ValueError("E_STORAGE_ENGLISH_BINDING: 处理轮次不属于当前节点")
    return root / node.relative_dir / f"round-{binding.round:03d}"
