"""只从新版色彩准入展开同一普通 Graph，不读取旧 gate 或生成未来 Artifact ID。"""

from zniku.avenhance_v27.template import PublicationRequest
from zniku.prepared_source.template import (
    PreparedSourceBinding as PreparedSourceBinding,
)
from zniku.prepared_source.template import (
    PreparedSourceBuild as PreparedSourceBuild,
)
from zniku.prepared_source.template import (
    PreparedSourceProcessing as PreparedSourceProcessing,
)
from zniku.prepared_source.template import (
    PreparedSourcePublication as PreparedSourcePublication,
)
from zniku.prepared_source.template import (
    build_with_contract,
)
from zniku.project import ProjectSnapshot
from zniku.source_color.contracts import check_reference_binding

from . import definitions

PROFILE = "zniku.prepared-color-overlap@0.3.4-color.1"


def build_prepared_source(
    current: ProjectSnapshot,
    binding: PreparedSourceBinding,
    processing: PreparedSourceProcessing,
    publication: PublicationRequest,
) -> PreparedSourceBuild:
    """准入和定义均来自新合同，复用的仅为规划数学与普通 Graph 建图。"""
    return build_with_contract(
        current, binding, processing, publication, check_reference_binding, definitions
    )
