"""执行新 exact 自动节点：一次可取消 Source 扫描，复用受控 FFV1/ProRes 编码，不修复原片。"""

from __future__ import annotations

import threading
from dataclasses import replace

from zniku.avenhance_v27 import adapters as av27
from zniku.avenhance_v27 import validators as media_validators
from zniku.avenhance_v27.probe import Av27MediaError, audio_signatures_from_metadata
from zniku.runtime import PythonAdapterContext, PythonAdapterResult
from zniku.runtime.runner import RunnerCancelled
from zniku.source_aligned import adapters as shared

from .contracts import VERSION, effective_contract, preflight
from .probe import admit_source

_lock = threading.Lock()
_active: dict[str, threading.Event] = {}


def cancel_source(node_run_id: str) -> bool:
    """进程内取消句柄不是 checkpoint；结束即移除，下一 attempt 必须从头扫描。"""
    with _lock:
        event = _active.get(node_run_id)
        if event is None:
            return False
        event.set()
        return True


def source_program(context: PythonAdapterContext) -> PythonAdapterResult:
    result = av27.source_program(context)
    path = result.outputs[0].path
    before = path.stat()
    event = threading.Event()
    with _lock:
        _active[context.node_run_id] = event
    try:
        admitted = admit_source(path, cancel_event=event, progress=context.progress)
    except Av27MediaError as error:
        if error.code == "E_SOURCE_ADMISSION_CANCELLED":
            raise RunnerCancelled(str(error)) from error
        raise
    finally:
        with _lock:
            _active.pop(context.node_run_id, None)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise Av27MediaError("E_SOURCE_ADMISSION_CHANGED", "分析期间源文件变化，结果不登记")
    ordinal = context.node.parameters.get("source_ordinal")
    if type(ordinal) is not int or ordinal != 0:
        raise ValueError("0.3.5 默认 Source 只接受单源 ordinal=0")
    namespace = admitted.namespace_summary(source_ordinal=ordinal)
    namespace["signal"] = media_validators._canonical_signal(namespace["signal"], role="Source")
    facts = {
        "contract_version": VERSION,
        "namespace": namespace,
        "header": admitted.header.video.to_summary(),
        "size": after.st_size,
        "mtime_ns": after.st_mtime_ns,
        "warnings": list(admitted.warnings),
        "cadence": admitted.cadence,
    }
    return replace(
        result,
        producer_metadata=dict.fromkeys(("video", "source_media"), facts),
        validation_summary={
            "source_contract": "AVEnhanceFlow-2.7.0",
            "warnings": list(admitted.warnings),
        },
    )


def source_admission(context: PythonAdapterContext) -> PythonAdapterResult:
    """仅复用已稳定的普通 gate 数据格式；它不声明旧节点 producer 身份。"""
    return av27.source_admission(context)


def atomic_split(context: PythonAdapterContext) -> PythonAdapterResult:
    contract = preflight("split", context.inputs, context.node.parameters)
    return shared._atomic_split(
        context, contract, verify_original=False, effective_reader=effective_contract
    )


def merge_video(context: PythonAdapterContext) -> PythonAdapterResult:
    return shared.merge_video(
        context, _contract=preflight("merge", context.inputs, context.node.parameters)
    )


def fi_context(context: PythonAdapterContext) -> PythonAdapterResult:
    return shared.fi_context(
        context, _contract=preflight("context", context.inputs, context.node.parameters)
    )


def fi_crop(context: PythonAdapterContext) -> PythonAdapterResult:
    return shared.fi_crop(
        context, _contract=preflight("crop", context.inputs, context.node.parameters)
    )


def program_encode(context: PythonAdapterContext) -> PythonAdapterResult:
    return shared.program_encode(
        context, _contract=preflight("program", context.inputs, context.node.parameters)
    )


def final_mux(context: PythonAdapterContext) -> PythonAdapterResult:
    """按 AV2.7 单源规则复制参考源音轨；不叠加旧版本 audio priming 禁入规则。"""
    contract = preflight("final", context.inputs, context.node.parameters)
    target = shared._targets(context, contract)["media"]
    program = shared._inputs(context, "video")[0]
    sources = shared._inputs(context, "sources")
    count = contract.outputs[0].metadata.frame_count
    actual = av27._execute_final_mux(
        context,
        program,
        sources,
        (),
        audio_signatures_from_metadata(sources[0].media_info),
        mode="program",
        target=target,
        expected_frames=count,
    )
    if actual is not None and actual != count:
        raise Av27MediaError("E_SOURCE_ADMISSION_FINAL_COUNT", "Final 实际帧计数与 Program 不符")
    return shared._result("media", target, count, {})
