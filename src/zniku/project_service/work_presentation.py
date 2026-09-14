"""普通工作源的可读目录及人工交付说明；只有完整 exact 身份匹配才展示专用帮助。"""

from pathlib import PureWindowsPath

from zniku.avenhance_v27.naming import validate_media_basename
from zniku.graph import NodeDefinition, NodeInstance
from zniku.prepared_source.work_definitions import definition_role as downstream_role
from zniku.project.paths import validate_filename_component
from zniku.runtime import Artifact, NodeRunState, Run
from zniku.runtime.runner import OutputPathSpec
from zniku.source_preparation.work_definitions import definition_role

from .models import ExternalHandoffContractProjection, HandoffContractField


def preparation_output_paths(
    node: NodeInstance, definition: NodeDefinition, *, media_basename: str
) -> tuple[OutputPathSpec, ...]:
    """命名持久新副本；Source 与 Admission 不因此复制媒体。"""
    if (node.type_id, node.definition_version) != (definition.type_id, definition.version):
        return ()
    role = definition_role(definition)
    if role not in {"builtin", "external"}:
        return ()
    basename = validate_media_basename(media_basename)
    extension = definition.type_id.rsplit(".", 1)[-1] if role == "external" else "mkv"
    suffix = "working-reference" if role == "external" else "working-retimed"
    filename = f"{basename}.{suffix}.{extension}"
    validate_filename_component(filename)
    return (OutputPathSpec("media", filename),)


def project_work_handoffs(
    run: Run, artifacts: tuple[Artifact, ...]
) -> tuple[ExternalHandoffContractProjection, ...]:
    """读取当前 handoff 与真实节点参数，不暗示外部结果已经验证。"""
    nodes = {n.node_id: n for n in run.graph_snapshot.nodes}
    definitions = {(d.type_id, d.version): d for d in run.definitions_snapshot}
    known = {a.artifact_id: a for a in artifacts}
    latest: dict[str, int] = {}
    for attempt in run.node_runs:
        latest[attempt.node_id] = max(latest.get(attempt.node_id, 0), attempt.attempt)
    result = []
    for attempt in run.node_runs:
        handoff = attempt.external_handoff
        if (
            handoff is None
            or attempt.state is not NodeRunState.WAITING_EXTERNAL
            or latest[attempt.node_id] != attempt.attempt
        ):
            continue
        node = nodes[attempt.node_id]
        definition = definitions[(node.type_id, node.definition_version)]
        reference = definition_role(definition) == "external"
        role = downstream_role(definition)
        if not reference and role not in {"external", "enhancement", "fi"}:
            continue
        rows = [
            ("提交方式", "复制到本任务收件目录，或选择处理好的文件导入；文件出现不会自动提交。"),
            ("原件保护", "只复制外部文件，不覆盖、改名或删除原件；历史工作数据不自动清除。"),
        ]
        targets = [
            PureWindowsPath(t.path).name
            for t in handoff.output_targets
            if t.port_id == ("media" if reference else "video")
        ]
        if len(targets) == 1:
            rows.append(("本次交付文件", targets[0]))
        if reference:
            title = "新的外部工作参考交付要求"
            rows.extend(
                [
                    (
                        "来源和变化",
                        "用户已明确允许采用新的参考；不要求逐像素等价，但须对来源和允许变化负责。",
                    ),
                    ("目标封装", definition.type_id.rsplit(".", 1)[-1].upper()),
                    ("目标精确帧率", str(node.parameters.get("target_frame_rate", "未设置"))),
                    (
                        "重新规划",
                        "按本文件实际 N/FPS/几何和工作解释重新规划；不能继承原件旧分章结果。",
                    ),
                    (
                        "音频来源",
                        "使用新参考自身音轨和相对视频起点；无音轨则无音频，不暗中沿用原件音频。",
                    ),
                    ("观察范围", "提交后检查当前处理器所需属性与视频帧时序；不是完整保内容审计。"),
                ]
            )
        else:
            title = {
                "external": "工作参考马赛克修复",
                "enhancement": "逐叶增强",
                "fi": "章节上下文补帧",
            }[str(role)] + "交付要求"
            rows.extend(
                [
                    ("时间和帧序", "严格遵守本任务声明的 N/FPS/几何；不自行增删帧、变速或重排。"),
                    (
                        "音频来源",
                        "成品使用工作准入显式绑定的音频载体，不采用 MR/增强/FI 文件附带的音频。",
                    ),
                ]
            )
            if role == "fi":
                rows.append(
                    ("裁边", "提交完整 fi-raw；不要自行裁边，下一节点按已规划上下文精确裁边。")
                )
        rows.append(("验收边界", "属性验收不证明实际模型、逐像素恒等或主观画质。"))
        inputs = [
            known[i]
            for i in attempt.input_artifact_ids
            if i in known and known[i].kind in {"MediaFile", "VideoFile"}
        ]
        result.append(
            ExternalHandoffContractProjection(
                node_run_id=attempt.node_run_id,
                handoff_id=handoff.handoff_id,
                input_artifact_id=inputs[0].artifact_id if len(inputs) == 1 else None,
                title=title,
                fields=tuple(HandoffContractField(label=k, value=v) for k, v in rows),
            )
        )
    return tuple(result)
