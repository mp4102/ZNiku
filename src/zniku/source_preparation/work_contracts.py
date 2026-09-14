"""普通工作源的局部输入绑定与轻量复核；不把旧审计报告转换成普通准入。

扫描结果只接受受信 exact 生产者当前登记的 metadata；数据读取本身不授予执行权限。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from zniku.runtime import RunnerInput
from zniku.runtime.progress import ProgressReporter

from .contracts import _plain, read_document
from .inspection import checked_file, file_stat, read_header
from .process import fail
from .work_models import NAMESPACE, WorkDiagnosticReport, WorkSourceGate


def namespace(item: RunnerInput) -> Mapping[str, object]:
    """只读取本 exact 专属命名空间；旧完整审计不能伪装普通观察，反之亦然。"""
    data = item.media_info.get(NAMESPACE)
    if not isinstance(data, Mapping):
        raise fail("INPUT_BINDING", "输入缺少普通工作源局部绑定")
    return data


def read_diagnosis(
    item: RunnerInput, original_input: RunnerInput | None = None
) -> WorkDiagnosticReport:
    """读取可信诊断 DataFile，可同时核对实际原件；不重新全片扫描。"""
    if item.kind != "DataFile":
        raise fail("INPUT_BINDING", "诊断必须为 DataFile")
    report = read_document(item.path, WorkDiagnosticReport)
    data = namespace(item)
    if (
        data.get("role") != "diagnosis"
        or data.get("original_media_artifact_id") != report.original_media_artifact_id
        or _plain(data.get("report")) != report.model_dump(mode="json")
    ):
        raise fail("INPUT_BINDING", "普通诊断与生产者绑定不一致")
    if original_input is not None:
        if (
            original_input.kind != "MediaFile"
            or original_input.artifact_id != report.original_media_artifact_id
        ):
            raise fail("INPUT_BINDING", "诊断不属于当前直接原件")
        if file_stat(checked_file(original_input.path)) != report.final_stat:
            raise fail("SOURCE_CHANGED", "原件在检查后变化，请从头重查")
    return report


def check_diagnosis(original: RunnerInput, diagnosis: RunnerInput) -> WorkDiagnosticReport:
    """兼容既有服务调用顺序的单一绑定入口。"""
    return read_diagnosis(diagnosis, original)


def read_report_artifact(path: Path) -> WorkDiagnosticReport:
    """有界读取纯报告；服务仍负责当前工程和 Run 归属。"""
    return read_document(path, WorkDiagnosticReport)


def read_reference_report(item: RunnerInput) -> WorkDiagnosticReport:
    """读取已完成准备节点的实际候选观察；便于准入失败后只改工作解释。"""
    data = namespace(item)
    if item.kind != "MediaFile" or data.get("role") != "prepared":
        raise fail("INPUT_BINDING", "输入不是已验证普通工作参考")
    try:
        import json

        report = WorkDiagnosticReport.model_validate_json(
            json.dumps(_plain(data.get("observation")))
        )
    except (ValueError, TypeError) as exc:
        raise fail("INPUT_BINDING", "准备结果缺少有效当前候选观察") from exc
    if (
        report.original_media_artifact_id != data.get("original_media_artifact_id")
        or data.get("verified_stat") != report.final_stat.model_dump(mode="json")
        or file_stat(checked_file(item.path)) != report.final_stat
    ):
        raise fail("REFERENCE_CHANGED", "候选绑定或 stat 改变，必须重新导入")
    return report


def read_gate(item: RunnerInput) -> WorkSourceGate:
    """普通 Gate 与已登记 metadata 必须同形，不查询全局索引。"""
    if item.kind != "DataFile":
        raise fail("INPUT_BINDING", "准入文件类型无效")
    gate = read_document(item.path, WorkSourceGate)
    data = namespace(item)
    if data.get("role") != "admission" or _plain(data.get("admission")) != gate.model_dump(
        mode="json"
    ):
        raise fail("INPUT_BINDING", "准入文件与当前登记内容不同")
    return gate


def check_reference_binding(video: RunnerInput, gate: RunnerInput) -> WorkSourceGate:
    """核对实际 VideoFile 和 Gate，不把未来身份伪装为已登记事实。"""
    result = read_gate(gate)
    data = namespace(video)
    if (
        video.kind != "VideoFile"
        or data.get("role") != "reference"
        or _plain(data.get("admission")) != result.model_dump(mode="json")
    ):
        raise fail("INPUT_BINDING", "工作视频与当前准入不匹配")
    if file_stat(checked_file(video.path)) != result.reference_stat:
        raise fail("REFERENCE_CHANGED", "工作参考在准入后变化")
    return result


def validate_audio_sources(
    gate: WorkSourceGate,
    inputs: Sequence[RunnerInput],
    *,
    progress: ProgressReporter | None = None,
) -> None:
    """只复核真实载体 identity/stat/头布局，不再次解码；Final 另验证实际封装输出。"""
    if len(inputs) != 1:
        raise fail("AUDIO_BINDING", "普通工作源必须显式绑定一个音频载体")
    item, binding = inputs[0], gate.audio_bindings[0]
    if item.kind != "MediaFile" or item.artifact_id != binding.artifact_id or item.ordinal != 0:
        raise fail("AUDIO_BINDING", "音频载体身份或顺序不符")
    before = file_stat(checked_file(item.path))
    if before != gate.audio_source_stat:
        raise fail("AUDIO_BINDING", "音频源在扫描后变化")
    header = read_header(item.path, progress=progress)
    streams = [h for h in header["streams"] if h.get("codec_type") == "audio"]
    if len(streams) != len(binding.tracks):
        raise fail("AUDIO_BINDING", "音频布局改变；不能默默丢弃或新增音轨")
    for actual, track in zip(streams, binding.tracks, strict=True):
        if (
            actual.get("index") != track.stream_index
            or actual.get("codec_name") != track.codec
            or int(actual.get("sample_rate", 0)) != track.sample_rate
            or actual.get("channels") != track.channels
            or actual.get("channel_layout", "unknown") != track.channel_layout
        ):
            raise fail("AUDIO_BINDING", "音轨属性与当前准入不符")
    if file_stat(item.path) != before:
        raise fail("AUDIO_BINDING", "轻量复核期间音频源变化")
