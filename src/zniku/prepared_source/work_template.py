"""从普通工作准入生成同一 Graph；只复用数学及排布，不借用旧媒体权威。"""

from zniku.avenhance_v27.template import Av27TemplateError, PublicationRequest
from zniku.project import ProjectSnapshot
from zniku.runtime import RunnerInput
from zniku.source_preparation.work_contracts import check_reference_binding
from zniku.source_preparation.work_models import WorkSourceGate

from . import work_definitions
from .template import (
    PreparedSourceBinding as PreparedSourceBinding,
)
from .template import (
    PreparedSourceBuild as PreparedSourceBuild,
)
from .template import (
    PreparedSourceProcessing as PreparedSourceProcessing,
)
from .template import (
    PreparedSourcePublication as PreparedSourcePublication,
)
from .template import (
    build_with_contract,
)

PROFILE = "zniku.prepared-work-overlap@0.3.4-work.1"


def _preset_reference(video: RunnerInput, gate: RunnerInput) -> WorkSourceGate:
    """16:9 是此处理预设的能力，不是通用源或准入的全局要求。"""
    result = check_reference_binding(video, gate)
    if result.geometry.width * 9 != result.geometry.height * 16:
        raise Av27TemplateError(
            "E_PREPARED_SOURCE_PRESET_GEOMETRY",
            "当前分章/增强预设只支持方形像素 16:9 工作参考；"
            "此素材已可建立工作源，但请在高级节点图选择适合其画幅的处理节点。",
        )
    return result


def build_prepared_source(
    current: ProjectSnapshot,
    binding: PreparedSourceBinding,
    processing: PreparedSourceProcessing,
    publication: PublicationRequest,
) -> PreparedSourceBuild:
    """检查的是真实 work gate，所加节点也全部使用新 exact 定义。"""
    return build_with_contract(
        current, binding, processing, publication, _preset_reference, work_definitions
    )
