"""仅供进程隔离测试启动的固定合成 helper，不导入 Tk、不创建窗口、不执行媒体操作。

测试通过 Python monkeypatch 替换父进程的固定命令入口；生产 HTTP 协议没有模式、路径或
可执行文件开关。本脚本只接受封闭测试场景，等待标记也只由 pytest 在自身临时目录创建。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def main(arguments: list[str]) -> int:
    """读取真实父协议后按固定场景退出，使测试保留真实子进程/管道/HTTP 边界。"""
    if not arguments or arguments[0] not in {
        "cancel",
        "nonzero",
        "bad_json",
        "extra_field",
        "wrong_version",
        "wait",
    }:
        return 64
    mode = arguments[0]
    if len(arguments) != (3 if mode == "wait" else 1):
        return 64
    request = json.loads(sys.stdin.buffer.read())
    if set(request) != {"version", "capability", "args"} or request["version"] != 1:
        return 65
    if mode == "nonzero":
        # 只终止本测试 helper，模拟 native crash 的非零退出边界，不伤及父服务进程。
        os._exit(87)
    if mode == "bad_json":
        print("not valid JSON", flush=True)
        return 0
    if mode == "wait":
        ready, release = Path(arguments[1]), Path(arguments[2])
        ready.touch()
        deadline = time.monotonic() + 5
        while not release.exists():
            if time.monotonic() >= deadline:
                return 66
            time.sleep(0.01)
    response: dict[str, object] = {"version": 1, "paths": []}
    if mode == "extra_field":
        response["unknown"] = True
    elif mode == "wrong_version":
        response["version"] = 2
    print(json.dumps(response), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
