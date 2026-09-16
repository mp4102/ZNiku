"""整章输出全部通过才登记任何 Artifact；沿用已有增强与重叠 FI 媒体验收成本。"""

from zniku.runtime import NodeValidatorContext, NodeValidatorResult
from zniku.source_aligned import validators as shared

from .contracts import NAMESPACE, BatchMetadata, preflight
from .definitions import definition_role


def _validate(context: NodeValidatorContext, role: str) -> NodeValidatorResult:
    return shared._validate(
        context,
        role,
        contract_reader=preflight,
        role_reader=definition_role,
        metadata_model=BatchMetadata,
        namespace=NAMESPACE,
        verify_original_audio_origins=False,
        report_output_port=role == "enhancement",
        output_name=lambda role, port: (
            f"{port}.enhancement.mov" if role == "enhancement" else shared._NAMES[role]
        ),
    )


def validate_enhancement(context: NodeValidatorContext) -> NodeValidatorResult:
    return _validate(context, "enhancement")


def validate_merge(context: NodeValidatorContext) -> NodeValidatorResult:
    return _validate(context, "merge")


def validate_context(context: NodeValidatorContext) -> NodeValidatorResult:
    return _validate(context, "context")


def validate_fi(context: NodeValidatorContext) -> NodeValidatorResult:
    return _validate(context, "fi")


def validate_crop(context: NodeValidatorContext) -> NodeValidatorResult:
    return _validate(context, "crop")


def validate_program(context: NodeValidatorContext) -> NodeValidatorResult:
    return _validate(context, "program")


def validate_final(context: NodeValidatorContext) -> NodeValidatorResult:
    return _validate(context, "final")
