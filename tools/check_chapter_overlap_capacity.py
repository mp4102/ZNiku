"""前检新 planner 在现有通用媒体上游子图中的容量，不执行媒体或未来 FI。

只构造 SourceMedia → 静态 SplitVideo → 人工增强 → 章内 Merge；所有 executor/validator
来自现有通用媒体注册表。结果不代表新 AV27/overlap 业务链、画布性能或千章媒体处理已通过。
输出写入操作者指定的新测试目录；不覆盖既有工程，不清理测试产物，不启动 HTTP 服务。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import tracemalloc
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter

from pydantic import JsonValue

from zniku.chapter_overlap import (
    AdmittedTimeline,
    AverageChapterSelector,
    ChapterLeafPlan,
    ChapterSettings,
    plan_chapters_and_leaves,
)
from zniku.graph import (
    Edge,
    Graph,
    GraphValidator,
    ManualExternalExecutorSpec,
    NodeDefinition,
    NodeInstance,
    PythonExecutorSpec,
    UiPosition,
)
from zniku.media import (
    external_video_transform_definition,
    media_python_adapters,
    media_validators,
    merge_video_definition,
    parse_segments,
    source_media_definition,
    split_video_definition,
)
from zniku.project import Project, ProjectSnapshot, ProjectStore, StudioState
from zniku.project_service.host import _MAX_BODY_BYTES
from zniku.project_service.models import SaveProjectCommand
from zniku.runtime import RuntimeService

SYNTHETIC_ARTIFACT_ID = "00000000-0000-4000-8000-000000000001"
SYNTHETIC_SESSION_ID = "00000000-0000-4000-8000-000000000002"


@dataclass(frozen=True, slots=True)
class CapacityFixture:
    """仅为容量实验携带已实现 planner 与普通 Project；不是新的运行计划模型。"""

    plan: ChapterLeafPlan
    project: Project
    definitions: tuple[NodeDefinition, ...]


@dataclass(frozen=True, slots=True)
class CapacityMeasurement:
    """区分传输、内存构图和 SQLite 证据，不把不同层容量混为成功。"""

    chapter_count: int
    leaf_count: int
    node_count: int
    edge_count: int
    split_port_count: int
    save_request_bytes: int
    snapshot_json_bytes: int
    http_body_limit_bytes: int
    save_request_fits_http: bool
    sqlite_bytes: int
    build_seconds: float
    build_peak_traced_bytes: int | None
    validate_seconds: float
    create_seconds: float
    create_pending_run_seconds: float
    save_seconds: float
    reopen_seconds: float
    unchanged_run_history: bool
    pending_run_node_count: int
    executed_media_nodes: int = 0
    complete_overlap_chain_verified: bool = False


def build_capacity_fixture(
    *, chapter_count: int = 1000, leaves_per_chapter: int = 1
) -> CapacityFixture:
    """以 30 FPS/5 分钟的合成事实构造有界上游图，不访问所声明的媒体路径。

    实验参数先限制在明确预算内，随后仍由正式 planner 决定所有章/叶区间。
    自定义 Split 的精确端口 shape 使用单独稳定 type_id；它复用真实既有 adapter，
    不注册未实现的 Context、FI、Crop 或 Program executor。
    """

    if type(chapter_count) is not int or not 1 <= chapter_count <= 1000:
        raise ValueError("E_CAPACITY_CHAPTER_BUDGET: 实验章数必须在 1..1000")
    if type(leaves_per_chapter) is not int or not 1 <= leaves_per_chapter <= 10:
        raise ValueError("E_CAPACITY_LEAF_BUDGET: 实验每章叶数必须在 1..10")
    plan = plan_chapters_and_leaves(
        AdmittedTimeline(
            artifact_id=SYNTHETIC_ARTIFACT_ID,
            frame_count=chapter_count * leaves_per_chapter * 30 * 60 * 5,
            frame_rate="30/1",
        ),
        ChapterSettings(
            chapter_selector=AverageChapterSelector(mode="average", count=chapter_count),
            leaf_max_minutes=5,
        ),
    )
    leaves = tuple(leaf for chapter in plan.chapters for leaf in chapter.leaves)
    source = source_media_definition()
    split = split_video_definition(
        tuple(leaf.leaf_id for leaf in leaves),
        type_id=f"zniku.media.capacity.split.{len(leaves)}",
    )
    enhancement = external_video_transform_definition("enhancement")
    merge = merge_video_definition()
    definitions = (source, split, enhancement, merge)
    adapters = media_python_adapters()
    validators = media_validators()
    for definition in definitions:
        executor = definition.executor
        if isinstance(executor, PythonExecutorSpec):
            if executor.adapter not in adapters:
                raise ValueError("E_CAPACITY_EXECUTOR_UNKNOWN: 必须使用真实已注册 adapter")
        elif not isinstance(executor, ManualExternalExecutorSpec):
            raise ValueError("E_CAPACITY_EXECUTOR_KIND: 非本实验允许的 executor")
        if definition.validator is not None and definition.validator.adapter not in validators:
            raise ValueError("E_CAPACITY_VALIDATOR_UNKNOWN: 必须使用真实已注册 validator")

    segments: list[JsonValue] = [
        {
            "port_id": leaf.leaf_id,
            "start_frame": leaf.start_frame,
            "end_frame": leaf.end_frame,
        }
        for leaf in leaves
    ]
    parse_segments(
        segments,
        expected_ports=tuple(leaf.leaf_id for leaf in leaves),
        input_frames=plan.source.frame_count,
    )
    nodes = [
        NodeInstance(
            node_id="source",
            type_id=source.type_id,
            definition_version=source.version,
            parameters={"source_path": "C:/synthetic-capacity/not-created.mkv"},
        ),
        NodeInstance(
            node_id="split",
            type_id=split.type_id,
            definition_version=split.version,
            parameters={"segments": segments},
        ),
    ]
    edges = [
        Edge(
            source_node_id="source",
            source_port_id="out",
            target_node_id="split",
            target_port_id="video",
        )
    ]
    for chapter in plan.chapters:
        merge_id = f"merge.{chapter.chapter_id}"
        for leaf in chapter.leaves:
            enhancement_id = f"enhance.{leaf.leaf_id}"
            nodes.append(
                NodeInstance(
                    node_id=enhancement_id,
                    type_id=enhancement.type_id,
                    definition_version=enhancement.version,
                    parameters={
                        "tool": "Synthetic external operator",
                        "model": "Capacity only - never executed",
                        "tool_version": "1.0.0",
                        "frame_relation": "equal",
                    },
                )
            )
            edges.extend(
                (
                    Edge(
                        source_node_id="split",
                        source_port_id=leaf.leaf_id,
                        target_node_id=enhancement_id,
                        target_port_id="video",
                    ),
                    Edge(
                        source_node_id=enhancement_id,
                        source_port_id="video",
                        target_node_id=merge_id,
                        target_port_id="videos",
                        ordinal=leaf.ordinal,
                    ),
                )
            )
        nodes.append(
            NodeInstance(
                node_id=merge_id,
                type_id=merge.type_id,
                definition_version=merge.version,
            )
        )
    return CapacityFixture(
        plan,
        Project(
            project_id=f"capacity.{chapter_count}.{leaves_per_chapter}",
            name="分章容量合成上游子图",
            graph=Graph(nodes=tuple(nodes), edges=tuple(edges)),
        ),
        definitions,
    )


def save_request_bytes(project: Project) -> bytes:
    """计算与现有保存命令一致的紧凑 UTF-8 请求体；不包含不存在的未来节点。"""

    command = SaveProjectCommand(
        operation="save_project",
        project_session_id=SYNTHETIC_SESSION_ID,
        expected_storage_revision=0,
        project=project,
        studio_state=StudioState(),
    )
    return command.model_dump_json().encode("utf-8")


def measure_capacity(
    directory: Path,
    *,
    chapter_count: int = 1000,
    leaves_per_chapter: int = 1,
    measure_build_memory: bool = False,
) -> CapacityMeasurement:
    """完整验证、建 SQLite、创建单 Source 待执行 Run、编辑保存及重开；不推进 Run。

    请求超限不影响本地 SQLite 的独立测量，报告明确标记 HTTP 不能承载；不放宽上限。
    仅允许新的实验目录，保存失败由原有 Store 事务处理，不修改历史 Run snapshot。
    Run 只选 Source，但保存完整图 snapshot；不宣称全图 Run all 启动容量已通过。
    tracemalloc 仅统计构图阶段 Python 分配峰值，不冒充全过程 RSS 峰值。
    """

    directory.mkdir(parents=True, exist_ok=False)
    started = perf_counter()
    if measure_build_memory:
        tracemalloc.start()
    try:
        fixture = build_capacity_fixture(
            chapter_count=chapter_count, leaves_per_chapter=leaves_per_chapter
        )
        peak = tracemalloc.get_traced_memory()[1] if measure_build_memory else None
    finally:
        if measure_build_memory:
            tracemalloc.stop()
    build_seconds = perf_counter() - started
    started = perf_counter()
    GraphValidator(fixture.definitions).validate(fixture.project.graph)
    validate_seconds = perf_counter() - started
    request_size = len(save_request_bytes(fixture.project))
    snapshot_size = len(
        ProjectSnapshot(project=fixture.project, definitions=fixture.definitions)
        .model_dump_json()
        .encode("utf-8")
    )
    path = directory / "capacity.zniku"
    started = perf_counter()
    store = ProjectStore.create(path, fixture.project, fixture.definitions)
    create_seconds = perf_counter() - started
    runtime = RuntimeService(
        store,
        directory / "attempts",
        python_adapters=media_python_adapters(),
        validators=media_validators(),
    )
    started = perf_counter()
    run = runtime.create_run(selected_targets=("source",))
    create_run_seconds = perf_counter() - started
    before = run.model_dump_json()
    updated_first = fixture.project.graph.nodes[0].model_copy(
        update={"ui_position": UiPosition(x=100.0, y=200.0)}
    )
    changed = fixture.project.model_copy(
        update={
            "graph": Graph(
                nodes=(updated_first, *fixture.project.graph.nodes[1:]),
                edges=fixture.project.graph.edges,
            )
        }
    )
    observed_revision = store.load_authoring().storage_revision
    started = perf_counter()
    store.save(changed, fixture.definitions, expected_storage_revision=observed_revision)
    save_seconds = perf_counter() - started
    started = perf_counter()
    reopened = ProjectStore.open(path).load()
    reopen_seconds = perf_counter() - started
    if reopened != ProjectSnapshot(project=changed, definitions=fixture.definitions):
        raise AssertionError("容量重开不能截断、重排或改变 definitions")
    unchanged = runtime.repository.get_run(run.run_id).model_dump_json() == before
    if not unchanged:
        raise AssertionError("画布编辑不能回写既有 Run snapshot 或历史")
    with sqlite3.connect(path) as connection:
        for table in ("artifacts", "node_results"):
            if connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] != 0:
                raise AssertionError("容量测试不得执行媒体或登记虚构结果")
    if any((directory / "attempts").iterdir()):
        raise AssertionError("创建待执行 Run 不应创建媒体 attempt 目录")
    return CapacityMeasurement(
        chapter_count=fixture.plan.chapter_count,
        leaf_count=fixture.plan.leaf_count,
        node_count=len(changed.graph.nodes),
        edge_count=len(changed.graph.edges),
        split_port_count=len(fixture.definitions[1].output_ports),
        save_request_bytes=request_size,
        snapshot_json_bytes=snapshot_size,
        http_body_limit_bytes=_MAX_BODY_BYTES,
        save_request_fits_http=request_size <= _MAX_BODY_BYTES,
        sqlite_bytes=path.stat().st_size,
        build_seconds=build_seconds,
        build_peak_traced_bytes=peak,
        validate_seconds=validate_seconds,
        create_seconds=create_seconds,
        create_pending_run_seconds=create_run_seconds,
        save_seconds=save_seconds,
        reopen_seconds=reopen_seconds,
        unchanged_run_history=unchanged,
        pending_run_node_count=len(run.node_runs),
    )


def main() -> int:
    """在独立新目录测量固定的千章单叶、四叶两组；超限明确返回非零，不删文件。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--measure-build-memory", action="store_true")
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        parser.error("输出必须为新的实验目录，禁止覆盖")
    measurements = [
        measure_capacity(
            output / f"1000-chapters-{count}-leaves",
            leaves_per_chapter=count,
            measure_build_memory=bool(args.measure_build_memory),
        )
        for count in (1, 4)
    ]
    print(json.dumps([asdict(item) for item in measurements], ensure_ascii=False, indent=2))
    return 0 if all(item.save_request_fits_http for item in measurements) else 1


if __name__ == "__main__":
    raise SystemExit(main())
