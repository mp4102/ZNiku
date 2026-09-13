"""从新精确定义投影友好文件名和外部要求；展示不验收媒体、不改变任何运行身份。"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path, PureWindowsPath

from pydantic import ValidationError

from zniku.avenhance_v27.naming import _path, validate_media_basename
from zniku.avenhance_v27.probe import Av27MediaError
from zniku.avenhance_v27.validators import _metadata_contract
from zniku.graph import NodeDefinition, NodeInstance
from zniku.project.paths import validate_filename_component
from zniku.runtime import Artifact, NodeRunState, Run, RunnerInput
from zniku.runtime.runner import OutputPathSpec
from zniku.source_aligned.definitions import definition_role
from zniku.source_aligned.node_contracts import (
    PARAMETER_MODELS,
    ChapterParameters,
    EnhancementParameters,
    ExternalParameters,
    SplitParameters,
    preflight,
)

from .models import ExternalHandoffContractProjection, HandoffContractField


def source_aligned_output_paths(
    node: NodeInstance, definition: NodeDefinition, *, media_basename: str
) -> tuple[OutputPathSpec, ...]:
    """名称不影响验收；不完整自定义草稿回退，不将命名变成运行门禁。"""
    if (node.type_id, node.definition_version) != (definition.type_id, definition.version):
        return ()
    role = definition_role(definition)
    if role is None or role in {"program", "final"}:
        return ()
    try:
        model = ExternalParameters if role == "external" else PARAMETER_MODELS[role]
        params = model.model_validate(node.model_dump(mode="json")["parameters"], strict=True)
    except (ValidationError, Av27MediaError, ValueError):
        return ()
    if isinstance(params, ExternalParameters):
        basename = validate_media_basename(media_basename)
        filename = f"{basename}.RM.{params.declared_container}"
        validate_filename_component(filename)
        return (OutputPathSpec("video", filename),)
    if isinstance(params, SplitParameters):
        if params.plan.leaf_count != len(definition.output_ports):
            return ()
        return tuple(
            OutputPathSpec(
                leaf.leaf_id,
                _path(media_basename, chapter.ordinal, f"leaf-{leaf.ordinal + 1:04d}.mkv"),
            )
            for chapter in params.plan.chapters
            for leaf in chapter.leaves
        )
    if not isinstance(params, ChapterParameters):
        return ()
    suffix = {
        "merge": "enhancement.mov",
        "context": "enhancement.fi-input.mov",
        "fi": "enhancement.fi-raw.mov",
        "crop": "enhancement.fi.mov",
    }.get(role)
    if isinstance(params, EnhancementParameters):
        leaf, chapter = params.leaf, params.chapter
        if not chapter.start_frame <= leaf.start_frame < leaf.end_frame <= chapter.end_frame or (
            (leaf.ordinal == 0) != (leaf.start_frame == chapter.start_frame)
            or (leaf.ordinal == leaf.count - 1) != (leaf.end_frame == chapter.end_frame)
        ):
            return ()
        suffix = f"leaf-{leaf.ordinal + 1:04d}.enhancement.mov"
    return (
        ()
        if suffix is None
        else (OutputPathSpec("video", _path(media_basename, params.chapter.ordinal, suffix)),)
    )


def _direct(artifact: Artifact) -> RunnerInput:
    return RunnerInput(
        "video",
        artifact.artifact_id,
        artifact.kind,
        Path(artifact.path),
        producer_node_run_id=artifact.producer_node_run_id,
        producer_port_id=artifact.producer_port_id,
        artifact_ordinal=artifact.ordinal,
        frame_range=artifact.frame_range,
        media_info=artifact.media_info,
        size=artifact.size,
        mtime_ns=artifact.mtime_ns,
    )


def project_source_aligned_handoffs(
    run: Run, artifacts: tuple[Artifact, ...]
) -> tuple[ExternalHandoffContractProjection, ...]:
    """只读当前最新等待交接；精确数字缺失时不猜测，也不读取媒体或准入文件。"""
    nodes = {node.node_id: node for node in run.graph_snapshot.nodes}
    definitions = {(d.type_id, d.version): d for d in run.definitions_snapshot}
    known = {a.artifact_id: a for a in artifacts}
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
        if role not in {"external", "enhancement", "fi"}:
            continue
        title = {
            "external": "第0步外部修复交付要求",
            "enhancement": "逐叶增强交付要求",
            "fi": "上下文补帧交付要求",
        }[role]
        rows = [
            ("提交规则", "选择文件或复制到专属收件目录；检查通过后显式提交，文件出现不会自动推进"),
            ("文件保留", "原片、外部原件和历史结果保留；导入不转码、不改速、不自动补丢帧"),
        ]
        targets = [
            PureWindowsPath(t.path).name for t in handoff.output_targets if t.port_id == "video"
        ]
        if len(targets) == 1:
            rows.append(("本次交付文件", targets[0]))
        video = None
        if attempt.input_artifact_ids == handoff.input_artifact_ids:
            videos = [
                known[i]
                for i in attempt.input_artifact_ids
                if i in known and known[i].kind == "VideoFile"
            ]
            if len(videos) == 1:
                video = videos[0]
        facts: list[tuple[str, str]] = []
        with suppress(ValidationError, Av27MediaError, ValueError):
            if role == "external":
                params = ExternalParameters.model_validate(
                    node.model_dump(mode="json")["parameters"]
                )
                rows.extend(
                    [
                        (
                            "输出封装",
                            params.declared_container.upper() + "；必须与真实媒体封装一致",
                        ),
                        ("模型(操作者声明)", params.model_name),
                        ("帧序约定", "原片第k帧对应结果第k帧；不剪辑、不变速、不增删/重排帧"),
                        ("验证边界", "检查属性与时间轴，不证明逐帧画面、实际模型或主观同步"),
                        ("音频来源", "最终使用原片音频；外部文件的音频不会替代原音轨"),
                        (
                            "检查耗时",
                            "精确时间轴检查会顺序读取原片与结果；大文件可能较慢，当前检查不能中途取消。",
                        ),
                    ]
                )
                if (
                    video is not None
                    and video.artifact_id == params.source.original_video_artifact_id
                ):
                    media = _metadata_contract(_direct(video))
                    facts = [
                        ("输入与输出帧数", str(media.frame_count)),
                        ("精确帧率", str(media.frame_rate)),
                        ("输出尺寸", f"{media.geometry[0]} x {media.geometry[1]}"),
                    ]
            elif video is not None:
                contract = preflight(role, (_direct(video),), node.parameters)
                expected = contract.outputs[0].metadata
                facts = [
                    ("输入帧数", str(contract.input_metadata[0].frame_count)),
                    ("输出帧数", str(expected.frame_count)),
                    ("输出帧率", expected.frame_rate),
                    ("输出尺寸", f"{expected.geometry.width} x {expected.geometry.height}"),
                    ("输出媒体", "MOV / ProRes 422 HQ / yuv422p10le"),
                ]
        rows.extend(
            facts or [("精确输入合同", "不可用：请刷新工程并检查当前输入绑定，不猜测帧数与帧率")]
        )
        if role == "fi":
            rows.append(
                (
                    "补帧输入",
                    "使用带上下文fi-input，交付未裁边fi-raw；不要自行删帧。Aion声明不证明相位或画质。",
                )
            )
        result.append(
            ExternalHandoffContractProjection(
                node_run_id=attempt.node_run_id,
                handoff_id=handoff.handoff_id,
                input_artifact_id=None if video is None else video.artifact_id,
                title=title,
                fields=tuple(HandoffContractField(label=k, value=v) for k, v in rows),
            )
        )
    return tuple(result)
