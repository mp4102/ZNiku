"""以真实严格 Run 模型验证显式工作源恢复的窄退休门禁，不访问媒体或数据库。"""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest

from zniku.graph import ValidatorSpec
from zniku.project_service.work_recovery import (
    can_retire_replaced_work_run,
    retire_replaced_work_run,
)
from zniku.runtime import ExternalHandoff, NodeRun, NodeRunState, Run, RunState, RuntimeService
from zniku.runtime.models import FailureReason, RuntimeFailure
from zniku.source_color.definitions import source_preparation_definitions as color_definitions
from zniku.source_color.template import build_preparation_graph as color_graph
from zniku.source_preparation.work_definitions import source_preparation_definitions
from zniku.source_preparation.work_template import build_preparation_graph


def _run(
    *,
    replacement: bool = False,
    old: bool = False,
    active: NodeRunState | None = None,
    forged: bool = False,
) -> Run:
    graph = (
        color_graph("D:/synthetic/not-read.mkv", route="external", target_frame_rate="30/1")
        if old
        else build_preparation_graph(
            "D:/synthetic/not-read.mkv",
            route="external",
            target_frame_rate="30/1",
            reference_change_confirmed=True,
        )
    )
    definitions = color_definitions() if old else source_preparation_definitions()
    if forged:
        definitions = tuple(
            d.model_copy(update={"validator": ValidatorSpec(adapter="tests:untrusted")})
            if d.type_id.endswith("admission")
            else d
            for d in definitions
        )
    now, run_id = datetime.now(UTC), str(uuid4())
    attempts = []
    for node in graph.nodes:
        state = (
            NodeRunState.PENDING
            if replacement
            else NodeRunState.FAILED
            if node.node_id.endswith("admission")
            else NodeRunState.COMPLETED
        )
        if active is not None and node.node_id.endswith("prepare"):
            state = active
        identity = str(uuid4())
        attempts.append(
            NodeRun(
                node_run_id=identity,
                run_id=run_id,
                node_id=node.node_id,
                definition_version=node.definition_version,
                attempt=1,
                state=state,
                created_at=now,
                work_dir="D:/synthetic/not-created",
                started_at=None if state is NodeRunState.PENDING else now,
                ended_at=now if state in {NodeRunState.COMPLETED, NodeRunState.FAILED} else None,
                error=RuntimeFailure(reason=FailureReason.VALIDATION_FAILED, message="合成准入失败")
                if state is NodeRunState.FAILED
                else None,
                external_handoff=ExternalHandoff(
                    handoff_id=str(uuid4()), node_run_id=identity, created_at=now
                )
                if state is NodeRunState.WAITING_EXTERNAL
                else None,
            )
        )
    return Run(
        run_id=run_id,
        project_id="synthetic",
        graph_snapshot=graph,
        definitions_snapshot=definitions,
        state=RunState.RUNNING,
        node_runs=tuple(attempts),
        created_at=now,
        started_at=now,
    )


def test_only_created_replacement_of_inactive_failed_work_can_retire() -> None:
    previous, replacement = _run(), _run(replacement=True)
    assert can_retire_replaced_work_run(previous, replacement, "source-preparation-admission")
    assert not can_retire_replaced_work_run(previous, previous, "source-preparation-admission")
    assert not can_retire_replaced_work_run(previous, replacement, "source-preparation-source")
    assert not can_retire_replaced_work_run(previous, replacement, "unknown")
    assert not can_retire_replaced_work_run(
        previous,
        replacement.model_copy(update={"project_id": "another"}),
        "source-preparation-admission",
    )
    assert not can_retire_replaced_work_run(
        previous, replacement.model_copy(update={"node_runs": ()}), "source-preparation-admission"
    )


@pytest.mark.parametrize("state", [NodeRunState.RUNNING, NodeRunState.WAITING_EXTERNAL])
def test_active_process_or_external_handoff_cannot_be_retired(state: NodeRunState) -> None:
    assert not can_retire_replaced_work_run(
        _run(active=state), _run(replacement=True), "source-preparation-admission"
    )


@pytest.mark.parametrize("old,forged", [(True, False), (False, True)])
def test_legacy_or_same_named_forged_definition_has_no_retirement_side_effect(
    old: bool, forged: bool
) -> None:
    previous, replacement = _run(old=old, forged=forged), _run(replacement=True, old=old)
    assert not can_retire_replaced_work_run(previous, replacement, "source-preparation-admission")
    # 旧线纯判定后返回，连额外仓库查询都不能依赖；更不可能调用 abandon。
    retire_replaced_work_run(
        cast(RuntimeService, object()), previous, replacement, "source-preparation-admission"
    )


def test_rechecks_current_persistent_state_and_does_not_swallow_abandon_failure() -> None:
    previous, replacement = _run(), _run(replacement=True)
    calls: list[str] = []
    states = {previous.run_id: previous, replacement.run_id: replacement}
    runtime = SimpleNamespace(
        repository=SimpleNamespace(get_run=states.__getitem__), abandon_run=calls.append
    )
    retire_replaced_work_run(
        cast(RuntimeService, runtime), previous, replacement, "source-preparation-admission"
    )
    assert calls == [previous.run_id]
    calls.clear()
    current = _run(active=NodeRunState.WAITING_EXTERNAL)
    states[previous.run_id] = current
    retire_replaced_work_run(
        cast(RuntimeService, runtime), previous, replacement, "source-preparation-admission"
    )
    assert not calls
    states[previous.run_id] = previous

    def fail(identity: str) -> None:
        raise RuntimeError("受控放弃失败")

    runtime.abandon_run = fail
    with pytest.raises(RuntimeError, match="受控放弃失败"):
        retire_replaced_work_run(
            cast(RuntimeService, runtime), previous, replacement, "source-preparation-admission"
        )
