"""在临时目录执行 Phase 6 短媒体、目标存储与长片性能开发门。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from zniku.validation import (
    publish_file_no_replace,
    run_long_film_gate,
    run_short_media_gate,
)


def main() -> None:
    """运行不会向仓库写入媒体 payload 的独立 Phase 6 gate。"""

    with tempfile.TemporaryDirectory(prefix="zniku-phase6-") as temporary:
        root = Path(temporary)
        media_directory = root / "media"
        target_directory = root / "target"
        media_directory.mkdir()
        target_directory.mkdir()
        probe = run_short_media_gate(media_directory)
        receipt = publish_file_no_replace(
            media_directory / probe.filename,
            target_directory,
            "phase6-published.mkv",
        )
        performance = run_long_film_gate()
        result = {
            "short_media": probe.to_data(),
            "publication": receipt.to_data(),
            "long_film": performance.to_data(),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
