"""锁定 0.2.0 Phase 0 的单一架构权威与 0.1.0 只读归档边界。"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
ACTIVE_ARCHITECTURE = ROOT / "docs" / "architecture"
LEGACY_ARCHIVE = ROOT / "docs" / "archive" / "0.1.0"

LEGACY_ARCHITECTURE_PATHS = (
    Path("agent-application-baseline.md"),
    Path("default-workflow-baseline.md"),
    Path("engine-contract.md"),
    Path("engine-sdk-baseline.md"),
    Path("execution-runtime-baseline.md"),
    Path("phase6-extension-validation-baseline.md"),
    Path("product-framework.md"),
    Path("real-media-acceptance-candidate-baseline.md"),
    Path("studio-formal-baseline.md"),
    Path("studio-framework.md"),
    Path("workflow-authoring-compiler-baseline.md"),
    Path("zbaton/design-baseline.md"),
    Path("zbaton/processing-history.md"),
    Path("zbaton/sample.json"),
)
MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\((?P<target>[^)]+)\)")


def test_graph_core_is_the_only_active_architecture_document() -> None:
    active_files = {
        path.relative_to(ACTIVE_ARCHITECTURE)
        for path in ACTIVE_ARCHITECTURE.rglob("*")
        if path.is_file()
    }

    assert active_files == {Path("graph-core-baseline.md")}
    baseline = (ACTIVE_ARCHITECTURE / "graph-core-baseline.md").read_text("utf-8")
    assert "已批准的唯一 0.2.0 目标架构基线" in baseline
    assert "Phase 0 已实施" in baseline
    assert "Phase 1" in baseline and "尚未实施" in baseline


def test_all_legacy_architecture_documents_are_archived() -> None:
    for relative_path in LEGACY_ARCHITECTURE_PATHS:
        assert not (ACTIVE_ARCHITECTURE / relative_path).exists()
        assert (LEGACY_ARCHIVE / relative_path).is_file()

    archive_index = (LEGACY_ARCHIVE / "README.md").read_text("utf-8")
    assert "对 0.2.0 没有规范权威" in archive_index
    for archived_markdown in LEGACY_ARCHIVE.rglob("*.md"):
        if archived_markdown.name != "README.md":
            assert "0.1.0 历史归档" in archived_markdown.read_text("utf-8")


def test_active_guidance_points_only_to_the_graph_core_authority() -> None:
    agents = (ROOT / "AGENTS.md").read_text("utf-8")
    readme = (ROOT / "README.md").read_text("utf-8")
    studio_readme = (ROOT / "apps" / "studio" / "README.md").read_text("utf-8")

    assert "docs/architecture/graph-core-baseline.md" in agents
    assert "唯一目标架构权威" in agents
    assert "多个 Source、多个 Output、零 Output" in agents
    assert "目标版本：`ZNIKU Studio 0.2.0`" in readme
    assert "当前可运行代码：`main@198d802` 的 `0.1.0` legacy implementation" in readme
    assert "../../docs/architecture/graph-core-baseline.md" in studio_readme

    active_guidance = "\n".join((agents, readme, studio_readme))
    for legacy_path in LEGACY_ARCHITECTURE_PATHS:
        old_link = f"docs/architecture/{legacy_path.as_posix()}"
        assert old_link not in active_guidance


def test_local_document_links_resolve_after_archiving() -> None:
    markdown_files = [
        ROOT / "README.md",
        ROOT / "apps" / "studio" / "README.md",
        *sorted((ROOT / "docs").rglob("*.md")),
    ]

    for markdown_file in markdown_files:
        content = markdown_file.read_text("utf-8")
        for match in MARKDOWN_LINK.finditer(content):
            target = match.group("target").strip().strip("<>")
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path_text = target.split("#", maxsplit=1)[0]
            if path_text:
                assert (markdown_file.parent / path_text).exists(), (
                    f"{markdown_file.relative_to(ROOT)} 包含失效链接：{target}"
                )
