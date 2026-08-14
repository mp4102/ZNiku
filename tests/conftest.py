"""为 Contract Kernel 测试提供不接触媒体文件的纯合成对象。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from zniku.contracts import (
    Artifact,
    ArtifactRef,
    ArtifactSet,
    ArtifactSetMember,
    ArtifactType,
    CoverageMode,
    CoverageSpan,
    CoverageUnit,
    EngineBinding,
    EngineManifest,
    MediaKind,
    PortBinding,
    Scope,
    StageRun,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "valid-engine-manifest.json"


@pytest.fixture
def manifest_payload() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))


@pytest.fixture
def valid_manifest() -> EngineManifest:
    return EngineManifest.from_json(FIXTURE_PATH.read_bytes())


@pytest.fixture
def video_artifact() -> Artifact:
    return Artifact(
        artifact_id="artifact.source.video",
        artifact_type=ArtifactType.MEDIA,
        media_kind=MediaKind.VIDEO,
        scope=Scope.PROGRAM,
        scope_id="program.synthetic",
        attributes={"width": 1920, "height": 1080, "frame_rate": "30000/1001"},
    )


@pytest.fixture
def output_artifact() -> Artifact:
    return Artifact(
        artifact_id="artifact.output.video",
        artifact_type=ArtifactType.MEDIA,
        media_kind=MediaKind.VIDEO,
        scope=Scope.PROGRAM,
        scope_id="program.synthetic",
        producer_stage_run_id="stage_run.enhance.001",
        attributes={"width": 3840, "height": 2160, "frame_rate": "30000/1001"},
    )


@pytest.fixture
def chapter_artifacts() -> tuple[Artifact, Artifact, Artifact]:
    artifacts = tuple(
        Artifact(
            artifact_id=f"artifact.chapter.{index}",
            artifact_type=ArtifactType.MEDIA,
            media_kind=MediaKind.VIDEO,
            scope=Scope.CHAPTER,
            scope_id=f"chapter.{index}",
            producer_stage_run_id=f"stage_run.chapter.{index}",
            attributes={"frame_rate": "30000/1001"},
        )
        for index in range(1, 4)
    )
    return cast(tuple[Artifact, Artifact, Artifact], artifacts)


@pytest.fixture
def valid_artifact_set(chapter_artifacts: tuple[Artifact, Artifact, Artifact]) -> ArtifactSet:
    spans = (
        CoverageSpan(unit=CoverageUnit.FRAME, start=0, end=100),
        CoverageSpan(unit=CoverageUnit.FRAME, start=100, end=220),
        CoverageSpan(unit=CoverageUnit.FRAME, start=220, end=360),
    )
    members = tuple(
        ArtifactSetMember(member_id=f"member.chapter.{index}", artifact=artifact, coverage=span)
        for index, (artifact, span) in enumerate(
            zip(chapter_artifacts, spans, strict=True), start=1
        )
    )
    return ArtifactSet(
        artifact_set_id="artifact_set.chapters",
        artifact_type=ArtifactType.MEDIA,
        media_kind=MediaKind.VIDEO,
        scope=Scope.CHAPTER,
        scope_id="chapter_plan.synthetic",
        expected_member_ids=("member.chapter.1", "member.chapter.2", "member.chapter.3"),
        members=members,
        coverage=CoverageSpan(unit=CoverageUnit.FRAME, start=0, end=360),
        coverage_mode=CoverageMode.SEQUENTIAL,
        producer_stage_run_id="stage_run.partition.001",
    )


@pytest.fixture
def valid_stage_run(
    valid_manifest: EngineManifest,
    video_artifact: Artifact,
    output_artifact: Artifact,
) -> StageRun:
    return StageRun(
        contract_version="0.1.0",
        stage_run_id="stage_run.enhance.001",
        workflow_run_id="workflow_run.synthetic.001",
        stage_spec_id="stage_spec.enhance",
        scope=Scope.PROGRAM,
        scope_id="program.synthetic",
        engine=EngineBinding.from_manifest(valid_manifest),
        parameters={"scale": 2, "model": "synthetic-v1"},
        inputs=(
            PortBinding(
                port_id="video_in",
                target=ArtifactRef(artifact_id=video_artifact.artifact_id),
            ),
        ),
        outputs=(
            PortBinding(
                port_id="video_out",
                target=ArtifactRef(artifact_id=output_artifact.artifact_id),
            ),
        ),
    )
