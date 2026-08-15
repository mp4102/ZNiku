"""验证 ZBaton vNext draft.2 的确定性投影、DAG 约束与下游继承。"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from zniku.contracts import JsonObject
from zniku.history import (
    AudioTrackSnapshot,
    CompletedStageHistory,
    FinalMediaSnapshot,
    HistoryProducer,
    HistorySubject,
    MediaSnapshot,
    ProcessingModel,
    SourceMediaSnapshot,
    TimelineSnapshot,
    VideoSnapshot,
    ZBatonDocument,
    ZBatonEnvelope,
    ZBatonProjectionInput,
    inherit_zbaton,
    project_zbaton,
)


def _snapshot(media_id: str, filename: str, digest_character: str) -> MediaSnapshot:
    return MediaSnapshot(
        media_id=media_id,
        filename=filename,
        size_bytes=1024,
        sha256=digest_character * 64,
        container="matroska",
        timeline=TimelineSnapshot(duration="00:00:10.000"),
        video=VideoSnapshot(
            codec="hevc",
            profile="Main 10",
            pixel_format="yuv420p10le",
            bit_depth=10,
            width=1920,
            height=1080,
            sample_aspect_ratio="1:1",
            frame_rate="24000/1001",
            frame_count=240,
            scan="progressive",
            color={"primaries": "bt709"},
        ),
        audio=(
            AudioTrackSnapshot(
                track_id="audio.main",
                codec="flac",
                profile=None,
                sample_rate=48000,
                channels=2,
                channel_layout="stereo",
                language="jpn",
                default=True,
            ),
        ),
    )


def _envelope(workflow: str) -> ZBatonEnvelope:
    return ZBatonEnvelope(
        type="media-history",
        version="1.0.0-draft.2",
        id=f"zbaton.{workflow}",
        status="draft",
        created_at="2026-08-15T10:00:00+08:00",
        producer=HistoryProducer(name="ZNIKU", version="0.1.0"),
        subject=HistorySubject(title="PHASE6-SYNTHETIC", release_year=2026),
    )


def _stage(
    record_id: str,
    stage: str,
    inputs: tuple[str, ...],
    outputs: tuple[str, ...],
    effect: str,
    *,
    workflow: str = "workflow.phase6",
    generation: int = 1,
    model: ProcessingModel | None = None,
    changes: JsonObject | None = None,
    preserved: tuple[str, ...] = (),
) -> CompletedStageHistory:
    return CompletedStageHistory(
        record_id=record_id,
        workflow=workflow,
        final_generation=generation,
        stage=stage,
        model=model,
        inputs=inputs,
        outputs=outputs,
        effects=(effect,),
        changes=changes or {},
        preserved=preserved,
        evidence_id=f"evidence.{record_id}",
        verified=True,
        published=True,
    )


def _branching_input() -> ZBatonProjectionInput:
    source = _snapshot("media.source", "source.mkv", "a")
    current = _snapshot("media.final", "final.mkv", "b")
    stages = (
        _stage(
            "record.demux",
            "Demux",
            ("media.source",),
            ("media.program.video", "media.program.audio"),
            "demux",
        ),
        _stage(
            "record.partition",
            "Chapter partition",
            ("media.program.video",),
            ("media.chapter.a", "media.chapter.b"),
            "chapter-partition",
        ),
        _stage(
            "record.decensoring.chapter.a",
            "Decensoring",
            ("media.chapter.a",),
            ("media.chapter.a.decensored",),
            "mosaic-removal",
            model=ProcessingModel(name="Jasna Synthetic", version="0.1.0"),
            preserved=("video.frame_rate", "timeline.duration"),
        ),
        _stage(
            "record.collect",
            "Chapter collect",
            ("media.chapter.a.decensored", "media.chapter.b"),
            ("media.video.collected",),
            "chapter-collect",
        ),
        _stage(
            "record.new01",
            "new01",
            ("media.video.collected",),
            ("media.video.new01",),
            "zniku:new01",
        ),
        _stage(
            "record.mux",
            "Mux",
            ("media.video.new01", "media.program.audio"),
            ("media.final",),
            "mux",
        ),
    )
    return ZBatonProjectionInput(
        zbaton=_envelope("workflow.phase6"),
        current_media=FinalMediaSnapshot(
            workflow="workflow.phase6", final_generation=1, snapshot=current
        ),
        source_media=(SourceMediaSnapshot(snapshot=source),),
        stages=stages,
    )


def test_branching_projection_records_local_chapter_without_leaf_noise() -> None:
    document = project_zbaton(_branching_input())
    decensoring = next(
        record for record in document.processing_history if record.stage == "Decensoring"
    )

    assert decensoring.inputs == ("media.chapter.a",)
    assert decensoring.outputs == ("media.chapter.a.decensored",)
    assert "media.chapter.b" in document.processing_history[3].inputs
    assert document.current_media.snapshot.media_id == "media.final"
    assert document.final_history == ()
    assert all(
        "leaf" not in media_id
        for record in document.processing_history
        for media_id in record.outputs
    )


def test_projection_json_round_trip_and_digest_are_stable() -> None:
    document = project_zbaton(_branching_input())
    restored = ZBatonDocument.from_json(document.to_canonical_bytes())

    assert restored == document
    assert restored.sha256_digest() == document.sha256_digest()
    zbaton = cast(dict[str, object], restored.to_data()["zbaton"])
    assert zbaton["version"] == "1.0.0-draft.2"


def test_incomplete_stage_and_history_inconsistencies_fail_closed() -> None:
    value = _branching_input()
    incomplete = value.stages[2].model_copy(update={"verified": False})
    with pytest.raises(ValueError, match="E_ZBATON_STAGE_INCOMPLETE"):
        project_zbaton(value.model_copy(update={"stages": (*value.stages[:2], incomplete)}))

    dangling = value.stages[2].model_copy(update={"inputs": ("media.missing",)})
    with pytest.raises(ValueError, match="E_ZBATON_INPUT_DANGLING"):
        project_zbaton(value.model_copy(update={"stages": (*value.stages[:2], dangling)}))

    extra_terminal = value.stages[-1].model_copy(
        update={"outputs": ("media.final", "media.preview")}
    )
    with pytest.raises(ValueError, match="E_ZBATON_CURRENT_NOT_UNIQUE_TERMINAL"):
        project_zbaton(value.model_copy(update={"stages": (*value.stages[:-1], extra_terminal)}))

    with pytest.raises(ValueError, match="E_ZBATON_CHANGE_PRESERVED_CONFLICT"):
        _stage(
            "record.conflict",
            "Conflict",
            ("media.source",),
            ("media.output",),
            "test-effect",
            changes={"video.frame_rate": {"from": "24/1", "to": "48/1"}},
            preserved=("video",),
        ).to_record(1)


def test_downstream_inheritance_preserves_parent_and_adds_final_history() -> None:
    source = _snapshot("media.original", "original.mkv", "c")
    final_a = _snapshot("media.final.a", "final-a.mkv", "d")
    parent = project_zbaton(
        ZBatonProjectionInput(
            zbaton=_envelope("workflow.a"),
            current_media=FinalMediaSnapshot(
                workflow="workflow.a", final_generation=1, snapshot=final_a
            ),
            source_media=(SourceMediaSnapshot(snapshot=source),),
            stages=(
                _stage(
                    "record.workflow.a",
                    "Enhancement",
                    ("media.original",),
                    ("media.final.a",),
                    "quality-restoration",
                    workflow="workflow.a",
                ),
            ),
        )
    )
    parent_bytes = parent.to_canonical_bytes()
    final_b = _snapshot("media.final.b", "final-b.mkv", "e")
    child = inherit_zbaton(
        parent,
        zbaton=_envelope("workflow.b"),
        current_media=FinalMediaSnapshot(
            workflow="workflow.b", final_generation=2, snapshot=final_b
        ),
        stages=(
            _stage(
                "record.workflow.b",
                "Decensoring",
                ("media.final.a",),
                ("media.final.b",),
                "mosaic-removal",
                workflow="workflow.b",
                generation=2,
            ),
        ),
    )

    assert parent.to_canonical_bytes() == parent_bytes
    assert child.processing_history[0] == parent.processing_history[0]
    assert child.final_history == (parent.current_media,)
    assert child.current_media.final_generation == 2
    assert child.source_media == parent.source_media


def test_validator_never_reads_media_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("ZBaton consistency validator 不得读取媒体")

    monkeypatch.setattr(Path, "open", forbidden)
    document = project_zbaton(_branching_input())
    assert document.current_media.snapshot.filename == "final.mkv"
