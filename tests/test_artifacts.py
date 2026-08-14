"""验证 Artifact 分类和 ArtifactSet 的成员、顺序与覆盖完整性。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

import pytest
from pydantic import ValidationError

from zniku.contracts import (
    Artifact,
    ArtifactSet,
    ArtifactSetMember,
    ArtifactType,
    CoverageMode,
    CoverageSpan,
    CoverageUnit,
    MediaKind,
    Scope,
)


def rebuild_set(
    base: ArtifactSet,
    *,
    expected_member_ids: Sequence[str] | None = None,
    members: Sequence[ArtifactSetMember] | None = None,
    coverage: CoverageSpan | None = None,
    coverage_mode: CoverageMode | None = None,
) -> ArtifactSet:
    payload = base.to_data()
    if expected_member_ids is not None:
        payload["expected_member_ids"] = list(expected_member_ids)
    if members is not None:
        payload["members"] = [member.to_data() for member in members]
    if coverage is not None:
        payload["coverage"] = coverage.to_data()
    if coverage_mode is not None:
        payload["coverage_mode"] = coverage_mode.value
    return ArtifactSet.from_json(json.dumps(payload))


def test_valid_ordered_artifact_set(valid_artifact_set: ArtifactSet) -> None:
    assert tuple(member.member_id for member in valid_artifact_set.members) == (
        "member.chapter.1",
        "member.chapter.2",
        "member.chapter.3",
    )
    assert valid_artifact_set.coverage.end == 360


def test_missing_member_fails_closed(valid_artifact_set: ArtifactSet) -> None:
    with pytest.raises(ValidationError, match="E_SET_MEMBER_MISSING"):
        rebuild_set(valid_artifact_set, members=valid_artifact_set.members[:-1])


def test_unexpected_member_fails_closed(valid_artifact_set: ArtifactSet) -> None:
    extra = ArtifactSetMember(
        member_id="member.chapter.4",
        artifact=Artifact(
            artifact_id="artifact.chapter.4",
            artifact_type=ArtifactType.MEDIA,
            media_kind=MediaKind.VIDEO,
            scope=Scope.CHAPTER,
            scope_id="chapter.4",
            producer_stage_run_id="stage_run.chapter.4",
            attributes={"frame_rate": "30000/1001"},
        ),
        coverage=CoverageSpan(unit=CoverageUnit.FRAME, start=360, end=400),
    )
    with pytest.raises(ValidationError, match="E_SET_MEMBER_UNEXPECTED"):
        rebuild_set(valid_artifact_set, members=(*valid_artifact_set.members, extra))


def test_duplicate_expected_member_fails_closed(valid_artifact_set: ArtifactSet) -> None:
    with pytest.raises(ValidationError, match="E_SET_EXPECTED_DUPLICATE"):
        rebuild_set(
            valid_artifact_set,
            expected_member_ids=("member.chapter.1", "member.chapter.1", "member.chapter.3"),
        )


def test_duplicate_actual_member_fails_closed(valid_artifact_set: ArtifactSet) -> None:
    duplicated = ArtifactSetMember(
        member_id="member.chapter.1",
        artifact=valid_artifact_set.members[1].artifact,
        coverage=valid_artifact_set.members[1].coverage,
    )
    with pytest.raises(ValidationError, match="E_SET_MEMBER_DUPLICATE"):
        rebuild_set(
            valid_artifact_set,
            members=(valid_artifact_set.members[0], duplicated, valid_artifact_set.members[2]),
        )


def test_duplicate_artifact_identity_fails_closed(valid_artifact_set: ArtifactSet) -> None:
    duplicated = ArtifactSetMember(
        member_id="member.chapter.2",
        artifact=valid_artifact_set.members[0].artifact,
        coverage=valid_artifact_set.members[1].coverage,
    )
    with pytest.raises(ValidationError, match="E_SET_ARTIFACT_DUPLICATE"):
        rebuild_set(
            valid_artifact_set,
            members=(valid_artifact_set.members[0], duplicated, valid_artifact_set.members[2]),
        )


def test_member_order_mismatch_fails_closed(valid_artifact_set: ArtifactSet) -> None:
    reordered = (
        valid_artifact_set.members[1],
        valid_artifact_set.members[0],
        valid_artifact_set.members[2],
    )
    with pytest.raises(ValidationError, match="E_SET_ORDER_MISMATCH"):
        rebuild_set(valid_artifact_set, members=reordered)


def test_sequential_coverage_gap_fails_closed(valid_artifact_set: ArtifactSet) -> None:
    changed = ArtifactSetMember(
        member_id="member.chapter.2",
        artifact=valid_artifact_set.members[1].artifact,
        coverage=CoverageSpan(unit=CoverageUnit.FRAME, start=101, end=220),
    )
    with pytest.raises(ValidationError, match="E_SET_COVERAGE_INCOMPLETE"):
        rebuild_set(
            valid_artifact_set,
            members=(valid_artifact_set.members[0], changed, valid_artifact_set.members[2]),
        )


def test_parallel_coverage_requires_every_member_to_cover_whole_span(
    valid_artifact_set: ArtifactSet,
) -> None:
    with pytest.raises(ValidationError, match="E_SET_COVERAGE_INCOMPLETE"):
        rebuild_set(valid_artifact_set, coverage_mode=CoverageMode.PARALLEL)


def test_nonempty_set_cannot_use_zero_length_coverage(
    valid_artifact_set: ArtifactSet,
) -> None:
    zero_members = tuple(
        ArtifactSetMember(
            member_id=member.member_id,
            artifact=member.artifact,
            coverage=CoverageSpan(unit=CoverageUnit.FRAME, start=0, end=0),
        )
        for member in valid_artifact_set.members
    )
    with pytest.raises(ValidationError, match="E_SET_COVERAGE_EMPTY_MEMBER"):
        rebuild_set(
            valid_artifact_set,
            members=zero_members,
            coverage=CoverageSpan(unit=CoverageUnit.FRAME, start=0, end=0),
        )


def test_empty_expected_set_requires_zero_length_coverage(
    valid_artifact_set: ArtifactSet,
) -> None:
    empty = rebuild_set(
        valid_artifact_set,
        expected_member_ids=(),
        members=(),
        coverage=CoverageSpan(unit=CoverageUnit.FRAME, start=0, end=0),
    )
    assert empty.members == ()


def test_artifact_attributes_are_deeply_immutable() -> None:
    artifact = Artifact(
        artifact_id="artifact.immutable",
        artifact_type=ArtifactType.MEDIA,
        media_kind=MediaKind.VIDEO,
        scope=Scope.PROGRAM,
        scope_id="program.synthetic",
        attributes={"nested": {"values": [1, 2]}},
    )

    with pytest.raises(TypeError, match="不可修改"):
        artifact.attributes["new"] = 1  # type: ignore[index]
    nested = artifact.attributes["nested"]
    assert isinstance(nested, Mapping)
    with pytest.raises(TypeError, match="不可修改"):
        nested["values"] = []


def test_artifact_rejects_executable_payload_key() -> None:
    with pytest.raises(ValidationError, match="E_EXECUTABLE_FIELD_FORBIDDEN"):
        Artifact(
            artifact_id="artifact.unsafe",
            artifact_type=ArtifactType.METADATA,
            media_kind=None,
            scope=Scope.PROGRAM,
            scope_id="program.synthetic",
            attributes={"command": "do-not-run"},
        )
