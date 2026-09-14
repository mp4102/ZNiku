"""复用既有生成器结构，盘点普通工作 exact Schema/展示；不执行任何媒体操作。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal

from generate_overlap_studio_corpus import schema_inventory

from zniku.prepared_source.work_definitions import built_in_overlap_definitions, external_definition
from zniku.presentation import build_builtin_presentation_catalog
from zniku.source_preparation.work_definitions import source_preparation_definitions

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "docs/architecture/studio-prepared-work-schema-corpus.json"


def corpus_document() -> dict[str, Any]:
    containers: tuple[Literal["mp4", "mov", "mkv"], ...] = ("mp4", "mov", "mkv")
    definitions = (
        *source_preparation_definitions(),
        *built_in_overlap_definitions(),
        *(external_definition(container) for container in containers),
    )
    return {
        "contract_kind": "studio_prepared_work_schema_authoring_corpus",
        "contract_version": "0.3.4-work.1",
        "description": "普通工作节点 Python Schema 机器盘点，不是新运行权威。",
        "definitions": [
            {
                "identity": {"type_id": item.type_id, "version": item.version},
                "parameter_schema": item.model_dump(mode="json")["parameter_schema"],
                "features": schema_inventory(item.parameter_schema),
            }
            for item in definitions
        ],
        "presentation": build_builtin_presentation_catalog(definitions).model_dump(mode="json"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--check", action="store_true")
    choice.add_argument("--write", action="store_true")
    args = parser.parse_args()
    content = json.dumps(corpus_document(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.write:
        CORPUS.write_text(content, encoding="utf-8", newline="\n")
    elif not CORPUS.is_file() or CORPUS.read_text(encoding="utf-8") != content:
        raise SystemExit(f"Generated working-source Studio asset differs: {CORPUS.name}")
    print(f"OK {CORPUS.name}")


if __name__ == "__main__":
    main()
