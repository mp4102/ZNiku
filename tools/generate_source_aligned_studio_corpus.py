"""生成独立原片规划 Schema 盘点与合成 UI 预览；旧 corpus 保持不变。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from generate_overlap_studio_corpus import preview_document as legacy_preview
from generate_overlap_studio_corpus import schema_inventory

from zniku.project_service.source_aligned import SourceAlignedFullEnvelope
from zniku.source_aligned.definitions import (
    atomic_split_definition,
    built_in_overlap_definitions,
    external_definition,
)

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "docs/architecture/studio-source-aligned-schema-corpus.json"
PREVIEW = ROOT / "apps/studio/src/studio/__fixtures__/source-aligned-preview.json"


def corpus_document() -> dict[str, Any]:
    """完整投影三种外部封装与八类节点，不把机器盘点变成语义 authority。"""
    entries = []
    for definition in (
        *built_in_overlap_definitions(),
        atomic_split_definition(2),
        external_definition("mp4"),
        external_definition("mov"),
        external_definition("mkv"),
    ):
        schema = definition.model_dump(mode="json")["parameter_schema"]
        entries.append(
            {
                "identity": {"type_id": definition.type_id, "version": definition.version},
                "parameter_schema": schema,
                "features": schema_inventory(schema),
            }
        )
    return {
        "contract_kind": "studio_source_aligned_schema_authoring_corpus",
        "contract_version": "0.3.3",
        "definitions": entries,
    }


def preview_document() -> dict[str, Any]:
    """仅复用纯数学合成样本，不复用旧版外部产物或伪造有效输入 UUID。"""
    data = legacy_preview()
    data.update(
        contract_version="0.3.3", profile_version="0.3.3", profile_id="zniku.source-aligned-overlap"
    )
    data["processing"]["mr"] = {"mode": "off"}
    return SourceAlignedFullEnvelope.model_validate(data).model_dump(mode="json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    arguments = parser.parse_args()
    for path, document in ((CORPUS, corpus_document()), (PREVIEW, preview_document())):
        data = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if arguments.write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data, encoding="utf-8", newline="\n")
        elif not path.is_file() or path.read_text("utf-8") != data:
            raise SystemExit(f"Generated source-aligned Studio asset differs: {path.name}")
        print(f"OK {path.name}")


if __name__ == "__main__":
    main()
