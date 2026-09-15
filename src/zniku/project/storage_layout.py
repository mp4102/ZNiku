"""提供与执行语义无关的可读 attempt 存储定位和严格路径边界。

编号只来自工程 SQLite 中已保存的映射，不读取画布顺序或扫描磁盘。命名提示由可信宿主
提供，未知业务退回 custom；映射首次建立后不随别名变化。所有函数均不创建、移动或删除目录。
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator, model_validator

from .models import ProjectModel
from .paths import validate_filename_component

StorageNumber = Annotated[int, Field(ge=1, le=999999)]
_TASK_MAX_UNITS = 32
_ATTEMPT_MAX_UNITS = 180
_BATCH_PATTERN = re.compile(r"R[0-9]{3,6}-A[0-9]{3,6}\Z")
_NODE_PATTERN = re.compile(r".+__N[0-9]{3,6}\Z")
_CHAPTER_PATTERN = re.compile(r"[0-9]{4,6}-.+\Z")


def readable_component(value: str, *, max_units: int = _TASK_MAX_UNITS) -> str:
    """把展示文字投影为短文件名；身份由独立编号区分，不靠截断后的标题唯一。"""

    cleaned = "".join(
        "_" if ord(character) < 32 or character in '<>:"/\\|?*' else character
        for character in value
    ).strip(" .")
    result = ""
    for character in cleaned:
        if len((result + character).encode("utf-16-le")) // 2 > max_units:
            break
        result += character
    result = result.rstrip(" .") or "任务"
    try:
        validate_filename_component(result, max_units=max_units)
    except ValueError:
        result = "任务"
    return result


class AttemptNamingHint(ProjectModel):
    """宿主提供的纯展示分类；不是 Graph scope，也不能影响依赖或复用。"""

    category: Literal["common", "chapters", "program", "custom"] = "custom"
    task_name: Annotated[str, StringConstraints(min_length=1, max_length=300)] = "任务"
    chapter_index: StorageNumber | None = None
    chapter_name: Annotated[str, StringConstraints(min_length=1, max_length=100)] | None = None

    @model_validator(mode="after")
    def validate_chapter(self) -> AttemptNamingHint:
        if self.category == "chapters":
            if self.chapter_index is None or self.chapter_name is None:
                raise ValueError("E_STORAGE_LAYOUT_HINT: 章节目录必须提供编号和展示名")
        elif self.chapter_index is not None or self.chapter_name is not None:
            raise ValueError("E_STORAGE_LAYOUT_HINT: 非章节目录不能携带章节展示字段")
        return self


class StorageNodeLocation(ProjectModel):
    """一个稳定 node_id 的持久目录，不因后续展示改名而移动。"""

    number: StorageNumber
    relative_dir: Annotated[str, StringConstraints(min_length=1, max_length=120)]

    @model_validator(mode="after")
    def validate_directory(self) -> StorageNodeLocation:
        parts = PurePosixPath(self.relative_dir).parts
        if "\\" in self.relative_dir or self.relative_dir != "/".join(parts):
            raise ValueError("E_STORAGE_LAYOUT_DIRECTORY: 必须使用规范相对目录")
        valid = (len(parts) == 2 and parts[0] in {"common", "program", "custom"}) or (
            len(parts) == 3
            and parts[0] == "chapters"
            and _CHAPTER_PATTERN.fullmatch(parts[1]) is not None
        )
        if not valid or not parts[-1].endswith(f"__N{self.number:03d}"):
            raise ValueError("E_STORAGE_LAYOUT_DIRECTORY: 目录分类或稳定编号无效")
        for part in parts:
            validate_filename_component(part, max_units=64)
        return self


class StorageLayoutState(ProjectModel):
    """普通持久存储索引；随机身份仍是正式身份，编号不是领域版本或执行权威。"""

    nodes: dict[str, StorageNodeLocation] = Field(default_factory=dict)
    runs: dict[str, StorageNumber] = Field(default_factory=dict)

    @field_validator("runs")
    @classmethod
    def validate_run_ids(cls, value: dict[str, int]) -> dict[str, int]:
        for run_id in value:
            parsed = UUID(run_id)
            if parsed.version != 4 or str(parsed) != run_id:
                raise ValueError("E_STORAGE_LAYOUT_ID: run_id 必须是规范 UUIDv4")
        return value

    @model_validator(mode="after")
    def validate_unique(self) -> StorageLayoutState:
        numbers = [item.number for item in self.nodes.values()]
        paths = [item.relative_dir.casefold() for item in self.nodes.values()]
        if (
            any(not key or len(key) > 128 for key in self.nodes)
            or len(numbers) != len(set(numbers))
            or len(paths) != len(set(paths))
            or len(self.runs) != len(set(self.runs.values()))
        ):
            raise ValueError("E_STORAGE_LAYOUT_UNIQUE: 存储身份、编号或目录必须唯一")
        return self


def safe_attempt_directory(root: Path, path: Path) -> Path:
    """验证 UUID 或可读 attempt 实体目录；拒绝根目录、上跳、链接和重解析点。

    对可读目录预留至少 80 个 UTF-16 单位给 outputs/文件名，避免把外部应用长路径支持当作保证。
    磁盘写操作必须在调用此检查后单独进行；可信本机模型不声称消除恶意并发替换。
    """

    if not root.is_absolute() or not path.is_absolute() or ".." in (*root.parts, *path.parts):
        raise ValueError("E_STORAGE_LAYOUT_PATH: 必须是无上跳的绝对路径")
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise ValueError("E_STORAGE_LAYOUT_PATH: attempt 必须严格位于工作根内") from error
    parts = relative.parts
    legacy = len(parts) == 1 and re.fullmatch(r"[a-f0-9]{32}", parts[0]) is not None
    readable = (len(parts) == 3 and parts[0] in {"common", "program", "custom"}) or (
        len(parts) == 4
        and parts[0] == "chapters"
        and _CHAPTER_PATTERN.fullmatch(parts[1]) is not None
    )
    if not legacy and not (
        readable
        and _NODE_PATTERN.fullmatch(parts[-2]) is not None
        and _BATCH_PATTERN.fullmatch(parts[-1]) is not None
    ):
        raise ValueError("E_STORAGE_LAYOUT_PATH: 不是已支持的独立 attempt 目录形状")
    for part in parts:
        validate_filename_component(part, max_units=64)
    if not legacy and len(str(path).encode("utf-16-le")) // 2 > _ATTEMPT_MAX_UNITS:
        raise ValueError("E_STORAGE_LAYOUT_PATH_BUDGET: 数据父目录过长，请选择更短的数据位置")
    for candidate in (path, *path.parents):
        try:
            attributes = candidate.lstat()
        except FileNotFoundError:
            continue
        except NotADirectoryError as error:
            # POSIX 在祖先是文件时即拒绝 lstat，Windows 通常先返回不存在；
            # 两个平台均应返回稳定路径错误，而不是泄漏系统异常或绕过边界。
            raise ValueError("E_STORAGE_LAYOUT_PATH: 工作路径的已有组件必须是目录") from error
        if stat.S_ISLNK(attributes.st_mode) or (
            getattr(attributes, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError("E_STORAGE_LAYOUT_REPARSE: 工作路径不得穿过链接或重解析点")
        if not stat.S_ISDIR(attributes.st_mode):
            raise ValueError("E_STORAGE_LAYOUT_PATH: 工作路径的已有组件必须是目录")
    if os.path.normcase(str(path.resolve(strict=False))) != os.path.normcase(str(path)):
        raise ValueError("E_STORAGE_LAYOUT_PATH: 工作路径解析发生改变")
    return path


def allocate_attempt_path(
    state: StorageLayoutState,
    *,
    root: Path,
    node_id: str,
    run_id: str,
    attempt: int,
    hint: AttemptNamingHint | None = None,
) -> tuple[StorageLayoutState, Path]:
    """从已有工程映射分配位置，返回新索引与路径；调用方负责同 NodeRun 原子提交。"""

    updated, paths = allocate_attempt_paths(
        state,
        root=root,
        requests=(AttemptLocationRequest(node_id, run_id, attempt, hint),),
    )
    return updated, paths[0]


@dataclass(frozen=True, slots=True)
class AttemptLocationRequest:
    """一次纯定位请求；真实 NodeRun 在调用方的同一 SQLite 事务中建立。"""

    node_id: str
    run_id: str
    attempt: int
    hint: AttemptNamingHint | None = None


def allocate_attempt_paths(
    state: StorageLayoutState,
    *,
    root: Path,
    requests: Sequence[AttemptLocationRequest],
) -> tuple[StorageLayoutState, tuple[Path, ...]]:
    """批量复用同一分配算法，整个映射只严格重验一次，避免大图产生二次方模型校验。

    输入顺序就是调用方已确定的持久分配次序，不访问 Graph 或扫描目录。中途任何请求失败，
    原映射始终不变，也没有目录被创建；返回路径与请求精确同序。
    """

    nodes, runs = dict(state.nodes), dict(state.runs)
    node_number = max((item.number for item in nodes.values()), default=0)
    run_number = max(runs.values(), default=0)
    paths: list[Path] = []
    for request in requests:
        if type(request.attempt) is not int or not 1 <= request.attempt <= 999999:
            raise ValueError("E_STORAGE_LAYOUT_ATTEMPT: attempt 超出存储编号范围")
        if request.node_id not in nodes:
            naming = AttemptNamingHint.model_validate(request.hint or AttemptNamingHint())
            node_number += 1
            parent: str = naming.category
            if naming.category == "chapters":
                assert naming.chapter_index is not None and naming.chapter_name is not None
                chapter = readable_component(naming.chapter_name, max_units=12)
                parent += f"/{naming.chapter_index:04d}-{chapter}"
            label = readable_component(naming.task_name)
            nodes[request.node_id] = StorageNodeLocation(
                number=node_number, relative_dir=f"{parent}/{label}__N{node_number:03d}"
            )
        if request.run_id not in runs:
            run_number += 1
            runs[request.run_id] = run_number
        relative = PurePosixPath(nodes[request.node_id].relative_dir) / (
            f"R{runs[request.run_id]:03d}-A{request.attempt:03d}"
        )
        paths.append(safe_attempt_directory(root, root.joinpath(*relative.parts)))
    updated = StorageLayoutState(nodes=nodes, runs=runs)
    return updated, tuple(paths)
