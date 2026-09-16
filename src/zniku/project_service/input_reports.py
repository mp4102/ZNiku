"""只读投影外部任务的已登记数据输入；报告不是待处理视频或新的验收权威。

仅访问当前交接引用的普通文件，限制数量和字节；变化、损坏或不可读时保留错误说明。
不重写历史 JSON，不运行算法，不因报告展示改变 Artifact、Run 或 stale。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from zniku.runtime import Artifact, Run

from .handoff_import import _safe_path
from .host_bridge import HostBridgeFailure
from .models import ArtifactInputReport, HandoffContractField


def project_input_reports(
    run: Run, artifacts: tuple[Artifact, ...]
) -> tuple[ArtifactInputReport, ...]:
    """返回最多十六份、每份至多256KiB的输入报告，拒绝将任意路径作为读取入口。"""
    wanted = {
        identity
        for attempt in run.node_runs
        if attempt.state.value == "waiting_external" and attempt.external_handoff is not None
        for identity in attempt.external_handoff.input_artifact_ids
    }
    results: list[ArtifactInputReport] = []
    for artifact in artifacts:
        if artifact.artifact_id not in wanted or artifact.kind != "DataFile" or len(results) >= 16:
            continue
        title = "分析 / 参考报告"
        try:
            path = _safe_path(Path(artifact.path))
            before = path.stat()
            if not path.is_file() or before.st_size > 256 * 1024:
                raise ValueError("报告不是普通小型 JSON 文件，请在高级详情查看路径。")
            if (before.st_size, before.st_mtime_ns) != (artifact.size, artifact.mtime_ns):
                raise ValueError("报告文件已变化，不展示过期的登记信息。")
            with path.open("rb") as stream:
                raw = stream.read(256 * 1024 + 1)
            after = path.stat()
            if len(raw) > 256 * 1024 or (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
                raise ValueError("读取期间报告变化，请重新检查。")
            document = json.loads(raw)
            if not isinstance(document, dict):
                raise ValueError("该参考文件不是 JSON 对象。")
            fields: list[HandoffContractField] = []
            if document.get("schema") == "zniku.avenhance.v27.admission/1":
                title = "素材分析报告"
                fields.append(
                    HandoffContractField(label="用途", value="记录已准入素材；无需交给外部软件处理")
                )
                sources = document.get("sources")
                if isinstance(sources, list):
                    fields.append(HandoffContractField(label="素材数量", value=str(len(sources))))
                    if len(sources) == 1 and isinstance(sources[0], dict):
                        source = sources[0]
                        for key, label in (("frame_count", "总帧数"), ("frame_rate", "精确帧率")):
                            if key in source:
                                fields.append(
                                    HandoffContractField(label=label, value=str(source[key])[:4096])
                                )
                        geometry = source.get("geometry")
                        if isinstance(geometry, dict):
                            width = geometry.get("width", "未知")
                            height = geometry.get("height", "未知")
                            fields.append(
                                HandoffContractField(
                                    label="画面尺寸",
                                    value=f"{width} x {height}"[:4096],
                                )
                            )
                        tracks = source.get("audio_tracks")
                        if isinstance(tracks, list):
                            fields.append(
                                HandoffContractField(label="音轨数量", value=str(len(tracks)))
                            )
            results.append(
                ArtifactInputReport(
                    artifact_id=artifact.artifact_id,
                    title=title,
                    fields=tuple(fields),
                    document=cast(dict[str, JsonValue], document),
                )
            )
        except (OSError, ValueError, RecursionError, HostBridgeFailure) as error:
            results.append(
                ArtifactInputReport(
                    artifact_id=artifact.artifact_id, title=title, message=str(error)[:1000]
                )
            )
    return tuple(results)
