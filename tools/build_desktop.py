"""构建 Windows 本地桌面候选，打包 production Studio、Python 与显式提供的媒体工具。

本工具只供开发构建使用，首次创作者只需解压后双击 exe。输出必须是全新目录，不删除或覆盖
既有包。FFmpeg 分发目录必须同时提供原 LICENSE/README；本地构建不表示公开再分发合规审阅。
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import subprocess
import sys
from pathlib import Path

from zniku.desktop.windows import is_windows

ROOT = Path(__file__).resolve().parents[1]


def build_arguments(media_root: Path, output: Path) -> list[str]:
    """构造固定 PyInstaller argv，输入只能指定构建资源目录，不是任意命令模板。"""

    media = media_root.resolve(strict=True)
    assets = ROOT / "apps" / "studio" / "dist"
    required = (
        media / "bin" / "ffmpeg.exe",
        media / "bin" / "ffprobe.exe",
        media / "LICENSE",
        media / "README.txt",
        assets / "index.html",
    )
    if any(not path.is_file() or path.stat().st_size == 0 for path in required):
        raise ValueError("缺少 production build、FFmpeg/FFprobe 或原始 LICENSE/README")
    output = output.resolve()
    if output.exists():
        raise ValueError("输出目录已存在；请指定新的候选目录，不覆盖旧包")
    if output == ROOT or ROOT.is_relative_to(output):
        raise ValueError("输出目录不得是仓库根或仓库祖先")
    return [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed",
        "--noupx",
        "--name",
        "ZNIKU Studio",
        "--distpath",
        str(output / "dist"),
        "--workpath",
        str(output / "build"),
        "--specpath",
        str(output),
        "--paths",
        str(ROOT / "src"),
        "--collect-all",
        "zniku",
        "--collect-data",
        "jsonschema_specifications",
        "--add-data",
        f"{assets};studio",
        "--add-binary",
        f"{media / 'bin' / 'ffmpeg.exe'};media-tools",
        "--add-binary",
        f"{media / 'bin' / 'ffprobe.exe'};media-tools",
        "--add-data",
        f"{media / 'LICENSE'};licenses/ffmpeg",
        "--add-data",
        f"{media / 'README.txt'};licenses/ffmpeg",
        str(ROOT / "tools" / "desktop_entry.py"),
    ]


def main() -> int:
    """要求构建者显式选择已安装媒体分发目录，不下载或接受不明可执行文件。"""

    parser = argparse.ArgumentParser(description="构建 ZNIKU Windows 本地桌面候选")
    parser.add_argument("--media-distribution-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not is_windows():
        parser.error("Windows 包必须在 Windows 上构建")
    command = build_arguments(args.media_distribution_root, args.output)
    completed = subprocess.run(command, cwd=ROOT, shell=False, check=False)
    if completed.returncode:
        return completed.returncode
    package = args.output.resolve() / "dist" / "ZNIKU Studio"
    if not (package / "ZNIKU Studio.exe").is_file():
        raise RuntimeError("PyInstaller 未产生预期双击入口")
    manifest = {
        "product": "ZNIKU Studio",
        "product_version": importlib.metadata.version("zniku"),
        "candidate_stage": "v0.3.0 Phase 5 local desktop candidate",
        "python_version": sys.version.split()[0],
        "pyinstaller_version": importlib.metadata.version("pyinstaller"),
        "distribution": "local acceptance only; external redistribution requires license review",
        "media_license": "_internal/licenses/ffmpeg/LICENSE",
        "media_provenance": "_internal/licenses/ffmpeg/README.txt",
    }
    (package / "BUILD-INFO.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (package / "开始使用.txt").write_text(
        "ZNIKU Studio 本地桌面候选\n\n完整解压此目录后双击 ZNIKU Studio.exe。"
        "无需安装 Python 或 npm。第一次启动需要允许浏览器打开本机页面。\n"
        "在界面中选择工程与媒体。退出请使用界面的“退出应用”，关闭浏览器标签页不会停止服务。\n"
        "新工程的工作数据默认在工程旁同名 .data 文件夹，可在建项时选择专用磁盘父目录。\n"
        "中间产物不会自动清除。外部节点可打开专属收件文件夹放入任意名文件，再明确检查收纳和提交。\n"
        "旧工程保留原数据位置；顶栏“工程数据”可预览并确认复制迁移，原位置全部保留。\n"
        "自动处理仍在进行时不能退出；外部处理等待可以退出，重开工程后继续检查与提交。\n"
        "请勿删除 _internal。工具来源与许可见 BUILD-INFO.json 所引用文件。\n"
        "此包供本机验收，不是正式 Release；公开再分发前仍需审阅第三方许可。\n",
        encoding="utf-8",
    )
    print(package)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
