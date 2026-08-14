"""从 Python Schema 确定性生成 Studio DTO、runtime Schema 与 core-node projection。

生成器只处理 Pydantic 当前导出的闭合 JSON Schema 子集。TypeScript 文件明确禁止手改；Studio 入站
payload 由同一份 checked-in Schema 直接驱动 Ajv 2020 runtime validation。生成清单不包含自身摘要，
避免递归 hash；它精确列出其他全部投影产物。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal, cast

import rfc8785
from pydantic import TypeAdapter

from zniku.contracts import EngineBinding, EngineManifest

from .compiler import InMemoryManifestCatalog, WorkflowCompiler
from .models import (
    AUTHORING_CONTRACT_VERSION,
    COMPILER_CONTRACT_VERSION,
    DIAGNOSTIC_CONTRACT_VERSION,
    PROJECTION_CONTRACT_VERSION,
    WORKFLOW_CONTRACT_VERSION,
    AuthoringCommand,
    AuthoringCommandRejected,
    ConnectPortsIntent,
    CoreNodeContractSet,
    DisconnectPortsIntent,
    EngineStageNodeSpec,
    FinalNodeSpec,
    PortEndpoint,
    ProjectionFile,
    ProjectionManifest,
    ProjectionSourceSchema,
    ReplaceParametersIntent,
    SourceNodeSpec,
    WireParseFailure,
    WorkflowDraftSnapshot,
    WorkflowEdgeSpec,
    WorkflowSpec,
)
from .service import AuthoringService

GENERATOR_VERSION: Literal["zniku-projection/0.1.0"] = "zniku-projection/0.1.0"

type WireDocument = (
    AuthoringCommand | WorkflowDraftSnapshot | WireParseFailure | AuthoringCommandRejected
)


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _jcs_digest(value: Any) -> str:
    return _sha256_bytes(cast(bytes, rfc8785.dumps(value)))


def _remove_openapi_annotations(value: Any) -> None:
    """移除 Pydantic discriminator 注解；JSON Schema 的 const+anyOf 已完整表达联合。"""

    if isinstance(value, dict):
        value.pop("discriminator", None)
        for item in value.values():
            _remove_openapi_annotations(item)
    elif isinstance(value, list):
        for item in value:
            _remove_openapi_annotations(item)


def _schema(adapter: TypeAdapter[Any], *, title: str, schema_id: str) -> dict[str, Any]:
    schema = adapter.json_schema()
    _remove_openapi_annotations(schema)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = schema_id
    schema["title"] = title
    return schema


def build_source_schemas() -> dict[str, dict[str, Any]]:
    """构造 authoring、Engine、core node 与 projection manifest 的正式 wire Schema。"""

    return {
        "authoring-wire.schema.json": _schema(
            TypeAdapter(WireDocument),
            title="AuthoringWireDocument",
            schema_id="urn:zniku:schema:authoring-wire:0.1.0",
        ),
        "engine-manifest.schema.json": _schema(
            TypeAdapter(EngineManifest),
            title="EngineManifest",
            schema_id="urn:zniku:schema:engine-manifest:0.1.0",
        ),
        "core-node-contracts.schema.json": _schema(
            TypeAdapter(CoreNodeContractSet),
            title="CoreNodeContractSet",
            schema_id="urn:zniku:schema:core-node-contracts:0.1.0",
        ),
        "projection-manifest.schema.json": _schema(
            TypeAdapter(ProjectionManifest),
            title="ProjectionManifest",
            schema_id="urn:zniku:schema:projection-manifest:0.1.0",
        ),
    }


def _ts_name(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_$]", "_", value)
    if not sanitized or sanitized[0].isdigit():
        sanitized = f"_{sanitized}"
    return sanitized


def _ts_literal(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _ts_type(schema: Any) -> str:
    if isinstance(schema, bool):
        return "unknown" if schema else "never"
    if not isinstance(schema, dict):
        return "unknown"
    if "$ref" in schema:
        return _ts_name(str(schema["$ref"]).rsplit("/", maxsplit=1)[-1])
    if "const" in schema:
        return _ts_literal(schema["const"])
    if "enum" in schema:
        return " | ".join(_ts_literal(item) for item in schema["enum"])
    for union_key in ("anyOf", "oneOf"):
        if union_key in schema:
            members = tuple(dict.fromkeys(_ts_type(item) for item in schema[union_key]))
            return " | ".join(members)
    if "allOf" in schema:
        return " & ".join(_ts_type(item) for item in schema["allOf"])

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        return " | ".join(_ts_type({"type": item}) for item in schema_type)
    if schema_type == "null":
        return "null"
    if schema_type == "boolean":
        return "boolean"
    if schema_type in {"integer", "number"}:
        return "number"
    if schema_type == "string":
        return "string"
    if schema_type == "array":
        if "prefixItems" in schema:
            return "readonly [" + ", ".join(_ts_type(item) for item in schema["prefixItems"]) + "]"
        return f"ReadonlyArray<{_ts_type(schema.get('items', True))}>"
    if schema_type == "object" or "properties" in schema or "additionalProperties" in schema:
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        fields: list[str] = []
        for name in sorted(properties):
            optional = "" if name in required else "?"
            fields.append(f"readonly {json.dumps(name)}{optional}: {_ts_type(properties[name])}")
        additional = schema.get("additionalProperties", False)
        if not fields and additional is not False:
            return f"Readonly<Record<string, {_ts_type(additional)}>>"
        body = "; ".join(fields)
        object_type = f"{{ {body} }}" if body else "Record<string, never>"
        if additional is not False:
            return f"({object_type} & Readonly<Record<string, {_ts_type(additional)}>>)"
        return object_type
    return "unknown"


def _render_definition(name: str, schema: dict[str, Any]) -> str:
    description = str(schema.get("description", "")).strip().replace("*/", "* /")
    comment = f"/** {description} */\n" if description else ""
    if schema.get("type") == "object" and "properties" in schema:
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        lines = [f"{comment}export interface {_ts_name(name)} {{"]
        for property_name in sorted(properties):
            optional = "" if property_name in required else "?"
            lines.append(
                f"  readonly {json.dumps(property_name)}{optional}: "
                f"{_ts_type(properties[property_name])}"
            )
        additional = schema.get("additionalProperties", False)
        if additional is not False:
            lines.append(f"  readonly [key: string]: {_ts_type(additional)}")
        lines.append("}")
        return "\n".join(lines)
    return f"{comment}export type {_ts_name(name)} = {_ts_type(schema)}"


def schema_to_typescript(schema: dict[str, Any]) -> str:
    """从 checked-in JSON Schema 生成不含业务判断的 TypeScript DTO。"""

    title = _ts_name(str(schema.get("title", "GeneratedDocument")))
    definitions = cast(dict[str, dict[str, Any]], schema.get("$defs", {}))
    sections = [
        "/* eslint-disable */",
        "// AUTO-GENERATED BY zniku.authoring.projection. DO NOT EDIT.",
        "",
    ]
    for name in sorted(definitions):
        sections.append(_render_definition(name, definitions[name]))
        sections.append("")
    root_without_metadata = {
        key: value
        for key, value in schema.items()
        if key not in {"$defs", "$schema", "$id", "title", "description"}
    }
    if root_without_metadata.get("type") == "object" and "properties" in root_without_metadata:
        sections.append(_render_definition(title, root_without_metadata))
    else:
        sections.append(f"export type {title} = {_ts_type(root_without_metadata)}")
    sections.append("")
    return "\n".join(sections)


def build_projection_files(core_contracts: CoreNodeContractSet) -> dict[str, bytes]:
    """在内存中生成除 projection manifest 本身之外的全部投影文件。"""

    schemas = build_source_schemas()
    files: dict[str, bytes] = {}
    for schema_name, schema in sorted(schemas.items()):
        files[schema_name] = (
            json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        ts_name = schema_name.replace(".schema.json", ".generated.ts")
        files[ts_name] = schema_to_typescript(schema).encode("utf-8")
    files["core-node-contracts.json"] = (
        json.dumps(core_contracts.to_data(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return files


def build_projection_manifest(
    core_contracts: CoreNodeContractSet,
    files: dict[str, bytes],
) -> ProjectionManifest:
    """用 source Schema JCS digest 与生成文件原始字节 digest 构造清单。"""

    schemas = build_source_schemas()
    return ProjectionManifest(
        projection_contract_version=PROJECTION_CONTRACT_VERSION,
        workflow_contract_version=WORKFLOW_CONTRACT_VERSION,
        authoring_contract_version=AUTHORING_CONTRACT_VERSION,
        compiler_contract_version=COMPILER_CONTRACT_VERSION,
        diagnostic_contract_version=DIAGNOSTIC_CONTRACT_VERSION,
        generator_version=GENERATOR_VERSION,
        core_node_contract_digest=core_contracts.sha256_digest(),
        source_schemas=tuple(
            ProjectionSourceSchema(name=name, schema_digest=_jcs_digest(schema))
            for name, schema in schemas.items()
        ),
        files=tuple(
            ProjectionFile(path=f"generated/{name}", file_digest=_sha256_bytes(payload))
            for name, payload in files.items()
        ),
    )


def generate_projection(output_directory: Path) -> ProjectionManifest:
    """确定性写入 Studio projection；目录外不产生副作用。"""

    core_contracts = CoreNodeContractSet.phase_2a()
    files = build_projection_files(core_contracts)
    manifest = build_projection_manifest(core_contracts, files)
    output_directory.mkdir(parents=True, exist_ok=True)
    for name, payload in files.items():
        (output_directory / name).write_bytes(payload)
    (output_directory / "projection-manifest.json").write_text(
        json.dumps(manifest.to_data(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest


def assert_projection_current(output_directory: Path) -> None:
    """在内存重建并逐字节确认 checked-in projection 没有漂移。"""

    core_contracts = CoreNodeContractSet.phase_2a()
    files = build_projection_files(core_contracts)
    manifest = build_projection_manifest(core_contracts, files)
    expected = dict(files)
    expected["projection-manifest.json"] = (
        json.dumps(manifest.to_data(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    actual_names = {
        path.name
        for path in output_directory.iterdir()
        if path.is_file() and not path.name.startswith(".")
    }
    if actual_names != set(expected):
        missing = sorted(set(expected) - actual_names)
        extra = sorted(actual_names - set(expected))
        raise AssertionError(f"projection 文件集合漂移: missing={missing}, extra={extra}")
    for name, payload in expected.items():
        if (output_directory / name).read_bytes() != payload:
            raise AssertionError(f"projection 文件内容漂移: {name}")


def build_synthetic_studio_fixture(manifest: EngineManifest) -> dict[str, Any]:
    """用真实 Python Compiler/Authoring Service 生成 Studio 组件测试 transcript。"""

    core = CoreNodeContractSet.phase_2a()
    compiler = WorkflowCompiler(InMemoryManifestCatalog((manifest,)), core)
    initial_spec = WorkflowSpec(
        workflow_contract_version=WORKFLOW_CONTRACT_VERSION,
        workflow_id="workflow.synthetic.program",
        nodes=(
            SourceNodeSpec(kind="source", node_id="node.source.program"),
            EngineStageNodeSpec(
                kind="engine_stage",
                node_id="node.engine.filter",
                engine=EngineBinding.from_manifest(manifest),
                parameters={"strength": 5},
            ),
            FinalNodeSpec(kind="final", node_id="node.final.program"),
        ),
        edges=(
            WorkflowEdgeSpec(
                edge_id="edge.source.filter",
                source=PortEndpoint(node_id="node.source.program", port_id="program"),
                target=PortEndpoint(node_id="node.engine.filter", port_id="program_in"),
            ),
        ),
    )

    def connected_service() -> tuple[AuthoringService, WorkflowDraftSnapshot]:
        service = AuthoringService(compiler)
        initial = service.create_draft("draft.synthetic.program", initial_spec)
        response = service.apply(
            AuthoringCommand(
                authoring_contract_version=AUTHORING_CONTRACT_VERSION,
                command_id="command.fixture.connect",
                draft_id=initial.draft_id,
                base_revision=0,
                intent=ConnectPortsIntent(
                    intent_kind="connect_ports",
                    source=PortEndpoint(node_id="node.engine.filter", port_id="program_out"),
                    target=PortEndpoint(node_id="node.final.program", port_id="program"),
                ),
            )
        )
        if not isinstance(response, WorkflowDraftSnapshot):  # pragma: no cover
            raise AssertionError("合成 connect command 必须成功")
        return service, response

    parameter_service, connected = connected_service()
    invalid_parameters = parameter_service.apply(
        AuthoringCommand(
            authoring_contract_version=AUTHORING_CONTRACT_VERSION,
            command_id="command.fixture.parameters.invalid",
            draft_id=connected.draft_id,
            base_revision=connected.spec_revision,
            intent=ReplaceParametersIntent(
                intent_kind="replace_parameters",
                node_id="node.engine.filter",
                parameters={"strength": 99},
            ),
        )
    )
    if not isinstance(invalid_parameters, WorkflowDraftSnapshot):  # pragma: no cover
        raise AssertionError("合成 parameter command 必须形成 invalid Draft")

    disconnect_service, connected_for_disconnect = connected_service()
    generated_edge = next(
        edge for edge in connected_for_disconnect.spec.edges if edge.edge_id != "edge.source.filter"
    )
    disconnected = disconnect_service.apply(
        AuthoringCommand(
            authoring_contract_version=AUTHORING_CONTRACT_VERSION,
            command_id="command.fixture.disconnect",
            draft_id=connected_for_disconnect.draft_id,
            base_revision=connected_for_disconnect.spec_revision,
            intent=DisconnectPortsIntent(
                intent_kind="disconnect_ports",
                edge_id=generated_edge.edge_id,
            ),
        )
    )
    if not isinstance(disconnected, WorkflowDraftSnapshot):  # pragma: no cover
        raise AssertionError("合成 disconnect command 必须成功")

    stale_service, connected_for_stale = connected_service()
    stale_rejection = stale_service.apply(
        AuthoringCommand(
            authoring_contract_version=AUTHORING_CONTRACT_VERSION,
            command_id="command.fixture.stale",
            draft_id=connected_for_stale.draft_id,
            base_revision=0,
            intent=DisconnectPortsIntent(
                intent_kind="disconnect_ports",
                edge_id="edge.source.filter",
            ),
        )
    )
    if not isinstance(stale_rejection, AuthoringCommandRejected):  # pragma: no cover
        raise AssertionError("合成 stale command 必须被拒绝")

    initial_service = AuthoringService(compiler)
    initial = initial_service.create_draft("draft.synthetic.program", initial_spec)
    return {
        "manifest": manifest.to_data(),
        "initial": initial.to_data(),
        "connected": connected.to_data(),
        "invalid_parameters": invalid_parameters.to_data(),
        "disconnected": disconnected.to_data(),
        "stale_rejection": stale_rejection.to_data(),
    }
