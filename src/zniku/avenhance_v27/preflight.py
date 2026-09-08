"""纯检查 AVEnhanceFlow v2.7 模板 Graph 是否仍符合专用 profile。

本模块只读取普通 ``Project``/``ProjectSnapshot``、精确 ``NodeDefinition`` 与调用方显式提供的
最新 binding facts。它不打开 Project Store、不 probe 文件、不读取 Runtime Repository，也不修改
Graph。profile 不兼容只影响模板兼容标记；同一 Graph 是否是合法自由 DAG 仍由 Graph Core 决定。
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from fractions import Fraction
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from zniku.graph import Edge, GraphValidator, NodeDefinition, NodeInstance
from zniku.media.definitions import is_supported_output_file_definition, output_file_definition
from zniku.project import Project, ProjectSnapshot

from .definitions import (
    AV27_PROFILE_VERSION,
    ENHANCEMENT_TYPE_ID,
    FINAL_MUX_TYPE_ID,
    FRAME_INTERPOLATION_TYPE_ID,
    MERGE_VIDEO_TYPE_ID,
    MOSAIC_RESTORATION_TYPE_ID,
    PROGRAM_ENCODE_TYPE_ID,
    SOURCE_ADMISSION_TYPE_ID,
    SOURCE_PROGRAM_TYPE_ID,
    atomic_split_definition,
    enhancement_definition,
    final_mux_definition,
    frame_interpolation_definition,
    merge_video_definition,
    mosaic_restoration_definition,
    program_encode_definition,
    source_admission_definition,
    source_program_definition,
)
from .probe import Av27MediaError, canonical_fraction, parse_fraction
from .profiles import atomic_split_count_from_type_id
from .template import (
    Av27TemplateError,
    ResolvedChapterPlan,
    derive_leaf_plan,
    excel_chapter_label,
    parse_canonical_time,
)

type Av27ProfileStatus = Literal[
    "preparation-compatible",
    "expanded-compatible",
    "replan_required",
    "incompatible",
]
type Av27ProfilePhase = Literal["preparation", "expanded"]
type _EdgeKey = tuple[str, str, str, str, int | None]

_Identifier = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    ),
]
_OUTPUT_TYPE_ID = "zniku.media.output_file.media"
_OUTPUT_VERSION = "0.2.0"
_EXPANDED_TYPES = frozenset(
    {
        ENHANCEMENT_TYPE_ID,
        MERGE_VIDEO_TYPE_ID,
        FRAME_INTERPOLATION_TYPE_ID,
        PROGRAM_ENCODE_TYPE_ID,
        FINAL_MUX_TYPE_ID,
        _OUTPUT_TYPE_ID,
    }
)
_FORBIDDEN_PARAMETER_KEYS = frozenset(
    {
        "argv",
        "code",
        "command",
        "executable",
        "filter_script",
        "output_path",
        "output_paths",
        "python_code",
        "python_expression",
        "script",
        "shell",
        "shell_command",
    }
)
_EXPECTED_SIGNAL: dict[str, str | int] = {
    "color_primaries": "bt709",
    "color_transfer": "bt709",
    "color_space": "bt709",
    "color_range": "tv",
    "chroma_location": "left",
    "field_order": "progressive",
    "rotation": 0,
}
_RATE_LABELS = {
    Fraction(30, 1): "30p",
    Fraction(30000, 1001): "29p97",
    Fraction(60, 1): "60p",
    Fraction(60000, 1001): "59p94",
}
_WINDOWS_FORBIDDEN = frozenset('<>:"/\\|?*')
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)


class _PreflightModel(BaseModel):
    """冻结 preflight 输入输出，拒绝未知字段与宽松类型转换。"""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        strict=True,
        validate_default=True,
    )


class Av27ProfileDiagnostic(_PreflightModel):
    """描述一个稳定、可定位但不扩大 Graph Core 的 profile 诊断。"""

    code: Annotated[str, StringConstraints(pattern=r"^E_AV27_PREFLIGHT_[A-Z0-9_]+$")]
    message: Annotated[str, StringConstraints(min_length=1, max_length=4096)]
    node_id: _Identifier | None = None
    field_path: Annotated[str, StringConstraints(min_length=1, max_length=512)] | None = None


class Av27SourceBindingFact(_PreflightModel):
    """保存一个 current preparation Source/effective-video 的精确媒体事实。"""

    source_ordinal: Annotated[int, Field(ge=0)]
    source_media_artifact_id: _Identifier
    source_frame_count: Annotated[int, Field(gt=0)]
    source_frame_rate: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    effective_video_artifact_id: _Identifier
    effective_video_frame_count: Annotated[int, Field(gt=0)]
    effective_video_frame_rate: Annotated[str, StringConstraints(min_length=1, max_length=128)]

    @field_validator("source_frame_rate", "effective_video_frame_rate")
    @classmethod
    def validate_canonical_rate(cls, value: str) -> str:
        try:
            parsed = parse_fraction(value)
        except Av27MediaError as error:
            raise ValueError(
                "E_AV27_PREFLIGHT_BINDING_RATE: frame rate 必须是 canonical 正 rational"
            ) from error
        if canonical_fraction(parsed) != value:
            raise ValueError(
                "E_AV27_PREFLIGHT_BINDING_RATE: frame rate 必须显式包含约分后的 denominator"
            )
        return value


class Av27BindingFacts(_PreflightModel):
    """由 Project Service 从当前 Repository 建立的只读 preparation binding 事实。

    preflight 不自行查询 Run 或 Artifact。调用方若提供本对象，就必须同时给出 snapshot/result current
    布尔值以及当前 Admission、Source/effective-video Artifact identity 与 N/FPS；任一不再 current
    都只产生 ``replan_required``，不会改写 Graph。N/FPS 是重算 Chapter/Leaf 的独立 authority，
    不允许由待检查 Graph 的 FinalMux 参数反向充当事实。
    """

    preparation_snapshot_current: bool
    preparation_results_current: bool
    admission_artifact_id: _Identifier
    sources: tuple[Av27SourceBindingFact, ...]

    @field_validator("sources", mode="before")
    @classmethod
    def normalize_sources(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_sources(self) -> Self:
        if not self.sources:
            raise ValueError("E_AV27_PREFLIGHT_BINDING_EMPTY: Source facts 不得为空")
        ordinals = tuple(item.source_ordinal for item in self.sources)
        if ordinals != tuple(range(len(self.sources))):
            raise ValueError("E_AV27_PREFLIGHT_BINDING_ORDER: Source facts 必须从 0 连续")
        source_ids = tuple(item.source_media_artifact_id for item in self.sources)
        effective_ids = self.effective_video_artifact_ids
        if len(source_ids) != len(set(source_ids)) or len(effective_ids) != len(set(effective_ids)):
            raise ValueError("E_AV27_PREFLIGHT_BINDING_DUPLICATE: Artifact IDs 不得重复")
        return self

    @property
    def effective_video_artifact_ids(self) -> tuple[str, ...]:
        """按 Source ordinal 返回 current effective-video Artifact IDs。"""

        return tuple(item.effective_video_artifact_id for item in self.sources)


class Av27PublicationFacts(_PreflightModel):
    """保存调用方基于当前文件系统建立的 canonical publication 只读事实。

    路径值必须是 caller 已解析后的 absolute identity；布尔值只描述同一次检查，不授权创建目录、
    覆盖目标或修改 Graph。dangling symlink 可表现为 ``target_exists=false`` 且
    ``target_is_symlink_or_reparse=true``，由 preflight 失败关闭。
    """

    target_path: Annotated[str, StringConstraints(min_length=1, max_length=32767)]
    resolved_output_root: Annotated[str, StringConstraints(min_length=1, max_length=32767)]
    resolved_canonical_parent: Annotated[str, StringConstraints(min_length=1, max_length=32767)]
    output_root_exists: bool
    output_root_is_directory: bool
    canonical_parent_exists: bool
    canonical_parent_is_directory: bool
    canonical_parent_contained: bool
    canonical_parent_is_symlink_or_reparse: bool = False
    target_exists: bool
    target_is_regular_file: bool
    target_is_symlink_or_reparse: bool
    target_contained: bool

    @field_validator("target_path", "resolved_output_root", "resolved_canonical_parent")
    @classmethod
    def validate_path_text(cls, value: str) -> str:
        if value.strip() != value or "\x00" in value:
            raise ValueError(
                "E_AV27_PREFLIGHT_PUBLICATION_PATH: publication fact path 含边界空白或 NUL"
            )
        return value


class Av27ProfilePreflightResult(_PreflightModel):
    """返回 profile 状态；compatible 状态必须没有阻断诊断。"""

    profile_version: Literal["2.7.0"] = AV27_PROFILE_VERSION
    status: Av27ProfileStatus
    phase: Av27ProfilePhase | None = None
    compatible: bool
    diagnostics: tuple[Av27ProfileDiagnostic, ...] = ()

    @field_validator("diagnostics", mode="before")
    @classmethod
    def normalize_diagnostics(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        expected_compatible = self.status in {
            "preparation-compatible",
            "expanded-compatible",
        }
        if self.compatible != expected_compatible:
            raise ValueError("E_AV27_PREFLIGHT_STATUS: compatible 与 status 不一致")
        if expected_compatible and self.diagnostics:
            raise ValueError("E_AV27_PREFLIGHT_DIAGNOSTICS: compatible 状态不能有阻断诊断")
        if not expected_compatible and not self.diagnostics:
            raise ValueError("E_AV27_PREFLIGHT_DIAGNOSTICS: 非 compatible 状态必须说明原因")
        if self.status == "preparation-compatible" and self.phase != "preparation":
            raise ValueError("E_AV27_PREFLIGHT_PHASE: preparation status 的 phase 不一致")
        if self.status in {"expanded-compatible", "replan_required"} and self.phase != "expanded":
            raise ValueError("E_AV27_PREFLIGHT_PHASE: expanded status 的 phase 不一致")
        return self


class _SourceContext:
    """保存完成基础 Source/Admission/MR shape 检查后的纯内存索引。"""

    def __init__(
        self,
        *,
        sources: tuple[NodeInstance, ...],
        admission: NodeInstance,
        source_mode: str,
        mr_mode: str,
        mrs: tuple[NodeInstance, ...],
        base_edges: tuple[_EdgeKey, ...],
    ) -> None:
        self.sources = sources
        self.admission = admission
        self.source_mode = source_mode
        self.mr_mode = mr_mode
        self.mrs = mrs
        self.base_edges = base_edges


class _Inspector:
    """以确定性顺序累积 profile shape 与 binding 诊断。"""

    def __init__(
        self,
        project: Project,
        definitions: tuple[NodeDefinition, ...],
        binding_facts: Av27BindingFacts | None,
        publication_facts: Av27PublicationFacts | None,
    ) -> None:
        self.project = project
        self.graph = project.graph
        self.definitions = definitions
        self.binding_facts = binding_facts
        self.publication_facts = publication_facts
        self.errors: list[Av27ProfileDiagnostic] = []
        self.replan: list[Av27ProfileDiagnostic] = []
        self.nodes_by_type: dict[str, list[NodeInstance]] = defaultdict(list)
        self.expected_definitions: dict[str, NodeDefinition] = {}
        for node in self.graph.nodes:
            self.nodes_by_type[node.type_id].append(node)

    def add(
        self,
        code: str,
        message: str,
        *,
        node: NodeInstance | None = None,
        field_path: str | None = None,
    ) -> None:
        self.errors.append(
            Av27ProfileDiagnostic(
                code=code,
                message=message,
                node_id=None if node is None else node.node_id,
                field_path=field_path,
            )
        )

    def add_replan(self, code: str, message: str, *, field_path: str | None = None) -> None:
        self.replan.append(Av27ProfileDiagnostic(code=code, message=message, field_path=field_path))

    def inspect(self) -> Av27ProfilePreflightResult:
        phase = self._classify_phase()
        self._check_core_and_definitions()
        self._check_forbidden_parameters()
        context = self._check_source_admission_mr()
        if phase == "preparation":
            if context is not None:
                self._check_preparation(context)
        elif context is not None:
            self._check_expanded(context)

        if self.errors:
            return Av27ProfilePreflightResult(
                status="incompatible",
                phase=phase,
                compatible=False,
                diagnostics=tuple(self.errors),
            )
        if phase == "expanded" and self.replan:
            return Av27ProfilePreflightResult(
                status="replan_required",
                phase=phase,
                compatible=False,
                diagnostics=tuple(self.replan),
            )
        return Av27ProfilePreflightResult(
            status=("preparation-compatible" if phase == "preparation" else "expanded-compatible"),
            phase=phase,
            compatible=True,
        )

    def _classify_phase(self) -> Av27ProfilePhase:
        for node in self.graph.nodes:
            if node.type_id in _EXPANDED_TYPES or atomic_split_count_from_type_id(node.type_id):
                return "expanded"
        return "preparation"

    def _check_core_and_definitions(self) -> None:
        if not self.definitions:
            self.add(
                "E_AV27_PREFLIGHT_DEFINITIONS_REQUIRED",
                "profile preflight 需要 Graph 对应的精确 NodeDefinition snapshot",
                field_path="definitions",
            )
            return
        try:
            violations = GraphValidator(self.definitions).inspect(self.graph)
        except ValueError as error:
            self.add(
                "E_AV27_PREFLIGHT_DEFINITION_CATALOG",
                str(error),
                field_path="definitions",
            )
            violations = ()
        for violation in violations:
            self.add(
                "E_AV27_PREFLIGHT_CORE_INVALID",
                f"{violation.code}: {violation.message}",
                field_path=violation.path,
            )

        catalog: dict[tuple[str, str], NodeDefinition] = {}
        for definition in self.definitions:
            key = (definition.type_id, definition.version)
            if key in catalog:
                continue
            catalog[key] = definition
        for node in self.graph.nodes:
            split_count = atomic_split_count_from_type_id(node.type_id)
            if split_count is not None and split_count != len(
                self.nodes_by_type.get(ENHANCEMENT_TYPE_ID, ())
            ):
                self.add(
                    "E_AV27_PREFLIGHT_SPLIT_COUNT",
                    "AtomicSplit type count 必须等于 Graph 的 Enhancement 节点数量",
                    node=node,
                    field_path="type_id",
                )
                continue
            expected = _expected_definition(node)
            if expected is None:
                self.add(
                    "E_AV27_PREFLIGHT_NODE_TYPE",
                    f"节点类型 {node.type_id}@{node.definition_version} 不属于 v2.7 profile",
                    node=node,
                    field_path="type_id",
                )
                continue
            self.expected_definitions[node.node_id] = expected
            actual = catalog.get((node.type_id, node.definition_version))
            if actual != expected and not (
                node.type_id == _OUTPUT_TYPE_ID
                and actual is not None
                and is_supported_output_file_definition(actual)
            ):
                self.add(
                    "E_AV27_PREFLIGHT_DEFINITION_MISMATCH",
                    f"节点必须绑定冻结定义 {expected.type_id}@{expected.version}",
                    node=node,
                    field_path="definition_version",
                )
        expected_keys = {
            (definition.type_id, definition.version)
            for definition in self.expected_definitions.values()
        }
        if set(catalog) != expected_keys:
            self.add(
                "E_AV27_PREFLIGHT_DEFINITION_SET",
                "definition snapshot 必须精确等于当前 profile Graph 使用的冻结定义集合",
                field_path="definitions",
            )

    def _check_forbidden_parameters(self) -> None:
        for node in self.graph.nodes:
            for path, key in _walk_parameter_keys(node.parameters):
                if key.casefold() in _FORBIDDEN_PARAMETER_KEYS:
                    self.add(
                        "E_AV27_PREFLIGHT_EXECUTABLE_PARAMETER",
                        f"模板参数不得携带可执行声明或客户端 output path：{key}",
                        node=node,
                        field_path=f"parameters.{path}",
                    )

    def _check_source_admission_mr(self) -> _SourceContext | None:
        sources = tuple(self.nodes_by_type.get(SOURCE_PROGRAM_TYPE_ID, ()))
        admissions = tuple(self.nodes_by_type.get(SOURCE_ADMISSION_TYPE_ID, ()))
        mrs = tuple(self.nodes_by_type.get(MOSAIC_RESTORATION_TYPE_ID, ()))
        if not sources:
            self.add("E_AV27_PREFLIGHT_SOURCE_COUNT", "profile 至少需要一个 SourceProgram")
        if len(admissions) != 1:
            self.add(
                "E_AV27_PREFLIGHT_ADMISSION_COUNT",
                "profile 必须恰好包含一个 SourceAdmission",
            )
        if not sources or len(admissions) != 1:
            return None
        admission = admissions[0]

        source_by_ordinal: dict[int, NodeInstance] = {}
        for source in sources:
            ordinal = _strict_int(source.parameters.get("source_ordinal"), minimum=0)
            if ordinal is None or ordinal in source_by_ordinal:
                self.add(
                    "E_AV27_PREFLIGHT_SOURCE_ORDINAL",
                    "Source ordinal 必须是从 0 连续的唯一整数",
                    node=source,
                    field_path="parameters.source_ordinal",
                )
                continue
            source_by_ordinal[ordinal] = source
        ordered_ordinals = tuple(sorted(source_by_ordinal))
        if ordered_ordinals != tuple(range(len(sources))):
            self.add(
                "E_AV27_PREFLIGHT_SOURCE_ORDINAL",
                "Source ordinal 必须从 0 连续且无缺口",
                field_path="nodes",
            )
            return None
        ordered_sources = tuple(source_by_ordinal[index] for index in ordered_ordinals)
        for source in ordered_sources:
            source_path = source.parameters.get("source_path")
            if (
                not isinstance(source_path, str)
                or not source_path
                or source_path.strip() != source_path
                or "\x00" in source_path
            ):
                self.add(
                    "E_AV27_PREFLIGHT_SOURCE_PATH",
                    "Source path 必须是无边界空白与 NUL 的非空文本",
                    node=source,
                    field_path="parameters.source_path",
                )

        source_mode = admission.parameters.get("source_mode")
        if not isinstance(source_mode, str) or source_mode not in {"program", "pre_chaptered"}:
            self.add(
                "E_AV27_PREFLIGHT_SOURCE_MODE",
                "SourceAdmission source_mode 必须是 program 或 pre_chaptered",
                node=admission,
                field_path="parameters.source_mode",
            )
            return None
        if source_mode == "program" and len(ordered_sources) != 1:
            self.add(
                "E_AV27_PREFLIGHT_PROGRAM_SOURCE_COUNT",
                "program mode 必须恰好有一个 SourceProgram",
                node=admission,
            )
        for source in ordered_sources:
            label = source.parameters.get("label")
            if source_mode == "program" and "label" in source.parameters:
                self.add(
                    "E_AV27_PREFLIGHT_CHAPTER_LABEL",
                    "program mode 的 SourceProgram 不得保存 chapter label",
                    node=source,
                    field_path="parameters.label",
                )
            if source_mode == "pre_chaptered" and (
                not isinstance(label, str) or not label or label.strip() != label
            ):
                self.add(
                    "E_AV27_PREFLIGHT_CHAPTER_LABEL",
                    "pre_chaptered SourceProgram 必须保存无边界空白的 chapter label",
                    node=source,
                    field_path="parameters.label",
                )

        expected_identities = [
            {"source_ordinal": index, "source_node_id": source.node_id}
            for index, source in enumerate(ordered_sources)
        ]
        if admission.parameters.get("sources") != expected_identities:
            self.add(
                "E_AV27_PREFLIGHT_SOURCE_BINDING",
                "SourceAdmission sources 必须按 ordinal 精确绑定 Source node identity",
                node=admission,
                field_path="parameters.sources",
            )

        base_edges: list[_EdgeKey] = [
            (source.node_id, "source_media", admission.node_id, "sources", index)
            for index, source in enumerate(ordered_sources)
        ]
        mr_by_source: dict[str, NodeInstance] = {}
        for mr in mrs:
            self._check_model_declaration(mr, role="MR", version_required=True)
            video_edges = self._incoming(mr.node_id, "video")
            gate_edges = self._incoming(mr.node_id, "gate")
            if len(video_edges) != 1:
                self.add(
                    "E_AV27_PREFLIGHT_MR_SOURCE",
                    "每个 MR video 必须直接来自一个 SourceProgram.video",
                    node=mr,
                    field_path="inputs.video",
                )
                continue
            video_edge = video_edges[0]
            matched_source = next(
                (
                    item
                    for item in ordered_sources
                    if item.node_id == video_edge.source_node_id
                    and video_edge.source_port_id == "video"
                ),
                None,
            )
            if matched_source is None or matched_source.node_id in mr_by_source:
                self.add(
                    "E_AV27_PREFLIGHT_MR_SOURCE",
                    "MR 必须与唯一 SourceProgram.video 一一对应",
                    node=mr,
                    field_path="inputs.video",
                )
            else:
                mr_by_source[matched_source.node_id] = mr
            if len(gate_edges) != 1 or _edge_key(gate_edges[0]) != (
                admission.node_id,
                "gate",
                mr.node_id,
                "gate",
                None,
            ):
                self.add(
                    "E_AV27_PREFLIGHT_GATE_SOURCE",
                    "MR gate 必须直接来自同一 SourceAdmission.gate",
                    node=mr,
                    field_path="inputs.gate",
                )

        if mrs and len(mrs) != len(ordered_sources):
            self.add(
                "E_AV27_PREFLIGHT_MR_COUNT",
                "external MR 必须与 Source 数量一致",
            )
        mr_mode = "external" if mrs else "off"
        ordered_mrs: tuple[NodeInstance, ...] = ()
        if mr_mode == "external" and len(mr_by_source) == len(ordered_sources):
            ordered_mrs = tuple(mr_by_source[source.node_id] for source in ordered_sources)
            for source, mr in zip(ordered_sources, ordered_mrs, strict=True):
                base_edges.extend(
                    (
                        (source.node_id, "video", mr.node_id, "video", None),
                        (admission.node_id, "gate", mr.node_id, "gate", None),
                    )
                )
            declarations = {
                (mr.parameters.get("model_name"), mr.parameters.get("model_version"))
                for mr in ordered_mrs
            }
            if len(declarations) != 1:
                self.add(
                    "E_AV27_PREFLIGHT_MR_DECLARATION",
                    "全部 MR 必须使用一致且完整的 model_name/model_version",
                    field_path="nodes.parameters",
                )
        return _SourceContext(
            sources=ordered_sources,
            admission=admission,
            source_mode=source_mode,
            mr_mode=mr_mode,
            mrs=ordered_mrs,
            base_edges=tuple(base_edges),
        )

    def _check_preparation(self, context: _SourceContext) -> None:
        allowed = {
            SOURCE_PROGRAM_TYPE_ID,
            SOURCE_ADMISSION_TYPE_ID,
            MOSAIC_RESTORATION_TYPE_ID,
        }
        unexpected = tuple(node for node in self.graph.nodes if node.type_id not in allowed)
        if unexpected:
            for node in unexpected:
                self.add(
                    "E_AV27_PREFLIGHT_PREPARATION_SHAPE",
                    "preparation Graph 不得包含 Split 或下游节点",
                    node=node,
                )
        self._check_exact_edges(context.base_edges)

    def _check_expanded(self, context: _SourceContext) -> None:
        splits = tuple(
            node
            for node in self.graph.nodes
            if atomic_split_count_from_type_id(node.type_id) is not None
        )
        enhancements = tuple(self.nodes_by_type.get(ENHANCEMENT_TYPE_ID, ()))
        merges = tuple(self.nodes_by_type.get(MERGE_VIDEO_TYPE_ID, ()))
        interpolation = tuple(self.nodes_by_type.get(FRAME_INTERPOLATION_TYPE_ID, ()))
        programs = tuple(self.nodes_by_type.get(PROGRAM_ENCODE_TYPE_ID, ()))
        finals = tuple(self.nodes_by_type.get(FINAL_MUX_TYPE_ID, ()))
        outputs = tuple(self.nodes_by_type.get(_OUTPUT_TYPE_ID, ()))
        required_singletons = (
            ("AtomicSplit", splits),
            ("ProgramEncode", programs),
            ("FinalMux", finals),
            ("OutputFile", outputs),
        )
        for label, values in required_singletons:
            if len(values) != 1:
                self.add(
                    "E_AV27_PREFLIGHT_EXPANDED_COUNT",
                    f"expanded profile 必须恰好包含一个 {label}",
                )
        if any(len(values) != 1 for _, values in required_singletons):
            return
        split, program, final, output = splits[0], programs[0], finals[0], outputs[0]
        split_count = atomic_split_count_from_type_id(split.type_id)
        assert split_count is not None

        chapters = _mapping_sequence(split.parameters.get("chapters"))
        segments = _mapping_sequence(split.parameters.get("segments"))
        planned_ids = _string_sequence(split.parameters.get("planned_effective_video_artifact_ids"))
        final_sources = _mapping_sequence(final.parameters.get("sources"))
        if chapters is None or segments is None or planned_ids is None or final_sources is None:
            self.add(
                "E_AV27_PREFLIGHT_PLAN_SHAPE",
                "Split/Final 的 server-derived plan 参数形状无效",
                node=split,
                field_path="parameters",
            )
            return
        if len(segments) != split_count:
            self.add(
                "E_AV27_PREFLIGHT_SPLIT_COUNT",
                "AtomicSplit type count、ports 与 segments 数量必须一致",
                node=split,
                field_path="parameters.segments",
            )
        if split.parameters.get("source_mode") != context.source_mode:
            self.add(
                "E_AV27_PREFLIGHT_SOURCE_MODE",
                "AtomicSplit source_mode 必须与 SourceAdmission 一致",
                node=split,
                field_path="parameters.source_mode",
            )
        if final.parameters.get("source_mode") != context.source_mode:
            self.add(
                "E_AV27_PREFLIGHT_SOURCE_MODE",
                "FinalMux source_mode 必须与 SourceAdmission 一致",
                node=final,
                field_path="parameters.source_mode",
            )
        if final.parameters.get("mr_mode") != context.mr_mode:
            self.add(
                "E_AV27_PREFLIGHT_MR_MODE",
                "FinalMux diagnostic mr_mode 必须与当前 MR 拓扑一致",
                node=final,
                field_path="parameters.mr_mode",
            )

        final_source_facts = self._parse_final_sources(final, final_sources, context)
        if final_source_facts is None:
            return
        admission_artifact_id = split.parameters.get("planned_admission_artifact_id")
        source_facts = self._check_binding_facts(
            final=final,
            final_source_facts=final_source_facts,
            admission_artifact_id=admission_artifact_id,
            planned_ids=planned_ids,
            source_count=len(context.sources),
        )
        source_fps = source_facts[0][1]
        if any(fps != source_fps for _, fps in source_facts):
            self.add(
                "E_AV27_PREFLIGHT_SOURCE_FPS",
                "全部 Source 的 canonical FPS 必须一致",
                node=final,
                field_path="parameters.sources",
            )
            return
        fps_text = canonical_fraction(source_fps)
        expected_chapters = self._derive_chapters(
            split,
            context,
            source_facts,
            source_fps,
        )
        if expected_chapters is None:
            return
        if list(chapters) != expected_chapters:
            self.add(
                "E_AV27_PREFLIGHT_CHAPTER_PLAN",
                "ResolvedChapterPlan 必须由 admitted N/FPS 与 selector 确定性派生",
                node=split,
                field_path="parameters.chapters",
            )

        leaf_minutes = _strict_int(split.parameters.get("leaf_duration_minutes"), minimum=1)
        if leaf_minutes is None:
            self.add(
                "E_AV27_PREFLIGHT_LEAF_DURATION",
                "leaf_duration_minutes 必须是严格正整数",
                node=split,
                field_path="parameters.leaf_duration_minutes",
            )
            return
        if len(planned_ids) != len(context.sources) or len(set(planned_ids)) != len(planned_ids):
            self.add(
                "E_AV27_PREFLIGHT_EFFECTIVE_BINDINGS",
                "planned effective Artifact IDs 必须按 Source ordinal 唯一完整",
                node=split,
                field_path="parameters.planned_effective_video_artifact_ids",
            )
            return
        expected_leaf_count = _derived_leaf_count(
            expected_chapters,
            source_fps=source_fps,
            leaf_duration_minutes=leaf_minutes,
        )
        if expected_leaf_count is None:
            self.add(
                "E_AV27_PREFLIGHT_LEAF_DURATION",
                "leaf duration 与 exact FPS 必须产生至少一帧",
                node=split,
                field_path="parameters.leaf_duration_minutes",
            )
            return
        if expected_leaf_count != split_count or expected_leaf_count != len(enhancements):
            self.add(
                "E_AV27_PREFLIGHT_SPLIT_COUNT",
                "AtomicSplit type count、Enhancement 数量与派生 leaf 数量必须一致",
                node=split,
                field_path="type_id",
            )
            return
        expected_segments = _derive_segments(
            expected_chapters,
            source_fps=source_fps,
            leaf_duration_minutes=leaf_minutes,
            planned_ids=planned_ids,
        )
        if expected_segments is None:
            self.add(
                "E_AV27_PREFLIGHT_LEAF_DURATION",
                "leaf duration 与 exact FPS 必须产生至少一帧",
                node=split,
                field_path="parameters.leaf_duration_minutes",
            )
            return
        if list(segments) != expected_segments:
            self.add(
                "E_AV27_PREFLIGHT_LEAF_PLAN",
                "segments 必须由 ChapterPlan 与 leaf_duration_minutes 唯一派生",
                node=split,
                field_path="parameters.segments",
            )
        expected_ports = tuple(segment["port_id"] for segment in expected_segments)
        split_definition = self.expected_definitions.get(split.node_id)
        actual_ports = (
            ()
            if split_definition is None
            else tuple(port.port_id for port in split_definition.output_ports)
        )
        if expected_ports != actual_ports:
            self.add(
                "E_AV27_PREFLIGHT_SPLIT_PORTS",
                "Split output ports、output_paths 与 segments 必须一一对应",
                node=split,
            )

        input_geometry = _geometry(1920, 1080)
        enhancement_index, scale, output_geometry, enhancement_model = self._check_enhancements(
            enhancements,
            expected_segments,
            fps_text,
            input_geometry,
        )
        merge_index = self._check_merges(
            merges,
            expected_chapters,
            fps_text,
            output_geometry,
            scale,
            enhancement_model,
        )
        fi_index, expected_signal = self._check_fi(
            interpolation,
            expected_chapters,
            source_fps=fps_text,
            output_geometry=output_geometry,
        )
        self._check_program_and_final(
            program,
            final,
            output,
            expected_chapters,
            source_facts,
            source_fps,
            output_geometry,
            expected_signal,
            context,
        )

        expected_leaf_ids = {str(segment["leaf_id"]) for segment in expected_segments}
        expected_chapter_ids = {str(chapter["chapter_id"]) for chapter in expected_chapters}
        if (
            set(enhancement_index) == expected_leaf_ids
            and set(merge_index) == expected_chapter_ids
            and set(fi_index) == expected_chapter_ids
        ):
            expected_edges = list(context.base_edges)
            effective_nodes = context.mrs if context.mr_mode == "external" else context.sources
            expected_edges.extend(
                (
                    node.node_id,
                    "video",
                    split.node_id,
                    "videos",
                    index,
                )
                for index, node in enumerate(effective_nodes)
            )
            expected_edges.append((context.admission.node_id, "gate", split.node_id, "gate", None))
            chapter_segments: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
            for segment in expected_segments:
                chapter_segments[str(segment["chapter_id"])].append(segment)
                enhancement = enhancement_index[str(segment["leaf_id"])]
                expected_edges.append(
                    (split.node_id, str(segment["port_id"]), enhancement.node_id, "video", None)
                )
            for chapter in expected_chapters:
                chapter_id = str(chapter["chapter_id"])
                merge = merge_index[chapter_id]
                fi = fi_index[chapter_id]
                for local_ordinal, chapter_segment in enumerate(chapter_segments[chapter_id]):
                    enhancement = enhancement_index[str(chapter_segment["leaf_id"])]
                    expected_edges.append(
                        (enhancement.node_id, "video", merge.node_id, "videos", local_ordinal)
                    )
                expected_edges.extend(
                    (
                        (merge.node_id, "video", fi.node_id, "video", None),
                        (
                            fi.node_id,
                            "video",
                            program.node_id,
                            "chapters",
                            int(chapter["chapter_ordinal"]),
                        ),
                    )
                )
            expected_edges.append((program.node_id, "video", final.node_id, "video", None))
            expected_edges.extend(
                (
                    source.node_id,
                    "source_media",
                    final.node_id,
                    "sources",
                    index,
                )
                for index, source in enumerate(context.sources)
            )
            expected_edges.extend(
                (
                    (context.admission.node_id, "gate", final.node_id, "gate", None),
                    (final.node_id, "media", output.node_id, "in", None),
                )
            )
            self._check_exact_edges(expected_edges)

    def _parse_final_sources(
        self,
        final: NodeInstance,
        values: Sequence[Mapping[str, Any]],
        context: _SourceContext,
    ) -> tuple[tuple[int, Fraction], ...] | None:
        if len(values) != len(context.sources):
            self.add(
                "E_AV27_PREFLIGHT_FINAL_SOURCES",
                "FinalMux sources 参数必须与 Source 数量一致",
                node=final,
                field_path="parameters.sources",
            )
            return None
        result: list[tuple[int, Fraction]] = []
        for index, value in enumerate(values):
            if set(value) != {"source_ordinal", "source_frames", "source_fps"}:
                self.add(
                    "E_AV27_PREFLIGHT_FINAL_SOURCES",
                    "FinalMux source fact 字段集合无效",
                    node=final,
                    field_path=f"parameters.sources.{index}",
                )
                return None
            if value.get("source_ordinal") != index:
                self.add(
                    "E_AV27_PREFLIGHT_FINAL_SOURCES",
                    "FinalMux source ordinal 必须从 0 连续",
                    node=final,
                    field_path=f"parameters.sources.{index}.source_ordinal",
                )
                return None
            frames = _strict_int(value.get("source_frames"), minimum=1)
            try:
                fps = parse_fraction(value.get("source_fps"))
            except Av27MediaError:
                frames = None
                fps = Fraction(1, 1)
            if frames is None:
                self.add(
                    "E_AV27_PREFLIGHT_FINAL_SOURCES",
                    "FinalMux source N/FPS 必须是 canonical exact 值",
                    node=final,
                    field_path=f"parameters.sources.{index}",
                )
                return None
            result.append((frames, fps))
        return tuple(result)

    def _derive_chapters(
        self,
        split: NodeInstance,
        context: _SourceContext,
        source_facts: tuple[tuple[int, Fraction], ...],
        source_fps: Fraction,
    ) -> list[dict[str, Any]] | None:
        if context.source_mode == "pre_chaptered":
            if "chapter_selector" in split.parameters:
                self.add(
                    "E_AV27_PREFLIGHT_SELECTOR",
                    "pre_chaptered mode 不得持久化 chapter_selector",
                    node=split,
                    field_path="parameters.chapter_selector",
                )
                return None
            chapters: list[dict[str, Any]] = []
            for index, (source, (frames, _)) in enumerate(
                zip(context.sources, source_facts, strict=True)
            ):
                label = source.parameters.get("label")
                if not isinstance(label, str) or not label or label.strip() != label:
                    self.add(
                        "E_AV27_PREFLIGHT_CHAPTER_LABEL",
                        "pre_chaptered Source 必须保存非空 chapter label",
                        node=source,
                        field_path="parameters.label",
                    )
                    return None
                chapters.append(
                    {
                        "chapter_id": f"chapter-{index + 1:04d}",
                        "chapter_ordinal": index,
                        "label": label,
                        "source_ordinal": index,
                        "start_frame": 0,
                        "end_frame": frames,
                    }
                )
            return chapters

        selector = split.parameters.get("chapter_selector")
        if not isinstance(selector, Mapping):
            self.add(
                "E_AV27_PREFLIGHT_SELECTOR",
                "program mode 必须持久化 strict chapter_selector",
                node=split,
                field_path="parameters.chapter_selector",
            )
            return None
        total_frames = source_facts[0][0]
        boundaries = _selector_boundaries(selector, total_frames=total_frames, fps=source_fps)
        if boundaries is None:
            self.add(
                "E_AV27_PREFLIGHT_SELECTOR",
                "chapter_selector 非 canonical、映射重复或越界",
                node=split,
                field_path="parameters.chapter_selector",
            )
            return None
        starts = (0, *boundaries)
        ends = (*boundaries, total_frames)
        return [
            {
                "chapter_id": f"chapter-{index + 1:04d}",
                "chapter_ordinal": index,
                "label": excel_chapter_label(index),
                "source_ordinal": 0,
                "start_frame": start,
                "end_frame": end,
            }
            for index, (start, end) in enumerate(zip(starts, ends, strict=True))
        ]

    def _check_binding_facts(
        self,
        *,
        final: NodeInstance,
        final_source_facts: tuple[tuple[int, Fraction], ...],
        admission_artifact_id: object,
        planned_ids: tuple[str, ...],
        source_count: int,
    ) -> tuple[tuple[int, Fraction], ...]:
        facts = self.binding_facts
        if facts is None:
            self.add_replan(
                "E_AV27_PREFLIGHT_BINDING_UNVERIFIED",
                "expanded profile 需要 Project Service 提供当前 preparation binding facts",
            )
            return final_source_facts
        if not facts.preparation_snapshot_current:
            self.add_replan(
                "E_AV27_PREFLIGHT_SNAPSHOT_STALE",
                "preparation Run snapshot 已不再对应当前 preparation 子图",
            )
        if not facts.preparation_results_current:
            self.add_replan(
                "E_AV27_PREFLIGHT_RESULTS_STALE",
                "Source/Admission/MR latest results 已发生变化或不再 completed",
            )
        binding_identity_current = True
        if admission_artifact_id != facts.admission_artifact_id:
            binding_identity_current = False
            self.add_replan(
                "E_AV27_PREFLIGHT_ADMISSION_STALE",
                "planned Admission Artifact 已变化",
                field_path="split.parameters.planned_admission_artifact_id",
            )
        if planned_ids != facts.effective_video_artifact_ids:
            binding_identity_current = False
            self.add_replan(
                "E_AV27_PREFLIGHT_EFFECTIVE_VIDEO_STALE",
                "planned effective-video Artifact IDs 已变化",
                field_path="split.parameters.planned_effective_video_artifact_ids",
            )
        if len(facts.sources) != source_count:
            self.add_replan(
                "E_AV27_PREFLIGHT_SOURCE_FACTS_STALE",
                "current Source facts 数量与 preparation Graph 不一致",
                field_path="binding_facts.sources",
            )
            return final_source_facts

        # stale facts 只决定需要重新规划；不能用已声明不 current 的 N/FPS 反向判 Graph 结构错误。
        if (
            not facts.preparation_snapshot_current
            or not facts.preparation_results_current
            or not binding_identity_current
        ):
            return final_source_facts

        source_values: list[tuple[int, Fraction]] = []
        effective_values: list[tuple[int, Fraction]] = []
        for item in facts.sources:
            source_values.append((item.source_frame_count, parse_fraction(item.source_frame_rate)))
            effective_values.append(
                (
                    item.effective_video_frame_count,
                    parse_fraction(item.effective_video_frame_rate),
                )
            )
        source_tuple = tuple(source_values)
        effective_tuple = tuple(effective_values)
        if source_tuple != effective_tuple:
            self.add_replan(
                "E_AV27_PREFLIGHT_EFFECTIVE_MEDIA_STALE",
                "current effective-video N/FPS 不再与原始 Source 闭合",
                field_path="binding_facts.sources",
            )
            return final_source_facts
        if final_source_facts != source_tuple:
            self.add(
                "E_AV27_PREFLIGHT_FINAL_SOURCE_BINDING",
                "FinalMux sources N/FPS 必须精确等于 current admitted Source facts",
                node=final,
                field_path="parameters.sources",
            )
        return effective_tuple

    def _check_enhancements(
        self,
        nodes: tuple[NodeInstance, ...],
        segments: list[dict[str, Any]],
        fps_text: str,
        input_geometry: dict[str, int | str],
    ) -> tuple[
        dict[str, NodeInstance],
        int,
        dict[str, int | str],
        tuple[object, object],
    ]:
        index: dict[str, NodeInstance] = {}
        for node in nodes:
            self._check_model_declaration(node, role="Enhancement", version_required=False)
            leaf_id = node.parameters.get("leaf_id")
            if not isinstance(leaf_id, str) or leaf_id in index:
                self.add(
                    "E_AV27_PREFLIGHT_ENHANCEMENT_IDENTITY",
                    "每个 Enhancement 必须绑定唯一 canonical leaf_id",
                    node=node,
                    field_path="parameters.leaf_id",
                )
            else:
                index[leaf_id] = node
        expected_ids = {str(segment["leaf_id"]) for segment in segments}
        if set(index) != expected_ids:
            self.add(
                "E_AV27_PREFLIGHT_ENHANCEMENT_COUNT",
                "每个 leaf 必须恰好有一个 Enhancement",
            )

        first = nodes[0] if nodes else None
        scale = (
            1
            if first is None
            else _strict_int(first.parameters.get("actual_scale_factor", 1), minimum=1) or 1
        )
        model = (
            None if first is None else first.parameters.get("model_name"),
            None if first is None else first.parameters.get("model_version"),
        )
        model_version_present = first is not None and "model_version" in first.parameters
        output_geometry = _geometry(1920 * scale, 1080 * scale)
        for segment in segments:
            enhancement_node = index.get(str(segment["leaf_id"]))
            if enhancement_node is None:
                continue
            expected_values = {
                "chapter_id": segment["chapter_id"],
                "chapter_ordinal": segment["chapter_ordinal"],
                "leaf_id": segment["leaf_id"],
                "leaf_ordinal": segment["leaf_ordinal"],
                "expected_frames": int(segment["end_frame"]) - int(segment["start_frame"]),
                "expected_fps": fps_text,
                "expected_input_geometry": input_geometry,
                "expected_output_geometry": output_geometry,
                "model_name": model[0],
            }
            for key, value in expected_values.items():
                self._expect_parameter(enhancement_node, key, value)
            actual_scale = _strict_int(
                enhancement_node.parameters.get("actual_scale_factor", 1), minimum=1
            )
            if actual_scale != scale:
                self.add(
                    "E_AV27_PREFLIGHT_ENHANCEMENT_SCALE",
                    "全部 Enhancement 必须使用一致的整数 scale",
                    node=enhancement_node,
                    field_path="parameters.actual_scale_factor",
                )
            if (
                "model_version" in enhancement_node.parameters
            ) != model_version_present or enhancement_node.parameters.get("model_version") != model[
                1
            ]:
                self.add(
                    "E_AV27_PREFLIGHT_ENHANCEMENT_DECLARATION",
                    "全部 Enhancement model version presence/value 必须一致",
                    node=enhancement_node,
                    field_path="parameters.model_version",
                )
        return index, scale, output_geometry, model

    def _check_merges(
        self,
        nodes: tuple[NodeInstance, ...],
        chapters: list[dict[str, Any]],
        fps_text: str,
        geometry: dict[str, int | str],
        scale: int,
        enhancement_model: tuple[object, object],
    ) -> dict[str, NodeInstance]:
        index = self._index_chapters(nodes, role="Merge")
        if set(index) != {str(chapter["chapter_id"]) for chapter in chapters}:
            self.add("E_AV27_PREFLIGHT_MERGE_COUNT", "每章必须恰好有一个 MergeVideo")
        version_present = any("model_version" in node.parameters for node in nodes)
        expected_version_present = enhancement_model[1] is not None
        for chapter in chapters:
            node = index.get(str(chapter["chapter_id"]))
            if node is None:
                continue
            expected = {
                "chapter_id": chapter["chapter_id"],
                "chapter_ordinal": chapter["chapter_ordinal"],
                "expected_frames": int(chapter["end_frame"]) - int(chapter["start_frame"]),
                "expected_fps": fps_text,
                "expected_geometry": geometry,
                "actual_scale_factor": scale,
                "model_name": enhancement_model[0],
            }
            for key, value in expected.items():
                self._expect_parameter(node, key, value)
            if (
                version_present != expected_version_present
                or node.parameters.get("model_version") != enhancement_model[1]
            ):
                self.add(
                    "E_AV27_PREFLIGHT_MERGE_DECLARATION",
                    "Merge 必须复制 Enhancement model declaration",
                    node=node,
                    field_path="parameters.model_version",
                )
        return index

    def _check_fi(
        self,
        nodes: tuple[NodeInstance, ...],
        chapters: list[dict[str, Any]],
        *,
        source_fps: str,
        output_geometry: dict[str, int | str],
    ) -> tuple[dict[str, NodeInstance], dict[str, str | int]]:
        index = self._index_chapters(nodes, role="FI")
        if set(index) != {str(chapter["chapter_id"]) for chapter in chapters}:
            self.add("E_AV27_PREFLIGHT_FI_COUNT", "每章必须恰好有一个 FrameInterpolation")
        first = nodes[0] if nodes else None
        model = (
            None if first is None else first.parameters.get("model_name"),
            None if first is None else first.parameters.get("model_version"),
        )
        version_present = first is not None and "model_version" in first.parameters
        for chapter in chapters:
            node = index.get(str(chapter["chapter_id"]))
            if node is None:
                continue
            self._check_model_declaration(node, role="FI", version_required=False)
            source_frames = int(chapter["end_frame"]) - int(chapter["start_frame"])
            expected = {
                "chapter_id": chapter["chapter_id"],
                "chapter_ordinal": chapter["chapter_ordinal"],
                "expected_input_frames": source_frames,
                "expected_output_frames": source_frames * 2 - 1,
                "source_fps": source_fps,
                "expected_geometry": output_geometry,
                "expected_signal": _EXPECTED_SIGNAL,
                "model_name": model[0],
            }
            for key, value in expected.items():
                self._expect_parameter(node, key, value)
            if ("model_version" in node.parameters) != version_present or node.parameters.get(
                "model_version"
            ) != model[1]:
                self.add(
                    "E_AV27_PREFLIGHT_FI_DECLARATION",
                    "全部 FI model version presence/value 必须一致",
                    node=node,
                    field_path="parameters.model_version",
                )
        return index, dict(_EXPECTED_SIGNAL)

    def _check_program_and_final(
        self,
        program: NodeInstance,
        final: NodeInstance,
        output: NodeInstance,
        chapters: list[dict[str, Any]],
        source_facts: tuple[tuple[int, Fraction], ...],
        source_fps: Fraction,
        geometry: dict[str, int | str],
        signal: dict[str, str | int],
        context: _SourceContext,
    ) -> None:
        program_chapters = [
            {
                "chapter_id": chapter["chapter_id"],
                "chapter_ordinal": chapter["chapter_ordinal"],
                "source_frames": int(chapter["end_frame"]) - int(chapter["start_frame"]),
                "expected_fi_frames": (int(chapter["end_frame"]) - int(chapter["start_frame"])) * 2
                - 1,
                "encoded_frames": (int(chapter["end_frame"]) - int(chapter["start_frame"])) * 2,
            }
            for chapter in chapters
        ]
        expected_program = {
            "source_fps": canonical_fraction(source_fps),
            "chapters": program_chapters,
            "expected_geometry": geometry,
            "expected_signal": signal,
        }
        for key, value in expected_program.items():
            self._expect_parameter(program, key, value)
        expected_program_frames = sum(int(item["encoded_frames"]) for item in program_chapters)
        expected_final_sources = [
            {
                "source_ordinal": index,
                "source_frames": frames,
                "source_fps": canonical_fraction(fps),
            }
            for index, (frames, fps) in enumerate(source_facts)
        ]
        expected_final = {
            "source_mode": context.source_mode,
            "sources": expected_final_sources,
            "expected_program_frames": expected_program_frames,
            "expected_geometry": geometry,
            "expected_signal": signal,
            "mr_mode": context.mr_mode,
        }
        for key, expected_value in expected_final.items():
            self._expect_parameter(final, key, expected_value)
        self._check_output_naming(
            output,
            mr_mode=context.mr_mode,
            final_rate=source_fps * 2,
            output_height=int(geometry["height"]),
        )
        self._check_publication_facts(output)
        if "output_root" in output.parameters:
            self._expect_parameter(
                output,
                "protected_paths",
                list(
                    dict.fromkeys(
                        str(source.parameters.get("source_path")) for source in context.sources
                    )
                ),
            )

    def _check_output_naming(
        self,
        output: NodeInstance,
        *,
        mr_mode: str,
        final_rate: Fraction,
        output_height: int,
    ) -> None:
        if output.parameters.get("mode") != "copy":
            self.add(
                "E_AV27_PREFLIGHT_OUTPUT_MODE",
                "模板 OutputFile 必须使用 copy mode",
                node=output,
                field_path="parameters.mode",
            )
        target = output.parameters.get("target_path")
        if (
            not isinstance(target, str)
            or not target
            or target.strip() != target
            or "\x00" in target
        ):
            self.add(
                "E_AV27_PREFLIGHT_NAMING",
                "OutputFile 必须包含无边界空白与 NUL 的 canonical target_path",
                node=output,
                field_path="parameters.target_path",
            )
            return
        rate_label = _RATE_LABELS.get(final_rate)
        if rate_label is None:
            self.add(
                "E_AV27_PREFLIGHT_NAMING_RATE",
                "最终 FPS 不属于 canonical Jellyfin rate label allowlist",
                node=output,
                field_path="parameters.target_path",
            )
            return
        normalized = target.replace("\\", "/")
        path = PurePosixPath(normalized)
        windows = PureWindowsPath(target)
        if not (path.is_absolute() or windows.is_absolute()):
            self.add(
                "E_AV27_PREFLIGHT_NAMING",
                "canonical target_path 必须是绝对路径",
                node=output,
                field_path="parameters.target_path",
            )
            return
        marker = "MR Enhanced" if mr_mode == "external" else "Enhanced"
        suffix = f" - {marker} FI{rate_label} {output_height}p.mkv"
        name_prefix = path.name[: -len(suffix)] if path.name.endswith(suffix) else ""
        parent_match = re.fullmatch(r"(?P<title>.+) \((?P<year>[0-9]{4})\)", name_prefix)
        if parent_match is None:
            self.add(
                "E_AV27_PREFLIGHT_NAMING",
                "canonical 成品名称必须包含 <Title> (<Year>) 与固定媒体后缀",
                node=output,
                field_path="parameters.target_path",
            )
            return
        title = parent_match.group("title")
        year = parent_match.group("year")
        if not _valid_windows_title(title):
            self.add(
                "E_AV27_PREFLIGHT_NAMING_TITLE",
                "title 不符合冻结的 Windows naming 规则",
                node=output,
                field_path="parameters.target_path",
            )
        expected_name = f"{title} ({year}) - {marker} FI{rate_label} {output_height}p.mkv"
        if path.name != expected_name:
            self.add(
                "E_AV27_PREFLIGHT_NAMING",
                "OutputFile target filename 不是 Python canonical naming 结果",
                node=output,
                field_path="parameters.target_path",
            )
        root_value = output.parameters.get("output_root")
        if root_value is None:
            layout_safe = path.parent.name == f"{title} ({year})"
        else:
            root = PurePosixPath(str(root_value).replace("\\", "/"))
            create_parent = output.parameters.get("create_parent")
            layout_safe = (create_parent is False and path.parent == root) or (
                create_parent is True
                and path.parent.parent == root
                and path.parent.name == f"{title} ({year})"
            )
        if not layout_safe:
            self.add("E_AV27_PREFLIGHT_NAMING", "输出布局与显式 root/创建权限不一致", node=output)

    def _index_chapters(
        self,
        nodes: tuple[NodeInstance, ...],
        *,
        role: str,
    ) -> dict[str, NodeInstance]:
        index: dict[str, NodeInstance] = {}
        for node in nodes:
            chapter_id = node.parameters.get("chapter_id")
            if not isinstance(chapter_id, str) or chapter_id in index:
                self.add(
                    "E_AV27_PREFLIGHT_CHAPTER_NODE_IDENTITY",
                    f"{role} 必须绑定唯一 canonical chapter_id",
                    node=node,
                    field_path="parameters.chapter_id",
                )
            else:
                index[chapter_id] = node
        return index

    def _check_publication_facts(self, output: NodeInstance) -> None:
        target = output.parameters.get("target_path")
        if (
            not isinstance(target, str)
            or not target
            or target.strip() != target
            or "\x00" in target
        ):
            return
        facts = self.publication_facts
        if facts is None:
            self.add_replan(
                "E_AV27_PREFLIGHT_PUBLICATION_UNVERIFIED",
                "expanded profile 需要调用方提供 current canonical publication facts",
                field_path="publication_facts",
            )
            return
        if target != facts.target_path:
            self.add_replan(
                "E_AV27_PREFLIGHT_PUBLICATION_STALE",
                "publication facts 没有绑定当前 OutputFile target_path",
                field_path="publication_facts.target_path",
            )
            return

        target_path = _pure_absolute_path(facts.target_path)
        output_root = _pure_absolute_path(facts.resolved_output_root)
        parent = _pure_absolute_path(facts.resolved_canonical_parent)
        has_root = "output_root" in output.parameters
        create_parent = output.parameters.get("create_parent", False) is True
        lexical_safe = (
            target_path is not None
            and output_root is not None
            and parent is not None
            and type(target_path) is type(output_root) is type(parent)
            and ".." not in target_path.parts
            and ".." not in output_root.parts
            and ".." not in parent.parts
            and target_path.parent == parent
            and (
                (has_root and not create_parent and parent == output_root)
                or ((not has_root or create_parent) and parent.parent == output_root)
            )
            and (
                not has_root
                or _pure_absolute_path(str(output.parameters["output_root"])) == output_root
            )
        )
        parent_ready = (facts.canonical_parent_exists and facts.canonical_parent_is_directory) or (
            create_parent
            and not facts.canonical_parent_exists
            and not facts.canonical_parent_is_directory
        )
        base_ready = (
            facts.output_root_exists
            and facts.output_root_is_directory
            and parent_ready
            and not facts.canonical_parent_is_symlink_or_reparse
            and facts.canonical_parent_contained
            and facts.target_contained
        )
        target_ready = not facts.target_is_symlink_or_reparse and (
            (not facts.target_exists and not facts.target_is_regular_file)
            or (
                facts.target_exists
                and facts.target_is_regular_file
                and output.parameters.get("overwrite") is True
            )
        )
        if not lexical_safe or not base_ready or not target_ready:
            self.add_replan(
                "E_AV27_PREFLIGHT_PUBLICATION_STALE",
                "canonical parent/containment/target/overwrite 的 current 文件系统事实不再可发布",
                field_path="publication_facts",
            )

    def _expect_parameter(self, node: NodeInstance, key: str, expected: object) -> None:
        if node.parameters.get(key) != expected:
            self.add(
                "E_AV27_PREFLIGHT_PARAMETER_MISMATCH",
                f"{key} 与 server-derived profile 值不一致",
                node=node,
                field_path=f"parameters.{key}",
            )

    def _check_model_declaration(
        self,
        node: NodeInstance,
        *,
        role: str,
        version_required: bool,
    ) -> None:
        model_name = node.parameters.get("model_name")
        model_version = node.parameters.get("model_version")
        if not _valid_model_text(model_name):
            self.add(
                "E_AV27_PREFLIGHT_MODEL_DECLARATION",
                f"{role} model_name 必须是无边界空白与 NUL 的非空文本",
                node=node,
                field_path="parameters.model_name",
            )
        if (version_required or "model_version" in node.parameters) and not _valid_model_text(
            model_version
        ):
            self.add(
                "E_AV27_PREFLIGHT_MODEL_DECLARATION",
                f"{role} model_version 必须是无边界空白与 NUL 的非空文本",
                node=node,
                field_path="parameters.model_version",
            )

    def _incoming(self, node_id: str, port_id: str) -> tuple[Edge, ...]:
        return tuple(
            edge
            for edge in self.graph.edges
            if edge.target_node_id == node_id and edge.target_port_id == port_id
        )

    def _check_exact_edges(self, expected: Iterable[_EdgeKey]) -> None:
        expected_counter = Counter(expected)
        actual_counter = Counter(_edge_key(edge) for edge in self.graph.edges)
        if actual_counter == expected_counter:
            return
        missing = tuple((expected_counter - actual_counter).elements())
        extra = tuple((actual_counter - expected_counter).elements())
        self.add(
            "E_AV27_PREFLIGHT_EDGE_SHAPE",
            "Graph producer type/version/port/ordinal 或无旁路拓扑不符合 profile；"
            f"missing={missing[:4]!r}, extra={extra[:4]!r}",
            field_path="edges",
        )


def preflight_av27_profile(
    value: Project | ProjectSnapshot,
    *,
    definitions: Iterable[NodeDefinition] | None = None,
    binding_facts: Av27BindingFacts | None = None,
    publication_facts: Av27PublicationFacts | None = None,
) -> Av27ProfilePreflightResult:
    """纯检查 preparation/expanded profile，并返回严格状态与定位诊断。

    ``ProjectSnapshot`` 自带精确定义；传入普通 ``Project`` 时调用方应同时提供 definitions。若两种
    definition 来源同时出现则失败关闭，避免调用方不清楚哪一份是检查 authority。
    """

    if isinstance(value, ProjectSnapshot):
        if definitions is not None:
            diagnostic = Av27ProfileDiagnostic(
                code="E_AV27_PREFLIGHT_DEFINITIONS_CONFLICT",
                message="ProjectSnapshot 已包含 definitions，不能再提供第二份目录",
                field_path="definitions",
            )
            return Av27ProfilePreflightResult(
                status="incompatible",
                phase=None,
                compatible=False,
                diagnostics=(diagnostic,),
            )
        project = value.project
        definition_values = value.definitions
    else:
        project = value
        definition_values = tuple(definitions or ())
    return _Inspector(
        project,
        tuple(definition_values),
        binding_facts,
        publication_facts,
    ).inspect()


def _expected_definition(node: NodeInstance) -> NodeDefinition | None:
    factories: dict[str, Callable[[], NodeDefinition]] = {
        SOURCE_PROGRAM_TYPE_ID: source_program_definition,
        SOURCE_ADMISSION_TYPE_ID: source_admission_definition,
        MOSAIC_RESTORATION_TYPE_ID: mosaic_restoration_definition,
        ENHANCEMENT_TYPE_ID: enhancement_definition,
        MERGE_VIDEO_TYPE_ID: merge_video_definition,
        FRAME_INTERPOLATION_TYPE_ID: frame_interpolation_definition,
        PROGRAM_ENCODE_TYPE_ID: program_encode_definition,
        FINAL_MUX_TYPE_ID: final_mux_definition,
        _OUTPUT_TYPE_ID: lambda: output_file_definition("MediaFile"),
    }
    factory = factories.get(node.type_id)
    if factory is not None:
        return factory()
    count = atomic_split_count_from_type_id(node.type_id)
    return atomic_split_definition(count) if count is not None else None


def _edge_key(edge: Edge) -> _EdgeKey:
    return (
        edge.source_node_id,
        edge.source_port_id,
        edge.target_node_id,
        edge.target_port_id,
        edge.ordinal,
    )


def _walk_parameter_keys(value: object, prefix: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield path, str(key)
            yield from _walk_parameter_keys(item, path)
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
        for index, item in enumerate(value):
            path = f"{prefix}.{index}" if prefix else str(index)
            yield from _walk_parameter_keys(item, path)


def _strict_int(value: object, *, minimum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        return None
    return value


def _mapping_sequence(value: object) -> tuple[Mapping[str, Any], ...] | None:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return None
    if not all(isinstance(item, Mapping) for item in value):
        return None
    return tuple(item for item in value if isinstance(item, Mapping))


def _string_sequence(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return None
    if not all(isinstance(item, str) and item for item in value):
        return None
    return tuple(str(item) for item in value)


def _selector_boundaries(
    selector: Mapping[str, Any],
    *,
    total_frames: int,
    fps: Fraction,
) -> tuple[int, ...] | None:
    mode = selector.get("mode")
    if mode == "single" and set(selector) == {"mode"}:
        return ()
    if mode == "exact_frames" and set(selector) == {"mode", "frames"}:
        raw = selector.get("frames")
        if not isinstance(raw, Sequence) or isinstance(raw, str | bytes) or not raw:
            return None
        values = tuple(_strict_int(item, minimum=1) for item in raw)
        if any(item is None for item in values):
            return None
        boundaries = tuple(int(item) for item in values if item is not None)
    elif mode == "exact_times" and set(selector) == {"mode", "times"}:
        raw = selector.get("times")
        if not isinstance(raw, Sequence) or isinstance(raw, str | bytes) or not raw:
            return None
        mapped: list[int] = []
        for item in raw:
            if not isinstance(item, str):
                return None
            try:
                moment = parse_canonical_time(item)
            except ValueError:
                return None
            exact_frame = moment * fps
            mapped.append(
                (exact_frame.numerator * 2 + exact_frame.denominator)
                // (2 * exact_frame.denominator)
            )
        boundaries = tuple(mapped)
    else:
        return None
    if (
        boundaries != tuple(sorted(boundaries))
        or len(boundaries) != len(set(boundaries))
        or any(boundary <= 0 or boundary >= total_frames for boundary in boundaries)
    ):
        return None
    return boundaries


def _derive_segments(
    chapters: list[dict[str, Any]],
    *,
    source_fps: Fraction,
    leaf_duration_minutes: int,
    planned_ids: tuple[str, ...],
) -> list[dict[str, Any]] | None:
    try:
        chapter_values = tuple(
            ResolvedChapterPlan.model_validate(chapter, strict=True) for chapter in chapters
        )
        leaves = derive_leaf_plan(
            chapter_values,
            frame_rate=source_fps,
            leaf_duration_minutes=leaf_duration_minutes,
        )
    except (Av27TemplateError, ValueError):
        return None
    return [
        {
            "port_id": leaf.port_id,
            "source_ordinal": leaf.source_ordinal,
            "planned_effective_video_artifact_id": planned_ids[leaf.source_ordinal],
            "chapter_id": leaf.chapter_id,
            "chapter_ordinal": leaf.chapter_ordinal,
            "leaf_id": leaf.leaf_id,
            "leaf_ordinal": leaf.leaf_ordinal,
            "start_frame": leaf.start_frame,
            "end_frame": leaf.end_frame,
        }
        for leaf in leaves
    ]


def _derived_leaf_count(
    chapters: Sequence[Mapping[str, Any]],
    *,
    source_fps: Fraction,
    leaf_duration_minutes: int,
) -> int | None:
    """在物化 leaves 前以 O(chapters) 闭合 count，拒绝不可信巨大 frame range。"""

    frames_per_leaf = round(source_fps * leaf_duration_minutes * 60)
    if frames_per_leaf <= 0:
        return None
    return sum(
        (int(chapter["end_frame"]) - int(chapter["start_frame"]) + frames_per_leaf - 1)
        // frames_per_leaf
        for chapter in chapters
    )


def _geometry(width: int, height: int) -> dict[str, int | str]:
    return {"width": width, "height": height, "sample_aspect_ratio": "1/1"}


def _pure_absolute_path(value: str) -> PurePosixPath | PureWindowsPath | None:
    windows = PureWindowsPath(value)
    if windows.is_absolute():
        return windows
    posix = PurePosixPath(value)
    return posix if posix.is_absolute() else None


def _is_strict_descendant(
    path: PurePosixPath | PureWindowsPath,
    root: PurePosixPath | PureWindowsPath,
) -> bool:
    if type(path) is not type(root) or path == root:
        return False
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _valid_windows_title(value: str) -> bool:
    if (
        not 1 <= len(value) <= 120
        or unicodedata.normalize("NFC", value) != value
        or value.strip() != value
        or value[-1:] in {".", " "}
    ):
        return False
    if value in {".", ".."} or any(ord(character) < 32 for character in value):
        return False
    if any(character in _WINDOWS_FORBIDDEN for character in value):
        return False
    return value.partition(".")[0].casefold().upper() not in _WINDOWS_RESERVED


def _valid_model_text(value: object) -> bool:
    return isinstance(value, str) and bool(value) and value.strip() == value and "\x00" not in value


__all__ = [
    "Av27BindingFacts",
    "Av27ProfileDiagnostic",
    "Av27ProfilePhase",
    "Av27ProfilePreflightResult",
    "Av27ProfileStatus",
    "Av27PublicationFacts",
    "Av27SourceBindingFact",
    "preflight_av27_profile",
]
