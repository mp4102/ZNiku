"""锁定 Phase 5 清理边界，防止 0.1.0 可执行面重新混入当前门禁。

历史设计文档仍保存在 ``docs/archive/0.1.0``，但旧测试、工具、fixture 和 Studio 投影不再作为
0.2.0 的可执行产品或回归权威。
"""

from pathlib import Path

ROOT = Path(__file__).parents[1]

LEGACY_TEST_PATHS = (
    "tests/conftest.py",
    "tests/fixtures/legacy/real-media-host.schema.json",
    "tests/fixtures/program-media-engine-manifest.json",
    "tests/fixtures/valid-engine-manifest.json",
    "tests/support/authoring_bridge_harness.py",
    "tests/test_agent_adapter.py",
    "tests/test_artifacts.py",
    "tests/test_authoring_projection.py",
    "tests/test_authoring_service.py",
    "tests/test_default_pipeline.py",
    "tests/test_engine_manifest.py",
    "tests/test_engine_sdk.py",
    "tests/test_execution.py",
    "tests/test_phase6_extensions.py",
    "tests/test_phase6_validation.py",
    "tests/test_ports.py",
    "tests/test_real_media_acceptance.py",
    "tests/test_real_media_host.py",
    "tests/test_real_media_profile.py",
    "tests/test_real_media_runtime.py",
    "tests/test_real_media_tools.py",
    "tests/test_serialization.py",
    "tests/test_stage_run.py",
    "tests/test_workflow_compiler.py",
    "tests/test_workflow_models.py",
    "tests/test_zbaton_projection.py",
)
LEGACY_TOOL_PATHS = (
    "tools/generate_authoring_projection.py",
    "tools/generate_real_media_projection.py",
    "tools/run_phase6_validation.py",
    "tools/run_real_media_acceptance.py",
    "tools/run_real_media_candidate_host.py",
)
LEGACY_STUDIO_PROJECTIONS = (
    "apps/studio/src/generated/authoring-wire.generated.ts",
    "apps/studio/src/generated/authoring-wire.schema.json",
    "apps/studio/src/generated/core-node-contracts.generated.ts",
    "apps/studio/src/generated/core-node-contracts.json",
    "apps/studio/src/generated/core-node-contracts.schema.json",
    "apps/studio/src/generated/engine-manifest.generated.ts",
    "apps/studio/src/generated/engine-manifest.schema.json",
    "apps/studio/src/generated/projection-manifest.generated.ts",
    "apps/studio/src/generated/projection-manifest.json",
    "apps/studio/src/generated/projection-manifest.schema.json",
    "apps/studio/src/generated/studio-authority.generated.ts",
    "apps/studio/src/generated/studio-authority.json",
    "apps/studio/src/generated/studio-authority.schema.json",
    "apps/studio/src/test/fixtures/python-authority.json",
)


def test_legacy_executable_surfaces_stay_removed() -> None:
    for relative_path in (
        *LEGACY_TEST_PATHS,
        *LEGACY_TOOL_PATHS,
        *LEGACY_STUDIO_PROJECTIONS,
    ):
        assert not (ROOT / relative_path).exists(), relative_path


def test_current_ci_uses_the_short_synthetic_smoke_only() -> None:
    core_ci = (ROOT / ".github" / "workflows" / "contract-ci.yml").read_text("utf-8")

    assert "python tools/run_media_smoke.py" in core_ci
    assert "verify-legacy" not in core_ci
    for relative_path in LEGACY_TOOL_PATHS:
        assert Path(relative_path).name not in core_ci


def test_phase5_acceptance_keeps_history_non_executable() -> None:
    acceptance = (ROOT / "docs" / "phase5-acceptance.md").read_text("utf-8")

    assert (ROOT / "docs" / "archive" / "0.1.0").is_dir()
    assert "不参与测试、类型检查、构建或产品运行" in acceptance
    assert "tools/run_media_smoke.py" in acceptance
