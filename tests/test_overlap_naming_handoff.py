"""合成图与 handoff 的只读命名/交付投影；不读媒体、不打开文件选择器、不自动提交。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from test_overlap_node_contracts import Step, pipeline
from zniku.chapter_overlap.definitions import (
    atomic_split_definition,
    built_in_overlap_definitions,
    definition_role,
    enhancement_definition,
    frame_interpolation_definition,
)
from zniku.chapter_overlap.naming import overlap_output_paths
from zniku.chapter_overlap.node_contracts import OVERLAP_NAMESPACE
from zniku.graph import (
    Edge,
    ExecutionMode,
    Graph,
    NodeDefinition,
    NodeInstance,
    PortSpec,
    PythonExecutorSpec,
)
from zniku.project_service.models import RunDetailEnvelope
from zniku.project_service.overlap_handoff import project_overlap_handoff_contracts
from zniku.runtime import (
    Artifact,
    ExternalHandoff,
    ExternalOutputTarget,
    NodeRun,
    NodeRunState,
    Run,
    RunState,
)

BASE = "Synthetic Movie (2026)"


def _node(step: Step) -> tuple[NodeInstance, NodeDefinition]:
    definitions = {d.type_id: d for d in built_in_overlap_definitions(len(step.contract.outputs))}
    definition = definitions[step.contract.outputs[0].metadata.producer_type_id]
    return NodeInstance(
        node_id="does-not-name-a-chapter",
        type_id=definition.type_id,
        definition_version=definition.version,
        parameters=step.parameters,
    ), definition


def _fixture(tmp_path: Path, role: str = "fi") -> tuple[Run, Artifact, Step]:
    step = next(s for s in pipeline(tmp_path) if s.role == role)
    manual, definition = _node(step)
    direct = step.inputs[0]
    now = datetime(2026, 1, 1, tzinfo=UTC)
    run_id, node_run_id = str(uuid4()), str(uuid4())
    source_definition = NodeDefinition(
        type_id="test.synthetic.overlap_input",
        version="1.0.0",
        output_ports=(PortSpec(port_id=direct.producer_port_id or "video", data_type="VideoFile"),),
        parameter_schema={"type": "object", "properties": {}, "additionalProperties": False},
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="synthetic:input"),
    )
    source_node = NodeInstance(
        node_id="arbitrary-source", type_id=source_definition.type_id, definition_version="1.0.0"
    )
    graph = Graph(
        nodes=(source_node, manual),
        edges=(
            Edge(
                source_node_id=source_node.node_id,
                source_port_id=direct.producer_port_id or "video",
                target_node_id=manual.node_id,
                target_port_id="video",
            ),
        ),
    )
    artifact = Artifact(
        artifact_id=direct.artifact_id,
        kind="VideoFile",
        path=str(direct.path),
        producer_node_run_id=direct.producer_node_run_id or str(uuid4()),
        producer_port_id=direct.producer_port_id or "video",
        media_info={OVERLAP_NAMESPACE: step.contract.input_metadata[0].model_dump(mode="json")},
    )
    handoff = ExternalHandoff(
        handoff_id=str(uuid4()),
        node_run_id=node_run_id,
        input_artifact_ids=(artifact.artifact_id,),
        output_targets=(
            ExternalOutputTarget(
                port_id="video", path=f"synthetic/A/{BASE}.A.enhancement.fi-raw.mov"
            ),
        ),
        created_at=now,
    )
    attempt = NodeRun(
        node_run_id=node_run_id,
        run_id=run_id,
        node_id=manual.node_id,
        definition_version="0.3.2",
        attempt=1,
        state=NodeRunState.WAITING_EXTERNAL,
        input_artifact_ids=(artifact.artifact_id,),
        work_dir="synthetic/attempt",
        created_at=now,
        started_at=now,
        external_handoff=handoff,
    )
    run = Run(
        run_id=run_id,
        project_id="synthetic.overlap",
        graph_snapshot=graph,
        definitions_snapshot=(source_definition, definition),
        state=RunState.RUNNING,
        node_runs=(attempt,),
        created_at=now,
        started_at=now,
    )
    return run, artifact, step


def _fields(run: Run, artifacts: tuple[Artifact, ...]) -> dict[str, str]:
    projection = project_overlap_handoff_contracts(run, artifacts)
    assert len(projection) == 1
    return {field.label: field.value for field in projection[0].fields}


def test_new_names_restart_per_chapter_and_distinguish_input_raw_and_crop(tmp_path: Path) -> None:
    steps = pipeline(tmp_path, n=3601, chapters=2, rate="30/1")
    split, definition = _node(steps[0])
    before = split.model_dump_json()
    paths = overlap_output_paths(split, definition, media_basename=BASE)
    assert [(p.port_id, p.relative_path) for p in paths] == [
        ("leaf-0001", f"A/{BASE}.A.leaf-0001.mkv"),
        ("leaf-0002", f"A/{BASE}.A.leaf-0002.mkv"),
        ("leaf-0003", f"B/{BASE}.B.leaf-0001.mkv"),
    ]
    assert split.model_dump_json() == before
    expected = {
        "merge": "enhancement.mov",
        "context": "enhancement.fi-input.mov",
        "fi": "enhancement.fi-raw.mov",
        "crop": "enhancement.fi.mov",
    }
    names = set()
    for role, suffix in expected.items():
        step = next(s for s in steps if s.role == role)
        node, definition = _node(step)
        paths = overlap_output_paths(node, definition, media_basename=BASE)
        assert paths[0].relative_path == f"A/{BASE}.A.{suffix}"
        names.add(paths[0].relative_path)
        renamed = node.model_copy(update={"node_id": "implies-chapter-ZZ"})
        assert overlap_output_paths(renamed, definition, media_basename=BASE) == paths
    assert len(names) == 4
    last = [s for s in steps if s.role == "enhancement"][-1]
    node, definition = _node(last)
    assert (
        overlap_output_paths(node, definition, media_basename=BASE)[0].relative_path
        == f"B/{BASE}.B.leaf-0001.enhancement.mov"
    )


@pytest.mark.parametrize(
    "unsafe", ["../escape", "A/B", "C:relative", "CON", "name.", " padded", "x" * 181]
)
def test_unsafe_basename_fails_without_rewriting(tmp_path: Path, unsafe: str) -> None:
    node, definition = _node(next(s for s in pipeline(tmp_path) if s.role == "fi"))
    with pytest.raises(ValueError, match="E_AV27_MEDIA_BASENAME"):
        overlap_output_paths(node, definition, media_basename=unsafe)


def test_naming_requires_full_parameters_and_exact_definition(tmp_path: Path) -> None:
    steps = pipeline(tmp_path)
    node, definition = _node(next(s for s in steps if s.role == "fi"))
    for parameters in (
        {"chapter": {"ordinal": 0}},
        dict(node.parameters, source={}),
        dict(node.parameters, unknown=True),
    ):
        draft = node.model_copy(update={"parameters": parameters})
        assert overlap_output_paths(draft, definition, media_basename=BASE) == ()
    old_node = node.model_copy(update={"definition_version": "0.2.1"})
    old_def = definition.model_copy(update={"version": "0.2.1"})
    assert overlap_output_paths(old_node, old_def, media_basename=BASE) == ()
    impostor = definition.model_copy(
        update={"parameter_schema": {"type": "object", "additionalProperties": True}}
    )
    assert overlap_output_paths(node, impostor, media_basename=BASE) == ()
    split, split_def = _node(steps[0])
    wrong = atomic_split_definition(len(steps[0].contract.outputs) + 1)
    changed = split.model_copy(update={"type_id": wrong.type_id})
    assert overlap_output_paths(changed, wrong, media_basename=BASE) == ()
    assert definition_role(split_def) == "split"


def test_cached_definitions_reject_mutation_and_bool_cache_collision() -> None:
    cached = atomic_split_definition(1)
    assert atomic_split_definition(1) is cached
    assert enhancement_definition() is enhancement_definition()
    with pytest.raises((TypeError, ValueError)):
        cached.parameter_schema["additionalProperties"] = True
    for value in (True, 1.0, False, 0, 10001):
        with pytest.raises(ValueError, match="E_OVERLAP_SPLIT_COUNT"):
            atomic_split_definition(value)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="E_OVERLAP_SPLIT_COUNT"):
            built_in_overlap_definitions(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("role", ["fi", "enhancement"])
def test_exact_waiting_handoff_is_read_only_and_requires_explicit_submit(
    tmp_path: Path, role: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, artifact, step = _fixture(tmp_path, role)
    before = run.model_dump_json(), artifact.model_dump_json()

    def no_io(*args: object, **kwargs: object) -> Any:
        raise AssertionError("交付说明不得读取磁盘/媒体")

    monkeypatch.setattr(Path, "open", no_io)
    fields = _fields(run, (artifact,))
    assert fields["外部输入帧数"] == str(step.contract.input_metadata[0].frame_count)
    assert fields["原始输出帧数"] == str(step.contract.outputs[0].metadata.frame_count)
    assert "显式提交" in fields["提交规则"] and "不会自动推进" in fields["提交规则"]
    assert "不自动清除" in fields["文件保留"]
    assert fields["本次冻结收件文件"] == f"{BASE}.A.enhancement.fi-raw.mov"
    if role == "fi":
        assert fields["软件版本(候选声明)"] == "v1.0"
        assert fields["模型(候选声明)"] == "Aion"
        assert "pending_real_acceptance" in fields["验收状态"]
        assert fields["输出帧率"] == "60000/1001"
        assert "Phase 5" in fields["相位假设"]
    assert (run.model_dump_json(), artifact.model_dump_json()) == before
    envelope = RunDetailEnvelope(
        run=run,
        artifacts=(artifact,),
        handoff_contracts=project_overlap_handoff_contracts(run, (artifact,)),
    )
    assert RunDetailEnvelope.model_validate_json(envelope.model_dump_json()) == envelope


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "old_metadata",
        "wrong_source",
        "same_count_wrong_crop",
        "wrong_profile",
        "corrupt_rate",
    ],
)
def test_missing_or_incompatible_input_never_displays_fabricated_exact_numbers(
    tmp_path: Path, mutation: str
) -> None:
    run, artifact, _ = _fixture(tmp_path)
    artifacts: tuple[Artifact, ...] = (artifact,)
    if mutation == "missing":
        artifacts = ()
    elif mutation == "corrupt_rate":
        node = run.graph_snapshot.nodes[-1]
        params = node.model_dump(mode="json")["parameters"]
        params["source"]["frame_rate"] = "60/1"
        node = node.model_copy(update={"parameters": params})
        run = run.model_copy(
            update={
                "graph_snapshot": Graph(
                    nodes=(*run.graph_snapshot.nodes[:-1], node), edges=run.graph_snapshot.edges
                )
            }
        )
    else:
        data = artifact.model_dump(mode="json")["media_info"]
        if mutation == "old_metadata":
            data = {"zniku.avenhance.v27": data[OVERLAP_NAMESPACE]}
        elif mutation == "wrong_source":
            data[OVERLAP_NAMESPACE]["source"]["effective_video_artifact_id"] = str(uuid4())
        elif mutation == "wrong_profile":
            data[OVERLAP_NAMESPACE]["fi_profile"]["phase"] = "odd"
        else:
            data[OVERLAP_NAMESPACE]["context"]["crop_start_frame"] += 1
            data[OVERLAP_NAMESPACE]["context"]["crop_end_frame"] += 1
        artifacts = (artifact.model_copy(update={"media_info": data}),)
    fields = _fields(run, artifacts)
    assert fields["精确输入合同"].startswith("不可用")
    assert "外部输入帧数" not in fields and "输出帧率" not in fields


def test_latest_attempt_and_handoff_identity_are_not_guessed(tmp_path: Path) -> None:
    run, artifact, _ = _fixture(tmp_path)
    first = run.node_runs[0]
    pending = NodeRun.pending(
        run_id=run.run_id,
        node_id=first.node_id,
        definition_version="0.3.2",
        attempt=2,
        input_artifact_ids=(),
        work_dir="synthetic/new-attempt",
        created_at=first.created_at,
    )
    later = run.model_copy(update={"node_runs": (first, pending)})
    assert project_overlap_handoff_contracts(later, (artifact,)) == ()
    assert first.external_handoff is not None
    wrong_handoff = first.external_handoff.model_copy(
        update={"input_artifact_ids": (str(uuid4()),)}
    )
    wrong = run.model_copy(
        update={"node_runs": (first.model_copy(update={"external_handoff": wrong_handoff}),)}
    )
    projection = project_overlap_handoff_contracts(wrong, (artifact,))[0]
    assert projection.input_artifact_id is None
    assert "外部输入帧数" not in {f.label for f in projection.fields}


def test_impostor_or_old_definition_is_not_called_new_overlap_handoff(tmp_path: Path) -> None:
    run, artifact, _ = _fixture(tmp_path)
    definition = frame_interpolation_definition()
    impostor = definition.model_copy(
        update={"parameter_schema": {"type": "object", "additionalProperties": True}}
    )
    changed = run.model_copy(
        update={"definitions_snapshot": (*run.definitions_snapshot[:-1], impostor)}
    )
    assert project_overlap_handoff_contracts(changed, (artifact,)) == ()
    node = run.graph_snapshot.nodes[-1].model_copy(update={"definition_version": "0.2.1"})
    old = run.model_copy(
        update={
            "graph_snapshot": Graph(
                nodes=(*run.graph_snapshot.nodes[:-1], node), edges=run.graph_snapshot.edges
            ),
            "definitions_snapshot": (
                *run.definitions_snapshot[:-1],
                definition.model_copy(update={"version": "0.2.1"}),
            ),
            "node_runs": tuple(
                item.model_copy(update={"definition_version": "0.2.1"}) for item in run.node_runs
            ),
        }
    )
    assert project_overlap_handoff_contracts(old, (artifact,)) == ()
