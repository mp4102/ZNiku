"""ZNIKU 0.1.0 Phase 1A 领域合同的唯一公共导入面。"""

from .base import ContractModel, JsonObject
from .engine import (
    EngineInputContract,
    EngineLifecycleCapabilities,
    EngineManifest,
    EngineOutputContract,
    ExecutionMode,
    MediaAttributeDisposition,
    MediaAttributeRule,
    MediaAttributeSource,
    validate_contract_artifact,
)
from .errors import ContractViolation
from .identifiers import AttributePath, ExactVersion, Sha256Digest, StableId
from .media import (
    Artifact,
    ArtifactSet,
    ArtifactSetMember,
    ArtifactType,
    CoverageMode,
    CoverageSpan,
    CoverageUnit,
    MediaKind,
    Scope,
)
from .ports import Cardinality, PortSpec, assert_ports_compatible, ports_compatible
from .schema import JSON_SCHEMA_DIALECT
from .stage import (
    ArtifactRef,
    ArtifactSetRef,
    EngineBinding,
    PortBinding,
    StageRun,
    validate_stage_run_bindings,
)

__all__ = [
    "JSON_SCHEMA_DIALECT",
    "Artifact",
    "ArtifactRef",
    "ArtifactSet",
    "ArtifactSetMember",
    "ArtifactSetRef",
    "ArtifactType",
    "AttributePath",
    "Cardinality",
    "ContractModel",
    "ContractViolation",
    "CoverageMode",
    "CoverageSpan",
    "CoverageUnit",
    "EngineBinding",
    "EngineInputContract",
    "EngineLifecycleCapabilities",
    "EngineManifest",
    "EngineOutputContract",
    "ExactVersion",
    "ExecutionMode",
    "JsonObject",
    "MediaAttributeDisposition",
    "MediaAttributeRule",
    "MediaAttributeSource",
    "MediaKind",
    "PortBinding",
    "PortSpec",
    "Scope",
    "Sha256Digest",
    "StableId",
    "StageRun",
    "assert_ports_compatible",
    "ports_compatible",
    "validate_contract_artifact",
    "validate_stage_run_bindings",
]
