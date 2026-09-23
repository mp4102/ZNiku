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


def getting_started_text(version: str) -> str:
    """提供当前候选的真实操作入口；不把可构建误称为真实媒体或公开发行已验收。"""

    return (
        f"ZNIKU Studio v{version} 本地验收候选 (不是正式发行)\n\n"
        "一、启动与隔离\n"
        "完整解压此目录后双击 ZNIKU Studio.exe，无需安装 Python 或 npm。"
        "保留整个目录，不能只复制 exe 或删除 _internal。\n"
        f"本候选使用独立 Studio-v{version}-candidate 本机状态通道，"
        "不接管 0.3.5 旧实例，不自动迁移旧工程、最近工程或媒体。\n"
        "再次双击同一候选只打开已有实例的网页，不再启动第二套服务。"
        "退出请使用界面的“退出应用”并确认；只关闭浏览器标签页不会停止服务。\n"
        "更新同一候选前先退出旧候选；自动处理、检查或提交期间不能退出，"
        "等待外部处理时可以退出，重开后继续检查和显式提交。\n\n"
        "二、本轮应如何选择流程\n"
        "请新建独立测试工程，使用新的工程目录、工作数据目录及成片位置，"
        "不要直接在已完成的真实工程上测试；只复制 .zniku 不会隔离其媒体路径。\n"
        "素材组织方式选择“一条完整视频”。在开始分析、创建分析工程之前，展开"
        "“工作流版本与旧工程兼容”，将“工作流方案”显式改为"
        "“0.3.6 融合编码候选（待验收）”。\n"  # noqa: RUF001 - 与界面选项原文一致。
        "“同时导出裁后章节（额外占用空间与时间）”默认关闭；保持关闭才能验证"  # noqa: RUF001
        "不生成完整裁后章副本的存储优化。确需独立裁后章归档时再开启，会增加读写与空间。\n"
        "默认流程仍是“ZNIKU 标准视频流程 · 0.3.5”，它没有自动切成融合候选。"
        "已创建工程不能靠切换选项升级；旧节点、等待任务和既有结果不自动改图或迁移。\n"
        "填写片名、年份、章节、分叶、增强、FI 和编码参数。章节支持平均章数、"
        "精确时间或精确帧切点；分叶最长默认 5 分钟，可独立设为 1-60 分钟。\n"
        "第 0 项外部马赛克修复默认关闭；需要时开启，仍先分析参考素材，"
        "确认工作流后再等待修复结果。它不同于源准入失败后的外部故障修复候选。\n"
        "源分析不通过时可选择外部修复候选并明确确认，候选通过同一准入后"
        "以自身帧数、帧率和音轨成为新参考；不复制、更名、覆盖或自动转码原片。\n\n"
        "三、外部处理与进度\n"
        "增强按章收件：可多选文件或选择目录，系统按章号和叶号预选匹配；"
        "请逐叶核对“选用处理好的文件”“接收后名称”和“本次操作”，"
        "可以手动更改，冲突不强行匹配。\n"
        "确认收件后可继续补件，收齐后点击“检查本章全部输出”，通过后再"
        "“提交本章并继续”；出现文件、收件完成或检查通过都不等于已经提交。\n"
        "外部原位置文件复制后仍保留；直接放入 incoming 的文件可在确认后原位收纳，"
        "不保证保留旧文件名副本。不要把待交付文件直接放进 outputs。\n"
        "马赛克修复可放入 incoming 后刷新，或选择原位置文件；选择时不复制，"
        "“检查并导入”通过后才收纳，最后仍须“提交并继续”。\n"
        "FI 必须处理节点给出的 fi-input，交回未经裁边的 fi-raw，不要自行删除上下文帧。"
        "融合流程直接选择有效帧并连续编码，默认不另存整章 fi 裁边文件；外部 fi-raw 会保留。\n"
        "FI 软件 v1.0 / Aion 是当前声明，不是本候选已经完成真实 AI 测试的证明。"
        "先以短样本核对边界相位、画面、帧数和音画同步，再进行长片测试。\n"
        "全局状态行、当前节点卡片、右侧节点详情同步说明当前任务。小步骤显示"
        "“正在请求/等待服务响应”，有实际进度的大步骤才显示进度条；请等待结果再继续。\n"
        "外部处理等待没有虚构倒计时；复制 100% 或媒体处理 100% 仍可能等待检查和登记，"
        "不等于节点已完成。网络结果不明时先核对处理记录，不要重复点击提交。\n\n"
        "四、工程数据与成片\n"
        "新工程的工作数据默认在工程旁同名 .data 文件夹，也可在建项时选择专用磁盘父目录。"
        "新布局为英文任务/章节/处理轮次，如 enhancement/A/round-001/incoming。\n"
        "incoming 是交回区，outputs 是任务输出区，logs 是记录；轮次表示实际处理次数，"
        "复用成果不创建媒体副本，文件位置本身不表示验收通过。\n"
        "成片位置以确认页完整路径为准，无需手动预建片名子目录。新建标准流程在"
        "最终目录封装候选，检查通过后再发布正式名称；失败保留候选，不复制第二份完整成片。\n"
        "源、外部原件、正式成果和未知文件不会自动删除。工程数据面板可先"
        "“扫描占用并预览内部中转”；仅服务端可靠识别、未登记且无正式依赖的内部中转可选。\n"
        "维护只能在没有运行或等待交付任务时进行，默认不勾选文件。删除需逐项选择并明确"
        "确认不可恢复；旧工程无可靠记录的中转保留，不是按文件名猜测删除。\n"
        "本轮全流程测试无需执行真实清理；扫描只显示逻辑占用，NAS 快照可能仍占空间。"
        "归档请保留 .zniku、.data 和外部参考视频；本候选不自动搬动或整理旧工程。\n\n"
        "五、验收与分发边界\n"
        "本包供本机 Phase 5 操作者全流程验收，不代表真实 AI、4K、长片或 NAS 性能已全部通过。"
        "测试结果由操作者确认；默认流程切换及正式发布另行决定。\n"
        "工具来源与许可见 BUILD-INFO.json 所引用文件；Git 基点和工作区状态如实记录。"
        "公开再分发前仍需第三方许可审阅，请勿把本地候选视为正式 Release。\n"
    )


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
    (package / "开始使用.txt").write_text(getting_started_text(version), encoding="utf-8")
    audit = audit_desktop(package, ROOT, args.media_distribution_root)
    print(json.dumps(audit, ensure_ascii=False, sort_keys=True))
    print(package)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
