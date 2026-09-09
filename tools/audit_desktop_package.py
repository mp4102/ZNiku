"""只读审计 wheel / Windows one-folder 候选的源码一致性与内容卫生。

摘要仅用于构建交付核对，不进入 Graph、Runtime、Artifact 或任何运行资格。只读取明确指定
的本地构建产物，不执行包内代码或媒体工具，不解压到磁盘，不删除或修复不合格候选。
one-folder 的 PYZ 校验需要与构建相同 Python minor 及已安装 PyInstaller；wheel 可跨平台审计。
"""

from __future__ import annotations

import argparse
import ast
import base64
import csv
import hashlib
import importlib
import io
import json
import re
import stat
import subprocess
import sys
import tomllib
import zipfile
from collections.abc import Mapping
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from types import CodeType
from typing import Protocol, cast

ROOT = Path(__file__).resolve().parents[1]
_FORBIDDEN_PARTS = {
    ".git",
    ".env",
    ".aws",
    ".ssh",
    "attempts",
    "incoming",
    "artifacts",
    "runtime-data",
    "workspace",
    "logs",
    "preview-cache",
    "project-data",
    "snapshots",
    "evidence",
    "receipts",
    "credentials",
    "secrets",
    "node_modules",
}
_FORBIDDEN_SUFFIXES = {
    ".zniku",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".log",
    ".tmp",
    ".work",
    ".key",
    ".pem",
    ".pfx",
    ".p12",
    ".mkv",
    ".mov",
    ".mp4",
    ".avi",
    ".webm",
    ".wmv",
    ".mxf",
    ".vob",
    ".m2ts",
    ".mts",
    ".mpeg",
    ".mpg",
    ".wav",
    ".flac",
    ".mp3",
    ".aac",
    ".onnx",
    ".safetensors",
    ".pt",
    ".pth",
}
_PRIVATE_TEXT = re.compile(
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{30,}"
    rb"|(?:[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"']+)|/home/[^/\s\"']+/"
)
_TEXT_SUFFIXES = {".py", ".json", ".js", ".css", ".html", ".txt", ".md"}


class PackageAuditError(ValueError):
    """候选内容不符合显式构建输入，拒绝交付但保留所有文件。"""


class _PyzReader(Protocol):
    toc: Mapping[str, object]

    def extract(self, name: str) -> object: ...


class _ArchiveReader(Protocol):
    toc: Mapping[str, tuple[int, int, int, int, str]]

    def open_embedded_archive(self, name: str) -> _PyzReader: ...

    def extract(self, name: str) -> bytes: ...


def _safe_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or ":" in name
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in name.split("/"))
    ):
        raise PackageAuditError("包内存在非相对或非规范文件名")
    return path


def _check_hygiene(name: str, data: bytes | None = None) -> None:
    path = _safe_name(name)
    parts = {part.casefold() for part in path.parts}
    if (
        parts & _FORBIDDEN_PARTS
        or path.suffix.casefold() in _FORBIDDEN_SUFFIXES
        or any(part.startswith(".env.") or part.endswith(".avenhance") for part in parts)
        or path.name.casefold() in {"auth.tpz", "local-config.json", "id_rsa", "id_ed25519"}
        or path.name.casefold() in {"direct_url.json", "uv_build.json", "uv_cache.json"}
        or re.search(r"\.(?:zniku|sqlite3?|db)(?:-|\.)", path.name.casefold()) is not None
    ):
        raise PackageAuditError(f"包内出现禁止内容：{name}")
    if (
        data is not None
        and (path.suffix.casefold() in _TEXT_SUFFIXES or path.name in {"METADATA", "WHEEL"})
        and _PRIVATE_TEXT.search(data)
    ):
        # 错误只提供相对条目名，不回显匹配到的个人路径或凭据。
        raise PackageAuditError(f"包内文本出现个人路径或凭据：{name}")


def _files(root: Path, *, ignore_pycache: bool = False) -> dict[str, Path]:
    """拒绝链接/reparse，避免审计跟随候选目录之外的用户资料。"""

    if not root.is_dir() or root.is_symlink() or root.is_junction():
        raise PackageAuditError("审计输入必须是现存的普通目录")
    result: dict[str, Path] = {}
    pending = [root]
    while pending:
        for path in sorted(pending.pop().iterdir()):
            if ignore_pycache and path.name == "__pycache__":
                continue
            if path.is_symlink() or path.is_junction():
                raise PackageAuditError("审计目录中不允许链接或 reparse 路径")
            if path.is_dir():
                pending.append(path)
            elif path.is_file():
                name = path.relative_to(root).as_posix()
                _check_hygiene(name)
                result[name] = path
            else:
                raise PackageAuditError("审计目录包含非普通文件")
    if len({name.casefold() for name in result}) != len(result):
        raise PackageAuditError("包内文件名存在 Windows 大小写冲突")
    return dict(sorted(result.items()))


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_files(repository: Path) -> dict[str, Path]:
    """以当前源码的精确 Python 文件集合为准；旧 __pycache__ 不成为打包输入。"""

    files = _files(repository / "src" / "zniku", ignore_pycache=True)
    if "__init__.py" not in files or "py.typed" not in files:
        raise PackageAuditError("当前源码缺少 __init__.py 或 py.typed")
    if any(not name.endswith(".py") and name != "py.typed" for name in files):
        raise PackageAuditError("当前 zniku 源码含未声明的非 Python package data")
    return {f"zniku/{name}": path for name, path in files.items()}


def product_version(repository: Path) -> str:
    """核对产品版本及 Python/npm 锁文件，不扫描或升级 wire / definition / 工程版本。"""

    version = (repository / "VERSION").read_text("utf-8").strip()
    if re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", version) is None:
        raise PackageAuditError("产品版本必须是精确三段版本")
    project = tomllib.loads((repository / "pyproject.toml").read_text("utf-8"))
    python_lock = tomllib.loads((repository / "uv.lock").read_text("utf-8"))
    locked_versions = [
        package.get("version")
        for package in python_lock.get("package", [])
        if package.get("name") == "zniku"
    ]
    package = json.loads((repository / "apps/studio/package.json").read_text("utf-8"))
    lock = json.loads((repository / "apps/studio/package-lock.json").read_text("utf-8"))
    tree = ast.parse((repository / "src/zniku/__init__.py").read_text("utf-8"))
    versions = [
        value.value.value
        for value in tree.body
        if isinstance(value, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in value.targets
        )
        and isinstance(value.value, ast.Constant)
    ]
    if (
        versions != [version]
        or locked_versions != [version]
        or project["project"]["version"] != version
        or package["version"] != version
        or lock["version"] != version
        or lock["packages"][""]["version"] != version
    ):
        raise PackageAuditError("产品 VERSION / Python / npm / lock 不一致")
    return version


def build_source_info(repository: Path, media_root: Path) -> dict[str, object]:
    """记录 working-tree 输入；Git SHA 只是基点，不把 dirty 构建伪称为该提交原样产物。"""

    sources = source_files(repository)
    assets = _files(repository / "apps/studio/dist")
    if "index.html" not in assets:
        raise PackageAuditError("缺少 Studio production index.html")
    inputs = {
        **{f"src/{name}": path for name, path in sources.items()},
        **{f"apps/studio/dist/{name}": path for name, path in assets.items()},
        **{
            name: repository / name
            for name in (
                "VERSION",
                "pyproject.toml",
                "uv.lock",
                "apps/studio/package.json",
                "apps/studio/package-lock.json",
                "tools/desktop_entry.py",
            )
        },
    }
    for name, path in inputs.items():
        _check_hygiene(name, path.read_bytes())
    revision, dirty = _git_state(repository)
    media = _media_inputs(media_root)
    return {
        "source_kind": "working_tree",
        "git_base_commit": revision,
        "git_dirty": dirty,
        "source_files_sha256": {name: _digest(path) for name, path in sorted(inputs.items())},
        "media_files_sha256": {name: _digest(path) for name, path in media.items()},
    }


def _git_state(repository: Path) -> tuple[str, bool]:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        capture_output=True,
        text=True,
        shell=False,
        check=True,
        timeout=15,
    )
    revision = result.stdout.strip()
    if re.fullmatch("[0-9a-f]{40}", revision) is None:
        raise PackageAuditError("无法确认构建基点 Git SHA")
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=repository,
        capture_output=True,
        text=True,
        shell=False,
        check=True,
        timeout=15,
    )
    return revision, bool(status.stdout.strip())


def _media_inputs(media_root: Path) -> dict[str, Path]:
    mapping = {
        "_internal/media-tools/ffmpeg.exe": media_root / "bin/ffmpeg.exe",
        "_internal/media-tools/ffprobe.exe": media_root / "bin/ffprobe.exe",
        "_internal/licenses/ffmpeg/LICENSE": media_root / "LICENSE",
        "_internal/licenses/ffmpeg/README.txt": media_root / "README.txt",
    }
    for path in mapping.values():
        if not path.is_file() or path.stat().st_size == 0 or path.is_symlink():
            raise PackageAuditError("缺少原始 FFmpeg/FFprobe 或非空 LICENSE/README")
    return mapping


def _compare_files(expected: Mapping[str, Path], actual: Mapping[str, Path], label: str) -> None:
    if expected.keys() != actual.keys():
        raise PackageAuditError(f"{label} 文件集合不一致 (缺失或额外文件)")
    for name, source in expected.items():
        if _digest(source) != _digest(actual[name]):
            raise PackageAuditError(f"{label} 字节不一致：{name}")


def audit_wheel(wheel: Path, repository: Path) -> dict[str, object]:
    """逐条核对源码、metadata 和 RECORD，不安装或执行 wheel。"""

    expected = source_files(repository)
    version = product_version(repository)
    metadata_root = f"zniku-{version}.dist-info"
    allowed_metadata = {
        f"{metadata_root}/{name}"
        for name in (
            "METADATA",
            "WHEEL",
            "RECORD",
            "top_level.txt",
        )
    }
    with zipfile.ZipFile(wheel) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(set(names)) != len(names) or len({name.casefold() for name in names}) != len(names):
            raise PackageAuditError("wheel 含重复或大小写冲突条目")
        for entry in entries:
            _check_hygiene(entry.filename)
            if entry.is_dir() or stat.S_ISLNK(entry.external_attr >> 16):
                raise PackageAuditError("wheel 不允许目录或链接条目")
        if set(names) != set(expected) | allowed_metadata:
            raise PackageAuditError("wheel 精确文件集合不一致 (可能含 legacy 或缺少 py.typed)")
        for name, source in expected.items():
            content = archive.read(name)
            _check_hygiene(name, content)
            if content != source.read_bytes():
                raise PackageAuditError(f"wheel 源码字节不一致：{name}")
        metadata = BytesParser().parsebytes(archive.read(f"{metadata_root}/METADATA"))
        if metadata.get_all("Name") != ["zniku"] or metadata.get_all("Version") != [version]:
            raise PackageAuditError("wheel metadata 产品身份/版本不一致")
        _check_hygiene(f"{metadata_root}/METADATA", archive.read(f"{metadata_root}/METADATA"))
        rows = list(
            csv.reader(io.StringIO(archive.read(f"{metadata_root}/RECORD").decode("utf-8")))
        )
        if any(len(row) != 3 for row in rows) or len(rows) != len(names):
            raise PackageAuditError("wheel RECORD 行数或结构不正确")
        if {row[0] for row in rows} != set(names):
            raise PackageAuditError("wheel RECORD 未精确覆盖所有文件")
        for name, digest, size in rows:
            if name == f"{metadata_root}/RECORD":
                if digest or size:
                    raise PackageAuditError("wheel RECORD 自身不得携带摘要")
                continue
            data = archive.read(name)
            checksum = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            if digest != f"sha256={checksum}" or size != str(len(data)):
                raise PackageAuditError(f"wheel RECORD 摘要或大小不一致：{name}")
    return {
        "kind": "wheel",
        "product_version": version,
        "package_files_exact": len(expected),
        "total_entries": len(names),
        "source_bytes_exact": True,
        "record_valid": True,
    }


def _archive_reader(executable: Path) -> _ArchiveReader:
    try:
        module = importlib.import_module("PyInstaller.archive.readers")
    except ImportError as error:
        raise PackageAuditError("PYZ 审计需要安装 desktop extra 的 PyInstaller") from error
    return cast(_ArchiveReader, module.CArchiveReader(str(executable)))


def _audit_pyz(executable: Path, repository: Path, sources: Mapping[str, Path]) -> int:
    archive = _archive_reader(executable)
    pyz_names = [name for name, value in archive.toc.items() if value[-1] == "z"]
    if pyz_names != ["PYZ.pyz"]:
        raise PackageAuditError("exe 必须含唯一 PYZ.pyz")
    pyz = archive.open_embedded_archive("PYZ.pyz")
    expected: dict[str, Path] = {}
    for name, path in sources.items():
        if name.endswith(".py"):
            parts = list(PurePosixPath(name).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            expected[".".join(parts)] = path
    actual = {name for name in pyz.toc if name == "zniku" or name.startswith("zniku.")}
    if actual != set(expected):
        raise PackageAuditError("实际 PYZ 的 zniku 模块集合不一致 (legacy 或缺失模块)")
    for name, source in expected.items():
        packed = pyz.extract(name)
        if not isinstance(packed, CodeType) or packed != compile(
            source.read_bytes(),
            packed.co_filename,
            "exec",
            dont_inherit=True,
            optimize=0,
        ):
            raise PackageAuditError(f"实际 PYZ 编译代码与源码不同：{name}")
    # 除 zniku 外也拒绝藏在 CArchive 的项目/媒体/日志；普通 Python runtime hook 仍允许。
    for name in archive.toc:
        _check_hygiene(name)
    import marshal

    entry = marshal.loads(archive.extract("desktop_entry"))
    if not isinstance(entry, CodeType) or entry != compile(
        (repository / "tools/desktop_entry.py").read_bytes(),
        entry.co_filename,
        "exec",
        dont_inherit=True,
        optimize=0,
    ):
        raise PackageAuditError("exe desktop_entry 与当前源码不一致")
    return len(expected)


def audit_desktop(package: Path, repository: Path, media_root: Path) -> dict[str, object]:
    """核对真实 exe/PYZ、附带源码、静态资源及原始工具；不依赖 COLLECT 自报清单。"""

    files = _files(package)
    if {PurePosixPath(name).parts[0] for name in files} != {
        "ZNIKU Studio.exe",
        "_internal",
        "BUILD-INFO.json",
        "开始使用.txt",
    }:
        raise PackageAuditError("one-folder 顶层含缺失或额外内容")
    sources = source_files(repository)
    packed_sources = {
        name.removeprefix("_internal/"): path
        for name, path in files.items()
        if name.startswith("_internal/zniku/")
    }
    _compare_files(sources, packed_sources, "one-folder 源码")
    assets = _files(repository / "apps/studio/dist")
    packed_assets = {
        name.removeprefix("_internal/studio/"): path
        for name, path in files.items()
        if name.startswith("_internal/studio/")
    }
    _compare_files(assets, packed_assets, "Studio production")
    media = _media_inputs(media_root)
    packed_media = {
        name: path
        for name, path in files.items()
        if name.startswith(("_internal/media-tools/", "_internal/licenses/ffmpeg/"))
    }
    _compare_files(media, packed_media, "媒体工具和原始许可")
    for name, path in files.items():
        # 第三方文本也可能夹带安装者路径或秘密；不因来自依赖目录而跳过同一卫生规则。
        # 不重写或清理原始许可，检测失败只拒绝候选并保留现场。
        if path.suffix.casefold() in _TEXT_SUFFIXES or path.name in {"METADATA", "WHEEL"}:
            _check_hygiene(name, path.read_bytes())
        if path.suffix.casefold() == ".zip":
            with zipfile.ZipFile(path) as archive:
                for entry in archive.infolist():
                    if not entry.is_dir():
                        _check_hygiene(entry.filename)
    info = json.loads(files["BUILD-INFO.json"].read_text("utf-8"))
    version = product_version(repository)
    metadata_files = {
        name
        for name in files
        if re.fullmatch(
            r"_internal/zniku-[^/]+\.dist-info/METADATA",
            name,
        )
    }
    metadata_path = f"_internal/zniku-{version}.dist-info/METADATA"
    if metadata_files != {metadata_path}:
        raise PackageAuditError("one-folder 安装元数据缺失或产品版本不一致")
    metadata = BytesParser().parsebytes(files[metadata_path].read_bytes())
    if metadata.get_all("Name") != ["zniku"] or metadata.get_all("Version") != [version]:
        raise PackageAuditError("one-folder metadata 产品身份/版本不一致")
    metadata_prefix = f"_internal/zniku-{version}.dist-info/"
    if {
        name.removeprefix(metadata_prefix) for name in files if name.startswith(metadata_prefix)
    } != {
        "METADATA",
        "WHEEL",
        "top_level.txt",
    }:
        raise PackageAuditError("one-folder 的 zniku metadata 超出明确许可清单")
    _check_hygiene(metadata_path, files[metadata_path].read_bytes())
    if info.get("product") != "ZNIKU Studio" or info.get("product_version") != version:
        raise PackageAuditError("BUILD-INFO 产品身份/版本不一致")
    if str(info.get("python_version", "")).split(".")[:2] != [str(v) for v in sys.version_info[:2]]:
        raise PackageAuditError("请使用与构建相同 Python minor 校验 PYZ")
    expected_info = build_source_info(repository, media_root)
    # 当前审计可能在提交之后，SHA/dirty 是构建时事实；只比较当时记录的精确内容摘要。
    if (
        info.get("source_kind") != "working_tree"
        or type(info.get("git_dirty")) is not bool
        or re.fullmatch("[0-9a-f]{40}", str(info.get("git_base_commit", ""))) is None
        or info.get("source_files_sha256") != expected_info["source_files_sha256"]
        or info.get("media_files_sha256") != expected_info["media_files_sha256"]
    ):
        raise PackageAuditError("BUILD-INFO 来源或输入字节已改变，必须重新构建候选")
    modules = _audit_pyz(files["ZNIKU Studio.exe"], repository, sources)
    return {
        "kind": "one-folder",
        "product_version": version,
        "python_modules_exact": modules,
        "package_sources_exact": len(sources),
        "production_assets_exact": len(assets),
        "media_and_licenses_exact": len(media),
        "files": len(files),
        "total_bytes": sum(path.stat().st_size for path in files.values()),
        "source_kind": info["source_kind"],
        "git_base_commit": info["git_base_commit"],
        "git_dirty_at_build": info["git_dirty"],
        "user_service_started_or_changed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="只读核对 ZNIKU 候选内容，不执行包或媒体工具")
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--media-distribution-root", type=Path)
    args = parser.parse_args()
    if args.wheel is None and args.package is None:
        parser.error("至少指定 --wheel 或 --package")
    if args.package is not None and args.media_distribution_root is None:
        parser.error("one-folder 审计必须显式提供 --media-distribution-root")
    results = []
    if args.wheel is not None:
        results.append(audit_wheel(args.wheel, args.repository))
    if args.package is not None:
        results.append(audit_desktop(args.package, args.repository, args.media_distribution_root))
    print(json.dumps({"audits": results}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
