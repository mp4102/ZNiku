"""从历史 Run 的正式声明生成通用人工交付清单，不执行媒体检查或新增约束。

AV27 使用专用节点合同投影；普通媒体 Transform 展示已有可选约束，其余插件明确保留 validator
边界。所有条目都是纯文本帮助，Submit 仍以 Runner 的完整验证为准。
"""

from __future__ import annotations

import json

from zniku.media.definitions import VIDEO_TRANSFORM_VALIDATOR
from zniku.media.probe import MediaNodeError
from zniku.media.validators import transform_frame_relation
from zniku.runtime import Artifact, NodeRunState, Run

from .av27_handoff import project_av27_handoff_contracts
from .models import ExternalHandoffContractProjection, HandoffContractField


def project_handoff_contracts(
    run: Run, artifacts: tuple[Artifact, ...]
) -> tuple[ExternalHandoffContractProjection, ...]:
    """只投影最新 waiting attempt，未知插件不冒认 generic 或 AV27 合同。"""

    contracts = list(project_av27_handoff_contracts(run, artifacts))
    covered = {item.node_run_id for item in contracts}
    nodes = {item.node_id: item for item in run.graph_snapshot.nodes}
    definitions = {(item.type_id, item.version): item for item in run.definitions_snapshot}
    latest: dict[str, int] = {}
    for item in run.node_runs:
        latest[item.node_id] = max(latest.get(item.node_id, 0), item.attempt)
    for attempt in run.node_runs:
        handoff = attempt.external_handoff
        if (
            attempt.node_run_id in covered
            or attempt.state is not NodeRunState.WAITING_EXTERNAL
            or attempt.attempt != latest[attempt.node_id]
            or handoff is None
        ):
            continue
        node = nodes[attempt.node_id]
        definition = definitions[(node.type_id, node.definition_version)]
        rows = [
            (
                "基本检查",
                "所有目标文件存在且非空，并通过节点声明的检查；"
                "仅媒体输出需要识别声明的媒体流，非媒体输出不要求流识别。",
            ),
            (
                "目标文件",
                "、".join(port.data_type for port in definition.output_ports)[:4096]
                or "无声明输出",
            ),
            ("处理方式", "从完整输入开始处理；文件出现不等于检查通过，不会自动提交"),
        ]
        if definition.validator is not None and (
            definition.validator.adapter == VIDEO_TRANSFORM_VALIDATOR
        ):
            labels = {
                "tool": "工具(操作者声明)",
                "model": "模型(操作者声明)",
                "tool_version": "工具版本(操作者声明)",
                "expected_width": "输出宽度(像素)",
                "expected_height": "输出高度(像素)",
                "expected_frame_rate": "输出帧率",
            }
            rows.extend(
                (label, str(node.parameters[key]))
                for key, label in labels.items()
                if key in node.parameters
            )
            try:
                relation = transform_frame_relation(node.parameters, node.type_id)
            except MediaNodeError:
                # 第三方可以声明该 validator 却提供不同 Schema；展示不得冒充检查结论或阻断读取。
                relation = "unknown"
            rows.append(
                (
                    "帧数关系",
                    {
                        "any": "不额外限制输入/输出帧数关系",
                        "equal": "输出帧数等于输入帧数(N → N)",
                        "double": "输出帧数是输入的两倍(N → 2N)",
                    }.get(str(relation), "以正式节点检查为准"),
                )
            )
        else:
            rows.append(("附加检查", "以此节点声明的 validator 为准，不推测第三方媒体约束"))
            for name, value in tuple(node.parameters.items())[:16]:
                text = json.dumps(value, ensure_ascii=False, sort_keys=True)
                rows.append((f"节点声明：{name}"[:120], text[:4096] or "空值"))
        rows.append(("验收边界", "检查不证明实际使用的模型或主观画质；提交时会再次完整验证"))
        contracts.append(
            ExternalHandoffContractProjection(
                node_run_id=attempt.node_run_id,
                handoff_id=handoff.handoff_id,
                input_artifact_id=(
                    attempt.input_artifact_ids[0] if len(attempt.input_artifact_ids) == 1 else None
                ),
                title="外部处理交付要求",
                fields=tuple(
                    HandoffContractField(label=label, value=value[:4096] or "空值")
                    for label, value in rows
                ),
            )
        )
    return tuple(contracts)
