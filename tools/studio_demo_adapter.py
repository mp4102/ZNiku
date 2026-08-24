"""为 Phase 3 Studio 验收工程提供纯文本、无媒体 I/O 的合成 command adapter。"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def main() -> int:
    """只创建或复制调用方明确给出的 attempt 文件，不解释 shell。"""

    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="operation", required=True)
    source = subcommands.add_parser("source")
    source.add_argument("message")
    source.add_argument("output", type=Path)
    transform = subcommands.add_parser("transform")
    transform.add_argument("input", type=Path)
    transform.add_argument("output", type=Path)
    transform.add_argument("uppercase", choices=("true", "false"))
    copy = subcommands.add_parser("copy")
    copy.add_argument("input", type=Path)
    copy.add_argument("output", type=Path)
    arguments = parser.parse_args()

    if arguments.operation == "source":
        arguments.output.write_text(arguments.message + "\n", encoding="utf-8")
        print("synthetic source created")
    elif arguments.operation == "transform":
        content = arguments.input.read_text("utf-8")
        if arguments.uppercase == "true":
            content = content.upper()
        arguments.output.write_text(content, encoding="utf-8")
        print("synthetic transform completed")
    else:
        shutil.copyfile(arguments.input, arguments.output)
        print("synthetic publish completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
