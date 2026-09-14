"""将可测的阶段进度写入当前 attempt 普通日志，不增加 Runtime 状态或伪百分比。"""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

MARKER = "ZNIKU_SOURCE_PROGRESS "
_LOG: ContextVar[Path | None] = ContextVar("source_progress_log", default=None)
_CANCEL: ContextVar[threading.Event | None] = ContextVar("source_preparation_cancel", default=None)


@contextmanager
def cancellation_scope(event: threading.Event) -> Iterator[None]:
    """仅绑定当前业务 worker 的协作取消，不接管进程、不创建 Runtime 状态。"""
    token = _CANCEL.set(event)
    try:
        check_cancellation()
        yield
    finally:
        _CANCEL.reset(token)


def check_cancellation() -> None:
    """取消只使当前节点失败；现有 Artifact/原件保留，重试必须新 attempt。"""
    from .process import fail

    event = _CANCEL.get()
    if event is not None and event.is_set():
        raise fail("CANCELLED", "用户停止检查/验证；原件和已有产物保留；重试创建新 attempt")


@dataclass
class Stage:
    name: str
    unit: str | None
    started: float
    last: float = 0
    current: int | None = None
    total: int | None = None


_STAGE: ContextVar[Stage | None] = ContextVar("source_progress_stage", default=None)


@contextmanager
def progress_log(path: Path) -> Iterator[None]:
    """在当前调用域指定普通 attempt 日志，不创建独立进度状态权威。"""
    token = _LOG.set(path)
    try:
        yield
    finally:
        _LOG.reset(token)


@contextmanager
def stage(name: str, unit: str | None = None) -> Iterator[None]:
    """每阶段从实际起点测时；异常退出不能被最终进度采样覆盖。"""
    token = _STAGE.set(Stage(name, unit, monotonic()))
    try:
        sample(force=True)
        yield
    finally:
        try:
            if sys.exc_info()[0] is None:
                sample(force=True)
        finally:
            _STAGE.reset(token)


def sample(current: int | None = None, total: int | None = None, *, force: bool = False) -> None:
    """未知分母保持 null；测量计数不变时只更新时间，不猜测外部进展。"""
    check_cancellation()
    path, state = _LOG.get(), _STAGE.get()
    if path is None or state is None:
        return
    now = monotonic()
    if current is not None:
        state.current, state.total = current, total
    if not force and now - state.last < 0.5:
        return
    elapsed = now - state.started
    data = {
        "stage": state.name,
        "current": state.current,
        "total": state.total,
        "unit": state.unit,
        "elapsed_seconds": elapsed,
        "rate_per_second": None
        if state.current is None or not elapsed
        else state.current / elapsed,
    }
    with path.open("a", encoding="utf-8") as stream:
        stream.write(MARKER + json.dumps(data, ensure_ascii=False, allow_nan=False) + "\n")
    state.last = now
