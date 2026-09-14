"""从正式 Python definitions 和 Presentation 生成 0.3.4-color.1 机器盘点。

本文件不读取媒体，不创建工程，不成为新语义 authority。默认及 --check 只检查；
--write 仅更新已命名的机器生成 JSON，不改旧版本 corpus。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from generate_overlap_studio_corpus import schema_inventory

from zniku.prepared_color.definitions import built_in_overlap_definitions, external_definition
from zniku.presentation import build_builtin_presentation_catalog
from zniku.source_color.definitions import source_preparation_definitions

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "docs/architecture/studio-prepared-color-schema-corpus.json"


def corpus_document() -> dict[str, Any]:
    """投影七类准备定义、十一类下游定义及其正式中文展示，顺序稳定。"""
    definitions = (
        *source_preparation_definitions(),
        *built_in_overlap_definitions(),
        external_definition("mp4"),
        external_definition("mov"),
        external_definition("mkv"),
    )
    presentation = build_builtin_presentation_catalog(definitions)
    return {
        "contract_kind": "studio_prepared_color_schema_authoring_corpus",
        "contract_version": "0.3.4-color.1",
        "description": (
            "Python exact NodeDefinition 与纯数据 Presentation 的机器盘点，不是语义权威。"
        ),
        "definitions": [
            {
                "identity": {"type_id": definition.type_id, "version": definition.version},
                "parameter_schema": definition.model_dump(mode="json")["parameter_schema"],
                "features": schema_inventory(definition.parameter_schema),
            }
            for definition in definitions
        ],
        "presentation": presentation.model_dump(mode="json"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--check", action="store_true")
    choice.add_argument("--write", action="store_true")
    arguments = parser.parse_args()
    content = json.dumps(corpus_document(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if arguments.write:
        CORPUS.write_text(content, encoding="utf-8", newline="\n")
    elif not CORPUS.is_file() or CORPUS.read_text(encoding="utf-8") != content:
        raise SystemExit(f"Generated prepared-color Studio asset differs: {CORPUS.name}")
    print(f"OK {CORPUS.name}")


if __name__ == "__main__":
    main()
