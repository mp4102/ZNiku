"""验证 AVEnhanceFlow v2.7 两段模板的严格请求、确定性计划与普通 Graph。

测试只使用临时文本文件和合成 Artifact metadata，不执行 FFmpeg、媒体 probe 或外部工具。
重点锁定 server-side planner、canonical naming、动态 Split 参数及 Graph/profile 闭合，避免
Studio 或 Project Service 另造章节、leaf、端口和发布路径语义。
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import JsonValue, ValidationError

from zniku.avenhance_v27.definitions import (
    ATOMIC_SPLIT_TYPE_PREFIX,
    ENHANCEMENT_TYPE_ID,
    FINAL_MUX_TYPE_ID,
    FRAME_INTERPOLATION_TYPE_ID,
    MERGE_VIDEO_TYPE_ID,
    MOSAIC_RESTORATION_TYPE_ID,
    PROGRAM_ENCODE_TYPE_ID,
    SOURCE_ADMISSION_TYPE_ID,
    SOURCE_PROGRAM_TYPE_ID,
)
from zniku.avenhance_v27.preflight import (
    Av27BindingFacts,
    Av27PublicationFacts,
    Av27SourceBindingFact,
    preflight_av27_profile,
)
from zniku.avenhance_v27.probe import canonical_fraction, metadata_frame_count, metadata_rate
from zniku.avenhance_v27.template import (
    Av27TemplateError,
    ExactFramesChapterSelector,
    ExactTimesChapterSelector,
    ExpandRequest,
    PreparationBinding,
    PreparationSourceBinding,
    PrepareRequest,
    PublicationRequest,
    ResolvedChapterPlan,
    SingleChapterSelector,
    build_expanded,
    build_preparation,
    canonical_publication_target,
    derive_leaf_plan,
    excel_chapter_label,
    resolve_chapter_plan,
)
from zniku.graph import GraphValidator
from zniku.project import ProjectSnapshot
from zniku.runtime import Artifact


def _id() -> str:
    return str(uuid4())


def _source_file(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(b"synthetic-source")
    return path


def _prepare_payload(
    tmp_path: Path,
    *,
    source_mode: str = "program",
    mr_mode: str = "off",
    source_count: int | None = None,
) -> dict[str, Any]:
    count = source_count if source_count is not None else (1 if source_mode == "program" else 2)
    sources: list[dict[str, Any]] = []
    for ordinal in range(count):
        source: dict[str, Any] = {
            "source_path": str(_source_file(tmp_path, f"source-{ordinal}.mkv").resolve()),
            "source_ordinal": ordinal,
        }
        if source_mode == "pre_chaptered":
            source["chapter_label"] = excel_chapter_label(ordinal)
        sources.append(source)
    return {
        "profile_version": "2.7.0",
        "project_path": str((tmp_path / f"{source_mode}-{mr_mode}.zniku").resolve()),
        "project_id": f"project-{source_mode}-{mr_mode}",
        "project_name": f"Synthetic {source_mode} {mr_mode}",
        "source_mode": source_mode,
        "sources": sources,
        "mr": (
            {"mode": "off"}
            if mr_mode == "off"
            else {
                "mode": "external",
                "model_name": "Jasna",
                "model_version": "0.10.0",
            }
        ),
    }


def _namespace(
    *,
    frames: int,
    fps: str,
    source_ordinal: int,
    geometry: tuple[int, int] = (1920, 1080),
) -> dict[str, object]:
    rate = Fraction(fps)
    return {
        "zniku.avenhance.v27": {
            "frame_count": frames,
            "frame_rate": fps,
            "duration_seconds": float(Fraction(frames, 1) / rate),
            "source_ordinal": source_ordinal,
            "geometry": {
                "width": geometry[0],
                "height": geometry[1],
            },
            "sample_aspect_ratio": "1/1",
            "signal": {
                "color_primaries": "bt709",
                "color_transfer": "bt709",
                "color_space": "bt709",
                "color_range": "tv",
                "chroma_location": "left",
                "field_order": "progressive",
                "rotation": 0,
            },
            "audio_tracks": [],
        }
    }


def _artifact(
    path: Path,
    *,
    kind: str,
    port_id: str,
    frames: int,
    fps: str,
    source_ordinal: int,
    geometry: tuple[int, int] = (1920, 1080),
) -> Artifact:
    return Artifact(
        artifact_id=_id(),
        kind=kind,
        path=str(path.resolve()),
        producer_node_run_id=_id(),
        producer_port_id=port_id,
        media_info=cast(
            dict[str, JsonValue],
            _namespace(
                frames=frames,
                fps=fps,
                source_ordinal=source_ordinal,
                geometry=geometry,
            ),
        ),
        size=path.stat().st_size,
        mtime_ns=path.stat().st_mtime_ns,
    )


def _binding(
    preparation: ProjectSnapshot,
    *,
    source_mode: str,
    mr_mode: str,
    frames: tuple[int, ...],
    fps: str = "30/1",
    geometry: tuple[int, int] = (1920, 1080),
) -> PreparationBinding:
    source_nodes = sorted(
        (
            node
            for node in preparation.project.graph.nodes
            if node.type_id == SOURCE_PROGRAM_TYPE_ID
        ),
        key=lambda node: cast(int, node.parameters["source_ordinal"]),
    )
    mr_nodes = {
        edge.source_node_id: edge.target_node_id
        for edge in preparation.project.graph.edges
        if edge.source_port_id == "video"
        and any(
            node.node_id == edge.target_node_id and node.type_id == MOSAIC_RESTORATION_TYPE_ID
            for node in preparation.project.graph.nodes
        )
    }
    bindings: list[PreparationSourceBinding] = []
    for ordinal, (node, frame_count) in enumerate(zip(source_nodes, frames, strict=True)):
        path = Path(cast(str, node.parameters["source_path"]))
        source_media = _artifact(
            path,
            kind="MediaFile",
            port_id="source_media",
            frames=frame_count,
            fps=fps,
            source_ordinal=ordinal,
            geometry=geometry,
        )
        effective = _artifact(
            path,
            kind="VideoFile",
            port_id="video",
            frames=frame_count,
            fps=fps,
            source_ordinal=ordinal,
            geometry=geometry,
        )
        bindings.append(
            PreparationSourceBinding(
                source_ordinal=ordinal,
                source_node_id=node.node_id,
                source_media_artifact=source_media,
                effective_video_artifact=effective,
                mr_node_id=mr_nodes.get(node.node_id),
                chapter_label=(
                    cast(str, node.parameters["label"]) if source_mode == "pre_chaptered" else None
                ),
            )
        )
    gate_path = Path(cast(str, source_nodes[0].parameters["source_path"]))
    gate = _artifact(
        gate_path,
        kind="DataFile",
        port_id="gate",
        frames=frames[0],
        fps=fps,
        source_ordinal=0,
        geometry=geometry,
    )
    return PreparationBinding(
        project_id=preparation.project.project_id,
        preparation_run_id=_id(),
        source_mode=cast(Any, source_mode),
        mr_mode=cast(Any, mr_mode),
        admission_node_id="admission",
        admission_artifact=gate,
        sources=tuple(bindings),
    )


def _publication(tmp_path: Path, *, title: str = "Example") -> dict[str, Any]:
    root = tmp_path / "published"
    root.mkdir(exist_ok=True)
    (root / f"{title} (2026)").mkdir(exist_ok=True)
    return {
        "output_root": str(root.resolve()),
        "title": title,
        "year": "2026",
        "overwrite": False,
    }


def _expand_request(
    tmp_path: Path,
    run_id: str,
    *,
    selector: dict[str, Any] | None,
    leaf_duration_minutes: int = 1,
) -> ExpandRequest:
    payload: dict[str, Any] = {
        "profile_version": "2.7.0",
        "preparation_run_id": run_id,
        "leaf_duration_minutes": leaf_duration_minutes,
        "enhancement": {
            "model_name": "Starlight Precise",
            "model_version": "1",
            "actual_scale_factor": 1,
        },
        "frame_interpolation": {"model_name": "Aion", "model_version": "1"},
        "program_encode": {"encoder": "cpu"},
        "publication": _publication(tmp_path),
    }
    if selector is not None:
        payload["chapter_selector"] = selector
    return ExpandRequest.model_validate(payload, strict=True)


def _binding_facts(binding: PreparationBinding) -> Av27BindingFacts:
    return Av27BindingFacts(
        preparation_snapshot_current=True,
        preparation_results_current=True,
        admission_artifact_id=binding.admission_artifact.artifact_id,
        sources=tuple(
            Av27SourceBindingFact(
                source_ordinal=item.source_ordinal,
                source_media_artifact_id=item.source_media_artifact.artifact_id,
                source_frame_count=metadata_frame_count(item.source_media_artifact.media_info),
                source_frame_rate=canonical_fraction(
                    metadata_rate(item.source_media_artifact.media_info)
                ),
                effective_video_artifact_id=item.effective_video_artifact.artifact_id,
                effective_video_frame_count=metadata_frame_count(
                    item.effective_video_artifact.media_info
                ),
                effective_video_frame_rate=canonical_fraction(
                    metadata_rate(item.effective_video_artifact.media_info)
                ),
            )
            for item in binding.sources
        ),
    )


def _publication_facts(snapshot: ProjectSnapshot) -> Av27PublicationFacts:
    output = next(
        node
        for node in snapshot.project.graph.nodes
        if node.type_id == "zniku.media.output_file.media"
    )
    target_value = output.parameters["target_path"]
    assert isinstance(target_value, str)
    target = Path(target_value)
    parent = target.parent.resolve(strict=True)
    root = parent.parent.resolve(strict=True)
    return Av27PublicationFacts(
        target_path=target_value,
        resolved_output_root=str(root),
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


@pytest.mark.parametrize(
    ("source_mode", "mr_mode", "source_count"),
    [
        ("program", "off", 1),
        ("program", "external", 1),
        ("pre_chaptered", "off", 2),
        ("pre_chaptered", "external", 2),
    ],
)
def test_preparation_builder_covers_source_and_mr_modes(
    tmp_path: Path,
    source_mode: str,
    mr_mode: str,
    source_count: int,
) -> None:
    request = PrepareRequest.model_validate(
        _prepare_payload(
            tmp_path,
            source_mode=source_mode,
            mr_mode=mr_mode,
            source_count=source_count,
        ),
        strict=True,
    )
    built = build_preparation(request)
    snapshot = ProjectSnapshot(project=built.project, definitions=built.definitions)

    counts: dict[str, int] = {}
    for node in built.project.graph.nodes:
        counts[node.type_id] = counts.get(node.type_id, 0) + 1
    assert counts[SOURCE_PROGRAM_TYPE_ID] == source_count
    assert counts[SOURCE_ADMISSION_TYPE_ID] == 1
    assert counts.get(MOSAIC_RESTORATION_TYPE_ID, 0) == (
        source_count if mr_mode == "external" else 0
    )
    assert len(built.project.graph.edges) == source_count + (
        source_count * 2 if mr_mode == "external" else 0
    )
    assert built.plan.mr_mode == mr_mode
    assert built.plan.chapter_count == built.plan.leaf_count == 0
    GraphValidator(built.definitions).validate(built.project.graph)
    preflight = preflight_av27_profile(snapshot)
    assert preflight.status == "preparation-compatible", preflight.diagnostics
    assert preflight.compatible is True


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload.update({"unknown": True}),
        lambda payload: payload.update({"profile_version": "2.7"}),
        lambda payload: payload["sources"][0].update({"source_ordinal": True}),
        lambda payload: payload["mr"].update({"unknown": "x"}),
    ],
)
def test_prepare_request_rejects_unknown_fields_and_bool(
    tmp_path: Path,
    mutator: Any,
) -> None:
    payload = _prepare_payload(tmp_path)
    mutator(payload)
    with pytest.raises(ValidationError):
        PrepareRequest.model_validate(payload, strict=True)


@pytest.mark.parametrize(
    "field_update",
    [
        {"profile_version": "2.7.1"},
        {"leaf_duration_minutes": True},
        {"enhancement": {"model_name": "E", "actual_scale_factor": True}},
        {"publication": {"unexpected": True}},
        {"chapter_selector": {"mode": "near", "times": ["1"]}},
    ],
)
def test_expand_request_rejects_unknown_fields_bool_and_selector(
    tmp_path: Path,
    field_update: dict[str, Any],
) -> None:
    payload: dict[str, Any] = {
        "profile_version": "2.7.0",
        "preparation_run_id": _id(),
        "chapter_selector": {"mode": "single"},
        "leaf_duration_minutes": 1,
        "enhancement": {"model_name": "E", "actual_scale_factor": 1},
        "frame_interpolation": {"model_name": "F"},
        "program_encode": {"encoder": "cpu"},
        "publication": _publication(tmp_path),
    }
    for key, value in field_update.items():
        if key in {"enhancement", "publication"}:
            payload[key].update(value)
        else:
            payload[key] = value
    with pytest.raises(ValidationError):
        ExpandRequest.model_validate(payload, strict=True)


def _planning_source(*, frames: int, fps: Fraction) -> Any:
    return SimpleNamespace(
        ordinal=0,
        frame_count=frames,
        frame_rate=fps,
        chapter_label=None,
    )


def test_chapter_selectors_are_server_derived_and_half_up() -> None:
    source = _planning_source(frames=100, fps=Fraction(25, 1))
    single = resolve_chapter_plan(
        (source,), source_mode="program", selector=SingleChapterSelector(mode="single")
    )
    frames = resolve_chapter_plan(
        (source,),
        source_mode="program",
        selector=ExactFramesChapterSelector(mode="exact_frames", frames=(20, 70)),
    )
    times = resolve_chapter_plan(
        (source,),
        source_mode="program",
        selector=ExactTimesChapterSelector(mode="exact_times", times=("1/50", "2")),
    )

    assert [(item.start_frame, item.end_frame) for item in single] == [(0, 100)]
    assert [(item.start_frame, item.end_frame) for item in frames] == [
        (0, 20),
        (20, 70),
        (70, 100),
    ]
    # 1/50 * 25 = 1/2，冻结规则为正数 half-up，因此落在 frame 1。
    assert [(item.start_frame, item.end_frame) for item in times] == [
        (0, 1),
        (1, 50),
        (50, 100),
    ]


def test_exact_time_collision_and_out_of_range_fail_closed() -> None:
    source = _planning_source(frames=100, fps=Fraction(10, 1))
    collision = ExactTimesChapterSelector(
        mode="exact_times",
        times=("1/20", "3/50"),
    )
    with pytest.raises(Av27TemplateError, match="E_AV27_PLAN_BOUNDARY_COLLISION"):
        resolve_chapter_plan((source,), source_mode="program", selector=collision)

    with pytest.raises(Av27TemplateError, match="E_AV27_PLAN_BOUNDARY_RANGE"):
        resolve_chapter_plan(
            (source,),
            source_mode="program",
            selector=ExactFramesChapterSelector(mode="exact_frames", frames=(100,)),
        )


def test_excel_labels_and_leaf_ties_to_even_cover_every_frame() -> None:
    assert (excel_chapter_label(0), excel_chapter_label(25), excel_chapter_label(26)) == (
        "A",
        "Z",
        "AA",
    )
    chapters = (
        ResolvedChapterPlan(
            chapter_id="chapter-0001",
            chapter_ordinal=0,
            label="A",
            source_ordinal=0,
            start_frame=0,
            end_frame=5,
        ),
    )
    # 1/24 fps * 60 秒 = 2.5 frames，Python ties-to-even 固定为 2。
    leaves = derive_leaf_plan(
        chapters,
        frame_rate=Fraction(1, 24),
        leaf_duration_minutes=1,
    )
    assert [(item.start_frame, item.end_frame) for item in leaves] == [
        (0, 2),
        (2, 4),
        (4, 5),
    ]
    assert [item.leaf_ordinal for item in leaves] == [0, 1, 2]
    assert [item.port_id for item in leaves] == ["leaf-0001", "leaf-0002", "leaf-0003"]


@pytest.mark.parametrize(
    ("rate", "label"),
    [
        (Fraction(30, 1), "30p"),
        (Fraction(30_000, 1_001), "29p97"),
        (Fraction(60, 1), "60p"),
        (Fraction(60_000, 1_001), "59p94"),
    ],
)
def test_canonical_naming_supports_four_frozen_rates(
    tmp_path: Path,
    rate: Fraction,
    label: str,
) -> None:
    request = PublicationRequest.model_validate(_publication(tmp_path), strict=True)
    target = canonical_publication_target(
        request,
        mr_mode="off",
        final_frame_rate=rate,
        height=1080,
    )
    assert target.name == f"Example (2026) - Enhanced FI{label} 1080p.mkv"


def test_canonical_naming_rejects_reserved_missing_parent_and_requires_overwrite(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    reserved = PublicationRequest(
        output_root=str(root.resolve()),
        title="CON.txt",
        year="2026",
        overwrite=False,
    )
    with pytest.raises(Av27TemplateError, match="E_AV27_NAMING_RESERVED"):
        canonical_publication_target(
            reserved,
            mr_mode="off",
            final_frame_rate=Fraction(60, 1),
            height=1080,
        )

    missing = PublicationRequest(
        output_root=str(root.resolve()),
        title="Missing",
        year="2026",
        overwrite=False,
    )
    with pytest.raises(Av27TemplateError, match="E_AV27_NAMING_PARENT"):
        canonical_publication_target(
            missing,
            mr_mode="off",
            final_frame_rate=Fraction(60, 1),
            height=1080,
        )

    parent = root / "Example (2026)"
    parent.mkdir()
    target = parent / "Example (2026) - MR Enhanced FI60p 1080p.mkv"
    target.write_bytes(b"existing")
    no_overwrite = PublicationRequest(
        output_root=str(root.resolve()),
        title="Example",
        year="2026",
        overwrite=False,
    )
    with pytest.raises(Av27TemplateError, match="E_AV27_NAMING_EXISTS"):
        canonical_publication_target(
            no_overwrite,
            mr_mode="external",
            final_frame_rate=Fraction(60, 1),
            height=1080,
        )
    overwrite = no_overwrite.model_copy(update={"overwrite": True})
    assert (
        canonical_publication_target(
            overwrite,
            mr_mode="external",
            final_frame_rate=Fraction(60, 1),
            height=1080,
        )
        == target.resolve()
    )


@pytest.mark.parametrize(
    ("source_mode", "mr_mode", "frames", "selector", "expected_chapters"),
    [
        ("program", "off", (3600,), {"mode": "single"}, 1),
        (
            "program",
            "external",
            (5000,),
            {"mode": "exact_frames", "frames": [1200, 3000]},
            3,
        ),
        ("pre_chaptered", "off", (2000, 2500), None, 2),
        ("pre_chaptered", "external", (2000, 2500), None, 2),
    ],
)
def test_expanded_builder_emits_complete_valid_profile_graph(
    tmp_path: Path,
    source_mode: str,
    mr_mode: str,
    frames: tuple[int, ...],
    selector: dict[str, Any] | None,
    expected_chapters: int,
) -> None:
    prepare = PrepareRequest.model_validate(
        _prepare_payload(
            tmp_path,
            source_mode=source_mode,
            mr_mode=mr_mode,
            source_count=len(frames),
        ),
        strict=True,
    )
    preparation_build = build_preparation(prepare)
    preparation = ProjectSnapshot(
        project=preparation_build.project,
        definitions=preparation_build.definitions,
    )
    binding = _binding(
        preparation,
        source_mode=source_mode,
        mr_mode=mr_mode,
        frames=frames,
    )
    request = _expand_request(
        tmp_path,
        binding.preparation_run_id,
        selector=selector,
    )

    expanded = build_expanded(preparation, request, binding)
    snapshot = ProjectSnapshot(project=expanded.project, definitions=expanded.definitions)
    GraphValidator(expanded.definitions).validate(expanded.project.graph)
    assert expanded.plan.chapter_count == expected_chapters
    assert expanded.plan.leaf_count >= expected_chapters
    assert expanded.plan.output_target_path is not None

    nodes = expanded.project.graph.nodes
    counts: dict[str, int] = {}
    for node in nodes:
        key = "split" if node.type_id.startswith(ATOMIC_SPLIT_TYPE_PREFIX) else node.type_id
        counts[key] = counts.get(key, 0) + 1
    assert counts["split"] == 1
    assert counts[ENHANCEMENT_TYPE_ID] == expanded.plan.leaf_count
    assert counts[MERGE_VIDEO_TYPE_ID] == expected_chapters
    assert counts[FRAME_INTERPOLATION_TYPE_ID] == expected_chapters
    assert counts[PROGRAM_ENCODE_TYPE_ID] == 1
    assert counts[FINAL_MUX_TYPE_ID] == 1

    split = next(node for node in nodes if node.type_id.startswith(ATOMIC_SPLIT_TYPE_PREFIX))
    assert split.parameters["source_mode"] == source_mode
    assert split.parameters["leaf_duration_minutes"] == 1
    assert (
        split.parameters["planned_admission_artifact_id"] == binding.admission_artifact.artifact_id
    )
    assert split.parameters["planned_effective_video_artifact_ids"] == [
        item.effective_video_artifact.artifact_id for item in binding.sources
    ]
    assert len(cast(list[object], split.parameters["chapters"])) == expected_chapters
    assert len(cast(list[object], split.parameters["segments"])) == expanded.plan.leaf_count
    if source_mode == "program":
        assert split.parameters["chapter_selector"] == selector
    else:
        assert "chapter_selector" not in split.parameters

    final = next(node for node in nodes if node.type_id == FINAL_MUX_TYPE_ID)
    assert final.parameters["mr_mode"] == mr_mode
    assert final.parameters["expected_program_frames"] == sum(frames) * 2
    assert [
        item["source_ordinal"] for item in cast(list[dict[str, Any]], final.parameters["sources"])
    ] == list(range(len(frames)))

    preflight = preflight_av27_profile(
        snapshot,
        binding_facts=_binding_facts(binding),
        publication_facts=_publication_facts(snapshot),
    )
    assert preflight.status == "expanded-compatible", preflight.diagnostics
    assert preflight.compatible is True


def test_expanded_builder_normalizes_non_1080_source_before_enhancement(
    tmp_path: Path,
) -> None:
    """AtomicSplit 后的 stage geometry 固定为 1080p，不继承 admitted Source 尺寸。"""

    request = PrepareRequest.model_validate(_prepare_payload(tmp_path), strict=True)
    preparation_build = build_preparation(request)
    preparation = ProjectSnapshot(
        project=preparation_build.project,
        definitions=preparation_build.definitions,
    )
    binding = _binding(
        preparation,
        source_mode="program",
        mr_mode="off",
        frames=(3600,),
        geometry=(1280, 720),
    )
    expanded = build_expanded(
        preparation,
        _expand_request(
            tmp_path,
            binding.preparation_run_id,
            selector={"mode": "single"},
        ),
        binding,
    )
    enhancement = next(
        node for node in expanded.project.graph.nodes if node.type_id == ENHANCEMENT_TYPE_ID
    )

    assert enhancement.parameters["expected_input_geometry"] == {
        "width": 1920,
        "height": 1080,
        "sample_aspect_ratio": "1/1",
    }
    assert enhancement.parameters["expected_output_geometry"] == {
        "width": 1920,
        "height": 1080,
        "sample_aspect_ratio": "1/1",
    }
    preflight = preflight_av27_profile(
        (snapshot := ProjectSnapshot(project=expanded.project, definitions=expanded.definitions)),
        binding_facts=_binding_facts(binding),
        publication_facts=_publication_facts(snapshot),
    )
    assert preflight.status == "expanded-compatible", preflight.diagnostics
