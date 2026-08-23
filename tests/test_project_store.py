"""验证 0.2.0 SQLite ``.zniku`` Project Store 的权威与失败关闭边界。

全部对象和文件均为测试临时目录中的纯合成数据；测试不访问真实媒体、旧 snapshot、Runtime、Run、
Artifact、Evidence 或日志。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from zniku.graph import (
    Edge,
    ExecutionMode,
    Graph,
    NodeDefinition,
    NodeInstance,
    PortSpec,
    PythonExecutorSpec,
    UiPosition,
)
from zniku.project import (
    PROJECT_APPLICATION_ID,
    PROJECT_SCHEMA_VERSION,
    Project,
    ProjectFormatError,
    ProjectSnapshot,
    ProjectStore,
    ProjectStoreError,
    ProjectValidationError,
)


def definitions() -> tuple[NodeDefinition, ...]:
    """构造顺序刻意不按 type_id 排列的精确版本目录。"""

    transform = NodeDefinition(
        type_id="test.VideoTransform",
        version="2.3.0",
        input_ports=(PortSpec(port_id="video_in", data_type="VideoFile", required=True),),
        output_ports=(PortSpec(port_id="video_out", data_type="VideoFile"),),
        parameter_schema={
            "type": "object",
            "properties": {"strength": {"type": "integer", "minimum": 1, "maximum": 10}},
            "required": ["strength"],
            "additionalProperties": False,
        },
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="tests.synthetic:transform"),
    )
    source = NodeDefinition(
        type_id="test.Source",
        version="1.0.0",
        output_ports=(PortSpec(port_id="video", data_type="VideoFile"),),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="tests.synthetic:source"),
    )
    output = NodeDefinition(
        type_id="test.Output",
        version="1.1.0",
        input_ports=(PortSpec(port_id="input", data_type="VideoFile", required=True),),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="tests.synthetic:output"),
    )
    return transform, source, output


def valid_project(*, name: str = "合成工程", strength: int = 7) -> Project:
    """构造带非字典序 node/edge 顺序、参数和 Studio 坐标的合法 DAG。"""

    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="node.transform",
                type_id="test.VideoTransform",
                definition_version="2.3.0",
                parameters={"strength": strength},
                ui_position=UiPosition(x=320.25, y=-18.5),
            ),
            NodeInstance(
                node_id="node.source",
                type_id="test.Source",
                definition_version="1.0.0",
                ui_position=UiPosition(x=12.0, y=40.0),
            ),
            NodeInstance(
                node_id="node.output",
                type_id="test.Output",
                definition_version="1.1.0",
                ui_position=None,
            ),
        ),
        edges=(
            Edge(
                source_node_id="node.transform",
                source_port_id="video_out",
                target_node_id="node.output",
                target_port_id="input",
            ),
            Edge(
                source_node_id="node.source",
                source_port_id="video",
                target_node_id="node.transform",
                target_port_id="video_in",
            ),
        ),
    )
    return Project(project_id="project.synthetic", name=name, graph=graph)


def saved_store(path: Path) -> ProjectStore:
    return ProjectStore.create(path, valid_project(), definitions())


def test_create_uses_sqlite_header_and_phase_2_project_runtime_tables(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.zniku"
    store = saved_store(path)

    assert path.read_bytes().startswith(b"SQLite format 3\x00")
    assert store.path == path
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA application_id").fetchone()[0] == PROJECT_APPLICATION_ID
        assert connection.execute("PRAGMA user_version").fetchone()[0] == PROJECT_SCHEMA_VERSION
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert tables == {
        "project",
        "node_definitions",
        "graph_nodes",
        "graph_edges",
        "runs",
        "node_runs",
        "artifacts",
        "node_results",
        "latest_results",
    }
    assert not path.with_suffix(".json").exists()


def test_project_model_is_frozen_and_rejects_unknown_fields() -> None:
    project = valid_project()

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Project.model_validate(
            {
                "project_id": project.project_id,
                "name": project.name,
                "graph": project.graph,
                "runtime_state": {},
            }
        )
    with pytest.raises(ValidationError, match="Instance is frozen"):
        project.name = "不得修改"


def test_round_trip_preserves_exact_definitions_graph_order_and_editor_values(
    tmp_path: Path,
) -> None:
    path = tmp_path / "round-trip.zniku"
    project = valid_project()
    expected = ProjectSnapshot(project=project, definitions=definitions())

    ProjectStore.create(path, project, definitions())
    loaded = ProjectStore.open(path).load()

    assert loaded == expected
    assert [item.type_id for item in loaded.definitions] == [
        "test.VideoTransform",
        "test.Source",
        "test.Output",
    ]
    assert [item.node_id for item in loaded.project.graph.nodes] == [
        "node.transform",
        "node.source",
        "node.output",
    ]
    assert [item.source_node_id for item in loaded.project.graph.edges] == [
        "node.transform",
        "node.source",
    ]
    assert loaded.project.graph.nodes[0].parameters == {"strength": 7}
    assert loaded.project.graph.nodes[0].ui_position == UiPosition(x=320.25, y=-18.5)


def test_save_atomically_replaces_previous_project_content(tmp_path: Path) -> None:
    path = tmp_path / "replace.zniku"
    store = saved_store(path)
    replacement = valid_project(name="第二版", strength=3)

    store.save(replacement, tuple(reversed(definitions())))
    loaded = store.load()

    assert loaded.project == replacement
    assert loaded.definitions == tuple(reversed(definitions()))
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM project").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM graph_nodes").fetchone()[0] == 3
        assert connection.execute("SELECT count(*) FROM graph_edges").fetchone()[0] == 2


@pytest.mark.parametrize("filename", ["project.sqlite", "project.json", "project.ZNIKU"])
def test_wrong_extension_fails_without_creating_a_file(tmp_path: Path, filename: str) -> None:
    path = tmp_path / filename

    with pytest.raises(ProjectFormatError, match="E_PROJECT_EXTENSION_INVALID"):
        ProjectStore.create(path, valid_project(), definitions())

    assert not path.exists()


def test_existing_non_sqlite_file_fails_closed_and_is_untouched(tmp_path: Path) -> None:
    path = tmp_path / "not-sqlite.zniku"
    original = b"synthetic non sqlite content"
    path.write_bytes(original)

    with pytest.raises(ProjectFormatError, match="E_PROJECT_NOT_SQLITE"):
        ProjectStore.open(path)
    with pytest.raises(ProjectFormatError, match="E_PROJECT_EXISTS"):
        ProjectStore.create(path, valid_project(), definitions())

    assert path.read_bytes() == original


def test_create_self_check_failure_only_cleans_its_owned_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "self-check-failure.zniku"

    def reject_created_schema(_store: ProjectStore) -> Any:
        raise ProjectFormatError("E_SYNTHETIC_SELF_CHECK", "合成自检失败")

    monkeypatch.setattr(ProjectStore, "_assert_file_and_schema", reject_created_schema)

    with pytest.raises(ProjectFormatError, match="E_SYNTHETIC_SELF_CHECK"):
        ProjectStore.create(path, valid_project(), definitions())

    assert not path.exists()


def test_unknown_schema_version_fails_without_migration(tmp_path: Path) -> None:
    path = tmp_path / "future.zniku"
    saved_store(path)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 999")

    with pytest.raises(ProjectFormatError, match="E_PROJECT_SCHEMA_VERSION_UNKNOWN"):
        ProjectStore.open(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 999


def test_phase_1_schema_migrates_transactionally_without_changing_project(
    tmp_path: Path,
) -> None:
    path = tmp_path / "phase-1.zniku"
    store = saved_store(path)
    before = store.load()
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        for table in ("latest_results", "artifacts", "node_results", "node_runs", "runs"):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("PRAGMA user_version = 1")

    migrated = ProjectStore.open(path)

    assert migrated.load() == before
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == PROJECT_SCHEMA_VERSION
        runtime_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table' "
                "AND name IN ('runs','node_runs','artifacts','node_results','latest_results')"
            )
        }
    assert runtime_tables == {
        "runs",
        "node_runs",
        "artifacts",
        "node_results",
        "latest_results",
    }


def test_corrupt_phase_1_model_is_rejected_before_any_migration_write(
    tmp_path: Path,
) -> None:
    path = tmp_path / "corrupt-phase-1.zniku"
    saved_store(path)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        for table in ("latest_results", "artifacts", "node_results", "node_runs", "runs"):
            connection.execute(f"DROP TABLE {table}")
        connection.execute(
            "UPDATE graph_nodes SET parameters_json = ? WHERE node_id = ?",
            ('{"strength":99}', "node.transform"),
        )
        connection.execute("PRAGMA user_version = 1")

    with pytest.raises(ProjectValidationError, match="E_PROJECT_GRAPH_INVALID"):
        ProjectStore.open(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        runtime_tables = connection.execute(
            "SELECT count(*) FROM sqlite_schema WHERE type = 'table' "
            "AND name IN ('runs','node_runs','artifacts','node_results','latest_results')"
        ).fetchone()[0]
    assert runtime_tables == 0


def test_phase_1_migration_post_check_failure_rolls_back_ddl_and_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "phase-1-post-check-failure.zniku"
    saved_store(path)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        for table in ("latest_results", "artifacts", "node_results", "node_runs", "runs"):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("PRAGMA user_version = 1")

    original = ProjectStore._assert_schema_shape

    def fail_runtime_schema_post_check(
        connection: sqlite3.Connection,
        *,
        expected_tables: frozenset[str],
        expected_columns: Any,
    ) -> None:
        if "runs" in expected_tables:
            raise ProjectFormatError("E_SYNTHETIC_MIGRATION_POST_CHECK", "合成迁移后检查失败")
        original(
            connection,
            expected_tables=expected_tables,
            expected_columns=expected_columns,
        )

    monkeypatch.setattr(
        ProjectStore,
        "_assert_schema_shape",
        staticmethod(fail_runtime_schema_post_check),
    )

    with pytest.raises(ProjectFormatError, match="E_SYNTHETIC_MIGRATION_POST_CHECK"):
        ProjectStore.open(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        runtime_tables = connection.execute(
            "SELECT count(*) FROM sqlite_schema WHERE type = 'table' "
            "AND name IN ('runs','node_runs','artifacts','node_results','latest_results')"
        ).fetchone()[0]
    assert runtime_tables == 0


def test_malformed_phase_1_schema_fails_without_partial_migration(tmp_path: Path) -> None:
    path = tmp_path / "malformed-phase-1.zniku"
    saved_store(path)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        for table in ("latest_results", "artifacts", "node_results", "node_runs", "runs"):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("ALTER TABLE graph_edges RENAME TO malformed_graph_edges")
        connection.execute("PRAGMA user_version = 1")

    with pytest.raises(ProjectFormatError, match="E_PROJECT_SCHEMA_INVALID"):
        ProjectStore.open(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT count(*) FROM sqlite_schema WHERE type = 'table' AND name = 'runs'"
            ).fetchone()[0]
            == 0
        )


def test_arbitrary_sqlite_file_is_not_accepted_as_a_project(tmp_path: Path) -> None:
    path = tmp_path / "foreign.zniku"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE foreign_data(value TEXT)")

    with pytest.raises(ProjectFormatError, match="E_PROJECT_APPLICATION_ID_UNKNOWN"):
        ProjectStore.open(path)


def test_corrupt_json_model_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "corrupt-model.zniku"
    store = saved_store(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE graph_nodes SET parameters_json = ? WHERE node_id = ?",
            ('{"strength":', "node.transform"),
        )

    with pytest.raises(ProjectValidationError, match="E_PROJECT_MODEL_CORRUPT"):
        store.load()


def test_corrupt_definition_identity_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "corrupt-definition.zniku"
    store = saved_store(path)
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT payload_json FROM node_definitions WHERE type_id = 'test.Source'"
        ).fetchone()
        assert row is not None
        connection.execute(
            "UPDATE node_definitions SET payload_json = replace(?, 'test.Source', 'test.Other') "
            "WHERE type_id = 'test.Source'",
            (row[0],),
        )

    with pytest.raises(ProjectValidationError, match="E_PROJECT_DEFINITION_BINDING_MISMATCH"):
        store.load()


def test_missing_definition_is_rejected_before_existing_content_changes(tmp_path: Path) -> None:
    path = tmp_path / "missing-definition.zniku"
    store = saved_store(path)
    before_bytes = path.read_bytes()
    before_snapshot = store.load()
    incomplete_definitions = tuple(
        item for item in definitions() if item.type_id != "test.VideoTransform"
    )

    with pytest.raises(ProjectValidationError, match="E_PROJECT_GRAPH_INVALID"):
        store.save(valid_project(name="不得保存"), incomplete_definitions)

    assert path.read_bytes() == before_bytes
    assert store.load() == before_snapshot


def test_invalid_graph_is_rejected_before_existing_content_changes(tmp_path: Path) -> None:
    path = tmp_path / "invalid-graph.zniku"
    store = saved_store(path)
    before_bytes = path.read_bytes()
    before_snapshot = store.load()
    invalid_graph = Graph(
        nodes=valid_project().graph.nodes,
        edges=(valid_project().graph.edges[0],),
    )
    invalid_project = Project(
        project_id="project.synthetic",
        name="缺少 required input",
        graph=invalid_graph,
    )

    with pytest.raises(ProjectValidationError, match="E_REQUIRED_INPUT_MISSING"):
        store.save(invalid_project, definitions())

    assert path.read_bytes() == before_bytes
    assert store.load() == before_snapshot


def test_invalid_initial_project_does_not_leave_a_half_created_store(tmp_path: Path) -> None:
    path = tmp_path / "invalid-initial.zniku"
    valid = valid_project()
    invalid = Project(
        project_id=valid.project_id,
        name=valid.name,
        graph=Graph(nodes=valid.graph.nodes, edges=(valid.graph.edges[0],)),
    )

    with pytest.raises(ProjectValidationError, match="E_REQUIRED_INPUT_MISSING"):
        ProjectStore.create(path, invalid, definitions())

    assert not path.exists()


def test_save_cannot_replace_existing_project_identity(tmp_path: Path) -> None:
    path = tmp_path / "immutable-project-id.zniku"
    store = saved_store(path)
    before = store.load()
    replacement = Project(
        project_id="project.replacement",
        name=before.project.name,
        graph=before.project.graph,
    )

    with pytest.raises(ProjectStoreError, match="E_PROJECT_ID_IMMUTABLE"):
        store.save(replacement, before.definitions)

    assert store.load() == before
