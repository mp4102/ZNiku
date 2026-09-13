"""覆盖可读目录真实收件调用链：选择复制、目录观察、收纳及显式提交，全部使用合成文本。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from authoring_helpers import authoring_command
from test_handoff_import import _persistent_state
from test_handoff_inbox import _inbox_setup
from zniku.project_service import HostBridgeFailure
from zniku.project_service.handoff_import import HandoffImportBinding, _target_path
from zniku.runtime import ExternalOutputTarget, NodeRunState, RunState


def test_readable_ui_import_and_inbox_observe_collect_submit(tmp_path: Path) -> None:
    value = _inbox_setup(tmp_path, readable=True)
    base = value.base
    before = _persistent_state(base)
    first, second = base.nodes["a"], base.nodes["b"]
    assert "__N001" in first.work_dir and "__N002" in second.work_dir
    assert Path(first.work_dir).name == "R001-A001"
    assert value.observe().candidates == ()

    # UI 选择文件只复制到精确目标，源文件保留，Run 仍等待。
    imported = base.preview()
    base.confirm(imported)
    assert base.source.read_bytes() == base.target().read_bytes() == b"899"
    assert not base.target("b").exists()
    assert value.observe().candidates == ()
    assert _persistent_state(base) == before

    # 操作者直接放到另一个任务目录，观察不提交，确认仅规范收纳。
    incoming = value.inbox("b") / "user-export.mov"
    incoming.write_bytes(b"902")
    observed = value.observe("b")
    assert [item.name for item in observed.candidates] == ["user-export.mov"]
    collected = value.preview("b", handle=observed.candidates[0].candidate_handle)
    value.confirm(collected)
    assert not incoming.exists() and base.target("b").read_bytes() == b"902"
    assert _persistent_state(base) == before

    for node in (first, second):
        assert node.external_handoff is not None
        authoring_command(
            base.application,
            {
                "operation": "submit_external",
                "run_id": node.run_id,
                "node_run_id": node.node_run_id,
                "handoff_id": node.external_handoff.handoff_id,
            },
        )
        assert base.application.wait_until_idle(timeout=5)
    completed = base.application.inspect_run_detail(first.run_id).run
    assert completed.state is RunState.COMPLETED
    assert all(item.state is NodeRunState.COMPLETED for item in completed.node_runs)
    assert sum(len(item.output_artifact_ids) for item in completed.node_runs) == 2


@pytest.mark.parametrize("change", ["neighbor", "unmapped", "outside_outputs", "legacy_name"])
def test_readable_import_never_accepts_a_different_persisted_attempt(
    tmp_path: Path, change: str
) -> None:
    value = _inbox_setup(tmp_path, readable=True)
    before = _persistent_state(value.base)
    binding = HandoffImportBinding.model_validate(value.binding())
    with value.base.application.handoff_import_authority(binding) as authority:
        assert _target_path(authority) == value.base.target()
        if change == "neighbor":
            forged = replace(
                authority,
                node_run=authority.node_run.model_copy(
                    update={"work_dir": value.base.nodes["b"].work_dir}
                ),
                target=ExternalOutputTarget(port_id="video", path=str(value.base.target("b"))),
            )
        elif change == "unmapped":
            assert authority.storage is not None
            forged = replace(
                authority,
                storage=authority.storage.model_copy(
                    update={
                        "layout_state": authority.storage.layout_state.model_copy(
                            update={"nodes": {}}
                        )
                    }
                ),
            )
        elif change == "outside_outputs":
            forged = replace(
                authority,
                target=ExternalOutputTarget(
                    port_id="video", path=str(Path(authority.node_run.work_dir) / "logs/stdout.log")
                ),
            )
        else:
            # 只把最后一级改成 UUID 不能绕过明确的 readable 映射。
            fake_path = Path(authority.work_root) / authority.node_run.node_run_id.replace("-", "")
            fake_path.mkdir()
            (fake_path / "outputs").mkdir()
            forged = replace(
                authority,
                node_run=authority.node_run.model_copy(update={"work_dir": str(fake_path)}),
                target=ExternalOutputTarget(
                    port_id="video", path=str(fake_path / "outputs/enhancement.mov")
                ),
            )
        with pytest.raises(
            HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_IMPORT_PATH"
        ):
            _target_path(forged)
    assert not value.base.target().exists() and not value.base.target("b").exists()
    assert _persistent_state(value.base) == before
