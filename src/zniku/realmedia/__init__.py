"""公开 ZNIKU 0.1.0 本地真实媒体验收候选的窄化接口。

本包只执行由 Runtime 组装根注入的受信 FFmpeg adapter。媒体路径属于本机 policy，不进入
WorkflowSpec、ExecutionPlan 或可共享 EngineManifest；任何失败均不得伪造 Evidence。
"""

from .host import RealMediaHostApplication, serve_real_media_host
from .media import (
    DetailedMediaProbe,
    MediaStreamProbe,
    concat_video,
    decode_verify,
    demux_media,
    derive_acceptance_clip,
    encode_hevc_main10,
    extract_chapter,
    hash_audio_stream,
    hash_file,
    make_acceptance_fixture,
    mux_original_audio,
    probe_detailed,
)
from .profile import RealMediaWorkflowBundle, build_real_media_workflow
from .projection import (
    RealHostEnvelope,
    RealMonitorChapter,
    RealMonitorHandoff,
    RealMonitorNode,
    RealMonitorProjection,
    build_host_envelope,
    build_monitor_projection,
)
from .runtime import (
    MediaFileArtifact,
    RealFullVerification,
    RealManualHandoff,
    RealMediaCandidateRuntime,
    RealNodeRun,
    RealRunAuthority,
    RealRuntimeSnapshot,
    RealStageEvidence,
)

__all__ = [
    "DetailedMediaProbe",
    "MediaFileArtifact",
    "MediaStreamProbe",
    "RealFullVerification",
    "RealHostEnvelope",
    "RealManualHandoff",
    "RealMediaCandidateRuntime",
    "RealMediaHostApplication",
    "RealMediaWorkflowBundle",
    "RealMonitorChapter",
    "RealMonitorHandoff",
    "RealMonitorNode",
    "RealMonitorProjection",
    "RealNodeRun",
    "RealRunAuthority",
    "RealRuntimeSnapshot",
    "RealStageEvidence",
    "build_host_envelope",
    "build_monitor_projection",
    "build_real_media_workflow",
    "concat_video",
    "decode_verify",
    "demux_media",
    "derive_acceptance_clip",
    "encode_hevc_main10",
    "extract_chapter",
    "hash_audio_stream",
    "hash_file",
    "make_acceptance_fixture",
    "mux_original_audio",
    "probe_detailed",
    "serve_real_media_host",
]
