"""定义合同内核在跨模型校验时使用的稳定失败语义。"""

from __future__ import annotations

from typing import NoReturn


class ContractViolation(ValueError):
    """表示结构合法但领域关系不满足的 fail-closed 错误。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def fail(code: str, message: str) -> NoReturn:
    """在 Pydantic 模型校验器中使用带稳定代码的失败信息。"""

    raise ValueError(f"{code}: {message}")
