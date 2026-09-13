"""生成独立 0.3.2 Studio Schema 盘点及纯合成预览，不读取媒体或改变旧语料库。

Schema 和样例由正式 Python 模型派生；机器盘点没有独立语义权威。默认只检查，显式 --write
才更新两份已命名的开发资产，不接触工程、服务、attempt 或候选包。
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from zniku.chapter_overlap import (
    AdmittedTimeline,
    AverageChapterSelector,
    ChapterSettings,
    plan_chapters_and_leaves,
)
from zniku.chapter_overlap.context import ExperimentalContextSettings, plan_experimental_contexts
from zniku.chapter_overlap.definitions import atomic_split_definition, built_in_overlap_definitions
from zniku.chapter_overlap.template import OverlapProcessing, OverlapPublication
from zniku.project_service.chapter_overlap import ChapterOverlapFullEnvelope

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "docs/architecture/studio-overlap-schema-corpus.json"
PREVIEW = ROOT / "apps/studio/src/studio/__fixtures__/overlap-preview.json"


def schema_inventory(schema: Mapping[str, Any]) -> dict[str, list[str]]:
    """盘点 Schema 位置，不把 properties/definitions 中的业务字段名当作关键字。"""
    keywords: set[str] = set()
    types: set[str] = set()
    refs: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, bool):
            return
        assert isinstance(node, Mapping)
        keywords.update(node)
        if "type" in node:
            assert isinstance(node["type"], str)
            types.add(node["type"])
        if "$ref" in node:
            refs.add(node["$ref"])
        for group in ("properties", "$defs"):
            for child in node.get(group, {}).values():
                visit(child)
        for key in ("items", "additionalProperties", "not", "if", "then", "else"):
            if key in node:
                visit(node[key])
        for key in ("allOf", "oneOf", "anyOf", "prefixItems"):
            for child in node.get(key, []):
                visit(child)

    visit(schema)
    return {
        "schema_keywords": sorted(keywords),
        "json_types": sorted(types),
        "local_refs": sorted(refs),
    }


def corpus_document() -> dict[str, Any]:
    """八类节点与最小动态二叶样例完整投影；旧 AV27 仍由旧 corpus 单独锁定。"""
    entries = []
    for definition in (*built_in_overlap_definitions(), atomic_split_definition(2)):
        data = definition.model_dump(mode="json")
        entries.append(
            {
                "identity": {"type_id": definition.type_id, "version": definition.version},
                "parameter_schema": data["parameter_schema"],
                "features": schema_inventory(data["parameter_schema"]),
            }
        )
    return {
        "contract_kind": "studio_overlap_schema_authoring_corpus",
        "contract_version": "0.3.2",
        "description": (
            "Python NodeDefinition 机器盘点；不是语义权威，不改变旧 studio-schema-corpus.json。"
        ),
        "definitions": entries,
    }


def preview_document() -> dict[str, Any]:
    """合成 1801 帧平均三章；不伪造真实 AI、媒体 probe 或可执行 Graph。"""
    settings = ChapterSettings(chapter_selector=AverageChapterSelector(count=3))
    plan = plan_chapters_and_leaves(
        AdmittedTimeline(
            artifact_id="00000000-0000-4000-8000-000000000001",
            frame_count=1801,
            frame_rate="30000/1001",
        ),
        settings,
    )
    processing = OverlapProcessing.model_validate_json(
        json.dumps(
            {
                "settings": settings.model_dump(mode="json"),
                "enhancement": {"model_name": "Synthetic enhancement", "actual_scale_factor": 2},
                "program_encode": {"encoder": "cpu"},
            }
        )
    )
    return ChapterOverlapFullEnvelope(
        project_session_id="00000000-0000-4000-8000-000000000002",
        storage_revision=1,
        preparation_run_id="00000000-0000-4000-8000-000000000003",
        processing=processing,
        plan=plan,
        contexts=plan_experimental_contexts(
            plan,
            ExperimentalContextSettings(
                left_context_frames=32, right_context_frames=32, minimum_input_frames=2
            ),
        ),
        publication=OverlapPublication(output_target_path="synthetic-output/Example.mkv"),
        node_count=24,
        edge_count=35,
    ).model_dump(mode="json")


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
            raise SystemExit(f"Generated overlap Studio asset differs: {path.name}")
        print(f"OK {path.name}")


if __name__ == "__main__":
    main()
