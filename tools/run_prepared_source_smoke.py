"""通过正式 Project Service 和 Runtime 运行 0.3.4 短合成全链，不使用真实媒体。

原件入口、完整诊断、显式选择、输入型准入与完整 builder 都真实执行；人工增强/FI
由固定合成来件替身显式提交，不伪造 Artifact、Run 状态或 producer metadata。输出只允许 build 子目录。
"""

from __future__ import annotations

import argparse
import json
import shutil
from fractions import Fraction
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from run_source_aligned_smoke import _artifact, _command, _fingerprint, _latest, _signal, _tool

from zniku.avenhance_v27.probe import probe_header
from zniku.media import (
    built_in_media_definitions,
    media_artifact_quick_probe,
    media_python_adapters,
    media_validators,
    runner_media_probe,
)
from zniku.prepared_source.audio import verify_audio_content
from zniku.prepared_source.definitions import (
    built_in_overlap_definitions,
    external_definition,
    overlap_python_adapters,
    overlap_validators,
)
from zniku.prepared_source.node_contracts import OVERLAP_NAMESPACE, OverlapMetadata
from zniku.project import ProjectStore
from zniku.project_service import ProjectServiceApplication
from zniku.project_service.models import RunDetailEnvelope
from zniku.project_service.prepared_source_application import choose, create, full, view
from zniku.runtime import Artifact, NodeRun, NodeRunState, RunState
from zniku.source_preparation import models as preparation_models
from zniku.source_preparation.definitions import (
    register_source_preparation_adapters,
    register_source_preparation_validators,
    source_preparation_definitions,
)
from zniku.source_preparation.models import T1_PROMOTED

_REPO = Path(__file__).resolve().parents[1]


def _application(output: Path) -> ProjectServiceApplication:
    return ProjectServiceApplication(
        work_root=output / "unused-legacy-attempts",
        project_data_default=True,
        definition_catalog=(
            *built_in_media_definitions(),
            *source_preparation_definitions(),
            *built_in_overlap_definitions(),
            external_definition(),
        ),
        python_adapters={
            **media_python_adapters(),
            **register_source_preparation_adapters(),
            **overlap_python_adapters(),
        },
        validators={
            **media_validators(),
            **register_source_preparation_validators(),
            **overlap_validators(),
        },
        media_probe=runner_media_probe,
        artifact_quick_probe=media_artifact_quick_probe,
    )


def _metadata(artifact: Artifact) -> OverlapMetadata:
    return OverlapMetadata.model_validate(
        artifact.model_dump(mode="json")["media_info"][OVERLAP_NAMESPACE]
    )


def check() -> dict[str, Any]:
    """只核对本机工具和实际可调用注册，不创建媒体或目录。"""
    for name in ("ffmpeg", "ffprobe"):
        if shutil.which(name) is None:
            raise RuntimeError(f"E_PREPARED_SMOKE_TOOL: 缺少 {name}")
    adapters = {**register_source_preparation_adapters(), **overlap_python_adapters()}
    validators = {**register_source_preparation_validators(), **overlap_validators()}
    for definition in (*source_preparation_definitions(), *built_in_overlap_definitions()):
        executor = definition.executor
        if executor.kind == "python" and executor.adapter not in adapters:
            raise AssertionError("E_PREPARED_SMOKE_ADAPTER: definition 未有真实实现")
        assert definition.validator is not None and definition.validator.adapter in validators
    return {
        "status": "ready_for_synthetic_smoke",
        "profile": "zniku.prepared-source-overlap@0.3.4",
        "t1_promoted": T1_PROMOTED,
        "external_AI_verified": False,
        "media_written": False,
    }


def _submit(
    app: ProjectServiceApplication,
    detail: RunDetailEnvelope,
    waiting: NodeRun,
    ffmpeg: str,
) -> RunDetailEnvelope:
    """专属目录中的来件不会自动提交；生成后仍调用正式 readiness 和显式 Submit。"""
    handoff = waiting.external_handoff
    assert handoff is not None
    target = Path(handoff.output_targets[0].path)
    assert target.resolve().is_relative_to(Path(waiting.work_dir).resolve())
    before = app.inspect_run_detail(detail.run.run_id)
    assert not app.inspect_external_readiness(
        run_id=detail.run.run_id, node_run_id=waiting.node_run_id, probe=True
    ).ready_for_submit
    source = next(
        item
        for item in detail.artifacts
        if item.artifact_id in waiting.input_artifact_ids and item.kind == "VideoFile"
    )
    metadata = _metadata(source)
    rate = Fraction(metadata.frame_rate)
    if waiting.node_id.startswith("overlap.enhance."):
        arguments = [
            "-i",
            source.path,
            "-an",
            "-vf",
            "format=yuv422p10le,setsar=1/1",
        ]
    elif waiting.node_id.startswith("overlap.fi."):
        binding = metadata.context
        assert binding is not None
        rate *= 2
        arguments = [
            "-f",
            "lavfi",
            "-i",
            f"nullsrc=size=1920x1080:rate={rate}",
            "-vf",
            f"geq=lum='16+({binding.context_start_frame * 2}+N)*3':cb=128:cr=128,setsar=1/1",
            "-frames:v",
            str(binding.raw_fi_frame_count),
            "-an",
        ]
    else:
        raise AssertionError("E_PREPARED_SMOKE_STAGE: 非预期人工步骤")
    _tool(
        ffmpeg,
        [
            *arguments,
            "-c:v",
            "prores_ks",
            "-profile:v",
            "3",
            "-threads",
            "2",
            "-pix_fmt",
            "yuv422p10le",
            *_signal(),
            "-video_track_timescale",
            str(rate.numerator),
            str(target),
        ],
    )
    assert app.inspect_external_readiness(
        run_id=detail.run.run_id, node_run_id=waiting.node_run_id, probe=True
    ).ready_for_submit
    assert app.inspect_run_detail(detail.run.run_id) == before
    return _command(
        app,
        {
            "operation": "submit_external",
            "run_id": detail.run.run_id,
            "node_run_id": waiting.node_run_id,
            "handoff_id": handoff.handoff_id,
        },
    )


def run_smoke(
    output: Path,
    *,
    frame_rate: str = "30/1",
    _preparation_route: Literal["direct", "builtin", "external"] = "direct",
) -> dict[str, Any]:
    """真实生成十八帧三章及原音轨，完成准备 Run 和完整处理/reuse Run。

    私有路线参数仅供 pytest 复用同一全链：函数不提升策略权限，CLI 不提供该开关；
    T1 路线必须由测试在本进程显式模拟晋级，并在结果中披露，而产品默认仍关闭。
    """
    check()
    if frame_rate not in {"30/1", "30000/1001"}:
        raise ValueError("E_PREPARED_SMOKE_RATE: 不支持此合成 FPS")
    build = (_REPO / "build").resolve()
    output = output.resolve()
    if output == build or not output.is_relative_to(build) or output.exists():
        raise ValueError("E_PREPARED_SMOKE_OUTPUT: 必须是仓库 build 内尚不存在的专属子目录")
    output.mkdir(parents=True, exist_ok=False)
    started = perf_counter()
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None
    source = output / "synthetic-source.mkv"
    reference_fixture = (
        source if _preparation_route == "direct" else output / "synthetic-reference-template.mkv"
    )
    encoded = output / "synthetic-encoded.mkv"
    rate, count = Fraction(frame_rate), 18
    duration = float(count / rate)
    _tool(
        ffmpeg,
        [
            "-f",
            "lavfi",
            "-i",
            f"nullsrc=size=640x360:rate={frame_rate}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=48000:duration={duration:.12f}",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-vf",
            "geq=lum='16+N*6':cb=128:cr=128,setsar=1/1,"
            "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
            "-frames:v",
            str(count),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "18",
            "-threads",
            "2",
            "-pix_fmt",
            "yuv420p",
            "-x264-params",
            "colorprim=bt709:transfer=bt709:colormatrix=bt709",
            *_signal(),
            "-c:a",
            "aac",
            "-metadata:s:a:0",
            "language=jpn",
            "-metadata:s:a:0",
            "title=Synthetic original",
            "-disposition:a:0",
            "default",
            "-f",
            "matroska",
            str(encoded),
        ],
    )
    # 合成 fixture 从 ADTS 构造没有容器 priming 声明的 AAC 原件；不把这一测试制作步骤
    # 作为产品修复策略，更不会对用户媒体剥离 skip/edit-list。priming 拒绝另有专项测试。
    elementary_audio = output / "synthetic-audio.aac"
    _tool(
        ffmpeg,
        [
            "-i",
            str(encoded),
            "-map",
            "0:a:0",
            "-c:a",
            "copy",
            "-f",
            "adts",
            str(elementary_audio),
        ],
    )
    _tool(
        ffmpeg,
        [
            "-i",
            str(encoded),
            "-i",
            str(elementary_audio),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c",
            "copy",
            "-metadata:s:a:0",
            "language=jpn",
            "-metadata:s:a:0",
            "title=Synthetic original",
            "-disposition:a:0",
            "default",
            str(reference_fixture),
        ],
    )
    if _preparation_route != "direct":
        # 只对自己刚生成的 fixture 施加有限时钟偏差；原件视频不再满足 CFR，音频不改。
        # 外部来件使用先前的正常 fixture，内置路线必须实际调用 mkvmerge 生成独立候选。
        _tool(
            ffmpeg,
            [
                "-i",
                str(reference_fixture),
                "-map",
                "0",
                "-c",
                "copy",
                "-bsf:v",
                "setts=pts=PTS*1.01:dts=DTS*1.01",
                str(source),
            ],
        )
    original_facts = _fingerprint(source)
    path = output / "prepared-source.zniku"
    app = _application(output)
    create(
        app,
        {
            "contract_version": "0.3.4",
            "project_path": str(path),
            "project_id": "smoke.prepared-source",
            "project_name": "素材准备合成全链",
            "source_path": str(source),
        },
    )
    diagnosis = _command(app, {"operation": "run_all"})
    assert diagnosis.run.state is RunState.COMPLETED
    state = app.inspect()
    initial = view(
        app,
        {
            "contract_version": "0.3.4",
            "project_session_id": state.project_session_id,
            "run_id": diagnosis.run.run_id,
        },
    )
    assert initial.diagnosis_status == "completed"
    if _preparation_route != "direct":
        assert {item.code for item in initial.findings} == {"E_SOURCE_PREPARATION_CLOCK_NOT_CFR"}
    choose(
        app,
        {
            "contract_version": "0.3.4",
            "project_session_id": state.project_session_id,
            "expected_storage_revision": state.storage_revision,
            "run_id": diagnosis.run.run_id,
            "route": _preparation_route,
            "target_frame_rate": frame_rate,
        },
    )
    preparation = _command(app, {"operation": "run_all"})
    repair_submissions: list[str] = []
    if _preparation_route == "external":
        repair_waiting = _latest(preparation)["source-preparation-prepare"]
        assert repair_waiting.state is NodeRunState.WAITING_EXTERNAL
        handoff = repair_waiting.external_handoff
        assert handoff is not None
        before = app.inspect_run_detail(preparation.run.run_id)
        target = Path(handoff.output_targets[0].path)
        assert not target.exists() and target.resolve().is_relative_to(
            Path(repair_waiting.work_dir)
        )
        shutil.copyfile(reference_fixture, target)
        assert app.inspect_external_readiness(
            run_id=preparation.run.run_id, node_run_id=repair_waiting.node_run_id, probe=True
        ).ready_for_submit
        assert app.inspect_run_detail(preparation.run.run_id) == before
        preparation = _command(
            app,
            {
                "operation": "submit_external",
                "run_id": preparation.run.run_id,
                "node_run_id": repair_waiting.node_run_id,
                "handoff_id": handoff.handoff_id,
            },
        )
        repair_submissions.append(repair_waiting.node_id)
    assert preparation.run.state is RunState.COMPLETED
    reference = _artifact(preparation, "source-preparation-admission")
    original = _artifact(preparation, "source-preparation-source", "media")
    assert original.path == str(source)
    if _preparation_route == "direct":
        assert reference.path == original.path
    else:
        assert reference.path != original.path
        assert reference.artifact_id != original.artifact_id
    state = app.inspect()
    full(
        app,
        {
            "contract_version": "0.3.4",
            "project_session_id": state.project_session_id,
            "expected_storage_revision": state.storage_revision,
            "preparation_run_id": preparation.run.run_id,
            "processing": {
                "mr": {"mode": "off"},
                "settings": {
                    "chapter_selector": {"mode": "exact_frames", "frames": [1, 3]},
                    "leaf_max_minutes": 5,
                },
                "enhancement": {"model_name": "synthetic-not-AI", "actual_scale_factor": 1},
                "program_encode": {"encoder": "cpu"},
            },
            "publication": {
                "output_root": str(output),
                "title": "Synthetic prepared",
                "year": "2026",
                "overwrite": False,
                "layout": "title_subdirectory",
            },
        },
        expand=True,
    )
    production = _command(app, {"operation": "run_all"})
    submitted: list[str] = []
    for _ in range(6):
        waiting = next(
            (
                node
                for node in _latest(production).values()
                if node.state is NodeRunState.WAITING_EXTERNAL
            ),
            None,
        )
        assert waiting is not None
        production = _submit(app, production, waiting, ffmpeg)
        submitted.append(waiting.node_id)
    assert production.run.state is RunState.COMPLETED
    final = _artifact(production, "overlap.final", "media")
    final_header = probe_header(final.path, count_frames=True)
    assert final_header.video.frame_count == 36 and len(final_header.audios) == 1
    binding = _metadata(final).source
    assert binding.original_media_artifact_id == original.artifact_id
    assert binding.reference_video_artifact_id == reference.artifact_id
    assert (
        binding.effective_video_artifact_id == reference.artifact_id
        and binding.mr_role == "reference"
    )
    verify_audio_content(source, Path(final.path))
    assert _fingerprint(source) == original_facts
    assert ProjectStore.open(path).load().project.graph == production.run.graph_snapshot
    reused = _command(app, {"operation": "run_all"})
    assert reused.run.state is RunState.COMPLETED
    assert all(node.reused_from_result_id is not None for node in _latest(reused).values())
    report: dict[str, Any] = {
        "evidence_kind": "synthetic_preparation_Graph_Runtime_Submit",
        "state": "completed",
        "profile": "zniku.prepared-source-overlap@0.3.4",
        "external_AI_verified": False,
        "GUI_verified": False,
        "real_repair_verified": False,
        "t1_promoted": T1_PROMOTED,
        "preparation_route": _preparation_route,
        "test_only_t1_promotion_override": preparation_models.T1_PROMOTED != T1_PROMOTED,
        "source_frames": count,
        "source_rate": frame_rate,
        "chapter_frames": [1, 2, 15],
        "final_frames": 36,
        "audio_tracks": 1,
        "actual_audio_samples_unchanged": True,
        "normal_source_not_copied": _preparation_route == "direct",
        "reference_is_distinct_from_original": reference.path != original.path,
        "original_video_clock_rejected": _preparation_route != "direct",
        "source_unchanged": True,
        "manual_submissions": submitted,
        "repair_manual_submissions": repair_submissions,
        "project_path": str(path),
        "final_path": final.path,
        "diagnosis_run_id": diagnosis.run.run_id,
        "preparation_run_id": preparation.run.run_id,
        "production_run_id": production.run.run_id,
        "reused_nodes": len(_latest(reused)),
        "seconds": perf_counter() - started,
    }
    with (output / "report.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只核对工具和真实注册，不写媒体")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--frame-rate", choices=("30/1", "30000/1001"), default="30/1")
    arguments = parser.parse_args()
    if not arguments.check and arguments.output is None:
        parser.error("标准运行需要 --output build/<新的专属子目录>")
    report = (
        check() if arguments.check else run_smoke(arguments.output, frame_rate=arguments.frame_rate)
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
