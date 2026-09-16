"""合成媒体覆盖新 MR 的实际封装、来源命名及旧 exact 隔离，不接触用户原片。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from test_source_aligned_media import TOOLS, external_context, media
from zniku.avenhance_v27.probe import Av27MediaError
from zniku.graph import NodeInstance
from zniku.presentation import build_builtin_presentation_catalog
from zniku.runtime import (
    ManualSubmission,
    NodeRunner,
    NodeValidatorContext,
    ProducedOutput,
    RunnerError,
)
from zniku.runtime.runner import OutputPathSpec
from zniku.source_admission import definitions
from zniku.source_admission.contracts import (
    NAMESPACE,
    SOURCE_NAMESPACE,
    ExternalMetadata,
    effective_contract,
)
from zniku.source_admission.mosaic_restoration import (
    TYPE_ID,
    MosaicRestorationMetadata,
    archive_basename,
    definition,
    inspect_candidate,
    inspect_legacy_candidate,
    validate,
)
from zniku.source_admission.validators import validate_external
from zniku.source_aligned.node_contracts import DeclaredContainer, SourceExpectation


def _context(tmp_path: Path, container: DeclaredContainer) -> NodeValidatorContext:
    # 25 FPS 在 MKV 毫秒时基下也是精确间隔，避免极短样本歧义被误认为容器兼容问题。
    context = external_context(tmp_path, container, rate="25/1")
    source, gate = context.request.inputs
    source_path = source.path.with_name("Example.repaired.mp4")
    source.path.rename(source_path)
    source = replace(
        source,
        path=source_path,
        media_info={**source.media_info, SOURCE_NAMESPACE: {"contract_version": "0.3.5"}},
    )
    inputs = (source, gate)
    target = context.outputs[0].path.with_name(archive_basename(inputs, container))
    context.outputs[0].path.rename(target)
    current = definition()
    node = NodeInstance(
        node_id=context.request.node.node_id,
        type_id=current.type_id,
        definition_version=current.version,
        parameters={**context.request.node.parameters, "declared_container": "mp4"},
    )
    return replace(
        context,
        request=replace(context.request, definition=current, node=node, inputs=inputs),
        outputs=(replace(context.outputs[0], path=target),),
    )


def test_new_definition_is_closed_and_old_exact_containers_keep_const() -> None:
    current = definition()
    assert current.type_id == TYPE_ID
    assert definitions.definition_role(current) == "external"
    assert current in definitions.built_in_definitions()
    schema = current.model_dump(mode="json")["parameter_schema"]
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False
    assert "const" not in schema["properties"]["declared_container"]
    for container in ("mp4", "mov", "mkv"):
        old = definitions.external_definition(container)
        assert old.type_id != current.type_id
        old_schema = old.model_dump(mode="json")["parameter_schema"]
        assert old_schema["properties"]["declared_container"]["const"] == container
    presentation = build_builtin_presentation_catalog((current,)).nodes[0]
    parameter = next(
        p for p in presentation.parameters if p.parameter_pointer == "/declared_container"
    )
    assert parameter.label == "建议输出封装"
    assert parameter.importance == "advanced"
    assert "实际 MP4/MOV/MKV" in (parameter.description or "")


@pytest.mark.skipif(not TOOLS, reason="requires ffmpeg/ffprobe")
@pytest.mark.parametrize("container", ["mp4", "mov", "mkv"])
def test_candidate_detects_actual_container_before_any_rename(
    tmp_path: Path, container: DeclaredContainer
) -> None:
    context = _context(tmp_path, container)
    canonical = context.outputs[0].path
    candidate = canonical.with_name("arbitrary-user-delivery.data")
    canonical.rename(candidate)
    before = candidate.stat()
    result = inspect_candidate(context.request.inputs, context.request.node.parameters, candidate)
    assert result.container == container
    assert result.archive_name == f"Example.repaired.RM.{container}"
    after = candidate.stat()
    assert (after.st_size, after.st_mtime_ns, after.st_ino) == (
        before.st_size,
        before.st_mtime_ns,
        before.st_ino,
    )
    assert not canonical.exists()
    candidate.rename(canonical)
    validated = validate(context)
    assert validated.passed, validated.message
    metadata = validated.media_info_extensions["video"][NAMESPACE]
    parsed = MosaicRestorationMetadata.model_validate(metadata)
    assert parsed.producer_type_id == TYPE_ID
    assert parsed.declared_container == container
    with pytest.raises((ValidationError, Av27MediaError)):
        ExternalMetadata.model_validate(metadata)
    # 新下游从真实 video 端口消费该新身份；不冒充旧固定容器 metadata。
    source = context.request.inputs[0]
    restored = replace(
        source, artifact_id="11111111-1111-4111-8111-111111111111", media_info={NAMESPACE: metadata}
    )
    expected = SourceExpectation.model_validate(context.request.node.parameters["source"])
    assert effective_contract(restored, expected).frame_count == expected.frame_count


@pytest.mark.skipif(not TOOLS, reason="requires ffmpeg/ffprobe")
def test_formal_validator_rejects_arbitrary_name_and_wrong_frame_count(tmp_path: Path) -> None:
    context = _context(tmp_path, "mp4")
    arbitrary = context.outputs[0].path.with_name("a-user-name.mp4")
    context.outputs[0].path.rename(arbitrary)
    altered = replace(context, outputs=(replace(context.outputs[0], path=arbitrary),))
    result = validate(altered)
    assert not result.passed
    assert result.summary["code"] == "E_SOURCE_ALIGNED_EXTERNAL_NAME"
    bad = tmp_path / "wrong-count.mp4"
    media(bad, frames=11, rate="25/1")
    with pytest.raises(Av27MediaError, match="EXTERNAL_CONTRACT"):
        inspect_candidate(context.request.inputs, context.request.node.parameters, bad)
    assert bad.exists()


@pytest.mark.skipif(not TOOLS, reason="requires ffmpeg/ffprobe")
def test_old_exact_does_not_accept_automatic_definition_or_container_switch(tmp_path: Path) -> None:
    context = _context(tmp_path, "mov")
    assert not validate_external(context).passed
    old = definitions.external_definition("mp4")
    old_node = NodeInstance(
        node_id=context.request.node.node_id,
        type_id=old.type_id,
        definition_version=old.version,
        parameters=context.request.node.parameters,
    )
    disguised = context.outputs[0].path.with_name("restoration.mp4")
    context.outputs[0].path.rename(disguised)
    changed = replace(
        context,
        request=replace(context.request, definition=old, node=old_node),
        outputs=(replace(context.outputs[0], path=disguised),),
    )
    rejected = validate_external(changed)
    assert not rejected.passed
    assert "CONTAINER" in str(rejected.summary.get("code"))
    with pytest.raises(Av27MediaError, match="CONTAINER"):
        inspect_legacy_candidate(old, changed.request.inputs, old_node.parameters, disguised)


@pytest.mark.skipif(not TOOLS, reason="requires ffmpeg/ffprobe")
def test_legacy_read_only_precheck_does_not_require_candidate_filename(tmp_path: Path) -> None:
    context = _context(tmp_path, "mp4")
    source = context.outputs[0].path
    candidate = source.with_name("external-anything.bin")
    source.rename(candidate)
    inspected = inspect_legacy_candidate(
        definitions.external_definition("mp4"),
        context.request.inputs,
        context.request.node.parameters,
        candidate,
    )
    assert inspected.container == "mp4"
    assert candidate.exists() and not source.exists()


@pytest.mark.skipif(not TOOLS, reason="requires ffmpeg/ffprobe")
def test_existing_runner_supports_actual_container_without_mutating_handoff(tmp_path: Path) -> None:
    context = _context(tmp_path, "mov")
    runner = NodeRunner(tmp_path / "attempts", validators=definitions.validators())
    request = replace(context.request, output_paths=(OutputPathSpec("video", "restoration.mp4"),))
    handoff = runner.prepare_manual(request)
    assert Path(handoff.outputs[0].path).name == "restoration.mp4"
    destination = Path(handoff.work_dir) / "outputs" / context.outputs[0].path.name
    context.outputs[0].path.rename(destination)
    # 新验收仍使用固定 video port，实际媒体在已绑定 attempt 内；历史 handoff 不改写。
    result = runner.submit_manual(
        request, handoff, ManualSubmission(outputs=(ProducedOutput("video", destination),))
    )
    assert result.artifacts[0].producer_port_id == "video"
    assert result.artifacts[0].path == destination
    assert Path(handoff.outputs[0].path).name == "restoration.mp4"
    assert not Path(handoff.outputs[0].path).exists()
    with pytest.raises(RunnerError, match="PATH_ESCAPE"):
        runner.submit_manual(
            request,
            handoff,
            ManualSubmission(outputs=(ProducedOutput("video", tmp_path / destination.name),)),
        )
