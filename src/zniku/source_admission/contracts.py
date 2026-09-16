"""为 0.3.5 绑定真实 producer identity，复用已有重叠帧数学而不冒充旧结果。"""

from __future__ import annotations

from collections.abc import Mapping
from fractions import Fraction
from typing import Literal

from pydantic import ValidationError

from zniku.avenhance_v27 import validators as media
from zniku.avenhance_v27.probe import namespace_from_media_info
from zniku.runtime import RunnerInput
from zniku.source_aligned import node_contracts as shared

VERSION = "0.3.5"
NAMESPACE = "zniku.source.admitted"
SOURCE_NAMESPACE = "zniku.source.admission"


class OverlapMetadata(shared.OverlapMetadata):
    """沿用精确区间数学，版本只允许新 lineage，旧 metadata 不可注入。"""

    # Pydantic frozen 模型以继承复用纯区间约束；没有运行时字段写入或旧结果强制转换。
    producer_version: Literal["0.3.5"] = "0.3.5"  # type: ignore[assignment]


class ExternalMetadata(shared.ExternalMetadata):
    """新 MR 验证 N/FPS/几何/信号，时间轴采用 AV2.7 cadence 而非旧逐帧 CFR。"""

    producer_version: Literal["0.3.5"] = "0.3.5"  # type: ignore[assignment]
    alignment: Literal["av27-cadence-n-to-n"] = "av27-cadence-n-to-n"  # type: ignore[assignment]


def effective_contract(item: RunnerInput, source: shared.SourceExpectation) -> media._MediaContract:
    """显式选择本版 source/MR namespace；不隐式接受旧源或仅同长的其他文件。"""
    if item.kind != "VideoFile" or item.producer_port_id != "video":
        shared.fail("SOURCE_BINDING", "有效输入必须来自 video 端口")
    if item.artifact_id == source.original_video_artifact_id:
        marker = item.media_info.get(SOURCE_NAMESPACE)
        if not isinstance(marker, Mapping) or marker.get("contract_version") != VERSION:
            shared.fail("SOURCE_VERSION", "必须使用 0.3.5 Source 实际准入结果")
        namespace = namespace_from_media_info(item.media_info)
        if type(namespace.get("source_ordinal")) is not int or namespace["source_ordinal"] != 0:
            shared.fail("SOURCE_STAGE", "必须为单一 program Source")
        result = media._metadata_contract(item)
    else:
        from .mosaic_restoration import TYPE_ID, MosaicRestorationMetadata

        try:
            raw = shared._plain(item.media_info.get(NAMESPACE))
            model = (
                MosaicRestorationMetadata
                if isinstance(raw, dict) and raw.get("producer_type_id") == TYPE_ID
                else ExternalMetadata
            )
            metadata = model.model_validate(raw)
        except ValidationError as error:
            shared.fail("EXTERNAL_METADATA", f"缺少本版外部修复 metadata: {error.error_count()}")
        if metadata.source != source:
            shared.fail("SOURCE_BINDING", "外部修复绑定其他参考源")
        result = media._MediaContract(
            source.frame_count,
            Fraction(source.frame_rate),
            (metadata.geometry.width, metadata.geometry.height),
            metadata.signal.model_dump(),
            float(Fraction(source.frame_count) / Fraction(source.frame_rate)),
        )
    if result.frame_count != source.frame_count or result.frame_rate != Fraction(source.frame_rate):
        shared.fail("SOURCE_TIMELINE", "直接来源 N/FPS 与绑定不符")
    return result


def preflight(
    role: str, inputs: tuple[RunnerInput, ...], parameters: Mapping[str, object]
) -> shared.NodeContract:
    """共用纯局部区间约束，读写本版 namespace，旧入口的默认合同保持不变。"""
    return shared.preflight(
        role,
        inputs,
        parameters,
        metadata_model=OverlapMetadata,
        namespace=NAMESPACE,
        effective_reader=effective_contract,
    )


def external_preflight(
    inputs: tuple[RunnerInput, ...], parameters: Mapping[str, object]
) -> tuple[shared.ExternalParameters, RunnerInput, media._MediaContract]:
    params = shared.ExternalParameters.model_validate(shared._plain(parameters))
    if {item.port_id for item in inputs} != {"video", "gate"}:
        shared.fail("INPUT_PORTS", "MR 必须直接连接源 video 与准入 gate")
    video = shared._inputs(inputs, "video", many=False)[0]
    gate = shared._inputs(inputs, "gate", many=False, kind="DataFile")[0]
    if (
        video.artifact_id != params.source.original_video_artifact_id
        or gate.artifact_id != params.source.admission_artifact_id
        or gate.producer_port_id != "gate"
    ):
        shared.fail("SOURCE_BINDING", "MR 源或 gate 身份不匹配")
    expected = effective_contract(video, params.source)
    shared._check_admission(gate, params.source)
    return params, video, expected
