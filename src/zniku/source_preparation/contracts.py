"""校验诊断、准入与直接输入间的局部绑定。

读回的 JSON 不是可信凭据；身份、stat、命名空间与当前输入仍需一致。外部修复必须重新
完整比较媒体，不能提交第三方报告或通过修改 DataFile 来略过验证。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import BaseModel

from zniku.runtime import RunnerInput
from zniku.runtime.progress import ProgressReporter

from .inspection import checked_file, file_stat, observe_audio, read_header
from .models import NAMESPACE, DiagnosticReport, SourceGate
from .process import fail

MAX_REPORT_BYTES = 256 * 1024


def read_document[M: BaseModel](path: Path, model: type[M]) -> M:
    """有界、拒重复键/NaN/未知字段读取普通节点 DataFile。"""
    if not path.is_file() or path.is_symlink() or not 0 < path.stat().st_size <= MAX_REPORT_BYTES:
        raise fail("REPORT_INVALID", "节点报告缺失、链接或超过预算")
    with path.open("rb") as stream:
        raw = stream.read(MAX_REPORT_BYTES + 1)
    if len(raw) > MAX_REPORT_BYTES:
        raise fail("REPORT_INVALID", "节点报告读取时超过预算")

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ValueError("重复 JSON 字段")
            result[key] = value
        return result

    try:
        json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("非标准 JSON 常量")),
        )
        return model.model_validate_json(raw)
    except (ValueError, TypeError) as exc:
        raise fail("REPORT_INVALID", "报告不符合严格 Schema") from exc


def namespace(item: RunnerInput) -> Mapping[str, object]:
    """读取局部命名空间；未知或缺失的输入默认拒绝。"""
    value = item.media_info.get(NAMESPACE)
    if not isinstance(value, Mapping):
        raise fail("INPUT_BINDING", "直接输入缺少本版本局部 metadata")
    return value


def read_diagnosis(item: RunnerInput) -> DiagnosticReport:
    """读取已登记诊断并核对普通 producer metadata，不查询任何全局索引。"""
    if item.kind != "DataFile":
        raise fail("INPUT_BINDING", "诊断输入必须是 DataFile")
    report = read_document(item.path, DiagnosticReport)
    data = namespace(item)
    if (
        data.get("role") != "diagnosis"
        or data.get("original_media_artifact_id") != (report.original_media_artifact_id)
        or _plain(data.get("report")) != report.model_dump(mode="json")
    ):
        raise fail("INPUT_BINDING", "诊断来源绑定不一致")
    return report


def read_report_artifact(path: Path) -> DiagnosticReport:
    """提供只读服务摘要入口；调用方仍须核对工程当前 Artifact/Run 归属。"""
    return read_document(path, DiagnosticReport)


def check_diagnosis(original: RunnerInput, diagnosis: RunnerInput) -> DiagnosticReport:
    """核对当前原件身份与 stat，不能把其他原件的完整报告用于本输入。"""
    report = read_diagnosis(diagnosis)
    checked_file(original.path)
    if original.kind != "MediaFile" or report.original_media_artifact_id != original.artifact_id:
        raise fail("INPUT_BINDING", "诊断不属于直接原件输入")
    if report.final_stat != file_stat(original.path):
        raise fail("SOURCE_CHANGED", "原件在诊断后改变，请重新检查")
    return report


def read_gate(item: RunnerInput) -> SourceGate:
    """只消费 gate 本身和已登记 metadata，不预先猜测参考视频输出身份。"""
    if item.kind != "DataFile":
        raise fail("INPUT_BINDING", "准入输入必须为 DataFile")
    gate = read_document(item.path, SourceGate)
    data = namespace(item)
    if data.get("role") != "admission" or json.loads(
        json.dumps(_plain(data.get("admission")))
    ) != gate.model_dump(mode="json"):
        raise fail("INPUT_BINDING", "准入文件与登记的局部绑定不一致")
    return gate


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    return value


def check_reference_binding(video: RunnerInput, gate: RunnerInput) -> SourceGate:
    """校验真实已登记 VideoFile 引用；绝不以 reference_media UUID 代替 video UUID。"""
    result = read_gate(gate)
    if video.kind != "VideoFile":
        raise fail("INPUT_BINDING", "参考视频输入类型不符")
    data = namespace(video)
    if data.get("role") != "reference" or _plain(data.get("admission")) != result.model_dump(
        mode="json"
    ):
        raise fail("INPUT_BINDING", "工作参考视频与准入不匹配")
    return result


def validate_audio_sources(
    gate: SourceGate, inputs: Sequence[RunnerInput], *, progress: ProgressReporter | None = None
) -> None:
    """核对当前音轨载体、顺序及有效样本；不要求载体视频通过 CFR。"""
    if len(inputs) != len(gate.audio_bindings):
        raise fail("AUDIO_BINDING", "音频载体数量与准入不同")
    for item, binding in zip(inputs, gate.audio_bindings, strict=True):
        if (
            item.kind != "MediaFile"
            or item.artifact_id != binding.artifact_id
            or (item.ordinal != binding.ordinal)
        ):
            raise fail("AUDIO_BINDING", "音频载体身份或 ordinal 与准入不同")
        header = read_header(item.path, progress=progress)
        audio = [stream for stream in header["streams"] if stream.get("codec_type") == "audio"]
        if len(audio) != len(binding.tracks):
            raise fail("AUDIO_BINDING", "音轨数量改变，不能默默丢弃或补入音轨")
        for stream, track in zip(audio, binding.tracks, strict=True):
            actual = observe_audio(item.path, stream, progress=progress)
            keys = (
                "stream_index",
                "codec",
                "sample_rate",
                "channels",
                "channel_layout",
                "sample_count",
                "start_time",
                "end_time",
            )
            if any(getattr(actual, key) != getattr(track, key) for key in keys) or (
                actual.skip_samples
                or actual.discard_padding
                or actual.missing_pts
                or actual.discontinuities
                or actual.decode_errors
            ):
                raise fail("AUDIO_BINDING", "当前音轨不再符合已绑定的内容时序事实")
