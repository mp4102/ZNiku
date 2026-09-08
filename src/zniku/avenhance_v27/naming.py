"""将 AV27 图中的明确章节/叶片身份投影为 attempt 内的友好文件名。

本模块无文件 I/O，不猜测媒体标题，也不修改 Graph、参数、端口或 reuse/stale 签名。调用者提供
已经选定的媒体基名；不安全的名称失败关闭。自定义图缺少唯一命名依据时保留 definition 默认路径，
而不是为显示名称强加模板拓扑。旧 attempt 必须继续使用已经固化的 output target，不重新调用本模块。
"""

from __future__ import annotations

from collections import defaultdict

from zniku.graph import Graph, NodeDefinition, NodeInstance
from zniku.project.paths import MEDIA_BASENAME_MAX_UNITS, validate_filename_component
from zniku.runtime.runner import OutputPathSpec

from .definitions import (
    AV27_NODE_VERSION,
    ENHANCEMENT_TYPE_ID,
    FRAME_INTERPOLATION_TYPE_ID,
    MERGE_VIDEO_TYPE_ID,
    atomic_split_port_ids,
)
from .planner import SplitSegment, parse_split_segments
from .probe import Av27MediaError
from .profiles import atomic_split_count_from_type_id, is_av27_definition
from .template import excel_chapter_label


def validate_media_basename(value: str) -> str:
    """拒绝非法 Windows 文件组件；不删除字符、改写标题或推导媒体身份。

    基名最多 180 个 UTF-16 code units，为章节及阶段后缀预留空间；最终生成的每个组件仍独立
    检查 Windows 的 255 单位上限。扩展名是否已去除由调用方明确决定，本函数不自动截断点号。
    """

    _require_safe_component(value, max_units=MEDIA_BASENAME_MAX_UNITS)
    return value


def descriptive_output_paths(
    node: NodeInstance,
    definition: NodeDefinition,
    *,
    media_basename: str,
    graph: Graph,
) -> tuple[OutputPathSpec, ...]:
    """返回只含相对路径的输出覆盖；普通节点或无唯一叶片依据时返回空 tuple。

    Split 的 global port 保持不变，章内序号只由已声明 segments 顺序计算。Enhancement 必须通过
    唯一 video 入边绑定同一 Split segment；不得从节点 ID、实例别名或源文件名反推章节/叶片。
    """

    if (
        node.type_id != definition.type_id
        or node.definition_version != definition.version
        or not is_av27_definition(definition)
    ):
        return ()
    split_count = atomic_split_count_from_type_id(node.type_id)
    if split_count is not None:
        leaves = _split_leaves(node)
        if not leaves:
            return ()
        return tuple(
            OutputPathSpec(
                segment.port_id,
                _path(media_basename, segment.chapter_ordinal, f"leaf-{ordinal:04d}.mkv"),
            )
            for segment, ordinal in leaves
        )
    if node.type_id == ENHANCEMENT_TYPE_ID:
        leaf = _enhancement_leaf(node, graph)
        if leaf is None:
            return ()
        segment, ordinal = leaf
        return (
            OutputPathSpec(
                "video",
                _path(
                    media_basename, segment.chapter_ordinal, f"leaf-{ordinal:04d}.enhancement.mov"
                ),
            ),
        )
    if node.type_id in {MERGE_VIDEO_TYPE_ID, FRAME_INTERPOLATION_TYPE_ID}:
        chapter_ordinal = node.parameters.get("chapter_ordinal")
        if (
            isinstance(chapter_ordinal, bool)
            or not isinstance(chapter_ordinal, int)
            or chapter_ordinal < 0
        ):
            return ()
        suffix = "enhancement.mov" if node.type_id == MERGE_VIDEO_TYPE_ID else "enhancement.fi.mov"
        return (OutputPathSpec("video", _path(media_basename, chapter_ordinal, suffix)),)
    return ()


def _require_safe_component(value: str, *, max_units: int = 255) -> None:
    try:
        validate_filename_component(value, max_units=max_units)
    except ValueError as error:
        raise ValueError(
            "E_AV27_MEDIA_BASENAME: 名称必须是长度受限的安全 Windows 文件组件"
        ) from error


def _path(media_basename: str, chapter_ordinal: int, suffix: str) -> str:
    base = validate_media_basename(media_basename)
    label = excel_chapter_label(chapter_ordinal)
    name = f"{base}.{label}.{suffix}"
    _require_safe_component(label)
    _require_safe_component(name)
    return f"{label}/{name}"


def _split_leaves(node: NodeInstance) -> tuple[tuple[SplitSegment, int], ...]:
    count = atomic_split_count_from_type_id(node.type_id)
    raw = node.parameters.get("segments")
    if (
        node.definition_version != AV27_NODE_VERSION
        or count is None
        or not isinstance(raw, list | tuple)
        or len(raw) != count
    ):
        return ()
    try:
        segments = parse_split_segments(raw, expected_ports=atomic_split_port_ids(count))
    except Av27MediaError:
        # 这里只决定能否命名；真正的参数/媒体错误仍由原有 Graph/profile/节点 validator 报告。
        return ()
    chapter_ids: dict[int, str] = {}
    chapter_ordinals: dict[str, int] = {}
    counts: dict[int, int] = defaultdict(int)
    result: list[tuple[SplitSegment, int]] = []
    for segment in segments:
        if (
            chapter_ids.setdefault(segment.chapter_ordinal, segment.chapter_id)
            != segment.chapter_id
            or chapter_ordinals.setdefault(segment.chapter_id, segment.chapter_ordinal)
            != segment.chapter_ordinal
        ):
            return ()
        counts[segment.chapter_ordinal] += 1
        result.append((segment, counts[segment.chapter_ordinal]))
    return tuple(result)


def _enhancement_leaf(node: NodeInstance, graph: Graph) -> tuple[SplitSegment, int] | None:
    inputs = tuple(
        edge
        for edge in graph.edges
        if edge.target_node_id == node.node_id and edge.target_port_id == "video"
    )
    if len(inputs) != 1:
        return None
    edge = inputs[0]
    sources = tuple(item for item in graph.nodes if item.node_id == edge.source_node_id)
    if len(sources) != 1:
        return None
    for segment, ordinal in _split_leaves(sources[0]):
        if segment.port_id != edge.source_port_id:
            continue
        if any(
            node.parameters.get(field) != getattr(segment, field)
            for field in ("chapter_id", "chapter_ordinal", "leaf_id", "leaf_ordinal")
        ):
            return None
        return segment, ordinal
    return None
