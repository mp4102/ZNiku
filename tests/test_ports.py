"""验证 typed ports、scope 和 cardinality 的精确兼容语义。"""

from __future__ import annotations

import pytest

from zniku.contracts import (
    ArtifactType,
    Cardinality,
    ContractViolation,
    MediaKind,
    PortSpec,
    Scope,
    assert_ports_compatible,
    ports_compatible,
)


def make_port(
    port_id: str,
    *,
    artifact_type: ArtifactType = ArtifactType.MEDIA,
    media_kind: MediaKind | None = MediaKind.VIDEO,
    scope: Scope = Scope.PROGRAM,
    cardinality: Cardinality = Cardinality.ONE,
) -> PortSpec:
    return PortSpec(
        port_id=port_id,
        artifact_type=artifact_type,
        media_kind=media_kind,
        scope=scope,
        cardinality=cardinality,
    )


def test_compatible_typed_ports() -> None:
    output = make_port("video_out")
    input_ = make_port("video_in", cardinality=Cardinality.OPTIONAL)

    assert_ports_compatible(output, input_)
    assert ports_compatible(output, input_)


@pytest.mark.parametrize(
    ("output", "input_", "code"),
    [
        (
            make_port("metadata_out", artifact_type=ArtifactType.METADATA, media_kind=None),
            make_port("video_in"),
            "E_PORT_ARTIFACT_TYPE_INCOMPATIBLE",
        ),
        (
            make_port("audio_out", media_kind=MediaKind.AUDIO),
            make_port("video_in"),
            "E_PORT_MEDIA_KIND_INCOMPATIBLE",
        ),
        (
            make_port("chapter_out", scope=Scope.CHAPTER),
            make_port("program_in"),
            "E_PORT_SCOPE_INCOMPATIBLE",
        ),
        (
            make_port("optional_out", cardinality=Cardinality.OPTIONAL),
            make_port("required_in"),
            "E_PORT_CARDINALITY_INCOMPATIBLE",
        ),
        (
            make_port("set_out", cardinality=Cardinality.SET),
            make_port("single_in"),
            "E_PORT_CARDINALITY_INCOMPATIBLE",
        ),
    ],
)
def test_incompatible_ports_fail_closed(output: PortSpec, input_: PortSpec, code: str) -> None:
    with pytest.raises(ContractViolation, match=code) as error:
        assert_ports_compatible(output, input_)

    assert error.value.code == code
    assert not ports_compatible(output, input_)


def test_non_media_port_rejects_media_kind() -> None:
    with pytest.raises(ValueError, match="E_MEDIA_KIND_FORBIDDEN"):
        make_port("metadata", artifact_type=ArtifactType.METADATA, media_kind=MediaKind.VIDEO)
