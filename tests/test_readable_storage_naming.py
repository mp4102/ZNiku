"""合成合同验证业务目录提示；目录名不猜测媒体身份，也不改变 Graph 或 Runtime。"""

from pathlib import Path

import pytest

from test_overlap_template_service import _request_data
from zniku.chapter_overlap.definitions import definition_role
from zniku.graph import ExecutionMode, Graph, NodeDefinition, NodeInstance, PythonExecutorSpec
from zniku.project import ProjectStore
from zniku.project_service.overlap_application import full_overlap
from zniku.project_service.storage_naming import resolve_attempt_naming
from zniku.runtime import Run


def _run(tmp_path: Path) -> Run:
    application, request, path = _request_data(tmp_path)
    full_overlap(application, request, expand=True)
    snapshot = ProjectStore.open(path).load()
    return Run.pending(
        project_id=snapshot.project.project_id,
        graph_snapshot=snapshot.project.graph,
        definitions_snapshot=snapshot.definitions,
    )


def test_all_overlap_roles_use_bound_chapters_without_mutating_graph(tmp_path: Path) -> None:
    run = _run(tmp_path)
    definitions = {item.type_id: item for item in run.definitions_snapshot}
    seen: set[tuple[str, str | None]] = set()
    for node in run.graph_snapshot.nodes:
        definition = definitions[node.type_id]
        role = definition_role(definition)
        if role is None:
            continue
        before = run.model_dump_json()
        hint = resolve_attempt_naming(run, node, definition)
        assert run.model_dump_json() == before
        seen.add((role, hint.chapter_name))
        if role == "split":
            assert hint.category == "common" and hint.task_name == "分章分叶"
        elif role in {"program", "final"}:
            assert hint.category == "program"
        else:
            chapter = node.model_dump(mode="json")["parameters"]["chapter"]
            assert hint.category == "chapters"
            assert hint.chapter_index == chapter["ordinal"] + 1
            assert hint.chapter_name == "ABC"[chapter["ordinal"]]
            if role == "enhancement":
                assert hint.task_name == "逐叶增强-leaf-0001"
    assert {("fi", "A"), ("fi", "B"), ("fi", "C"), ("split", None)} <= seen


@pytest.mark.parametrize("change", ["identity", "definition", "parameters", "cross_field"])
def test_unreliable_business_hints_fall_back_to_custom(tmp_path: Path, change: str) -> None:
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
    hint = resolve_attempt_naming(run, node, definition)
    assert hint.category == "custom"
    assert hint.chapter_name is None and hint.chapter_index is None


def test_arbitrary_custom_node_id_is_never_interpreted_as_chapter() -> None:
    definition = NodeDefinition(
        type_id="user.effects.my_filter",
        version="1.0.0",
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="synthetic:unused"),
    )
    node = NodeInstance(
        node_id="overlap.fi.A",
        type_id=definition.type_id,
        definition_version=definition.version,
    )
    run = Run.pending(
        project_id="synthetic",
        graph_snapshot=Graph(nodes=(node,)),
        definitions_snapshot=(definition,),
    )
    hint = resolve_attempt_naming(run, node, definition)
    assert hint.category == "custom" and hint.task_name == "my_filter"
