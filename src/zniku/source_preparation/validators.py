"""仅在当前输出及来源合同通过后登记准备结果，人工导入不信任第三方成功声明。"""

from __future__ import annotations

from pathlib import Path

from zniku.runtime import NodeValidatorContext, NodeValidatorResult

from . import models
from .adapters import single_input
from .contracts import check_diagnosis, read_document
from .definitions import require_definition_role
from .inspection import checked_file, file_stat, read_header
from .models import (
    NAMESPACE,
    AdmissionParameters,
    DiagnosticReport,
    ExternalParameters,
    PrepareParameters,
    SourceGate,
    SourceParameters,
)
from .preservation import verify_preservation
from .process import fail
from .progress import progress_log, stage


def source(context: NodeValidatorContext) -> NodeValidatorResult:
    """只允许正式只读入口登记操作者选择的实际原件路径。"""
    require_definition_role(context.request.definition, "source")
    params = SourceParameters.model_validate(dict(context.request.node.parameters))
    if len(context.outputs) != 1 or context.outputs[0].path != checked_file(
        Path(params.source_path)
    ):
        raise fail("SOURCE_BINDING", "只读原件输出路径不符合选择")
    return NodeValidatorResult(passed=True)


def diagnostics(context: NodeValidatorContext) -> NodeValidatorResult:
    """只给正式自动检查产物附加局部 metadata，拒绝自定义人工报告借用入口。"""
    require_definition_role(context.request.definition, "diagnostics")
    original = single_input(context.request.inputs, "original_media")
    if len(context.outputs) != 1 or context.outputs[0].port_id != "diagnosis":
        raise fail("REPORT_INVALID", "诊断输出 shape 无效")
    report = read_document(context.outputs[0].path, DiagnosticReport)
    if report.original_media_artifact_id != original.artifact_id or report.final_stat != file_stat(
        checked_file(original.path)
    ):
        raise fail("SOURCE_CHANGED", "诊断来源或检查后 stat 不匹配")
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
    """内置采用受信 adapter 的当前结果；外部文件重新执行同一完整保内容比较。"""
    role = require_definition_role(context.request.definition, "builtin", "external")
    original = single_input(context.request.inputs, "original_media")
    diagnosis = single_input(context.request.inputs, "diagnosis")
    report = check_diagnosis(original, diagnosis)
    if len(context.outputs) != 1 or context.outputs[0].port_id != "media":
        raise fail("OUTPUT_SHAPE", "准备节点仅接受一个 MediaFile 输出")
    output = context.outputs[0]
    strategy: str
    if role == "builtin":
        parameters = PrepareParameters.model_validate(dict(context.request.node.parameters))
        if not models.T1_PROMOTED:
            raise fail("STRATEGY_NOT_PROMOTED", "T1 尚未晋级")
        metadata = output.producer_metadata
        if metadata.get("verified_preservation") is not True or (
            metadata.get("original_media_artifact_id") != original.artifact_id
            or metadata.get("diagnosis_artifact_id") != diagnosis.artifact_id
            or metadata.get("source_stat") != report.final_stat.model_dump(mode="json")
            or metadata.get("candidate_stat") != file_stat(output.path).model_dump(mode="json")
        ):
            raise fail("PRESERVATION_REQUIRED", "缺少当前内置结果的完整保内容结论")
        strategy = parameters.strategy_id
    else:
        external = ExternalParameters.model_validate(dict(context.request.node.parameters))
        declared = context.request.definition.type_id.rsplit(".", 1)[-1]
        if declared not in {"mp4", "mov", "mkv"} or output.path.suffix.lower() != f".{declared}":
            raise fail("CONTAINER", "外部格式与当前 exact definition 不一致")
        header = read_header(output.path)
        brand = str(header.get("format", {}).get("tags", {}).get("major_brand", "")).strip()
        if (declared == "mov" and brand != "qt") or (declared == "mp4" and brand == "qt"):
            raise fail("CONTAINER", "不能通过改扩展名把 MOV 冒充 MP4 或反向冒充")
        with progress_log(context.work_dir / "logs" / "stdout.log"), stage("external_verify"):
            verify_preservation(
                original.path,
                output.path,
                report,
                target_frame_rate=external.target_frame_rate,
            )
        strategy = "external-preservation/1"
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
    """检查当前 direct-input 身份和双输出角色，不把未来视频 UUID 写进 gate。"""
    require_definition_role(context.request.definition, "admission")
    parameters = AdmissionParameters.model_validate(dict(context.request.node.parameters))
    original = single_input(context.request.inputs, "original_media")
    reference = single_input(context.request.inputs, "reference_media")
    diagnosis = single_input(context.request.inputs, "diagnosis")
    report = check_diagnosis(original, diagnosis)
    outputs = {item.port_id: item for item in context.outputs}
    if set(outputs) != {"video", "gate"} or outputs["video"].path != reference.path:
        raise fail("INPUT_BINDING", "准入双输出 shape 或工作参考路径不符")
    gate = read_document(outputs["gate"].path, SourceGate)
    audio = tuple(item for item in context.request.inputs if item.port_id == "audio_sources")
    if (
        gate.original_media_artifact_id != original.artifact_id
        or gate.reference_media_artifact_id != reference.artifact_id
        or gate.diagnosis_artifact_id != diagnosis.artifact_id
        or gate.source_frame_count != report.video.frame_count
        or gate.frame_rate != (parameters.target_frame_rate or report.target_frame_rate)
        or gate.audio_policy != parameters.audio_policy
        or tuple(item.artifact_id for item in audio)
        != tuple(item.artifact_id for item in gate.audio_bindings)
    ):
        raise fail("INPUT_BINDING", "准入未绑定当前原件、参考、音频及诊断")
    return NodeValidatorResult(
        passed=True,
        media_info_extensions={
            port: {NAMESPACE: {"role": role, "admission": gate.model_dump(mode="json")}}
            for port, role in (("video", "reference"), ("gate", "admission"))
        },
    )
