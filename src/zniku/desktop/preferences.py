"""原子保存 launcher 本机偏好；损坏时安全空值，不读写 Project、媒体或 token。"""

from __future__ import annotations

import threading
from pathlib import Path
from tempfile import NamedTemporaryFile

from .contracts import DesktopPreferences


def write_local_document(path: Path, text: str) -> None:
    """在固定本机目录独占创建临时文件；只替换已声明目标，失败只清理自己创建的文件。"""

    parent = path.parent.resolve(strict=True)
    pending: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=".desktop-",
            suffix=".tmp",
            dir=parent,
            delete=False,
        ) as stream:
            pending = Path(stream.name)
            stream.write(text)
        pending.replace(path)
        pending = None
    finally:
        if pending is not None:
            # tempfile 是独占新建；不递归删除，也不清理同名旧文件或用户提供的目录。
            if pending.parent.resolve(strict=True) != parent:
                raise RuntimeError("本机临时文件不在指定偏好目录内")
            pending.unlink(missing_ok=True)


class DesktopPreferenceStore:
    """只管理固定文件；无任意文件读写接口，session 内串行替换。"""

    def __init__(self, data_root: Path | None) -> None:
        self.path = None if data_root is None else data_root.resolve() / "desktop-preferences.json"
        self._lock = threading.Lock()
        self._value = DesktopPreferences()
        if self.path is not None and self.path.is_file():
            try:
                if self.path.stat().st_size > 65_536:
                    raise ValueError("偏好文件超出大小上限")
                self._value = DesktopPreferences.model_validate_json(self.path.read_bytes())
            except (OSError, ValueError):
                self._value = DesktopPreferences()

    def read(self) -> DesktopPreferences:
        with self._lock:
            return self._value

    def save(self, value: DesktopPreferences) -> None:
        """磁盘原子替换成功后才更新内存；失败保留先前偏好。"""

        checked = DesktopPreferences.model_validate(value.model_dump(mode="python"), strict=True)
        with self._lock:
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                write_local_document(self.path, checked.model_dump_json())
            self._value = checked
