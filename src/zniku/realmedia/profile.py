"""组装 Real Media Acceptance Candidate 的正式 WorkflowSpec 与 Manifest authority。

该 profile 复用已批准默认拓扑和人工 Engine 合同，只把合成 Demux、Encode、Mux 绑定替换为精确的
本地真实媒体 Manifest。替换发生在编译前，ExecutionPlan 因此不会把合成 Engine 身份冒充真实执行。
"""

from __future__ import annotations

from dataclasses import dataclass

from zniku.authoring import (
    CoreNodeContractSet,
    EngineStageNodeSpec,
    InMemoryManifestCatalog,
    WorkflowCompiler,
    WorkflowSpec,
)
from zniku.contracts import EngineBinding, EngineLifecycleCapabilities, EngineManifest
from zniku.pipelines import build_default_workflow


@dataclass(frozen=True, slots=True)
class RealMediaWorkflowBundle:
    """真实媒体候选的 Spec、Manifest 与唯一 Compiler 组装结果。"""

    spec: WorkflowSpec
    manifests: tuple[EngineManifest, ...]
    compiler: WorkflowCompiler


def _real_manifest(
    source: EngineManifest, *, engine_id: str, display_name: str, supports_recovery: bool
) -> EngineManifest:
    return source.model_copy(
        update={
            "engine_id": engine_id,
            "display_name": display_name,
            "lifecycle": EngineLifecycleCapabilities(
                supports_acceptance=True,
                supports_publication=True,
                supports_recovery=supports_recovery,
            ),
        }
    )


def build_real_media_workflow() -> RealMediaWorkflowBundle:
    """构造不含路径或 executable 的真实媒体候选工作流。"""

    default = build_default_workflow()
    synthetic_demux, enhancement, interpolation, synthetic_encode, synthetic_mux = default.manifests
    demux = _real_manifest(
        synthetic_demux,
        engine_id="zniku.builtin.ffmpeg-demux",
        display_name="ZNIKU FFmpeg Demux",
        supports_recovery=True,
    )
    encode = _real_manifest(
        synthetic_encode,
        engine_id="zniku.builtin.ffmpeg-hevc-main10",
        display_name="ZNIKU FFmpeg One-shot HEVC Main10",
        supports_recovery=False,
    )
    mux = _real_manifest(
        synthetic_mux,
        engine_id="zniku.builtin.ffmpeg-mux",
        display_name="ZNIKU FFmpeg Matroska Mux",
        supports_recovery=True,
    )
    bindings = {
        "node.demux": EngineBinding.from_manifest(demux),
        "node.video_encode": EngineBinding.from_manifest(encode),
        "node.mux": EngineBinding.from_manifest(mux),
    }
    nodes = tuple(
        node.model_copy(update={"engine": bindings[node.node_id]})
        if isinstance(node, EngineStageNodeSpec) and node.node_id in bindings
        else node
        for node in default.spec.nodes
    )
    spec = default.spec.model_copy(
        update={"workflow_id": "workflow.real-media-acceptance.0.1.0", "nodes": nodes}
    )
    manifests = (demux, enhancement, interpolation, encode, mux)
    compiler = WorkflowCompiler(InMemoryManifestCatalog(manifests), CoreNodeContractSet.phase_2a())
    return RealMediaWorkflowBundle(spec=spec, manifests=manifests, compiler=compiler)
