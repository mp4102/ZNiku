"""生成单一源准入的独立 Schema 盘点与合成预览，不修改旧版机器语料。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from generate_overlap_studio_corpus import schema_inventory
from generate_source_aligned_studio_corpus import preview_document as aligned_preview

from zniku.project_service.source_admitted import SourceAdmittedFullEnvelope
from zniku.source_admission.definitions import atomic_split_definition, built_in_definitions

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "docs/architecture/studio-source-admitted-schema-corpus.json"
PREVIEW = ROOT / "apps/studio/src/studio/__fixtures__/source-admitted-preview.json"


def corpus_document() -> dict[str, Any]:
    """盘点实际 exact definitions；它不是新的语义权威。"""
    entries = []
    for definition in (*built_in_definitions(), atomic_split_definition(2)):
        schema = definition.model_dump(mode="json")["parameter_schema"]
        entries.append(
            {
                "identity": {"type_id": definition.type_id, "version": definition.version},
                "parameter_schema": schema,
                "features": schema_inventory(schema),
            }
        )
    return {
        "contract_kind": "studio_source_admitted_schema_authoring_corpus",
        "contract_version": "0.3.5",
        "definitions": entries,
    }


def preview_document() -> dict[str, Any]:
    """数学样本不冒充真实 Source 分析；用于 Python/TypeScript wire 对照。"""
    data = aligned_preview()
    data.update(
        contract_version="0.3.5",
        profile_version="0.3.5",
        profile_id="zniku.source-admitted-overlap",
        warnings=[],
    )
    return SourceAdmittedFullEnvelope.model_validate_json(json.dumps(data)).model_dump(mode="json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    for path, document in ((CORPUS, corpus_document()), (PREVIEW, preview_document())):
        data = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.write:
            path.write_text(data, encoding="utf-8", newline="\n")
        elif not path.is_file() or path.read_text("utf-8") != data:
            raise SystemExit(f"Generated source-admitted Studio asset differs: {path.name}")
        print(f"OK {path.name}")


if __name__ == "__main__":
    main()
