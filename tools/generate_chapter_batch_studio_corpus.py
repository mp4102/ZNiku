"""生成章级批量新定义的独立 Schema 盘点，旧精确定义 corpus 不改写。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from generate_overlap_studio_corpus import schema_inventory

from zniku.chapter_batch.definitions import built_in_definitions, definition

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "docs/architecture/studio-chapter-batch-schema-corpus.json"


def corpus_document() -> dict[str, Any]:
    """单叶与多叶使用同一严格参数族；盘点是测试输入，不是第二套合同权威。"""
    entries = []
    for item in (
        *built_in_definitions(),
        definition("enhancement", 2),
        definition("enhancement", 5),
    ):
        schema = item.model_dump(mode="json")["parameter_schema"]
        entries.append(
            {
                "identity": {"type_id": item.type_id, "version": item.version},
                "parameter_schema": schema,
                "features": schema_inventory(schema),
            }
        )
    return {
        "contract_kind": "studio_chapter_batch_schema_authoring_corpus",
        "contract_version": "0.3.5",
        "definitions": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    text = json.dumps(corpus_document(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.write:
        TARGET.write_text(text, encoding="utf-8", newline="\n")
    elif not TARGET.is_file() or TARGET.read_text("utf-8") != text:
        raise SystemExit("Generated chapter-batch Studio corpus differs")
    print(f"OK {TARGET.name}")


if __name__ == "__main__":
    main()
