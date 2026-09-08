"""以合成文件验证输出位置、受控目录创建、源保护与旧定义兼容，不接触真实媒体。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from zniku.graph import ExecutionMode, NodeDefinition, PortSpec, PythonExecutorSpec
from zniku.media.definitions import (
    is_supported_output_file_definition,
    legacy_output_file_definition,
    output_file_definition,
)
from zniku.media.probe import MediaNodeError
from zniku.media.publication import checked_output_target


def _parameters(tmp_path: Path) -> tuple[dict[str, object], Path, Path]:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"synthetic source")
    root = tmp_path / "test123"
    root.mkdir()
    target = root / "Example (2026)" / "result.mkv"
    return (
        {
            "mode": "copy",
            "overwrite": False,
            "output_root": str(root),
            "create_parent": True,
            "target_path": str(target),
        },
        source,
        target,
    )


def test_directory_creation_only_occurs_at_explicit_publish(tmp_path: Path) -> None:
    parameters, source, target = _parameters(tmp_path)
    with pytest.raises(MediaNodeError, match="E_MEDIA_OUTPUT_PARENT_INVALID"):
        checked_output_target(parameters, source, allow_create=False)
    assert not target.parent.exists()
    assert checked_output_target(parameters, source, allow_create=True) == target
    assert target.parent.is_dir()
    assert not target.exists()
    assert checked_output_target(parameters, source, allow_create=False) == target
    assert source.read_bytes() == b"synthetic source"


@pytest.mark.parametrize("case", ["no_root", "no_permission", "nested", "outside", "file"])
def test_creation_is_single_level_and_fail_closed(tmp_path: Path, case: str) -> None:
    parameters, source, target = _parameters(tmp_path)
    if case == "no_root":
        del parameters["output_root"]
    elif case == "no_permission":
        parameters["create_parent"] = False
    elif case == "nested":
        parameters["target_path"] = str(target.parent / "nested" / target.name)
    elif case == "outside":
        parameters["target_path"] = str(tmp_path / "outside" / target.name)
    else:
        target.parent.write_bytes(b"operator-owned")
    with pytest.raises(MediaNodeError):
        checked_output_target(parameters, source, allow_create=True)
    if case == "file":
        assert target.parent.read_bytes() == b"operator-owned"
    else:
        assert not target.parent.exists()
    assert not (tmp_path / "outside").exists()


def test_directory_mkdir_permission_failure_is_actionable_without_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parameters, source, target = _parameters(tmp_path)

    def deny(_path: Path, *args: Any, **kwargs: Any) -> None:
        raise PermissionError("synthetic readonly")

    monkeypatch.setattr(Path, "mkdir", deny)
    with pytest.raises(MediaNodeError, match="E_MEDIA_OUTPUT_PARENT_INVALID"):
        checked_output_target(parameters, source, allow_create=True)
    assert not target.parent.exists()
    assert source.exists()


@pytest.mark.parametrize("hard_link", [False, True])
def test_protected_source_same_path_or_hardlink_is_never_overwritten(
    tmp_path: Path, hard_link: bool
) -> None:
    parameters, source, target = _parameters(tmp_path)
    target.parent.mkdir()
    original = target.parent / "original.mkv"
    original.write_bytes(b"original source")
    if hard_link:
        target.hardlink_to(original)
    else:
        target = original
    parameters.update(target_path=str(target), protected_paths=[str(original)], overwrite=True)
    with pytest.raises(MediaNodeError, match="E_MEDIA_OUTPUT_SAME_PATH"):
        checked_output_target(parameters, source, allow_create=True)
    assert original.read_bytes() == b"original source"


def test_recheck_rejects_parent_and_target_link_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parameters, source, target = _parameters(tmp_path)
    checked_output_target(parameters, source, allow_create=True)
    original = Path.is_symlink
    for replaced in (target.parent, target):
        monkeypatch.setattr(
            Path, "is_symlink", lambda path, selected=replaced: path == selected or original(path)
        )
        # target 必须有目录项才能参与 lstat/reparse 检查。
        target.write_bytes(b"untrusted replacement")
        with pytest.raises(MediaNodeError):
            checked_output_target(parameters, source, allow_create=False)


@pytest.mark.parametrize(
    "extra",
    [
        {"create_parent": True},
        {"create_parent": "true", "output_root": "/tmp"},
        {"mode": "reference", "create_parent": True, "output_root": "/tmp"},
        {"mode": "reference", "output_root": "/tmp"},
        {"unknown": True},
    ],
)
def test_invalid_output_permission_schema_is_rejected(extra: dict[str, object]) -> None:
    parameters = {"mode": "copy", "overwrite": False, "target_path": "/tmp/result.mkv", **extra}
    assert list(
        Draft202012Validator(output_file_definition().parameter_schema).iter_errors(parameters)
    )


def test_only_exact_old_and_new_output_definitions_are_supported() -> None:
    legacy = legacy_output_file_definition()
    assert is_supported_output_file_definition(legacy)
    assert is_supported_output_file_definition(output_file_definition())
    properties = legacy.parameter_schema["properties"]
    assert isinstance(properties, dict)
    assert set(properties) == {"mode", "overwrite", "target_path"}
    tampered = legacy.model_dump(mode="python")
    tampered["parameter_schema"]["additionalProperties"] = True
    assert not is_supported_output_file_definition(
        NodeDefinition.model_validate(tampered, strict=True)
    )


def test_legacy_definition_equals_frozen_19a0f09_shape() -> None:
    """字面 fixture 摘自 git 19a0f09，不能随当前 factory 同步漂移而误报兼容。"""

    frozen = NodeDefinition(
        type_id="zniku.media.output_file.video",
        version="0.2.0",
        input_ports=(PortSpec(port_id="in", data_type="VideoFile", required=True),),
        output_ports=(PortSpec(port_id="published", data_type="VideoFile"),),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="zniku.media.adapters:output_file"),
        parameter_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "required": ["mode", "overwrite"],
            "properties": {
                "target_path": {
                    "type": "string",
                    "minLength": 1,
                    "description": "copy 模式明确选择的本地绝对输出路径。",
                },
                "mode": {
                    "type": "string",
                    "enum": ["copy", "reference"],
                    "default": "copy",
                    "description": "copy 发布到 target_path；reference 保留上游路径。",
                },
                "overwrite": {
                    "type": "boolean",
                    "default": False,
                    "description": "必须显式为 true 才允许覆盖已有文件。",
                },
            },
            "allOf": [
                {
                    "if": {"properties": {"mode": {"const": "copy"}}, "required": ["mode"]},
                    "then": {"required": ["target_path"]},
                }
            ],
        },
    )
    assert frozen == legacy_output_file_definition()
