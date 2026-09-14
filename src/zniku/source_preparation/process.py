"""提供可取消、有超时和内存上限的只读媒体子进程管道。

只接受代码构造的 argv；逐行消费长媒体输出，独立排空 stderr，取消时回收整个当前子进程。
这不是通用命令节点，也不接受操作者提供可执行文本。
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable, Sequence
from queue import Empty, Full, Queue
from time import monotonic

from zniku.media.probe import MediaNodeError
from zniku.runtime.process_window import background_creation_flags
from zniku.runtime.progress import ProgressReporter

from .progress import check_cancellation, sample

MAX_LINE = 256 * 1024
MAX_CAPTURE = 4 * 1024 * 1024


def fail(code: str, message: str) -> MediaNodeError:
    return MediaNodeError(f"E_SOURCE_PREPARATION_{code}", message)


def stream_process(
    argv: Sequence[str],
    *,
    consume: Callable[[bytes], None],
    progress: ProgressReporter | None = None,
    timeout: float = 43200,
    permit_media_errors: bool = False,
    merge_stderr: bool = False,
) -> bool:
    """流式执行固定工具并返回是否观察到 stderr；执行失败从不伪装完整 EOF。

    只有受控 bitstream header 日志解析可合流 stderr；此时所有日志同样受逐行预算约束，
    调用者自行解析，不能将其用于替代全片解码错误检查。
    """
    check_cancellation()
    try:
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
            shell=False,
            creationflags=background_creation_flags(),
        )
    except OSError as exc:
        raise fail("TOOL_UNAVAILABLE", "媒体检查工具无法启动") from exc
    assert process.stdout is not None
    queue: Queue[bytes | Exception | None] = Queue(maxsize=32)
    stop = threading.Event()
    errors = bytearray()

    def put(value: bytes | Exception | None) -> None:
        while not stop.is_set():
            try:
                queue.put(value, timeout=0.1)
                return
            except Full:
                pass

    def read_stdout() -> None:
        try:
            assert process.stdout is not None
            while line := process.stdout.readline(MAX_LINE + 1):
                if len(line) > MAX_LINE:
                    raise fail("OUTPUT_BUDGET", "媒体工具单行超过内存预算")
                put(line)
        except Exception as exc:
            put(exc)
        finally:
            put(None)

    def read_stderr() -> None:
        assert process.stderr is not None
        while chunk := process.stderr.read(4096):
            errors.extend(chunk)
            if len(errors) > 8192:
                del errors[:-8192]

    reader_functions = (read_stdout,) if merge_stderr else (read_stdout, read_stderr)
    readers = [threading.Thread(target=reader, daemon=True) for reader in reader_functions]
    for reader in readers:
        reader.start()
    started = heartbeat = monotonic()
    try:
        stdout_eof = False
        # stdout EOF 不等于进程退出；工具可能仍在排空、封装或等待。继续相同的取消/超时循环，
        # 不能在 EOF 后用长 wait 暂时失去停止能力或把挂起进程当作已完成。
        while not stdout_eof or process.poll() is None:
            now = monotonic()
            sample()
            if now - started > timeout:
                raise fail("TIMEOUT", "检查超过执行预算，未登记结果，可从头重试")
            if progress is not None and now - heartbeat >= 0.2:
                progress.report(0.0)
                heartbeat = now
            if stdout_eof:
                stop.wait(0.1)
                continue
            try:
                line = queue.get(timeout=0.1)
            except Empty:
                continue
            if line is None:
                stdout_eof = True
                continue
            if isinstance(line, Exception):
                raise line
            consume(line)
        if process.returncode != 0:
            raise fail("PROCESS_FAILED", "媒体工具非零退出，检查未完整完成")
        if not merge_stderr:
            readers[1].join(timeout=2)
        if errors and not permit_media_errors:
            raise fail("DECODE_ERRORS", "媒体工具报告错误，结果未通过")
        return bool(errors)
    finally:
        stop.set()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        for reader in readers:
            reader.join(timeout=2)
        process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()


def capture_process(
    argv: Sequence[str], *, progress: ProgressReporter | None = None, timeout: float = 60
) -> bytes:
    """只为头信息/工具版本捕获有限输出；全片帧/包必须使用流式接口。"""
    result = bytearray()

    def consume(line: bytes) -> None:
        if len(result) + len(line) > MAX_CAPTURE:
            raise fail("OUTPUT_BUDGET", "工具头信息超过有限预算")
        result.extend(line)

    stream_process(argv, consume=consume, progress=progress, timeout=timeout)
    return bytes(result)
