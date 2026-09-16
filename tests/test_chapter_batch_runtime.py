"""以真实批量 Graph/definitions 验证章级 stale/reuse，媒体边界在此显式使用合成替身。

本测试只验证编排粒度和历史不变，不宣称字节占位文件通过真实媒体合同；真实逐叶来源与
输出媒体校验由 test_chapter_batch_contracts 和独立合成媒体端到端测试覆盖。
"""

from pathlib import Path

from test_av27_template import _binding
from test_source_admitted_service import create
from zniku.avenhance_v27.template import PublicationRequest
from zniku.chapter_batch import definitions
from zniku.chapter_batch.presentation import chapter_views
from zniku.graph import PythonExecutorSpec
from zniku.project import ProjectStore
from zniku.project.studio import GroupViewState, NodeViewState, StudioState, StudioViewport
from zniku.runtime import (
    NodeRunState,
    NodeValidatorContext,
    NodeValidatorResult,
    PythonAdapterContext,
    PythonAdapterResult,
    Run,
    RunState,
    RuntimeService,
)
from zniku.source_admission.definitions import external_definition
from zniku.source_admission.naming import publication_target
from zniku.source_aligned.template import (
    SourceAlignedBuild,
    SourceAlignedProcessing,
    build_source_aligned,
)


def _build(tmp_path: Path, *, frames: int = 26001) -> tuple[ProjectStore, SourceAlignedBuild]:
    app, _source, path = create(tmp_path)
    snapshot = app.inspect().snapshot
    assert snapshot is not None
    binding = _binding(snapshot, source_mode="program", mr_mode="off", frames=(frames,))
    build = build_source_aligned(
        snapshot,
        binding,
        SourceAlignedProcessing.model_validate(
            {
                "settings": {
                    "chapter_selector": {"mode": "average", "count": 3},
                    "leaf_max_minutes": 1,
                },
                "enhancement": {"model_name": "Synthetic"},
                "program_encode": {"encoder": "cpu"},
            }
        ),
        PublicationRequest(
            output_root=str(tmp_path), title="Synthetic", year="2026", overwrite=False
        ),
        definition_factory=definitions.definition,
        external_factory=external_definition,
        publication_target=publication_target,
        batch_enhancement=True,
    )
    store = ProjectStore.open(path)
    store.save(build.project, build.definitions)
    return store, build


def _synthetic_adapter(context: PythonAdapterContext) -> PythonAdapterResult:
    """测试替身写非媒体占位；不调用真实源、FFmpeg 或外部模型。"""
    for output in context.outputs:
        output.path.parent.mkdir(parents=True, exist_ok=True)
        output.path.write_bytes(b"synthetic-runtime-only")
    return PythonAdapterResult()


def _synthetic_validator(context: NodeValidatorContext) -> NodeValidatorResult:
    del context
    return NodeValidatorResult(passed=True, summary={"synthetic_runtime_only": True})


def _finish(service: RuntimeService, run: Run) -> Run:
    for _ in range(12):
        run = service.run_until_blocked(run.run_id)
        if run.state is RunState.COMPLETED:
            return run
        waiting = [item for item in run.node_runs if item.state is NodeRunState.WAITING_EXTERNAL]
        assert waiting, [item.error for item in run.node_runs]
        for item in waiting:
            assert item.external_handoff is not None
            for output in item.external_handoff.output_targets:
                Path(output.path).write_bytes(b"synthetic-manual-runtime-only")
            run = service.submit_external(item.node_run_id)
    raise AssertionError("合成图未在有界轮次内完成")


def test_change_one_batch_preserves_other_chapters_and_only_stales_explicit_neighbors(
    tmp_path: Path,
) -> None:
    store, build = _build(tmp_path)
    storage = store.load_storage()
    assert storage is not None
    service = RuntimeService(
        store,
        storage.attempts_root,
        python_adapters={
            definition.executor.adapter: _synthetic_adapter
            for definition in build.definitions
            if isinstance(definition.executor, PythonExecutorSpec)
        },
        validators={
            definition.validator.adapter: _synthetic_validator
            for definition in build.definitions
            if definition.validator is not None
        },
        media_probe=lambda _path, _kind: {"synthetic_runtime_only": True},
        artifact_quick_probe=lambda artifact: Path(artifact.path).is_file(),
    )
    first = _finish(service, service.create_run())
    historical = first.model_dump_json()
    before = {item.node_id: item for item in first.node_runs}
    store.save(
        build.project,
        build.definitions,
        studio_state=chapter_views(build, store.load_authoring().studio_state),
    )
    assert not any(item.stale for item in service.repository.list_latest())
    assert service.repository.get_run(first.run_id).model_dump_json() == historical
    changed_graph = build.project.graph.model_copy(
        update={
            "nodes": tuple(
                node.model_copy(update={"parameters": {**node.parameters, "model_name": "Changed"}})
                if node.node_id == "overlap.enhance.A"
                else node
                for node in build.project.graph.nodes
            )
        }
    )
    store.save(build.project.model_copy(update={"graph": changed_graph}), build.definitions)
    expected_stale = {
        "overlap.enhance.A",
        "overlap.merge.A",
        "overlap.context.A",
        "overlap.fi.A",
        "overlap.crop.A",
        "overlap.context.B",
        "overlap.fi.B",
        "overlap.crop.B",
        "overlap.program",
        "overlap.final",
        "output",
    }
    assert {
        item.node_id for item in service.repository.list_latest() if item.stale
    } == expected_stale
    assert service.repository.get_run(first.run_id).model_dump_json() == historical

    second = service.run_until_blocked(service.create_run().run_id)
    after = {item.node_id: item for item in second.node_runs}
    for node_id in before:
        if node_id in expected_stale:
            assert after[node_id].reused_from_result_id is None
            assert after[node_id].output_artifact_ids == ()
        else:
            assert after[node_id].reused_from_result_id is not None
            assert after[node_id].output_artifact_ids == before[node_id].output_artifact_ids
    handoff = after["overlap.enhance.A"].external_handoff
    assert handoff is not None and len(handoff.output_targets) == 5
    assert service.repository.get_run(first.run_id).model_dump_json() == historical


def test_batch_layout_uses_chapter_spacing_not_leaf_count(tmp_path: Path) -> None:
    _store, build = _build(tmp_path)
    nodes = {node.node_id: node for node in build.project.graph.nodes}
    positions = [nodes[f"overlap.enhance.{chapter}"].ui_position for chapter in "ABC"]
    assert all(position is not None for position in positions)
    assert [position.y for position in positions if position is not None] == [80, 600, 1120]


def test_batch_layout_grows_with_port_rows_without_per_leaf_node_spacing(tmp_path: Path) -> None:
    _store, build = _build(tmp_path, frames=81001)
    nodes = {node.node_id: node for node in build.project.graph.nodes}
    first, second = nodes["overlap.enhance.A"], nodes["overlap.enhance.B"]
    assert first.ui_position is not None and second.ui_position is not None
    assert len(first.parameters["leaves"]) == 16  # type: ignore[arg-type]
    assert 1000 <= second.ui_position.y - first.ui_position.y < 1200


def test_chapter_aliases_save_reopen_and_preserve_user_views_without_graph_changes(
    tmp_path: Path,
) -> None:
    store, build = _build(tmp_path)
    before = build.project.graph.model_dump_json()
    original = StudioState(
        viewport=StudioViewport(x=12.0, y=34.0, zoom=0.7),
        groups=(GroupViewState(group_id="custom", title="我的分组"),),
        node_views=(
            NodeViewState(
                node_id="overlap.enhance.B",
                display_name="我的 B 章处理",
                collapsed=True,
                group_id="custom",
            ),
        ),
    )
    studio = chapter_views(build, original)
    names = {view.node_id: view.display_name for view in studio.node_views}
    assert names["overlap.enhance.A"] == "A 章 · 批量增强 (5 份)"
    assert names["overlap.enhance.B"] == "我的 B 章处理"
    assert names["overlap.enhance.C"] == "C 章 · 批量增强 (5 份)"
    for role in ("merge", "context", "fi", "crop"):
        assert names[f"overlap.{role}.A"].startswith("A 章")  # type: ignore[union-attr]
    assert studio.viewport == original.viewport and studio.groups == original.groups
    assert (
        next(view for view in studio.node_views if view.node_id == "overlap.enhance.B")
        == original.node_views[0]
    )
    store.save(build.project, build.definitions, studio_state=studio)
    reopened = ProjectStore.open(store.path).load_authoring()
    assert reopened.studio_state == studio
    assert reopened.snapshot.project.graph == build.project.graph
    assert build.project.graph.model_dump_json() == before
    assert chapter_views(build, studio) == studio
