"""定义首批稳定 ID、属性路径和精确版本的词法约束。"""

from __future__ import annotations

from typing import Annotated

from pydantic import StringConstraints

STABLE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$"
ENGINE_ID_PATTERN = r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$"
SEMVER_PATTERN = (
    r"^(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)"
    r"(?:-((?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
ATTRIBUTE_PATH_PATTERN = r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$"

StableId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=STABLE_ID_PATTERN),
]
EngineId = Annotated[
    str,
    StringConstraints(min_length=3, max_length=128, pattern=ENGINE_ID_PATTERN),
]
ExactVersion = Annotated[
    str,
    StringConstraints(min_length=5, max_length=128, pattern=SEMVER_PATTERN),
]
Sha256Digest = Annotated[str, StringConstraints(pattern=DIGEST_PATTERN)]
AttributePath = Annotated[
    str,
    StringConstraints(min_length=1, max_length=160, pattern=ATTRIBUTE_PATH_PATTERN),
]
