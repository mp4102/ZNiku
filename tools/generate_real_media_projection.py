"""生成 0.1.0 真实媒体 host 的 legacy 测试 Schema；不再进入正式 Studio。"""

from __future__ import annotations

import json
from pathlib import Path

from zniku.realmedia import RealHostEnvelope

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tests" / "fixtures" / "legacy" / "real-media-host.schema.json"


def main() -> None:
    schema = RealHostEnvelope.model_json_schema()
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(
        json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
