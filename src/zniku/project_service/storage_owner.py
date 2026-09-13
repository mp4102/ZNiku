"""记录数据根的普通归属定位标记，防止缺盘恢复时选中另一个同形工程目录。

标记只重复 SQLite 已保存的随机 data_id，不列媒体、不证明内容，也不参与 Graph 或 Runtime 运行。
仅显式创建或复制维护能建立标记；恢复仅检查，不从目录反向收养未知工程或补造身份。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from zniku.project.models import ProjectModel
from zniku.project.storage import ProjectStorage

from .storage import _failure, _safe_path

OWNER_NAME = ".zniku-storage-owner.json"


class _StorageOwner(ProjectModel):
    contract_version: Literal["0.3.2"] = "0.3.2"
    data_id: str


def verify_storage_owner(storage: ProjectStorage, *, required: bool = True) -> None:
    """读取固定、有界、严格归属标记；缺失来源时没有此绑定就不能凭相似目录恢复。"""

    marker = Path(storage.data_root) / OWNER_NAME
    _safe_path(marker, missing=True)
    if storage.data_id is None:
        if required or marker.exists():
            raise _failure(
                "RESTORE_UNPROVEN", "旧工程没有可核对的数据归属绑定，请恢复原位置后显式迁移"
            )
        return
    if not marker.exists() and not required:
        return
    if not marker.is_file() or marker.stat().st_size > 2048:
        raise _failure("RESTORE_UNPROVEN", "所选目录缺少有效的数据归属标记，不能自动重新定位")
    try:
        with marker.open("rb") as stream:
            payload = stream.read(2049)
        if len(payload) > 2048:
            raise _failure("RESTORE_UNPROVEN", "数据归属标记超过固定大小限制")
        owner = _StorageOwner.model_validate_json(payload)
    except (OSError, ValidationError) as error:
        raise _failure(
            "RESTORE_UNPROVEN", "数据归属标记无法读取或字段无效，不能重新定位"
        ) from error
    if owner.data_id != storage.data_id:
        raise _failure(
            "RESTORE_MISMATCH", "所选数据目录属于其他工程数据绑定，请选择本工程的完整副本"
        )


def create_storage_owner(storage: ProjectStorage) -> None:
    """仅为已有随机定位身份建立排他小文件；不同或损坏的标记不会被覆盖。"""

    if storage.data_id is None:
        return
    marker = Path(storage.data_root) / OWNER_NAME
    _safe_path(marker, missing=True)
    if marker.exists():
        verify_storage_owner(storage)
        return
    try:
        with marker.open("x", encoding="utf-8") as stream:
            stream.write(_StorageOwner(data_id=storage.data_id).model_dump_json())
    except OSError as error:
        raise _failure("OWNER_WRITE", "数据归属标记未建立；不会覆盖原目录或切换工程定位") from error
