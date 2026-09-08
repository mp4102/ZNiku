"""以合成文件覆盖项目数据位置、路径策略注入与持久 handoff 绑定的集成边界。

不操作桌面、真实工程或媒体；AV27 Source 的 adapter/validator/probe 全部显式替换为合成实现，
这里只验运行目录及存储策略，不声称验证媒体质量或完整 AV27 工作流。
"""

from __future__ import annotations

import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from authoring_helpers import authoring_command
from zniku.avenhance_v27.definitions import (
    SOURCE_PROGRAM_ADAPTER,
    SOURCE_PROGRAM_TYPE_ID,
    SOURCE_PROGRAM_VALIDATOR,
)
from zniku.graph import (
    Edge,
    ExecutionMode,
    ExecutorOutputPathSpec,
    Graph,
    ManualExternalExecutorSpec,
    NodeDefinition,
    NodeInstance,
    PortSpec,
    PythonExecutorSpec,
)
from zniku.project import Project, ProjectStore
from zniku.project.paths import validate_filename_component
from zniku.project.storage import new_project_storage
from zniku.project_service import ProjectServiceApplication, ProjectServiceError
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
from zniku.runtime.paths import incoming_directory_name
from zniku.runtime.runner import OutputPathSpec


def _fixture(*, manual: bool = False) -> tuple[Project, tuple[NodeDefinition, ...]]:
    source = NodeDefinition(
        type_id="test.data.source",
        version="1.0.0",
        output_ports=(PortSpec(port_id="out", data_type="DataFile"),),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(
            adapter="tests.data:source",
            output_paths=(ExecutorOutputPathSpec(port_id="out", relative_path="source.txt"),),
        ),
    )
    external = NodeDefinition(
        type_id="test.data.manual",
        version="1.0.0",
        input_ports=(PortSpec(port_id="in", data_type="DataFile", required=True),),
        output_ports=(PortSpec(port_id="out", data_type="DataFile"),),
        execution_mode=ExecutionMode.MANUAL_EXTERNAL,
        executor=ManualExternalExecutorSpec(
            instructions="提交合成文件",
            output_paths=(ExecutorOutputPathSpec(port_id="out", relative_path="legacy.txt"),),
        ),
    )
    definitions = (source, external) if manual else (source,)
    graph = Graph(
        nodes=tuple(
            NodeInstance(
                node_id="manual" if definition == external else "source",
                type_id=definition.type_id,
                definition_version=definition.version,
            )
            for definition in definitions
        ),
        edges=(
            Edge(
                source_node_id="source",
                source_port_id="out",
                target_node_id="manual",
                target_port_id="in",
            ),
        )
        if manual
        else (),
    )
    return Project(
        project_id="test.project.data", name="合成数据目录测试", graph=graph
    ), definitions


def _adapter(context: PythonAdapterContext) -> PythonAdapterResult:
    for output in context.outputs:
        output.path.write_bytes(b"synthetic-output")
    return PythonAdapterResult()


def _synthetic_validator(_context: NodeValidatorContext) -> NodeValidatorResult:
    return NodeValidatorResult(passed=True)


def _av27_command(tmp_path: Path) -> dict[str, object]:
    source = tmp_path / "Synthetic Input.mkv"
    source.write_bytes(b"synthetic-not-media")
    return {
        "operation": "create_av_enhance_v27",
        "request": {
            "profile_version": "2.7.0",
            "project_path": str(tmp_path / "Final Project.zniku"),
            "project_id": "synthetic.av27.storage",
            "project_name": "合成存储工程",
            "source_mode": "program",
            "sources": [{"source_path": str(source), "source_ordinal": 0}],
            "mr": {"mode": "off"},
        },
    }


def _version(path: Path) -> int:
    with closing(sqlite3.connect(path)) as connection:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])


def _run(application: ProjectServiceApplication, node_id: str | None = None) -> Run:
    command = (
        {"operation": "run_all"} if node_id is None else {"operation": "run_to", "node_id": node_id}
    )
    started = authoring_command(application, command)
    assert started.active_run_id is not None
    assert application.wait_until_idle(timeout=5)
    return application.inspect_run_detail(started.active_run_id).run


@pytest.mark.parametrize("descriptive", [False, True])
def test_resolver_affects_new_outputs_but_cannot_reinterpret_existing_handoff(
    tmp_path: Path, descriptive: bool
) -> None:
    project, definitions = _fixture(manual=True)
    store = ProjectStore.create(tmp_path / "workflow.zniku", project, definitions)
    root = tmp_path / "attempts"

    def resolve(
        _run: Run, node: NodeInstance, _definition: NodeDefinition
    ) -> tuple[OutputPathSpec, ...]:
        return (OutputPathSpec("out", f"A/Synthetic.{node.node_id}.txt"),)

    service = RuntimeService(
        store,
        root,
        python_adapters={"tests.data:source": _adapter},
        output_path_resolver=resolve if descriptive else None,
    )
    run = service.run_until_blocked(service.create_run().run_id)
    node_run = next(item for item in run.node_runs if item.node_id == "manual")
    assert node_run.state is NodeRunState.WAITING_EXTERNAL
    assert node_run.external_handoff is not None
    target = Path(node_run.external_handoff.output_targets[0].path)
    assert target.name == ("Synthetic.manual.txt" if descriptive else "legacy.txt")
    assert target.is_relative_to(Path(node_run.work_dir) / "outputs")
    incoming = Path(node_run.work_dir) / "incoming" / incoming_directory_name("out")
    assert incoming.is_dir() and tuple(incoming.iterdir()) == ()
    source_attempt = next(item for item in run.node_runs if item.node_id == "source")
    source_artifact = service.repository.get_artifact(source_attempt.output_artifact_ids[0])
    assert Path(source_artifact.path).name == (
        "Synthetic.source.txt" if descriptive else "source.txt"
    )
    assert Path(source_artifact.path).read_bytes() == b"synthetic-output"
    target.write_bytes(b"synthetic-external-output")

    def changed_policy(
        _run: Run, _node: NodeInstance, _definition: NodeDefinition
    ) -> tuple[OutputPathSpec, ...]:
        pytest.fail("已有 handoff 不能再次调用命名策略，即使重开应用后策略发生变化")

    reopened = RuntimeService(
        store,
        root,
        python_adapters={"tests.data:source": _adapter},
        output_path_resolver=changed_policy,
    )
    finished = reopened.submit_external(node_run.node_run_id)
    assert finished.state is RunState.COMPLETED
    final_attempt = next(item for item in finished.node_runs if item.node_id == "manual")
    artifact = reopened.repository.get_artifact(final_attempt.output_artifact_ids[0])
    assert artifact.path == str(target)
    assert incoming.is_dir() and target.read_bytes() == b"synthetic-external-output"
    reused = reopened.run_until_blocked(reopened.create_run().run_id)
    assert reused.state is RunState.COMPLETED
    assert all(item.reused_from_result_id is not None for item in reused.node_runs)
    assert store.load().project == project


@pytest.mark.parametrize(
    "port_id", ["out", "a/b", "a/../../../../escaped", "C:output", "CON", "p" * 128]
)
def test_incoming_directory_encodes_valid_graph_ports_without_path_escape(
    tmp_path: Path, port_id: str
) -> None:
    """Graph 的 port ID 不等于文件系统组件，不能因此限制合法 Graph 或逃逸 attempt。"""

    project, definitions = _fixture(manual=True)
    external = definitions[1].model_copy(
        update={
            "output_ports": (PortSpec(port_id=port_id, data_type="DataFile"),),
            "executor": ManualExternalExecutorSpec(
                instructions="提交合成文件",
                output_paths=(ExecutorOutputPathSpec(port_id=port_id, relative_path="result.txt"),),
            ),
        }
    )
    store = ProjectStore.create(tmp_path / "ports.zniku", project, (definitions[0], external))
    runtime = RuntimeService(
        store, tmp_path / "attempts", python_adapters={"tests.data:source": _adapter}
    )
    run = runtime.run_until_blocked(runtime.create_run().run_id)
    attempt = next(item for item in run.node_runs if item.node_id == "manual")
    assert not (tmp_path / "escaped").exists()
    assert attempt.state is NodeRunState.WAITING_EXTERNAL
    assert attempt.external_handoff is not None
    assert attempt.external_handoff.output_targets[0].port_id == port_id
    incoming = Path(attempt.work_dir) / "incoming"
    directories = tuple(incoming.iterdir())
    assert len(directories) == 1 and directories[0].is_dir()
    validate_filename_component(directories[0].name)
    assert tuple(directories[0].iterdir()) == ()
    assert directories[0].resolve().is_relative_to(incoming.resolve())


def test_case_distinct_ports_get_distinct_incoming_directories_on_windows(tmp_path: Path) -> None:
    project, definitions = _fixture(manual=True)
    ports = ("out", "OUT")
    external = definitions[1].model_copy(
        update={
            "output_ports": tuple(PortSpec(port_id=port, data_type="DataFile") for port in ports),
            "executor": ManualExternalExecutorSpec(
                instructions="提交合成文件",
                output_paths=tuple(
                    ExecutorOutputPathSpec(port_id=port, relative_path=f"{index}.txt")
                    for index, port in enumerate(ports)
                ),
            ),
        }
    )
    store = ProjectStore.create(tmp_path / "case.zniku", project, (definitions[0], external))
    runtime = RuntimeService(
        store, tmp_path / "attempts", python_adapters={"tests.data:source": _adapter}
    )
    run = runtime.run_until_blocked(runtime.create_run().run_id)
    attempt = next(item for item in run.node_runs if item.node_id == "manual")
    assert attempt.state is NodeRunState.WAITING_EXTERNAL
    assert attempt.external_handoff is not None
    assert tuple(item.port_id for item in attempt.external_handoff.output_targets) == ports
    directories = tuple((Path(attempt.work_dir) / "incoming").iterdir())
    assert len(directories) == 2
    assert len({item.name.casefold() for item in directories}) == 2
    for directory in directories:
        validate_filename_component(directory.name)
        assert directory.is_dir() and tuple(directory.iterdir()) == ()


@pytest.mark.parametrize("desktop_default", [False, True])
def test_new_generic_project_runs_in_its_configured_persistent_root(
    tmp_path: Path, desktop_default: bool
) -> None:
    project, definitions = _fixture()
    path = tmp_path / "Final Project.zniku"
    legacy = tmp_path / "launcher-attempts"
    application = ProjectServiceApplication(
        work_root=legacy,
        definition_catalog=definitions,
        python_adapters={"tests.data:source": _adapter},
        project_data_default=desktop_default,
    )
    created = application.command({"operation": "create_project", "path": str(path)})
    assert created.snapshot is not None
    storage = ProjectStore.open(path).load_storage()
    expected_root = path.with_suffix(".data") / "attempts" if desktop_default else legacy
    assert application.work_root == expected_root
    if desktop_default:
        assert storage is not None and storage.mode == "adjacent"
        assert storage.data_root == str(path.with_suffix(".data"))
        assert storage.attempts_root == str(expected_root)
    else:
        assert storage is None
    authoring_command(
        application,
        {
            "operation": "save_project",
            "project": created.snapshot.project.model_copy(update={"graph": project.graph}),
        },
    )
    run = _run(application)
    assert run.state is RunState.COMPLETED
    assert Path(run.node_runs[0].work_dir).parent == expected_root
    assert (Path(run.node_runs[0].work_dir) / "outputs" / "source.txt").is_file()
    reopened = ProjectServiceApplication(
        work_root=tmp_path / "new-launcher", project_data_default=True
    )
    reopened.command({"operation": "open_project", "path": str(path)})
    assert reopened.work_root == (expected_root if desktop_default else tmp_path / "new-launcher")


@pytest.mark.parametrize("schema_version", [2, 3])
def test_open_and_run_legacy_project_keep_original_root_without_automatic_schema_upgrade(
    tmp_path: Path, schema_version: int
) -> None:
    project, definitions = _fixture()
    path = tmp_path / "legacy.zniku"
    store = ProjectStore.create(path, project, definitions)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("DROP TABLE project_storage")
        if schema_version == 2:
            connection.execute("DROP TABLE studio_state")
        connection.execute(f"PRAGMA user_version = {schema_version}")
    before = path.read_bytes()
    legacy = tmp_path / "existing-launcher-root"
    application = ProjectServiceApplication(
        work_root=legacy,
        python_adapters={"tests.data:source": _adapter},
        project_data_default=True,
    )
    application.command({"operation": "open_project", "path": str(path)})
    assert application.work_root == legacy
    assert ProjectStore.open(path).load_storage() is None
    assert path.read_bytes() == before
    run = _run(application)
    assert run.state is RunState.COMPLETED
    assert Path(run.node_runs[0].work_dir).parent == legacy
    assert _version(path) == schema_version
    assert store.load_storage() is None
    assert not path.with_suffix(".data").exists()


def test_av27_atomic_create_uses_final_project_name_and_source_runs_in_that_root(
    tmp_path: Path,
) -> None:
    path = tmp_path / "Final Video Project.zniku"
    source = tmp_path / "Synthetic Movie (2026).mkv"
    source.write_bytes(b"synthetic-not-media")
    application = ProjectServiceApplication(
        work_root=tmp_path / "launcher",
        python_adapters={SOURCE_PROGRAM_ADAPTER: _adapter},
        validators={SOURCE_PROGRAM_VALIDATOR: _synthetic_validator},
        media_probe=lambda _path, _kind: {"synthetic": True},
        project_data_default=True,
    )
    created = application.command(
        {
            "operation": "create_av_enhance_v27",
            "request": {
                "profile_version": "2.7.0",
                "project_path": str(path),
                "project_id": "synthetic.av27.data",
                "project_name": "合成 AV27 工程",
                "source_mode": "program",
                "sources": [{"source_path": str(source), "source_ordinal": 0}],
                "mr": {"mode": "off"},
            },
        }
    )
    assert created.snapshot is not None
    storage = ProjectStore.open(path).load_storage()
    assert storage is not None
    assert storage.data_root == str(path.with_suffix(".data"))
    assert storage.media_basename == source.stem
    node = next(
        item
        for item in created.snapshot.project.graph.nodes
        if item.type_id == SOURCE_PROGRAM_TYPE_ID
    )
    run = _run(application, node.node_id)
    assert run.state is RunState.COMPLETED
    assert len(run.node_runs) == 1
    assert Path(run.node_runs[0].work_dir).parent == Path(storage.attempts_root)
    assert sorted(item.name for item in tmp_path.glob("*.data")) == ["Final Video Project.data"]
    assert source.read_bytes() == b"synthetic-not-media"


@pytest.mark.parametrize("desktop_default", [False, True])
def test_av27_create_custom_parent_and_explicit_media_basename_are_persisted(
    tmp_path: Path, desktop_default: bool
) -> None:
    parent = tmp_path / "dedicated-disk"
    parent.mkdir()
    application = ProjectServiceApplication(
        work_root=tmp_path / "launcher",
        project_data_default=desktop_default,
        python_adapters={SOURCE_PROGRAM_ADAPTER: _adapter},
        validators={SOURCE_PROGRAM_VALIDATOR: _synthetic_validator},
        media_probe=lambda _path, _kind: {"synthetic": True},
    )
    command = {
        **_av27_command(tmp_path),
        "data_parent_directory": str(parent),
        "media_basename": "Explicit Title (2026)",
    }
    created = application.command(command)
    assert created.snapshot is not None
    path = tmp_path / "Final Project.zniku"
    store = ProjectStore.open(path)
    storage = store.load_storage()
    assert storage is not None
    assert storage.mode == "custom"
    assert storage.data_root == str(parent / "Final Project.data")
    assert storage.media_basename == "Explicit Title (2026)"
    assert not path.with_suffix(".data").exists()
    assert sorted(item.name for item in parent.iterdir()) == ["Final Project.data"]
    source = next(
        node
        for node in created.snapshot.project.graph.nodes
        if node.type_id == SOURCE_PROGRAM_TYPE_ID
    )
    run = _run(application, source.node_id)
    assert run.state is RunState.COMPLETED
    assert Path(run.node_runs[0].work_dir).parent == Path(storage.attempts_root)
    assert store.load_storage() == storage


@pytest.mark.parametrize(
    "failure", ["missing_parent", "root_file", "existing_root", "write_denied"]
)
def test_av27_storage_prepare_failure_does_not_create_database_or_change_open_legacy_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    project, definitions = _fixture()
    old_path = tmp_path / "existing.zniku"
    old_store = ProjectStore.create(old_path, project, definitions)
    before = old_path.read_bytes()
    legacy = tmp_path / "original-launcher"
    application = ProjectServiceApplication(work_root=legacy, project_data_default=True)
    application.command({"operation": "open_project", "path": str(old_path)})
    initial_session = application.inspect().project_session_id
    parent = tmp_path / "data-parent"
    root = parent / "Final Project.data"
    if failure != "missing_parent":
        parent.mkdir()
    if failure == "root_file":
        root.write_bytes(b"existing-user-file")
    elif failure == "existing_root":
        root.mkdir()
        (root / "keep.bin").write_bytes(b"existing-user-file")
    elif failure == "write_denied":

        def denied(*_args: object, **_kwargs: object) -> None:
            raise OSError("synthetic write denied")

        monkeypatch.setattr("zniku.project_service.storage_paths.tempfile.TemporaryFile", denied)
    with pytest.raises(ProjectServiceError, match="E_PROJECT_STORAGE"):
        application.command({**_av27_command(tmp_path), "data_parent_directory": str(parent)})
    assert not (tmp_path / "Final Project.zniku").exists()
    assert not tuple(tmp_path.glob(".zniku-create-*"))
    assert application.inspect().project_path == str(old_path)
    assert application.inspect().project_session_id == initial_session
    assert application.work_root == legacy
    assert old_path.read_bytes() == before
    assert old_store.load_storage() is None
    if failure == "root_file":
        assert root.read_bytes() == b"existing-user-file"
    elif failure == "existing_root":
        assert tuple(item.name for item in root.iterdir()) == ("keep.bin",)
        assert (root / "keep.bin").read_bytes() == b"existing-user-file"


@pytest.mark.parametrize(
    "parent", ["relative", ".", "C:relative", "../outside", "relative/../relative"]
)
def test_av27_create_rejects_nonabsolute_or_traversing_data_parent_before_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parent: str
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "relative").mkdir()
    application = ProjectServiceApplication(
        work_root=tmp_path / "launcher", project_data_default=True
    )
    with pytest.raises(ProjectServiceError):
        application.command({**_av27_command(tmp_path), "data_parent_directory": parent})
    assert not (tmp_path / "Final Project.zniku").exists()
    assert not (tmp_path / "Final Project.data").exists()
    assert tuple((tmp_path / "relative").iterdir()) == ()


def test_explicit_media_basename_alone_enables_adjacent_storage_without_desktop_default(
    tmp_path: Path,
) -> None:
    application = ProjectServiceApplication(work_root=tmp_path / "launcher")
    application.command({**_av27_command(tmp_path), "media_basename": "Chosen Title"})
    storage = ProjectStore.open(tmp_path / "Final Project.zniku").load_storage()
    assert storage is not None
    assert storage.mode == "adjacent"
    assert storage.media_basename == "Chosen Title"
    assert storage.data_root == str(tmp_path / "Final Project.data")


@pytest.mark.parametrize("basename", ["COM¹", "\ud800", "😀" * 91, "wrong/name", "../outside"])
def test_create_rejects_unsafe_media_basename_before_preparing_storage(
    tmp_path: Path, basename: str
) -> None:
    application = ProjectServiceApplication(
        work_root=tmp_path / "launcher", project_data_default=True
    )
    with pytest.raises(ProjectServiceError):
        application.command({**_av27_command(tmp_path), "media_basename": basename})
    assert not (tmp_path / "Final Project.zniku").exists()
    assert not (tmp_path / "Final Project.data").exists()


def test_new_project_storage_operation_remains_pollable_without_any_run(tmp_path: Path) -> None:
    application = ProjectServiceApplication(
        work_root=tmp_path / "launcher", project_data_default=True
    )
    created = application.command(
        {"operation": "create_project", "path": str(tmp_path / "poll.zniku")}
    )
    assert created.project_session_id is not None
    assert created.active_run_id is None
    with application.storage_authority(created.project_session_id, modifying=True):
        status = application.inspect()
        assert status.project_session_id == created.project_session_id
        assert status.active_operation == "migrate_storage"
        assert status.active_run_id is None
    assert application.inspect().active_operation is None


@pytest.mark.parametrize("location", ["data", "attempts"])
@pytest.mark.parametrize("failure", ["missing", "junction"])
def test_open_configured_storage_rejects_missing_or_replaced_directory_without_recreating_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, location: str, failure: str
) -> None:
    """用合成 Windows reparse 标志模拟目录被换成 junction，不依赖创建系统链接权限。"""

    project, definitions = _fixture()
    path = tmp_path / "configured.zniku"
    storage = new_project_storage(path)
    root, attempts = Path(storage.data_root), Path(storage.attempts_root)
    attempts.mkdir(parents=True)
    ProjectStore.create(path, project, definitions, storage=storage)
    before = path.read_bytes()
    affected = root if location == "data" else attempts
    if failure == "missing":
        retained = affected.with_name(f"{affected.name}.offline")
        assert affected.is_relative_to(tmp_path) and retained.is_relative_to(tmp_path)
        affected.rename(retained)
    else:
        original_lstat = Path.lstat

        def replaced_lstat(value: Path) -> os.stat_result:
            if value == affected:
                return cast(
                    os.stat_result, SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
                )
            return original_lstat(value)

        monkeypatch.setattr(Path, "lstat", replaced_lstat)
    application = ProjectServiceApplication(
        work_root=tmp_path / "launcher", project_data_default=True
    )
    with pytest.raises(ProjectServiceError, match="E_PROJECT_STORAGE"):
        application.command({"operation": "open_project", "path": str(path)})
    assert application.inspect().snapshot is None
    assert path.read_bytes() == before
    if failure == "missing":
        assert not affected.exists()
