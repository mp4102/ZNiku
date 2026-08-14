"""为 Studio 集成测试提供 JSONL Python bridge；不是产品 API 或 CLI。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, cast

from zniku.authoring import (
    AuthoringCommand,
    AuthoringService,
    CoreNodeContractSet,
    InMemoryManifestCatalog,
    WorkflowCompiler,
    WorkflowDraftSnapshot,
)
from zniku.contracts import EngineBinding, EngineManifest
from zniku.pipelines import build_default_workflow


def main() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    fixture_path = (
        repository_root / "apps" / "studio" / "src" / "test" / "fixtures" / "python-authority.json"
    )
    fixture = cast(dict[str, Any], json.loads(fixture_path.read_text(encoding="utf-8")))
    manifest = EngineManifest.from_data(fixture["manifest"])
    initial = WorkflowDraftSnapshot.from_data(fixture["initial"])
    default = build_default_workflow()
    catalog = InMemoryManifestCatalog((manifest, *default.manifests))
    service = AuthoringService(WorkflowCompiler(catalog, CoreNodeContractSet.phase_2a()))
    service.create_draft(initial.draft_id, initial.spec)
    service.create_draft("draft.default", default.spec)

    for line in sys.stdin:
        request = cast(dict[str, Any], json.loads(line))
        operation = request.get("operation")
        payload = request.get("payload")
        if operation == "load_draft":
            draft_id = cast(dict[str, Any], payload)["draft_id"]
            response: Any = service.get_snapshot(draft_id).to_data()
        elif operation == "load_manifest":
            binding = EngineBinding.from_data(cast(dict[str, Any], payload))
            resolved = catalog.resolve(binding)
            if resolved is None:
                raise ValueError("E_ENGINE_BINDING_UNKNOWN")
            response = resolved.to_data()
        elif operation == "apply_command":
            command = AuthoringCommand.from_data(cast(dict[str, Any], payload))
            response = service.apply(command).to_data()
        else:
            raise ValueError(f"未知测试 operation: {operation}")
        sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
