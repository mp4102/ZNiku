"""验证准备产物可读命名只覆盖新副本，显示提示不赋予未知定义执行身份。"""

from __future__ import annotations

import pytest

from zniku.graph import NodeInstance
from zniku.project_service.prepared_color_presentation import preparation_output_paths
from zniku.source_color.definitions import (
    builtin_prepare_definition,
    definition_role,
    external_repair_definition,
    source_preparation_definitions,
)


def test_preparation_paths_name_only_new_media_copies() -> None:
    for definition in source_preparation_definitions():
        node = NodeInstance(
            node_id="synthetic",
            type_id=definition.type_id,
            definition_version=definition.version,
        )
        paths = preparation_output_paths(node, definition, media_basename="Example (2026)")
        role = definition_role(definition)
        if role not in {"builtin", "external"}:
            assert paths == ()
            continue
        suffix = (
            "source-prepared.mkv"
            if role == "builtin"
            else ("source-repaired." + definition.type_id.rsplit(".", 1)[-1])
        )
        assert len(paths) == 1
        assert paths[0].port_id == "media"
        assert paths[0].relative_path == f"Example (2026).{suffix}"


def test_preparation_name_does_not_accept_forged_definition_or_mismatched_node() -> None:
    definition = external_repair_definition()
    node = NodeInstance(
        node_id="synthetic", type_id=definition.type_id, definition_version=definition.version
    )
    modified = definition.model_copy(update={"validator": None})
    assert preparation_output_paths(node, modified, media_basename="Example") == ()
    builtin = builtin_prepare_definition()
    assert preparation_output_paths(node, builtin, media_basename="Example") == ()


@pytest.mark.parametrize("basename", ["../outside", "nested/file", "nested\\file", "CON"])
def test_preparation_name_rejects_unsafe_filename_component(basename: str) -> None:
    definition = external_repair_definition()
    node = NodeInstance(
        node_id="synthetic", type_id=definition.type_id, definition_version=definition.version
    )
    with pytest.raises(ValueError):
        preparation_output_paths(node, definition, media_basename=basename)
