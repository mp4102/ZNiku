"""公开 v0.3.4 素材检查、保内容准备、外部修复与工作参考准入接口。"""

from .contracts import (
    check_reference_binding,
    read_diagnosis,
    read_gate,
    read_report_artifact,
    validate_audio_sources,
)
from .definitions import (
    ADMISSION_TYPE_ID,
    BUILTIN_PREPARE_TYPE_ID,
    DIAGNOSTICS_TYPE_ID,
    EXTERNAL_REPAIR_TYPE_PREFIX,
    SOURCE_TYPE_ID,
    admission_definition,
    builtin_prepare_definition,
    diagnostics_definition,
    external_repair_definition,
    register_source_preparation_adapters,
    register_source_preparation_validators,
    source_definition,
    source_preparation_definitions,
)
from .models import SOURCE_PREPARATION_VERSION, DiagnosticReport, SourceGate, summary_for_report
from .template import build_preparation_graph

__all__ = [
    "ADMISSION_TYPE_ID",
    "BUILTIN_PREPARE_TYPE_ID",
    "DIAGNOSTICS_TYPE_ID",
    "EXTERNAL_REPAIR_TYPE_PREFIX",
    "SOURCE_PREPARATION_VERSION",
    "SOURCE_TYPE_ID",
    "DiagnosticReport",
    "SourceGate",
    "admission_definition",
    "build_preparation_graph",
    "builtin_prepare_definition",
    "check_reference_binding",
    "diagnostics_definition",
    "external_repair_definition",
    "read_diagnosis",
    "read_gate",
    "read_report_artifact",
    "register_source_preparation_adapters",
    "register_source_preparation_validators",
    "source_definition",
    "source_preparation_definitions",
    "summary_for_report",
    "validate_audio_sources",
]
