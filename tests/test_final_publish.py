"""用合成合同与临时小文件验证直接发布的副作用边界，不读取用户媒体或启动真实工具。"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from test_chapter_batch_contracts import batch_pipeline
from test_source_aligned_node_contracts import _header
from zniku.avenhance_v27 import adapters as av27
from zniku.avenhance_v27.probe import Av27MediaError
from zniku.chapter_batch import definitions, final_publish
from zniku.chapter_batch.contracts import NAMESPACE, BatchMetadata
from zniku.graph import NodeInstance
from zniku.media.publication import no_replace_publisher
from zniku.presentation import build_builtin_presentation_catalog
from zniku.runtime import NodeExecutionRequest, NodeRunner, PythonAdapterContext
from zniku.runtime.runner import OutputTarget, RunnerError, RunnerProcessCleanupError
from zniku.source_aligned import validators as shared


def fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, overwrite: bool = False
) -> tuple[PythonAdapterContext, Path, list[str]]:
    """媒体 probe 使用完整合成 header，其余参数、绑定和发布路径验证都执行真实代码。"""
    step = batch_pipeline(tmp_path)[-1]
    for item in step.inputs:
        if not item.path.exists():
            item.path.write_bytes(b"synthetic-upstream")
    target = tmp_path / "final-directory" / "Synthetic.mkv"
    definition = final_publish.definition()
    node = NodeInstance(
        node_id="final",
        type_id=definition.type_id,
        definition_version=definition.version,
        parameters={
            **step.parameters,
            "target_path": str(target),
            "overwrite": overwrite,
            "output_root": str(tmp_path),
            "create_parent": True,
            "protected_paths": [],
        },
    )
    work = tmp_path / "attempt"
    work.mkdir()
    context = PythonAdapterContext(
        str(uuid4()),
        1,
        definition,
        node,
        work,
        step.inputs,
        (OutputTarget("media", "MediaFile", work / "outputs" / "media"),),
        work / "stdout.log",
        work / "stderr.log",
    )
    metadata = (
        final_publish.preflight("final", context.inputs, context.node.parameters)
        .outputs[0]
        .metadata
    )
    events: list[str] = []

    def mux(*_args: Any, target: Path, expected_frames: int, **_kwargs: Any) -> int:
        assert target.parent.parent == tmp_path / "final-directory"
        assert target.parent.name.startswith(".zniku-publish-")
        assert not target.exists()
        target.write_bytes(b"checked-final")
        events.append("mux")
        return expected_frames

    def header(path: Path) -> Any:
        events.append("candidate-check" if path.parent.name.endswith(".pending") else "final-check")
        return _header(path, metadata)

    monkeypatch.setattr(av27, "_execute_final_mux", mux)
    monkeypatch.setattr(shared, "probe_header", header)
    monkeypatch.setattr(shared, "verify_video_span", lambda *_args, **_kw: None)
    return context, target, events


def test_direct_candidate_is_checked_before_publish_and_runner_only_returns_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, target, events = fixture(tmp_path, monkeypatch)
    before = {item.path: item.path.read_bytes() for item in context.inputs}
    runner = NodeRunner(
        tmp_path / "runner",
        python_adapters=definitions.python_adapters(),
        validators=definitions.validators(),
        media_probe=lambda _p, _k: {"synthetic": True},
    )
    result = runner.run_automatic(
        NodeExecutionRequest(
            str(uuid4()),
            1,
            context.definition,
            context.node,
            context.inputs,
        )
    )
    assert events == ["mux", "candidate-check", "final-check"]
    assert target.read_bytes() == b"checked-final"
    assert len(result.artifacts) == 1 and result.artifacts[0].path == target
    metadata = final_publish.Metadata.model_validate(result.artifacts[0].media_info[NAMESPACE])
    assert metadata.producer_type_id == final_publish.TYPE_ID
    with pytest.raises((ValidationError, Av27MediaError)):
        BatchMetadata.model_validate(metadata.model_dump())
    assert all(path.read_bytes() == value for path, value in before.items())
    assert not list((tmp_path / "runner").rglob("*.mkv"))
    assert "Artifact registration pending" in result.stdout_log_path.read_text()


def test_failed_candidate_validation_preserves_existing_target_and_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, target, _events = fixture(tmp_path, monkeypatch, overwrite=True)
    target.parent.mkdir()
    target.write_bytes(b"existing-approved-result")

    def bad_header(_path: Path) -> Any:
        raise Av27MediaError("E_SYNTHETIC_BAD_MEDIA", "invalid candidate")

    monkeypatch.setattr(shared, "probe_header", bad_header)
    with pytest.raises(Av27MediaError, match="E_FINAL_PUBLISH_VALIDATION"):
        final_publish.execute(context)
    assert target.read_bytes() == b"existing-approved-result"
    assert (
        next(target.parent.glob(".zniku-publish-*.pending/*.mkv")).read_bytes() == b"checked-final"
    )


@pytest.mark.parametrize("unknown_exit", [False, True])
def test_mux_failure_retains_candidate_and_never_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unknown_exit: bool
) -> None:
    context, target, _events = fixture(tmp_path, monkeypatch)

    def failed(*_args: Any, target: Path, **_kwargs: Any) -> None:
        target.write_bytes(b"partial")
        if unknown_exit:
            raise RunnerProcessCleanupError("producer still alive")
        raise OSError("synthetic storage failure")

    monkeypatch.setattr(av27, "_execute_final_mux", failed)
    with pytest.raises(RunnerProcessCleanupError if unknown_exit else Av27MediaError):
        final_publish.execute(context)
    assert not target.exists()
    assert next(target.parent.glob(".zniku-publish-*.pending/*.mkv")).read_bytes() == b"partial"


@pytest.mark.parametrize("port", ["video", "sources", "gate"])
def test_all_direct_inputs_are_protected_even_if_user_omits_protected_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, port: str
) -> None:
    context, _target, events = fixture(tmp_path, monkeypatch, overwrite=True)
    source = next(item.path for item in context.inputs if item.port_id == port)
    before = source.read_bytes()
    node = context.node.model_copy(
        update={
            "parameters": {
                **context.node.parameters,
                "target_path": str(source),
                "create_parent": False,
            }
        }
    )
    with pytest.raises(Exception, match="E_MEDIA_OUTPUT_SAME_PATH"):
        final_publish.execute(replace(context, node=node))
    assert events == [] and source.read_bytes() == before


def test_no_overwrite_rejects_competing_target_at_atomic_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, target, _events = fixture(tmp_path, monkeypatch)
    publish = no_replace_publisher()

    def race(candidate: Path, destination: Path) -> None:
        destination.write_bytes(b"concurrent-user-file")
        publish(candidate, destination)

    monkeypatch.setattr(final_publish, "no_replace_publisher", lambda: race)
    with pytest.raises(Av27MediaError, match="E_FINAL_PUBLISH_FAILED"):
        final_publish.execute(context)
    assert target.read_bytes() == b"concurrent-user-file"
    assert (
        next(target.parent.glob(".zniku-publish-*.pending/*.mkv")).read_bytes() == b"checked-final"
    )


def test_explicit_overwrite_only_replaces_after_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, target, events = fixture(tmp_path, monkeypatch, overwrite=True)
    target.parent.mkdir()
    target.write_bytes(b"old")
    publish = os.replace

    def checked(candidate: Path, destination: Path) -> None:
        assert events == ["mux", "candidate-check"]
        assert destination.read_bytes() == b"old"
        publish(candidate, destination)

    monkeypatch.setattr(os, "replace", checked)
    final_publish.execute(context)
    assert target.read_bytes() == b"checked-final"


def test_post_publication_failure_keeps_file_and_never_returns_runner_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, target, _events = fixture(tmp_path, monkeypatch)
    log = av27._append_log

    def fail_after_publish(path: Path, message: str) -> None:
        if "committed=" in message:
            raise OSError("synthetic log store unavailable")
        log(path, message)

    monkeypatch.setattr(av27, "_append_log", fail_after_publish)
    runner = NodeRunner(
        tmp_path / "runner",
        python_adapters=definitions.python_adapters(),
        validators=definitions.validators(),
        media_probe=lambda _p, _k: {"synthetic": True},
    )
    with pytest.raises(RunnerError, match="E_FINAL_PUBLISH_UNREGISTERED"):
        runner.run_automatic(
            NodeExecutionRequest(
                str(uuid4()),
                1,
                context.definition,
                context.node,
                context.inputs,
            )
        )
    assert target.read_bytes() == b"checked-final"


def test_new_definition_is_independent_registered_and_presented() -> None:
    old = definitions.definition("final")
    new = final_publish.definition()
    assert old.type_id != new.type_id and old.version == new.version == "0.3.5"
    assert old.executor.adapter == "zniku.chapter_batch.adapters:final"  # type: ignore[union-attr]
    assert old.parameter_schema["properties"].keys() == {"source", "mr_mode"}  # type: ignore[union-attr]
    assert old in definitions.built_in_definitions() and new in definitions.built_in_definitions()
    assert final_publish.ADAPTER in definitions.python_adapters()
    assert final_publish.VALIDATOR in definitions.validators()
    catalog = build_builtin_presentation_catalog((new,))
    assert "成片封装并发布" in catalog.model_dump_json()


def test_existing_target_and_unsupported_platform_fail_before_media(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, target, events = fixture(tmp_path, monkeypatch)
    target.parent.mkdir()
    target.write_bytes(b"user-result")
    with pytest.raises(Av27MediaError, match="E_MEDIA_OUTPUT_EXISTS"):
        final_publish.execute(context)
    assert not list(target.parent.glob(".zniku-publish-*.pending"))
    assert target.read_bytes() == b"user-result" and events == []

    def unsupported() -> Any:
        raise Av27MediaError("E_FINAL_PUBLISH_PLATFORM_UNSUPPORTED", "synthetic unsupported")

    monkeypatch.setattr(final_publish, "no_replace_publisher", unsupported)
    with pytest.raises(Av27MediaError, match="E_FINAL_PUBLISH_PLATFORM_UNSUPPORTED"):
        final_publish.execute(context)
    assert not list(target.parent.glob(".zniku-publish-*.pending"))
    assert target.read_bytes() == b"user-result" and events == []


def test_formal_validator_failure_retains_published_file_with_explicit_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, target, _events = fixture(tmp_path, monkeypatch)
    metadata = (
        final_publish.preflight("final", context.inputs, context.node.parameters)
        .outputs[0]
        .metadata
    )

    def fail_formal(path: Path) -> Any:
        if path == target:
            raise Av27MediaError("E_SYNTHETIC_POST_PUBLISH", "synthetic formal check failure")
        return _header(path, metadata)

    monkeypatch.setattr(shared, "probe_header", fail_formal)
    runner = NodeRunner(
        tmp_path / "runner",
        python_adapters=definitions.python_adapters(),
        validators=definitions.validators(),
        media_probe=lambda _p, _k: {"synthetic": True},
    )
    with pytest.raises(RunnerError, match="E_FINAL_PUBLISH_UNREGISTERED") as error:
        runner.run_automatic(
            NodeExecutionRequest(
                str(uuid4()),
                1,
                context.definition,
                context.node,
                context.inputs,
            )
        )
    assert str(target) in str(error.value)
    assert target.read_bytes() == b"checked-final"


def test_candidate_change_during_validation_prevents_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, target, _events = fixture(tmp_path, monkeypatch)
    metadata = (
        final_publish.preflight("final", context.inputs, context.node.parameters)
        .outputs[0]
        .metadata
    )

    def mutate_after_read(path: Path) -> Any:
        header = _header(path, metadata)
        path.write_bytes(b"changed-after-media-check")
        return header

    monkeypatch.setattr(shared, "probe_header", mutate_after_read)
    with pytest.raises(Av27MediaError, match="E_FINAL_PUBLISH_CHANGED"):
        final_publish.execute(context)
    assert not target.exists()
    assert (
        next(target.parent.glob(".zniku-publish-*.pending/*.mkv")).read_bytes()
        == b"changed-after-media-check"
    )
