"""验证桌面构建只读取显式资源、不覆盖旧包且完整保留媒体来源和许可。"""

import importlib.metadata
import importlib.util
import sys
from importlib.metadata import PathDistribution
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

if TYPE_CHECKING:
    import build_desktop
else:
    _SPEC = importlib.util.spec_from_file_location(
        "zniku_desktop_build", Path(__file__).parents[1] / "tools" / "build_desktop.py"
    )
    if _SPEC is None or _SPEC.loader is None:
        raise RuntimeError("无法加载桌面构建工具")
    build_desktop = importlib.util.module_from_spec(_SPEC)
    with patch.object(sys, "path", [str(Path(__file__).parents[1] / "tools"), *sys.path]):
        _SPEC.loader.exec_module(build_desktop)


@pytest.fixture
def build_resources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    sources = root / "src/zniku"
    sources.mkdir(parents=True)
    (sources / "__init__.py").write_text("# synthetic", encoding="utf-8")
    (sources / "py.typed").touch()
    assets = root / "apps" / "studio" / "dist"
    assets.mkdir(parents=True)
    (assets / "index.html").write_text("synthetic production", encoding="utf-8")
    media = tmp_path / "media-distribution"
    (media / "bin").mkdir(parents=True)
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        (media / "bin" / name).write_bytes(b"synthetic non-executable fixture")
    for name in ("LICENSE", "README.txt"):
        (media / name).write_text("synthetic source metadata", encoding="utf-8")
    monkeypatch.setattr(build_desktop, "ROOT", root)
    metadata = tmp_path / "zniku-0.3.2.dist-info"
    metadata.mkdir()
    for name in ("METADATA", "WHEEL", "top_level.txt"):
        (metadata / name).write_text("synthetic metadata", encoding="utf-8")
    monkeypatch.setattr(
        build_desktop,
        "metadata_files",
        lambda: {
            f"zniku-0.3.2.dist-info/{name}": metadata / name
            for name in ("METADATA", "WHEEL", "top_level.txt")
        },
    )
    return media, root


def test_builder_uses_fixed_argv_and_preserves_provenance(
    build_resources: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    media, root = build_resources
    output = tmp_path / "candidate"
    arguments = build_desktop.build_arguments(media, output)
    assert "--onedir" in arguments and "--windowed" in arguments
    assert "--collect-all" not in arguments
    assert "--collect-submodules" in arguments
    assert f"{root / 'src/zniku/py.typed'};zniku" in arguments
    assert not any("direct_url" in argument or "uv_cache" in argument for argument in arguments)
    assert arguments[arguments.index("--optimize") + 1] == "0"
    assert f"{media / 'LICENSE'};licenses/ffmpeg" in arguments
    assert f"{media / 'README.txt'};licenses/ffmpeg" in arguments
    assert str(root / "tools" / "desktop_entry.py") == arguments[-1]
    assert not output.exists()


def test_builder_does_not_overwrite_existing_directory(
    build_resources: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    media, _ = build_resources
    output = tmp_path / "existing"
    output.mkdir()
    (output / "preserve.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="已存在"):
        build_desktop.build_arguments(media, output)
    assert (output / "preserve.txt").read_text(encoding="utf-8") == "keep"


def test_builder_rejects_missing_license(
    build_resources: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    media, _ = build_resources
    (media / "LICENSE").rename(media / "original-license-preserved")
    with pytest.raises(ValueError, match="LICENSE"):
        build_desktop.build_arguments(media, tmp_path / "candidate")


def test_metadata_collection_excludes_editable_source_paths_and_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """构建时按清单选择，不在成包之后删除或隐藏已泄漏的安装元数据。"""

    metadata = tmp_path / "zniku-0.3.2.dist-info"
    metadata.mkdir()
    names = (
        "METADATA",
        "WHEEL",
        "top_level.txt",
        "direct_url.json",
        "uv_build.json",
        "uv_cache.json",
    )
    for name in names:
        (metadata / name).write_text("synthetic local metadata", encoding="utf-8")
    (metadata / "METADATA").write_text("Name: zniku\nVersion: 0.3.2\n", encoding="utf-8")
    (metadata / "RECORD").write_text(
        "".join(f"zniku-0.3.2.dist-info/{name},,\n" for name in names), encoding="utf-8"
    )
    distribution = PathDistribution(metadata)
    monkeypatch.setattr(importlib.metadata, "distribution", lambda _: distribution)
    assert set(build_desktop.metadata_files()) == {
        "zniku-0.3.2.dist-info/METADATA",
        "zniku-0.3.2.dist-info/WHEEL",
        "zniku-0.3.2.dist-info/top_level.txt",
    }
    assert all((metadata / name).is_file() for name in names)


def test_getting_started_distinguishes_fused_candidate_from_unchanged_default() -> None:
    """操作者须在建项前显式选候选，不能被旧说明误导为测试默认链即验收了融合链。"""

    text = build_desktop.getting_started_text("0.3.6")
    assert text.startswith("ZNIKU Studio v0.3.6 本地验收候选 (不是正式发行)")
    for instruction in (
        "新建独立测试工程",
        "只复制 .zniku 不会隔离其媒体路径",
        "在开始分析、创建分析工程之前",
        "工作流版本与旧工程兼容",
        "显式改为“0.3.6 融合编码候选（待验收）”",  # noqa: RUF001 - 检查界面原文。
        "同时导出裁后章节（额外占用空间与时间）”默认关闭",  # noqa: RUF001
        "默认流程仍是“ZNIKU 标准视频流程 · 0.3.5”",
        "旧节点、等待任务和既有结果不自动改图或迁移",
        "交回未经裁边的 fi-raw，不要自行删除上下文帧",
        "默认不另存整章 fi 裁边文件",
        "外部 fi-raw 会保留",
    ):
        assert instruction in text
    assert "系统另存fi成品章" not in text


def test_getting_started_preserves_explicit_handoff_and_safe_maintenance_boundary() -> None:
    """收件、检查、提交和清理各自明示，不把状态提示、100%或文件出现视为成功。"""

    text = build_desktop.getting_started_text("0.3.6")
    for instruction in (
        "系统按章号和叶号预选匹配",
        "可以手动更改，冲突不强行匹配",
        "出现文件、收件完成或检查通过都不等于已经提交",
        "全局状态行、当前节点卡片、右侧节点详情",
        "复制 100% 或媒体处理 100% 仍可能等待检查和登记",
        "外部处理等待没有虚构倒计时",
        "源、外部原件、正式成果和未知文件不会自动删除",
        "未登记且无正式依赖的内部中转可选",
        "旧工程无可靠记录的中转保留",
        "本轮全流程测试无需执行真实清理",
        "不是本候选已经完成真实 AI 测试的证明",
        "公开再分发前仍需第三方许可审阅",
    ):
        assert instruction in text


def test_getting_started_identifies_independent_channel_and_exit_rules() -> None:
    """包说明准确指向当前产品通道，不建议只关标签页或接管旧版本实例。"""

    text = build_desktop.getting_started_text("0.3.6")
    assert "独立 Studio-v0.3.6-candidate 本机状态通道" in text
    assert "不接管 0.3.5 旧实例" in text
    assert "再次双击同一候选只打开已有实例的网页" in text
    assert "只关闭浏览器标签页不会停止服务" in text
    assert "等待外部处理时可以退出，重开后继续检查和显式提交" in text
