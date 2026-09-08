"""用 OS 文件锁协调同一用户的桌面单实例，磁盘只保存非敏感发现信息。

不持久化 HostBridge token，不探测/结束 PID，不凭旧文件判断服务存活。二次启动只有在
锁被占用且 loopback health 返回精确 instance_id 时才打开现有页面。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import BinaryIO
from urllib.error import URLError
from urllib.request import urlopen

from pydantic import BaseModel, ConfigDict

from zniku.project_service.host_bridge import HostRandomId, validate_studio_origin

from .preferences import write_local_document


class InstanceRecord(BaseModel):
    """只用于本机服务发现，不提供系统动作或运行授权。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    instance_id: HostRandomId
    origin: str


def _lock(stream: BinaryIO, *, unlock: bool = False) -> None:
    stream.seek(0)
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN if unlock else fcntl.LOCK_EX | fcntl.LOCK_NB)


class InstanceLock:
    """持有独占锁；旧文件可以覆盖，只有当前锁所有者可更改发现记录。"""

    def __init__(self, data_root: Path) -> None:
        self.root = data_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.record_path = self.root / "desktop-instance.json"
        self._stream: BinaryIO | None = None

    def acquire(self) -> bool:
        stream = (self.root / "desktop-instance.lock").open("a+b")
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"\0")
            stream.flush()
        try:
            _lock(stream)
        except OSError:
            stream.close()
            return False
        self._stream = stream
        return True

    def publish(self, record: InstanceRecord) -> None:
        """在已经占有随机端口并通过健康检查后原子发布不含 token 的发现数据。"""

        if self._stream is None:
            raise RuntimeError("非锁所有者不能发布实例")
        validate_studio_origin(record.origin)
        write_local_document(self.record_path, record.model_dump_json())

    def existing(self, *, timeout: float = 10.0) -> InstanceRecord:
        """等待另一次双击启动完成；陈旧/不匹配/不可达记录绝不用于启动或误杀。"""

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                record = InstanceRecord.model_validate_json(self.record_path.read_bytes())
                validate_studio_origin(record.origin)
                if check_health(record):
                    return record
            except (OSError, ValueError):
                pass
            time.sleep(0.1)
        raise RuntimeError("已有 Studio 正在启动或无法连接；未启动第二个实例，也未结束任何进程。")

    def close(self) -> None:
        """只释放持有的 OS 锁；不删除本机配置、Project 或 attempt 目录。"""

        if self._stream is not None:
            _lock(self._stream, unlock=True)
            self._stream.close()
            self._stream = None


def check_health(record: InstanceRecord) -> bool:
    """只信任 exact Origin 和启动实例的健康响应，不把端口占用视为准备完成。"""

    validate_studio_origin(record.origin)
    try:
        with urlopen(record.origin + "/api/desktop/health", timeout=1) as response:
            payload = json.loads(response.read(4097))
            return bool(
                payload
                == {
                    "contract_version": "0.3.0",
                    "instance_id": record.instance_id,
                    "status": "ready",
                }
            )
    except (OSError, URLError, ValueError):
        return False
