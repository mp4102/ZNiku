"""纯合成英文目录提示：精确定义、章节与旧布局隔离，不触发媒体 I/O 或修改图。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from test_chapter_batch_contracts import batch_pipeline
from test_readable_storage_naming import _run
from test_source_aligned_node_contracts import pipeline
from zniku.avenhance_v27 import definitions as av27
from zniku.chapter_batch import definitions as batch
from zniku.chapter_overlap.definitions import definition_role
from zniku.graph import ExecutionMode, Graph, NodeDefinition, NodeInstance, PythonExecutorSpec
from zniku.project_service.storage_naming import (
    resolve_attempt_naming,
    resolve_english_attempt_naming,
)
from zniku.runtime import Run
from zniku.source_admission import definitions as admitted

NAMES = {
    "split": "chapter-split",
    "enhancement": "enhancement",
    "merge": "chapter-merge",
    "context": "fi-context",
    "fi": "frame-interpolation",
    "crop": "fi-crop",
    "program": "video-encode",
    "final": "audio-mux",
}


def _single_run(
    definition: NodeDefinition, node: NodeInstance | None = None
) -> tuple[Run, NodeInstance]:
    """使用空 Run 测试不依赖图关系的提示；孤立 node 不加入运行图或执行。"""
    node = node or NodeInstance(
        node_id="synthetic-node",
        type_id=definition.type_id,
        definition_version=definition.version,
    )
    return (
        Run.pending(
            project_id="synthetic-project",
            graph_snapshot=Graph(),
            definitions_snapshot=(),
        ),
        node,
    )


def test_overlap_english_hints_preserve_media_parameters_and_old_names(tmp_path: Path) -> None:
    run = _run(tmp_path)
    before = run.model_dump_json()
    definitions = {item.type_id: item for item in run.definitions_snapshot}
    roles: set[str] = set()
    for node in run.graph_snapshot.nodes:
        definition = definitions[node.type_id]
        role = definition_role(definition)
        if role is None:
            continue
        roles.add(role)
        old = resolve_attempt_naming(run, node, definition)
        english = resolve_english_attempt_naming(run, node, definition)
        assert english.task_name == NAMES[role] + ("-leaf-0001" if role == "enhancement" else "")
        assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", english.task_name)
        assert (english.chapter_index, english.chapter_name, english.category) == (
            old.chapter_index,
            old.chapter_name,
            old.category,
        )
        if role == "split":
            assert old.task_name == "分章分叶"
        elif role == "enhancement":
            assert old.task_name == "逐叶增强-leaf-0001"
    assert roles == set(NAMES)
    assert run.model_dump_json() == before


def test_batch_three_chapters_share_enhancement_name_and_chapter_binding(tmp_path: Path) -> None:
    enhanced: list[tuple[str, str | None]] = []
    for step in batch_pipeline(tmp_path):
        count = len(step.contract.outputs) if step.role == "enhancement" else 1
        definition = batch.definition(step.role, count)
        node = NodeInstance(
            node_id="synthetic-node",
            type_id=definition.type_id,
            definition_version=definition.version,
            parameters=step.parameters,
        )
        run, node = _single_run(definition, node)
        before = run.model_dump_json()
        node_before = node.model_dump_json()
        hint = resolve_english_attempt_naming(run, node, definition)
        assert hint.task_name == NAMES[step.role]
        assert run.model_dump_json() == before
        assert node.model_dump_json() == node_before
        if step.role == "enhancement":
            enhanced.append((hint.task_name, hint.chapter_name))
            assert resolve_attempt_naming(run, node, definition).task_name == "章节批量增强"
    assert enhanced == [("enhancement", "A"), ("enhancement", "B"), ("enhancement", "C")]


def test_source_repair_name_has_no_port_or_node_identifiers(tmp_path: Path) -> None:
    split = pipeline(tmp_path)[0]
    definition = admitted.external_definition("mp4")
    node = NodeInstance(
        node_id="repair--N022",
        type_id=definition.type_id,
        definition_version=definition.version,
        parameters={
            "source": split.parameters["source"],
            "declared_container": "mp4",
            "model_name": "synthetic",
            "operator_frame_order_confirmed": True,
        },
    )
    run, node = _single_run(definition, node)
    hint = resolve_english_attempt_naming(run, node, definition)
    assert hint.task_name == "source-repair"
    assert hint.chapter_name is None
    assert resolve_attempt_naming(run, node, definition).task_name == "外部修复"


@pytest.mark.parametrize(
    ("definition", "name"),
    [
        (admitted.source_program_definition(), "source-analysis"),
        (admitted.source_admission_definition(), "source-admission"),
        (av27.source_program_definition(), "source"),
        (av27.source_admission_definition(), "source-analysis"),
    ],
)
def test_exact_source_roles_are_english(definition: NodeDefinition, name: str) -> None:
    run, node = _single_run(definition)
    hint = resolve_english_attempt_naming(run, node, definition)
    assert hint.task_name == name and hint.chapter_name is None


@pytest.mark.parametrize("change", ["identity", "definition", "parameters", "cross_field"])
def test_unreliable_hints_use_task_without_guessing_chapters(tmp_path: Path, change: str) -> None:
    run = _run(tmp_path)
    definition = next(item for item in run.definitions_snapshot if definition_role(item) == "fi")
    node = next(item for item in run.graph_snapshot.nodes if item.type_id == definition.type_id)
    if change == "identity":
        node = node.model_copy(update={"definition_version": "1.0.0"})
    elif change == "definition":
        definition = definition.model_copy(update={"validator": None})
    elif change == "parameters":
        node = node.model_copy(update={"parameters": {}})
    else:
        parameters = node.model_dump(mode="json")["parameters"]
        parameters["chapter"]["chapter_id"] = "chapter-0002"
        node = node.model_copy(update={"parameters": parameters})
    hint = resolve_english_attempt_naming(run, node, definition)
    assert hint.task_name == "task" and hint.category == "custom"
    assert hint.chapter_name is None and hint.chapter_index is None


def test_custom_identifier_and_parameters_are_not_directory_authority() -> None:
    definition = NodeDefinition(
        type_id="user.effects.enhancement_A",
        version="1.0.0",
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="synthetic:unused"),
    )
    node = NodeInstance(
        node_id="chapter.A.enhancement",
        type_id=definition.type_id,
        definition_version=definition.version,
        parameters={"directory": "../other-folder", "chapter": "B"},
    )
    run, node = _single_run(definition, node)
    before = run.model_dump_json()
    node_before = node.model_dump_json()
    hint = resolve_english_attempt_naming(run, node, definition)
    assert hint.task_name == "task" and hint.category == "custom"
    assert hint.chapter_name is None
    assert run.model_dump_json() == before
    assert node.model_dump_json() == node_before
