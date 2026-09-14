"""将已准入工作参考与可选 MR 展开为普通 Graph，不等待未来 MR 产物。

已准入 reference 是设计时坐标；original 留作保内容比较与独立音频来源，未来 MR
只由直接边输入在执行时绑定。旧模板不迁移；本模块不创建 Run、Artifact 或输出目录。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, field_validator

from zniku.avenhance_v27.template import (
    Av27TemplateError,
    ProgramEncodeDeclaration,
    PublicationRequest,
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
from zniku.runtime import Artifact, RunnerInput
from zniku.source_preparation.contracts import check_reference_binding

from . import definitions as node_definitions
from .node_contracts import CandidateFiProfile, DeclaredContainer, SourceExpectation


@dataclass(frozen=True, slots=True)
class PreparedSourceBinding:
    """Service 已重验的普通 preparation 结果；每个身份均已登记，不生成未来 UUID。"""

    project_id: str
    preparation_run_id: str
    admission_node_id: str
    reference_video: Artifact
    admission_artifact: Artifact
    original_media: Artifact
    audio_sources: tuple[Artifact, ...]
    audio_node_ports: tuple[tuple[str, str], ...]


def _runner_input(artifact: Artifact, port: str) -> RunnerInput:
    return RunnerInput(
        port_id=port,
        artifact_id=artifact.artifact_id,
        kind=artifact.kind,
        path=Path(artifact.path),
        producer_node_run_id=artifact.producer_node_run_id,
        producer_port_id=artifact.producer_port_id,
        media_info=artifact.media_info,
    )


class PreparedSourceEnhancement(ChapterModel):
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


class PreparedSourceMrOff(ChapterModel):
    """默认不插入前处理节点，不改变原片分析。"""

    mode: Literal["off"] = "off"


class PreparedSourceMrExternal(ChapterModel):
    """操作者声明的工作源绑定外部前处理，不证明模型身份或逐帧内容。"""

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


type PreparedSourceMr = Annotated[
    PreparedSourceMrOff | PreparedSourceMrExternal, Field(discriminator="mode")
]


class PreparedSourceProcessing(ChapterModel):
    """唯一处理设置；有效默认值不隐藏必填项，也不证明外部软件能力。"""

    mr: PreparedSourceMr = Field(default_factory=PreparedSourceMrOff)
    settings: ChapterSettings = Field(default_factory=ChapterSettings)
    enhancement: PreparedSourceEnhancement
    fi_profile: CandidateFiProfile = Field(default_factory=CandidateFiProfile)
    program_encode: ProgramEncodeDeclaration


class PreparedSourcePublication(ChapterModel):
    """只读出版目标预览；目录实际创建仍由受控Output节点执行。"""

    output_target_path: str
    output_directory_to_create: str | None = None


class PreparedSourceBuild(ChapterModel):
    """一次纯Graph展开的返回值，不持久化为ExecutionPlan或独立权威。"""

    project: Project
    definitions: tuple[NodeDefinition, ...]
    plan: ChapterLeafPlan
    contexts: ExperimentalContextPlan
    publication: PreparedSourcePublication


def build_prepared_source(
    current: ProjectSnapshot,
    binding: PreparedSourceBinding,
    processing: PreparedSourceProcessing,
    publication: PublicationRequest,
) -> PreparedSourceBuild:
    """扩展同一 Graph；只从新 admission 及实际视频输出建立章节参考。"""

    return build_with_contract(
        current, binding, processing, publication, check_reference_binding, node_definitions
    )


def build_with_contract(
    current: ProjectSnapshot,
    binding: PreparedSourceBinding,
    processing: PreparedSourceProcessing,
    publication: PublicationRequest,
    reference_checker: Callable[[RunnerInput, RunnerInput], Any],
    definitions_provider: Any,
) -> PreparedSourceBuild:
    """共享普通图排布与纯规划；各版本显式提供自己的严格准入与 exact definitions。

    provider 仅由可信 Python 调用方指定，不来自 Graph/JSON，不改写输入 metadata，也不先构造
    旧图再改名。旧入口仍使用原 reader 和原定义，因此 Schema、图及运行语义不变。
    """
    atomic_split_definition = definitions_provider.atomic_split_definition
    enhancement_definition = definitions_provider.enhancement_definition
    external_definition = definitions_provider.external_definition
    fi_context_definition = definitions_provider.fi_context_definition
    fi_crop_definition = definitions_provider.fi_crop_definition
    final_mux_definition = definitions_provider.final_mux_definition
    frame_interpolation_definition = definitions_provider.frame_interpolation_definition
    merge_video_definition = definitions_provider.merge_video_definition
    program_encode_definition = definitions_provider.program_encode_definition

    if current.project.project_id != binding.project_id:
        raise Av27TemplateError("E_PREPARED_SOURCE_PROJECT", "分析结果不属于当前工程")
    gate = reference_checker(
        _runner_input(binding.reference_video, "video"),
        _runner_input(binding.admission_artifact, "gate"),
    )
    if (
        binding.original_media.artifact_id != gate.original_media_artifact_id
        or tuple(item.artifact_id for item in binding.audio_sources)
        != tuple(item.artifact_id for item in gate.audio_bindings)
        or len(binding.audio_node_ports) != len(binding.audio_sources)
    ):
        raise Av27TemplateError("E_PREPARED_SOURCE_BINDING", "已登记原件和音频输入与准入不匹配")
    timeline = AdmittedTimeline(
        artifact_id=binding.reference_video.artifact_id,
        frame_count=gate.source_frame_count,
        frame_rate=gate.frame_rate,
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
        "reference_video_artifact_id": timeline.artifact_id,
        "reference_media_artifact_id": gate.reference_media_artifact_id,
        "original_media_artifact_id": gate.original_media_artifact_id,
        "admission_artifact_id": binding.admission_artifact.artifact_id,
        "diagnosis_artifact_id": gate.diagnosis_artifact_id,
        "audio_source_artifact_ids": [item.artifact_id for item in gate.audio_bindings],
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
    target = canonical_publication_target(
        publication,
        mr_mode=processing.mr.mode,
        final_frame_rate=Fraction(gate.frame_rate) * 2,
        height=height * scale,
    )
    protected = list(
        dict.fromkeys(
            str(item.path)
            for item in (binding.original_media, binding.reference_video, *binding.audio_sources)
        )
    )
    for path in map(Path, protected):
        if target == path.resolve(strict=False) or (
            target.exists() and path.exists() and target.samefile(path)
        ):
            raise Av27TemplateError(
                "E_PREPARED_SOURCE_OUTPUT_SOURCE", "成品目标不得覆盖源媒体或有效视频"
            )

    nodes = list(current.project.graph.nodes)
    edges = list(current.project.graph.edges)
    definitions = list(current.definitions)
    definition_keys = {(item.type_id, item.version) for item in definitions}
    enhancement_def = enhancement_definition()
    merge_def = merge_video_definition()
    context_def = fi_context_definition()
    fi_def = frame_interpolation_definition()
    crop_def = fi_crop_definition()
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
    split_column = 1 if isinstance(processing.mr, PreparedSourceMrExternal) else 0

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

    effective_node_id = binding.admission_node_id
    if isinstance(processing.mr, PreparedSourceMrExternal):
        effective_node_id = node(
            "source-aligned.mr",
            external_definition(processing.mr.declared_container),
            {"source": source_data, **processing.mr.model_dump(mode="json", exclude={"mode"})},
            0,
            80,
        )
        edge(binding.admission_node_id, "video", effective_node_id, "video")
        edge(binding.admission_node_id, "gate", effective_node_id, "gate")

    split = node(
        "overlap.split",
        atomic_split_definition(plan.leaf_count),
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
        chapter_y += max(320, len(chapter.leaves) * leaf_pitch)
        merge = node(
            f"overlap.merge.{chapter.label}",
            merge_def,
            {"source": source_data, "chapter": chapter_data},
            split_column + 2,
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
        program_encode_definition(),
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
    final = node(
        "overlap.final",
        final_mux_definition(),
        {"source": source_data, "mr_mode": processing.mr.mode},
        split_column + 7,
        80,
    )
    edge(program, "video", final, "video")
    for ordinal, (audio_node_id, audio_port_id) in enumerate(binding.audio_node_ports):
        edge(audio_node_id, audio_port_id, final, "sources", ordinal)
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
        split_column + 8,
        80,
    )
    edge(final, "media", output, "in")
    graph = Graph(nodes=tuple(nodes), edges=tuple(edges))
    GraphValidator(definitions).validate(graph)
    return PreparedSourceBuild(
        project=Project(
            project_id=current.project.project_id, name=current.project.name, graph=graph
        ),
        definitions=tuple(definitions),
        plan=plan,
        contexts=contexts,
        publication=PreparedSourcePublication(
            output_target_path=str(target),
            output_directory_to_create=str(target.parent) if not target.parent.exists() else None,
        ),
    )
