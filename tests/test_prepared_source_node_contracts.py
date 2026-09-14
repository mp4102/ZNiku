"""以纯合成直接输入与 header 验证新节点闭环，不声明任何真实 Aion 验收。"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from fractions import Fraction
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from zniku.avenhance_v27.definitions import built_in_av27_definitions
from zniku.avenhance_v27.probe import (
    AV27_NAMESPACE,
    Av27MediaError,
    Av27MediaHeader,
    Av27VideoHeader,
)
from zniku.chapter_overlap import (
    AdmittedTimeline,
    AverageChapterSelector,
    ChapterSettings,
    ExactFramesChapterSelector,
    plan_chapters_and_leaves,
)
from zniku.graph import NodeInstance
from zniku.media.probe import MediaNodeError
from zniku.prepared_source import validators
from zniku.prepared_source.definitions import (
    atomic_split_definition,
    built_in_overlap_definitions,
    overlap_validators,
)
from zniku.prepared_source.node_contracts import (
    OVERLAP_NAMESPACE,
    CandidateFiProfile,
    ChapterBinding,
    ExternalMetadata,
    Geometry,
    NodeContract,
    OverlapMetadata,
    Signal,
    SourceExpectation,
    preflight,
)
from zniku.runtime import (
    FrameRange,
    NodeExecutionRequest,
    NodeRunner,
    NodeValidatorContext,
    RunnerInput,
)
from zniku.runtime.runner import ValidatedOutput


@pytest.fixture(autouse=True)
def synthetic_audio_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(validators, "verify_audio_origins", lambda source, output: None)
    from zniku.prepared_source import audio
    from zniku.source_preparation import contracts

    monkeypatch.setattr(contracts, "validate_audio_sources", lambda gate, inputs: None)
    monkeypatch.setattr(audio, "verify_audio_content", lambda source, output: None)


@dataclass(frozen=True)
class Step:
    role: str
    inputs: tuple[RunnerInput, ...]
    parameters: dict[str, Any]
    contract: NodeContract


def _registered(tmp_path: Path, metadata: OverlapMetadata) -> RunnerInput:
    identifier = str(uuid4())
    return RunnerInput(
        port_id="video",
        artifact_id=identifier,
        kind="VideoFile",
        path=tmp_path / f"{identifier}.mov",
        producer_node_run_id=str(uuid4()),
        producer_port_id=metadata.leaf.leaf_id
        if metadata.role == "split" and metadata.leaf
        else "video",
        media_info={OVERLAP_NAMESPACE: metadata.model_dump(mode="json")},
    )


def _chapter(item: RunnerInput) -> ChapterBinding:
    result = OverlapMetadata.model_validate(item.media_info[OVERLAP_NAMESPACE]).chapter
    assert result is not None
    return result


def pipeline(
    tmp_path: Path,
    *,
    n: int = 1801,
    chapters: int = 3,
    points: tuple[int, ...] | None = None,
    profile: CandidateFiProfile | None = None,
    rate: str = "30000/1001",
    restored: bool = False,
) -> list[Step]:
    """只创建小 JSON gate 与局部模型，视频路径无需存在也不触发媒体 I/O。"""
    source_id, audio_id, gate_id = str(uuid4()), str(uuid4()), str(uuid4())
    selector = (
        AverageChapterSelector(count=chapters)
        if points is None
        else ExactFramesChapterSelector(mode="exact_frames", frames=points)
    )
    plan = plan_chapters_and_leaves(
        AdmittedTimeline(artifact_id=source_id, frame_count=n, frame_rate=rate),
        ChapterSettings(chapter_selector=selector, leaf_max_minutes=1),
    )
    profile = profile or CandidateFiProfile()
    reference_media_id, diagnosis_id = str(uuid4()), str(uuid4())
    namespace = {
        "schema_version": "zniku.source.prepared.admission/1",
        "original_media_artifact_id": audio_id,
        "reference_media_artifact_id": reference_media_id,
        "diagnosis_artifact_id": diagnosis_id,
        "source_frame_count": n,
        "frame_rate": rate,
        "original_video_start": "0/1",
        "reference_start": "0/1",
        "preparation_strategy": "external-preservation/1",
        "geometry": {"width": 1920, "height": 1080, "sample_aspect_ratio": "1/1"},
        "signal": Signal().model_dump(),
        "audio_policy": "none",
        "audio_bindings": [{"artifact_id": audio_id, "ordinal": 0, "tracks": []}],
    }
    source = RunnerInput(
        "videos",
        source_id,
        "VideoFile",
        tmp_path / "source.mkv",
        ordinal=0,
        producer_port_id="video",
        media_info={OVERLAP_NAMESPACE: {"role": "reference", "admission": namespace}},
    )
    gate_path = tmp_path / "admission.json"
    gate_path.write_text(json.dumps(namespace), encoding="utf-8")
    gate = RunnerInput(
        "gate",
        gate_id,
        "DataFile",
        gate_path,
        producer_port_id="gate",
        media_info={OVERLAP_NAMESPACE: {"role": "admission", "admission": namespace}},
    )
    steps: list[Step] = []

    def add(
        role: str, inputs: tuple[RunnerInput, ...], params: dict[str, Any]
    ) -> tuple[RunnerInput, ...]:
        contract = preflight(role, inputs, params)
        steps.append(Step(role, inputs, params, contract))
        return tuple(_registered(tmp_path, output.metadata) for output in contract.outputs)

    expectation = SourceExpectation(
        reference_video_artifact_id=source_id,
        original_media_artifact_id=audio_id,
        reference_media_artifact_id=reference_media_id,
        diagnosis_artifact_id=diagnosis_id,
        audio_source_artifact_ids=(audio_id,),
        admission_artifact_id=gate_id,
        frame_count=n,
        frame_rate=rate,
    )
    if restored:
        external = ExternalMetadata(
            producer_type_id="zniku.prepared_source.external.mp4",
            source=expectation,
            declared_container="mp4",
            model_name="synthetic",
            geometry=Geometry(width=1920, height=1080),
            signal=Signal(),
        )
        source = replace(
            source,
            artifact_id=str(uuid4()),
            media_info={OVERLAP_NAMESPACE: external.model_dump(mode="json")},
        )
    leaves = add(
        "split",
        (source, gate),
        {
            "plan": plan.model_dump(mode="json"),
            "source": expectation.model_dump(mode="json"),
        },
    )
    source_data = steps[0].contract.source.expectation().model_dump(mode="json")
    enhanced: list[RunnerInput] = []
    for leaf in leaves:
        m = OverlapMetadata.model_validate(leaf.media_info[OVERLAP_NAMESPACE])
        assert m.chapter is not None and m.leaf is not None
        enhanced.extend(
            add(
                "enhancement",
                (leaf,),
                {
                    "source": source_data,
                    "chapter": m.chapter.model_dump(),
                    "leaf": m.leaf.model_dump(),
                    "expected_input_geometry": m.geometry.model_dump(),
                    "expected_output_geometry": Geometry(width=3840, height=2160).model_dump(),
                    "model_name": "synthetic-enhancer",
                    "actual_scale_factor": 2,
                },
            )
        )
    merges: list[RunnerInput] = []
    for planned_chapter in plan.chapters:
        members = tuple(
            item for item in enhanced if _chapter(item).ordinal == planned_chapter.ordinal
        )
        first = OverlapMetadata.model_validate(members[0].media_info[OVERLAP_NAMESPACE])
        assert first.chapter is not None
        merges.extend(
            add(
                "merge",
                tuple(replace(item, port_id="videos", ordinal=i) for i, item in enumerate(members)),
                {"source": source_data, "chapter": first.chapter.model_dump()},
            )
        )
    crops: list[RunnerInput] = []
    for merge in merges:
        m = OverlapMetadata.model_validate(merge.media_info[OVERLAP_NAMESPACE])
        assert m.chapter is not None
        chapter = m.chapter
        params = {
            "source": source_data,
            "chapter": chapter.model_dump(),
            "fi_profile": profile.model_dump(),
        }
        a, b = (
            max(0, chapter.start_frame - profile.left_context_frames),
            min(n, chapter.end_frame + profile.right_context_frames),
        )
        neighbors = tuple(
            item
            for item in merges
            if _chapter(item).end_frame > a and _chapter(item).start_frame < b
        )
        contexts = add(
            "context",
            tuple(replace(item, port_id="chapters", ordinal=i) for i, item in enumerate(neighbors)),
            params,
        )
        raw = add("fi", contexts, params)
        crops.extend(add("crop", raw, params))
    program = add(
        "program",
        tuple(replace(item, port_id="chapters", ordinal=i) for i, item in enumerate(crops)),
        {"source": source_data, "chapter_count": plan.chapter_count, "encoder": "cpu"},
    )
    original = RunnerInput(
        "sources",
        audio_id,
        "MediaFile",
        tmp_path / "source.mkv",
        ordinal=0,
        producer_port_id="source_media",
        media_info={AV27_NAMESPACE: namespace},
    )
    add(
        "final",
        (*program, original, gate),
        {"source": source_data, "mr_mode": "external" if restored else "off"},
    )
    return steps


def _step(steps: list[Step], role: str) -> Step:
    return next(step for step in steps if step.role == role)


def _header(path: Path, m: OverlapMetadata) -> Av27MediaHeader:
    role, rate = m.role, Fraction(m.frame_rate)
    encoded = role in {"program", "final"}
    video = Av27VideoHeader(
        index=0,
        codec="ffv1" if role == "split" else "hevc" if encoded else "prores",
        profile="Main 10" if encoded else "HQ",
        codec_tag_string="hvc1" if encoded else "apch",
        width=m.geometry.width,
        height=m.geometry.height,
        pixel_format="yuv420p10le" if role == "split" or encoded else "yuv422p10le",
        frame_rate=rate,
        avg_frame_rate=rate,
        r_frame_rate=rate,
        time_base=Fraction(1, rate.numerator),
        frame_count=m.frame_count,
        sample_aspect_ratio="1:1",
        field_order="progressive",
        rotation=0,
        color_range="tv",
        color_space="bt709",
        color_transfer="bt709",
        color_primaries="bt709",
        chroma_location="left",
        hdr_side_data=(),
        duration_seconds=float(m.frame_count / rate),
    )
    return Av27MediaHeader(
        path=path,
        format_name="matroska" if role in {"split", "final"} else "mov,mp4",
        duration_seconds=video.duration_seconds,
        streams=("video",),
        videos=(video,),
        audios=(),
        others=(),
        chapter_count=0,
    )


def validator_context(
    step: Step, tmp_path: Path
) -> tuple[NodeValidatorContext, dict[Path, Av27MediaHeader]]:
    definitions = {d.type_id: d for d in built_in_overlap_definitions(len(step.contract.outputs))}
    definition = definitions[step.contract.outputs[0].metadata.producer_type_id]
    names = {
        "enhancement": "enhancement.mov",
        "merge": "merge.mov",
        "context": "context.mov",
        "fi": "fi.raw.mov",
        "crop": "fi.crop.mov",
        "program": "program.mp4",
        "final": "final.mkv",
    }
    outputs: list[ValidatedOutput] = []
    headers: dict[Path, Av27MediaHeader] = {}
    for contract in step.contract.outputs:
        m = contract.metadata
        path = tmp_path / (f"{contract.port_id}.mkv" if step.role == "split" else names[step.role])
        frame_range = (
            None
            if m.leaf is None or step.role != "split"
            else FrameRange(start_frame=m.leaf.start_frame, end_frame=m.leaf.end_frame)
        )
        outputs.append(
            ValidatedOutput(
                contract.port_id,
                "MediaFile" if step.role == "final" else "VideoFile",
                path,
                100,
                1,
                {},
                {} if step.role in {"fi", "enhancement"} else {"output_frames": m.frame_count},
                frame_range,
            )
        )
        headers[path] = _header(path, m)
    node = NodeInstance(
        node_id="test-node",
        type_id=definition.type_id,
        definition_version=definition.version,
        parameters=step.parameters,
    )
    context = NodeValidatorContext(
        NodeExecutionRequest(str(uuid4()), 1, definition, node, step.inputs),
        tmp_path,
        tuple(outputs),
    )
    return context, headers


def test_full_local_pipeline_all_eight_validators_probe_and_extend_only_new_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    steps = pipeline(tmp_path)
    seen: set[str] = set()
    for step in steps:
        context, headers = validator_context(step, tmp_path)
        probed: list[Path] = []

        def probe(
            path: Path,
            seen_paths: list[Path] = probed,
            current_headers: dict[Path, Av27MediaHeader] = headers,
        ) -> Av27MediaHeader:
            seen_paths.append(path)
            return current_headers.get(path, next(iter(current_headers.values())))

        monkeypatch.setattr(validators, "probe_header", probe)
        result = validators._validate(context, step.role)
        assert result.passed, result.message
        assert NodeRunner._strict_media_info_extensions(
            result.media_info_extensions, outputs=context.outputs
        )
        assert all(o.path in probed for o in context.outputs)
        assert all(
            set(extension) == {OVERLAP_NAMESPACE}
            for extension in result.media_info_extensions.values()
        )
        assert result.summary["fi_acceptance"] == "pending_real_acceptance"
        seen.add(step.role)
    assert len(seen) == 8
    crops = [step.contract.outputs[0].metadata for step in steps if step.role == "crop"]
    assert sum(m.frame_count for m in crops) == 2 * 1801 - 1
    assert steps[-1].contract.outputs[0].metadata.frame_count == 3602


def test_exact_899_902_context_geometry_and_single_program_tail(tmp_path: Path) -> None:
    steps = pipeline(tmp_path, points=(899,))
    merges = [s.contract.outputs[0].metadata.frame_count for s in steps if s.role == "merge"]
    crops = [s.contract.outputs[0].metadata for s in steps if s.role == "crop"]
    assert merges == [899, 902]
    assert [m.frame_count for m in crops] == [1798, 1803]
    assert crops[0].context is not None and crops[1].context is not None
    assert (
        crops[0].context.global_end_half_frame == crops[1].context.global_start_half_frame == 1798
    )
    assert _step(steps, "program").contract.outputs[0].metadata.frame_count == 3602


@pytest.mark.parametrize("n,chapters", [(2, 1), (2, 2), (5, 5), (19, 7), (63, 3), (1801, 1)])
def test_short_and_multiple_neighbor_chapters_cover_all_half_frames(
    tmp_path: Path, n: int, chapters: int
) -> None:
    steps = pipeline(
        tmp_path,
        n=n,
        chapters=chapters,
        profile=CandidateFiProfile(left_context_frames=4, right_context_frames=2),
    )
    positions: list[int] = []
    for step in steps:
        if step.role == "crop":
            ctx = step.contract.outputs[0].metadata.context
            assert ctx is not None
            positions.extend(range(ctx.global_start_half_frame, ctx.global_end_half_frame))
    assert positions == list(range(2 * n - 1))


@pytest.mark.parametrize(
    "field,value",
    [
        ("left_context_frames", True),
        ("left_context_frames", -1),
        ("left_context_frames", 241),
        ("right_context_frames", 0),
        ("right_context_frames", 1.0),
        ("right_context_frames", 241),
        ("minimum_input_frames", 1),
        ("minimum_input_frames", 257),
        ("software_version", "2.7.0"),
        ("model_name", "other"),
        ("status", "verified"),
        ("phase", "odd"),
        ("command", "cmd"),
    ],
)
def test_candidate_profile_is_strict_and_never_real_verified(field: str, value: Any) -> None:
    with pytest.raises((ValidationError, Av27MediaError)):
        CandidateFiProfile.model_validate({field: value})


def test_minimum_input_is_not_filled_with_clones(tmp_path: Path) -> None:
    with pytest.raises(Av27MediaError, match="INPUT_TOO_SHORT"):
        pipeline(tmp_path, n=1, chapters=1)


@pytest.mark.parametrize(
    "field,value",
    [
        ("producer_version", "0.2.1"),
        ("producer_type_id", "zniku.avenhance.v27.frame_interpolation.external"),
        ("frame_count", True),
        ("frame_count", 3599),
        ("frame_rate", "60/1"),
        ("unknown", 1),
    ],
)
def test_metadata_rejects_wrong_producer_version_rate_count_and_unknowns(
    tmp_path: Path, field: str, value: Any
) -> None:
    metadata = _step(pipeline(tmp_path), "fi").contract.outputs[0].metadata.model_dump()
    metadata[field] = value
    with pytest.raises((ValidationError, Av27MediaError)):
        OverlapMetadata.model_validate(metadata)


@pytest.mark.parametrize(
    "field",
    [
        "crop_start_frame",
        "crop_end_frame",
        "global_start_half_frame",
        "global_end_half_frame",
        "raw_fi_frame_count",
    ],
)
def test_context_metadata_recomputes_offsets_not_just_lengths(tmp_path: Path, field: str) -> None:
    metadata = _step(pipeline(tmp_path), "fi").contract.outputs[0].metadata.model_dump()
    metadata["context"][field] += 1
    with pytest.raises(Av27MediaError, match="CONTEXT_GEOMETRY"):
        OverlapMetadata.model_validate(metadata)


def test_equal_length_wrong_crop_and_wrong_source_part_fail(tmp_path: Path) -> None:
    original = _step(pipeline(tmp_path), "fi").contract.outputs[0].metadata
    for mode in ("crop", "part"):
        data = original.model_dump()
        ctx = data["context"]
        if mode == "crop":
            ctx["crop_start_frame"] += 1
            ctx["crop_end_frame"] += 1
        else:
            ctx["parts"][0]["start_frame"] += 1
            ctx["parts"][0]["end_frame"] += 1
        with pytest.raises(Av27MediaError):
            OverlapMetadata.model_validate(data)


@pytest.mark.parametrize(
    "role", ["enhancement", "merge", "context", "fi", "crop", "program", "final"]
)
def test_direct_input_wrong_source_and_old_namespace_fail_before_probe(
    tmp_path: Path, role: str
) -> None:
    step = _step(pipeline(tmp_path), role)
    for mode in ("source", "old", "producer"):
        item = step.inputs[0]
        data: dict[str, Any] = dict(item.media_info)
        if mode == "old":
            data = {AV27_NAMESPACE: data[OVERLAP_NAMESPACE]}
        else:
            data[OVERLAP_NAMESPACE] = json.loads(json.dumps(data[OVERLAP_NAMESPACE]))
            if mode == "source":
                data[OVERLAP_NAMESPACE]["source"]["reference_video_artifact_id"] = str(uuid4())
            else:
                data[OVERLAP_NAMESPACE]["producer_version"] = "0.2.1"
        with pytest.raises((ValidationError, Av27MediaError)):
            preflight(role, (replace(item, media_info=data), *step.inputs[1:]), step.parameters)


@pytest.mark.parametrize("role", ["merge", "context", "program"])
@pytest.mark.parametrize("mutation", ["missing", "duplicate", "order"])
def test_ordered_inputs_require_exact_coverage(tmp_path: Path, role: str, mutation: str) -> None:
    step = _step(pipeline(tmp_path, n=7201, chapters=2), role)
    assert len(step.inputs) >= 2
    inputs = (
        step.inputs[:-1]
        if mutation == "missing"
        else (step.inputs[0],) * len(step.inputs)
        if mutation == "duplicate"
        else tuple(reversed(step.inputs))
    )
    # 即使攻击者重新编 edge ordinal，源范围/章内 ordinal 仍必须失败。
    inputs = tuple(replace(item, ordinal=i) for i, item in enumerate(inputs))
    with pytest.raises((ValidationError, Av27MediaError)):
        preflight(role, inputs, step.parameters)


def test_admission_rejects_same_length_unrelated_original_source(tmp_path: Path) -> None:
    steps = pipeline(tmp_path)
    step = _step(steps, "split")
    wrong = dict(
        step.parameters,
        source=dict(step.parameters["source"], original_media_artifact_id=str(uuid4())),
    )
    with pytest.raises(Av27MediaError, match="ADMISSION_BINDING"):
        preflight("split", step.inputs, wrong)
    gate = next(item for item in step.inputs if item.port_id == "gate")
    gate.path.write_text('{"schema":1,"schema":2}', encoding="utf-8")
    with pytest.raises(MediaNodeError, match="REPORT_INVALID"):
        preflight("split", step.inputs, step.parameters)


@pytest.mark.parametrize(
    "role", ["split", "enhancement", "merge", "context", "fi", "crop", "program", "final"]
)
def test_validator_rejects_wrong_output_count_and_never_extends_partial_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    step = _step(pipeline(tmp_path), role)
    context, headers = validator_context(step, tmp_path)
    path = context.outputs[0].path
    media = headers[path]
    assert media.video.frame_count is not None
    headers[path] = replace(
        media, videos=(replace(media.video, frame_count=media.video.frame_count + 1),)
    )
    monkeypatch.setattr(
        validators, "probe_header", lambda path: headers.get(path, next(iter(headers.values())))
    )
    result = validators._validate(context, role)
    assert not result.passed
    assert not result.media_info_extensions


@pytest.mark.parametrize(
    "change",
    [
        "geometry",
        "signal",
        "rate",
        "timebase",
        "codec",
        "producer",
        "producer_extra",
        "frame_range",
    ],
)
def test_context_output_full_lightweight_media_contract_is_not_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    context, headers = validator_context(_step(pipeline(tmp_path), "context"), tmp_path)
    output = context.outputs[0]
    media = headers[output.path]
    if change == "producer":
        context = replace(
            context, outputs=(replace(output, producer_metadata={"output_frames": True}),)
        )
    elif change == "producer_extra":
        context = replace(
            context,
            outputs=(
                replace(
                    output,
                    producer_metadata={
                        "output_frames": media.video.frame_count,
                        "source": "fabricated",
                    },
                ),
            ),
        )
    elif change == "frame_range":
        context = replace(
            context, outputs=(replace(output, frame_range=FrameRange(start_frame=0, end_frame=10)),)
        )
    else:
        variants: dict[str, dict[str, Any]] = {
            "geometry": {"width": 1920},
            "signal": {"color_space": "bt2020nc"},
            "rate": {"frame_rate": Fraction(30)},
            "timebase": {"time_base": Fraction(1, 1000)},
            "codec": {"codec": "h264"},
        }
        headers[output.path] = replace(media, videos=(replace(media.video, **variants[change]),))
    monkeypatch.setattr(validators, "probe_header", headers.__getitem__)
    result = validators.validate_fi_context(context)
    assert not result.passed
    assert result.media_info_extensions == {}


def test_all_definitions_strict_roundtrip_and_no_old_identity_replacement(tmp_path: Path) -> None:
    steps = pipeline(tmp_path)
    definitions = built_in_overlap_definitions(3)
    assert len(definitions) == 8 and len(overlap_validators()) == 9
    assert {d.type_id for d in definitions}.isdisjoint(
        d.type_id for d in built_in_av27_definitions(3)
    )
    for step in steps:
        context, _ = validator_context(step, tmp_path)
        definition = context.request.definition
        schema = Draft202012Validator(definition.model_dump(mode="json")["parameter_schema"])
        assert not list(schema.iter_errors(step.parameters))
        assert list(schema.iter_errors(dict(step.parameters, unknown=True)))
        assert definition.model_validate_json(definition.model_dump_json()) == definition
        metadata = step.contract.outputs[0].metadata
        assert OverlapMetadata.model_validate_json(metadata.model_dump_json()) == metadata
    with pytest.raises(ValueError):
        atomic_split_definition(True)


def test_parameters_model_construct_cannot_bypass_revalidation(tmp_path: Path) -> None:
    step = _step(pipeline(tmp_path), "context")
    invalid = CandidateFiProfile.model_construct(right_context_frames=0)
    with pytest.raises((ValidationError, Av27MediaError)):
        preflight("context", step.inputs, dict(step.parameters, fi_profile=invalid))


@pytest.mark.parametrize("ordinal", [False, 0.0, "0"])
def test_direct_edge_ordinal_is_strict_integer(tmp_path: Path, ordinal: Any) -> None:
    step = _step(pipeline(tmp_path), "merge")
    with pytest.raises(Av27MediaError, match="INPUT_ORDER"):
        preflight("merge", (replace(step.inputs[0], ordinal=ordinal),), step.parameters)


def test_direct_artifact_identity_is_not_an_arbitrary_string(tmp_path: Path) -> None:
    step = _step(pipeline(tmp_path), "fi")
    with pytest.raises(Av27MediaError, match="INPUT_ID"):
        preflight("fi", (replace(step.inputs[0], artifact_id="unbound"),), step.parameters)


@pytest.mark.parametrize("restored", [False, True])
def test_effective_binding_appears_only_at_runtime_and_propagates(
    tmp_path: Path, restored: bool
) -> None:
    steps = pipeline(tmp_path, restored=restored)
    source = steps[0].contract.source
    assert (source.effective_video_artifact_id != source.reference_video_artifact_id) == restored
    for step in steps:
        assert step.contract.source == source
        assert "effective_video_artifact_id" not in step.parameters["source"]
        assert step.contract.outputs[0].metadata.source == source
    assert (
        steps[0].parameters["plan"]["source"]["artifact_id"] == source.reference_video_artifact_id
    )


@pytest.mark.parametrize("role", ["merge", "context", "program"])
def test_runtime_merge_rejects_mixed_effective_sources(tmp_path: Path, role: str) -> None:
    step = _step(pipeline(tmp_path, n=7201, chapters=2, restored=True), role)
    first, *others = step.inputs
    changed = json.loads(json.dumps(first.media_info))
    changed[OVERLAP_NAMESPACE]["source"]["effective_video_artifact_id"] = str(uuid4())
    with pytest.raises(Av27MediaError, match="INPUT_CONTRACT"):
        preflight(role, (replace(first, media_info=changed), *others), step.parameters)


def test_split_rejects_unregistered_future_uuid_and_old_mr(tmp_path: Path) -> None:
    step = _step(pipeline(tmp_path), "split")
    source, gate = step.inputs
    with pytest.raises((Av27MediaError, ValidationError)):
        preflight("split", (replace(source, artifact_id=str(uuid4())), gate), step.parameters)
    wrong = json.loads(json.dumps(step.parameters))
    wrong["plan"]["source"]["artifact_id"] = str(uuid4())
    with pytest.raises(Av27MediaError, match="PLAN_SOURCE"):
        preflight("split", step.inputs, wrong)


def test_new_exact_definitions_do_not_change_old_definitions() -> None:
    from zniku.chapter_overlap.definitions import built_in_overlap_definitions as old

    previous = old()
    current = built_in_overlap_definitions()
    assert {item.type_id for item in current}.isdisjoint(item.type_id for item in previous)
    assert all(item.version == "0.3.2" for item in previous)
    assert all(item.version == "0.3.4" for item in current)
    assert previous == old()
