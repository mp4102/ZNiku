"""限制 OutputFile 的显式发布副作用，不提供递归目录创建或清理能力。

输出根、直属目录、既有目标和保护源在实际发布时重新检查；预览事实不是写入权限。普通可信本机
模型不承诺敌对并发下的路径原子性，但每次写入和覆盖前必须重验，任何失败都不删除用户内容。
"""

from __future__ import annotations

import stat
from collections.abc import Mapping
from pathlib import Path

from .probe import MediaNodeError


def _is_link(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return path.is_symlink() or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
    )


def checked_output_target(
    parameters: Mapping[str, object], source: Path, *, allow_create: bool
) -> Path:
    """在显式 copy 执行边界检查目标；仅授权时创建现有 root 下一个直属父目录。"""

    try:
        target_value = parameters.get("target_path")
        if not isinstance(target_value, str) or not target_value or "\x00" in target_value:
            raise MediaNodeError("E_MEDIA_OUTPUT_PATH_RELATIVE", "target_path 必须是绝对路径")
        raw = Path(target_value)
        if not raw.is_absolute() or ".." in raw.parts:
            raise MediaNodeError(
                "E_MEDIA_OUTPUT_PATH_RELATIVE", "target_path 必须是无上跳的绝对路径"
            )
        create_parent = parameters.get("create_parent", False)
        if not isinstance(create_parent, bool):
            raise MediaNodeError("E_MEDIA_OUTPUT_PARENT_INVALID", "create_parent 必须是 boolean")
        root_value = parameters.get("output_root")
        root: Path | None = None
        if root_value is not None:
            if not isinstance(root_value, str) or not Path(root_value).is_absolute():
                raise MediaNodeError("E_MEDIA_OUTPUT_PARENT_INVALID", "output_root 必须是绝对目录")
            root = Path(root_value).resolve(strict=True)
            if not root.is_dir() or root != Path(root_value):
                raise MediaNodeError("E_MEDIA_OUTPUT_PARENT_INVALID", "输出根已改变或不是普通目录")
        if create_parent and root is None:
            raise MediaNodeError(
                "E_MEDIA_OUTPUT_PARENT_INVALID", "创建父目录必须显式绑定 output_root"
            )
        if root is not None and raw.parent not in {root} and raw.parent.parent != root:
            raise MediaNodeError("E_MEDIA_OUTPUT_PARENT_INVALID", "目标越出输出根或不是直属子目录")
        if create_parent and raw.parent == root:
            raise MediaNodeError("E_MEDIA_OUTPUT_PARENT_INVALID", "创建权限只适用于直属子目录")
        if _is_link(raw.parent):
            raise MediaNodeError(
                "E_MEDIA_OUTPUT_PARENT_INVALID", "目标父目录不得是链接或 reparse point"
            )

        protected = parameters.get("protected_paths", ())
        if not isinstance(protected, list | tuple) or any(
            not isinstance(value, str) or not Path(value).is_absolute() for value in protected
        ):
            raise MediaNodeError("E_MEDIA_OUTPUT_SAME_PATH", "保护路径必须是绝对路径数组")
        protected_paths = (source, *(Path(str(value)).resolve(strict=True) for value in protected))
        target = raw.resolve(strict=False)
        if target.parent != raw.parent.resolve(strict=False):
            raise MediaNodeError("E_MEDIA_OUTPUT_PARENT_INVALID", "目标解析已越出父目录")
        if _is_link(raw):
            raise MediaNodeError("E_MEDIA_OUTPUT_TARGET_INVALID", "目标不得是链接或 reparse point")
        for item in protected_paths:
            if target == item or (target.exists() and target.samefile(item)):
                raise MediaNodeError(
                    "E_MEDIA_OUTPUT_SAME_PATH", "OutputFile 不得覆盖上游或保护源媒体"
                )
        if target.exists() and not target.is_file():
            raise MediaNodeError("E_MEDIA_OUTPUT_TARGET_INVALID", "既有目标必须是普通文件")

        if not raw.parent.exists():
            if not (create_parent and allow_create):
                raise MediaNodeError("E_MEDIA_OUTPUT_PARENT_INVALID", "目标父目录不存在")
            # 不使用 parents=True；根消失、同名文件或其他竞争均失败，不修复/覆盖/清理。
            raw.parent.mkdir(exist_ok=True)
        parent = raw.parent.resolve(strict=True)
        if not parent.is_dir() or _is_link(raw.parent) or parent != raw.parent:
            raise MediaNodeError("E_MEDIA_OUTPUT_PARENT_INVALID", "目标父目录已改变或不是普通目录")
        if root is not None and root.resolve(strict=True) != root:
            raise MediaNodeError("E_MEDIA_OUTPUT_PARENT_INVALID", "输出根在准备期间发生变化")
        return parent / raw.name
    except MediaNodeError:
        raise
    except (OSError, RuntimeError, ValueError) as error:
        raise MediaNodeError(
            "E_MEDIA_OUTPUT_PARENT_INVALID", "无法准备输出位置；请检查目录、权限与保护源。"
        ) from error


__all__ = ["checked_output_target"]
