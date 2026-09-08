"""在保存工程数据定位前准备受控目录；失败不修改配置，也不回退其他磁盘。

此处只创建指定的新数据根与 attempts 子目录，不删除用户目录。CAS 或建项随后失败时，
本次创建的空目录也保留，避免把另一进程刚写入的用户资产误删。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from zniku.project import ProjectStoreError
from zniku.project.storage import ProjectStorage

from .storage import _safe_path


def existing_storage_root(storage: ProjectStorage) -> Path:
    """重开已配置工程时不重建遗失数据目录，也不跟随后来替换的联接。"""

    root = _safe_path(Path(storage.data_root))
    attempts = _safe_path(Path(storage.attempts_root))
    if not root.is_dir() or not attempts.is_dir() or not attempts.is_relative_to(root):
        raise ProjectStoreError(
            "E_PROJECT_STORAGE_MISSING", "工程数据目录不可用，请先恢复原磁盘位置"
        )
    return attempts


def prepare_storage_location(storage: ProjectStorage, *, current: ProjectStorage | None) -> None:
    """显式保存时检查父目录、链接与可写性；不覆盖另一个已有数据根。"""

    root = Path(storage.data_root)
    same_root = current is not None and Path(current.data_root) == root
    _safe_path(root.parent)
    _safe_path(root, missing=True)
    if root.exists() and not same_root:
        raise ProjectStoreError(
            "E_PROJECT_STORAGE_TARGET_EXISTS", "数据目录已存在，请选择新的父目录；不会覆盖已有资产"
        )
    try:
        root.mkdir(exist_ok=same_root)
        attempts = Path(storage.attempts_root)
        _safe_path(attempts, missing=True)
        attempts.mkdir(exist_ok=same_root)
        # 排他临时写入只验证当前目录可写，不枚举、探测或删除媒体。
        with tempfile.TemporaryFile(dir=attempts) as stream:
            stream.write(b"ZNIKU storage check")
            stream.flush()
    except OSError as error:
        raise ProjectStoreError(
            "E_PROJECT_STORAGE_UNWRITABLE", "无法准备工程数据目录；请检查磁盘、权限和可用空间"
        ) from error
