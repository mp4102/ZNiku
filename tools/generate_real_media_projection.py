"""生成 Studio 使用的真实媒体 host JSON Schema；只写确定性合同投影。"""

from __future__ import annotations

import json
from pathlib import Path

from zniku.realmedia import RealHostEnvelope

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "apps" / "studio" / "src" / "formal" / "real-media-host.schema.json"


def main() -> None:
    schema = RealHostEnvelope.model_json_schema()
    TARGET.write_text(
        json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
