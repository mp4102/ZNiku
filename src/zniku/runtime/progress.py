"""实现 automatic attempt 的可信、限频且线程安全的进度上报边界。

``ProgressReporter`` 只接受绑定 attempt 的测量值；它不向 adapter 暴露 Repository，也不把
进程内投影升级为恢复权威。非法 sample 会在发布投影或持久化前失败关闭，持久化基础设施错误则
保留原始原因交还 Runtime 收敛，避免被误报为普通 adapter 失败。
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Literal, Protocol

from .models import utc_now

type ProgressUnit = Literal["frames", "bytes", "microseconds", "items"]
type ProgressTargetValidator = Callable[[str, str, int], None]
type ProgressPersister = Callable[[str, str, int, float], None]
type ProgressPublisher = Callable[["ProgressSample"], None]
type ProgressRemover = Callable[[str], None]
type WallClock = Callable[[], datetime]
type MonotonicClock = Callable[[], float]

_UNITS: frozenset[str] = frozenset({"frames", "bytes", "microseconds", "items"})
_RATIO_TOLERANCE = 1e-9
_PERSIST_INTERVAL_SECONDS = 0.4
_PERSIST_FRACTION_DELTA = 0.01
_PERSIST_FRACTION_TOLERANCE = 1e-12


class ProgressError(RuntimeError):
    """表示 sample 或 reporter 生命周期违反稳定 ``E_PROGRESS_*`` 合同。"""

    def __init__(self, code: str, message: str) -> None:
        if not code.startswith("E_PROGRESS_"):
            raise ValueError("ProgressError code 必须使用 E_PROGRESS_* 前缀")
        self.code = code
        super().__init__(f"{code}: {message}")


class ProgressInfrastructureError(RuntimeError):
    """隔离 reporter 回调的基础设施失败，供 Runtime 恢复原始 Repository 语义。"""

    def __init__(self, cause: Exception) -> None:
        self.cause = cause
        super().__init__(str(cause) or type(cause).__name__)


@dataclass(frozen=True, slots=True)
class ProgressSample:
    """一个已通过合同校验、绑定到唯一 automatic attempt 的进程内 sample。"""

    run_id: str
    node_run_id: str
    attempt: int
    fraction: float
    current: int | None
    total: int | None
    unit: ProgressUnit | None
    observed_at: datetime


class ProgressReporter(Protocol):
    """仅向 Python adapter 暴露的只写 progress 接口。"""

    def report(
        self,
        fraction: float,
        *,
        current: int | None = None,
        total: int | None = None,
        unit: ProgressUnit | None = None,
    ) -> None:
        """上报一个 determinate 测量值。"""


class BoundProgressReporter:
    """验证、投影并限频持久化一个绑定 attempt 的进度。

    ``validate_target`` 在每次 sample 发布前确认绑定仍是最新 ``running`` attempt；``persist``
    只接收已验证 fraction。二者均由 Runtime 注入，因此 adapter 只能调用 :meth:`report`。
    ``close`` 与 ``report`` 共用一把锁，使终态收敛与迟到 callback 严格串行。
    """

    def __init__(
        self,
        run_id: str,
        node_run_id: str,
        attempt: int,
        *,
        validate_target: ProgressTargetValidator,
        persist: ProgressPersister,
        publish: ProgressPublisher,
        remove: ProgressRemover,
        wall_clock: WallClock = utc_now,
        monotonic_clock: MonotonicClock = monotonic,
    ) -> None:
        if (
            type(run_id) is not str
            or not run_id
            or type(node_run_id) is not str
            or not node_run_id
            or type(attempt) is not int
            or attempt < 1
        ):
            raise ValueError("ProgressReporter 必须绑定有效 run_id/node_run_id/attempt")
        self._run_id = run_id
        self._node_run_id = node_run_id
        self._attempt = attempt
        self._validate_target = validate_target
        self._persist = persist
        self._publish = publish
        self._remove = remove
        self._wall_clock = wall_clock
        self._monotonic_clock = monotonic_clock
        self._lock = threading.RLock()
        self._terminal = False
        self._last_sample: ProgressSample | None = None
        self._last_persisted_fraction: float | None = None
        self._last_persisted_at: float | None = None

    @property
    def run_id(self) -> str:
        """返回 reporter 的固定 Run identity。"""

        return self._run_id

    @property
    def node_run_id(self) -> str:
        """返回 reporter 的固定 NodeRun identity。"""

        return self._node_run_id

    @property
    def attempt(self) -> int:
        """返回 reporter 的固定 attempt number。"""

        return self._attempt

    @property
    def last_sample(self) -> ProgressSample | None:
        """返回最后一个可信 sample；值对象不可变，可安全跨线程读取。"""

        with self._lock:
            return self._last_sample

    @property
    def last_persisted_fraction(self) -> float | None:
        """返回最后成功交给持久层的 fraction。"""

        with self._lock:
            return self._last_persisted_fraction

    @property
    def terminal(self) -> bool:
        """报告 reporter 是否已被终态边界关闭。"""

        with self._lock:
            return self._terminal

    def report(
        self,
        fraction: float,
        *,
        current: int | None = None,
        total: int | None = None,
        unit: ProgressUnit | None = None,
    ) -> None:
        """接受一个可信测量值，并按冻结的时间/增量门槛持久化。

        合同校验与 target 校验都发生在任何投影、SQLite 更新之前。合法但被限频的 sample 仍会
        更新当前进程投影；相同 fraction 允许刷新 ``current/total`` 观测，但不会重复写 SQLite。
        """

        with self._lock:
            if self._terminal:
                raise ProgressError(
                    "E_PROGRESS_TERMINAL",
                    "attempt 已进入终态边界，迟到 callback 不得改变任何状态",
                )
            normalized = self._validate_measurement(fraction, current, total, unit)
            if self._last_sample is not None and normalized[0] < self._last_sample.fraction:
                raise ProgressError("E_PROGRESS_REGRESSION", "同一 attempt 的 fraction 不得回退")

            self._call_target_validator()
            observed_at = self._read_wall_clock()
            observed_monotonic = self._read_monotonic_clock()
            sample = ProgressSample(
                run_id=self._run_id,
                node_run_id=self._node_run_id,
                attempt=self._attempt,
                fraction=normalized[0],
                current=normalized[1],
                total=normalized[2],
                unit=normalized[3],
                observed_at=observed_at,
            )
            should_persist = self._should_persist(sample.fraction, observed_monotonic)

            # 先发布进程投影，再提交 SQLite；并发 detail 因此不会看到低于已持久值的旧投影。
            self._last_sample = sample
            self._call_publisher(sample)
            if should_persist:
                self._call_persister(sample.fraction)
                self._last_persisted_fraction = sample.fraction
                self._last_persisted_at = observed_monotonic

    def close(self) -> ProgressSample | None:
        """原子关闭 reporter、移除细粒度投影并返回最后可信 sample。

        调用方必须把返回 fraction 与 failed 终态放入同一 Repository 事务；成功终态仍由
        ``register_result`` 原子写入精确 ``1.0``。重复关闭幂等，但关闭后的 ``report`` 始终失败。
        """

        with self._lock:
            if not self._terminal:
                self._terminal = True
                self._call_remover()
            return self._last_sample

    def _should_persist(self, fraction: float, observed_at: float) -> bool:
        if self._last_persisted_fraction is None or self._last_persisted_at is None:
            return True
        if fraction <= self._last_persisted_fraction:
            return False
        elapsed = observed_at - self._last_persisted_at
        delta = fraction - self._last_persisted_fraction
        return elapsed >= _PERSIST_INTERVAL_SECONDS or (
            delta >= _PERSIST_FRACTION_DELTA
            or math.isclose(
                delta,
                _PERSIST_FRACTION_DELTA,
                rel_tol=0.0,
                abs_tol=_PERSIST_FRACTION_TOLERANCE,
            )
        )

    def _call_target_validator(self) -> None:
        try:
            self._validate_target(self._run_id, self._node_run_id, self._attempt)
        except (ProgressError, ProgressInfrastructureError):
            raise
        except Exception as error:
            raise ProgressInfrastructureError(error) from error

    def _call_persister(self, fraction: float) -> None:
        try:
            self._persist(self._run_id, self._node_run_id, self._attempt, fraction)
        except (ProgressError, ProgressInfrastructureError):
            raise
        except Exception as error:
            raise ProgressInfrastructureError(error) from error

    def _call_publisher(self, sample: ProgressSample) -> None:
        try:
            self._publish(sample)
        except (ProgressError, ProgressInfrastructureError):
            raise
        except Exception as error:
            raise ProgressInfrastructureError(error) from error

    def _call_remover(self) -> None:
        try:
            self._remove(self._node_run_id)
        except (ProgressError, ProgressInfrastructureError):
            raise
        except Exception as error:
            raise ProgressInfrastructureError(error) from error

    def _read_wall_clock(self) -> datetime:
        try:
            value = self._wall_clock()
        except Exception as error:
            raise ProgressInfrastructureError(error) from error
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ProgressError("E_PROGRESS_CLOCK_INVALID", "wall clock 必须返回 aware datetime")
        return value.astimezone(UTC)

    def _read_monotonic_clock(self) -> float:
        try:
            value = self._monotonic_clock()
        except Exception as error:
            raise ProgressInfrastructureError(error) from error
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ProgressError("E_PROGRESS_CLOCK_INVALID", "monotonic clock 必须返回有限数值")
        normalized = float(value)
        if not math.isfinite(normalized):
            raise ProgressError("E_PROGRESS_CLOCK_INVALID", "monotonic clock 必须返回有限数值")
        if self._last_persisted_at is not None and normalized < self._last_persisted_at:
            raise ProgressError("E_PROGRESS_CLOCK_REGRESSION", "monotonic clock 不得回退")
        return normalized

    @staticmethod
    def _validate_measurement(
        fraction: object,
        current: object,
        total: object,
        unit: object,
    ) -> tuple[float, int | None, int | None, ProgressUnit | None]:
        if type(fraction) is not float:
            raise ProgressError("E_PROGRESS_FRACTION_TYPE", "fraction 必须是 float")
        if not math.isfinite(fraction):
            raise ProgressError("E_PROGRESS_FRACTION_NONFINITE", "fraction 必须是有限数")
        if not 0.0 <= fraction <= 1.0:
            raise ProgressError("E_PROGRESS_FRACTION_RANGE", "fraction 必须位于 0.0..1.0")

        values = (current, total, unit)
        if any(value is None for value in values):
            if any(value is not None for value in values):
                raise ProgressError(
                    "E_PROGRESS_MEASUREMENT_PARTIAL",
                    "current/total/unit 必须全部出现或全部为 null",
                )
            return fraction, None, None, None

        if isinstance(current, bool) or not isinstance(current, int):
            raise ProgressError("E_PROGRESS_CURRENT_TYPE", "current 必须是 integer")
        if isinstance(total, bool) or not isinstance(total, int):
            raise ProgressError("E_PROGRESS_TOTAL_TYPE", "total 必须是 integer")
        if current < 0:
            raise ProgressError("E_PROGRESS_CURRENT_RANGE", "current 不得小于 0")
        if total <= 0:
            raise ProgressError("E_PROGRESS_TOTAL_RANGE", "total 必须大于 0")
        if current > total:
            raise ProgressError("E_PROGRESS_CURRENT_RANGE", "current 不得大于 total")
        if not isinstance(unit, str) or unit not in _UNITS:
            raise ProgressError("E_PROGRESS_UNIT_INVALID", "unit 不属于冻结单位集合")
        if abs(fraction - current / total) > _RATIO_TOLERANCE:
            raise ProgressError(
                "E_PROGRESS_FRACTION_MISMATCH",
                "fraction 与 current/total 超出 absolute tolerance 1e-9",
            )
        return fraction, current, total, unit  # type: ignore[return-value]


__all__ = [
    "ProgressError",
    "ProgressInfrastructureError",
    "ProgressReporter",
    "ProgressSample",
    "ProgressUnit",
]
