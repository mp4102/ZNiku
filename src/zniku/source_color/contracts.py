"""校验色彩新版诊断、工作参考及直接输入绑定；不接受旧 namespace 冒充新事实。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from zniku.runtime import RunnerInput
from zniku.runtime.progress import ProgressReporter
from zniku.source_preparation.contracts import _plain, read_document
from zniku.source_preparation.contracts import validate_audio_sources as validate_previous_audio
from zniku.source_preparation.inspection import checked_file, file_stat
from zniku.source_preparation.process import fail

from .models import NAMESPACE, DiagnosticReport, SourceGate, legacy_audio_gate


def namespace(item: RunnerInput) -> Mapping[str, object]:
    value = item.media_info.get(NAMESPACE)
    if not isinstance(value, Mapping):
        raise fail("INPUT_BINDING", "直接输入缺少新版色彩局部绑定")
    return value


def read_diagnosis(item: RunnerInput) -> DiagnosticReport:
    """读取并核对已登记新版诊断，不信任仅有 schema 的第三方 JSON。"""
    if item.kind != "DataFile":
        raise fail("INPUT_BINDING", "诊断必须是 DataFile")
    report = read_document(item.path, DiagnosticReport)
    data = namespace(item)
    if (
        data.get("role") != "diagnosis"
        or data.get("original_media_artifact_id") != report.original_media_artifact_id
        or _plain(data.get("report")) != report.model_dump(mode="json")
    ):
        raise fail("INPUT_BINDING", "诊断文件与当前已登记观察不一致")
    return report


def read_report_artifact(path: Path) -> DiagnosticReport:
    """服务只读入口；调用者须另外核对当前工程 Artifact 归属。"""
    return read_document(path, DiagnosticReport)


def check_diagnosis(original: RunnerInput, diagnosis: RunnerInput) -> DiagnosticReport:
    report = read_diagnosis(diagnosis)
    if original.kind != "MediaFile" or original.artifact_id != report.original_media_artifact_id:
        raise fail("INPUT_BINDING", "诊断不属于当前直接原件")
    if file_stat(checked_file(original.path)) != report.final_stat:
        raise fail("SOURCE_CHANGED", "原件在诊断后变化")
    return report


def read_gate(item: RunnerInput) -> SourceGate:
    """严格读取 /2 gate，原始观察与工作解释不能经文件改写互换。"""
    if item.kind != "DataFile":
        raise fail("INPUT_BINDING", "准入必须为 DataFile")
    gate = read_document(item.path, SourceGate)
    data = namespace(item)
    if data.get("role") != "admission" or _plain(data.get("admission")) != gate.model_dump(
        mode="json"
    ):
        raise fail("INPUT_BINDING", "准入文件与已登记新版绑定不同")
    return gate


def check_reference_binding(video: RunnerInput, gate: RunnerInput) -> SourceGate:
    result = read_gate(gate)
    data = namespace(video)
    if (
        video.kind != "VideoFile"
        or data.get("role") != "reference"
        or _plain(data.get("admission")) != result.model_dump(mode="json")
    ):
        raise fail("INPUT_BINDING", "视频与新版准入不是相同来源/解释")
    return result


def validate_audio_sources(
    gate: SourceGate, inputs: Sequence[RunnerInput], *, progress: ProgressReporter | None = None
) -> None:
    """只复用原件音频既有完整验证，色彩解释没有音频豁免。"""
    validate_previous_audio(legacy_audio_gate(gate), inputs, progress=progress)
