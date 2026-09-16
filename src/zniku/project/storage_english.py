"""声明英文任务目录及逐节点处理轮次；不改变 Graph 身份、重试或复用语义。

命名只在首次绑定时由可信宿主提供。轮次只在真正开始处理前分配，数据库事务由调用方负责；
本模块不扫描文件系统分配编号，不创建目录，也不把已有媒体复制为展示副本。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Annotated
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from .models import ProjectModel
from .paths import validate_filename_component
from .storage_layout import AttemptLocationRequest, AttemptNamingHint, StorageNumber

_TASK = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
_CHAPTER = re.compile(r"[A-Z][A-Z0-9-]{0,23}\Z|chapter-[0-9]{3,6}\Z")
_ROUND = re.compile(r"round-[0-9]{3,6}\Z")


def english_attempt_parts(parts: Sequence[str]) -> bool:
    """识别新版目录形状；身份及精确位置仍必须另外与工程持久映射核对。"""

    return (
        len(parts) in {2, 3}
        and _TASK.fullmatch(parts[0]) is not None
        and (len(parts) == 2 or _CHAPTER.fullmatch(parts[1]) is not None)
        and _ROUND.fullmatch(parts[-1]) is not None
        and int(parts[-1].removeprefix("round-")) > 0
    )


def _task_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:40].rstrip("-")
    if not slug or not slug[0].isalpha():
        slug = "task"
    try:
        validate_filename_component(slug, max_units=40)
    except ValueError:
        slug = "task"
    return slug


class EnglishStorageNode(ProjectModel):
    """稳定节点首次分配的英文任务位置；UI 改名不会移动已分配目录。"""

    relative_dir: Annotated[str, StringConstraints(min_length=1, max_length=72)]

    @model_validator(mode="after")
    def validate_directory(self) -> EnglishStorageNode:
        parts = PurePosixPath(self.relative_dir).parts
        if self.relative_dir != "/".join(parts) or not english_attempt_parts((*parts, "round-001")):
            raise ValueError("E_STORAGE_ENGLISH_DIRECTORY: 英文任务目录格式无效")
        for part in parts:
            validate_filename_component(part, max_units=48)
        return self


class EnglishStorageAttempt(ProjectModel):
    """一次实际处理的轮次；node_run_id 为映射键，不出现在实体目录名中。"""

    node_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    round: StorageNumber


class EnglishStorageState(ProjectModel):
    """只保存普通存储绑定；保留全部历史轮次，不回收删除节点或失败任务的编号。"""

    nodes: dict[str, EnglishStorageNode] = Field(default_factory=dict)
    attempts: dict[str, EnglishStorageAttempt] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_bindings(self) -> EnglishStorageState:
        paths = [item.relative_dir.casefold() for item in self.nodes.values()]
        unique_paths = set(paths)
        if (
            any(not key or len(key) > 128 for key in self.nodes)
            or len(paths) != len(unique_paths)
            or any("/" in path and path.split("/", 1)[0] in unique_paths for path in paths)
        ):
            raise ValueError("E_STORAGE_ENGLISH_UNIQUE: 节点目录必须唯一且不得嵌套")
        identities: set[tuple[str, int]] = set()
        for key, binding in self.attempts.items():
            parsed = UUID(key)
            if parsed.version != 4 or str(parsed) != key or binding.node_id not in self.nodes:
                raise ValueError("E_STORAGE_ENGLISH_BINDING: 处理身份或节点绑定无效")
            identity = (binding.node_id, binding.round)
            if identity in identities:
                raise ValueError("E_STORAGE_ENGLISH_UNIQUE: 同节点处理轮次不得重复")
            identities.add(identity)
        return self


def reserve_english_nodes(
    state: EnglishStorageState, requests: Sequence[AttemptLocationRequest]
) -> EnglishStorageState:
    """在 NodeRun 建立事务中固定英文名称，但不为排队或复用任务占用处理轮次。"""

    nodes = dict(state.nodes)
    used = {item.relative_dir.casefold() for item in nodes.values()}
    parents = {path.split("/", 1)[0] for path in used if "/" in path}
    for request in requests:
        if request.node_id in nodes:
            continue
        hint = AttemptNamingHint.model_validate(request.hint or AttemptNamingHint())
        task = _task_slug(hint.task_name)
        chapter = ""
        if hint.category == "chapters":
            assert hint.chapter_name is not None and hint.chapter_index is not None
            chapter = hint.chapter_name.upper()
            if _CHAPTER.fullmatch(chapter) is None:
                chapter = f"chapter-{hint.chapter_index:03d}"
            try:
                validate_filename_component(chapter, max_units=24)
            except ValueError:
                chapter = f"chapter-{hint.chapter_index:03d}"
        suffix = 1
        while True:
            name = task if suffix == 1 else f"{task}-{suffix}"
            candidate = name if not chapter else f"{name}/{chapter}"
            normalized = candidate.casefold()
            if (
                normalized not in used
                and normalized not in parents
                and ("/" not in normalized or normalized.split("/", 1)[0] not in used)
            ):
                break
            suffix += 1
        nodes[request.node_id] = EnglishStorageNode(relative_dir=candidate)
        used.add(normalized)
        if "/" in normalized:
            parents.add(normalized.split("/", 1)[0])
    return EnglishStorageState(nodes=nodes, attempts=state.attempts)


def allocate_english_attempt(
    state: EnglishStorageState, *, root: Path, node_id: str, node_run_id: str
) -> tuple[EnglishStorageState, Path]:
    """幂等分配同节点跨 Run 连续轮次；调用方必须与 NodeRun 路径同事务提交。"""

    from .storage_layout import safe_attempt_directory

    if node_id not in state.nodes:
        raise ValueError("E_STORAGE_ENGLISH_BINDING: 缺少已保存的节点目录")
    attempts = dict(state.attempts)
    binding = attempts.get(node_run_id)
    if binding is not None and binding.node_id != node_id:
        raise ValueError("E_STORAGE_ENGLISH_BINDING: 处理身份已绑定其他节点")
    if binding is None:
        number = max(
            (item.round for item in attempts.values() if item.node_id == node_id), default=0
        )
        binding = EnglishStorageAttempt(node_id=node_id, round=number + 1)
        attempts[node_run_id] = binding
    updated = EnglishStorageState(nodes=state.nodes, attempts=attempts)
    relative = PurePosixPath(state.nodes[node_id].relative_dir) / f"round-{binding.round:03d}"
    return updated, safe_attempt_directory(root, root.joinpath(*relative.parts))
