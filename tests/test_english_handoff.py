"""用合成 DataFile 验证英文平铺交回、旧布局兼容和明确提交，不访问真实媒体或 NAS。

目录仅用于定位；来件出现、预览、收件和完整检查均不得登记 Artifact。重名输出、安全
端口编码、失败回退及 HostBridge 打开位置必须使用同一份持久交接绑定。
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from test_handoff_batch import _batch, _binding, _check, _confirm, _preview, _sources
from test_handoff_import import _persistent_state, _setup
from test_handoff_inbox import InboxSetup
from test_node_runner import request_for
from zniku.graph import ExecutionMode, ManualExternalExecutorSpec, NodeDefinition, PortSpec
from zniku.project_service import HostBridgeFailure
from zniku.project_service.handoff_batch import HandoffBatchManager
from zniku.project_service.handoff_inbox import HandoffInboxManager
from zniku.runtime.paths import IncomingLayout, incoming_directories, incoming_directory_name
from zniku.runtime.runner import NodeRunner, OutputPathSpec


def test_incoming_layout_is_flat_unique_and_stable_for_duplicate_names(tmp_path: Path) -> None:
    assert incoming_directories(tmp_path, (), layout="english") == {}
    outputs = (("video", str(tmp_path / "a.mov")), ("other", str(tmp_path / "b.mov")))
    flat = incoming_directories(tmp_path, outputs, layout="english")
    assert set(flat.values()) == {tmp_path / "incoming"}
    assert incoming_directories(tmp_path, outputs)["video"] == (
        tmp_path / "incoming" / incoming_directory_name("video")
    )
    duplicates = (("video/../CON", "nested/a.mov"), ("other", "different/A.MOV"))
    separated = incoming_directories(tmp_path, duplicates, layout="english")
    assert separated == incoming_directories(
        tmp_path, tuple(reversed(duplicates)), layout="english"
    )
    assert set(separated.values()) == {
        tmp_path / "incoming" / "output-001",
        tmp_path / "incoming" / "output-002",
    }
    with pytest.raises(ValueError, match="E_INCOMING_LAYOUT"):
        incoming_directories(tmp_path, outputs, layout=cast(IncomingLayout, "unknown"))
    with pytest.raises(ValueError, match="E_INCOMING_BINDING"):
        incoming_directories(tmp_path, (("video", "a.mov"), ("video", "b.mov")), layout="english")


def test_runner_prepares_readable_collision_directories_without_port_hash(tmp_path: Path) -> None:
    definition = NodeDefinition(
        type_id="tests.english.external",
        version="1.0.0",
        execution_mode=ExecutionMode.MANUAL_EXTERNAL,
        output_ports=tuple(
            PortSpec(port_id=port, data_type="DataFile") for port in ("v/one", "v:two")
        ),
        executor=ManualExternalExecutorSpec(instructions="交回两个合成输出"),
    )
    request = replace(
        request_for(
            definition,
            output_paths=(
                OutputPathSpec("v/one", "a/result.dat"),
                OutputPathSpec("v:two", "b/result.dat"),
            ),
        ),
        incoming_layout="english",
    )
    handoff = NodeRunner(tmp_path / "root").prepare_manual(request)
    inbox = Path(handoff.work_dir) / "incoming"
    assert sorted(item.name for item in inbox.iterdir()) == ["output-001", "output-002"]
    assert not list(inbox.rglob("port-*"))


def test_manual_node_without_outputs_keeps_existing_graph_freedom(tmp_path: Path) -> None:
    definition = NodeDefinition(
        type_id="tests.external.review",
        version="1.0.0",
        execution_mode=ExecutionMode.MANUAL_EXTERNAL,
        executor=ManualExternalExecutorSpec(instructions="确认一个无输出的人工步骤"),
    )
    request = replace(request_for(definition), incoming_layout="english")
    handoff = NodeRunner(tmp_path / "root").prepare_manual(request)
    assert handoff.outputs == ()
    assert not (Path(handoff.work_dir) / "incoming").exists()


def test_english_single_inbox_and_host_reveal_use_flat_directory(tmp_path: Path) -> None:
    base = _setup(tmp_path, readable=True)
    value = InboxSetup(base, HandoffInboxManager())
    observed = value.observe()
    work = Path(base.nodes["a"].work_dir)
    assert work.name == "round-001"
    assert Path(observed.inbox_path) == work / "incoming"
    assert not list((work / "incoming").iterdir())
    assert "attempts" not in work.relative_to(tmp_path / "project.data").parts
    binding = value.binding()
    action = base.session.issue_user_action({"capability": "reveal_in_file_manager"})
    base.session.invoke(
        {
            "capability": "reveal_in_file_manager",
            "user_action_id": action.user_action_id,
            "arguments": {
                "reference": {
                    "kind": "handoff",
                    **{key: binding[key] for key in ("run_id", "node_run_id", "handoff_id")},
                    "selector": {"role": "incoming_directory", "port_id": "video", "ordinal": None},
                }
            },
        }
    )
    assert base.platform.launches[-1].argv == (str(work / "incoming"),)


@pytest.mark.parametrize("valid", [True, False])
def test_english_single_candidate_preserves_data_and_never_submits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, valid: bool
) -> None:
    base = _setup(tmp_path, readable=True)
    value = InboxSetup(base, HandoffInboxManager())
    incoming = Path(value.observe().inbox_path)
    source = incoming / "any-export-name.mov"
    source.write_bytes(b"899" if valid else b"wrong")
    inode = source.stat().st_ino
    before = _persistent_state(base)
    if os.name == "nt":

        def no_link(*args: object, **kwargs: object) -> None:
            raise AssertionError("Windows 英文收件不得依赖硬链接")

        monkeypatch.setattr(os, "link", no_link)
    if valid:
        value.confirm(value.preview())
        assert not source.exists()
        assert base.target().read_bytes() == b"899"
        assert base.target().stat().st_ino == inode
    else:
        base.target().write_bytes(b"old")
        with pytest.raises(HostBridgeFailure, match="E_SYNTHETIC_FRAME_COUNT"):
            value.confirm(value.preview(), overwrite=True)
        assert source.read_bytes() == b"wrong"
        assert base.target().read_bytes() == b"old"
    assert _persistent_state(base) == before
    assert not list(Path(base.nodes["a"].work_dir).glob(".handoff-inbox-*"))


def test_english_batch_can_confirm_already_flat_files_then_check_and_submit(tmp_path: Path) -> None:
    value = _batch(tmp_path, readable=True)
    manager = HandoffBatchManager()
    observed = manager.observe(
        _binding(value), session=value.session, application=value.application
    )
    sources = _sources(value, Path(observed.inbox_path))
    before = _persistent_state(value)
    inodes = {path.name: path.stat().st_ino for path in sources}
    preview = _preview(manager, value, ())
    assert all(Path(row.incoming_path).parent == Path(preview.inbox_path) for row in preview.rows)
    confirmed = _confirm(manager, value, preview)
    assert confirmed.complete and all(row.collected for row in confirmed.rows)
    assert all(path.exists() for path in sources)
    checked = _check(manager, value)
    assert checked.published and checked.readiness and checked.readiness.ready_for_submit
    assert all(not path.exists() for path in sources)
    assert all(
        Path(row.target_path).stat().st_ino == inodes[row.target_name] for row in checked.rows
    )
    assert _persistent_state(value) == before
    binding = _binding(value)
    value.application.command(
        {
            "operation": "submit_external",
            **{key: binding[key] for key in ("run_id", "node_run_id", "handoff_id")},
        }
    )
    assert value.application.wait_until_idle()
    detail = value.application.inspect_run_detail(binding["run_id"])
    node = next(item for item in detail.run.node_runs if item.node_id == "a")
    assert node.state.value == "completed" and len(node.output_artifact_ids) == 2


def test_english_bad_batch_retains_flat_incoming_and_old_outputs(tmp_path: Path) -> None:
    value = _batch(tmp_path, readable=True)
    manager = HandoffBatchManager()
    observed = manager.observe(
        _binding(value), session=value.session, application=value.application
    )
    sources = _sources(value, Path(observed.inbox_path), content="wrong")
    before = _persistent_state(value)
    checked = _check(manager, value)
    assert not checked.published and checked.validation_error
    assert all(path.read_text(encoding="utf-8") == "wrong" for path in sources)
    assert all(not Path(row.target_path).exists() for row in checked.rows)
    assert _persistent_state(value) == before
