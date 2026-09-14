"""普通工作参考的局部绑定边界，复用既有章叶/FI 数学而不借用旧准入。

工作解释来自直接 Gate，输出只承诺当前节点所需检查，不携带完整色彩审计证明。
旧 namespace 不接受新数据；来源、顺序和范围不匹配仍在媒体写入前失败。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from fractions import Fraction
from typing import Any, Literal

from zniku.avenhance_v27 import validators as legacy
from zniku.runtime import RunnerInput
from zniku.source_preparation.work_contracts import check_reference_binding, read_gate
from zniku.source_preparation.work_models import NAMESPACE, WorkSourceGate

from . import node_contracts as shared
from .node_contracts import ExternalParameters, NodeContract, SourceExpectation, fail

OVERLAP_NAMESPACE = "zniku.prepared.work"
OVERLAP_NODE_VERSION = "0.3.4-work.1"


class WorkOverlapMetadata(shared.OverlapMetadata):
    """沿用帧区间约束；工作信号不是原件完整声明的审计结论。"""

    producer_version: Literal["0.3.4-work.1"] = "0.3.4-work.1"  # type: ignore[assignment]
    observation_scope: Literal["ffprobe-stream"] | None = None
    inherited_interpretation: tuple[str, ...] = ()


class WorkExternalMetadata(shared.ExternalMetadata):
    """MR 的 N→N 与操作者帧序声明；不证明模型、像素等价或完整色彩恒定。"""

    producer_version: Literal["0.3.4-work.1"] = "0.3.4-work.1"  # type: ignore[assignment]
    observation_scope: Literal["ffprobe-stream"] = "ffprobe-stream"
    inherited_interpretation: tuple[str, ...] = ()


def read_metadata(item: RunnerInput) -> WorkOverlapMetadata:
    value = WorkOverlapMetadata.model_validate_json(
        json.dumps(shared._plain(item.media_info.get(OVERLAP_NAMESPACE)))
    )
    port = value.leaf.leaf_id if value.role == "split" and value.leaf else "video"
    if (
        item.kind != "VideoFile"
        or item.producer_port_id != port
        or value.observation_scope != "ffprobe-stream"
    ):
        fail("WORK_PRODUCER", "输入不是当前普通工作节点已检查的输出")
    return value


def _check_source(gate: WorkSourceGate, source: SourceExpectation) -> None:
    if (
        gate.original_media_artifact_id != source.original_media_artifact_id
        or gate.reference_media_artifact_id != source.reference_media_artifact_id
        or gate.diagnosis_artifact_id != source.diagnosis_artifact_id
        or gate.source_frame_count != source.frame_count
        or gate.frame_rate != source.frame_rate
        or tuple(binding.artifact_id for binding in gate.audio_bindings)
        != source.audio_source_artifact_ids
    ):
        fail("WORK_SOURCE_BINDING", "工作参考、音频来源或 N/FPS 与当前规划不一致")


def check_admission(item: RunnerInput, source: SourceExpectation) -> None:
    if item.artifact_id != source.admission_artifact_id:
        fail("WORK_ADMISSION_BINDING", "输入不是当前规划直接绑定的工作 Gate")
    _check_source(read_gate(item), source)


def reference_gate(item: RunnerInput, source: SourceExpectation) -> WorkSourceGate:
    namespace = shared._plain(item.media_info.get(NAMESPACE))
    if (
        not isinstance(namespace, dict)
        or set(namespace) != {"role", "admission"}
        or namespace["role"] != "reference"
    ):
        fail("WORK_REFERENCE", "参考必须来自本版工作源节点，不能借用旧审计结果")
    gate = WorkSourceGate.model_validate_json(json.dumps(namespace["admission"]))
    _check_source(gate, source)
    return gate


def effective_contract(item: RunnerInput, source: SourceExpectation) -> legacy._MediaContract:
    if item.kind != "VideoFile" or item.producer_port_id != "video":
        fail("WORK_REFERENCE", "有效视频必须来自当前 video 输出")
    if item.artifact_id == source.reference_video_artifact_id:
        gate = reference_gate(item, source)
        geometry = (gate.geometry.width, gate.geometry.height)
        signal = gate.working_signal.model_dump()
    else:
        value = WorkExternalMetadata.model_validate_json(
            json.dumps(shared._plain(item.media_info.get(OVERLAP_NAMESPACE)))
        )
        if value.source != source:
            fail("WORK_MR_BINDING", "外部前处理属于其他工作参考")
        geometry = (value.geometry.width, value.geometry.height)
        signal = value.signal.model_dump()
    return legacy._MediaContract(
        source.frame_count,
        Fraction(source.frame_rate),
        geometry,
        signal,
        float(Fraction(source.frame_count) / Fraction(source.frame_rate)),
    )


def _metadata(*args: Any, **kwargs: Any) -> WorkOverlapMetadata:
    # 共享构造只计算区间和所选 signal，不生成 Artifact 或旧 namespace，也不宣称旧验收通过。
    calculated = shared._metadata(*args, **kwargs)
    return WorkOverlapMetadata.model_validate(calculated.model_dump(exclude={"producer_version"}))


def preflight(
    role: str, inputs: tuple[RunnerInput, ...], parameters: Mapping[str, object]
) -> NodeContract:
    return shared.preflight(
        role,
        inputs,
        parameters,
        _kernel=shared.ContractKernel(
            read_metadata, effective_contract, check_admission, check_reference_binding, _metadata
        ),
    )


def external_preflight(
    inputs: tuple[RunnerInput, ...], parameters: Mapping[str, object]
) -> tuple[ExternalParameters, RunnerInput, legacy._MediaContract]:
    params = ExternalParameters.model_validate(shared._plain(parameters))
    if {item.port_id for item in inputs} != {"video", "gate"}:
        fail("WORK_INPUT_PORTS", "MR 必须直接连接工作参考和 Gate")
    video = shared._inputs(inputs, "video", many=False)[0]
    gate = shared._inputs(inputs, "gate", many=False, kind="DataFile")[0]
    if video.artifact_id != params.source.reference_video_artifact_id:
        fail("WORK_REFERENCE", "MR 不得消费其他工作参考")
    check_admission(gate, params.source)
    check_reference_binding(video, gate)
    return params, video, effective_contract(video, params.source)
