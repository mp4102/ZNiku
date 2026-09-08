"""建立并校验 ZNIKU Studio v0.3.0 的 Presentation catalog。

内建条目必须与实际 ``NodeDefinition`` 的 exact identity、参数 Schema 和端口逐项绑定，任何漂移都
阻止 catalog 构建。第三方条目采用隔离语义：无效条目只产生非阻塞 diagnostic，缺失条目由 Studio
回退到通用 Schema 表单；这两种情况都不能改变 Graph 或 Runtime 的正式结论。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from pydantic import ValidationError

from zniku.avenhance_v27 import (
    ATOMIC_SPLIT_TYPE_PREFIX,
    AV27_NODE_VERSION,
    ENHANCEMENT_TYPE_ID,
    FINAL_MUX_TYPE_ID,
    FRAME_INTERPOLATION_TYPE_ID,
    MERGE_VIDEO_TYPE_ID,
    MOSAIC_RESTORATION_TYPE_ID,
    PROGRAM_ENCODE_TYPE_ID,
    SOURCE_ADMISSION_TYPE_ID,
    SOURCE_PROGRAM_TYPE_ID,
    built_in_av27_definitions,
    is_av27_definition,
)
from zniku.graph import NodeDefinition, PortSpec
from zniku.media import built_in_media_definitions
from zniku.media.definitions import is_supported_output_file_definition

from .models import (
    PRESENTATION_CONTRACT_VERSION,
    PRESENTATION_LOCALE,
    CategoryPresentation,
    ControlHint,
    EnumLabel,
    IconToken,
    NodePresentation,
    PaletteLevel,
    ParameterGroupPresentation,
    ParameterImportance,
    ParameterPresentation,
    PickerPresentation,
    PortPresentation,
    PresentationCatalog,
    PresentationCatalogResolution,
    PresentationDiagnostic,
    decode_json_pointer,
)


class PresentationCatalogError(ValueError):
    """表示内建 Presentation 或 definition catalog 漂移，必须失败关闭。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class _NodeMetadata:
    title: str
    description: str
    category_id: str
    icon_token: IconToken
    palette_level: PaletteLevel
    keywords: tuple[str, ...]
    card_summary_names: tuple[str, ...] = ()


_CATEGORIES = (
    CategoryPresentation(
        category_id="input",
        title="导入素材",
        description="选择只读源媒体并建立工作流入口。",
        order=10,
    ),
    CategoryPresentation(
        category_id="transform",
        title="画面处理",
        description="转换、增强和补帧等视频处理步骤。",
        order=20,
    ),
    CategoryPresentation(
        category_id="structure",
        title="拆分与合并",
        description="按顺序拆分或汇合视频分支。",
        order=30,
    ),
    CategoryPresentation(
        category_id="delivery",
        title="编码与输出",
        description="编码、封装并发布最终文件。",
        order=40,
    ),
    CategoryPresentation(
        category_id="av27",
        title="AVEnhanceFlow v2.7.0",
        description="由模板生成的精确 AVEnhanceFlow v2.7.0 节点。",
        order=50,
    ),
)

_GENERIC_METADATA: dict[tuple[str, str], _NodeMetadata] = {
    ("zniku.media.source.video", "0.2.0"): _NodeMetadata(
        "导入视频",
        "选择一个本机视频作为只读工作流输入。",
        "input",
        IconToken.VIDEO,
        PaletteLevel.PRIMARY,
        ("视频", "导入", "素材", "source"),
        ("source_path",),
    ),
    ("zniku.media.source.audio", "0.2.0"): _NodeMetadata(
        "导入音频",
        "选择一个本机音频作为只读工作流输入。",
        "input",
        IconToken.AUDIO,
        PaletteLevel.SECONDARY,
        ("音频", "导入", "素材", "source"),
        ("source_path",),
    ),
    ("zniku.media.source.media", "0.2.0"): _NodeMetadata(
        "导入媒体",
        "选择一个包含媒体流的本机文件作为只读输入。",
        "input",
        IconToken.MEDIA,
        PaletteLevel.SECONDARY,
        ("媒体", "导入", "素材", "source"),
        ("source_path",),
    ),
    ("zniku.media.video_transform.automatic", "0.2.0"): _NodeMetadata(
        "视频转换",
        "自动完成直通、缩放或帧率转换。",
        "transform",
        IconToken.TRANSFORM,
        PaletteLevel.PRIMARY,
        ("转换", "缩放", "帧率", "transform"),
        ("operation", "width", "height", "frame_rate"),
    ),
    ("zniku.media.video_transform.mr.external", "0.2.0"): _NodeMetadata(
        "马赛克修复",
        "在外部工具中完成 Mosaic Restoration 并显式提交结果。",
        "transform",
        IconToken.TRANSFORM,
        PaletteLevel.SECONDARY,
        ("马赛克", "修复", "MR", "external"),
        ("tool", "model", "frame_relation"),
    ),
    ("zniku.media.video_transform.enhancement.external", "0.2.0"): _NodeMetadata(
        "画质增强",
        "在外部工具中完成 Enhancement 并显式提交结果。",
        "transform",
        IconToken.TRANSFORM,
        PaletteLevel.PRIMARY,
        ("增强", "画质", "Enhancement", "external"),
        ("tool", "model", "expected_width", "expected_height"),
    ),
    ("zniku.media.video_transform.fi.external", "0.2.0"): _NodeMetadata(
        "视频补帧",
        "在外部工具中完成 Frame Interpolation 并显式提交结果。",
        "transform",
        IconToken.TRANSFORM,
        PaletteLevel.PRIMARY,
        ("补帧", "插帧", "FI", "external"),
        ("tool", "model", "expected_frame_rate"),
    ),
    ("zniku.media.split_video.2", "0.2.0"): _NodeMetadata(
        "拆分视频",
        "按精确半开帧区间把视频拆成两个有序分段。",
        "structure",
        IconToken.SPLIT,
        PaletteLevel.PRIMARY,
        ("拆分", "分段", "帧", "split"),
        ("segments",),
    ),
    ("zniku.media.merge_video", "0.2.0"): _NodeMetadata(
        "合并视频",
        "按连接顺序合并多个视频输入。",
        "structure",
        IconToken.MERGE,
        PaletteLevel.PRIMARY,
        ("合并", "顺序", "merge"),
    ),
    ("zniku.media.encode_video", "0.2.0"): _NodeMetadata(
        "编码视频",
        "使用明确的编码器参数产生视频文件。",
        "delivery",
        IconToken.ENCODE,
        PaletteLevel.SECONDARY,
        ("编码", "压缩", "codec", "encode"),
        ("codec", "preset", "crf", "pixel_format"),
    ),
    ("zniku.media.mux_media", "0.2.0"): _NodeMetadata(
        "封装音视频",
        "把视频和可选有序音轨封装为媒体文件。",
        "delivery",
        IconToken.MUX,
        PaletteLevel.SECONDARY,
        ("封装", "音轨", "mux"),
    ),
    ("zniku.media.output_file.video", "0.2.0"): _NodeMetadata(
        "输出视频",
        "复制或引用上游视频并明确控制覆盖行为。",
        "delivery",
        IconToken.OUTPUT,
        PaletteLevel.PRIMARY,
        ("输出", "发布", "视频", "output"),
        ("mode", "target_path", "overwrite"),
    ),
    ("zniku.media.output_file.audio", "0.2.0"): _NodeMetadata(
        "输出音频",
        "复制或引用上游音频并明确控制覆盖行为。",
        "delivery",
        IconToken.OUTPUT,
        PaletteLevel.SECONDARY,
        ("输出", "发布", "音频", "output"),
        ("mode", "target_path", "overwrite"),
    ),
    ("zniku.media.output_file.media", "0.2.0"): _NodeMetadata(
        "输出媒体",
        "复制或引用上游媒体并明确控制覆盖行为。",
        "delivery",
        IconToken.OUTPUT,
        PaletteLevel.SECONDARY,
        ("输出", "发布", "媒体", "output"),
        ("mode", "target_path", "overwrite"),
    ),
}

_GENERIC_DEFINITIONS = {
    (definition.type_id, definition.version): definition
    for definition in built_in_media_definitions()
}

_AV27_METADATA: dict[tuple[str, str], _NodeMetadata] = {
    (SOURCE_PROGRAM_TYPE_ID, AV27_NODE_VERSION): _NodeMetadata(
        "导入节目源",
        "导入并登记 AVEnhanceFlow v2.7.0 原始节目媒体。",
        "av27",
        IconToken.SOURCE,
        PaletteLevel.ADVANCED,
        ("AVEnhanceFlow", "节目源", "source"),
        ("source_ordinal", "label", "source_path"),
    ),
    (SOURCE_ADMISSION_TYPE_ID, AV27_NODE_VERSION): _NodeMetadata(
        "检查节目源",
        "核对有序节目源身份并生成后续步骤使用的准入信息。",
        "av27",
        IconToken.CHECK,
        PaletteLevel.ADVANCED,
        ("AVEnhanceFlow", "准入", "检查"),
        ("source_mode", "sources"),
    ),
    (MOSAIC_RESTORATION_TYPE_ID, AV27_NODE_VERSION): _NodeMetadata(
        "外部马赛克修复",
        "按 AVEnhanceFlow v2.7.0 合同完成人工 MR 交接。",
        "av27",
        IconToken.TRANSFORM,
        PaletteLevel.ADVANCED,
        ("AVEnhanceFlow", "MR", "马赛克修复"),
        ("model_name", "model_version"),
    ),
    (ENHANCEMENT_TYPE_ID, AV27_NODE_VERSION): _NodeMetadata(
        "外部逐段增强",
        "按 leaf 身份和目标几何完成人工画质增强交接。",
        "av27",
        IconToken.TRANSFORM,
        PaletteLevel.ADVANCED,
        ("AVEnhanceFlow", "Enhancement", "增强"),
        ("model_name", "expected_output_geometry", "expected_frames"),
    ),
    (MERGE_VIDEO_TYPE_ID, AV27_NODE_VERSION): _NodeMetadata(
        "合并章节分段",
        "按 leaf ordinal 合并一个章节的全部增强结果。",
        "av27",
        IconToken.MERGE,
        PaletteLevel.ADVANCED,
        ("AVEnhanceFlow", "章节", "合并"),
        ("chapter_ordinal", "expected_frames"),
    ),
    (FRAME_INTERPOLATION_TYPE_ID, AV27_NODE_VERSION): _NodeMetadata(
        "外部章节补帧",
        "对已合并章节执行人工补帧并验证目标帧数。",
        "av27",
        IconToken.TRANSFORM,
        PaletteLevel.ADVANCED,
        ("AVEnhanceFlow", "FI", "补帧"),
        ("chapter_ordinal", "expected_output_frames", "source_fps"),
    ),
    (PROGRAM_ENCODE_TYPE_ID, AV27_NODE_VERSION): _NodeMetadata(
        "连续节目编码",
        "按章节顺序执行一次连续 Main10 节目编码。",
        "av27",
        IconToken.ENCODE,
        PaletteLevel.ADVANCED,
        ("AVEnhanceFlow", "Main10", "编码"),
        ("encoder", "source_fps", "expected_geometry"),
    ),
    (FINAL_MUX_TYPE_ID, AV27_NODE_VERSION): _NodeMetadata(
        "最终音视频封装",
        "将节目视频与原始音轨封装为最终 MKV。",
        "av27",
        IconToken.MUX,
        PaletteLevel.ADVANCED,
        ("AVEnhanceFlow", "音轨", "最终封装"),
        ("source_mode", "expected_program_frames", "mr_mode"),
    ),
}

_PARAMETER_LABELS: dict[str, str] = {
    "actual_scale_factor": "实际放大倍数",
    "chapter_id": "章节标识",
    "chapter_ordinal": "章节顺序",
    "chapter_selector": "章节切分方式",
    "chapters": "章节计划",
    "codec": "视频编码器",
    "crf": "质量系数 CRF",
    "create_parent": "输出时创建子文件夹",
    "encoder": "编码设备",
    "expected_fps": "预期帧率",
    "expected_frame_rate": "预期帧率",
    "expected_frames": "预期帧数",
    "expected_geometry": "预期画面尺寸",
    "expected_height": "预期高度",
    "expected_input_frames": "预期输入帧数",
    "expected_input_geometry": "预期输入尺寸",
    "expected_output_frames": "预期输出帧数",
    "expected_output_geometry": "预期输出尺寸",
    "expected_program_frames": "预期节目帧数",
    "expected_signal": "预期视频信号",
    "expected_width": "预期宽度",
    "frame_rate": "目标帧率",
    "frame_relation": "输入输出帧数关系",
    "height": "高度",
    "label": "素材名称",
    "leaf_duration_minutes": "分段时长",
    "leaf_id": "分段标识",
    "leaf_ordinal": "分段顺序",
    "mode": "输出方式",
    "model": "模型",
    "model_name": "模型名称",
    "model_version": "模型版本",
    "mr_mode": "马赛克修复方式",
    "operation": "转换方式",
    "output_root": "输出根目录",
    "overwrite": "允许覆盖",
    "pixel_format": "像素格式",
    "planned_admission_artifact_id": "准入结果标识",
    "planned_effective_video_artifact_ids": "有效视频标识",
    "preset": "编码速度",
    "protected_paths": "禁止覆盖的源文件",
    "require_frame_count_equal": "保持帧数不变",
    "segments": "分段范围",
    "source_fps": "源帧率",
    "source_mode": "素材组织方式",
    "source_ordinal": "素材顺序",
    "source_path": "源文件",
    "sources": "节目源清单",
    "target_path": "输出文件",
    "tool": "外部工具",
    "tool_version": "工具版本",
    "width": "宽度",
}

_ENUM_LABELS: dict[str, str] = {
    "any": "不限制",
    "copy": "复制到新文件",
    "cpu": "CPU 编码",
    "double": "帧数加倍",
    "equal": "帧数保持一致",
    "external": "外部人工处理",
    "fast": "快速",
    "faster": "较快",
    "ffv1": "FFV1 无损",
    "frame_rate": "转换帧率",
    "gpu": "GPU 编码",
    "identity": "保持原样",
    "libx264": "H.264 / x264",
    "libx265": "H.265 / x265",
    "medium": "均衡",
    "off": "不启用",
    "pre_chaptered": "已分章素材",
    "program": "完整节目",
    "reference": "引用原文件",
    "scale": "调整尺寸",
    "slow": "慢速高质量",
    "superfast": "超快",
    "ultrafast": "极速",
    "veryfast": "很快",
    "yuv420p": "8-bit 4:2:0",
    "yuv420p10le": "10-bit 4:2:0",
    "yuv422p10le": "10-bit 4:2:2",
}

_BINDING_PARAMETERS = {
    "chapter_id",
    "chapter_ordinal",
    "chapters",
    "leaf_id",
    "leaf_ordinal",
    "planned_admission_artifact_id",
    "planned_effective_video_artifact_ids",
    "segments",
    "source_ordinal",
    "sources",
}
_QUALITY_PARAMETERS = {
    "actual_scale_factor",
    "codec",
    "crf",
    "encoder",
    "expected_fps",
    "expected_frame_rate",
    "expected_frames",
    "expected_geometry",
    "expected_height",
    "expected_input_frames",
    "expected_input_geometry",
    "expected_output_frames",
    "expected_output_geometry",
    "expected_program_frames",
    "expected_signal",
    "expected_width",
    "frame_rate",
    "frame_relation",
    "height",
    "pixel_format",
    "preset",
    "require_frame_count_equal",
    "source_fps",
    "width",
}
_GROUP_METADATA = {
    "basic": ("基础设置", 10),
    "quality": ("质量与输出", 20),
    "binding": ("高级绑定", 30),
}
_ADVANCED_PARAMETERS = _BINDING_PARAMETERS | {
    "output_root",
    "protected_paths",
    "expected_frames",
    "expected_input_frames",
    "expected_output_frames",
    "expected_program_frames",
    "expected_signal",
}


def _builtin_metadata(definition: NodeDefinition) -> _NodeMetadata:
    key = (definition.type_id, definition.version)
    if definition.type_id.startswith("zniku.media."):
        expected = _GENERIC_DEFINITIONS.get(key)
        # 历史工程仍持有原始 OutputFile definition；只接受已冻结的旧/新完整 shape，
        # 不能为了显示兼容而替换旧 Schema，或容忍任意 executor/参数约束漂移。
        if expected is None or (
            expected != definition and not is_supported_output_file_definition(definition)
        ):
            raise PresentationCatalogError(
                "E_PRESENTATION_BUILTIN_DEFINITION_DRIFT",
                "generic media definition 与正式内建结构不一致："
                f"{definition.type_id}@{definition.version}",
            )
    elif definition.type_id.startswith("zniku.avenhance.v27.") and not is_av27_definition(
        definition
    ):
        raise PresentationCatalogError(
            "E_PRESENTATION_BUILTIN_DEFINITION_DRIFT",
            f"AVEnhanceFlow v2.7.0 definition 与正式内建结构不一致："
            f"{definition.type_id}@{definition.version}",
        )
    metadata = _GENERIC_METADATA.get(key) or _AV27_METADATA.get(key)
    if metadata is not None:
        return metadata
    if definition.version == AV27_NODE_VERSION and definition.type_id.startswith(
        ATOMIC_SPLIT_TYPE_PREFIX
    ):
        return _NodeMetadata(
            "拆分为处理分段",
            "按 Python 已规划的章节与 leaf 范围原子拆分节目视频。",
            "av27",
            IconToken.SPLIT,
            PaletteLevel.ADVANCED,
            ("AVEnhanceFlow", "AtomicSplit", "分段"),
            ("source_mode", "leaf_duration_minutes", "segments"),
        )
    raise PresentationCatalogError(
        "E_PRESENTATION_BUILTIN_IDENTITY",
        f"没有内建展示声明：{definition.type_id}@{definition.version}",
    )


def _is_builtin_definition(definition: NodeDefinition) -> bool:
    """按受控 namespace 识别必须失败关闭的仓库内 definition。"""

    return definition.type_id.startswith(("zniku.media.", "zniku.avenhance.v27."))


def _schema_properties(definition: NodeDefinition) -> Mapping[str, object]:
    properties = definition.parameter_schema.get("properties")
    if not isinstance(properties, Mapping):
        raise PresentationCatalogError(
            "E_PRESENTATION_PARAMETER_SCHEMA",
            f"{definition.type_id}@{definition.version} 的 parameter_schema.properties 无效",
        )
    return cast(Mapping[str, object], properties)


def _parameter_group(name: str) -> str:
    if name in _BINDING_PARAMETERS:
        return "binding"
    if name in _QUALITY_PARAMETERS:
        return "quality"
    return "basic"


def _control_hint(name: str, schema: Mapping[str, object]) -> ControlHint:
    if name == "source_path":
        return ControlHint.FILE_PATH
    if name == "target_path":
        return ControlHint.SAVE_FILE
    if name == "output_root":
        return ControlHint.DIRECTORY_PATH
    if isinstance(schema.get("enum"), Sequence) and not isinstance(schema.get("enum"), str | bytes):
        return ControlHint.SELECT
    schema_type = schema.get("type")
    if schema_type == "boolean":
        return ControlHint.SWITCH
    if schema_type == "integer":
        return ControlHint.INTEGER
    if schema_type == "number":
        return ControlHint.NUMBER
    if schema_type == "string":
        return ControlHint.TEXT
    return ControlHint.AUTO


def _enum_labels(schema: Mapping[str, object]) -> tuple[EnumLabel, ...]:
    raw_values = schema.get("enum")
    if not isinstance(raw_values, Sequence) or isinstance(raw_values, str | bytes):
        return ()
    labels: list[EnumLabel] = []
    for value in raw_values:
        if not isinstance(value, str):
            raise PresentationCatalogError(
                "E_PRESENTATION_BUILTIN_ENUM",
                "当前内建 Presentation 只允许为 string enum 提供标签",
            )
        label = _ENUM_LABELS.get(value)
        if label is None:
            raise PresentationCatalogError(
                "E_PRESENTATION_BUILTIN_ENUM",
                f"内建 enum 尚无中文标签：{value!r}",
            )
        labels.append(EnumLabel(value=value, label=label))
    return tuple(labels)


def _parameter_presentations(
    definition: NodeDefinition,
) -> tuple[tuple[ParameterGroupPresentation, ...], tuple[ParameterPresentation, ...]]:
    parameters: list[ParameterPresentation] = []
    groups_in_use: set[str] = set()
    for order, (name, raw_schema) in enumerate(_schema_properties(definition).items(), start=1):
        label = _PARAMETER_LABELS.get(name)
        if label is None:
            raise PresentationCatalogError(
                "E_PRESENTATION_BUILTIN_PARAMETER",
                f"内建参数尚无展示声明：{definition.type_id}@{definition.version} /{name}",
            )
        if not isinstance(raw_schema, Mapping):
            raise PresentationCatalogError(
                "E_PRESENTATION_PARAMETER_SCHEMA",
                f"/{name} 的 Schema 必须是 object",
            )
        schema = cast(Mapping[str, object], raw_schema)
        group_id = _parameter_group(name)
        groups_in_use.add(group_id)
        control_hint = _control_hint(name, schema)
        picker = None
        if control_hint is ControlHint.FILE_PATH:
            picker = PickerPresentation(
                extensions=(".mkv", ".mp4", ".mov", ".avi", ".wav", ".flac", ".m4a")
            )
        elif control_hint is ControlHint.SAVE_FILE:
            picker = PickerPresentation(extensions=(".mkv", ".mp4", ".mov", ".wav", ".flac"))
        elif control_hint is ControlHint.DIRECTORY_PATH:
            picker = PickerPresentation()
        parameters.append(
            ParameterPresentation(
                parameter_pointer=f"/{name.replace('~', '~0').replace('/', '~1')}",
                label=label,
                group_id=group_id,
                order=order,
                importance=(
                    ParameterImportance.ADVANCED
                    if name in _ADVANCED_PARAMETERS
                    else ParameterImportance.PRIMARY
                ),
                control_hint=control_hint,
                unit="分钟" if name == "leaf_duration_minutes" else None,
                placeholder="例如 30000/1001"
                if name in {"frame_rate", "expected_fps", "expected_frame_rate", "source_fps"}
                else None,
                enum_labels=_enum_labels(schema),
                picker=picker,
            )
        )
    groups = tuple(
        ParameterGroupPresentation(group_id=group_id, title=title, order=order)
        for group_id, (title, order) in _GROUP_METADATA.items()
        if group_id in groups_in_use
    )
    return groups, tuple(parameters)


def _port_label(port: PortSpec, *, direction: str, ordinal: int) -> str:
    labels = {
        ("input", "video"): "输入视频",
        ("output", "video"): "输出视频",
        ("input", "audio"): "输入音轨",
        ("output", "audio"): "输出音频",
        ("input", "in"): "输入",
        ("output", "out"): "媒体输出",
        ("output", "source_media"): "节目源媒体",
        ("input", "videos"): "有序视频",
        ("input", "chapters"): "有序章节",
        ("input", "sources"): "有序节目源",
        ("input", "gate"): "准入信息",
        ("output", "gate"): "准入结果",
        ("output", "media"): "封装媒体",
        ("output", "published"): "已发布文件",
    }
    label = labels.get((direction, port.port_id))
    if label is not None:
        return label
    if port.port_id in {"A", "B", "C"}:
        return f"分段 {port.port_id}"
    if port.port_id.startswith("leaf-"):
        return f"处理分段 {ordinal + 1}"
    return port.port_id


def _port_presentations(definition: NodeDefinition) -> tuple[PortPresentation, ...]:
    values: list[PortPresentation] = []
    for direction, ports in (
        ("input", definition.input_ports),
        ("output", definition.output_ports),
    ):
        values.extend(
            PortPresentation(
                direction=cast(Any, direction),
                port_id=port.port_id,
                label=_port_label(port, direction=direction, ordinal=ordinal),
            )
            for ordinal, port in enumerate(ports)
        )
    return tuple(values)


def _build_builtin_node(definition: NodeDefinition) -> NodePresentation:
    metadata = _builtin_metadata(definition)
    groups, parameters = _parameter_presentations(definition)
    summary_paths = tuple(f"/{name}" for name in metadata.card_summary_names)
    presentation = NodePresentation(
        type_id=definition.type_id,
        definition_version=definition.version,
        title=metadata.title,
        description=metadata.description,
        category_id=metadata.category_id,
        icon_token=metadata.icon_token,
        palette_level=metadata.palette_level,
        keywords=metadata.keywords,
        parameter_groups=groups,
        parameters=parameters,
        ports=_port_presentations(definition),
        card_summary_paths=summary_paths,
    )
    validate_node_presentation(presentation, definition, require_complete=True)
    return presentation


def _canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def _resolve_parameter_pointer(
    definition: NodeDefinition,
    pointer: str,
) -> Mapping[str, object]:
    current: object = definition.parameter_schema
    for token in decode_json_pointer(pointer):
        if not isinstance(current, Mapping):
            raise PresentationCatalogError(
                "E_PRESENTATION_PARAMETER_POINTER",
                f"参数 pointer 不存在：{pointer}",
            )
        properties = current.get("properties")
        if isinstance(properties, Mapping) and token in properties:
            current = properties[token]
            continue
        if token == "0" or (token.isascii() and token.isdecimal() and not token.startswith("0")):
            index = int(token)
            prefix_items = current.get("prefixItems")
            if (
                isinstance(prefix_items, Sequence)
                and not isinstance(prefix_items, str | bytes)
                and index < len(prefix_items)
            ):
                current = prefix_items[index]
                continue
            items = current.get("items")
            if isinstance(items, Mapping):
                current = items
                continue
        raise PresentationCatalogError(
            "E_PRESENTATION_PARAMETER_POINTER",
            f"参数 pointer 不存在：{pointer}",
        )
    if not isinstance(current, Mapping):
        raise PresentationCatalogError(
            "E_PRESENTATION_PARAMETER_POINTER",
            f"参数 pointer 未绑定 Schema object：{pointer}",
        )
    return cast(Mapping[str, object], current)


def _control_matches_schema(control: ControlHint, schema: Mapping[str, object]) -> bool:
    if control is ControlHint.AUTO:
        return True
    schema_type = schema.get("type")
    if control in {
        ControlHint.TEXT,
        ControlHint.TEXTAREA,
        ControlHint.FILE_PATH,
        ControlHint.DIRECTORY_PATH,
        ControlHint.SAVE_FILE,
    }:
        return schema_type == "string"
    if control is ControlHint.FILE_PATHS:
        items = schema.get("items")
        return (
            schema_type == "array" and isinstance(items, Mapping) and items.get("type") == "string"
        )
    if control is ControlHint.INTEGER:
        return schema_type == "integer"
    if control is ControlHint.NUMBER:
        return schema_type in {"integer", "number"}
    if control is ControlHint.SLIDER:
        return schema_type in {"integer", "number"} and "minimum" in schema and "maximum" in schema
    if control is ControlHint.SWITCH:
        return schema_type == "boolean"
    if control in {ControlHint.SELECT, ControlHint.RADIO}:
        values = schema.get("enum")
        return isinstance(values, Sequence) and not isinstance(values, str | bytes)
    return False


def validate_node_presentation(
    presentation: NodePresentation,
    definition: NodeDefinition,
    *,
    require_complete: bool = False,
) -> None:
    """验证一条展示声明只引用 exact definition 已有参数与端口。"""

    if (presentation.type_id, presentation.definition_version) != (
        definition.type_id,
        definition.version,
    ):
        raise PresentationCatalogError(
            "E_PRESENTATION_DEFINITION_BINDING",
            "Presentation 必须绑定 exact type_id + definition_version",
        )
    group_ids = {group.group_id for group in presentation.parameter_groups}
    for parameter in presentation.parameters:
        if parameter.group_id not in group_ids:
            raise PresentationCatalogError(
                "E_PRESENTATION_PARAMETER_GROUP",
                f"{parameter.parameter_pointer} 引用未知 group：{parameter.group_id}",
            )
        schema = _resolve_parameter_pointer(definition, parameter.parameter_pointer)
        if not _control_matches_schema(parameter.control_hint, schema):
            raise PresentationCatalogError(
                "E_PRESENTATION_CONTROL_SCHEMA",
                f"{parameter.parameter_pointer} 的 control_hint 与正式 Schema 不兼容",
            )
        raw_enum = schema.get("enum")
        if parameter.enum_labels and (
            not isinstance(raw_enum, Sequence) or isinstance(raw_enum, str | bytes)
        ):
            raise PresentationCatalogError(
                "E_PRESENTATION_ENUM_SOURCE",
                f"{parameter.parameter_pointer} 没有正式 enum，不能提供 enum_labels",
            )
        allowed_enum = (
            {_canonical_json(value) for value in raw_enum}
            if isinstance(raw_enum, Sequence) and not isinstance(raw_enum, str | bytes)
            else set()
        )
        labels = tuple(_canonical_json(item.value) for item in parameter.enum_labels)
        if len(labels) != len(set(labels)) or any(item not in allowed_enum for item in labels):
            raise PresentationCatalogError(
                "E_PRESENTATION_ENUM_VALUE",
                f"{parameter.parameter_pointer} 的 enum_labels 引入了未知或重复 enum 值",
            )

    known_ports = {("input", port.port_id) for port in definition.input_ports} | {
        ("output", port.port_id) for port in definition.output_ports
    }
    presented_ports = {(port.direction, port.port_id) for port in presentation.ports}
    unknown_ports = presented_ports - known_ports
    if unknown_ports:
        raise PresentationCatalogError(
            "E_PRESENTATION_PORT_BINDING",
            f"Presentation 引用未知 port：{sorted(unknown_ports)!r}",
        )
    for pointer in presentation.card_summary_paths:
        _resolve_parameter_pointer(definition, pointer)

    if require_complete:
        expected_parameters = {f"/{name}" for name in _schema_properties(definition)}
        presented_parameters = {
            parameter.parameter_pointer for parameter in presentation.parameters
        }
        if presented_parameters != expected_parameters:
            raise PresentationCatalogError(
                "E_PRESENTATION_PARAMETER_COVERAGE",
                "内建 Presentation 必须覆盖全部顶层参数",
            )
        if presented_ports != known_ports:
            raise PresentationCatalogError(
                "E_PRESENTATION_PORT_COVERAGE",
                "内建 Presentation 必须覆盖全部实际 input/output ports",
            )


def _validate_catalog_categories(catalog: PresentationCatalog) -> None:
    categories = {category.category_id for category in catalog.categories}
    for node in catalog.nodes:
        if node.category_id not in categories:
            raise PresentationCatalogError(
                "E_PRESENTATION_CATEGORY_BINDING",
                f"{node.type_id}@{node.definition_version} 引用未知 category：{node.category_id}",
            )


def build_builtin_presentation_catalog(
    definitions: Iterable[NodeDefinition] | None = None,
) -> PresentationCatalog:
    """构建内建中文目录；任何身份、字段、端口或 Schema 漂移都失败关闭。"""

    values = (
        (*built_in_media_definitions(), *built_in_av27_definitions())
        if definitions is None
        else tuple(definitions)
    )
    if any(not isinstance(definition, NodeDefinition) for definition in values):
        raise PresentationCatalogError(
            "E_PRESENTATION_DEFINITION_CATALOG", "definition catalog 只能包含 NodeDefinition"
        )
    keys = tuple((definition.type_id, definition.version) for definition in values)
    if len(keys) != len(set(keys)):
        raise PresentationCatalogError(
            "E_PRESENTATION_DEFINITION_DUPLICATE", "definition catalog 不得重复 exact identity"
        )
    catalog = PresentationCatalog(
        categories=_CATEGORIES,
        nodes=tuple(_build_builtin_node(definition) for definition in values),
    )
    _validate_catalog_categories(catalog)
    return catalog


def _diagnostic(
    code: str,
    message: str,
    *,
    reference: str,
    node: NodePresentation | None = None,
) -> PresentationDiagnostic:
    return PresentationDiagnostic(
        code=code,
        message=message[:4096] or "Presentation 条目无效",
        type_id=node.type_id if node is not None else None,
        definition_version=node.definition_version if node is not None else None,
        reference=reference,
    )


def _parse_third_party_catalog(
    payload: object,
    *,
    index: int,
) -> tuple[
    tuple[CategoryPresentation, ...],
    tuple[NodePresentation, ...],
    tuple[PresentationDiagnostic, ...],
]:
    reference = f"third_party[{index}]"
    if isinstance(payload, PresentationCatalog):
        raw: Mapping[str, object] = payload.model_dump(mode="python")
    elif isinstance(payload, Mapping):
        raw = cast(Mapping[str, object], payload)
    else:
        return (
            (),
            (),
            (
                _diagnostic(
                    "W_PRESENTATION_CATALOG_INVALID",
                    "第三方 Presentation catalog 必须是 object，已隔离",
                    reference=reference,
                ),
            ),
        )

    allowed_keys = {"contract_version", "locale", "categories", "nodes"}
    if set(raw) != allowed_keys:
        return (
            (),
            (),
            (
                _diagnostic(
                    "W_PRESENTATION_CATALOG_INVALID",
                    "第三方 Presentation catalog 字段不完整或含未知字段，已隔离",
                    reference=reference,
                ),
            ),
        )
    if (
        raw.get("contract_version") != PRESENTATION_CONTRACT_VERSION
        or raw.get("locale") != PRESENTATION_LOCALE
    ):
        return (
            (),
            (),
            (
                _diagnostic(
                    "W_PRESENTATION_CATALOG_VERSION",
                    "第三方 Presentation catalog 版本或 locale 不受支持，已隔离",
                    reference=reference,
                ),
            ),
        )
    raw_categories = raw.get("categories")
    raw_nodes = raw.get("nodes")
    if not isinstance(raw_categories, list | tuple) or not isinstance(raw_nodes, list | tuple):
        return (
            (),
            (),
            (
                _diagnostic(
                    "W_PRESENTATION_CATALOG_INVALID",
                    "第三方 categories/nodes 必须是 array，已隔离",
                    reference=reference,
                ),
            ),
        )

    categories: list[CategoryPresentation] = []
    nodes: list[NodePresentation] = []
    diagnostics: list[PresentationDiagnostic] = []
    category_ids: set[str] = set()
    for item_index, item in enumerate(raw_categories):
        item_reference = f"{reference}.categories[{item_index}]"
        try:
            category = CategoryPresentation.model_validate_json(_canonical_json(item))
        except (TypeError, ValueError, ValidationError) as error:
            diagnostics.append(
                _diagnostic(
                    "W_PRESENTATION_CATEGORY_INVALID",
                    f"第三方 category 无效，已隔离：{error}",
                    reference=item_reference,
                )
            )
            continue
        if category.category_id in category_ids:
            diagnostics.append(
                _diagnostic(
                    "W_PRESENTATION_CATEGORY_DUPLICATE",
                    "第三方 category_id 重复，重复条目已隔离",
                    reference=item_reference,
                )
            )
            continue
        category_ids.add(category.category_id)
        categories.append(category)
    for item_index, item in enumerate(raw_nodes):
        item_reference = f"{reference}.nodes[{item_index}]"
        try:
            nodes.append(NodePresentation.model_validate_json(_canonical_json(item)))
        except (TypeError, ValueError, ValidationError) as error:
            diagnostics.append(
                _diagnostic(
                    "W_PRESENTATION_NODE_INVALID",
                    f"第三方 node presentation 无效，已隔离：{error}",
                    reference=item_reference,
                )
            )
    return tuple(categories), tuple(nodes), tuple(diagnostics)


def resolve_presentation_catalog(
    definitions: Iterable[NodeDefinition],
    *,
    third_party_catalogs: Iterable[object] = (),
) -> PresentationCatalogResolution:
    """合并内建与第三方展示；内建错误阻断启动，第三方错误逐条隔离。"""

    definition_values = tuple(definitions)
    if any(not isinstance(definition, NodeDefinition) for definition in definition_values):
        raise PresentationCatalogError(
            "E_PRESENTATION_DEFINITION_CATALOG", "definition catalog 只能包含 NodeDefinition"
        )
    definition_map = {
        (definition.type_id, definition.version): definition for definition in definition_values
    }
    if len(definition_map) != len(definition_values):
        raise PresentationCatalogError(
            "E_PRESENTATION_DEFINITION_DUPLICATE", "definition catalog 不得重复 exact identity"
        )

    builtins = tuple(
        definition for definition in definition_values if _is_builtin_definition(definition)
    )
    builtin_catalog = build_builtin_presentation_catalog(builtins)
    categories: dict[str, CategoryPresentation] = {
        category.category_id: category for category in builtin_catalog.categories
    }
    nodes: dict[tuple[str, str], NodePresentation] = {
        (node.type_id, node.definition_version): node for node in builtin_catalog.nodes
    }
    diagnostics: list[PresentationDiagnostic] = []

    for catalog_index, payload in enumerate(third_party_catalogs):
        parsed_categories, parsed_nodes, parse_diagnostics = _parse_third_party_catalog(
            payload,
            index=catalog_index,
        )
        diagnostics.extend(parse_diagnostics)
        for category_index, category in enumerate(parsed_categories):
            existing = categories.get(category.category_id)
            if existing is None:
                categories[category.category_id] = category
            elif existing != category:
                diagnostics.append(
                    _diagnostic(
                        "W_PRESENTATION_CATEGORY_CONFLICT",
                        f"第三方 category 与既有 {category.category_id!r} 冲突，已隔离",
                        reference=f"third_party[{catalog_index}].categories[{category_index}]",
                    )
                )
        for node_index, node in enumerate(parsed_nodes):
            reference = f"third_party[{catalog_index}].nodes[{node_index}]"
            key = (node.type_id, node.definition_version)
            if key in nodes:
                diagnostics.append(
                    _diagnostic(
                        "W_PRESENTATION_NODE_DUPLICATE",
                        "第三方 Presentation 与既有 exact identity 重复，已隔离",
                        reference=reference,
                        node=node,
                    )
                )
                continue
            definition = definition_map.get(key)
            if definition is None:
                diagnostics.append(
                    _diagnostic(
                        "W_PRESENTATION_DEFINITION_MISSING",
                        "找不到 exact NodeDefinition，Presentation 已隔离",
                        reference=reference,
                        node=node,
                    )
                )
                continue
            if node.category_id not in categories:
                diagnostics.append(
                    _diagnostic(
                        "W_PRESENTATION_CATEGORY_MISSING",
                        "Presentation 引用未知 category，已隔离",
                        reference=reference,
                        node=node,
                    )
                )
                continue
            try:
                validate_node_presentation(node, definition)
            except PresentationCatalogError as error:
                diagnostics.append(
                    _diagnostic(
                        f"W{error.code.removeprefix('E')}",
                        f"{error.message}；第三方条目已隔离",
                        reference=reference,
                        node=node,
                    )
                )
                continue
            nodes[key] = node

    for definition in definition_values:
        key = (definition.type_id, definition.version)
        if key not in nodes:
            diagnostics.append(
                _diagnostic(
                    "W_PRESENTATION_MISSING",
                    "节点缺少 Presentation；Studio 必须回退通用 Schema 展示",
                    reference="definition_catalog",
                    node=NodePresentation(
                        type_id=definition.type_id,
                        definition_version=definition.version,
                        title=definition.type_id,
                        description="仅用于缺失 Presentation diagnostic 的 exact identity 绑定。",
                        category_id="input",
                        icon_token=IconToken.MEDIA,
                        palette_level=PaletteLevel.ADVANCED,
                    ),
                )
            )

    ordered_nodes = tuple(
        nodes[key]
        for definition in definition_values
        if (key := (definition.type_id, definition.version)) in nodes
    )
    ordered_categories = tuple(
        sorted(categories.values(), key=lambda item: (item.order, item.category_id))
    )
    catalog = PresentationCatalog(categories=ordered_categories, nodes=ordered_nodes)
    _validate_catalog_categories(catalog)
    return PresentationCatalogResolution(catalog=catalog, diagnostics=tuple(diagnostics))


__all__ = [
    "PresentationCatalogError",
    "build_builtin_presentation_catalog",
    "resolve_presentation_catalog",
    "validate_node_presentation",
]
