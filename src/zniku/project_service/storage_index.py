"""从工程正式记录重建本机可读文件目录，不存第二套运行真值或复制媒体。

HTML 只含转义文字和数据根内已绑定文件的相对链接；根外依赖明确列出但不生成任意文件链接。
生成必须是操作者显式动作，且全部 Run 已终结。旧索引不被 Runtime 读取，也不会改变 reuse/stale。
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from html import escape
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from zniku.project.models import ProjectModel
from zniku.project.storage import legacy_project_storage
from zniku.project.store import ProjectStore, ProjectStoreError
from zniku.runtime.repository import RuntimeRepository

from .storage import _assert_terminal, _failure, _identity, _safe_path, _state, inspect_storage

INDEX_NAME = "文件目录.html"
_MARKER = "<!-- ZNIKU derived storage index; not runtime authority -->"


class StorageIndexResult(ProjectModel):
    """只返回固定派生文件位置；打开能力仍须通过绑定当前工程的宿主引用。"""

    contract_version: Literal["0.3.2"] = "0.3.2"
    project_session_id: str
    expected_storage_revision: int
    path: str
    artifact_count: int
    external_dependency_count: int
    warnings: tuple[str, ...]


def _cell(value: object) -> str:
    return escape(str(value), quote=True)


def _file_link(raw: str, root: Path) -> str:
    path = Path(raw)
    if not path.is_absolute() or not path.is_relative_to(root):
        return _cell(raw)
    try:
        _safe_path(path)
    except (ProjectStoreError, OSError):
        # 显示缺失项但不生成链接；索引不能借文件路径绕过宿主的 reparse 边界。
        return _cell(raw) + "(缺失或不可安全访问)"
    relative = path.relative_to(root).as_posix()
    href = quote(relative, safe="/")
    return f'<a href="{_cell(href)}">{_cell(relative)}</a>'


def _declaration(parameters: Mapping[str, object], *, prefix: str = "", depth: int = 0) -> str:
    """有限展示嵌套的声明字段，不将任意参数或路径解释为媒体、模型能力证明。"""

    values: list[str] = []
    for key, value in parameters.items():
        if any(
            part in key.lower() for part in ("model", "version", "tool", "software")
        ) and isinstance(value, str | int | float | bool):
            values.append(f"{prefix}{key}={value}")
        elif isinstance(value, Mapping) and depth < 3:
            child = _declaration(value, prefix=f"{prefix}{key}.", depth=depth + 1)
            if child:
                values.append(child)
    return "; ".join(values)


def export_storage_index(
    store: ProjectStore,
    *,
    legacy_root: str | Path,
    project_session_id: str,
    expected_storage_revision: int,
) -> StorageIndexResult:
    """显式重建 HTML；数据库 CAS、Run 终态和目标安全检查全部通过才原子替换旧派生索引。"""

    state = _state(store)
    store._check_storage_revision(state.revision, expected_storage_revision)
    _assert_terminal(state)
    storage = state.storage or legacy_project_storage(legacy_root)
    if storage.mode == "legacy":
        raise _failure("INDEX_LOCATION", "旧共享数据位置不写工程索引，请先显式迁移到专属数据目录")
    root = _safe_path(Path(storage.data_root))
    destination = root / INDEX_NAME
    _safe_path(destination, missing=True)
    destination_identity = _identity(destination) if destination.exists() else None
    if destination.exists():
        if not destination.is_file():
            raise _failure("INDEX_TARGET", "文件目录目标不是普通文件")
        with destination.open("r", encoding="utf-8") as stream:
            if stream.read(len(_MARKER)) != _MARKER:
                raise _failure("INDEX_TARGET", "文件目录.html 已被其他文件占用，不会覆盖用户文件")
    inspection = inspect_storage(store, legacy_root)
    repository = RuntimeRepository(store)
    latest = {
        repository.get_result(item.result_id).node_run_id: item for item in repository.list_latest()
    }
    snapshot = store.load()
    rows: list[str] = []
    artifacts_seen: set[str] = set()
    for run_number, run in enumerate(state.runs, start=1):
        number = storage.layout_state.runs.get(run.run_id, run_number)
        nodes = {item.node_id: item for item in run.graph_snapshot.nodes}
        for attempt in run.node_runs:
            instance = nodes[attempt.node_id]
            location = storage.layout_state.nodes.get(attempt.node_id)
            label = instance.node_id if location is None else location.relative_dir
            declaration = _declaration(instance.parameters) or "无工具或模型声明"
            if not attempt.output_artifact_ids:
                rows.append(
                    "<tr>"
                    + "".join(
                        f"<td>{value}</td>"
                        for value in (
                            _cell(label),
                            _cell(f"R{number:03d}-A{attempt.attempt:03d}"),
                            _cell(instance.type_id),
                            _cell(declaration),
                            _cell(attempt.state.value),
                            _cell(attempt.started_at or "未开始"),
                            _cell(attempt.ended_at or "未结束"),
                            "尚无登记成果",
                            "—",
                            "—",
                            _file_link(attempt.log_path or attempt.work_dir, root),
                        )
                    )
                    + "</tr>"
                )
            for artifact_id in attempt.output_artifact_ids:
                if artifact_id in artifacts_seen:
                    continue
                artifacts_seen.add(artifact_id)
                artifact = repository.get_artifact(artifact_id)
                result = latest.get(artifact.producer_node_run_id)
                validity = (
                    "历史结果" if result is None else "已失效" if result.stale else "当前有效结果"
                )
                frames = artifact.media_info.get("frame_count", "未登记")
                frame_range = (
                    "—"
                    if artifact.frame_range is None
                    else (f"[{artifact.frame_range.start_frame}, {artifact.frame_range.end_frame})")
                )
                rows.append(
                    "<tr>"
                    + "".join(
                        f"<td>{value}</td>"
                        for value in (
                            _cell(label),
                            _cell(f"R{number:03d}-A{attempt.attempt:03d}"),
                            _cell(instance.type_id),
                            _cell(declaration),
                            _cell(attempt.state.value),
                            _cell(attempt.started_at or "未开始"),
                            _cell(attempt.ended_at or "未结束"),
                            _cell(validity),
                            _cell(frames),
                            _cell(frame_range),
                            _file_link(artifact.path, root),
                        )
                    )
                    + "</tr>"
                )
    dependencies = (
        "".join(
            f"<li>{_cell(item.path)} — {_cell(item.state)}</li>"
            for item in inspection.external_dependencies
        )
        or "<li>已登记 Artifact 范围内未发现根外依赖；不覆盖未运行节点或插件隐含路径。</li>"
    )
    missing = "".join(
        f"<li>{_cell(item.path)} — {_cell(item.state)}</li>" for item in inspection.missing
    )
    html = (
        _MARKER
        + '\n<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        + (
            '<meta http-equiv="Content-Security-Policy" '
            "content=\"default-src 'none'; style-src 'unsafe-inline'\">"
            "<title>ZNIKU 文件目录</title><style>"
            "body{font:15px system-ui;margin:2rem;color:#172033}"
            "table{border-collapse:collapse}td,th{border:1px solid #ccd3df;"
            "padding:.55rem;text-align:left}"
            "td{vertical-align:top}a{color:#174ea6}h1{font-size:1.6rem}</style><body>"
        )
        + f"<h1>{_cell(snapshot.project.name)} · 文件目录</h1>"
        + (
            "<p>此文件由 .zniku 工程记录重建，只用于找文件和归档浏览，不是运行或完整归档的权威。"
            "当前有效仅表示工程最新结果未标记 stale；不证明文件内容、外部模型或主观画质。</p>"
            "<p>工具与模型均为声明值。重新运行、改名和删除节点不会自动删除历史媒体。</p>"
            "<p>历史交接说明和日志按原字节保留，其中的绝对路径可能指向迁移前位置；"
            "找当前文件请以本目录或工程内的正式文件定位为准，不能重新提交旧终态交接。</p>"
            "<table><thead><tr>"
        )
        + "".join(
            f"<th>{label}</th>"
            for label in (
                "章节 / 任务",
                "批次",
                "节点类型",
                "工具 / 模型声明",
                "状态",
                "开始",
                "结束",
                "工程结果",
                "帧数",
                "帧区间",
                "文件",
            )
        )
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
        + (
            f"<h2>仍在数据目录外的依赖</h2><ul>{dependencies}</ul>"
            f"<h2>缺失或不可读的登记文件</h2><ul>{missing or '<li>未发现</li>'}</ul>"
            "<p>请同时保存 .zniku、.data 和外部依赖。其他磁盘的"
            "原素材及最终成片不会自动复制或重新定位。</p>"
            "</body></html>"
        )
    )
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=root,
            prefix=".zniku-index-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(html)
            stream.flush()
            os.fsync(stream.fileno())
        if _state(store) != state:
            raise _failure("CHANGED", "工程已改变，未替换文件目录，请重新生成")
        _safe_path(destination, missing=True)
        current_identity = _identity(destination) if destination.exists() else None
        if current_identity != destination_identity:
            raise _failure("CHANGED", "文件目录目标在生成期间已变化，不会覆盖用户文件")
        os.replace(temporary, destination)
        temporary = None
    except OSError as error:
        raise _failure(
            "INDEX_WRITE", "文件目录未生成，请检查数据目录权限和可用空间；未更改工程或媒体"
        ) from error
    finally:
        if temporary is not None:
            try:
                _safe_path(temporary)
                temporary.unlink(missing_ok=True)
            except (OSError, ProjectStoreError):
                # 无法确认仍是原临时位置时保留，不跨联接或目录替换清理其他数据。
                pass
    return StorageIndexResult(
        project_session_id=project_session_id,
        expected_storage_revision=expected_storage_revision,
        path=str(destination),
        artifact_count=len(artifacts_seen),
        external_dependency_count=len(inspection.external_dependencies),
        warnings=inspection.warnings,
    )
