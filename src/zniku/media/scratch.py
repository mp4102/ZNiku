"""记录已生成内部中转的维护提示，不参与媒体验收、Artifact 或 reuse。

索引丢失、损坏或写入失败只会失去清理资格，不能阻断媒体处理。它不授权删除：维护端仍须
核对精确定义、固定文件职责、正式引用、绑定目录及当前文件身份。旧文件不会被按名字追认。
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Literal

from pydantic import Field

from zniku.graph import NodeDefinition
from zniku.project.models import ProjectModel
from zniku.runtime import PythonAdapterContext

INDEX_NAME = "scratch-index.json"
INDEX_LIMIT = 1024 * 1024
ENTRY_LIMIT = 4096
type ScratchRole = Literal["context_part", "timescale"]


class ScratchIdentity(ProjectModel):
    """同一本机文件的低成本身份与属性，不是内容证明。"""

    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int


class ScratchRecord(ProjectModel):
    relative_path: str
    role: ScratchRole
    identity: ScratchIdentity


class ScratchIndex(ProjectModel):
    contract_version: Literal["0.3.6"] = "0.3.6"
    node_run_id: str
    type_id: str
    definition_version: str
    declared_outputs: tuple[str, ...]
    entries: tuple[ScratchRecord, ...] = Field(max_length=ENTRY_LIMIT)


def exact_role(definition: NodeDefinition) -> str | None:
    """只识别完整内建精确定义；相似 type 名或修改过的 executor 不获得维护资格。"""

    from zniku.chapter_batch.definitions import definition_role as batch_role
    from zniku.chapter_batch.fused import definition_role as fused_role
    from zniku.chapter_overlap.definitions import definition_role as overlap_role
    from zniku.source_admission.definitions import definition_role as admitted_role
    from zniku.source_aligned.definitions import definition_role as aligned_role

    for recognize in (fused_role, batch_role, admitted_role, aligned_role, overlap_role):
        role = recognize(definition)
        if role is not None:
            return role
    return None


def safe_path(path: Path) -> Path:
    """拒绝路径链中链接/reparse，解析不允许改变绝对路径。"""

    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("scratch 必须使用无上跳绝对路径")
    for part in (*reversed(path.parents), path):
        value = part.lstat()
        if stat.S_ISLNK(value.st_mode) or getattr(value, "st_file_attributes", 0) & 0x400:
            raise ValueError("scratch 不接受链接或 reparse point")
    resolved = path.resolve(strict=True)
    if os.path.normcase(str(resolved)) != os.path.normcase(str(path)):
        raise ValueError("scratch 解析改变了定位")
    return resolved


def file_identity(path: Path) -> ScratchIdentity:
    """无可靠 file id 或共享硬链接就保留，不能以文件大小代替归属。"""

    safe_path(path)
    value = path.stat()
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1 or value.st_ino <= 0:
        raise ValueError("scratch 必须为具有可靠身份的独占普通文件")
    return ScratchIdentity(
        device=value.st_dev,
        inode=value.st_ino,
        size=value.st_size,
        mtime_ns=value.st_mtime_ns,
        ctime_ns=value.st_ctime_ns,
    )


def allowed_path(relative: str, role: ScratchRole, producer_role: str | None) -> bool:
    """有界闭合路径规则；只覆盖 context/crop，不提供插件自定义删除模板。"""

    path = Path(relative)
    if (
        path.is_absolute()
        or ".." in path.parts
        or len(path.parts) != 2
        or any(char in relative for char in ":\x00\r\n")
    ):
        return False
    parent, name = path.parts
    if role == "context_part":
        return (
            producer_role == "context"
            and parent == "context-parts"
            and re.fullmatch(r"part-[0-9]{4}\.mov", name) is not None
        )
    if producer_role not in {"context", "crop"}:
        return False
    if parent == "context-parts":
        return (
            producer_role == "context"
            and re.fullmatch(r"part-[0-9]{4}\.timescale\.mov", name) is not None
        )
    return parent == "outputs" and (
        name.endswith(".timescale.mov")
        or (
            producer_role == "context"
            and re.fullmatch(r".+\.input-[0-9]{4}\.mov", name) is not None
        )
    )


def read_index(work_dir: Path) -> ScratchIndex | None:
    """有界读取；任何无法确认的提示都退为未知，而不是猜测目录所有权。"""

    path = work_dir / INDEX_NAME
    try:
        if file_identity(path).size > INDEX_LIMIT:
            return None
        with path.open("rb") as stream:
            raw = stream.read(INDEX_LIMIT + 1)
        if len(raw) > INDEX_LIMIT:
            return None
        strict_json(raw)
        value = ScratchIndex.model_validate_json(raw)
        names = [item.relative_path.casefold() for item in value.entries]
        return value if len(names) == len(set(names)) else None
    except (OSError, ValueError):
        return None


def strict_json(raw: str | bytes) -> object:
    """维护提示和明确 JSON 引用都拒绝重复键与非有限常量，损坏时只能保留。"""

    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in values:
            if key in result:
                raise ValueError("维护 JSON 含重复字段")
            result[key] = value
        return result

    def constant(_value: str) -> object:
        raise ValueError("维护 JSON 含非法常量")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except RecursionError as error:
        raise ValueError("维护 JSON 嵌套超过解析预算") from error


def record_internal_scratch(context: PythonAdapterContext, path: Path, role: ScratchRole) -> bool:
    """媒体成功生成后尝试记录，失败保留文件且不改变执行结论；不认领既有未知文件。"""

    temporary: Path | None = None
    try:
        root = safe_path(context.work_dir)
        relative = path.relative_to(root).as_posix()
        if not allowed_path(relative, role, exact_role(context.definition)):
            return False
        identity = file_identity(path)
        outputs = tuple(str(item.path.absolute()) for item in context.outputs)
        if str(path) in outputs:
            return False
        target = root / INDEX_NAME
        existing = read_index(root)
        if target.exists() and existing is None:
            return False
        if existing is not None and (
            existing.node_run_id != context.node_run_id
            or existing.type_id != context.definition.type_id
            or existing.definition_version != context.definition.version
            or existing.declared_outputs != outputs
        ):
            return False
        entries = () if existing is None else existing.entries
        if any(item.relative_path.casefold() == relative.casefold() for item in entries):
            return False
        index = ScratchIndex(
            node_run_id=context.node_run_id,
            type_id=context.definition.type_id,
            definition_version=context.definition.version,
            declared_outputs=outputs,
            entries=(*entries, ScratchRecord(relative_path=relative, role=role, identity=identity)),
        )
        payload = index.model_dump_json().encode("utf-8")
        if len(payload) > INDEX_LIMIT:
            return False
        with tempfile.NamedTemporaryFile(dir=root, prefix=".scratch-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        if target.exists():
            file_identity(target)
        os.replace(temporary, target)
        temporary = None
        return True
    except (OSError, ValueError):
        return False
    finally:
        if temporary is not None:
            # 只删除本调用亲自创建的极小临时索引，永不删除媒体或已有索引。
            with suppress(OSError):
                temporary.unlink()
