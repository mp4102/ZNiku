"""普通 exact 节点验收：人工新参考扫描一次，自动候选消费本 attempt 的受信观察。"""

from __future__ import annotations

import json
from pathlib import Path

from zniku.runtime import NodeValidatorContext, NodeValidatorResult

from .adapters import single_input
from .contracts import _plain, read_document
from .inspection import checked_file, file_stat, read_header
from .process import fail
from .progress import progress_log
from .work_adapters import make_gate
from .work_contracts import check_diagnosis
from .work_definitions import require_definition_role
from .work_inspection import inspect_source
from .work_models import (
    NAMESPACE,
    AdmissionParameters,
    ExternalParameters,
    PrepareParameters,
    SourceParameters,
    WorkDiagnosticReport,
    WorkSourceGate,
)


def source(context: NodeValidatorContext) -> NodeValidatorResult:
    require_definition_role(context.request.definition, "source")
    params = SourceParameters.model_validate(dict(context.request.node.parameters))
    if len(context.outputs) != 1 or context.outputs[0].path != checked_file(
        Path(params.source_path)
    ):
        raise fail("SOURCE_BINDING", "原件输出必须是选择的实际只读路径")
    return NodeValidatorResult(passed=True)


def diagnostics(context: NodeValidatorContext) -> NodeValidatorResult:
    require_definition_role(context.request.definition, "diagnostics")
    original = single_input(context.request.inputs, "original_media")
    if len(context.outputs) != 1 or context.outputs[0].port_id != "diagnosis":
        raise fail("REPORT_INVALID", "诊断输出 shape 无效")
    report = read_document(context.outputs[0].path, WorkDiagnosticReport)
    if report.original_media_artifact_id != original.artifact_id or report.final_stat != file_stat(
        checked_file(original.path)
    ):
        raise fail("SOURCE_CHANGED", "诊断与当前直接输入不符")
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
    """外部新参考不比较原像素/N；必须显式确认并通过自身工作能力完整检查。"""
    role = require_definition_role(context.request.definition, "builtin", "external")
    original, diagnosis = (
        single_input(context.request.inputs, "original_media"),
        single_input(context.request.inputs, "diagnosis"),
    )
    report = check_diagnosis(original, diagnosis)
    if len(context.outputs) != 1 or context.outputs[0].port_id != "media":
        raise fail("OUTPUT_SHAPE", "准备只允许一个实际媒体输出")
    output = context.outputs[0]
    path = checked_file(output.path)
    if path.samefile(original.path):
        raise fail("CANDIDATE_ALIASED", "工作参考不能是原件或其硬链接")
    strategy: str
    if role == "builtin":
        params = PrepareParameters.model_validate(dict(context.request.node.parameters))
        metadata = output.producer_metadata
        if (
            metadata.get("original_media_artifact_id") != original.artifact_id
            or metadata.get("diagnosis_artifact_id") != diagnosis.artifact_id
            or _plain(metadata.get("source_stat")) != report.final_stat.model_dump(mode="json")
            or _plain(metadata.get("candidate_stat")) != file_stat(path).model_dump(mode="json")
            or metadata.get("frame_count_preserved") is not True
        ):
            raise fail("WORK_REFERENCE_INVALID", "缺少当前受信生成器的完整工作观察")
        fresh = WorkDiagnosticReport.model_validate_json(
            json.dumps(_plain(metadata.get("observation")))
        )
        strategy = params.strategy_id
        if (
            report.video is None
            or fresh.video is None
            or fresh.video.frame_count != report.video.frame_count
            or fresh.video.codec != "ffv1"
            or fresh.audio
            or params.target_frame_rate != fresh.target_frame_rate
        ):
            raise fail("WORK_REFERENCE_INVALID", "内置候选不满足保帧 FFV1 声明")
    else:
        external = ExternalParameters.model_validate(dict(context.request.node.parameters))
        declared = context.request.definition.type_id.rsplit(".", 1)[-1]
        if path.suffix.lower() != f".{declared}":
            raise fail("CONTAINER", "外部参考格式与当前 exact 声明不符")
        header = read_header(path)
        brand = str(header.get("format", {}).get("tags", {}).get("major_brand", "")).strip()
        if (declared == "mov" and brand != "qt") or (declared == "mp4" and brand == "qt"):
            raise fail("CONTAINER", "MOV 与 MP4 不能靠改后缀冒充")
        with progress_log(context.work_dir / "logs" / "stdout.log"):
            fresh = inspect_source(
                path, original.artifact_id, target_frame_rate=external.target_frame_rate
            )
        strategy = "external-new-reference/1"
    if (
        fresh.status != "direct"
        or fresh.final_stat != file_stat(path)
        or fresh.original_media_artifact_id != original.artifact_id
    ):
        raise fail(
            "WORK_REFERENCE_INVALID",
            fresh.findings[0].message if fresh.findings else "候选不是可靠工作参考",
        )
    if file_stat(original.path) != report.final_stat:
        raise fail("SOURCE_CHANGED", "候选验证期间原件变化")
    return NodeValidatorResult(
        passed=True,
        media_info_extensions={
            "media": {
                NAMESPACE: {
                    "role": "prepared",
                    "strategy_id": strategy,
                    "original_media_artifact_id": original.artifact_id,
                    "diagnosis_artifact_id": diagnosis.artifact_id,
                    "verified_stat": fresh.final_stat.model_dump(mode="json"),
                    "observation": fresh.model_dump(mode="json"),
                }
            }
        },
    )


def admission(context: NodeValidatorContext) -> NodeValidatorResult:
    """重算纯绑定和轻量头检而非全片扫描；任何解释/身份差异都拒绝登记。"""
    require_definition_role(context.request.definition, "admission")
    params = AdmissionParameters.model_validate(dict(context.request.node.parameters))
    reference = single_input(context.request.inputs, "reference_media")
    outputs = {o.port_id: o for o in context.outputs}
    if set(outputs) != {"video", "gate"} or outputs["video"].path != reference.path:
        raise fail("INPUT_BINDING", "准入双输出 shape 不符")
    gate = read_document(outputs["gate"].path, WorkSourceGate)
    if gate != make_gate(context.request.inputs, params):
        raise fail("INPUT_BINDING", "准入与当前输入观察/选择不一致")
    return NodeValidatorResult(
        passed=True,
        media_info_extensions={
            port: {NAMESPACE: {"role": role, "admission": gate.model_dump(mode="json")}}
            for port, role in (("video", "reference"), ("gate", "admission"))
        },
    )
