"""生成或核对 Python Project Service 向 Studio 暴露的 JSON Schema。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import TypeAdapter

from zniku.avenhance_v27.template import TemplatePreviewRequest
from zniku.project_service.host_bridge import (
    HOST_PATH_REFERENCE_ADAPTER,
    HostCapabilitiesEnvelope,
    HostDialogArguments,
    HostInvokeEnvelope,
    HostInvokeRequest,
    HostSystemArguments,
    HostUserActionEnvelope,
    HostUserActionRequest,
)
from zniku.project_service.models import (
    ExternalHandoffReadiness,
    NodeLogEnvelope,
    PresentationCatalogEnvelope,
    ProjectServiceCommand,
    RunDetailEnvelope,
    RunSummaryPageEnvelope,
    StatusEnvelope,
    TemplatePreviewEnvelope,
)

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "apps" / "studio" / "src" / "service" / "project-service.schema.json"


def require_serialized_properties(schema: object) -> None:
    """让 wire output Schema 精确描述默认 ``model_dump`` 的完整对象字段。"""

    if isinstance(schema, dict):
        properties = schema.get("properties")
        if isinstance(properties, dict):
            schema["required"] = list(properties)
        for value in tuple(schema.values()):
            require_serialized_properties(value)
    elif isinstance(schema, list):
        for value in schema:
            require_serialized_properties(value)


def namespace_definitions(
    schema: dict[str, object],
    *,
    namespace: str,
) -> dict[str, object]:
    """隔离不同 input 与 response 中同名但 required 语义不同的模型。"""

    raw_definitions = schema.pop("$defs", {})
    if not isinstance(raw_definitions, dict):
        raise RuntimeError("Project Service command $defs 必须是 object")
    names = {name: f"{namespace}_{name}" for name in raw_definitions}

    def rewrite(value: object) -> None:
        if isinstance(value, dict):
            for key, child in tuple(value.items()):
                if isinstance(child, str) and child.startswith("#/$defs/"):
                    name = child.removeprefix("#/$defs/")
                    if name in names:
                        value[key] = f"#/$defs/{names[name]}"
                else:
                    rewrite(child)
        elif isinstance(value, list):
            for child in value:
                rewrite(child)

    rewrite(schema)
    rewrite(raw_definitions)
    return {names[name]: definition for name, definition in raw_definitions.items()}


def verify_local_references(schema: dict[str, object]) -> None:
    """拒绝生成任何指向不存在 ``$defs`` 成员的本地 ref 或 discriminator mapping。"""

    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        raise RuntimeError("Project Service response Schema 缺少 $defs")

    def verify(value: object) -> None:
        if isinstance(value, dict):
            for child in value.values():
                verify(child)
        elif isinstance(value, list):
            for child in value:
                verify(child)
        elif isinstance(value, str) and value.startswith("#/$defs/"):
            name = value.removeprefix("#/$defs/")
            if name not in definitions:
                raise RuntimeError(f"Project Service Schema 含悬空本地引用：{value}")

    verify(schema)


def render_schema() -> str:
    """输出 status root、全部定向 response 与 command Schema；它只是 wire drift gate。"""

    response_models = (
        StatusEnvelope,
        RunSummaryPageEnvelope,
        RunDetailEnvelope,
        NodeLogEnvelope,
        ExternalHandoffReadiness,
        PresentationCatalogEnvelope,
        TemplatePreviewEnvelope,
        HostCapabilitiesEnvelope,
        HostUserActionEnvelope,
        HostInvokeEnvelope,
    )
    envelope_schema: dict[str, object] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": "#/$defs/StatusEnvelope",
        "$defs": {},
        "title": "StatusEnvelope",
    }
    envelope_definitions = envelope_schema["$defs"]
    if not isinstance(envelope_definitions, dict):  # pragma: no cover - 本地常量防御
        raise RuntimeError("Project Service response $defs 必须是 object")
    for model in response_models:
        model_schema = model.model_json_schema(mode="serialization")
        require_serialized_properties(model_schema)
        nested = model_schema.pop("$defs", {})
        if not isinstance(nested, dict):
            raise RuntimeError(f"{model.__name__} $defs 必须是 object")
        for name, definition in nested.items():
            existing = envelope_definitions.get(name)
            if existing is not None and existing != definition:
                raise RuntimeError(f"Project Service response Schema definition 冲突：{name}")
            envelope_definitions[name] = definition
        model_schema.pop("$schema", None)
        existing = envelope_definitions.get(model.__name__)
        if existing is not None and existing != model_schema:
            raise RuntimeError(f"Project Service response root definition 冲突：{model.__name__}")
        envelope_definitions[model.__name__] = model_schema

    preview_schema = TypeAdapter(TemplatePreviewRequest).json_schema()
    preview_definitions = namespace_definitions(preview_schema, namespace="Preview")
    for name, definition in preview_definitions.items():
        if name in envelope_definitions:
            raise RuntimeError(f"Project Service Schema definition 冲突：{name}")
        envelope_definitions[name] = definition
    envelope_definitions["TemplatePreviewRequest"] = preview_schema

    host_request_models = (
        HostUserActionRequest,
        HostInvokeRequest,
        HostDialogArguments,
        HostSystemArguments,
    )
    for host_model in host_request_models:
        host_schema = host_model.model_json_schema()
        nested = namespace_definitions(
            host_schema,
            namespace=f"Host_{host_model.__name__}",
        )
        for name, definition in nested.items():
            if name in envelope_definitions:
                raise RuntimeError(f"Project Service HostBridge Schema definition 冲突：{name}")
            envelope_definitions[name] = definition
        envelope_definitions[host_model.__name__] = host_schema
    reference_schema = HOST_PATH_REFERENCE_ADAPTER.json_schema()
    reference_definitions = namespace_definitions(reference_schema, namespace="Host_Reference")
    for name, definition in reference_definitions.items():
        if name in envelope_definitions:
            raise RuntimeError(f"Project Service HostBridge Schema definition 冲突：{name}")
        envelope_definitions[name] = definition
    envelope_definitions["HostPathReference"] = reference_schema

    command_schema = TypeAdapter(ProjectServiceCommand).json_schema()
    command_definitions = namespace_definitions(command_schema, namespace="Command")
    for name, definition in command_definitions.items():
        if name in envelope_definitions:
            raise RuntimeError(f"Project Service Schema definition 冲突：{name}")
        envelope_definitions[name] = definition
    if "ProjectServiceCommand" in envelope_definitions:
        raise RuntimeError("ProjectServiceCommand Schema 名称冲突")
    envelope_definitions["ProjectServiceCommand"] = command_schema
    verify_local_references(envelope_schema)

    return (
        json.dumps(
            envelope_schema,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )


def main() -> int:
    """默认更新 checked-in Schema，``--check`` 只比较且不写文件。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    expected = render_schema()
    if arguments.check:
        if not TARGET.is_file() or TARGET.read_text("utf-8") != expected:
            raise SystemExit("Project Service Schema 已漂移；请重新运行生成器")
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(expected, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
