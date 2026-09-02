"""验证 AVEnhanceFlow v2.7 profile preflight 的纯检查与失败关闭语义。

fixtures 只由普通 Project/Graph/NodeDefinition 组成，不创建 SQLite、Run、Artifact 或媒体文件。
所有 binding facts 都是显式合成值，确保 preflight 不通过路径或 Repository 隐式补充事实。
"""

from __future__ import annotations

from collections import defaultdict
from fractions import Fraction
from pathlib import PurePosixPath
from typing import Any, Literal, cast

import pytest
from pydantic import ValidationError

from zniku.avenhance_v27 import (
    atomic_split_definition,
    enhancement_definition,
    final_mux_definition,
    frame_interpolation_definition,
    merge_video_definition,
    mosaic_restoration_definition,
    program_encode_definition,
    source_admission_definition,
    source_program_definition,
)
from zniku.avenhance_v27.preflight import (
    Av27BindingFacts,
    Av27ProfilePreflightResult,
    Av27PublicationFacts,
    Av27SourceBindingFact,
    preflight_av27_profile,
)
from zniku.graph import Edge, Graph, NodeDefinition, NodeInstance, PythonExecutorSpec
from zniku.media import output_file_definition
from zniku.project import Project, ProjectSnapshot

SourceMode = Literal["program", "pre_chaptered"]
MrMode = Literal["off", "external"]

_FPS = "30/1"
_SIGNAL: dict[str, Any] = {
    "color_primaries": "bt709",
    "color_transfer": "bt709",
    "color_space": "bt709",
    "color_range": "tv",
    "chroma_location": "left",
    "field_order": "progressive",
    "rotation": 0,
}
_INPUT_GEOMETRY: dict[str, Any] = {
    "width": 1920,
    "height": 1080,
    "sample_aspect_ratio": "1/1",
}
_OUTPUT_GEOMETRY: dict[str, Any] = {
    "width": 3840,
    "height": 2160,
    "sample_aspect_ratio": "1/1",
}


def _preparation_snapshot(*, mr_mode: MrMode = "off") -> ProjectSnapshot:
    source = NodeInstance(
        node_id="source.program",
        type_id="zniku.avenhance.v27.source_program",
        definition_version="0.2.1",
        parameters={"source_path": "/synthetic/source.mkv", "source_ordinal": 0},
    )
    admission = NodeInstance(
        node_id="admission.sources",
        type_id="zniku.avenhance.v27.source_admission",
        definition_version="0.2.1",
        parameters={
            "source_mode": "program",
            "sources": [{"source_ordinal": 0, "source_node_id": source.node_id}],
        },
    )
    nodes = [source, admission]
    edges = [
        Edge(
            source_node_id=source.node_id,
            source_port_id="source_media",
            target_node_id=admission.node_id,
            target_port_id="sources",
            ordinal=0,
        )
    ]
    definitions: list[NodeDefinition] = [
        source_program_definition(),
        source_admission_definition(),
    ]
    if mr_mode == "external":
        mr = NodeInstance(
            node_id="mr.program",
            type_id="zniku.avenhance.v27.mosaic_restoration.external",
            definition_version="0.2.1",
            parameters={"model_name": "Jasna", "model_version": "0.10.0"},
        )
        nodes.append(mr)
        edges.extend(
            (
                Edge(
                    source_node_id=source.node_id,
                    source_port_id="video",
                    target_node_id=mr.node_id,
                    target_port_id="video",
                ),
                Edge(
                    source_node_id=admission.node_id,
                    source_port_id="gate",
                    target_node_id=mr.node_id,
                    target_port_id="gate",
                ),
            )
        )
        definitions.append(mosaic_restoration_definition())
    project = Project(
        project_id="project.av27-preparation",
        name="v2.7 preparation",
        graph=Graph(nodes=tuple(nodes), edges=tuple(edges)),
    )
    return ProjectSnapshot(project=project, definitions=tuple(definitions))


def _expanded_snapshot(
    *,
    source_mode: SourceMode = "program",
    mr_mode: MrMode = "off",
) -> ProjectSnapshot:
    source_rows: tuple[tuple[str, int, str | None], ...]
    if source_mode == "program":
        source_rows = (("source.program", 4000, None),)
        chapters: list[dict[str, Any]] = [
            {
                "chapter_id": "chapter-0001",
                "chapter_ordinal": 0,
                "label": "A",
                "source_ordinal": 0,
                "start_frame": 0,
                "end_frame": 2000,
            },
            {
                "chapter_id": "chapter-0002",
                "chapter_ordinal": 1,
                "label": "B",
                "source_ordinal": 0,
                "start_frame": 2000,
                "end_frame": 4000,
            },
        ]
        selector: dict[str, Any] | None = {"mode": "exact_frames", "frames": [2000]}
    else:
        source_rows = (
            ("source.chapter-0001", 1800, "Opening"),
            ("source.chapter-0002", 900, "Feature"),
        )
        chapters = [
            {
                "chapter_id": "chapter-0001",
                "chapter_ordinal": 0,
                "label": "Opening",
                "source_ordinal": 0,
                "start_frame": 0,
                "end_frame": 1800,
            },
            {
                "chapter_id": "chapter-0002",
                "chapter_ordinal": 1,
                "label": "Feature",
                "source_ordinal": 1,
                "start_frame": 0,
                "end_frame": 900,
            },
        ]
        selector = None

    sources: list[NodeInstance] = []
    nodes: list[NodeInstance] = []
    edges: list[Edge] = []
    definitions: list[NodeDefinition] = [
        source_program_definition(),
        source_admission_definition(),
    ]
    for ordinal, (node_id, _frames, label) in enumerate(source_rows):
        parameters: dict[str, Any] = {
            "source_path": f"/synthetic/source-{ordinal}.mkv",
            "source_ordinal": ordinal,
        }
        if label is not None:
            parameters["label"] = label
        source = NodeInstance(
            node_id=node_id,
            type_id="zniku.avenhance.v27.source_program",
            definition_version="0.2.1",
            parameters=parameters,
        )
        sources.append(source)
        nodes.append(source)

    admission = NodeInstance(
        node_id="admission.sources",
        type_id="zniku.avenhance.v27.source_admission",
        definition_version="0.2.1",
        parameters={
            "source_mode": source_mode,
            "sources": [
                {"source_ordinal": index, "source_node_id": source.node_id}
                for index, source in enumerate(sources)
            ],
        },
    )
    nodes.append(admission)
    edges.extend(
        Edge(
            source_node_id=source.node_id,
            source_port_id="source_media",
            target_node_id=admission.node_id,
            target_port_id="sources",
            ordinal=index,
        )
        for index, source in enumerate(sources)
    )

    effective_nodes: list[NodeInstance] = sources
    if mr_mode == "external":
        effective_nodes = []
        definitions.append(mosaic_restoration_definition())
        for index, source in enumerate(sources):
            mr = NodeInstance(
                node_id=f"mr.{index + 1:04d}",
                type_id="zniku.avenhance.v27.mosaic_restoration.external",
                definition_version="0.2.1",
                parameters={"model_name": "Jasna", "model_version": "0.10.0"},
            )
            nodes.append(mr)
            effective_nodes.append(mr)
            edges.extend(
                (
                    Edge(
                        source_node_id=source.node_id,
                        source_port_id="video",
                        target_node_id=mr.node_id,
                        target_port_id="video",
                    ),
                    Edge(
                        source_node_id=admission.node_id,
                        source_port_id="gate",
                        target_node_id=mr.node_id,
                        target_port_id="gate",
                    ),
                )
            )

    planned_ids = tuple(f"artifact-effective-{index + 1:04d}" for index in range(len(sources)))
    segments = _segments(chapters, planned_ids=planned_ids)
    split_parameters: dict[str, Any] = {
        "source_mode": source_mode,
        "leaf_duration_minutes": 1,
        "planned_admission_artifact_id": "artifact-admission-0001",
        "planned_effective_video_artifact_ids": list(planned_ids),
        "chapters": chapters,
        "segments": segments,
    }
    if selector is not None:
        split_parameters["chapter_selector"] = selector
    split = NodeInstance(
        node_id="split.atomic",
        type_id=f"zniku.avenhance.v27.atomic_split.leaves.{len(segments)}",
        definition_version="0.2.1",
        parameters=split_parameters,
    )
    nodes.append(split)
    definitions.append(atomic_split_definition(len(segments)))
    edges.extend(
        Edge(
            source_node_id=node.node_id,
            source_port_id="video",
            target_node_id=split.node_id,
            target_port_id="videos",
            ordinal=index,
        )
        for index, node in enumerate(effective_nodes)
    )
    edges.append(
        Edge(
            source_node_id=admission.node_id,
            source_port_id="gate",
            target_node_id=split.node_id,
            target_port_id="gate",
        )
    )

    enhancements: dict[str, NodeInstance] = {}
    for segment in segments:
        node = NodeInstance(
            node_id=f"enhance.{segment['leaf_id']}",
            type_id="zniku.avenhance.v27.enhancement.external",
            definition_version="0.2.1",
            parameters={
                "model_name": "Starlight Precise",
                "model_version": "1.0",
                "actual_scale_factor": 2,
                "expected_input_geometry": _INPUT_GEOMETRY,
                "expected_output_geometry": _OUTPUT_GEOMETRY,
                "expected_frames": segment["end_frame"] - segment["start_frame"],
                "expected_fps": _FPS,
                "chapter_id": segment["chapter_id"],
                "chapter_ordinal": segment["chapter_ordinal"],
                "leaf_id": segment["leaf_id"],
                "leaf_ordinal": segment["leaf_ordinal"],
            },
        )
        enhancements[str(segment["leaf_id"])] = node
        nodes.append(node)
        edges.append(
            Edge(
                source_node_id=split.node_id,
                source_port_id=str(segment["port_id"]),
                target_node_id=node.node_id,
                target_port_id="video",
            )
        )
    definitions.append(enhancement_definition())

    chapter_segments: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for segment in segments:
        chapter_segments[str(segment["chapter_id"])].append(segment)
    merges: dict[str, NodeInstance] = {}
    fis: dict[str, NodeInstance] = {}
    for chapter in chapters:
        chapter_id = str(chapter["chapter_id"])
        frames = chapter["end_frame"] - chapter["start_frame"]
        merge = NodeInstance(
            node_id=f"merge.{chapter_id}",
            type_id="zniku.avenhance.v27.merge_video",
            definition_version="0.2.1",
            parameters={
                "model_name": "Starlight Precise",
                "model_version": "1.0",
                "chapter_id": chapter_id,
                "chapter_ordinal": chapter["chapter_ordinal"],
                "expected_frames": frames,
                "expected_fps": _FPS,
                "expected_geometry": _OUTPUT_GEOMETRY,
                "actual_scale_factor": 2,
            },
        )
        fi = NodeInstance(
            node_id=f"fi.{chapter_id}",
            type_id="zniku.avenhance.v27.frame_interpolation.external",
            definition_version="0.2.1",
            parameters={
                "model_name": "Aion",
                "model_version": "1.0",
                "chapter_id": chapter_id,
                "chapter_ordinal": chapter["chapter_ordinal"],
                "expected_input_frames": frames,
                "expected_output_frames": frames * 2 - 1,
                "source_fps": _FPS,
                "expected_geometry": _OUTPUT_GEOMETRY,
                "expected_signal": _SIGNAL,
            },
        )
        merges[chapter_id] = merge
        fis[chapter_id] = fi
        nodes.extend((merge, fi))
        for local_ordinal, segment in enumerate(chapter_segments[chapter_id]):
            edges.append(
                Edge(
                    source_node_id=enhancements[str(segment["leaf_id"])].node_id,
                    source_port_id="video",
                    target_node_id=merge.node_id,
                    target_port_id="videos",
                    ordinal=local_ordinal,
                )
            )
        edges.append(
            Edge(
                source_node_id=merge.node_id,
                source_port_id="video",
                target_node_id=fi.node_id,
                target_port_id="video",
            )
        )
    definitions.extend((merge_video_definition(), frame_interpolation_definition()))

    program_chapters = [
        {
            "chapter_id": chapter["chapter_id"],
            "chapter_ordinal": chapter["chapter_ordinal"],
            "source_frames": chapter["end_frame"] - chapter["start_frame"],
            "expected_fi_frames": (chapter["end_frame"] - chapter["start_frame"]) * 2 - 1,
            "encoded_frames": (chapter["end_frame"] - chapter["start_frame"]) * 2,
        }
        for chapter in chapters
    ]
    program_parameters: dict[str, Any] = {
        "encoder": "cpu",
        "source_fps": _FPS,
        "chapters": program_chapters,
        "expected_geometry": _OUTPUT_GEOMETRY,
        "expected_signal": _SIGNAL,
    }
    program = NodeInstance(
        node_id="program.encode",
        type_id="zniku.avenhance.v27.program_encode",
        definition_version="0.2.1",
        parameters=program_parameters,
    )
    nodes.append(program)
    for chapter in chapters:
        fi = fis[str(chapter["chapter_id"])]
        edges.append(
            Edge(
                source_node_id=fi.node_id,
                source_port_id="video",
                target_node_id=program.node_id,
                target_port_id="chapters",
                ordinal=chapter["chapter_ordinal"],
            )
        )
    definitions.append(program_encode_definition())

    final = NodeInstance(
        node_id="final.mux",
        type_id="zniku.avenhance.v27.final_mux",
        definition_version="0.2.1",
        parameters={
            "source_mode": source_mode,
            "sources": [
                {
                    "source_ordinal": index,
                    "source_frames": frames,
                    "source_fps": _FPS,
                }
                for index, (_node_id, frames, _label) in enumerate(source_rows)
            ],
            "expected_program_frames": sum(item["encoded_frames"] for item in program_chapters),
            "expected_geometry": _OUTPUT_GEOMETRY,
            "expected_signal": _SIGNAL,
            "mr_mode": mr_mode,
        },
    )
    marker = "MR Enhanced" if mr_mode == "external" else "Enhanced"
    output = NodeInstance(
        node_id="output.final",
        type_id="zniku.media.output_file.media",
        definition_version="0.2.0",
        parameters={
            "mode": "copy",
            "overwrite": False,
            "target_path": (
                f"/synthetic/output/Film (2026)/Film (2026) - {marker} FI60p 2160p.mkv"
            ),
        },
    )
    nodes.extend((final, output))
    edges.append(
        Edge(
            source_node_id=program.node_id,
            source_port_id="video",
            target_node_id=final.node_id,
            target_port_id="video",
        )
    )
    edges.extend(
        Edge(
            source_node_id=source.node_id,
            source_port_id="source_media",
            target_node_id=final.node_id,
            target_port_id="sources",
            ordinal=index,
        )
        for index, source in enumerate(sources)
    )
    edges.extend(
        (
            Edge(
                source_node_id=admission.node_id,
                source_port_id="gate",
                target_node_id=final.node_id,
                target_port_id="gate",
            ),
            Edge(
                source_node_id=final.node_id,
                source_port_id="media",
                target_node_id=output.node_id,
                target_port_id="in",
            ),
        )
    )
    definitions.extend((final_mux_definition(), output_file_definition("MediaFile")))
    project = Project(
        project_id=f"project.av27-{source_mode}-{mr_mode}",
        name="v2.7 expanded",
        graph=Graph(nodes=tuple(nodes), edges=tuple(edges)),
    )
    return ProjectSnapshot(project=project, definitions=tuple(definitions))


def _segments(
    chapters: list[dict[str, Any]],
    *,
    planned_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    ordinal = 0
    frames_per_leaf = round(Fraction(30, 1) * 60)
    for chapter in chapters:
        cursor = int(chapter["start_frame"])
        while cursor < int(chapter["end_frame"]):
            end = min(cursor + frames_per_leaf, int(chapter["end_frame"]))
            leaf_id = f"leaf-{ordinal + 1:04d}"
            result.append(
                {
                    "port_id": leaf_id,
                    "source_ordinal": chapter["source_ordinal"],
                    "planned_effective_video_artifact_id": planned_ids[
                        int(chapter["source_ordinal"])
                    ],
                    "chapter_id": chapter["chapter_id"],
                    "chapter_ordinal": chapter["chapter_ordinal"],
                    "leaf_id": leaf_id,
                    "leaf_ordinal": ordinal,
                    "start_frame": cursor,
                    "end_frame": end,
                }
            )
            cursor = end
            ordinal += 1
    return result


def _replace_node_parameters(
    snapshot: ProjectSnapshot,
    node_id: str,
    **updates: Any,
) -> ProjectSnapshot:
    nodes: list[NodeInstance] = []
    for node in snapshot.project.graph.nodes:
        if node.node_id == node_id:
            parameters = node.parameters.copy()
            parameters.update(updates)
            node = node.model_copy(update={"parameters": parameters})
        nodes.append(node)
    graph = snapshot.project.graph.model_copy(update={"nodes": tuple(nodes)})
    project = snapshot.project.model_copy(update={"graph": graph})
    return snapshot.model_copy(update={"project": project})


def _current_binding_facts(snapshot: ProjectSnapshot) -> Av27BindingFacts:
    split = next(node for node in snapshot.project.graph.nodes if node.node_id == "split.atomic")
    final = next(node for node in snapshot.project.graph.nodes if node.node_id == "final.mux")
    planned_ids = split.parameters["planned_effective_video_artifact_ids"]
    admission_id = split.parameters["planned_admission_artifact_id"]
    source_values = cast(list[dict[str, Any]], final.parameters["sources"])
    assert isinstance(planned_ids, list)
    assert all(isinstance(item, str) for item in planned_ids)
    assert isinstance(admission_id, str)
    return Av27BindingFacts(
        preparation_snapshot_current=True,
        preparation_results_current=True,
        admission_artifact_id=admission_id,
        sources=tuple(
            Av27SourceBindingFact(
                source_ordinal=index,
                source_media_artifact_id=f"artifact-source-{index + 1:04d}",
                source_frame_count=int(source["source_frames"]),
                source_frame_rate=str(source["source_fps"]),
                effective_video_artifact_id=str(planned_ids[index]),
                effective_video_frame_count=int(source["source_frames"]),
                effective_video_frame_rate=str(source["source_fps"]),
            )
            for index, source in enumerate(source_values)
        ),
    )


def _current_publication_facts(snapshot: ProjectSnapshot) -> Av27PublicationFacts:
    output = next(node for node in snapshot.project.graph.nodes if node.node_id == "output.final")
    target = output.parameters["target_path"]
    assert isinstance(target, str)
    target_path = PurePosixPath(target.replace("\\", "/"))
    parent = target_path.parent
    return Av27PublicationFacts(
        target_path=target,
        resolved_output_root=str(parent.parent),
        resolved_canonical_parent=str(parent),
        output_root_exists=True,
        output_root_is_directory=True,
        canonical_parent_exists=True,
        canonical_parent_is_directory=True,
        canonical_parent_contained=True,
        target_exists=False,
        target_is_regular_file=False,
        target_is_symlink_or_reparse=False,
        target_contained=True,
    )


def _preflight_current(snapshot: ProjectSnapshot) -> Av27ProfilePreflightResult:
    return preflight_av27_profile(
        snapshot,
        binding_facts=_current_binding_facts(snapshot),
        publication_facts=_current_publication_facts(snapshot),
    )


@pytest.mark.parametrize("mr_mode", ["off", "external"])
def test_preparation_profile_recognizes_mr_modes(mr_mode: MrMode) -> None:
    result = preflight_av27_profile(_preparation_snapshot(mr_mode=mr_mode))

    assert result.status == "preparation-compatible"
    assert result.phase == "preparation"
    assert result.compatible is True
    assert result.diagnostics == ()


@pytest.mark.parametrize(
    ("source_mode", "mr_mode"),
    [
        ("program", "off"),
        ("program", "external"),
        ("pre_chaptered", "off"),
        ("pre_chaptered", "external"),
    ],
)
def test_expanded_profile_recognizes_two_stage_shapes(
    source_mode: SourceMode,
    mr_mode: MrMode,
) -> None:
    snapshot = _expanded_snapshot(source_mode=source_mode, mr_mode=mr_mode)
    before = snapshot.model_dump(mode="json")

    result = preflight_av27_profile(
        snapshot,
        binding_facts=_current_binding_facts(snapshot),
        publication_facts=_current_publication_facts(snapshot),
    )

    assert result.status == "expanded-compatible"
    assert result.compatible is True
    assert result.diagnostics == ()
    assert snapshot.model_dump(mode="json") == before


def test_preparation_source_labels_follow_source_mode() -> None:
    snapshot = _preparation_snapshot()
    missing_pre_chaptered_label = _replace_node_parameters(
        snapshot,
        "admission.sources",
        source_mode="pre_chaptered",
    )
    unexpected_program_label = _replace_node_parameters(
        snapshot,
        "source.program",
        label="Chapter A",
    )

    missing = preflight_av27_profile(missing_pre_chaptered_label)
    unexpected = preflight_av27_profile(unexpected_program_label)

    assert {item.code for item in missing.diagnostics} >= {"E_AV27_PREFLIGHT_CHAPTER_LABEL"}
    assert {item.code for item in unexpected.diagnostics} >= {"E_AV27_PREFLIGHT_CHAPTER_LABEL"}


@pytest.mark.parametrize("source_path", ["/synthetic/source.mkv ", "/synthetic/\x00source.mkv"])
def test_preparation_rejects_builder_impossible_source_path_text(source_path: str) -> None:
    snapshot = _replace_node_parameters(
        _preparation_snapshot(),
        "source.program",
        source_path=source_path,
    )

    result = preflight_av27_profile(snapshot)

    assert result.status == "incompatible"
    assert {item.code for item in result.diagnostics} >= {"E_AV27_PREFLIGHT_SOURCE_PATH"}


def test_preparation_rejects_whitespace_only_mr_declaration() -> None:
    snapshot = _replace_node_parameters(
        _preparation_snapshot(mr_mode="external"),
        "mr.program",
        model_name=" ",
        model_version=" ",
    )

    result = preflight_av27_profile(snapshot)

    assert result.status == "incompatible"
    assert {item.code for item in result.diagnostics} >= {"E_AV27_PREFLIGHT_MODEL_DECLARATION"}


def test_plain_project_requires_explicit_exact_definitions() -> None:
    snapshot = _preparation_snapshot()

    missing = preflight_av27_profile(snapshot.project)
    supplied = preflight_av27_profile(snapshot.project, definitions=snapshot.definitions)

    assert missing.status == "incompatible"
    assert {item.code for item in missing.diagnostics} >= {"E_AV27_PREFLIGHT_DEFINITIONS_REQUIRED"}
    assert supplied.status == "preparation-compatible"


def test_profile_rejects_unused_definition_authority() -> None:
    snapshot = _preparation_snapshot()
    with_unused = snapshot.model_copy(
        update={"definitions": (*snapshot.definitions, mosaic_restoration_definition())}
    )

    result = preflight_av27_profile(with_unused)

    assert result.status == "incompatible"
    assert {item.code for item in result.diagnostics} >= {"E_AV27_PREFLIGHT_DEFINITION_SET"}


def test_expanded_binding_facts_distinguish_current_from_replan() -> None:
    snapshot = _expanded_snapshot()
    current = _current_binding_facts(snapshot)
    stale = current.model_copy(
        update={
            "sources": (
                current.sources[0].model_copy(
                    update={
                        "effective_video_artifact_id": "artifact-effective-replaced",
                        "effective_video_frame_count": 4100,
                    }
                ),
            )
        }
    )

    publication = _current_publication_facts(snapshot)
    assert (
        preflight_av27_profile(
            snapshot,
            binding_facts=current,
            publication_facts=publication,
        ).status
        == "expanded-compatible"
    )
    result = preflight_av27_profile(
        snapshot,
        binding_facts=stale,
        publication_facts=publication,
    )

    assert result.status == "replan_required"
    assert result.compatible is False
    assert {item.code for item in result.diagnostics} == {"E_AV27_PREFLIGHT_EFFECTIVE_VIDEO_STALE"}


def test_current_admitted_media_facts_are_independent_plan_authority() -> None:
    snapshot = _expanded_snapshot()
    current = _current_binding_facts(snapshot)
    changed = current.model_copy(
        update={
            "sources": (
                current.sources[0].model_copy(
                    update={
                        "source_frame_count": 4100,
                        "effective_video_frame_count": 4100,
                    }
                ),
            )
        }
    )

    result = preflight_av27_profile(
        snapshot,
        binding_facts=changed,
        publication_facts=_current_publication_facts(snapshot),
    )

    assert result.status == "incompatible"
    assert {item.code for item in result.diagnostics} >= {
        "E_AV27_PREFLIGHT_FINAL_SOURCE_BINDING",
        "E_AV27_PREFLIGHT_CHAPTER_PLAN",
    }


@pytest.mark.parametrize("frame_rate", ["30", "60/2", "0/1"])
def test_binding_fact_requires_explicit_canonical_rational(frame_rate: str) -> None:
    payload = {
        "source_ordinal": 0,
        "source_media_artifact_id": "artifact-source-0001",
        "source_frame_count": 4000,
        "source_frame_rate": frame_rate,
        "effective_video_artifact_id": "artifact-effective-0001",
        "effective_video_frame_count": 4000,
        "effective_video_frame_rate": "30/1",
    }

    with pytest.raises(ValidationError):
        Av27SourceBindingFact.model_validate(payload, strict=True)


def test_expanded_profile_without_repository_facts_requires_replan() -> None:
    snapshot = _expanded_snapshot()
    result = preflight_av27_profile(
        snapshot,
        publication_facts=_current_publication_facts(snapshot),
    )

    assert result.status == "replan_required"
    assert {item.code for item in result.diagnostics} == {"E_AV27_PREFLIGHT_BINDING_UNVERIFIED"}


def test_expanded_profile_without_publication_facts_requires_replan() -> None:
    snapshot = _expanded_snapshot()

    result = preflight_av27_profile(
        snapshot,
        binding_facts=_current_binding_facts(snapshot),
    )

    assert result.status == "replan_required"
    assert {item.code for item in result.diagnostics} == {"E_AV27_PREFLIGHT_PUBLICATION_UNVERIFIED"}


@pytest.mark.parametrize(
    "updates",
    [
        {"canonical_parent_exists": False},
        {"target_exists": True, "target_is_regular_file": True},
        {"target_is_symlink_or_reparse": True},
        {"target_contained": False},
    ],
)
def test_publication_current_facts_fail_closed(updates: dict[str, bool]) -> None:
    snapshot = _expanded_snapshot()
    facts = _current_publication_facts(snapshot).model_copy(update=updates)

    result = preflight_av27_profile(
        snapshot,
        binding_facts=_current_binding_facts(snapshot),
        publication_facts=facts,
    )

    assert result.status == "replan_required"
    assert {item.code for item in result.diagnostics} == {"E_AV27_PREFLIGHT_PUBLICATION_STALE"}


def test_existing_regular_publication_target_requires_explicit_overwrite() -> None:
    snapshot = _replace_node_parameters(_expanded_snapshot(), "output.final", overwrite=True)
    facts = _current_publication_facts(snapshot).model_copy(
        update={"target_exists": True, "target_is_regular_file": True}
    )

    result = preflight_av27_profile(
        snapshot,
        binding_facts=_current_binding_facts(snapshot),
        publication_facts=facts,
    )

    assert result.status == "expanded-compatible"


def test_free_edited_publication_escape_path_requires_replan() -> None:
    snapshot = _replace_node_parameters(
        _expanded_snapshot(),
        "output.final",
        target_path=(
            "/synthetic/output/escape/../Film (2026)/Film (2026) - Enhanced FI60p 2160p.mkv"
        ),
    )

    result = _preflight_current(snapshot)

    assert result.status == "replan_required"
    assert {item.code for item in result.diagnostics} == {"E_AV27_PREFLIGHT_PUBLICATION_STALE"}


def test_exact_time_selector_is_recomputed_with_exact_fraction() -> None:
    snapshot = _expanded_snapshot()
    with_exact_time = _replace_node_parameters(
        snapshot,
        "split.atomic",
        chapter_selector={"mode": "exact_times", "times": ["200/3"]},
    )

    result = preflight_av27_profile(
        with_exact_time,
        binding_facts=_current_binding_facts(with_exact_time),
        publication_facts=_current_publication_facts(with_exact_time),
    )

    assert result.status == "expanded-compatible"


def test_profile_rejects_wrong_source_identity_and_gate_producer() -> None:
    snapshot = _preparation_snapshot(mr_mode="external")
    wrong_identity = _replace_node_parameters(
        snapshot,
        "admission.sources",
        sources=[{"source_ordinal": 0, "source_node_id": "source.other"}],
    )
    wrong_gate_edges = tuple(
        edge
        for edge in snapshot.project.graph.edges
        if not (edge.target_node_id == "mr.program" and edge.target_port_id == "gate")
    )
    wrong_gate_graph = snapshot.project.graph.model_copy(update={"edges": wrong_gate_edges})
    wrong_gate_project = snapshot.project.model_copy(update={"graph": wrong_gate_graph})
    wrong_gate = snapshot.model_copy(update={"project": wrong_gate_project})

    identity_result = preflight_av27_profile(wrong_identity)
    gate_result = preflight_av27_profile(wrong_gate)

    assert "E_AV27_PREFLIGHT_SOURCE_BINDING" in {item.code for item in identity_result.diagnostics}
    assert "E_AV27_PREFLIGHT_GATE_SOURCE" in {item.code for item in gate_result.diagnostics}
    assert gate_result.status == "incompatible"


def test_profile_rejects_leaf_stage_and_frame_relation_drift() -> None:
    snapshot = _expanded_snapshot()
    bad_model = _replace_node_parameters(
        snapshot,
        "enhance.leaf-0002",
        model_version="2.0",
    )
    bad_fi = _replace_node_parameters(
        snapshot,
        "fi.chapter-0001",
        expected_output_frames=4000,
    )

    model_result = _preflight_current(bad_model)
    frame_result = _preflight_current(bad_fi)

    assert "E_AV27_PREFLIGHT_ENHANCEMENT_DECLARATION" in {
        item.code for item in model_result.diagnostics
    }
    assert "E_AV27_PREFLIGHT_PARAMETER_MISMATCH" in {item.code for item in frame_result.diagnostics}


@pytest.mark.parametrize("node_id", ["enhance.leaf-0001", "fi.chapter-0001"])
def test_expanded_profile_rejects_whitespace_only_stage_model(node_id: str) -> None:
    snapshot = _expanded_snapshot()
    malformed = _replace_node_parameters(snapshot, node_id, model_name=" ")

    result = preflight_av27_profile(
        malformed,
        binding_facts=_current_binding_facts(snapshot),
        publication_facts=_current_publication_facts(snapshot),
    )

    assert result.status == "incompatible"
    assert {item.code for item in result.diagnostics} >= {"E_AV27_PREFLIGHT_MODEL_DECLARATION"}


@pytest.mark.parametrize(
    ("node_id", "parameter", "replacement", "expected_code"),
    [
        (
            "enhance.leaf-0002",
            "leaf_id",
            "leaf-9999",
            "E_AV27_PREFLIGHT_ENHANCEMENT_COUNT",
        ),
        (
            "merge.chapter-0001",
            "chapter_id",
            "chapter-9999",
            "E_AV27_PREFLIGHT_MERGE_COUNT",
        ),
        (
            "fi.chapter-0001",
            "chapter_id",
            "chapter-9999",
            "E_AV27_PREFLIGHT_FI_COUNT",
        ),
    ],
)
def test_identity_substitution_fails_closed_without_key_error(
    node_id: str,
    parameter: str,
    replacement: str,
    expected_code: str,
) -> None:
    snapshot = _expanded_snapshot()
    malformed = _replace_node_parameters(snapshot, node_id, **{parameter: replacement})

    result = preflight_av27_profile(
        malformed,
        binding_facts=_current_binding_facts(snapshot),
        publication_facts=_current_publication_facts(snapshot),
    )

    assert result.status == "incompatible"
    assert expected_code in {item.code for item in result.diagnostics}


def test_implausible_dynamic_split_count_is_rejected_before_leaf_materialization() -> None:
    malformed = _expanded_snapshot()
    nodes = tuple(
        node.model_copy(update={"type_id": "zniku.avenhance.v27.atomic_split.leaves.999"})
        if node.node_id == "split.atomic"
        else node
        for node in malformed.project.graph.nodes
    )
    graph = malformed.project.graph.model_copy(update={"nodes": nodes})
    malformed = malformed.model_copy(
        update={"project": malformed.project.model_copy(update={"graph": graph})}
    )

    result = _preflight_current(malformed)

    assert result.status == "incompatible"
    assert "E_AV27_PREFLIGHT_SPLIT_COUNT" in {item.code for item in result.diagnostics}


def test_profile_rejects_final_mr_diagnostic_naming_and_bypass() -> None:
    snapshot = _expanded_snapshot()
    bad_final = _replace_node_parameters(snapshot, "final.mux", mr_mode="external")
    bad_output = _replace_node_parameters(
        snapshot,
        "output.final",
        target_path="/synthetic/output/Film (2026)/custom.mkv",
    )
    extra_edge = Edge(
        source_node_id="source.program",
        source_port_id="video",
        target_node_id="enhance.leaf-0001",
        target_port_id="video",
    )
    bypass_graph = snapshot.project.graph.model_copy(
        update={"edges": (*snapshot.project.graph.edges, extra_edge)}
    )
    bypass = snapshot.model_copy(
        update={"project": snapshot.project.model_copy(update={"graph": bypass_graph})}
    )

    assert "E_AV27_PREFLIGHT_MR_MODE" in {
        item.code for item in _preflight_current(bad_final).diagnostics
    }
    assert "E_AV27_PREFLIGHT_NAMING" in {
        item.code for item in _preflight_current(bad_output).diagnostics
    }
    assert "E_AV27_PREFLIGHT_EDGE_SHAPE" in {
        item.code for item in _preflight_current(bypass).diagnostics
    }


def test_profile_rejects_noncanonical_leaf_plan_and_definition() -> None:
    snapshot = _expanded_snapshot()
    split = next(node for node in snapshot.project.graph.nodes if node.node_id == "split.atomic")
    raw_segments = split.parameters["segments"]
    assert isinstance(raw_segments, list)
    assert all(isinstance(item, dict) for item in raw_segments)
    bad_segments = [dict(item) for item in raw_segments if isinstance(item, dict)]
    bad_segments[0]["end_frame"] = 1799
    bad_plan = _replace_node_parameters(snapshot, split.node_id, segments=bad_segments)
    replacement_definition = source_program_definition().model_copy(
        update={"executor": PythonExecutorSpec(adapter="tests.synthetic:source")}
    )
    bad_definitions = tuple(
        replacement_definition if item.type_id == replacement_definition.type_id else item
        for item in snapshot.definitions
    )
    bad_definition = snapshot.model_copy(update={"definitions": bad_definitions})

    assert "E_AV27_PREFLIGHT_LEAF_PLAN" in {
        item.code for item in _preflight_current(bad_plan).diagnostics
    }
    assert "E_AV27_PREFLIGHT_DEFINITION_MISMATCH" in {
        item.code for item in _preflight_current(bad_definition).diagnostics
    }


def test_profile_reports_forbidden_executable_parameter_key() -> None:
    snapshot = _preparation_snapshot()
    source = snapshot.project.graph.nodes[0]
    bad_source = source.model_copy(
        update={"parameters": {**source.parameters, "shell_command": "synthetic"}}
    )
    graph = snapshot.project.graph.model_copy(
        update={"nodes": (bad_source, *snapshot.project.graph.nodes[1:])}
    )
    project = snapshot.project.model_copy(update={"graph": graph})
    result = preflight_av27_profile(snapshot.model_copy(update={"project": project}))

    assert result.status == "incompatible"
    assert "E_AV27_PREFLIGHT_EXECUTABLE_PARAMETER" in {item.code for item in result.diagnostics}
