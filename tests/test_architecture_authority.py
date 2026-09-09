"""锁定 Graph Core 的单一上位权威、正式 Python namespace 与只读文档归档边界。"""

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
LEGACY_PYTHON_PACKAGES = (
    "agent",
    "application",
    "authoring",
    "contracts",
    "engines",
    "history",
    "pipelines",
    "realmedia",
    "studio",
    "validation",
    "workflow",
)


def test_graph_core_is_the_upper_authority_and_designs_are_subordinate() -> None:
    active_files = {
        path.relative_to(ACTIVE_ARCHITECTURE)
        for path in ACTIVE_ARCHITECTURE.rglob("*")
        if path.is_file()
    }

    assert active_files == {
        Path("av-enhance-flow-v2.7-template-contract.md"),
        Path("graph-core-baseline.md"),
        Path("host-bridge-prototype.md"),
        Path("media-node-contract.md"),
        Path("studio-schema-corpus.json"),
        Path("studio-run-observability.md"),
        Path("studio-ux-baseline.md"),
    }
    baseline = (ACTIVE_ARCHITECTURE / "graph-core-baseline.md").read_text("utf-8")
    host_bridge_prototype = (ACTIVE_ARCHITECTURE / "host-bridge-prototype.md").read_text("utf-8")
    media_contract = (ACTIVE_ARCHITECTURE / "media-node-contract.md").read_text("utf-8")
    observability = (ACTIVE_ARCHITECTURE / "studio-run-observability.md").read_text("utf-8")
    studio_ux = (ACTIVE_ARCHITECTURE / "studio-ux-baseline.md").read_text("utf-8")
    av27_contract = (ACTIVE_ARCHITECTURE / "av-enhance-flow-v2.7-template-contract.md").read_text(
        "utf-8"
    )
    assert "已批准的 Graph\uff0fRuntime 上位架构权威" in baseline
    assert "0.2.0 Phase 0\u20135 已实施" in baseline
    assert "v0.3.0 Phase 0 窄幅修订" in baseline
    assert "Phase 5 尚未实施" not in baseline
    assert "studio-ux-baseline.md" in baseline
    assert "无条件以本文为准" in baseline
    assert "同一个 `Project.graph`" in baseline
    assert "`E_REQUIRED_INPUT_MISSING`" in baseline
    assert "每次创建 Run 前" in baseline
    assert "第二张 Graph" in baseline
    assert "非架构权威" in host_bridge_prototype
    assert "studio-ux-baseline.md" in host_bridge_prototype
    assert "graph-core-baseline.md" in host_bridge_prototype
    assert "graph-core-baseline.md" in media_contract
    assert "本文从属于" in media_contract
    assert "发生冲突时以上位基线为准" in media_contract
    for subordinate_design in (observability, av27_contract, studio_ux):
        assert "graph-core-baseline.md" in subordinate_design
        assert "下位设计" in subordinate_design
        assert "上位架构权威" in subordinate_design


def test_all_legacy_architecture_documents_are_archived() -> None:
    for relative_path in LEGACY_ARCHITECTURE_PATHS:
        assert not (ACTIVE_ARCHITECTURE / relative_path).exists()
        assert (LEGACY_ARCHIVE / relative_path).is_file()

    archive_index = (LEGACY_ARCHIVE / "README.md").read_text("utf-8")
    assert "对 0.2.0 没有规范权威" in archive_index
    for archived_markdown in LEGACY_ARCHIVE.rglob("*.md"):
        if archived_markdown.name != "README.md":
            assert "0.1.0 历史归档" in archived_markdown.read_text("utf-8")


def test_active_guidance_preserves_core_authority_and_ux_subordination() -> None:
    agents = (ROOT / "AGENTS.md").read_text("utf-8")
    readme = (ROOT / "README.md").read_text("utf-8")
    studio_readme = (ROOT / "apps" / "studio" / "README.md").read_text("utf-8")
    package_doc = (ROOT / "src" / "zniku" / "__init__.py").read_text("utf-8")

    assert "docs/architecture/graph-core-baseline.md" in agents
    assert "唯一上位架构" in agents
    assert "docs/architecture/studio-ux-baseline.md" in agents
    assert "正式下位设计" in agents
    assert "多个 Source、多个 Output、零 Output" in agents
    assert "目标版本：`ZNIKU Studio v0.3.1`" in readme
    assert "docs/v0.3.1-ui-optimization-plan.md" in agents
    assert "docs/v0.3.1-ui-optimization-plan.md" in readme
    assert "当前实现版本：`0.3.1`" in readme
    assert "`v0.2.1` Phase 0\u20135 已完成" in readme
    assert "docs/architecture/studio-ux-baseline.md" in readme
    assert "docs/v0.3.0-execution-plan.md" in readme
    assert all(
        public_module in readme
        for public_module in (
            "`zniku.graph`",
            "`zniku.project`",
            "`zniku.runtime`",
            "`zniku.project_service`",
            "`zniku.media`",
            "`zniku.avenhance_v27`",
            "`zniku.presentation`",
        )
    )
    assert "0.1.0 contracts" in package_doc
    assert "Project Service" in package_doc and "唯一正式 Studio" in package_doc
    assert "../../docs/architecture/graph-core-baseline.md" in studio_readme

    active_guidance = "\n".join((agents, readme, studio_readme, package_doc))
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


def test_legacy_python_implementation_is_not_shipped_as_product_code() -> None:
    package_root = ROOT / "src" / "zniku"
    for package_name in LEGACY_PYTHON_PACKAGES:
        assert not tuple((package_root / package_name).glob("*.py"))

    assert {init_file.parent.name for init_file in package_root.glob("*/__init__.py")} == {
        "desktop",
        "graph",
        "avenhance_v27",
        "media",
        "project",
        "project_service",
        "presentation",
        "runtime",
    }
