"""复用已验证的章合并、上下文、裁边与单次编码执行器，只注入本族严格局部合同。"""

from zniku.avenhance_v27 import adapters as av27
from zniku.avenhance_v27.probe import Av27MediaError, audio_signatures_from_metadata
from zniku.runtime import PythonAdapterContext, PythonAdapterResult
from zniku.source_aligned import adapters as shared

from .contracts import preflight


def merge(context: PythonAdapterContext) -> PythonAdapterResult:
    return shared.merge_video(
        context, _contract=preflight("merge", context.inputs, context.node.parameters)
    )


def context(context: PythonAdapterContext) -> PythonAdapterResult:
    return shared.fi_context(
        context, _contract=preflight("context", context.inputs, context.node.parameters)
    )


def crop(context: PythonAdapterContext) -> PythonAdapterResult:
    return shared.fi_crop(
        context, _contract=preflight("crop", context.inputs, context.node.parameters)
    )


def program(context: PythonAdapterContext) -> PythonAdapterResult:
    return shared.program_encode(
        context, _contract=preflight("program", context.inputs, context.node.parameters)
    )


def final(context: PythonAdapterContext) -> PythonAdapterResult:
    """继续使用当前参考源音轨，不重新施加已删除的旧 source priming 门禁。"""
    contract = preflight("final", context.inputs, context.node.parameters)
    target = shared._targets(context, contract)["media"]
    sources = shared._inputs(context, "sources")
    count = contract.outputs[0].metadata.frame_count
    actual = av27._execute_final_mux(
        context,
        shared._inputs(context, "video")[0],
        sources,
        (),
        audio_signatures_from_metadata(sources[0].media_info),
        mode="program",
        target=target,
        expected_frames=count,
    )
    if actual is not None and actual != count:
        raise Av27MediaError("E_CHAPTER_BATCH_FINAL_COUNT", "Final 实际帧数与 Program 不一致")
    return shared._result("media", target, count, {})
