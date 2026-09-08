"""只读投影 AV27 人工节点的交付合同，避免操作者从参数 JSON 猜测输出要求。

只消费请求 Run 的 snapshot 和已登记 Artifact metadata，不 probe、不写文件、不改变 Runtime。
缺失或非法 metadata 明确显示不可用；显示行不是 validator、媒体规划或新的运行权威。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import PureWindowsPath

from zniku.avenhance_v27.definitions import (
    AV27_NODE_VERSION,
    ENHANCEMENT_TYPE_ID,
    FRAME_INTERPOLATION_TYPE_ID,
    MOSAIC_RESTORATION_TYPE_ID,
)
from zniku.avenhance_v27.probe import (
    Av27MediaError,
    canonical_fraction,
    metadata_frame_count,
    metadata_rate,
    namespace_from_media_info,
)
from zniku.runtime import Artifact, NodeRunState, Run

from .models import ExternalHandoffContractProjection, HandoffContractField

_STAGES = {
    MOSAIC_RESTORATION_TYPE_ID: "Mosaic Restoration",
    ENHANCEMENT_TYPE_ID: "Enhancement",
    FRAME_INTERPOLATION_TYPE_ID: "Frame Interpolation",
}
_UNAVAILABLE = "不可用(已登记 metadata 或 snapshot 缺失/非法；不猜测)"


def _text(value: object) -> str:
    if value is None or value == "":
        return _UNAVAILABLE
    if isinstance(value, str | int | float | bool):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def project_av27_handoff_contracts(
    run: Run, artifacts: tuple[Artifact, ...]
) -> tuple[ExternalHandoffContractProjection, ...]:
    """只为本 Run 最新 waiting AV27 attempt 生成纯文本合同；历史/generic 不冒认。"""

    nodes = {item.node_id: item for item in run.graph_snapshot.nodes}
    artifacts_by_id = {item.artifact_id: item for item in artifacts}
    latest_attempts: dict[str, int] = {}
    for item in run.node_runs:
        latest_attempts[item.node_id] = max(latest_attempts.get(item.node_id, 0), item.attempt)
    result: list[ExternalHandoffContractProjection] = []
    for node_run in run.node_runs:
        handoff = node_run.external_handoff
        node = nodes.get(node_run.node_id)
        if (
            node_run.state is not NodeRunState.WAITING_EXTERNAL
            or node_run.attempt != latest_attempts[node_run.node_id]
            or handoff is None
            or node is None
            or node.type_id not in _STAGES
            or node.definition_version != AV27_NODE_VERSION
        ):
            continue
        videos = tuple(
            artifacts_by_id[identity]
            for identity in node_run.input_artifact_ids
            if identity in artifacts_by_id and artifacts_by_id[identity].kind == "VideoFile"
        )
        video = videos[0] if len(videos) == 1 else None
        namespace: Mapping[str, object] = {}
        input_count: int | None = None
        input_rate: str | None = None
        doubled_rate: str | None = None
        if video is not None:
            try:
                namespace = namespace_from_media_info(video.media_info)
                input_count = metadata_frame_count(video.media_info)
                rate = metadata_rate(video.media_info)
                input_rate = canonical_fraction(rate)
                doubled_rate = canonical_fraction(rate * 2)
            except Av27MediaError:
                # 不凭 duration、路径或当前 Graph 补造合同；validator 仍是 Submit 的唯一验收边界。
                pass
        params = node.parameters
        is_mr = node.type_id == MOSAIC_RESTORATION_TYPE_ID
        is_fi = node.type_id == FRAME_INTERPOLATION_TYPE_ID
        output_count = (
            input_count
            if is_mr
            else params.get("expected_output_frames" if is_fi else "expected_frames")
        )
        geometry = (
            namespace.get("geometry")
            if is_mr
            else params.get("expected_geometry" if is_fi else "expected_output_geometry")
        )
        signal = params.get("expected_signal") if is_fi else namespace.get("signal")
        version = params.get("model_version")
        output_names = tuple(
            PureWindowsPath(target.path).name
            for target in handoff.output_targets
            if target.port_id == "video"
        )
        output_name = output_names[0] if len(output_names) == 1 else _UNAVAILABLE
        rows: list[tuple[str, object]] = [
            ("Model name(操作者声明)", params.get("model_name")),
            (
                "Model version(操作者声明)",
                version if version is not None or is_mr else "未声明(可选)",
            ),
            ("输入 exact N", input_count),
            ("输出 exact N", output_count),
            ("帧关系", "N → 2N-1；明确拒绝 2N" if is_fi else "N → N"),
            ("输入 canonical FPS", input_rate),
            ("输出 canonical FPS", doubled_rate if is_fi else input_rate),
            ("输出 geometry", geometry),
            ("输出 signal", signal),
            ("SAR / field / rotation", "1:1(或缺失) / progressive / 0°"),
            (
                "输出容器与名称",
                f"{'Matroska' if is_mr else 'MOV'} · {output_name}",
            ),
            (
                "视频 codec / pixel format",
                "保持媒体合同；不强制 MR codec" if is_mr else "ProRes 422 HQ / yuv422p10le",
            ),
            (
                "允许流",
                "唯一 video；可附带 audio，不可作 Final audio 来源"
                if is_mr
                else "唯一 video-only；禁止 audio/subtitle/data/chapters"
                if is_fi
                else "唯一 video；可附带 audio/subtitle/timecode；Merge 仅消费 video",
            ),
        ]
        if not is_mr and not is_fi:
            rows.append(("Actual scale factor", params.get("actual_scale_factor", 1)))
        if is_fi:
            rows.append(("FPS 容差", "observed FPS 与 source exact FPS x 2 的相对误差 ≤ 2e-6"))
        rows.append(("验收边界", "只验证媒体合同，不证明实际模型或画质；失败后新 attempt 从头处理"))
        result.append(
            ExternalHandoffContractProjection(
                node_run_id=node_run.node_run_id,
                handoff_id=handoff.handoff_id,
                input_artifact_id=video.artifact_id if video is not None else None,
                title=f"{_STAGES[node.type_id]} 输出合同",
                fields=tuple(
                    HandoffContractField(label=label, value=_text(value)) for label, value in rows
                ),
            )
        )
    return tuple(result)
