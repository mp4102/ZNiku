"""公开 ZNIKU 0.2.0 Graph Core 的领域模型与验证接口。

该命名空间与 0.1.0 contracts/workflow 模块严格分离，避免旧 scope、ArtifactSet、Final、Compiler 或
digest 语义进入新的自由媒体 DAG。
"""

from .models import (
    JSON_SCHEMA_DIALECT,
    Cardinality,
    CommandExecutorSpec,
    CorePortType,
    Edge,
    ExecutionMode,
    ExecutorSpec,
    Graph,
    ManualExternalExecutorSpec,
    NodeDefinition,
    NodeInstance,
    PortSpec,
    PythonExecutorSpec,
    UiPosition,
    ValidatorSpec,
)
from .validation import GraphValidationError, GraphValidator, GraphViolation

__all__ = [
    "JSON_SCHEMA_DIALECT",
    "Cardinality",
    "CommandExecutorSpec",
    "CorePortType",
    "Edge",
    "ExecutionMode",
    "ExecutorSpec",
    "Graph",
    "GraphValidationError",
    "GraphValidator",
    "GraphViolation",
    "ManualExternalExecutorSpec",
    "NodeDefinition",
    "NodeInstance",
    "PortSpec",
    "PythonExecutorSpec",
    "UiPosition",
    "ValidatorSpec",
]
