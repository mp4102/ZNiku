"""验证 0.2.0 Runtime 模型、SQLite 历史与结果投影的失败关闭语义。

测试只使用临时 ``.zniku`` 与纯合成路径/媒体信息；不创建媒体文件、不执行进程、不调用 FFprobe，也不
实现 Scheduler 或 Node Runner。
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from zniku.graph import (
    Cardinality,
    CommandExecutorSpec,
    Edge,
    ExecutionMode,
    Graph,
    ManualExternalExecutorSpec,
    NodeDefinition,
    NodeInstance,
    PortSpec,
    PythonExecutorSpec,
    UiPosition,
)
from zniku.project import Project, ProjectStore
from zniku.runtime.models import (
    Artifact,
    ExternalHandoff,
    ExternalOutputTarget,
    FailureReason,
    FrameRange,
    NodeResult,
    NodeRun,
    NodeRunState,
    Run,
    RunState,
    RuntimeFailure,
    StaleReason,
)
from zniku.runtime.repository import (
    RuntimeConflictError,
    RuntimeDataError,
    RuntimeNotFoundError,
    RuntimeRepository,
)

# 固定在产品测试运行日期之后，避免 ProjectStore 的真实保存时钟超过合成 attempt 时钟，
# 从而把“时间回退”测试偶然变成依赖执行当天时刻的测试。
BASE_TIME = datetime(2100, 1, 1, 1, 0, tzinfo=UTC)


def rid(number: int) -> str:
    """生成可读且满足 UUIDv4/variant 词法的固定合成身份。"""

    return f"00000000-0000-4000-8000-{number:012x}"


def at(minutes: int) -> datetime:
    return BASE_TIME + timedelta(minutes=minutes)


def definitions() -> tuple[NodeDefinition, ...]:
    source = NodeDefinition(
        type_id="test.Source",
        version="1.0.0",
        output_ports=(PortSpec(port_id="video", data_type="VideoFile"),),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="tests.synthetic:source"),
    )
    transform = NodeDefinition(
        type_id="test.Transform",
        version="2.0.0",
        input_ports=(PortSpec(port_id="video_in", data_type="VideoFile", required=True),),
        output_ports=(PortSpec(port_id="video_out", data_type="VideoFile"),),
        parameter_schema={
            "type": "object",
            "properties": {"strength": {"type": "integer", "minimum": 1}},
            "required": ["strength"],
            "additionalProperties": False,
        },
        execution_mode=ExecutionMode.MANUAL_EXTERNAL,
        executor=ManualExternalExecutorSpec(instructions="使用合成外部工具"),
    )
    output = NodeDefinition(
        type_id="test.Output",
        version="1.0.0",
        input_ports=(PortSpec(port_id="input", data_type="VideoFile", required=True),),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="tests.synthetic:output"),
    )
    return source, transform, output


def project(*, strength: int = 7, transform_x: float = 200.0) -> Project:
    return Project(
        project_id="project.runtime.synthetic",
        name="Runtime 合成工程",
        graph=Graph(
            nodes=(
                NodeInstance(
                    node_id="node.source",
                    type_id="test.Source",
                    definition_version="1.0.0",
                    ui_position=UiPosition(x=0.0, y=0.0),
                ),
                NodeInstance(
                    node_id="node.transform",
                    type_id="test.Transform",
                    definition_version="2.0.0",
                    parameters={"strength": strength},
                    ui_position=UiPosition(x=transform_x, y=0.0),
                ),
                NodeInstance(
                    node_id="node.output",
                    type_id="test.Output",
                    definition_version="1.0.0",
                    ui_position=UiPosition(x=400.0, y=0.0),
                ),
            ),
            edges=(
                Edge(
                    source_node_id="node.source",
                    source_port_id="video",
                    target_node_id="node.transform",
                    target_port_id="video_in",
                ),
                Edge(
                    source_node_id="node.transform",
                    source_port_id="video_out",
                    target_node_id="node.output",
                    target_port_id="input",
                ),
            ),
        ),
    )


def repository(tmp_path: Path) -> tuple[ProjectStore, RuntimeRepository]:
    store = ProjectStore.create(tmp_path / "runtime.zniku", project(), definitions())
    return store, RuntimeRepository(store)


def running_run(
    repo: RuntimeRepository,
    *,
    run_id_number: int = 1,
    created_at: datetime | None = None,
) -> Run:
    snapshot = repo.project_store.load()
    pending = Run.pending(
        run_id=rid(run_id_number),
        project_id=snapshot.project.project_id,
        graph_snapshot=snapshot.project.graph,
        definitions_snapshot=snapshot.definitions,
        created_at=created_at or at(run_id_number),
    )
    node_versions = {item.node_id: item.definition_version for item in pending.graph_snapshot.nodes}
    attempts = tuple(
        NodeRun.pending(
            node_run_id=rid(9000 + run_id_number * 10 + index),
            run_id=pending.run_id,
            node_id=node.node_id,
            definition_version=node_versions[node.node_id],
            attempt=1,
            input_artifact_ids=(),
            created_at=pending.created_at + timedelta(minutes=1),
            work_dir=f"C:/synthetic/initial-{run_id_number}-{index}",
        )
        for index, node in enumerate(pending.graph_snapshot.nodes)
    )
    return repo.start_run(
        pending,
        attempts,
        started_at=max(at(10), pending.created_at + timedelta(minutes=1)),
    )


def pending_node_run(
    run: Run,
    *,
    node_id: str,
    version: str,
    node_run_number: int,
    attempt: int = 1,
    input_ids: tuple[str, ...] = (),
) -> NodeRun:
    created_at = max(at(11 + node_run_number), run.created_at + timedelta(minutes=1))
    return NodeRun.pending(
        node_run_id=rid(node_run_number),
        run_id=run.run_id,
        node_id=node_id,
        definition_version=version,
        attempt=attempt,
        input_artifact_ids=input_ids,
        created_at=created_at,
        work_dir=f"C:/synthetic/attempt-{node_run_number}",
    )


def closure_attempts(
    run: Run,
    *,
    attempt: int,
    first_number: int,
    node_ids: tuple[str, ...] = ("node.source", "node.transform", "node.output"),
    created_at: datetime | None = None,
) -> tuple[NodeRun, ...]:
    """按线性合成图顺序构造尚未绑定输入的一组 attempts。"""

    versions = {
        "node.source": "1.0.0",
        "node.transform": "2.0.0",
        "node.output": "1.0.0",
    }
    candidates = tuple(
        pending_node_run(
            run,
            node_id=node_id,
            version=versions[node_id],
            node_run_number=first_number + index,
            attempt=attempt,
        )
        for index, node_id in enumerate(node_ids)
    )
    if created_at is None:
        return candidates
    return tuple(item.model_copy(update={"created_at": created_at}) for item in candidates)


def _next_attempt_time(run: Run) -> datetime:
    """生成严格晚于当前聚合全部已知时间的合成 rerun 创建时点。"""

    values = [run.created_at]
    if run.started_at is not None:
        values.append(run.started_at)
    for item in run.node_runs:
        values.append(item.created_at)
        if item.started_at is not None:
            values.append(item.started_at)
        if item.ended_at is not None:
            values.append(item.ended_at)
    return max(values) + timedelta(minutes=1)


def next_closure_attempts(
    run: Run,
    *,
    source_node_id: str,
    first_number: int,
    created_at: datetime,
) -> tuple[NodeRun, ...]:
    """按每个节点自己的连续 attempt 号构造 rerun closure。"""

    node_order = ("node.source", "node.transform", "node.output")
    versions = {
        "node.source": "1.0.0",
        "node.transform": "2.0.0",
        "node.output": "1.0.0",
    }
    selected = node_order[node_order.index(source_node_id) :]
    return tuple(
        NodeRun.pending(
            node_run_id=rid(first_number + index),
            run_id=run.run_id,
            node_id=node_id,
            definition_version=versions[node_id],
            attempt=max(item.attempt for item in run.node_runs if item.node_id == node_id) + 1,
            input_artifact_ids=(),
            created_at=created_at,
            work_dir=f"C:/synthetic/rerun-{first_number + index}",
        )
        for index, node_id in enumerate(selected)
    )


def _after_input_completion(
    repo: RuntimeRepository,
    input_ids: tuple[str, ...],
    fallback: datetime,
) -> datetime:
    """让下游 started_at 严格晚于其输入 producer 的完成时点。"""

    ended = []
    for artifact_id in dict.fromkeys(input_ids):
        artifact = repo.get_artifact(artifact_id)
        producer = repo.get_node_run(artifact.producer_node_run_id)
        if producer.ended_at is not None:
            ended.append(producer.ended_at + timedelta(minutes=1))
    return max((fallback, *ended))


def complete_automatic(
    repo: RuntimeRepository,
    run: Run,
    *,
    node_id: str,
    version: str,
    node_run_number: int,
    result_number: int,
    artifact_number: int | None,
    attempt: int = 1,
    input_ids: tuple[str, ...] = (),
    port_id: str = "video",
    exit_code: int | None = None,
) -> tuple[NodeRun, NodeResult]:
    loaded = repo.get_run(run.run_id)
    matching = next(
        (item for item in loaded.node_runs if item.node_id == node_id and item.attempt == attempt),
        None,
    )
    if matching is not None and matching.state is NodeRunState.PENDING:
        pending = matching
        if input_ids:
            pending = repo.bind_inputs(pending.node_run_id, input_ids)
    else:
        created_at = _next_attempt_time(loaded)
        candidates = next_closure_attempts(
            loaded,
            source_node_id=node_id,
            first_number=5000 + node_run_number * 10,
            created_at=created_at,
        )
        created = repo.create_rerun_attempts(
            loaded.run_id,
            node_id,
            candidates,
            updated_at=created_at,
        )
        pending = next(item for item in created if item.node_id == node_id)
        if input_ids:
            pending = repo.bind_inputs(pending.node_run_id, input_ids)
    started_at = _after_input_completion(
        repo,
        input_ids,
        max(at(30 + node_run_number), pending.created_at + timedelta(minutes=1)),
    )
    repo.transition_node_run(
        pending.node_run_id,
        NodeRunState.RUNNING,
        occurred_at=started_at,
        log_path=f"C:/synthetic/log-{node_run_number}.txt",
    )
    outputs: tuple[Artifact, ...] = ()
    if artifact_number is not None:
        outputs = (
            Artifact(
                artifact_id=rid(artifact_number),
                kind="VideoFile",
                path=f"C:/synthetic/artifact-{artifact_number}.mkv",
                producer_node_run_id=pending.node_run_id,
                producer_port_id=port_id,
                frame_range=FrameRange(start_frame=0, end_frame=120),
                media_info={"video": {"codec": "synthetic", "frames": [1, 2]}},
                size=4096,
                mtime_ns=123456,
            ),
        )
    result_created_at = max(at(60 + node_run_number), started_at + timedelta(minutes=1))
    result = NodeResult(
        result_id=rid(result_number),
        node_run_id=pending.node_run_id,
        outputs=outputs,
        media_summary={"synthetic": True},
        validation_summary={"passed": True},
        created_at=result_created_at,
    )
    completed = repo.register_result(
        result,
        ended_at=max(at(61 + node_run_number), result_created_at + timedelta(minutes=1)),
        exit_code=exit_code,
    )
    return completed, result


def complete_manual(
    repo: RuntimeRepository,
    run: Run,
    *,
    node_run_number: int,
    result_number: int,
    artifact_number: int,
    input_id: str,
    attempt: int = 1,
) -> tuple[NodeRun, NodeResult]:
    loaded = repo.get_run(run.run_id)
    matching = next(
        (
            item
            for item in loaded.node_runs
            if item.node_id == "node.transform" and item.attempt == attempt
        ),
        None,
    )
    if matching is not None and matching.state is NodeRunState.PENDING:
        pending = matching
        if not pending.input_artifact_ids:
            pending = repo.bind_inputs(pending.node_run_id, (input_id,))
    else:
        created_at = _next_attempt_time(loaded)
        candidates = next_closure_attempts(
            loaded,
            source_node_id="node.transform",
            first_number=5000 + node_run_number * 10,
            created_at=created_at,
        )
        created = repo.create_rerun_attempts(
            loaded.run_id,
            "node.transform",
            candidates,
            updated_at=created_at,
        )
        pending = next(item for item in created if item.node_id == "node.transform")
        pending = repo.bind_inputs(pending.node_run_id, (input_id,))
    handoff_created_at = _after_input_completion(
        repo,
        (input_id,),
        max(at(30 + node_run_number), pending.created_at + timedelta(minutes=1)),
    )
    output_path = f"{pending.work_dir}/manual-{artifact_number}.mkv"
    handoff = ExternalHandoff(
        handoff_id=rid(800 + node_run_number),
        node_run_id=pending.node_run_id,
        input_artifact_ids=(input_id,),
        output_targets=(
            ExternalOutputTarget(
                port_id="video_out",
                path=output_path,
            ),
        ),
        instructions="使用合成外部工具",
        created_at=handoff_created_at,
    )
    repo.transition_node_run(
        pending.node_run_id,
        NodeRunState.WAITING_EXTERNAL,
        occurred_at=handoff_created_at,
        external_handoff=handoff,
    )
    result_created_at = max(at(60 + node_run_number), handoff_created_at + timedelta(minutes=1))
    result = NodeResult(
        result_id=rid(result_number),
        node_run_id=pending.node_run_id,
        outputs=(
            Artifact(
                artifact_id=rid(artifact_number),
                kind="VideoFile",
                path=output_path,
                producer_node_run_id=pending.node_run_id,
                producer_port_id="video_out",
                media_info={"video": {"frames": 120}},
            ),
        ),
        validation_summary={"passed": True},
        created_at=result_created_at,
    )
    completed = repo.register_result(
        result,
        ended_at=max(at(61 + node_run_number), result_created_at + timedelta(minutes=1)),
    )
    return completed, result


def test_runtime_models_reject_digest_ids_invalid_states_and_mutable_json() -> None:
    with pytest.raises(ValidationError):
        Artifact(
            artifact_id="sha256:" + "0" * 64,
            kind="VideoFile",
            path="C:/synthetic/a.mkv",
            producer_node_run_id=rid(2),
            producer_port_id="video",
        )
    with pytest.raises(ValidationError, match="E_FRAME_RANGE_INVALID"):
        FrameRange(start_frame=12, end_frame=12)
    with pytest.raises(ValidationError):
        NodeRun.model_validate(
            {
                "node_run_id": rid(2),
                "run_id": rid(1),
                "node_id": "node.source",
                "definition_version": "1.0.0",
                "attempt": 1,
                "state": "ready",
                "created_at": at(1),
                "work_dir": "C:/synthetic/attempt",
            }
        )

    artifact = Artifact(
        artifact_id=rid(3),
        kind="VideoFile",
        path="C:/synthetic/a.mkv",
        producer_node_run_id=rid(2),
        producer_port_id="video",
        media_info={"nested": {"values": [1, 2]}},
    )
    with pytest.raises(TypeError, match="不可原地修改"):
        cast(Any, artifact.media_info["nested"])["values"].append(3)


def test_ordered_inputs_can_repeat_the_same_artifact_identity() -> None:
    repeated = rid(100)
    node_run = NodeRun.pending(
        node_run_id=rid(2),
        run_id=rid(1),
        node_id="node.merge",
        definition_version="1.0.0",
        attempt=1,
        input_artifact_ids=(repeated, repeated),
        created_at=at(1),
        work_dir="C:/synthetic/merge",
    )
    handoff = ExternalHandoff(
        handoff_id=rid(3),
        node_run_id=node_run.node_run_id,
        input_artifact_ids=(repeated, repeated),
        created_at=at(2),
    )

    assert node_run.input_artifact_ids == (repeated, repeated)
    assert handoff.input_artifact_ids == (repeated, repeated)


def test_run_and_node_run_round_trip_with_ordinary_snapshots(tmp_path: Path) -> None:
    _, repo = repository(tmp_path)
    run = running_run(repo)
    node_run = next(item for item in run.node_runs if item.node_id == "node.source")

    stored = repo.get_node_run(node_run.node_run_id)
    loaded_run = repo.get_run(run.run_id)

    assert stored == node_run
    assert loaded_run.graph_snapshot == project().graph
    assert loaded_run.definitions_snapshot == definitions()
    assert loaded_run.node_runs == run.node_runs
    assert repo.list_runs() == (loaded_run,)
    assert repo.list_node_runs(run.run_id) == run.node_runs


def test_start_run_atomically_persists_running_run_and_complete_attempt_set(
    tmp_path: Path,
) -> None:
    store, repo = repository(tmp_path)
    snapshot = store.load()
    run = Run.pending(
        run_id=rid(1),
        project_id=snapshot.project.project_id,
        graph_snapshot=snapshot.project.graph,
        definitions_snapshot=snapshot.definitions,
        created_at=at(1),
    )
    attempts = closure_attempts(run, attempt=1, first_number=20, created_at=at(2))

    started = repo.start_run(run, attempts, started_at=at(10))

    assert started.state is RunState.RUNNING
    assert started.started_at == at(10)
    assert started.node_runs == attempts


def test_start_run_bulk_conflict_rolls_back_run_and_all_attempts(tmp_path: Path) -> None:
    store, repo = repository(tmp_path)
    existing_run = running_run(repo)
    existing = next(item for item in existing_run.node_runs if item.node_id == "node.source")
    snapshot = store.load()
    run = Run.pending(
        run_id=rid(2),
        project_id=snapshot.project.project_id,
        graph_snapshot=snapshot.project.graph,
        definitions_snapshot=snapshot.definitions,
        created_at=at(2),
    )
    candidates = list(closure_attempts(run, attempt=1, first_number=51, created_at=at(3)))
    candidates[1] = candidates[1].model_copy(update={"node_run_id": existing.node_run_id})

    with pytest.raises(RuntimeConflictError, match="E_NODE_RUN_ID_CONFLICT"):
        repo.start_run(run, tuple(candidates), started_at=at(10))

    with pytest.raises(RuntimeNotFoundError, match="E_RUN_NOT_FOUND"):
        repo.get_run(run.run_id)
    with pytest.raises(RuntimeNotFoundError, match="E_NODE_RUN_NOT_FOUND"):
        repo.get_node_run(candidates[0].node_run_id)


def test_start_run_mid_insert_failure_rolls_back_first_attempt_and_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo = repository(tmp_path)
    snapshot = store.load()
    run = Run.pending(
        run_id=rid(1),
        project_id=snapshot.project.project_id,
        graph_snapshot=snapshot.project.graph,
        definitions_snapshot=snapshot.definitions,
        created_at=at(1),
    )
    attempts = closure_attempts(run, attempt=1, first_number=20, created_at=at(2))
    original = RuntimeRepository._insert_node_run
    inserted = 0

    def fail_on_second(connection: sqlite3.Connection, node_run: NodeRun) -> None:
        nonlocal inserted
        inserted += 1
        if inserted == 2:
            raise sqlite3.IntegrityError("合成第二个 attempt INSERT 冲突")
        original(connection, node_run)

    monkeypatch.setattr(RuntimeRepository, "_insert_node_run", staticmethod(fail_on_second))

    with pytest.raises(RuntimeConflictError, match="E_RUN_START_CONFLICT"):
        repo.start_run(run, attempts, started_at=at(10))

    with pytest.raises(RuntimeNotFoundError, match="E_RUN_NOT_FOUND"):
        repo.get_run(run.run_id)
    with pytest.raises(RuntimeNotFoundError, match="E_NODE_RUN_NOT_FOUND"):
        repo.get_node_run(attempts[0].node_run_id)


def test_pending_input_binding_is_one_time_and_matches_run_edges(
    tmp_path: Path,
) -> None:
    _, repo = repository(tmp_path)
    run = running_run(repo)
    _, source_result = complete_automatic(
        repo,
        run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=30,
        artifact_number=40,
        exit_code=None,
    )
    artifact_id = source_result.outputs[0].artifact_id
    pending = next(
        item for item in repo.get_run(run.run_id).node_runs if item.node_id == "node.transform"
    )

    bound = repo.bind_inputs(pending.node_run_id, (artifact_id,))

    assert bound.input_artifact_ids == (artifact_id,)
    with pytest.raises(RuntimeConflictError, match="E_NODE_RUN_INPUTS_ALREADY_BOUND"):
        repo.bind_inputs(pending.node_run_id, (artifact_id,))


def test_invalid_transitions_fail_closed(tmp_path: Path) -> None:
    _, repo = repository(tmp_path)
    run = running_run(repo)
    pending = next(item for item in run.node_runs if item.node_id == "node.source")

    with pytest.raises(RuntimeConflictError, match="E_NODE_RUN_COMPLETION_REQUIRES_RESULT"):
        repo.transition_node_run(
            pending.node_run_id,
            NodeRunState.COMPLETED,
            occurred_at=at(50),
        )
    with pytest.raises(RuntimeConflictError, match="E_NODE_RUN_EXECUTION_MODE_MISMATCH"):
        repo.transition_node_run(
            pending.node_run_id,
            NodeRunState.WAITING_EXTERNAL,
            occurred_at=at(50),
        )
    assert repo.get_node_run(pending.node_run_id) == pending


def test_result_artifacts_latest_and_get_artifact_round_trip(tmp_path: Path) -> None:
    _, repo = repository(tmp_path)
    run = running_run(repo)

    completed, result = complete_automatic(
        repo,
        run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=30,
        artifact_number=40,
    )

    assert completed.state is NodeRunState.COMPLETED
    assert completed.output_artifact_ids == (rid(40),)
    assert repo.get_result(result.result_id) == result
    assert repo.get_artifact(rid(40)) == result.outputs[0]
    latest = repo.get_latest("node.source")
    assert latest is not None
    assert latest.result_id == result.result_id
    assert latest.stale is False
    assert repo.list_latest() == (latest,)


def test_result_and_artifacts_rollback_together_on_identity_conflict(tmp_path: Path) -> None:
    _, repo = repository(tmp_path)
    run = running_run(repo)
    _, first_result = complete_automatic(
        repo,
        run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=30,
        artifact_number=40,
    )
    rerun_candidates = closure_attempts(
        repo.get_run(run.run_id),
        attempt=2,
        first_number=210,
        created_at=at(82),
    )
    created = repo.create_rerun_attempts(
        run.run_id,
        "node.source",
        rerun_candidates,
        updated_at=at(82),
    )
    second_pending = next(item for item in created if item.node_id == "node.source")
    repo.transition_node_run(
        second_pending.node_run_id,
        NodeRunState.RUNNING,
        occurred_at=at(83),
    )
    conflicting = NodeResult(
        result_id=rid(31),
        node_run_id=second_pending.node_run_id,
        outputs=(
            Artifact(
                artifact_id=rid(40),
                kind="VideoFile",
                path="C:/synthetic/conflict.mkv",
                producer_node_run_id=second_pending.node_run_id,
                producer_port_id="video",
            ),
        ),
        created_at=at(84),
    )

    with pytest.raises(RuntimeConflictError, match="E_RESULT_REGISTRATION_CONFLICT"):
        repo.register_result(conflicting, ended_at=at(85))

    assert repo.get_node_run(second_pending.node_run_id).state is NodeRunState.RUNNING
    with pytest.raises(RuntimeNotFoundError, match="E_RESULT_NOT_FOUND"):
        repo.get_result(conflicting.result_id)
    assert repo.get_result(first_result.result_id) == first_result
    assert repo.get_latest("node.source").result_id == first_result.result_id  # type: ignore[union-attr]


def test_recovery_fails_only_running_and_preserves_waiting_external(tmp_path: Path) -> None:
    store, repo = repository(tmp_path)
    run = running_run(repo)
    source_completed, source_result = complete_automatic(
        repo,
        run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=30,
        artifact_number=40,
    )
    source_artifact_id = source_result.outputs[0].artifact_id
    waiting = next(
        item for item in repo.get_run(run.run_id).node_runs if item.node_id == "node.transform"
    )
    waiting = repo.bind_inputs(waiting.node_run_id, (source_artifact_id,))
    handoff = ExternalHandoff(
        handoff_id=rid(821),
        node_run_id=waiting.node_run_id,
        input_artifact_ids=(source_artifact_id,),
        output_targets=(
            ExternalOutputTarget(
                port_id="video_out",
                path=f"{waiting.work_dir}/external.mkv",
            ),
        ),
        instructions="使用合成外部工具",
        created_at=at(82),
    )
    waiting = repo.transition_node_run(
        waiting.node_run_id,
        NodeRunState.WAITING_EXTERNAL,
        occurred_at=at(82),
        external_handoff=handoff,
    )
    rerun_candidates = closure_attempts(
        repo.get_run(run.run_id),
        attempt=2,
        first_number=220,
        created_at=at(83),
    )
    created = repo.create_rerun_attempts(
        run.run_id,
        "node.source",
        rerun_candidates,
        updated_at=at(83),
    )
    running = next(item for item in created if item.node_id == "node.source")
    repo.transition_node_run(running.node_run_id, NodeRunState.RUNNING, occurred_at=at(84))

    reopened = RuntimeRepository(store)
    recovered = reopened.recover_interrupted(recovered_at=at(90))

    assert tuple(item.node_run_id for item in recovered) == (running.node_run_id,)
    assert recovered[0].state is NodeRunState.FAILED
    assert recovered[0].error is not None
    assert recovered[0].error.reason is FailureReason.INTERRUPTED
    assert reopened.get_node_run(waiting.node_run_id) == waiting
    assert reopened.get_node_run(source_completed.node_run_id) == source_completed


def test_new_upstream_artifact_stales_downstream_without_rewriting_history(
    tmp_path: Path,
) -> None:
    _, repo = repository(tmp_path)
    run = running_run(repo)
    source_completed, source_result = complete_automatic(
        repo,
        run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=30,
        artifact_number=40,
    )
    transform_completed, transform_result = complete_manual(
        repo,
        run,
        node_run_number=21,
        result_number=31,
        artifact_number=41,
        input_id=source_result.outputs[0].artifact_id,
    )
    output_completed, _ = complete_automatic(
        repo,
        run,
        node_id="node.output",
        version="1.0.0",
        node_run_number=22,
        result_number=32,
        artifact_number=None,
        input_ids=(transform_result.outputs[0].artifact_id,),
        port_id="output",
    )
    assert repo.get_latest("node.transform").stale is False  # type: ignore[union-attr]
    assert repo.get_latest("node.output").stale is False  # type: ignore[union-attr]

    complete_automatic(
        repo,
        run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=23,
        result_number=33,
        artifact_number=43,
        attempt=2,
    )

    transform_latest = repo.get_latest("node.transform")
    output_latest = repo.get_latest("node.output")
    assert (
        transform_latest is not None
        and transform_latest.stale_reason is StaleReason.UPSTREAM_CHANGED
    )
    assert output_latest is not None and output_latest.stale_reason is StaleReason.UPSTREAM_CHANGED
    assert repo.get_node_run(source_completed.node_run_id) == source_completed
    assert repo.get_node_run(transform_completed.node_run_id) == transform_completed
    assert repo.get_node_run(output_completed.node_run_id) == output_completed


def test_new_upstream_result_does_not_overwrite_existing_graph_changed_reason(
    tmp_path: Path,
) -> None:
    store, repo = repository(tmp_path)
    run = running_run(repo)
    _, source_result = complete_automatic(
        repo,
        run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=30,
        artifact_number=40,
    )
    complete_manual(
        repo,
        run,
        node_run_number=21,
        result_number=31,
        artifact_number=41,
        input_id=source_result.outputs[0].artifact_id,
    )
    store.save(project(strength=3), definitions())
    assert repo.get_latest("node.transform").stale_reason is StaleReason.GRAPH_CHANGED  # type: ignore[union-attr]

    complete_automatic(
        repo,
        run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=22,
        result_number=32,
        artifact_number=42,
        attempt=2,
    )

    assert repo.get_latest("node.transform").stale_reason is StaleReason.GRAPH_CHANGED  # type: ignore[union-attr]


def test_rerun_closure_conflict_rolls_back_then_success_uses_precise_stale_reasons(
    tmp_path: Path,
) -> None:
    _, repo = repository(tmp_path)
    run = running_run(repo)
    _, source_result = complete_automatic(
        repo,
        run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=30,
        artifact_number=40,
    )
    _, transform_result = complete_manual(
        repo,
        run,
        node_run_number=21,
        result_number=31,
        artifact_number=41,
        input_id=source_result.outputs[0].artifact_id,
    )
    complete_automatic(
        repo,
        run,
        node_id="node.output",
        version="1.0.0",
        node_run_number=22,
        result_number=32,
        artifact_number=None,
        input_ids=(transform_result.outputs[0].artifact_id,),
        port_id="output",
    )
    before_attempts = repo.list_node_runs(run.run_id)
    before_latest = repo.list_latest()
    conflicting = list(closure_attempts(run, attempt=2, first_number=50, created_at=at(100)))
    conflicting[1] = conflicting[1].model_copy(
        update={"node_run_id": before_attempts[0].node_run_id}
    )

    with pytest.raises(RuntimeConflictError, match="E_NODE_RUN_ID_CONFLICT"):
        repo.create_rerun_attempts(
            run.run_id,
            "node.source",
            tuple(conflicting),
            updated_at=at(100),
        )

    assert repo.list_node_runs(run.run_id) == before_attempts
    assert repo.list_latest() == before_latest

    created = repo.create_rerun_attempts(
        run.run_id,
        "node.source",
        closure_attempts(run, attempt=2, first_number=50, created_at=at(100)),
        updated_at=at(101),
    )

    assert tuple(item.attempt for item in created) == (2, 2, 2)
    assert repo.get_latest("node.source").stale_reason is StaleReason.RERUN_REQUESTED  # type: ignore[union-attr]
    assert repo.get_latest("node.transform").stale_reason is StaleReason.UPSTREAM_CHANGED  # type: ignore[union-attr]
    assert repo.get_latest("node.output").stale_reason is StaleReason.UPSTREAM_CHANGED  # type: ignore[union-attr]


def test_rerun_mid_insert_failure_rolls_back_stale_and_first_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, repo = repository(tmp_path)
    run = running_run(repo)
    _, source_result = complete_automatic(
        repo,
        run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=30,
        artifact_number=40,
    )
    _, transform_result = complete_manual(
        repo,
        run,
        node_run_number=21,
        result_number=31,
        artifact_number=41,
        input_id=source_result.outputs[0].artifact_id,
    )
    complete_automatic(
        repo,
        run,
        node_id="node.output",
        version="1.0.0",
        node_run_number=22,
        result_number=32,
        artifact_number=None,
        input_ids=(transform_result.outputs[0].artifact_id,),
        port_id="output",
    )
    before_attempts = repo.list_node_runs(run.run_id)
    before_latest = repo.list_latest()
    candidates = closure_attempts(run, attempt=2, first_number=50, created_at=at(100))
    original = RuntimeRepository._insert_node_run
    inserted = 0

    def fail_on_second(connection: sqlite3.Connection, node_run: NodeRun) -> None:
        nonlocal inserted
        inserted += 1
        if inserted == 2:
            raise sqlite3.IntegrityError("合成 rerun 第二个 attempt INSERT 冲突")
        original(connection, node_run)

    monkeypatch.setattr(RuntimeRepository, "_insert_node_run", staticmethod(fail_on_second))

    with pytest.raises(RuntimeConflictError, match="E_RERUN_CREATE_CONFLICT"):
        repo.create_rerun_attempts(
            run.run_id,
            "node.source",
            candidates,
            updated_at=at(100),
        )

    assert repo.list_node_runs(run.run_id) == before_attempts
    assert repo.list_latest() == before_latest


def test_superseded_old_input_handoff_cannot_register_or_clear_upstream_stale(
    tmp_path: Path,
) -> None:
    _, repo = repository(tmp_path)
    run = running_run(repo)
    _, source_result = complete_automatic(
        repo,
        run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=30,
        artifact_number=40,
    )
    _, first_transform = complete_manual(
        repo,
        run,
        node_run_number=21,
        result_number=31,
        artifact_number=41,
        input_id=source_result.outputs[0].artifact_id,
    )
    candidates = next_closure_attempts(
        repo.get_run(run.run_id),
        source_node_id="node.transform",
        first_number=220,
        created_at=at(85),
    )
    created = repo.create_rerun_attempts(
        run.run_id,
        "node.transform",
        candidates,
        updated_at=at(85),
    )
    late = next(item for item in created if item.node_id == "node.transform")
    late = repo.bind_inputs(late.node_run_id, (source_result.outputs[0].artifact_id,))
    handoff = ExternalHandoff(
        handoff_id=rid(822),
        node_run_id=late.node_run_id,
        input_artifact_ids=late.input_artifact_ids,
        output_targets=(
            ExternalOutputTarget(
                port_id="video_out",
                path=f"{late.work_dir}/late-old-input.mkv",
            ),
        ),
        instructions="使用合成外部工具",
        created_at=at(86),
    )
    repo.transition_node_run(
        late.node_run_id,
        NodeRunState.WAITING_EXTERNAL,
        occurred_at=at(86),
        external_handoff=handoff,
    )
    complete_automatic(
        repo,
        run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=23,
        result_number=33,
        artifact_number=43,
        attempt=2,
    )
    stale_before = repo.get_latest("node.transform")
    assert stale_before is not None
    assert stale_before.result_id == first_transform.result_id
    assert stale_before.stale_reason is StaleReason.RERUN_REQUESTED
    late_result = NodeResult(
        result_id=rid(34),
        node_run_id=late.node_run_id,
        outputs=(
            Artifact(
                artifact_id=rid(44),
                kind="VideoFile",
                path="C:/synthetic/late-old-input.mkv",
                producer_node_run_id=late.node_run_id,
                producer_port_id="video_out",
            ),
        ),
        created_at=at(91),
    )

    with pytest.raises(RuntimeConflictError, match="E_RESULT_ATTEMPT_SUPERSEDED"):
        repo.register_result(late_result, ended_at=at(92))

    with pytest.raises(RuntimeNotFoundError, match="E_RESULT_NOT_FOUND"):
        repo.get_result(late_result.result_id)
    assert repo.get_latest("node.transform") == stale_before


def test_project_edit_preserves_history_and_old_run_result_cannot_become_fresh(
    tmp_path: Path,
) -> None:
    store, repo = repository(tmp_path)
    run = running_run(repo)
    _, source_result = complete_automatic(
        repo,
        run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=30,
        artifact_number=40,
    )
    _, first_transform = complete_manual(
        repo,
        run,
        node_run_number=21,
        result_number=31,
        artifact_number=41,
        input_id=source_result.outputs[0].artifact_id,
    )
    assert repo.get_latest("node.transform").stale is False  # type: ignore[union-attr]

    store.save(project(transform_x=275.0), definitions())
    assert repo.get_latest("node.transform").stale is False  # type: ignore[union-attr]
    assert repo.get_run(run.run_id).graph_snapshot == run.graph_snapshot

    store.save(project(strength=3, transform_x=275.0), definitions())
    changed_latest = repo.get_latest("node.transform")
    assert changed_latest is not None
    assert changed_latest.stale_reason is StaleReason.GRAPH_CHANGED
    candidates = next_closure_attempts(
        repo.get_run(run.run_id),
        source_node_id="node.transform",
        first_number=230,
        created_at=at(85),
    )
    created = repo.create_rerun_attempts(
        run.run_id,
        "node.transform",
        candidates,
        updated_at=at(85),
    )
    second_pending = next(item for item in created if item.node_id == "node.transform")
    second_pending = repo.bind_inputs(
        second_pending.node_run_id,
        (source_result.outputs[0].artifact_id,),
    )
    output_path = f"{second_pending.work_dir}/old-run.mkv"
    handoff = ExternalHandoff(
        handoff_id=rid(822),
        node_run_id=second_pending.node_run_id,
        input_artifact_ids=second_pending.input_artifact_ids,
        output_targets=(ExternalOutputTarget(port_id="video_out", path=output_path),),
        instructions="使用合成外部工具",
        created_at=at(86),
    )
    repo.transition_node_run(
        second_pending.node_run_id,
        NodeRunState.WAITING_EXTERNAL,
        occurred_at=at(86),
        external_handoff=handoff,
    )
    old_run_result = NodeResult(
        result_id=rid(34),
        node_run_id=second_pending.node_run_id,
        outputs=(
            Artifact(
                artifact_id=rid(44),
                kind="VideoFile",
                path=output_path,
                producer_node_run_id=second_pending.node_run_id,
                producer_port_id="video_out",
            ),
        ),
        created_at=at(87),
    )
    repo.register_result(old_run_result, ended_at=at(88))

    latest = repo.get_latest("node.transform")
    assert latest is not None
    assert latest.result_id == first_transform.result_id
    assert latest.stale_reason is StaleReason.GRAPH_CHANGED
    assert repo.get_result(first_transform.result_id) == first_transform
    assert repo.get_result(old_run_result.result_id) == old_run_result
    assert sum(item.node_id == "node.transform" for item in repo.list_node_runs(run.run_id)) == 2


def test_exact_completed_result_can_be_reused_without_new_artifact(tmp_path: Path) -> None:
    store, repo = repository(tmp_path)
    first_run = running_run(repo, run_id_number=1)
    _, result = complete_automatic(
        repo,
        first_run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=30,
        artifact_number=40,
    )
    second_run = running_run(repo, run_id_number=2, created_at=at(100))
    second_node = next(item for item in second_run.node_runs if item.node_id == "node.source")

    reused = repo.reuse_result(second_node.node_run_id, result.result_id, completed_at=at(102))

    assert reused.state is NodeRunState.COMPLETED
    assert reused.reused_from_result_id == result.result_id
    assert reused.output_artifact_ids == (result.outputs[0].artifact_id,)
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT count(*) FROM node_results").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 1


def test_historical_result_reuse_does_not_overwrite_another_fresh_head(
    tmp_path: Path,
) -> None:
    _, repo = repository(tmp_path)
    first_run = running_run(repo, run_id_number=1)
    _, first_result = complete_automatic(
        repo,
        first_run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=30,
        artifact_number=40,
    )
    second_run = running_run(repo, run_id_number=2)
    _, second_result = complete_automatic(
        repo,
        second_run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=21,
        result_number=31,
        artifact_number=41,
    )
    fresh_before = repo.get_latest("node.source")
    assert fresh_before is not None and fresh_before.result_id == second_result.result_id
    third_run = running_run(repo, run_id_number=3, created_at=at(100))
    candidate = next(item for item in third_run.node_runs if item.node_id == "node.source")

    reused = repo.reuse_result(
        candidate.node_run_id,
        first_result.result_id,
        completed_at=at(102),
    )

    assert reused.reused_from_result_id == first_result.result_id
    assert reused.output_artifact_ids == (first_result.outputs[0].artifact_id,)
    assert repo.get_latest("node.source") == fresh_before
    assert repo.list_results_for_node("node.source") == (second_result, first_result)
    assert repo.list_results_for_node("node.source", before=at(82)) == (first_result,)
    with pytest.raises(RuntimeConflictError, match="E_RUNTIME_TIMESTAMP_INVALID"):
        repo.list_results_for_node("node.source", before=datetime(2026, 8, 24, 2, 0))


def test_old_snapshot_late_result_cannot_overwrite_new_graph_fresh_head(
    tmp_path: Path,
) -> None:
    store, repo = repository(tmp_path)
    old_run = running_run(repo, run_id_number=1)
    _, source_result = complete_automatic(
        repo,
        old_run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=30,
        artifact_number=40,
    )
    _, old_transform = complete_manual(
        repo,
        old_run,
        node_run_number=21,
        result_number=31,
        artifact_number=41,
        input_id=source_result.outputs[0].artifact_id,
    )
    store.save(project(strength=3), definitions())
    new_run = running_run(repo, run_id_number=2, created_at=at(100))
    new_source = next(item for item in new_run.node_runs if item.node_id == "node.source")
    repo.reuse_result(
        new_source.node_run_id,
        source_result.result_id,
        completed_at=at(102),
    )
    _, new_transform = complete_manual(
        repo,
        new_run,
        node_run_number=22,
        result_number=32,
        artifact_number=42,
        input_id=source_result.outputs[0].artifact_id,
    )
    fresh_before = repo.get_latest("node.transform")
    assert fresh_before is not None
    assert fresh_before.result_id == new_transform.result_id
    assert fresh_before.stale is False

    candidates = next_closure_attempts(
        repo.get_run(old_run.run_id),
        source_node_id="node.transform",
        first_number=240,
        created_at=at(90),
    )
    created = repo.create_rerun_attempts(
        old_run.run_id,
        "node.transform",
        candidates,
        updated_at=at(90),
    )
    late = next(item for item in created if item.node_id == "node.transform")
    late = repo.bind_inputs(late.node_run_id, (source_result.outputs[0].artifact_id,))
    handoff = ExternalHandoff(
        handoff_id=rid(823),
        node_run_id=late.node_run_id,
        input_artifact_ids=late.input_artifact_ids,
        output_targets=(
            ExternalOutputTarget(
                port_id="video_out",
                path=f"{late.work_dir}/old-graph-late.mkv",
            ),
        ),
        instructions="使用合成外部工具",
        created_at=at(93),
    )
    repo.transition_node_run(
        late.node_run_id,
        NodeRunState.WAITING_EXTERNAL,
        occurred_at=at(93),
        external_handoff=handoff,
    )
    late_result = NodeResult(
        result_id=rid(33),
        node_run_id=late.node_run_id,
        outputs=(
            Artifact(
                artifact_id=rid(43),
                kind="VideoFile",
                path="C:/synthetic/old-graph-late.mkv",
                producer_node_run_id=late.node_run_id,
                producer_port_id="video_out",
            ),
        ),
        created_at=at(94),
    )

    repo.register_result(late_result, ended_at=at(95))

    assert repo.get_result(old_transform.result_id) == old_transform
    assert repo.get_result(late_result.result_id) == late_result
    assert repo.get_latest("node.transform") == fresh_before


def test_old_snapshot_rerun_creates_history_without_staling_new_graph_projection(
    tmp_path: Path,
) -> None:
    store, repo = repository(tmp_path)
    old_run = running_run(repo)
    _, source_result = complete_automatic(
        repo,
        old_run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=30,
        artifact_number=40,
    )
    _, transform_result = complete_manual(
        repo,
        old_run,
        node_run_number=21,
        result_number=31,
        artifact_number=41,
        input_id=source_result.outputs[0].artifact_id,
    )
    complete_automatic(
        repo,
        old_run,
        node_id="node.output",
        version="1.0.0",
        node_run_number=22,
        result_number=32,
        artifact_number=None,
        input_ids=(transform_result.outputs[0].artifact_id,),
        port_id="output",
    )
    store.save(project(strength=3), definitions())
    new_run = running_run(repo, run_id_number=2, created_at=at(100))
    new_source = next(item for item in new_run.node_runs if item.node_id == "node.source")
    repo.reuse_result(
        new_source.node_run_id,
        source_result.result_id,
        completed_at=at(102),
    )
    complete_manual(
        repo,
        new_run,
        node_run_number=23,
        result_number=33,
        artifact_number=43,
        input_id=source_result.outputs[0].artifact_id,
    )
    projection_before = repo.list_latest()

    created = repo.create_rerun_attempts(
        old_run.run_id,
        "node.transform",
        closure_attempts(
            old_run,
            attempt=2,
            first_number=50,
            node_ids=("node.transform", "node.output"),
            created_at=at(90),
        ),
        updated_at=at(100),
    )

    assert tuple(item.node_id for item in created) == ("node.transform", "node.output")
    assert repo.list_latest() == projection_before


def test_completed_node_run_relation_tampering_to_other_artifact_fails_closed(
    tmp_path: Path,
) -> None:
    store, repo = repository(tmp_path)
    run = running_run(repo)
    _, source_result = complete_automatic(
        repo,
        run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=30,
        artifact_number=40,
    )
    transform, transform_result = complete_manual(
        repo,
        run,
        node_run_number=21,
        result_number=31,
        artifact_number=41,
        input_id=source_result.outputs[0].artifact_id,
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE node_runs SET output_artifact_ids_json = ? WHERE node_run_id = ?",
            (f'["{source_result.outputs[0].artifact_id}"]', transform.node_run_id),
        )

    with pytest.raises(RuntimeDataError, match="E_NODE_RUN_RESULT_RELATION_CORRUPT"):
        repo.get_run(run.run_id)
    with pytest.raises(RuntimeDataError, match="E_NODE_RUN_RESULT_RELATION_CORRUPT"):
        repo.get_node_run(transform.node_run_id)
    with pytest.raises(RuntimeDataError, match="E_NODE_RUN_RESULT_RELATION_CORRUPT"):
        repo.get_result(transform_result.result_id)
    with pytest.raises(RuntimeDataError, match="E_NODE_RUN_RESULT_RELATION_CORRUPT"):
        repo.get_artifact(transform_result.outputs[0].artifact_id)
    with pytest.raises(RuntimeDataError, match="E_NODE_RUN_RESULT_RELATION_CORRUPT"):
        repo.get_latest("node.transform")
    with pytest.raises(RuntimeDataError, match="E_NODE_RUN_RESULT_RELATION_CORRUPT"):
        repo.list_latest()
    with pytest.raises(RuntimeDataError, match="E_NODE_RUN_RESULT_RELATION_CORRUPT"):
        repo.list_results_for_node("node.transform")


def test_attempt_history_gap_fails_closed_on_run_read(tmp_path: Path) -> None:
    store, repo = repository(tmp_path)
    run = running_run(repo)
    complete_automatic(
        repo,
        run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=30,
        artifact_number=40,
    )
    second, _ = complete_automatic(
        repo,
        run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=21,
        result_number=31,
        artifact_number=41,
        attempt=2,
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE node_runs SET attempt = 3 WHERE node_run_id = ?",
            (second.node_run_id,),
        )

    with pytest.raises(RuntimeDataError, match="E_NODE_RUN_ATTEMPT_HISTORY_CORRUPT"):
        repo.get_run(run.run_id)


def test_latest_stale_and_owner_relationship_are_strictly_validated(tmp_path: Path) -> None:
    store, repo = repository(tmp_path)
    run = running_run(repo)
    _, source_result = complete_automatic(
        repo,
        run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=30,
        artifact_number=40,
    )
    complete_manual(
        repo,
        run,
        node_run_number=21,
        result_number=31,
        artifact_number=41,
        input_id=source_result.outputs[0].artifact_id,
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE latest_results SET result_id = ? WHERE node_id = 'node.transform'",
            (source_result.result_id,),
        )

    with pytest.raises(RuntimeDataError, match="E_LATEST_RESULT_RELATION_CORRUPT"):
        repo.get_latest("node.transform")

    with sqlite3.connect(store.path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE latest_results SET stale = 2, stale_reason = 'graph_changed' "
            "WHERE node_id = 'node.source'"
        )

    with pytest.raises(RuntimeDataError, match="E_LATEST_STALE_CORRUPT"):
        repo.get_latest("node.source")


def test_public_write_timestamp_rejects_naive_before_mutation(tmp_path: Path) -> None:
    _, repo = repository(tmp_path)
    run = running_run(repo)
    complete_automatic(
        repo,
        run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=30,
        artifact_number=40,
    )
    before = repo.get_latest("node.source")

    with pytest.raises(RuntimeConflictError, match="E_RUNTIME_TIMESTAMP_INVALID"):
        repo.mark_latest_stale(
            ("node.source",),
            StaleReason.RERUN_REQUESTED,
            updated_at=datetime(2026, 8, 24, 3, 0),
        )

    assert repo.get_latest("node.source") == before


def test_corrupt_runtime_json_fails_closed(tmp_path: Path) -> None:
    store, repo = repository(tmp_path)
    run = running_run(repo)
    pending = next(item for item in run.node_runs if item.node_id == "node.source")
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE node_runs SET input_artifact_ids_json = '[NaN]' WHERE node_run_id = ?",
            (pending.node_run_id,),
        )

    with pytest.raises(RuntimeDataError, match="E_RUNTIME_DATA_CORRUPT"):
        repo.get_node_run(pending.node_run_id)


def test_missing_wrong_and_mode_mismatched_inputs_fail_before_node_start(
    tmp_path: Path,
) -> None:
    _, repo = repository(tmp_path)
    run = running_run(repo)
    source_pending = next(item for item in run.node_runs if item.node_id == "node.source")
    automatic_handoff = ExternalHandoff(
        handoff_id=rid(500),
        node_run_id=source_pending.node_run_id,
        output_targets=(
            ExternalOutputTarget(
                port_id="video",
                path=f"{source_pending.work_dir}/source.mkv",
            ),
        ),
        created_at=at(50),
    )
    for unexpected in (
        {"exit_code": 0},
        {
            "error": RuntimeFailure(
                reason=FailureReason.EXECUTION_ERROR,
                message="启动字段不应被静默忽略",
            )
        },
    ):
        with pytest.raises(RuntimeConflictError, match="E_NODE_RUN_START_FIELDS"):
            repo.transition_node_run(
                source_pending.node_run_id,
                NodeRunState.RUNNING,
                occurred_at=at(50),
                **unexpected,
            )
    with pytest.raises(RuntimeConflictError, match="E_NODE_RUN_EXECUTION_MODE_MISMATCH"):
        repo.transition_node_run(
            source_pending.node_run_id,
            NodeRunState.WAITING_EXTERNAL,
            occurred_at=at(50),
            external_handoff=automatic_handoff,
        )

    _, source_result = complete_automatic(
        repo,
        run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=30,
        artifact_number=40,
    )
    transform = next(
        item for item in repo.get_run(run.run_id).node_runs if item.node_id == "node.transform"
    )
    valid_started_at = at(82)
    valid_handoff = ExternalHandoff(
        handoff_id=rid(501),
        node_run_id=transform.node_run_id,
        input_artifact_ids=(source_result.outputs[0].artifact_id,),
        output_targets=(
            ExternalOutputTarget(
                port_id="video_out",
                path=f"{transform.work_dir}/manual.mkv",
            ),
        ),
        instructions="使用合成外部工具",
        created_at=valid_started_at,
    )
    with pytest.raises(RuntimeConflictError, match="E_NODE_RUN_INPUT_BINDING_MISMATCH"):
        repo.transition_node_run(
            transform.node_run_id,
            NodeRunState.WAITING_EXTERNAL,
            occurred_at=valid_started_at,
            external_handoff=valid_handoff,
        )
    with pytest.raises(RuntimeConflictError, match="E_NODE_RUN_INPUT_BINDING_MISMATCH"):
        repo.bind_inputs(transform.node_run_id, (rid(999),))

    transform = repo.bind_inputs(transform.node_run_id, (source_result.outputs[0].artifact_id,))
    with pytest.raises(RuntimeConflictError, match="E_NODE_RUN_EXECUTION_MODE_MISMATCH"):
        repo.transition_node_run(
            transform.node_run_id,
            NodeRunState.RUNNING,
            occurred_at=valid_started_at,
        )

    # 测试 fixture 的 ``C:/synthetic`` 在 POSIX 上是相对路径；先解析为宿主绝对路径，
    # 确保越界目标在 Windows 与 Linux CI 上表达同一语义。
    outside_target = str(Path(transform.work_dir).resolve(strict=False).parent / "outside.mkv")
    malformed = (
        valid_handoff.model_copy(update={"input_artifact_ids": (rid(998),)}),
        valid_handoff.model_copy(update={"output_targets": ()}),
        valid_handoff.model_copy(
            update={
                "output_targets": (
                    ExternalOutputTarget(
                        port_id="wrong",
                        path=f"{transform.work_dir}/manual.mkv",
                    ),
                )
            }
        ),
        valid_handoff.model_copy(update={"instructions": "错误说明"}),
        valid_handoff.model_copy(update={"created_at": at(83)}),
        valid_handoff.model_copy(
            update={
                "output_targets": (
                    ExternalOutputTarget(
                        port_id="video_out",
                        path=outside_target,
                    ),
                )
            }
        ),
    )
    for handoff in malformed:
        with pytest.raises(RuntimeConflictError, match="E_HANDOFF_CONTRACT_MISMATCH"):
            repo.transition_node_run(
                transform.node_run_id,
                NodeRunState.WAITING_EXTERNAL,
                occurred_at=valid_started_at,
                external_handoff=handoff,
            )
        assert repo.get_node_run(transform.node_run_id).state is NodeRunState.PENDING

    with pytest.raises(RuntimeConflictError, match="E_NODE_RUN_START_FIELDS"):
        repo.transition_node_run(
            transform.node_run_id,
            NodeRunState.WAITING_EXTERNAL,
            occurred_at=valid_started_at,
            external_handoff=valid_handoff,
            exit_code=0,
        )

    waiting = repo.transition_node_run(
        transform.node_run_id,
        NodeRunState.WAITING_EXTERNAL,
        occurred_at=valid_started_at,
        external_handoff=valid_handoff,
    )
    assert waiting.external_handoff == valid_handoff


@pytest.mark.parametrize(
    ("column", "value"),
    (("producer_port_id", "wrong"), ("kind", "AudioFile")),
)
def test_corrupt_upstream_artifact_port_or_kind_blocks_input_binding(
    tmp_path: Path,
    column: str,
    value: str,
) -> None:
    store, repo = repository(tmp_path)
    run = running_run(repo)
    _, source_result = complete_automatic(
        repo,
        run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=30,
        artifact_number=40,
    )
    transform = next(
        item for item in repo.get_run(run.run_id).node_runs if item.node_id == "node.transform"
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            f"UPDATE artifacts SET {column} = ? WHERE artifact_id = ?",
            (value, source_result.outputs[0].artifact_id),
        )

    with pytest.raises(RuntimeDataError, match="E_RESULT_OUTPUT_CONTRACT_CORRUPT"):
        repo.bind_inputs(transform.node_run_id, (source_result.outputs[0].artifact_id,))
    with sqlite3.connect(store.path) as connection:
        assert (
            connection.execute(
                "SELECT input_artifact_ids_json FROM node_runs WHERE node_run_id = ?",
                (transform.node_run_id,),
            ).fetchone()[0]
            == "[]"
        )


def test_result_output_contract_rejects_port_kind_order_and_ordinal_atomically(
    tmp_path: Path,
) -> None:
    definition = NodeDefinition(
        type_id="test.MultiOutput",
        version="1.0.0",
        output_ports=(
            PortSpec(port_id="video", data_type="VideoFile"),
            PortSpec(port_id="audio", data_type="AudioFile"),
        ),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="tests.synthetic:multi"),
    )
    project_value = Project(
        project_id="project.multi-output",
        name="多输出合同",
        graph=Graph(
            nodes=(
                NodeInstance(
                    node_id="node.multi",
                    type_id=definition.type_id,
                    definition_version=definition.version,
                ),
            )
        ),
    )
    store = ProjectStore.create(tmp_path / "multi.zniku", project_value, (definition,))
    repo = RuntimeRepository(store)
    pending_run = Run.pending(
        run_id=rid(600),
        project_id=project_value.project_id,
        graph_snapshot=project_value.graph,
        definitions_snapshot=(definition,),
        created_at=at(1),
    )
    pending_node = NodeRun.pending(
        node_run_id=rid(601),
        run_id=pending_run.run_id,
        node_id="node.multi",
        definition_version="1.0.0",
        attempt=1,
        input_artifact_ids=(),
        created_at=at(2),
        work_dir="C:/synthetic/multi",
    )
    repo.start_run(pending_run, (pending_node,), started_at=at(3))
    repo.transition_node_run(
        pending_node.node_run_id,
        NodeRunState.RUNNING,
        occurred_at=at(4),
    )

    def output(
        number: int,
        *,
        port: str,
        kind: str,
        ordinal: int | None = None,
    ) -> Artifact:
        return Artifact(
            artifact_id=rid(number),
            kind=kind,
            path=f"C:/synthetic/output-{number}",
            producer_node_run_id=pending_node.node_run_id,
            producer_port_id=port,
            ordinal=ordinal,
        )

    invalid_outputs = (
        (output(610, port="wrong", kind="VideoFile"), output(611, port="audio", kind="AudioFile")),
        (output(612, port="video", kind="AudioFile"), output(613, port="audio", kind="AudioFile")),
        (output(614, port="audio", kind="AudioFile"), output(615, port="video", kind="VideoFile")),
        (
            output(616, port="video", kind="VideoFile", ordinal=0),
            output(617, port="audio", kind="AudioFile"),
        ),
    )
    for index, outputs in enumerate(invalid_outputs):
        result = NodeResult(
            result_id=rid(620 + index),
            node_run_id=pending_node.node_run_id,
            outputs=outputs,
            created_at=at(5),
        )
        with pytest.raises(RuntimeConflictError, match="E_RESULT_OUTPUT_CONTRACT"):
            repo.register_result(result, ended_at=at(6))
        assert repo.get_node_run(pending_node.node_run_id).state is NodeRunState.RUNNING
        with sqlite3.connect(store.path) as connection:
            assert connection.execute("SELECT count(*) FROM node_results").fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 0

    wrong_python_exit = NodeResult(
        result_id=rid(629),
        node_run_id=pending_node.node_run_id,
        outputs=(
            output(618, port="video", kind="VideoFile"),
            output(619, port="audio", kind="AudioFile"),
        ),
        created_at=at(6),
    )
    with pytest.raises(RuntimeConflictError, match="E_RESULT_EXIT_CODE"):
        repo.register_result(wrong_python_exit, ended_at=at(7), exit_code=0)

    correct = NodeResult(
        result_id=rid(630),
        node_run_id=pending_node.node_run_id,
        outputs=(
            output(631, port="video", kind="VideoFile"),
            output(632, port="audio", kind="AudioFile"),
        ),
        created_at=at(7),
    )
    assert repo.register_result(correct, ended_at=at(8)).state is NodeRunState.COMPLETED


def test_command_result_requires_exact_zero_exit_code(tmp_path: Path) -> None:
    definition = NodeDefinition(
        type_id="test.Command",
        version="1.0.0",
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=CommandExecutorSpec(executable="synthetic.exe", argv=("--test",)),
    )
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="command",
                type_id=definition.type_id,
                definition_version=definition.version,
            ),
        )
    )
    project_value = Project(
        project_id="project.command-exit",
        name="command exit code",
        graph=graph,
    )
    store = ProjectStore.create(tmp_path / "command.zniku", project_value, (definition,))
    repo = RuntimeRepository(store)
    pending_run = Run.pending(
        run_id=rid(650),
        project_id=project_value.project_id,
        graph_snapshot=graph,
        definitions_snapshot=(definition,),
        created_at=at(1),
    )
    pending_node = NodeRun.pending(
        node_run_id=rid(651),
        run_id=pending_run.run_id,
        node_id="command",
        definition_version="1.0.0",
        attempt=1,
        input_artifact_ids=(),
        created_at=at(2),
        work_dir="C:/synthetic/command",
    )
    repo.start_run(pending_run, (pending_node,), started_at=at(3))
    repo.transition_node_run(
        pending_node.node_run_id,
        NodeRunState.RUNNING,
        occurred_at=at(4),
    )
    result = NodeResult(
        result_id=rid(652),
        node_run_id=pending_node.node_run_id,
        created_at=at(5),
    )
    with pytest.raises(RuntimeConflictError, match="E_RESULT_EXIT_CODE"):
        repo.register_result(result, ended_at=at(6))
    assert repo.get_node_run(pending_node.node_run_id).state is NodeRunState.RUNNING
    completed = repo.register_result(result, ended_at=at(6), exit_code=0)
    assert completed.state is NodeRunState.COMPLETED
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE node_runs SET exit_code = NULL WHERE node_run_id = ?",
            (completed.node_run_id,),
        )
    with pytest.raises(RuntimeDataError, match="E_NODE_RUN_EXIT_CODE_RELATION_CORRUPT"):
        repo.get_result(result.result_id)


def test_ordered_many_binding_uses_ordinal_and_historical_swap_fails_closed(
    tmp_path: Path,
) -> None:
    source_definition = NodeDefinition(
        type_id="test.OrderedSource",
        version="1.0.0",
        output_ports=(PortSpec(port_id="video", data_type="VideoFile"),),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="tests.synthetic:ordered-source"),
    )
    merge_definition = NodeDefinition(
        type_id="test.OrderedMerge",
        version="1.0.0",
        input_ports=(
            PortSpec(
                port_id="videos",
                data_type="VideoFile",
                cardinality=Cardinality.ORDERED_MANY,
                required=True,
            ),
        ),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="tests.synthetic:ordered-merge"),
    )
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="source.a",
                type_id=source_definition.type_id,
                definition_version=source_definition.version,
            ),
            NodeInstance(
                node_id="source.b",
                type_id=source_definition.type_id,
                definition_version=source_definition.version,
            ),
            NodeInstance(
                node_id="merge",
                type_id=merge_definition.type_id,
                definition_version=merge_definition.version,
            ),
        ),
        edges=(
            Edge(
                source_node_id="source.b",
                source_port_id="video",
                target_node_id="merge",
                target_port_id="videos",
                ordinal=1,
            ),
            Edge(
                source_node_id="source.a",
                source_port_id="video",
                target_node_id="merge",
                target_port_id="videos",
                ordinal=0,
            ),
        ),
    )
    project_value = Project(
        project_id="project.ordered-many",
        name="ordered_many 合同",
        graph=graph,
    )
    store = ProjectStore.create(
        tmp_path / "ordered.zniku",
        project_value,
        (source_definition, merge_definition),
    )
    repo = RuntimeRepository(store)
    pending_run = Run.pending(
        run_id=rid(700),
        project_id=project_value.project_id,
        graph_snapshot=graph,
        definitions_snapshot=(source_definition, merge_definition),
        created_at=at(1),
    )
    attempts = tuple(
        NodeRun.pending(
            node_run_id=rid(701 + index),
            run_id=pending_run.run_id,
            node_id=node.node_id,
            definition_version=node.definition_version,
            attempt=1,
            input_artifact_ids=(),
            created_at=at(2),
            work_dir=f"C:/synthetic/ordered-{index}",
        )
        for index, node in enumerate(graph.nodes)
    )
    run = repo.start_run(pending_run, attempts, started_at=at(3))

    artifact_ids: dict[str, str] = {}
    for index, node_id in enumerate(("source.a", "source.b")):
        node_run = next(item for item in run.node_runs if item.node_id == node_id)
        repo.transition_node_run(
            node_run.node_run_id,
            NodeRunState.RUNNING,
            occurred_at=at(10 + index),
        )
        artifact = Artifact(
            artifact_id=rid(710 + index),
            kind="VideoFile",
            path=f"C:/synthetic/{node_id}.mkv",
            producer_node_run_id=node_run.node_run_id,
            producer_port_id="video",
        )
        result = NodeResult(
            result_id=rid(720 + index),
            node_run_id=node_run.node_run_id,
            outputs=(artifact,),
            created_at=at(20 + index),
        )
        repo.register_result(result, ended_at=at(22 + index))
        artifact_ids[node_id] = artifact.artifact_id

    merge = next(item for item in run.node_runs if item.node_id == "merge")
    expected = (artifact_ids["source.a"], artifact_ids["source.b"])
    with pytest.raises(RuntimeConflictError, match="E_NODE_RUN_INPUT_BINDING_MISMATCH"):
        repo.bind_inputs(merge.node_run_id, tuple(reversed(expected)))
    merge = repo.bind_inputs(merge.node_run_id, expected)
    repo.transition_node_run(
        merge.node_run_id,
        NodeRunState.RUNNING,
        occurred_at=at(30),
    )
    merge_result = NodeResult(
        result_id=rid(730),
        node_run_id=merge.node_run_id,
        created_at=at(31),
    )
    repo.register_result(merge_result, ended_at=at(32))

    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE node_runs SET input_artifact_ids_json = ? WHERE node_run_id = ?",
            (_dump_test_json(tuple(reversed(expected))), merge.node_run_id),
        )
    with pytest.raises(RuntimeDataError, match="E_NODE_RUN_INPUT_RELATION_CORRUPT"):
        repo.get_run(run.run_id)


def _dump_test_json(values: tuple[str, ...]) -> str:
    """生成测试篡改所需的紧凑 JSON，不作为工程 authority。"""

    return "[" + ",".join(f'"{item}"' for item in values) + "]"


def test_ordered_many_binding_allows_same_artifact_at_distinct_ordinals(
    tmp_path: Path,
) -> None:
    source_definition = NodeDefinition(
        type_id="test.RepeatedSource",
        version="1.0.0",
        output_ports=(PortSpec(port_id="video", data_type="VideoFile"),),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="tests.synthetic:repeated-source"),
    )
    merge_definition = NodeDefinition(
        type_id="test.RepeatedMerge",
        version="1.0.0",
        input_ports=(
            PortSpec(
                port_id="videos",
                data_type="VideoFile",
                cardinality=Cardinality.ORDERED_MANY,
                required=True,
            ),
        ),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="tests.synthetic:repeated-merge"),
    )
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="source",
                type_id=source_definition.type_id,
                definition_version=source_definition.version,
            ),
            NodeInstance(
                node_id="merge",
                type_id=merge_definition.type_id,
                definition_version=merge_definition.version,
            ),
        ),
        edges=tuple(
            Edge(
                source_node_id="source",
                source_port_id="video",
                target_node_id="merge",
                target_port_id="videos",
                ordinal=ordinal,
            )
            for ordinal in (0, 1)
        ),
    )
    project_value = Project(
        project_id="project.repeated-input",
        name="重复输入 identity",
        graph=graph,
    )
    store = ProjectStore.create(
        tmp_path / "repeated.zniku",
        project_value,
        (source_definition, merge_definition),
    )
    repo = RuntimeRepository(store)
    pending_run = Run.pending(
        run_id=rid(740),
        project_id=project_value.project_id,
        graph_snapshot=graph,
        definitions_snapshot=(source_definition, merge_definition),
        created_at=at(1),
    )
    attempts = tuple(
        NodeRun.pending(
            node_run_id=rid(741 + index),
            run_id=pending_run.run_id,
            node_id=node.node_id,
            definition_version=node.definition_version,
            attempt=1,
            input_artifact_ids=(),
            created_at=at(2),
            work_dir=f"C:/synthetic/repeated-{index}",
        )
        for index, node in enumerate(graph.nodes)
    )
    run = repo.start_run(pending_run, attempts, started_at=at(3))
    source = next(item for item in run.node_runs if item.node_id == "source")
    repo.transition_node_run(source.node_run_id, NodeRunState.RUNNING, occurred_at=at(4))
    artifact = Artifact(
        artifact_id=rid(750),
        kind="VideoFile",
        path="C:/synthetic/repeated.mkv",
        producer_node_run_id=source.node_run_id,
        producer_port_id="video",
    )
    repo.register_result(
        NodeResult(
            result_id=rid(751),
            node_run_id=source.node_run_id,
            outputs=(artifact,),
            created_at=at(5),
        ),
        ended_at=at(6),
    )
    merge = next(item for item in run.node_runs if item.node_id == "merge")

    bound = repo.bind_inputs(
        merge.node_run_id,
        (artifact.artifact_id, artifact.artifact_id),
    )
    assert bound.input_artifact_ids == (artifact.artifact_id, artifact.artifact_id)


def test_run_lifecycle_requires_atomic_start_complete_children_and_valid_times(
    tmp_path: Path,
) -> None:
    store, repo = repository(tmp_path)
    snapshot = store.load()
    pending_only = Run.pending(
        run_id=rid(800),
        project_id=snapshot.project.project_id,
        graph_snapshot=snapshot.project.graph,
        definitions_snapshot=snapshot.definitions,
        created_at=at(1),
    )
    repo.create_run(pending_only)
    with pytest.raises(RuntimeConflictError, match="E_RUN_TRANSITION_INVALID"):
        repo.transition_run(pending_only.run_id, RunState.RUNNING, occurred_at=at(2))
    assert repo.get_run(pending_only.run_id).state is RunState.PENDING

    selected_pending = Run.pending(
        run_id=rid(801),
        project_id=snapshot.project.project_id,
        graph_snapshot=snapshot.project.graph,
        definitions_snapshot=snapshot.definitions,
        selected_targets=("node.source",),
        created_at=at(10),
    )
    source_attempt = NodeRun.pending(
        node_run_id=rid(802),
        run_id=selected_pending.run_id,
        node_id="node.source",
        definition_version="1.0.0",
        attempt=1,
        input_artifact_ids=(),
        created_at=at(11),
        work_dir="C:/synthetic/selected-source",
    )
    run = repo.start_run(selected_pending, (source_attempt,), started_at=at(12))
    with pytest.raises(RuntimeConflictError, match="E_RUN_COMPLETION_INCOMPLETE"):
        repo.transition_run(run.run_id, RunState.COMPLETED, occurred_at=at(20))
    with pytest.raises(RuntimeConflictError, match="E_RUN_TRANSITION_INVALID"):
        repo.transition_run(
            run.run_id,
            RunState.FAILED,
            occurred_at=at(20),
            error=RuntimeFailure(
                reason=FailureReason.EXECUTION_ERROR,
                message="不得直接终结聚合",
            ),
        )

    completed, _ = complete_automatic(
        repo,
        run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=803,
        artifact_number=804,
    )
    assert completed.ended_at is not None
    with pytest.raises(RuntimeConflictError, match="E_RUN_COMPLETION_TIME_INVALID"):
        repo.transition_run(
            run.run_id,
            RunState.COMPLETED,
            occurred_at=completed.ended_at - timedelta(microseconds=1),
        )
    finished = repo.transition_run(
        run.run_id,
        RunState.COMPLETED,
        occurred_at=completed.ended_at,
    )
    assert finished.state is RunState.COMPLETED


def test_start_rerun_and_single_attempt_apis_enforce_aggregate_time_boundaries(
    tmp_path: Path,
) -> None:
    store, repo = repository(tmp_path)
    snapshot = store.load()
    late_pending = Run.pending(
        run_id=rid(820),
        project_id=snapshot.project.project_id,
        graph_snapshot=snapshot.project.graph,
        definitions_snapshot=snapshot.definitions,
        selected_targets=("node.source",),
        created_at=at(1),
    )
    late_attempt = NodeRun.pending(
        node_run_id=rid(821),
        run_id=late_pending.run_id,
        node_id="node.source",
        definition_version="1.0.0",
        attempt=1,
        input_artifact_ids=(),
        created_at=at(11),
        work_dir="C:/synthetic/late-start",
    )
    with pytest.raises(RuntimeConflictError, match="E_RUN_ATTEMPT_TIME_INVALID"):
        repo.start_run(late_pending, (late_attempt,), started_at=at(10))
    with pytest.raises(RuntimeNotFoundError, match="E_RUN_NOT_FOUND"):
        repo.get_run(late_pending.run_id)

    run = running_run(repo, run_id_number=1)
    _, source_result = complete_automatic(
        repo,
        run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=30,
        artifact_number=40,
    )
    loaded = repo.get_run(run.run_id)
    source = max(
        (item for item in loaded.node_runs if item.node_id == "node.source"),
        key=lambda item: item.attempt,
    )
    assert source.ended_at is not None
    too_early = next_closure_attempts(
        loaded,
        source_node_id="node.source",
        first_number=830,
        created_at=source.ended_at - timedelta(microseconds=1),
    )
    latest_before = repo.list_latest()
    with pytest.raises(RuntimeConflictError, match="E_NODE_RUN_TIME_NON_MONOTONIC"):
        repo.create_rerun_attempts(
            run.run_id,
            "node.source",
            too_early,
            updated_at=at(90),
        )
    assert repo.list_node_runs(run.run_id) == loaded.node_runs
    assert repo.list_latest() == latest_before

    direct_attempt = pending_node_run(
        loaded,
        node_id="node.source",
        version="1.0.0",
        node_run_number=840,
        attempt=2,
    ).model_copy(update={"created_at": at(90)})
    with pytest.raises(RuntimeConflictError, match="E_NODE_RUN_AGGREGATE_REQUIRED"):
        repo.create_node_run(direct_attempt)
    prebound = direct_attempt.model_copy(
        update={
            "node_run_id": rid(841),
            "input_artifact_ids": (source_result.outputs[0].artifact_id,),
        }
    )
    with pytest.raises(RuntimeConflictError, match="E_NODE_RUN_AGGREGATE_REQUIRED"):
        repo.create_node_run(prebound)


def test_create_node_run_cannot_inject_node_outside_selected_closure(
    tmp_path: Path,
) -> None:
    store, repo = repository(tmp_path)
    snapshot = store.load()
    pending_run = Run.pending(
        run_id=rid(850),
        project_id=snapshot.project.project_id,
        graph_snapshot=snapshot.project.graph,
        definitions_snapshot=snapshot.definitions,
        selected_targets=("node.source",),
        created_at=at(1),
    )
    source = NodeRun.pending(
        node_run_id=rid(851),
        run_id=pending_run.run_id,
        node_id="node.source",
        definition_version="1.0.0",
        attempt=1,
        input_artifact_ids=(),
        created_at=at(2),
        work_dir="C:/synthetic/selected",
    )
    run = repo.start_run(pending_run, (source,), started_at=at(3))
    injected = NodeRun.pending(
        node_run_id=rid(852),
        run_id=run.run_id,
        node_id="node.transform",
        definition_version="2.0.0",
        attempt=1,
        input_artifact_ids=(),
        created_at=at(4),
        work_dir="C:/synthetic/injected",
    )
    with pytest.raises(RuntimeConflictError, match="E_NODE_RUN_OUTSIDE_SELECTION"):
        repo.create_node_run(injected)


def test_prestart_failure_is_atomic_and_can_be_rerun(tmp_path: Path) -> None:
    _, repo = repository(tmp_path)
    run = running_run(repo)
    transform = next(item for item in run.node_runs if item.node_id == "node.transform")
    failure = RuntimeFailure(
        reason=FailureReason.EXECUTION_ERROR,
        message="合成 handoff 准备失败",
    )
    with pytest.raises(RuntimeConflictError, match="E_NODE_RUN_INPUT_SOURCE_NOT_COMPLETED"):
        repo.fail_node_run_before_start(
            transform.node_run_id,
            failed_at=at(20),
            error=failure,
        )
    _, source_result = complete_automatic(
        repo,
        run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=861,
        artifact_number=862,
    )
    transform = repo.bind_inputs(transform.node_run_id, (source_result.outputs[0].artifact_id,))
    failed = repo.fail_node_run_before_start(
        transform.node_run_id,
        failed_at=at(82),
        error=failure,
    )
    assert failed.state is NodeRunState.FAILED
    assert failed.started_at == failed.ended_at == at(82)

    loaded = repo.get_run(run.run_id)
    candidates = next_closure_attempts(
        loaded,
        source_node_id="node.transform",
        first_number=860,
        created_at=at(83),
    )
    created = repo.create_rerun_attempts(
        run.run_id,
        "node.transform",
        candidates,
        updated_at=at(83),
    )
    assert tuple(item.attempt for item in created) == (2, 2)


def test_reuse_rejects_attempt_two_and_results_completed_after_run_creation(
    tmp_path: Path,
) -> None:
    _, repo = repository(tmp_path)
    first_run = running_run(repo, run_id_number=1)
    _, historical = complete_automatic(
        repo,
        first_run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=30,
        artifact_number=40,
    )
    current_run = running_run(repo, run_id_number=2, created_at=at(100))
    current_source = next(item for item in current_run.node_runs if item.node_id == "node.source")
    first_attempt = repo.reuse_result(
        current_source.node_run_id,
        historical.result_id,
        completed_at=at(102),
    )
    assert first_attempt.state is NodeRunState.COMPLETED
    loaded = repo.get_run(current_run.run_id)
    candidates = next_closure_attempts(
        loaded,
        source_node_id="node.source",
        first_number=900,
        created_at=at(103),
    )
    created = repo.create_rerun_attempts(
        current_run.run_id,
        "node.source",
        candidates,
        updated_at=at(103),
    )
    source_attempt_two = next(item for item in created if item.node_id == "node.source")
    with pytest.raises(RuntimeConflictError, match="E_REUSE_BINDING_MISMATCH"):
        repo.reuse_result(
            source_attempt_two.node_run_id,
            historical.result_id,
            completed_at=at(104),
        )
    assert repo.get_node_run(source_attempt_two.node_run_id).state is NodeRunState.PENDING

    future_run = running_run(repo, run_id_number=3, created_at=at(105))
    _, future_result = complete_automatic(
        repo,
        future_run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=21,
        result_number=31,
        artifact_number=41,
    )
    separate_run = running_run(repo, run_id_number=4, created_at=at(100))
    separate_source = next(item for item in separate_run.node_runs if item.node_id == "node.source")
    with pytest.raises(RuntimeConflictError, match="E_REUSE_BINDING_MISMATCH"):
        repo.reuse_result(
            separate_source.node_run_id,
            future_result.result_id,
            completed_at=at(120),
        )
    assert repo.get_node_run(separate_source.node_run_id).state is NodeRunState.PENDING


def test_reused_relation_snapshot_tampering_fails_closed(tmp_path: Path) -> None:
    store, repo = repository(tmp_path)
    first_run = running_run(repo, run_id_number=1)
    _, source_result = complete_automatic(
        repo,
        first_run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=30,
        artifact_number=40,
    )
    _, strength_seven = complete_manual(
        repo,
        first_run,
        node_run_number=21,
        result_number=31,
        artifact_number=41,
        input_id=source_result.outputs[0].artifact_id,
    )

    store.save(project(strength=3), definitions())
    second_run = running_run(repo, run_id_number=2, created_at=at(100))
    second_source = next(item for item in second_run.node_runs if item.node_id == "node.source")
    repo.reuse_result(second_source.node_run_id, source_result.result_id, completed_at=at(102))
    _, strength_three = complete_manual(
        repo,
        second_run,
        node_run_number=22,
        result_number=32,
        artifact_number=42,
        input_id=source_result.outputs[0].artifact_id,
    )

    store.save(project(strength=7), definitions())
    third_run = running_run(repo, run_id_number=3, created_at=at(200))
    third_source = next(item for item in third_run.node_runs if item.node_id == "node.source")
    repo.reuse_result(third_source.node_run_id, source_result.result_id, completed_at=at(202))
    third_transform = next(item for item in third_run.node_runs if item.node_id == "node.transform")
    third_transform = repo.bind_inputs(
        third_transform.node_run_id, (source_result.outputs[0].artifact_id,)
    )
    reused = repo.reuse_result(
        third_transform.node_run_id,
        strength_seven.result_id,
        completed_at=at(203),
    )
    assert reused.reused_from_result_id == strength_seven.result_id

    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE node_runs SET reused_from_result_id = ?, "
            "output_artifact_ids_json = ? WHERE node_run_id = ?",
            (
                strength_three.result_id,
                _dump_test_json((strength_three.outputs[0].artifact_id,)),
                reused.node_run_id,
            ),
        )
    with pytest.raises(RuntimeDataError, match="E_NODE_RUN_RESULT_RELATION_CORRUPT"):
        repo.get_node_run(reused.node_run_id)


def test_corrupt_latest_head_is_validated_before_any_stale_write(
    tmp_path: Path,
) -> None:
    store, repo = repository(tmp_path)
    run = running_run(repo)
    _, source_result = complete_automatic(
        repo,
        run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=30,
        artifact_number=40,
    )
    complete_manual(
        repo,
        run,
        node_run_number=21,
        result_number=31,
        artifact_number=41,
        input_id=source_result.outputs[0].artifact_id,
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE latest_results SET result_id = ? WHERE node_id = 'node.transform'",
            (source_result.result_id,),
        )
        before = connection.execute(
            "SELECT node_id, result_id, stale, stale_reason, updated_at "
            "FROM latest_results ORDER BY rowid"
        ).fetchall()

    with pytest.raises(RuntimeDataError, match="E_LATEST_RESULT_RELATION_CORRUPT"):
        repo.mark_latest_stale(
            ("node.transform", "node.source"),
            StaleReason.RERUN_REQUESTED,
            updated_at=at(100),
        )
    with sqlite3.connect(store.path) as connection:
        after = connection.execute(
            "SELECT node_id, result_id, stale, stale_reason, updated_at "
            "FROM latest_results ORDER BY rowid"
        ).fetchall()
    assert after == before


def test_run_and_attempt_time_relation_tampering_fails_closed(tmp_path: Path) -> None:
    store, repo = repository(tmp_path)
    run = running_run(repo)
    complete_automatic(
        repo,
        run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=30,
        artifact_number=40,
    )
    loaded = repo.get_run(run.run_id)
    candidates = next_closure_attempts(
        loaded,
        source_node_id="node.source",
        first_number=950,
        created_at=at(90),
    )
    created = repo.create_rerun_attempts(
        run.run_id,
        "node.source",
        candidates,
        updated_at=at(90),
    )
    source_two = next(item for item in created if item.node_id == "node.source")
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE node_runs SET created_at = ? WHERE node_run_id = ?",
            (at(1).isoformat().replace("+00:00", "Z"), source_two.node_run_id),
        )
    with pytest.raises(RuntimeDataError, match="E_NODE_RUN_TIME_RELATION_CORRUPT"):
        repo.get_run(run.run_id)


def test_latest_projection_never_regresses_for_late_completion_or_stale_time(
    tmp_path: Path,
) -> None:
    store, repo = repository(tmp_path)
    late_run = running_run(repo, run_id_number=1)
    late_source = next(item for item in late_run.node_runs if item.node_id == "node.source")
    repo.transition_node_run(
        late_source.node_run_id,
        NodeRunState.RUNNING,
        occurred_at=at(50),
    )

    fresh_run = running_run(repo, run_id_number=2)
    _, _fresh_result = complete_automatic(
        repo,
        fresh_run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=21,
        result_number=970,
        artifact_number=971,
    )
    fresh_head = repo.get_latest("node.source")
    assert fresh_head is not None

    late_result = NodeResult(
        result_id=rid(972),
        node_run_id=late_source.node_run_id,
        outputs=(
            Artifact(
                artifact_id=rid(973),
                kind="VideoFile",
                path="C:/synthetic/late-completion.mkv",
                producer_node_run_id=late_source.node_run_id,
                producer_port_id="video",
            ),
        ),
        created_at=at(70),
    )
    repo.register_result(late_result, ended_at=at(80))
    assert repo.get_result(late_result.result_id) == late_result
    assert repo.get_latest("node.source") == fresh_head

    with pytest.raises(RuntimeConflictError, match="E_LATEST_TIME_REGRESSION"):
        repo.mark_latest_stale(
            ("node.source",),
            StaleReason.RERUN_REQUESTED,
            updated_at=fresh_head.updated_at - timedelta(microseconds=1),
        )
    assert repo.get_latest("node.source") == fresh_head

    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE latest_results SET updated_at = ? WHERE node_id = 'node.source'",
            (at(1).isoformat().replace("+00:00", "Z"),),
        )
    with pytest.raises(RuntimeDataError, match="E_LATEST_RESULT_RELATION_CORRUPT"):
        repo.get_latest("node.source")


def test_python_manual_and_reused_exit_code_tampering_fails_closed(
    tmp_path: Path,
) -> None:
    store, repo = repository(tmp_path)
    first_run = running_run(repo, run_id_number=1)
    source, source_result = complete_automatic(
        repo,
        first_run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=980,
        artifact_number=981,
    )
    manual, _ = complete_manual(
        repo,
        first_run,
        node_run_number=21,
        result_number=982,
        artifact_number=983,
        input_id=source_result.outputs[0].artifact_id,
    )
    second_run = running_run(repo, run_id_number=2, created_at=at(100))
    second_source = next(item for item in second_run.node_runs if item.node_id == "node.source")
    reused = repo.reuse_result(
        second_source.node_run_id,
        source_result.result_id,
        completed_at=at(102),
    )

    for node_run in (source, manual, reused):
        with sqlite3.connect(store.path) as connection:
            connection.execute(
                "UPDATE node_runs SET exit_code = 0 WHERE node_run_id = ?",
                (node_run.node_run_id,),
            )
        with pytest.raises(RuntimeDataError, match="E_NODE_RUN_EXIT_CODE_RELATION_CORRUPT"):
            repo.get_node_run(node_run.node_run_id)
        with sqlite3.connect(store.path) as connection:
            connection.execute(
                "UPDATE node_runs SET exit_code = NULL WHERE node_run_id = ?",
                (node_run.node_run_id,),
            )
        assert repo.get_node_run(node_run.node_run_id).state is NodeRunState.COMPLETED


def test_reused_node_run_time_and_handoff_tampering_fails_closed(
    tmp_path: Path,
) -> None:
    store, repo = repository(tmp_path)
    first_run = running_run(repo, run_id_number=1)
    _, source_result = complete_automatic(
        repo,
        first_run,
        node_id="node.source",
        version="1.0.0",
        node_run_number=20,
        result_number=990,
        artifact_number=991,
    )
    _, transform_result = complete_manual(
        repo,
        first_run,
        node_run_number=21,
        result_number=992,
        artifact_number=993,
        input_id=source_result.outputs[0].artifact_id,
    )
    second_run = running_run(repo, run_id_number=2, created_at=at(100))
    second_source = next(item for item in second_run.node_runs if item.node_id == "node.source")
    repo.reuse_result(second_source.node_run_id, source_result.result_id, completed_at=at(102))
    second_transform = next(
        item for item in second_run.node_runs if item.node_id == "node.transform"
    )
    second_transform = repo.bind_inputs(
        second_transform.node_run_id, (source_result.outputs[0].artifact_id,)
    )
    reused = repo.reuse_result(
        second_transform.node_run_id,
        transform_result.result_id,
        completed_at=at(103),
    )
    assert reused.started_at == reused.ended_at == at(103)

    forged_handoff = ExternalHandoff(
        handoff_id=rid(994),
        node_run_id=reused.node_run_id,
        input_artifact_ids=reused.input_artifact_ids,
        output_targets=(
            ExternalOutputTarget(
                port_id="video_out",
                path=f"{reused.work_dir}/forged.mkv",
            ),
        ),
        instructions="使用合成外部工具",
        created_at=at(103),
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE node_runs SET external_handoff_json = ? WHERE node_run_id = ?",
            (forged_handoff.model_dump_json(), reused.node_run_id),
        )
    with pytest.raises(RuntimeDataError, match="E_NODE_RUN_RESULT_RELATION_CORRUPT"):
        repo.get_node_run(reused.node_run_id)

    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE node_runs SET external_handoff_json = NULL, started_at = ? "
            "WHERE node_run_id = ?",
            (at(102).isoformat().replace("+00:00", "Z"), reused.node_run_id),
        )
    with pytest.raises(RuntimeDataError, match="E_NODE_RUN_RESULT_RELATION_CORRUPT"):
        repo.get_node_run(reused.node_run_id)


@pytest.mark.parametrize(
    "second_work_dir",
    (
        "C:/synthetic/work-dir-alias/../work-dir-root",
        "C:/synthetic/work-dir-root/nested-attempt",
    ),
)
def test_start_run_rejects_aliased_or_nested_attempt_directories_atomically(
    tmp_path: Path,
    second_work_dir: str,
) -> None:
    store, repo = repository(tmp_path)
    snapshot = store.load()
    pending = Run.pending(
        run_id=rid(1100),
        project_id=snapshot.project.project_id,
        graph_snapshot=snapshot.project.graph,
        definitions_snapshot=snapshot.definitions,
        created_at=at(1),
    )
    candidates = list(closure_attempts(pending, attempt=1, first_number=1101, created_at=at(2)))
    candidates[0] = candidates[0].model_copy(update={"work_dir": "C:/synthetic/work-dir-root"})
    candidates[1] = candidates[1].model_copy(update={"work_dir": second_work_dir})

    with pytest.raises(RuntimeConflictError, match="E_NODE_RUN_WORK_DIR_CONFLICT"):
        repo.start_run(pending, tuple(candidates), started_at=at(3))

    with pytest.raises(RuntimeNotFoundError, match="E_RUN_NOT_FOUND"):
        repo.get_run(pending.run_id)
    with pytest.raises(RuntimeNotFoundError, match="E_NODE_RUN_NOT_FOUND"):
        repo.get_node_run(candidates[0].node_run_id)


def test_start_run_rejects_cross_run_work_dir_alias_without_touching_existing_run(
    tmp_path: Path,
) -> None:
    store, repo = repository(tmp_path)
    existing_run = running_run(repo)
    existing_source = next(item for item in existing_run.node_runs if item.node_id == "node.source")
    snapshot = store.load()
    pending = Run.pending(
        run_id=rid(1110),
        project_id=snapshot.project.project_id,
        graph_snapshot=snapshot.project.graph,
        definitions_snapshot=snapshot.definitions,
        created_at=at(20),
    )
    candidates = list(closure_attempts(pending, attempt=1, first_number=1111, created_at=at(21)))
    candidates[0] = candidates[0].model_copy(
        update={"work_dir": "C:/synthetic/cross-run-alias/../initial-1-0"}
    )

    with pytest.raises(RuntimeConflictError, match="E_NODE_RUN_WORK_DIR_CONFLICT"):
        repo.start_run(pending, tuple(candidates), started_at=at(22))

    assert repo.get_run(existing_run.run_id) == existing_run
    assert repo.get_node_run(existing_source.node_run_id) == existing_source
    with pytest.raises(RuntimeNotFoundError, match="E_RUN_NOT_FOUND"):
        repo.get_run(pending.run_id)


def test_rerun_and_direct_create_reject_work_dir_overlap_without_partial_writes(
    tmp_path: Path,
) -> None:
    _, repo = repository(tmp_path)
    run = running_run(repo)
    existing_source = next(item for item in run.node_runs if item.node_id == "node.source")
    before = repo.list_node_runs(run.run_id)
    candidates = list(
        next_closure_attempts(
            run,
            source_node_id="node.source",
            first_number=1120,
            created_at=at(20),
        )
    )
    candidates[1] = candidates[1].model_copy(
        update={"work_dir": f"{existing_source.work_dir}/nested-rerun"}
    )

    with pytest.raises(RuntimeConflictError, match="E_NODE_RUN_WORK_DIR_CONFLICT"):
        repo.create_rerun_attempts(
            run.run_id,
            "node.source",
            tuple(candidates),
            updated_at=at(20),
        )
    assert repo.list_node_runs(run.run_id) == before

    direct = NodeRun.pending(
        node_run_id=rid(1130),
        run_id=run.run_id,
        node_id="node.source",
        definition_version="1.0.0",
        attempt=1,
        input_artifact_ids=(),
        created_at=at(21),
        work_dir=f"{existing_source.work_dir}/direct-child",
    )
    with pytest.raises(RuntimeConflictError, match="E_NODE_RUN_WORK_DIR_CONFLICT"):
        repo.create_node_run(direct)
    with pytest.raises(RuntimeNotFoundError, match="E_NODE_RUN_NOT_FOUND"):
        repo.get_node_run(direct.node_run_id)


def test_public_reads_fail_closed_when_work_directories_are_sql_tampered_to_overlap(
    tmp_path: Path,
) -> None:
    store, repo = repository(tmp_path)
    run = running_run(repo)
    source = next(item for item in run.node_runs if item.node_id == "node.source")
    transform = next(item for item in run.node_runs if item.node_id == "node.transform")
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE node_runs SET work_dir = ? WHERE node_run_id = ?",
            (f"{source.work_dir}/tampered-child", transform.node_run_id),
        )

    with pytest.raises(RuntimeDataError, match="E_NODE_RUN_WORK_DIR_RELATION_CORRUPT"):
        repo.get_run(run.run_id)
    with pytest.raises(RuntimeDataError, match="E_NODE_RUN_WORK_DIR_RELATION_CORRUPT"):
        repo.get_node_run(source.node_run_id)
