"""用纯合成包验证可重复打包审计；不启动桌面、FFmpeg 或用户服务。

wheel 用真实 ZIP/RECORD；PYZ 单测替身只隔离 PyInstaller 读取，不跳过模块集合和实际编译
CodeType 比较。最终候选仍必须由同一审计工具读取真实 exe 后才可交付。
"""

from __future__ import annotations

import base64
import csv
import hashlib
import importlib.util
import io
import json
import marshal
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import audit_desktop_package as audit
else:
    _SPEC = importlib.util.spec_from_file_location(
        "zniku_desktop_package_audit", Path(__file__).parents[1] / "tools/audit_desktop_package.py"
    )
    if _SPEC is None or _SPEC.loader is None:
        raise RuntimeError("无法加载只读打包审计工具")
    audit = importlib.util.module_from_spec(_SPEC)
    _SPEC.loader.exec_module(audit)


@pytest.fixture
def resources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    repository = tmp_path / "repo"
    sources = repository / "src/zniku"
    sources.mkdir(parents=True)
    (sources / "__init__.py").write_text('__version__ = "0.3.1"\n', encoding="utf-8")
    (sources / "py.typed").touch()
    (sources / "module.py").write_text('def value():\n    return "current"\n', encoding="utf-8")
    studio = repository / "apps/studio"
    (studio / "dist/assets").mkdir(parents=True)
    (studio / "dist/index.html").write_text(
        '<script src="assets/app.js"></script>', encoding="utf-8"
    )
    (studio / "dist/assets/app.js").write_text('document.title="synthetic";', encoding="utf-8")
    (studio / "package.json").write_text('{"version":"0.3.1"}', encoding="utf-8")
    (studio / "package-lock.json").write_text(
        '{"version":"0.3.1","packages":{"":{"version":"0.3.1"}}}', encoding="utf-8"
    )
    (repository / "VERSION").write_text("0.3.1\n", encoding="utf-8")
    (repository / "pyproject.toml").write_text('[project]\nversion="0.3.1"\n', encoding="utf-8")
    (repository / "uv.lock").write_text(
        'version = 1\n[[package]]\nname = "zniku"\nversion = "0.3.1"\n', encoding="utf-8"
    )
    (repository / "tools").mkdir()
    (repository / "tools/desktop_entry.py").write_text('print("synthetic")\n', encoding="utf-8")
    media = tmp_path / "media"
    (media / "bin").mkdir(parents=True)
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        (media / "bin" / name).write_bytes(b"MZ synthetic, never executed")
    for name in ("LICENSE", "README.txt"):
        (media / name).write_text("synthetic original distribution metadata", encoding="utf-8")
    monkeypatch.setattr(audit, "_git_state", lambda _: ("a" * 40, True))
    return repository, media


def _wheel(
    repository: Path, target: Path, changes: Mapping[str, bytes | None] | None = None
) -> Path:
    entries = {name: path.read_bytes() for name, path in audit.source_files(repository).items()}
    metadata = "zniku-0.3.1.dist-info"
    entries.update(
        {
            f"{metadata}/METADATA": b"Metadata-Version: 2.4\nName: zniku\nVersion: 0.3.1\n",
            f"{metadata}/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            f"{metadata}/top_level.txt": b"zniku\n",
        }
    )
    for name, content in (changes or {}).items():
        if content is None:
            del entries[name]
        else:
            entries[name] = content
    record = io.StringIO()
    writer = csv.writer(record)
    for name, content in sorted(entries.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        writer.writerow([name, f"sha256={digest}", len(content)])
    writer.writerow([f"{metadata}/RECORD", "", ""])
    entries[f"{metadata}/RECORD"] = record.getvalue().encode()
    with zipfile.ZipFile(target, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return target


class _SyntheticPyz:
    def __init__(self, repository: Path) -> None:
        self.modules: dict[str, object] = {}
        for name, path in audit.source_files(repository).items():
            if name.endswith(".py"):
                module = name.removesuffix(".py").replace("/", ".").removesuffix(".__init__")
                self.modules[module] = compile(path.read_bytes(), name, "exec", dont_inherit=True)
        self.toc = dict.fromkeys(self.modules, ())

    def extract(self, name: str) -> object:
        return self.modules[name]


class _SyntheticArchive:
    def __init__(self, repository: Path) -> None:
        self.toc = {"PYZ.pyz": (0, 0, 0, 0, "z"), "desktop_entry": (0, 0, 0, 0, "s")}
        self.pyz = _SyntheticPyz(repository)
        self.entry = compile(
            (repository / "tools/desktop_entry.py").read_bytes(),
            "entry.py",
            "exec",
            dont_inherit=True,
        )

    def open_embedded_archive(self, name: str) -> _SyntheticPyz:
        assert name == "PYZ.pyz"
        return self.pyz

    def extract(self, name: str) -> bytes:
        assert name == "desktop_entry"
        return marshal.dumps(self.entry)


def _package(repository: Path, media: Path, target: Path) -> Path:
    target.mkdir()
    (target / "ZNIKU Studio.exe").write_bytes(b"synthetic, never executed")
    (target / "开始使用.txt").write_text("v0.3.1 验收候选", encoding="utf-8")
    shutil.copytree(repository / "src/zniku", target / "_internal/zniku")
    shutil.copytree(repository / "apps/studio/dist", target / "_internal/studio")
    metadata = target / "_internal/zniku-0.3.1.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_bytes(b"Name: zniku\nVersion: 0.3.1\n")
    (metadata / "WHEEL").write_bytes(b"Wheel-Version: 1.0\n")
    (metadata / "top_level.txt").write_bytes(b"zniku\n")
    for name, source in audit._media_inputs(media).items():
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target / name)
    _write_info(repository, media, target)
    return target


def _write_info(repository: Path, media: Path, target: Path) -> None:
    info = {
        "product": "ZNIKU Studio",
        "product_version": "0.3.1",
        "python_version": sys.version.split()[0],
        **audit.build_source_info(repository, media),
    }
    (target / "BUILD-INFO.json").write_text(json.dumps(info), encoding="utf-8")


def test_wheel_matches_exact_source_and_record(
    resources: tuple[Path, Path], tmp_path: Path
) -> None:
    repository, _ = resources
    wheel = _wheel(repository, tmp_path / "candidate.whl")
    before = wheel.read_bytes()
    result = audit.audit_wheel(wheel, repository)
    assert result == {
        "kind": "wheel",
        "product_version": "0.3.1",
        "package_files_exact": 3,
        "total_entries": 7,
        "source_bytes_exact": True,
        "record_valid": True,
    }
    assert wheel.read_bytes() == before


@pytest.mark.parametrize(
    ("name", "content", "error"),
    [
        ("zniku/py.typed", None, "精确文件集合"),
        ("zniku/module.py", b"obsolete = True\n", "源码字节"),
        ("zniku/legacy.py", b"old = True\n", "精确文件集合"),
        ("zniku-0.3.1.dist-info/METADATA", b"Name: zniku\nVersion: 0.2.0\n", "身份/版本"),
        ("extra.zniku", b"private", "禁止内容"),
        ("secret.pem", b"private", "禁止内容"),
        ("../escaped.py", b"private", "非相对"),
        ("zniku/MODULE.py", b"duplicate", "大小写冲突"),
    ],
)
def test_wheel_rejects_pollution_or_version_drift(
    resources: tuple[Path, Path],
    tmp_path: Path,
    name: str,
    content: bytes | None,
    error: str,
) -> None:
    repository, _ = resources
    wheel = _wheel(repository, tmp_path / "bad.whl", {name: content})
    with pytest.raises(audit.PackageAuditError, match=error):
        audit.audit_wheel(wheel, repository)
    assert wheel.is_file()


def test_wheel_record_tamper_is_rejected(resources: tuple[Path, Path], tmp_path: Path) -> None:
    repository, _ = resources
    wheel = _wheel(repository, tmp_path / "bad.whl")
    with zipfile.ZipFile(wheel) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    entries["zniku-0.3.1.dist-info/RECORD"] = entries["zniku-0.3.1.dist-info/RECORD"].replace(
        b"sha256=", b"sha512=", 1
    )
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    with pytest.raises(audit.PackageAuditError, match="RECORD 摘要"):
        audit.audit_wheel(wheel, repository)


def test_desktop_checks_actual_code_assets_media_and_dirty_source(
    resources: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, media = resources
    package = _package(repository, media, tmp_path / "candidate")
    archive = _SyntheticArchive(repository)
    monkeypatch.setattr(audit, "_archive_reader", lambda _: archive)
    result = audit.audit_desktop(package, repository, media)
    assert result["python_modules_exact"] == 2
    assert result["production_assets_exact"] == 2
    assert result["media_and_licenses_exact"] == 4
    assert result["git_dirty_at_build"] is True
    assert result["user_service_started_or_changed"] is False
    assert "source_kind" in result and str(tmp_path) not in json.dumps(result)
    # 提交只改变基点状态，不使字节完全一致的 dirty 候选假失效或改写原记录。
    monkeypatch.setattr(audit, "_git_state", lambda _: ("b" * 40, False))
    assert audit.audit_desktop(package, repository, media)["git_base_commit"] == "a" * 40


@pytest.mark.parametrize(
    ("name", "error"),
    [
        ("_internal/zniku/module.py", "源码 字节"),
        ("_internal/studio/assets/app.js", "production 字节"),
        ("_internal/media-tools/ffmpeg.exe", "媒体工具和原始许可 字节"),
        ("_internal/licenses/ffmpeg/LICENSE", "媒体工具和原始许可 字节"),
    ],
)
def test_desktop_rejects_changed_source_asset_or_original_tool(
    resources: tuple[Path, Path],
    tmp_path: Path,
    name: str,
    error: str,
) -> None:
    repository, media = resources
    package = _package(repository, media, tmp_path / "candidate")
    (package / name).write_bytes(b"changed")
    with pytest.raises(audit.PackageAuditError, match=error):
        audit.audit_desktop(package, repository, media)
    assert (package / name).read_bytes() == b"changed"


@pytest.mark.parametrize(
    "name",
    [
        "_internal/zniku/legacy.py",
        "_internal/zniku/__pycache__/module.pyc",
        "_internal/studio/assets/old.js",
        "_internal/media-tools/extra.exe",
        "_internal/source.mkv",
        "_internal/user.zniku",
        "_internal/attempts/output.txt",
        "_internal/debug.log",
        "_internal/.env.production",
        "_internal/user.pem",
        "_internal/zniku-0.3.1.dist-info/direct_url.json",
        "_internal/zniku-0.3.1.dist-info/uv_build.json",
        "_internal/zniku-0.3.1.dist-info/uv_cache.json",
        "_internal/user.zniku-wal",
        "_internal/user.sqlite.bak",
    ],
)
def test_desktop_rejects_extra_or_sensitive_content(
    resources: tuple[Path, Path],
    tmp_path: Path,
    name: str,
) -> None:
    repository, media = resources
    package = _package(repository, media, tmp_path / "candidate")
    (package / name).parent.mkdir(parents=True, exist_ok=True)
    (package / name).write_bytes(b"do not remove")
    with pytest.raises(audit.PackageAuditError):
        audit.audit_desktop(package, repository, media)
    assert (package / name).read_bytes() == b"do not remove"


@pytest.mark.parametrize("kind", ["changed_code", "extra_module", "missing_module", "entry"])
def test_desktop_rejects_pyz_mismatch_even_if_bundled_sources_match(
    resources: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    repository, media = resources
    package = _package(repository, media, tmp_path / "candidate")
    archive = _SyntheticArchive(repository)
    if kind == "changed_code":
        archive.pyz.modules["zniku.module"] = compile("old = True", "zniku/module.py", "exec")
    elif kind == "extra_module":
        archive.pyz.toc["zniku.legacy"] = ()
    elif kind == "missing_module":
        del archive.pyz.toc["zniku.module"]
    else:
        archive.entry = compile("old = True", "entry.py", "exec")
    monkeypatch.setattr(audit, "_archive_reader", lambda _: archive)
    with pytest.raises(audit.PackageAuditError, match=r"PYZ|desktop_entry"):
        audit.audit_desktop(package, repository, media)


def test_source_snapshot_rejects_stale_build_info_and_personal_path(
    resources: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    repository, media = resources
    package = _package(repository, media, tmp_path / "candidate")
    lock = repository / "uv.lock"
    lock.write_text(lock.read_text("utf-8") + "# changed build input\n", encoding="utf-8")
    with pytest.raises(audit.PackageAuditError, match="来源或输入字节"):
        audit.audit_desktop(package, repository, media)
    (repository / "tools/desktop_entry.py").write_text(
        'personal = "C:/Users/SyntheticPerson/private"', encoding="utf-8"
    )
    with pytest.raises(audit.PackageAuditError, match="个人路径或凭据"):
        audit.build_source_info(repository, media)


def test_source_inventory_ignores_old_cache_but_not_unknown_data(
    resources: tuple[Path, Path],
) -> None:
    repository, _ = resources
    old = repository / "src/zniku/legacy/__pycache__"
    old.mkdir(parents=True)
    (old / "obsolete.pyc").write_bytes(b"preserve old cache")
    assert len(audit.source_files(repository)) == 3
    (repository / "src/zniku/unknown.txt").write_text("not declared", encoding="utf-8")
    with pytest.raises(audit.PackageAuditError, match="未声明"):
        audit.source_files(repository)
    assert (old / "obsolete.pyc").read_bytes() == b"preserve old cache"


def test_version_gate_is_product_only(resources: tuple[Path, Path]) -> None:
    repository, _ = resources
    (repository / "src/zniku/module.py").write_text(
        'wire_version = "0.3.0"\ndefinition_version = "0.2.1"\nschema_version = 4', encoding="utf-8"
    )
    assert audit.product_version(repository) == "0.3.1"
    (repository / "VERSION").write_text("0.3.0", encoding="utf-8")
    with pytest.raises(audit.PackageAuditError, match="不一致"):
        audit.product_version(repository)


def test_python_lock_product_version_drift_is_rejected(resources: tuple[Path, Path]) -> None:
    repository, _ = resources
    (repository / "uv.lock").write_text(
        'version = 1\n[[package]]\nname = "zniku"\nversion = "0.2.0"\n', encoding="utf-8"
    )
    with pytest.raises(audit.PackageAuditError, match="不一致"):
        audit.product_version(repository)


def test_cli_requires_explicit_target_without_mutation(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parents[1] / "tools/audit_desktop_package.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        check=False,
        timeout=15,
    )
    assert result.returncode == 2
    assert list(tmp_path.iterdir()) == []


def test_wheel_cli_runs_without_loading_pyinstaller(
    resources: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    repository, _ = resources
    wheel = _wheel(repository, tmp_path / "candidate.whl")
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[1] / "tools/audit_desktop_package.py"),
            "--repository",
            str(repository),
            "--wheel",
            str(wheel),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["audits"][0]["source_bytes_exact"] is True
    assert str(tmp_path) not in result.stdout


def test_python_minor_mismatch_is_reported_before_pyz_decode(
    resources: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, media = resources
    package = _package(repository, media, tmp_path / "candidate")
    info_path = package / "BUILD-INFO.json"
    info = json.loads(info_path.read_text("utf-8"))
    info["python_version"] = "3.99.0"
    info_path.write_text(json.dumps(info), encoding="utf-8")

    def forbidden_decode(_: Path) -> None:
        pytest.fail("minor 不匹配时不得调用 PYZ decoder")

    monkeypatch.setattr(audit, "_archive_reader", forbidden_decode)
    with pytest.raises(audit.PackageAuditError, match="相同 Python minor"):
        audit.audit_desktop(package, repository, media)


def test_archive_inside_package_cannot_hide_media(
    resources: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    repository, media = resources
    package = _package(repository, media, tmp_path / "candidate")
    nested = package / "_internal/base_library.zip"
    with zipfile.ZipFile(nested, "w") as archive:
        archive.writestr("ordinary.pyc", b"synthetic standard library")
        archive.writestr("hidden.mov", b"synthetic forbidden media")
    with pytest.raises(audit.PackageAuditError, match="禁止内容"):
        audit.audit_desktop(package, repository, media)
    assert nested.is_file()


@pytest.mark.parametrize(
    "name",
    [
        "module.py",
        "config.json",
        "script.js",
        "theme.css",
        "index.html",
        "README.txt",
        "help.md",
        "METADATA",
        "WHEEL",
    ],
)
@pytest.mark.parametrize(
    "private_content",
    [
        b"C:/Users/SyntheticOperator/private/workspace",
        b"-----BEGIN " + b"PRIVATE KEY-----\nsynthetic-not-a-real-key",
    ],
)
def test_dependency_text_cannot_hide_private_paths_or_secrets(
    resources: tuple[Path, Path],
    tmp_path: Path,
    name: str,
    private_content: bytes,
) -> None:
    """整个包的依赖文本使用同一规则；拒绝不回显秘密，不改源分发及已生成候选。"""

    repository, media = resources
    package = _package(repository, media, tmp_path / "candidate")
    dependency_file = package / "_internal/synthetic_dependency" / name
    dependency_file.parent.mkdir()
    dependency_file.write_bytes(private_content)
    before = {
        path.relative_to(package): path.read_bytes()
        for path in package.rglob("*")
        if path.is_file()
    }
    license_bytes = (media / "LICENSE").read_bytes()
    readme_bytes = (media / "README.txt").read_bytes()
    with pytest.raises(audit.PackageAuditError, match="个人路径或凭据") as failure:
        audit.audit_desktop(package, repository, media)
    assert private_content.decode() not in str(failure.value)
    assert {
        path.relative_to(package): path.read_bytes()
        for path in package.rglob("*")
        if path.is_file()
    } == before
    assert (media / "LICENSE").read_bytes() == license_bytes
    assert (media / "README.txt").read_bytes() == readme_bytes


def test_benign_dependency_text_and_original_licenses_are_preserved(
    resources: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, media = resources
    package = _package(repository, media, tmp_path / "candidate")
    dependency = package / "_internal/synthetic_dependency"
    dependency.mkdir()
    for name in ("README.txt", "module.py", "METADATA", "WHEEL"):
        (dependency / name).write_bytes(b"synthetic public dependency documentation")
    archive = _SyntheticArchive(repository)
    monkeypatch.setattr(audit, "_archive_reader", lambda _: archive)
    result = audit.audit_desktop(package, repository, media)
    assert result["media_and_licenses_exact"] == 4
    assert (package / "_internal/licenses/ffmpeg/README.txt").read_bytes() == (
        media / "README.txt"
    ).read_bytes()
    assert all(
        path.read_bytes() == b"synthetic public dependency documentation"
        for path in dependency.iterdir()
    )
