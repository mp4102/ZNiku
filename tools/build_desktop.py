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

from audit_desktop_package import audit_desktop, build_source_info, product_version, source_files

from zniku.desktop.windows import is_windows

ROOT = Path(__file__).resolve().parents[1]


def metadata_files() -> dict[str, Path]:
    """仅保留分发身份，不把 editable 的个人路径或 uv 构建缓存拷进候选。"""

    distribution = importlib.metadata.distribution("zniku")
    required = {"METADATA", "WHEEL", "top_level.txt"}
    files = {
        str(path): Path(str(distribution.locate_file(path)))
        for path in distribution.files or ()
        if path.parent.name == f"zniku-{distribution.version}.dist-info" and path.name in required
    }
    if {Path(name).name for name in files} != required:
        raise ValueError("安装的 zniku 缺少必要 METADATA/WHEEL/top_level.txt")
    return files


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
    data_arguments = []
    for name, source in {**source_files(ROOT), **metadata_files()}.items():
        data_arguments.extend(["--add-data", f"{source};{Path(name).parent.as_posix()}"])
    return [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed",
        "--noupx",
        "--optimize",
        "0",
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
        "--collect-submodules",
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
        *data_arguments,
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
    version = product_version(ROOT)
    if importlib.metadata.version("zniku") != version:
        parser.error("安装的 zniku 版本与源码不同；先按当前 lock 同步构建环境")
    command = build_arguments(args.media_distribution_root, args.output)
    source_info = build_source_info(ROOT, args.media_distribution_root)
    completed = subprocess.run(command, cwd=ROOT, shell=False, check=False)
    if completed.returncode:
        return completed.returncode
    package = args.output.resolve() / "dist" / "ZNIKU Studio"
    if not (package / "ZNIKU Studio.exe").is_file():
        raise RuntimeError("PyInstaller 未产生预期双击入口")
    manifest = {
        "product": "ZNIKU Studio",
        "product_version": version,
        "candidate_stage": f"v{version} acceptance candidate; not a release",
        "python_version": sys.version.split()[0],
        "pyinstaller_version": importlib.metadata.version("pyinstaller"),
        "distribution": "local acceptance only; external redistribution requires license review",
        "media_license": "_internal/licenses/ffmpeg/LICENSE",
        "media_provenance": "_internal/licenses/ffmpeg/README.txt",
        **source_info,
    }
    (package / "BUILD-INFO.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (package / "开始使用.txt").write_text(
        f"ZNIKU Studio v{version} 本地验收候选 (不是正式发行)\n\n"
        "完整解压此目录后双击 ZNIKU Studio.exe。"
        "无需安装 Python 或 npm。第一次启动需要允许浏览器打开本机页面。\n"
        "在界面中选择工程与媒体。退出请使用界面的“退出应用”，关闭浏览器标签页不会停止服务。\n"
        "本0.3.6候选使用独立Studio-v0.3.6-candidate本机状态通道，不会连接旧版本实例。\n"
        "同一候选更新前先在其页面确认“退出应用”；旧候选和真实工程不会自动迁移。\n"
        "测试新链时，新建工程，使用默认“ZNIKU 标准视频流程 · 0.3.5”。\n"
        "本候选新增章级批量增强：每章一个增强节点，共享设置，按章多选文件或选择目录收件。\n"
        "可以分批补件；收齐后“检查本章全部输出”，通过后再“提交本章并继续”，文件出现不会自动提交。\n"
        "旧工程的逐叶节点不会自动转换；请新建测试工程验证批量流程，不改写仍在处理中的旧工程。\n"
        "更新同一0.3.6候选前请退出旧候选；0.3.5已验收程序独立保留，不会被本包接管。\n"
        "第0项外部修复默认关闭；开启后先分析原片，确认工作流后再等待外部文件。\n"
        "新建的马赛克修复任务自动识别实际MP4/MOV/MKV，按当前输入stem加.RM和真实封装扩展名归档。\n"
        "素材分析报告可展开查看中文摘要与格式化JSON，无需把admission.json交给外部视频软件。\n"
        "放入incoming后刷新，或直接选择原位置文件；选择时不复制，点击“检查并导入”通过后才收纳。\n"
        "外部原件保留；incoming内文件只规范改名，不重复复制。最后仍需“提交并继续”才推进节点。\n"
        "若发布后页面中断，可展开恢复入口“检查已收纳输出”，重新检查后提交，不必重新复制。\n"
        "旧马赛克修复节点保持原文件名和封装合同；请新建测试工程验证新交回体验，不迁移旧等待任务。\n"
        "源准入按AVEnhanceFlow2.7媒体规则；分析显示进度并可取消，不要求原片符合旧严格逐帧时间戳合同。\n"
        "分析失败可选择外部修复候选并明确确认；候选重新分析，以自身N/FPS/音轨作为新参考。\n"
        "选用候选只修改工程引用，原文件不更名、不覆盖、不自动转码；归档时应一并保留参考文件。\n"
        "可选第0项马赛克修复不同于源故障修复：前者须保持已准入参考的N/FPS/几何和帧序。\n"
        "章节支持平均章数、精确时间或精确帧切点；分叶最长默认5分钟，独立可设1-60分钟。\n"
        "FI软件v1.0、模型Aion仍待真实验收：处理fi-input，提交未裁边fi-raw，系统另存fi成品章。\n"
        "默认左右32帧上下文与最短2帧仅为候选工程设置，必须先以短片验证相位、画质与同步。\n"
        "新工程的工作数据默认在工程旁同名 .data 文件夹，可在建项时选择专用磁盘父目录。\n"
        "新工程使用英文任务/章节/处理轮次目录，例如 enhancement/A/round-001/incoming。\n"
        "round-001表示该任务第1次实际处理，跨运行连续计数；复用成果不创建媒体副本。\n"
        "incoming是交回区、outputs是任务输出区、logs是记录；文件位置不代表验收通过。\n"
        "旧UUID和中英混排工程保持原目录，不自动改名、搬动或清理历史媒体。\n"
        "工程数据面板可显式整理到新位置(复制核对后切换，保留原件)，整理前请备份.zniku。\n"
        "英文布局可生成index.html文件目录；旧布局保留原文件目录.html，不是完整归档证明。\n"
        "已自行换盘的.data可在工程数据中重新定位；缺盘恢复需匹配数据归属标记。\n"
        "成片默认保存到工程所在目录下的“片名 (年份)”文件夹，可在成片设置中改位置，无需提前创建。\n"
        "新建标准流程在成片目录内封装临时候选，检查通过后发布正式文件名，不再复制第二份完整成片。\n"
        "检查或发布失败会保留候选；旧工程继续使用原复制流程，但已修复进度报告导致复制过慢的问题。\n"
        "复制节点显示已复制容量、进度条和有依据的步骤平均速度；100%字节不代表工程已确认完成。\n"
        "中间产物不会自动清除。外部节点可打开专属收件文件夹放入任意名文件，再明确检查收纳和提交。\n"
        "旧工程保留原数据位置；工程菜单中的“工程数据”可预览并确认复制迁移，原位置全部保留。\n"
        "自动处理仍在进行时不能退出；外部处理等待可以退出，重开工程后继续检查与提交。\n"
        "保留整个解压目录，不能只复制 exe 或删除 _internal；升级不自动迁移或清除用户数据。\n"
        "工具来源与许可见 BUILD-INFO.json 所引用文件；Git 基点不表示未提交构建等于该提交。\n"
        "此包供本机验收，不是正式 Release；公开再分发前仍需审阅第三方许可。\n",
        encoding="utf-8",
    )
    audit = audit_desktop(package, ROOT, args.media_distribution_root)
    print(json.dumps(audit, ensure_ascii=False, sort_keys=True))
    print(package)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
