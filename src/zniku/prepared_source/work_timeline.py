"""普通外部来件的展示帧时间轴核对；粗时间基但严格对齐的文件不是坏片。

复用有界、可取消进程读取，不执行旧源审计，不扫描上游输入，不持久保存逐帧账本。
"""

from fractions import Fraction
from pathlib import Path

from zniku.avenhance_v27.probe import probe_header
from zniku.runtime.progress import ProgressReporter
from zniku.source_preparation.inspection import _scan
from zniku.source_preparation.progress import sample, stage
from zniku.source_preparation.work_models import clock_tolerance

from .node_contracts import fail


def probe_work_cfr(
    path: Path, *, count: int, rate: Fraction, progress: ProgressReporter | None = None
) -> None:
    """核对当前新来件 N、帧序和相对 i/FPS；不以容器 time_base 单独拒绝它。"""
    header = probe_header(path)
    tb = header.video.time_base
    tolerance = clock_tolerance(rate, tb)
    first: Fraction | None = None
    previous: Fraction | None = None
    measured = 0

    def consume(row: dict[str, str]) -> None:
        nonlocal first, previous, measured
        if not row:
            return
        try:
            stamp = int(row["pts"])
        except (KeyError, ValueError) as error:
            raise ValueError("解码帧缺少真实整数 PTS") from error
        if row.get("best_effort_timestamp") != row["pts"]:
            fail("WORK_TIMELINE", "来件需要时间戳重建，不能隐式代替真实帧时间")
        actual = stamp * tb
        first = actual if first is None else first
        if measured >= count or (previous is not None and actual <= previous):
            fail("WORK_TIMELINE", "实际展示帧数量、重复或逆序不符合交接要求")
        if abs(actual - first - measured / rate) > tolerance:
            fail("WORK_TIMELINE", "来件相对帧时间不符合所选工作 FPS；不自动改速或补删帧")
        previous = actual
        measured += 1
        sample(measured, count)

    with stage("work_external_frames", "frames"):
        errors = _scan(path, "v:0", "frame=pts,best_effort_timestamp", True, consume, progress)
    if errors or measured != count or first is None:
        fail("WORK_TIMELINE", "当前来件未完成所需展示帧检查")
