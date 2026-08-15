"""验证真实媒体候选在编译前绑定精确非合成 Engine authority。"""

from zniku.authoring import EngineStageNodeSpec
from zniku.realmedia import build_real_media_workflow


def test_real_media_profile_replaces_all_automatic_media_bindings() -> None:
    bundle = build_real_media_workflow()
    engines = {
        node.node_id: node.engine.engine_id
        for node in bundle.spec.nodes
        if isinstance(node, EngineStageNodeSpec)
    }
    assert engines == {
        "node.demux": "zniku.builtin.ffmpeg-demux",
        "node.video_encode": "zniku.builtin.ffmpeg-hevc-main10",
        "node.mux": "zniku.builtin.ffmpeg-mux",
    }
    assert all(
        "synthetic" not in manifest.engine_id
        for manifest in bundle.manifests
        if manifest.execution_mode.value == "automatic"
    )
    assert bundle.compiler.validate(bundle.spec).diagnostics == ()
