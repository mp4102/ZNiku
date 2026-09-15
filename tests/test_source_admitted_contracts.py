"""以纯合成局部输入证明新 exact lineage 隔离与 Schema 关闭，不运行媒体。"""

from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from test_source_aligned_node_contracts import pipeline
from zniku.avenhance_v27.probe import Av27MediaError
from zniku.source_admission import definitions
from zniku.source_admission.contracts import SOURCE_NAMESPACE, OverlapMetadata, preflight
from zniku.source_aligned.node_contracts import OverlapMetadata as OldMetadata


@pytest.mark.parametrize("value", [True, False, 0, -1, 10001, 1.0, "1"])
def test_new_definition_dynamic_shape_is_strict_before_cache(value: object) -> None:
    definitions.definition("split", 1)
    with pytest.raises(ValueError):
        definitions.definition("split", value)  # type: ignore[arg-type]


def test_split_does_not_masquerade_as_old_producer_or_consume_old_source(tmp_path: Path) -> None:
    step = pipeline(tmp_path)[0]
    with pytest.raises(Av27MediaError, match="SOURCE_VERSION"):
        preflight("split", step.inputs, step.parameters)
    source, gate = step.inputs
    source = replace(
        source, media_info={**source.media_info, SOURCE_NAMESPACE: {"contract_version": "0.3.5"}}
    )
    contract = preflight("split", (source, gate), step.parameters)
    assert sum(value.metadata.frame_count for value in contract.outputs) == 1801
    for output in contract.outputs:
        assert isinstance(output.metadata, OverlapMetadata)
        assert output.metadata.producer_version == "0.3.5"
        with pytest.raises(ValidationError):
            OldMetadata.model_validate(output.metadata.model_dump())
    with pytest.raises(ValidationError):
        OverlapMetadata.model_validate(step.contract.outputs[0].metadata.model_dump())


def test_new_role_identification_requires_full_exact_schema_and_executor() -> None:
    source = definitions.source_program_definition()
    assert definitions.definition_role(source) == "source"
    data = source.model_dump()
    data["executor"]["adapter"] = "third.party:source"
    assert definitions.definition_role(type(source).model_validate(data)) is None
    assert len(definitions.built_in_definitions()) == 13
