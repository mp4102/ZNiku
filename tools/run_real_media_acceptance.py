"""运行真实媒体候选完整门并把无路径报告写入本地工作根。"""

from __future__ import annotations

import argparse
from pathlib import Path

from zniku.realmedia import run_real_media_acceptance


def main() -> None:
    parser = argparse.ArgumentParser(description="ZNIKU 0.1.0 real media acceptance gate")
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    report = run_real_media_acceptance(reference=args.reference, root=args.root)
    output = args.root / "acceptance-report.json"
    output.write_bytes(report.to_canonical_bytes())
    print(report.to_canonical_json())


if __name__ == "__main__":
    main()
