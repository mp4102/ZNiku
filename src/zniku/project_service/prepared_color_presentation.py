"""把准备及工作参考节点投影为可归档名称与人工交付说明，不执行媒体验证。

仅完整匹配正式 exact definition；名称和帮助不参与准入。未知参数保持通用提示，不能按
路径、节点别名或用户声明推断内容已通过验证。
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import PureWindowsPath

from pydantic import ValidationError

from zniku.avenhance_v27.naming import validate_media_basename
from zniku.graph import NodeDefinition, NodeInstance
from zniku.prepared_color.definitions import definition_role as downstream_role
from zniku.prepared_color.node_contracts import (
    PARAMETER_MODELS,
    ExternalParameters,
)
from zniku.project.paths import validate_filename_component
from zniku.runtime import Artifact, NodeRunState, Run
from zniku.runtime.runner import OutputPathSpec
from zniku.source_color.definitions import definition_role

from .models import ExternalHandoffContractProjection, HandoffContractField


def preparation_output_paths(
    node: NodeInstance, definition: NodeDefinition, *, media_basename: str
) -> tuple[OutputPathSpec, ...]:
    """只命名新副本；只读 Source/Admission 不因命名创建或复制媒体。"""
    if (node.type_id, node.definition_version) != (definition.type_id, definition.version):
        return ()
    role = definition_role(definition)
    if role not in {"builtin", "external"}:
        return ()
    basename = validate_media_basename(media_basename)
    extension = definition.type_id.rsplit(".", 1)[-1] if role == "external" else "mkv"
    suffix = "source-repaired" if role == "external" else "source-prepared"
    filename = f"{basename}.{suffix}.{extension}"
    validate_filename_component(filename)
    return (OutputPathSpec("media", filename),)


def project_prepared_handoffs(
    run: Run, artifacts: tuple[Artifact, ...]
) -> tuple[ExternalHandoffContractProjection, ...]:
    """只读取当前 snapshot 和登记数据，绝不在刷新页面时扫描原片。"""
    nodes = {item.node_id: item for item in run.graph_snapshot.nodes}
    definitions = {(d.type_id, d.version): d for d in run.definitions_snapshot}
    known = {item.artifact_id: item for item in artifacts}
    latest: dict[str, int] = {}
    for item in run.node_runs:
        latest[item.node_id] = max(latest.get(item.node_id, 0), item.attempt)
    results = []
    for attempt in run.node_runs:
        handoff = attempt.external_handoff
        if (
            handoff is None
            or attempt.state is not NodeRunState.WAITING_EXTERNAL
            or attempt.attempt != latest[attempt.node_id]
        ):
            continue
        node = nodes[attempt.node_id]
        definition = definitions[(node.type_id, node.definition_version)]
        repair = definition_role(definition) == "external"
        role = downstream_role(definition)
        if not repair and role not in {"external", "enhancement", "fi"}:
            continue
        rows = [
            ("提交方式", "复制到本任务收件目录，或选择处理好的文件导入；文件出现不会自动提交。"),
            ("原件保护", "导入只复制，不改名、覆盖或删除外部原件；不自动转码、不丢弃历史产物。"),
        ]
        target_port = "media" if repair else "video"
        targets = [
            PureWindowsPath(t.path).name for t in handoff.output_targets if t.port_id == target_port
        ]
        if len(targets) == 1:
            rows.append(("本次交付文件", targets[0]))
        if repair:
            title = "外部保内容素材修复交付要求"
            rows.extend(
                [
                    (
                        "输出封装",
                        definition.type_id.rsplit(".", 1)[-1].upper() + "；实体封装必须相符",
                    ),
                    ("目标精确帧率", str(node.parameters.get("target_frame_rate", "未设置"))),
                    (
                        "视频保留",
                        "完整展示帧数、帧序及解码像素保持一致；不裁剪、缩放、增强或有损重编码。",
                    ),
                    ("音频保留", "保留全部音轨和解码样本、轨道属性与音画关系；不能只对视频计帧。"),
                    ("检查方式", "显式提交后逐项完整验证；通过前不登记可用修复产物。"),
                    (
                        "内容已改变",
                        "请另建新工作源工程重新分析；不能继承此原件的旧规划或复用结果。",
                    ),
                ]
            )
        else:
            title = {
                "external": "外部工作参考马赛克修复",
                "enhancement": "逐叶增强",
                "fi": "章节上下文补帧",
            }[str(role)] + "交付要求"
            with suppress(ValidationError, ValueError):
                model = ExternalParameters if role == "external" else PARAMETER_MODELS[str(role)]
                params = model.model_validate(node.model_dump(mode="json")["parameters"])
                data = params.model_dump(mode="json")
                rows.append(("工作参考帧率", str(data["source"]["frame_rate"])))
                if role == "external":
                    rows.extend(
                        [
                            (
                                "帧序约定",
                                "工作参考第 k 帧对应 MR 结果第 k 帧；不剪辑、变速或增删重排帧。",
                            ),
                            ("音频来源", "成品使用显式原音频载体，不采用 MR 结果附带的音频。"),
                        ]
                    )
                elif role == "fi":
                    rows.append(
                        (
                            "补帧边界",
                            "使用 fi-input，提交未裁边 fi-raw；不要自行删帧。"
                            "之后由裁边节点精确处理。",
                        )
                    )
                rows.append(("质量边界", "属性验证不证明外部模型版本或主观画质。"))
        videos = [
            known[i]
            for i in attempt.input_artifact_ids
            if i in known and known[i].kind in {"MediaFile", "VideoFile"}
        ]
        results.append(
            ExternalHandoffContractProjection(
                node_run_id=attempt.node_run_id,
                handoff_id=handoff.handoff_id,
                input_artifact_id=videos[0].artifact_id if len(videos) == 1 else None,
                title=title,
                fields=tuple(HandoffContractField(label=k, value=v[:4096]) for k, v in rows),
            )
        )
    return tuple(results)
