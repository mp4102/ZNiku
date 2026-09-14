"""新 exact 色彩链执行入口：先重验解释边界，执行后完整观察实际输出且支持取消。

媒体数学和受控进程复用既有 helper；不调用旧 adapter 入口、不转换旧 namespace。
仅在输出全帧观察成功后返回事实，由新独立 validator 核准后登记 Artifact。
"""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction

from zniku.prepared_source import adapters as media
from zniku.runtime import PythonAdapterContext, PythonAdapterResult, RunnerInput
from zniku.source_color.contracts import read_gate, validate_audio_sources
from zniku.source_preparation.progress import progress_log

from .definitions import definition_role
from .node_contracts import NodeContract, effective_contract, fail, preflight
from .signal import observe_output


def _run(context: PythonAdapterContext, role: str) -> PythonAdapterResult:
    if definition_role(context.definition) != role:
        fail("COLOR_DEFINITION", "只有完整匹配的新 exact definition 可执行色彩链")
    contract = preflight(role, context.inputs, context.node.parameters)
    active = replace(
        context,
        progress=None if context.progress is None else media._FinalProgress(context.progress),
    )
    if active.progress is not None:
        active.progress.report(0.0)
    with progress_log(context.stdout_log_path):
        if role == "split":
            result = media.atomic_split_media(active, contract, _split_filter)
        elif role == "final":
            result = media.final_mux_media(active, contract, read_gate, validate_audio_sources)
        else:
            helpers = {
                "merge": media.merge_video_media,
                "context": media.fi_context_media,
                "crop": media.fi_crop_media,
                "program": media.program_encode_media,
            }
            result = helpers[role](active, contract)
        facts: dict[str, dict[str, object]] = {}
        for output in result.outputs:
            before = output.path.stat()
            observed = observe_output(output.path, progress=active.progress)
            after = output.path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                fail("COLOR_OUTPUT_CHANGED", "全帧扫描期间输出变化，整节点失败")
            facts[output.port_id] = {
                **result.producer_metadata[output.port_id],
                "color_observation": observed.model_dump(mode="json"),
                "color_size": after.st_size,
                "color_mtime_ns": after.st_mtime_ns,
            }
    if context.progress is not None:
        context.progress.report(1.0)
    return replace(result, producer_metadata=facts)


def atomic_split(context: PythonAdapterContext) -> PythonAdapterResult:
    return _run(context, "split")


def merge_video(context: PythonAdapterContext) -> PythonAdapterResult:
    return _run(context, "merge")


def fi_context(context: PythonAdapterContext) -> PythonAdapterResult:
    return _run(context, "context")


def fi_crop(context: PythonAdapterContext) -> PythonAdapterResult:
    return _run(context, "crop")


def program_encode(context: PythonAdapterContext) -> PythonAdapterResult:
    return _run(context, "program")


def final_mux(context: PythonAdapterContext) -> PythonAdapterResult:
    return _run(context, "final")


def _split_filter(source: RunnerInput, contract: NodeContract) -> str:
    """这里显式 setparams 是对已批准工作解释编码，不声称它来自原片实测标签。"""
    width, height = effective_contract(source, contract.source.expectation()).geometry
    if width * 9 != height * 16:
        fail("COLOR_SPLIT_GEOMETRY", "输入必须为精确 16:9")
    scale = (
        ""
        if (width, height) == (1920, 1080)
        else "zscale=w=1920:h=1080:filter=spline36:chromalin=left:chromal=left,"
    )
    rate = Fraction(contract.source.frame_rate)
    return (
        "[0:v:0]setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709,"
        + scale
        + "format=pix_fmts=yuv420p10le,setsar=1/1,"
        + f"settb=expr={rate.denominator}/{rate.numerator},setpts=N[vsegment]"
    )
