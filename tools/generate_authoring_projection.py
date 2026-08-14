"""重新生成 Phase 2A Python→Studio 投影产物；它不是产品 CLI。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from zniku.authoring.projection import (
    assert_projection_current,
    build_synthetic_studio_fixture,
    generate_projection,
)
from zniku.contracts import EngineManifest


def main() -> None:
    parser = argparse.ArgumentParser(description="生成或检查 Studio authoring projection")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只检查 checked-in projection 与 Python authority 是否一致",
    )
    arguments = parser.parse_args()
    repository_root = Path(__file__).resolve().parents[1]
    output_directory = repository_root / "apps" / "studio" / "src" / "generated"
    engine_manifest = EngineManifest.from_json(
        (repository_root / "tests" / "fixtures" / "program-media-engine-manifest.json").read_bytes()
    )
    fixture_path = (
        repository_root / "apps" / "studio" / "src" / "test" / "fixtures" / "python-authority.json"
    )
    fixture_payload = (
        json.dumps(
            build_synthetic_studio_fixture(engine_manifest),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    if arguments.check:
        assert_projection_current(output_directory)
        if fixture_path.read_text(encoding="utf-8") != fixture_payload:
            raise AssertionError("Studio Python authority fixture 已漂移")
        print("Studio authoring projection 与 Python authority 一致")
        return

    manifest = generate_projection(output_directory)
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(
        fixture_payload,
        encoding="utf-8",
        newline="\n",
    )
    print(
        "已生成 Studio authoring projection: "
        f"{len(manifest.files)} files, core={manifest.core_node_contract_digest}"
    )


if __name__ == "__main__":
    main()
