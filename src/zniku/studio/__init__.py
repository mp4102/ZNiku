"""公开 Python→ZNIKU Studio 的只读产品投影。"""

from .projection import (
    STUDIO_PROJECTION_CONTRACT_VERSION,
    CoreOperatorProjection,
    StudioAuthorityProjection,
    build_studio_authority_projection,
)

__all__ = [
    "STUDIO_PROJECTION_CONTRACT_VERSION",
    "CoreOperatorProjection",
    "StudioAuthorityProjection",
    "build_studio_authority_projection",
]
