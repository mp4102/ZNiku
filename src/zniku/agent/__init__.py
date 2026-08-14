"""公开不持有 Runtime authority 的可选 Agent adapter。"""

from .adapter import (
    AGENT_TOOL_CONTRACT_VERSION,
    AgentAdapter,
    AgentErrorExplanation,
    AgentToolName,
    AgentToolResponse,
)

__all__ = [
    "AGENT_TOOL_CONTRACT_VERSION",
    "AgentAdapter",
    "AgentErrorExplanation",
    "AgentToolName",
    "AgentToolResponse",
]
