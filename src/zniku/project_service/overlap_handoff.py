"""将新重叠候选的正式参数与当前handoff投影为人类交付说明，不执行或接纳文件。

只展示本Run最新等待attempt；不从文件名猜角色，不证明模型实际使用。检查和显式Submit仍由Runtime负责。
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path, PureWindowsPath

from pydantic import ValidationError

from zniku.avenhance_v27.probe import Av27MediaError
from zniku.chapter_overlap.definitions import definition_role
from zniku.chapter_overlap.node_contracts import NodeContract, preflight
from zniku.runtime import Artifact, NodeRunState, Run, RunnerInput

from .models import ExternalHandoffContractProjection, HandoffContractField


def project_overlap_handoff_contracts(
    run: Run, artifacts: tuple[Artifact, ...]
) -> tuple[ExternalHandoffContractProjection, ...]:
    """候选声明与实际输入事实分开；精确数字只来自当前 handoff 的严格直接输入合同。"""

    nodes = {node.node_id: node for node in run.graph_snapshot.nodes}
    definitions = {(d.type_id, d.version): d for d in run.definitions_snapshot}
    known = {item.artifact_id: item for item in artifacts}
    latest: dict[str, int] = {}
    for attempt in run.node_runs:
        latest[attempt.node_id] = max(latest.get(attempt.node_id, 0), attempt.attempt)
    result: list[ExternalHandoffContractProjection] = []
    for attempt in run.node_runs:
        node = nodes.get(attempt.node_id)
        handoff = attempt.external_handoff
        if (
            node is None
            or handoff is None
            or attempt.state is not NodeRunState.WAITING_EXTERNAL
            or attempt.attempt != latest[attempt.node_id]
        ):
            continue
        definition = definitions.get((node.type_id, node.definition_version))
        role = None if definition is None else definition_role(definition)
        if role not in {"enhancement", "fi"} or attempt.definition_version != "0.3.2":
            continue
        video: Artifact | None = None
        contract: NodeContract | None = None
        if (
            attempt.input_artifact_ids == handoff.input_artifact_ids
            and len(attempt.input_artifact_ids) == 1
        ):
            candidate = known.get(attempt.input_artifact_ids[0])
            if candidate is not None and candidate.kind == "VideoFile":
                video = candidate
                direct = RunnerInput(
                    "video",
                    candidate.artifact_id,
                    candidate.kind,
                    Path(candidate.path),
                    producer_node_run_id=candidate.producer_node_run_id,
                    producer_port_id=candidate.producer_port_id,
                    artifact_ordinal=candidate.ordinal,
                    frame_range=candidate.frame_range,
                    media_info=candidate.media_info,
                    size=candidate.size,
                    mtime_ns=candidate.mtime_ns,
                )
                with suppress(ValidationError, Av27MediaError, ValueError):
                    # 这两类 preflight 只读参数/metadata，不会走 Split/Final 的 gate 文件读取分支。
                    contract = preflight(role, (direct,), node.parameters)
        fi = role == "fi"
        rows = [
            ("处理阶段", "带上下文的章节FI" if fi else "正式分叶增强"),
            (
                "提交规则",
                "选择处理好的文件或复制到本任务专属收件目录；检查通过后显式提交，不会自动推进",
            ),
            ("文件保留", "输入及外部原始产物保存在工程工作数据目录，不自动清除或原地裁边"),
        ]
        targets = tuple(
            PureWindowsPath(target.path).name
            for target in handoff.output_targets
            if target.port_id == "video"
        )
        if len(targets) == 1:
            rows.append(("本次冻结收件文件", targets[0]))
        if fi:
            rows.extend(
                [
                    ("软件版本(候选声明)", "v1.0"),
                    ("模型(候选声明)", "Aion"),
                    (
                        "验收状态",
                        "pending_real_acceptance：待真实验收；文件不能证明模型或偶数输入相位",
                    ),
                    (
                        "请处理的输入",
                        "fi-input.mov是含邻章真实增强帧的工作输入；不要改用正式章或旧FI输入",
                    ),
                    ("原始输出", "交付fi-raw.mov，不自行裁边；后续自动节点另存正式fi.mov"),
                ]
            )
        if contract is None:
            rows.append(
                ("精确输入合同", "不可用：当前 Artifact 缺失、来源不符或参数陈旧；不猜测帧数与帧率")
            )
        else:
            expected = contract.outputs[0].metadata
            rows.extend(
                [
                    ("外部输入帧数", str(contract.input_metadata[0].frame_count)),
                    ("原始输出帧数", str(expected.frame_count)),
                    ("输出帧率", expected.frame_rate),
                    ("输出尺寸", f"{expected.geometry.width} x {expected.geometry.height}"),
                    ("输出媒体", "MOV / ProRes 422 HQ / yuv422p10le"),
                ]
            )
            if fi and expected.chapter is not None and expected.context is not None:
                rows.extend(
                    [
                        (
                            "正式章帧数",
                            str(expected.chapter.end_frame - expected.chapter.start_frame),
                        ),
                        ("裁后正式帧数", str(expected.context.cropped_frame_count)),
                        ("相位假设", "偶数输出位置对应输入帧；真实工具行为留待 Phase 5 验收"),
                    ]
                )
        result.append(
            ExternalHandoffContractProjection(
                node_run_id=attempt.node_run_id,
                handoff_id=handoff.handoff_id,
                input_artifact_id=None if video is None else video.artifact_id,
                title="重叠FI候选交付要求" if fi else "逐叶增强交付要求",
                fields=tuple(
                    HandoffContractField(label=label, value=value) for label, value in rows
                ),
            )
        )
    return tuple(result)
