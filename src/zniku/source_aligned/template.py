"""将原片分析与可选外部前处理展开为普通源对齐 Graph，不等待外部产物。

原片是唯一设计时坐标；未来有效视频只由直接边输入在节点执行时绑定。旧 0.3.2 模板和
旧 MR preparation 不作隐式迁移。此模块无媒体副作用，不创建 Run、Artifact 或输出目录。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, field_validator

from zniku.avenhance_v27.template import (
    Av27TemplateError,
    PreparationBinding,
    ProgramEncodeDeclaration,
    PublicationRequest,
    _source_facts,
    canonical_publication_target,
)
from zniku.chapter_overlap import (
    AdmittedTimeline,
    ChapterLeafPlan,
    ChapterSettings,
    plan_chapters_and_leaves,
)
from zniku.chapter_overlap.context import (
    ExperimentalContextPlan,
    ExperimentalContextSettings,
    plan_experimental_contexts,
)
from zniku.chapter_overlap.models import ChapterModel
from zniku.graph import Edge, Graph, GraphValidator, NodeDefinition, NodeInstance, UiPosition
from zniku.media import output_file_definition
from zniku.project import Project, ProjectSnapshot

from .definitions import (
    atomic_split_definition,
    enhancement_definition,
    external_definition,
    fi_context_definition,
    fi_crop_definition,
    final_mux_definition,
    frame_interpolation_definition,
    merge_video_definition,
    program_encode_definition,
)
from .node_contracts import CandidateFiProfile, DeclaredContainer, SourceExpectation


class SourceAlignedEnhancement(ChapterModel):
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


class SourceAlignedMrOff(ChapterModel):
    """默认不插入前处理节点，不改变原片分析。"""

    mode: Literal["off"] = "off"


class SourceAlignedMrExternal(ChapterModel):
    """操作者声明的源对齐外部前处理，不证明模型身份或逐帧内容。"""

    mode: Literal["external"]
    model_name: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    model_version: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None
    declared_container: DeclaredContainer = "mp4"
    operator_frame_order_confirmed: Literal[True]

    @field_validator("operator_frame_order_confirmed", mode="before")
    @classmethod
    def confirmed(cls, value: Any) -> Any:
        if value is not True:
            raise ValueError("必须明确确认外部处理保持原片帧序，不接受宽松布尔值")
        return value

    @field_validator("model_name", "model_version")
    @classmethod
    def text(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or value.strip() != value):
            raise ValueError("模型声明不能空白或含边界空白")
        return value


type SourceAlignedMr = Annotated[
    SourceAlignedMrOff | SourceAlignedMrExternal, Field(discriminator="mode")
]


class SourceAlignedProcessing(ChapterModel):
    """唯一处理设置；有效默认值不隐藏必填项，也不证明外部软件能力。"""

    mr: SourceAlignedMr = Field(default_factory=SourceAlignedMrOff)
    settings: ChapterSettings = Field(default_factory=ChapterSettings)
    enhancement: SourceAlignedEnhancement
    fi_profile: CandidateFiProfile = Field(default_factory=CandidateFiProfile)
    program_encode: ProgramEncodeDeclaration


class SourceAlignedPublication(ChapterModel):
    """只读出版目标预览；目录实际创建仍由受控Output节点执行。"""

    output_target_path: str
    output_directory_to_create: str | None = None


class SourceAlignedBuild(ChapterModel):
    """一次纯Graph展开的返回值，不持久化为ExecutionPlan或独立权威。"""

    project: Project
    definitions: tuple[NodeDefinition, ...]
    plan: ChapterLeafPlan
    contexts: ExperimentalContextPlan
    publication: SourceAlignedPublication


def build_source_aligned(
    current: ProjectSnapshot,
    binding: PreparationBinding,
    processing: SourceAlignedProcessing,
    publication: PublicationRequest,
    *,
    definition_factory: Callable[[str, int], NodeDefinition] | None = None,
    external_factory: Callable[[DeclaredContainer], NodeDefinition] = external_definition,
    publication_target: Callable[..., Path] = canonical_publication_target,
    batch_enhancement: bool = False,
    final_publication_factory: Callable[[], NodeDefinition] | None = None,
) -> SourceAlignedBuild:
    """仅扩展严格preparation；所有来源、区间、媒体尺寸均由已登记Artifact派生。"""

    if current.project.project_id != binding.project_id:
        raise Av27TemplateError("E_SOURCE_ALIGNED_PROJECT", "分析结果不属于当前工程")
    if binding.source_mode != "program" or len(binding.sources) != 1:
        raise Av27TemplateError("E_SOURCE_ALIGNED_SOURCE_MODE", "重叠候选只支持单一program时间轴")
    if binding.mr_mode != "off" or any(item.mr_node_id is not None for item in binding.sources):
        raise Av27TemplateError(
            "E_SOURCE_ALIGNED_LEGACY_PREPARATION",
            "此分析包含旧外部修复节点。请沿用旧工作流，或新建仅原片分析的工程；不会自动迁移旧结果。",
        )
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
        "original_video_artifact_id": timeline.artifact_id,
        "admission_artifact_id": binding.admission_artifact.artifact_id,
        "source_media_artifact_id": source.source_artifact.artifact_id,
        "frame_count": timeline.frame_count,
        "frame_rate": timeline.frame_rate,
    }
    SourceExpectation.model_validate(source_data)
    scale = processing.enhancement.actual_scale_factor
    # 新Split继承已验收的16:9→1080p FFV1基底；Enhancement倍率作用于Split，不能误乘原片尺寸。
    width, height = 1920, 1080
    input_geometry = {"width": width, "height": height, "sample_aspect_ratio": "1/1"}
    output_geometry = {
        "width": width * scale,
        "height": height * scale,
        "sample_aspect_ratio": "1/1",
    }
    target = publication_target(
        publication,
        mr_mode=processing.mr.mode,
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
            raise Av27TemplateError(
                "E_SOURCE_ALIGNED_OUTPUT_SOURCE", "成品目标不得覆盖源媒体或有效视频"
            )

    nodes = list(current.project.graph.nodes)
    edges = list(current.project.graph.edges)
    definitions = list(current.definitions)
    definition_keys = {(item.type_id, item.version) for item in definitions}
    defaults = {
        "enhancement": enhancement_definition,
        "merge": merge_video_definition,
        "context": fi_context_definition,
        "fi": frame_interpolation_definition,
        "crop": fi_crop_definition,
        "program": program_encode_definition,
        "final": final_mux_definition,
    }

    def selected(role: str, count: int = 1) -> NodeDefinition:
        if definition_factory is not None:
            return definition_factory(role, count)
        return atomic_split_definition(count) if role == "split" else defaults[role]()

    enhancement_def = selected("enhancement")
    merge_def = selected("merge")
    context_def = selected("context")
    fi_def = selected("fi")
    crop_def = selected("crop")
    # 仅为首次模板生成保留展示槽位：现有 Source/Admission 原位保留，新增节点从其右侧开始。
    # Studio 卡宽 248；320 列距留出端口与连线通道，MR 开启时独占一列，不覆盖 Admission。
    column_pitch, leaf_pitch = 320, 260
    processing_x = (
        max(
            (item.ui_position.x if item.ui_position is not None else 0 for item in nodes),
            default=0,
        )
        + column_pitch
    )
    split_column = 1 if isinstance(processing.mr, SourceAlignedMrExternal) else 0

    def node(
        identity: str, definition: NodeDefinition, params: dict[str, Any], column: int, y: int
    ) -> str:
        nodes.append(
            NodeInstance(
                node_id=identity,
                type_id=definition.type_id,
                definition_version=definition.version,
                parameters=params,
                ui_position=UiPosition(x=processing_x + column * column_pitch, y=y),
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

    effective_node_id = source.node_id
    if isinstance(processing.mr, SourceAlignedMrExternal):
        effective_node_id = node(
            "source-aligned.mr",
            external_factory(processing.mr.declared_container),
            {"source": source_data, **processing.mr.model_dump(mode="json", exclude={"mode"})},
            0,
            80,
        )
        edge(source.node_id, "video", effective_node_id, "video")
        edge(binding.admission_node_id, "gate", effective_node_id, "gate")

    split = node(
        "overlap.split",
        selected("split", plan.leaf_count),
        {
            "plan": plan.model_dump(mode="json"),
            "source": source_data,
        },
        split_column,
        80,
    )
    edge(effective_node_id, "video", split, "videos", 0)
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
        # 批量节点仍显式显示各 output 行；按卡片高度预留槽位，不为每叶留一整张节点的高度。
        chapter_y += (
            max(400, 280 + len(chapter.leaves) * 48)
            if batch_enhancement
            else max(320, len(chapter.leaves) * leaf_pitch)
        )
        merge = node(
            f"overlap.merge.{chapter.label}",
            merge_def,
            {"source": source_data, "chapter": chapter_data},
            split_column + 2,
            y,
        )
        merges.append(merge)
        leaf_parameters = [
            {
                "leaf_id": leaf.leaf_id,
                "global_ordinal": leaf.global_ordinal,
                "ordinal": leaf.ordinal,
                "count": len(chapter.leaves),
                "start_frame": leaf.start_frame,
                "end_frame": leaf.end_frame,
            }
            for leaf in chapter.leaves
        ]
        if batch_enhancement:
            # 一个普通多输出节点是本章的完整完成/重跑单位；没有隐藏逐叶 NodeRun。
            enhancement = node(
                f"overlap.enhance.{chapter.label}",
                selected("enhancement", len(chapter.leaves)),
                {
                    "source": source_data,
                    "chapter": chapter_data,
                    "leaves": leaf_parameters,
                    "expected_input_geometry": input_geometry,
                    "expected_output_geometry": output_geometry,
                    **processing.enhancement.model_dump(mode="json", exclude_none=True),
                },
                split_column + 1,
                y,
            )
            for leaf in chapter.leaves:
                edge(split, leaf.leaf_id, enhancement, "videos", leaf.ordinal)
                edge(enhancement, f"leaf-{leaf.ordinal + 1:04d}", merge, "videos", leaf.ordinal)
        for leaf in () if batch_enhancement else chapter.leaves:
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
                split_column + 1,
                y + leaf.ordinal * leaf_pitch,
            )
            edge(split, leaf.leaf_id, enhancement, "video")
            edge(enhancement, "video", merge, "videos", leaf.ordinal)
        common = {
            "source": source_data,
            "chapter": chapter_data,
            "fi_profile": profile.model_dump(mode="json"),
        }
        context = node(f"overlap.context.{chapter.label}", context_def, common, split_column + 3, y)
        fi = node(f"overlap.fi.{chapter.label}", fi_def, common, split_column + 4, y)
        crop = node(f"overlap.crop.{chapter.label}", crop_def, common, split_column + 5, y)
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
        selected("program"),
        {
            "source": source_data,
            "chapter_count": plan.chapter_count,
            "encoder": processing.program_encode.encoder,
        },
        split_column + 6,
        80,
    )
    for ordinal, crop in enumerate(crops):
        edge(crop, "video", program, "chapters", ordinal)
    publication_parameters = {
        "target_path": str(target),
        "overwrite": publication.overwrite,
        "output_root": str(Path(publication.output_root).resolve(strict=True)),
        "create_parent": publication.layout == "title_subdirectory",
        "protected_paths": protected,
    }
    final = node(
        "overlap.final",
        final_publication_factory() if final_publication_factory is not None else selected("final"),
        {
            "source": source_data,
            "mr_mode": processing.mr.mode,
            **(publication_parameters if final_publication_factory is not None else {}),
        },
        split_column + 7,
        80,
    )
    edge(program, "video", final, "video")
    edge(source.node_id, "source_media", final, "sources", 0)
    edge(binding.admission_node_id, "gate", final, "gate")
    output = node(
        "output",
        output_file_definition("MediaFile"),
        {"mode": "reference", "overwrite": False}
        if final_publication_factory is not None
        else {
            "mode": "copy",
            **publication_parameters,
        },
        split_column + 8,
        80,
    )
    edge(final, "media", output, "in")
    graph = Graph(nodes=tuple(nodes), edges=tuple(edges))
    GraphValidator(definitions).validate(graph)
    return SourceAlignedBuild(
        project=Project(
            project_id=current.project.project_id, name=current.project.name, graph=graph
        ),
        definitions=tuple(definitions),
        plan=plan,
        contexts=contexts,
        publication=SourceAlignedPublication(
            output_target_path=str(target),
            output_directory_to_create=str(target.parent) if not target.parent.exists() else None,
        ),
    )
