"""以有界管道读取新准入的 FFprobe 输出，取消或失败时终止本次子进程。

不执行 shell、不改写媒体，不将完整输出或无限 stderr 积累在内存。回调异常原样传播，
使 Runtime 的取消与基础设施失败保持其原有语义。
"""

from __future__ import annotations

import subprocess
import threading
from collections import deque
from collections.abc import Callable, Generator, Sequence
from queue import Empty, Full, Queue
from time import monotonic
from typing import BinaryIO

from zniku.avenhance_v27.probe import Av27MediaError
from zniku.runtime.process_window import background_creation_flags

_LINE_LIMIT = 64 * 1024


def check_cancel(cancel_event: threading.Event | None) -> None:
    """响应调用方取消；不把取消转成已通过或候选文件存在。"""
    if cancel_event is not None and cancel_event.is_set():
        raise Av27MediaError("E_SOURCE_ADMISSION_CANCELLED", "素材检查已取消，源文件未改动")


def _pump(stream: BinaryIO, queue: Queue[bytes | Exception | None], stop: threading.Event) -> None:
    def put(value: bytes | Exception | None) -> None:
        while not stop.is_set():
            try:
                queue.put(value, timeout=0.1)
                return
            except Full:
                pass

    try:
        while not stop.is_set():
            line = stream.readline(_LINE_LIMIT + 1)
            if not line:
                break
            if len(line) > _LINE_LIMIT:
                raise ValueError("FFprobe 单行超过 64 KiB")
            put(line)
    except Exception as error:
        put(error)
    finally:
        put(None)


def probe_lines(
    argv: Sequence[str],
    *,
    cancel_event: threading.Event | None = None,
    heartbeat: Callable[[], None] | None = None,
    timeout: float = 3600,
) -> Generator[str, None, None]:
    """流式消费工具输出；有限队列、行长、错误尾部及总时长共同限定成本。

    这里只把 FFprobe 非零退出判为工具失败；与 AV2.7 一致，不把退出为零的 stderr
        自动升级为全片解码证明失败。媒体合同由调用方检查已读字段。
    """
    check_cancel(cancel_event)
    try:
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            creationflags=background_creation_flags(),
        )
    except OSError as error:
        raise Av27MediaError("E_AV27_PROBE_FAILED", str(error)) from error
    assert process.stdout is not None and process.stderr is not None
    queue: Queue[bytes | Exception | None] = Queue(maxsize=64)
    stop = threading.Event()
    errors: deque[bytes] = deque(maxlen=2)

    def drain(stream: BinaryIO) -> None:
        while chunk := stream.read(4096):
            errors.append(chunk)

    output_reader = threading.Thread(target=_pump, args=(process.stdout, queue, stop), daemon=True)
    error_reader = threading.Thread(target=drain, args=(process.stderr,), daemon=True)
    output_reader.start()
    error_reader.start()
    deadline, next_heartbeat = monotonic() + timeout, 0.0
    try:
        while True:
            check_cancel(cancel_event)
            now = monotonic()
            if now >= deadline:
                raise Av27MediaError("E_AV27_PROBE_FAILED", "FFprobe 检查超过本次时间预算")
            if heartbeat is not None and now >= next_heartbeat:
                heartbeat()
                next_heartbeat = now + 0.25
            try:
                line = queue.get(timeout=0.1)
            except Empty:
                continue
            if line is None:
                break
            if isinstance(line, Exception):
                raise Av27MediaError("E_AV27_PROBE_INVALID", str(line)) from line
            try:
                yield line.decode("utf-8", errors="strict")
            except UnicodeDecodeError as error:
                raise Av27MediaError("E_AV27_PROBE_INVALID", "FFprobe 输出不是 UTF-8") from error
        return_code = process.wait(timeout=5)
        error_reader.join(timeout=2)
        if return_code != 0:
            message = b"".join(errors).decode("utf-8", errors="replace")[-1000:]
            raise Av27MediaError(
                "E_AV27_PROBE_FAILED", message.strip() or f"FFprobe 退出码 {return_code}"
            )
        check_cancel(cancel_event)
    except subprocess.TimeoutExpired as error:
        raise Av27MediaError("E_AV27_PROBE_FAILED", "FFprobe 未在读取完成后退出") from error
    finally:
        stop.set()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        output_reader.join(timeout=2)
        error_reader.join(timeout=2)
        process.stdout.close()
        process.stderr.close()
