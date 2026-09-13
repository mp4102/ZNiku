"""将已接纳的单一时间轴展开为可编辑普通 Graph，不创建媒体或第二张运行权威。

只复用既有 Source/Admission/MR preparation 和最终出版规则。所有新处理节点采用0.3.2身份；
相邻增强章通过显式有序边供给上下文，旧FI结果不会被隐式采用。外部候选能力始终标为待真实验收。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, StringConstraints, field_validator

from zniku.avenhance_v27.template import (
    Av27TemplateError,
    PreparationBinding,
    ProgramEncodeDeclaration,
    PublicationRequest,
    _source_facts,
    canonical_publication_target,
)
from zniku.graph import Edge, Graph, GraphValidator, NodeDefinition, NodeInstance, UiPosition
from zniku.media import output_file_definition
from zniku.project import Project, ProjectSnapshot

from . import AdmittedTimeline, ChapterLeafPlan, ChapterSettings, plan_chapters_and_leaves
from .context import (
    ExperimentalContextPlan,
    ExperimentalContextSettings,
    plan_experimental_contexts,
)
from .definitions import (
    atomic_split_definition,
    enhancement_definition,
    fi_context_definition,
    fi_crop_definition,
    final_mux_definition,
    frame_interpolation_definition,
    merge_video_definition,
    program_encode_definition,
)
from .models import ChapterModel
from .node_contracts import CandidateFiProfile


class OverlapEnhancement(ChapterModel):
    """新wire允许响应中明确的空模型版本；不修改旧请求null拒绝语义。"""

    model_name: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    model_version: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None
    actual_scale_factor: Annotated[int, Field(ge=1, le=8)] = 1

    @field_validator("model_name", "model_version")
    @classmethod
    def text(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or value.strip() != value):
            raise ValueError("模型声明不能空白或含边界空白")
        return value


class OverlapProcessing(ChapterModel):
    """唯一处理设置；有效默认值不隐藏必填项，也不证明外部软件能力。"""

    settings: ChapterSettings = Field(default_factory=ChapterSettings)
    enhancement: OverlapEnhancement
    fi_profile: CandidateFiProfile = Field(default_factory=CandidateFiProfile)
    program_encode: ProgramEncodeDeclaration


class OverlapPublication(ChapterModel):
    """只读出版目标预览；目录实际创建仍由受控Output节点执行。"""

    output_target_path: str
    output_directory_to_create: str | None = None


class OverlapBuild(ChapterModel):
    """一次纯Graph展开的返回值，不持久化为ExecutionPlan或独立权威。"""

    project: Project
    definitions: tuple[NodeDefinition, ...]
    plan: ChapterLeafPlan
    contexts: ExperimentalContextPlan
    publication: OverlapPublication


def build_overlap(
    current: ProjectSnapshot,
    binding: PreparationBinding,
    processing: OverlapProcessing,
    publication: PublicationRequest,
) -> OverlapBuild:
    """仅扩展严格preparation；所有来源、区间、媒体尺寸均由已登记Artifact派生。"""

    if current.project.project_id != binding.project_id:
        raise Av27TemplateError("E_OVERLAP_PROJECT", "分析结果不属于当前工程")
    if binding.source_mode != "program" or len(binding.sources) != 1:
        raise Av27TemplateError("E_OVERLAP_SOURCE_MODE", "重叠候选只支持单一program时间轴")
    source = _source_facts(binding)[0]
    timeline = AdmittedTimeline(
        artifact_id=source.effective_artifact.artifact_id,
        frame_count=source.frame_count,
        frame_rate=str(source.frame_rate.numerator) + "/" + str(source.frame_rate.denominator),
    )
    plan = plan_chapters_and_leaves(timeline, processing.settings)
    profile = processing.fi_profile
    contexts = plan_experimental_contexts(
        plan,
        ExperimentalContextSettings(
            left_context_frames=profile.left_context_frames,
            right_context_frames=profile.right_context_frames,
            minimum_input_frames=profile.minimum_input_frames,
        ),
    )
    source_data: dict[str, Any] = {
        "effective_video_artifact_id": timeline.artifact_id,
        "admission_artifact_id": binding.admission_artifact.artifact_id,
        "source_media_artifact_id": source.source_artifact.artifact_id,
        "frame_count": timeline.frame_count,
        "frame_rate": timeline.frame_rate,
    }
    scale = processing.enhancement.actual_scale_factor
    # 新Split继承已验收的16:9→1080p FFV1基底；Enhancement倍率作用于Split，不能误乘原片尺寸。
    width, height = 1920, 1080
    input_geometry = {"width": width, "height": height, "sample_aspect_ratio": "1/1"}
    output_geometry = {
        "width": width * scale,
        "height": height * scale,
        "sample_aspect_ratio": "1/1",
    }
    target = canonical_publication_target(
        publication,
        mr_mode=binding.mr_mode,
        final_frame_rate=source.frame_rate * 2,
        height=height * scale,
    )
    protected = list(
        dict.fromkeys((str(source.source_artifact.path), str(source.effective_artifact.path)))
    )
    for path in map(Path, protected):
        if target == path.resolve(strict=False) or (
            target.exists() and path.exists() and target.samefile(path)
        ):
            raise Av27TemplateError("E_OVERLAP_OUTPUT_SOURCE", "成品目标不得覆盖源媒体或有效视频")

    nodes = list(current.project.graph.nodes)
    edges = list(current.project.graph.edges)
    definitions = list(current.definitions)
    definition_keys = {(item.type_id, item.version) for item in definitions}
    enhancement_def = enhancement_definition()
    merge_def = merge_video_definition()
    context_def = fi_context_definition()
    fi_def = frame_interpolation_definition()
    crop_def = fi_crop_definition()

    def node(
        identity: str, definition: NodeDefinition, params: dict[str, Any], x: int, y: int
    ) -> str:
        nodes.append(
            NodeInstance(
                node_id=identity,
                type_id=definition.type_id,
                definition_version=definition.version,
                parameters=params,
                ui_position=UiPosition(x=x, y=y),
            )
        )
        key = (definition.type_id, definition.version)
        if key not in definition_keys:
            definitions.append(definition)
            definition_keys.add(key)
        return identity

    def edge(start: str, out: str, end: str, port: str, ordinal: int | None = None) -> None:
        edges.append(
            Edge(
                source_node_id=start,
                source_port_id=out,
                target_node_id=end,
                target_port_id=port,
                ordinal=ordinal,
            )
        )

    split = node(
        "overlap.split",
        atomic_split_definition(plan.leaf_count),
        {
            "plan": plan.model_dump(mode="json"),
            "admission_artifact_id": binding.admission_artifact.artifact_id,
            "source_media_artifact_id": source.source_artifact.artifact_id,
        },
        680,
        80,
    )
    edge(source.mr_node_id or source.node_id, "video", split, "videos", 0)
    edge(binding.admission_node_id, "gate", split, "gate")
    merges: list[str] = []
    crops: list[str] = []
    chapter_y = 80
    for chapter in plan.chapters:
        chapter_data = {
            "chapter_id": chapter.chapter_id,
            "ordinal": chapter.ordinal,
            "count": plan.chapter_count,
            "start_frame": chapter.start_frame,
            "end_frame": chapter.end_frame,
        }
        y = chapter_y
        chapter_y += max(300, len(chapter.leaves) * 180)
        merge = node(
            f"overlap.merge.{chapter.label}",
            merge_def,
            {"source": source_data, "chapter": chapter_data},
            1280,
            y,
        )
        merges.append(merge)
        for leaf in chapter.leaves:
            enhancement = node(
                f"overlap.enhance.{chapter.label}.{leaf.ordinal + 1:03d}",
                enhancement_def,
                {
                    "source": source_data,
                    "chapter": chapter_data,
                    "leaf": {
                        "leaf_id": leaf.leaf_id,
                        "global_ordinal": leaf.global_ordinal,
                        "ordinal": leaf.ordinal,
                        "count": len(chapter.leaves),
                        "start_frame": leaf.start_frame,
                        "end_frame": leaf.end_frame,
                    },
                    "expected_input_geometry": input_geometry,
                    "expected_output_geometry": output_geometry,
                    **processing.enhancement.model_dump(mode="json", exclude_none=True),
                },
                980,
                y + leaf.ordinal * 180,
            )
            edge(split, leaf.leaf_id, enhancement, "video")
            edge(enhancement, "video", merge, "videos", leaf.ordinal)
        common = {
            "source": source_data,
            "chapter": chapter_data,
            "fi_profile": profile.model_dump(mode="json"),
        }
        context = node(f"overlap.context.{chapter.label}", context_def, common, 1580, y)
        fi = node(f"overlap.fi.{chapter.label}", fi_def, common, 1880, y)
        crop = node(f"overlap.crop.{chapter.label}", crop_def, common, 2180, y)
        edge(context, "video", fi, "video")
        edge(fi, "video", crop, "video")
        crops.append(crop)
    for chapter, context_plan in zip(plan.chapters, contexts.chapters, strict=True):
        for ordinal, part in enumerate(context_plan.sources):
            edge(
                merges[part.chapter_ordinal],
                "video",
                f"overlap.context.{chapter.label}",
                "chapters",
                ordinal,
            )
    program = node(
        "overlap.program",
        program_encode_definition(),
        {
            "source": source_data,
            "chapter_count": plan.chapter_count,
            "encoder": processing.program_encode.encoder,
        },
        2480,
        80,
    )
    for ordinal, crop in enumerate(crops):
        edge(crop, "video", program, "chapters", ordinal)
    final = node(
        "overlap.final",
        final_mux_definition(),
        {"source": source_data, "mr_mode": binding.mr_mode},
        2780,
        80,
    )
    edge(program, "video", final, "video")
    edge(source.node_id, "source_media", final, "sources", 0)
    edge(binding.admission_node_id, "gate", final, "gate")
    output = node(
        "output",
        output_file_definition("MediaFile"),
        {
            "mode": "copy",
            "target_path": str(target),
            "overwrite": publication.overwrite,
            "output_root": str(Path(publication.output_root).resolve(strict=True)),
            "create_parent": publication.layout == "title_subdirectory",
            "protected_paths": protected,
        },
        3080,
        80,
    )
    edge(final, "media", output, "in")
    graph = Graph(nodes=tuple(nodes), edges=tuple(edges))
    GraphValidator(definitions).validate(graph)
    return OverlapBuild(
        project=Project(
            project_id=current.project.project_id, name=current.project.name, graph=graph
        ),
        definitions=tuple(definitions),
        plan=plan,
        contexts=contexts,
        publication=OverlapPublication(
            output_target_path=str(target),
            output_directory_to_create=str(target.parent) if not target.parent.exists() else None,
        ),
    )
