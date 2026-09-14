"""仅为完整 exact 节点授予新版局部事实；人工导入必须重新完整比较真实媒体。"""

from __future__ import annotations

from pathlib import Path

from zniku.runtime import NodeValidatorContext, NodeValidatorResult
from zniku.source_preparation.adapters import single_input
from zniku.source_preparation.contracts import read_document
from zniku.source_preparation.inspection import checked_file, file_stat, read_header
from zniku.source_preparation.models import SourceParameters
from zniku.source_preparation.process import fail
from zniku.source_preparation.progress import progress_log, stage

from . import models
from .contracts import check_diagnosis
from .definitions import require_definition_role
from .models import (
    NAMESPACE,
    AdmissionParameters,
    DiagnosticReport,
    ExternalParameters,
    PrepareParameters,
    SourceGate,
)
from .policy import resolve_interpretation
from .preservation import verify_preservation


def source(context: NodeValidatorContext) -> NodeValidatorResult:
    require_definition_role(context.request.definition, "source")
    params = SourceParameters.model_validate(dict(context.request.node.parameters))
    if len(context.outputs) != 1 or context.outputs[0].path != checked_file(
        Path(params.source_path)
    ):
        raise fail("SOURCE_BINDING", "实际只读原件与配置不同")
    return NodeValidatorResult(passed=True)


def diagnostics(context: NodeValidatorContext) -> NodeValidatorResult:
    require_definition_role(context.request.definition, "diagnostics")
    original = single_input(context.request.inputs, "original_media")
    if len(context.outputs) != 1 or context.outputs[0].port_id != "diagnosis":
        raise fail("REPORT_INVALID", "新版诊断只接受一个报告输出")
    report = read_document(context.outputs[0].path, DiagnosticReport)
    if report.original_media_artifact_id != original.artifact_id or report.final_stat != file_stat(
        checked_file(original.path)
    ):
        raise fail("SOURCE_CHANGED", "完整诊断没有绑定当前原件")
    return NodeValidatorResult(
        passed=True,
        media_info_extensions={
            "diagnosis": {
                NAMESPACE: {
                    "role": "diagnosis",
                    "original_media_artifact_id": original.artifact_id,
                    "report": report.model_dump(mode="json"),
                }
            }
        },
    )


def prepared(context: NodeValidatorContext) -> NodeValidatorResult:
    """内置采用受信 adapter 的当前完整结论；外部文件不信任用户成功声明。"""
    role = require_definition_role(context.request.definition, "builtin", "external")
    original, diagnosis = (
        single_input(context.request.inputs, name) for name in ("original_media", "diagnosis")
    )
    report = check_diagnosis(original, diagnosis)
    if len(context.outputs) != 1 or context.outputs[0].port_id != "media":
        raise fail("OUTPUT_SHAPE", "准备只接受一个媒体输出")
    output = context.outputs[0]
    if role == "builtin":
        params = PrepareParameters.model_validate(dict(context.request.node.parameters))
        if not models.T1_PROMOTED:
            raise fail("STRATEGY_NOT_PROMOTED", "新版 T1 尚未晋级")
        facts = output.producer_metadata
        if (
            facts.get("verified_preservation") is not True
            or facts.get("source_stat") != report.final_stat.model_dump(mode="json")
            or facts.get("candidate_stat") != file_stat(output.path).model_dump(mode="json")
            or facts.get("original_media_artifact_id") != original.artifact_id
            or facts.get("diagnosis_artifact_id") != diagnosis.artifact_id
        ):
            raise fail("PRESERVATION_REQUIRED", "缺少当前 attempt 完整保内容结论")
        strategy: str = params.strategy_id
    else:
        external = ExternalParameters.model_validate(dict(context.request.node.parameters))
        declared = context.request.definition.type_id.rsplit(".", 1)[-1]
        if output.path.suffix.lower() != f".{declared}":
            raise fail("CONTAINER", "外部媒体封装声明不符")
        header = read_header(output.path)
        brand = str(header.get("format", {}).get("tags", {}).get("major_brand", "")).strip()
        if (declared == "mov" and brand != "qt") or (declared == "mp4" and brand == "qt"):
            raise fail("CONTAINER", "外部文件实际品牌与 MP4/MOV 声明不同")
        with progress_log(context.work_dir / "logs" / "stdout.log"), stage("external_verify"):
            verify_preservation(
                original.path, output.path, report, target_frame_rate=external.target_frame_rate
            )
        strategy = "external-preservation/2"
    return NodeValidatorResult(
        passed=True,
        media_info_extensions={
            "media": {
                NAMESPACE: {
                    "role": "prepared",
                    "strategy_id": strategy,
                    "original_media_artifact_id": original.artifact_id,
                    "diagnosis_artifact_id": diagnosis.artifact_id,
                    "verified_stat": file_stat(output.path).model_dump(mode="json"),
                }
            }
        },
    )


def admission(context: NodeValidatorContext) -> NodeValidatorResult:
    """核对实际直接输入和新工作解释，不凭未来 Artifact ID 建立关系。"""
    require_definition_role(context.request.definition, "admission")
    params = AdmissionParameters.model_validate(dict(context.request.node.parameters))
    original, reference, diagnosis = (
        single_input(context.request.inputs, name)
        for name in ("original_media", "reference_media", "diagnosis")
    )
    report = check_diagnosis(original, diagnosis)
    outputs = {item.port_id: item for item in context.outputs}
    if set(outputs) != {"video", "gate"} or outputs["video"].path != reference.path:
        raise fail("INPUT_BINDING", "准入输出 shape 不符")
    gate = read_document(outputs["gate"].path, SourceGate)
    audio = tuple(item for item in context.request.inputs if item.port_id == "audio_sources")
    if (
        gate.original_media_artifact_id != original.artifact_id
        or gate.reference_media_artifact_id != reference.artifact_id
        or gate.diagnosis_artifact_id != diagnosis.artifact_id
        or gate.source_frame_count != report.video.frame_count
        or gate.frame_rate != (params.target_frame_rate or report.target_frame_rate)
        or gate.audio_policy != params.audio_policy
        or gate.interpretation_policy != params.interpretation_policy
        or tuple(item.artifact_id for item in audio)
        != tuple(item.artifact_id for item in gate.audio_bindings)
    ):
        raise fail("INPUT_BINDING", "准入未绑定当前原件、参考、音频和显式解释")
    working, basis = resolve_interpretation(gate.observed_signal, params.interpretation_policy)
    if working != gate.working_signal or basis != gate.basis:
        raise fail("COLOR_POLICY", "准入工作解释与实际观察推导不符")
    return NodeValidatorResult(
        passed=True,
        media_info_extensions={
            port: {NAMESPACE: {"role": role, "admission": gate.model_dump(mode="json")}}
            for port, role in (("video", "reference"), ("gate", "admission"))
        },
    )
