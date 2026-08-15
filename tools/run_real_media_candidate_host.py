"""启动 loopback Real Media Acceptance Candidate host 的开发期入口。"""

from __future__ import annotations

import argparse
from pathlib import Path

from zniku.realmedia import serve_real_media_host


def main() -> None:
    parser = argparse.ArgumentParser(description="ZNIKU 0.1.0 real media candidate host")
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = serve_real_media_host(root=args.root, reference=args.reference, port=args.port)
    print(f"ZNIKU real media host listening on http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
