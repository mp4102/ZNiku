"""执行六类工作源绑定 0.3.4 自动节点，不改变旧 AV27、Core 或人工节点含义。

每次先重算直接输入合同，再在独立 attempt 执行。ProRes context/crop 仅复制包，
Program 只追加一个全局尾帧。任何缺失、乱序、空间不足、取消或媒体错误均整节点失败，
保留源/raw/部分文件且不返回部分 Artifact；最终登记仍由 Runtime 的独立 validator 决定。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import Any

from zniku.avenhance_v27.adapters import (
    _audio_metadata_options,
    _promote_attempt_file,
    _read_segment_list,
)
from zniku.avenhance_v27.probe import probe_header
from zniku.chapter_overlap.media_io import (
    OverlapMediaError,
    capacity_check,
    concat_prores,
    copy_prores_range,
    encode_program,
    output_path,
    run_ffmpeg,
    source_path,
)
from zniku.runtime import (
    FrameRange,
    ProducedOutput,
    PythonAdapterContext,
    PythonAdapterResult,
    RunnerInput,
)
from zniku.runtime.progress import ProgressReporter, ProgressUnit
from zniku.source_aligned.timeline import offset_decimal, probe_cfr, require_supported_audio_origins
from zniku.source_preparation.progress import progress_log, stage

from .node_contracts import (
    NodeContract,
    ProgramParameters,
    SplitParameters,
    effective_contract,
    preflight,
)


def _targets(context: PythonAdapterContext, contract: NodeContract) -> dict[str, Path]:
    expected = {item.port_id for item in contract.outputs}
    result = {item.port_id: item.path for item in context.outputs}
    if expected != set(result) or len(result) != len(context.outputs):
        raise OverlapMediaError("E_OVERLAP_OUTPUT_PORTS", "输出必须完整对应声明端口")
    resolved = {port: output_path(context, path) for port, path in result.items()}
    if len(set(resolved.values())) != len(resolved):
        raise OverlapMediaError("E_OVERLAP_OUTPUT_PATH", "多个输出不得共享同一文件")
    return resolved


def _inputs(context: PythonAdapterContext, port: str) -> tuple[RunnerInput, ...]:
    # 顺序已由 preflight 核对；这里不排序掩盖非法输入，也不查隐藏 Artifact 索引。
    return tuple(item for item in context.inputs if item.port_id == port)


def _result(
    port: str, target: Path, count: int, capacity: dict[str, object]
) -> PythonAdapterResult:
    return PythonAdapterResult(
        outputs=(ProducedOutput(port, target),),
        producer_metadata={port: {"output_frames": count}},
        validation_summary={"capacity": capacity, "original_and_raw_retained": True},
    )


def atomic_split(context: PythonAdapterContext) -> PythonAdapterResult:
    """单源连续 FFV1 分叶；每叶快速 packet 计数作为独立实际 producer 事实。

    第二轮只 stream-copy 到 null，不重新解码整片。总进度为编码 N 加逐叶包计数 N；
    取消或任一叶不符时全部不登记，尚未成功的 stage 文件保留供诊断。
    """

    contract = preflight("split", context.inputs, context.node.parameters)
    return atomic_split_media(context, contract, _split_filter)


def atomic_split_media(
    context: PythonAdapterContext,
    contract: NodeContract,
    split_filter: Callable[[RunnerInput, NodeContract], str],
    *,
    reference_validator: Callable[..., object] | None = probe_cfr,
) -> PythonAdapterResult:
    """执行已由调用包重验的局部切分合同；不读取或转换任何 namespace。

    旧入口默认重验 CFR；普通工作源入口已持有完成观察，可只复用其当前绑定。
    该 hook 仅供受信 Python adapter 使用，不是 Graph 参数或关闭输出计数的开关。
    """
    assert isinstance(contract.params, SplitParameters)
    targets = _targets(context, contract)
    source = _inputs(context, "videos")[0]
    path = source_path(source.path)
    frame_count = contract.source.frame_count
    if reference_validator is not None and (
        source.artifact_id == contract.source.reference_video_artifact_id
    ):
        reference_validator(
            path,
            count=frame_count,
            rate=Fraction(contract.source.frame_rate),
            progress=context.progress,
        )
    duration = Fraction(frame_count) / Fraction(contract.source.frame_rate)
    estimate = int(duration * 32 * 1024 * 1024)
    capacity = capacity_check(
        context,
        output_bytes=estimate,
        input_bytes=path.stat().st_size,
        input_frames=frame_count,
        output_frames=frame_count,
    )
    stage = context.work_dir / "split-stage"
    stage.mkdir(exist_ok=False)
    segment_list = stage / "segments.csv"
    leaves = tuple(leaf for chapter in contract.params.plan.chapters for leaf in chapter.leaves)
    boundaries = ",".join(str(leaf.end_frame) for leaf in leaves)
    run_ffmpeg(
        context,
        [
            "-xerror",
            "-fflags",
            "+genpts",
            "-i",
            str(path),
            "-filter_complex",
            split_filter(source, contract),
            "-map",
            "[vsegment]",
            "-an",
            "-sn",
            "-dn",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-c:v",
            "ffv1",
            "-level:v",
            "3",
            "-coder:v",
            "1",
            "-context:v",
            "1",
            "-g:v",
            "1",
            "-slicecrc:v",
            "1",
            "-slices:v",
            "16",
            "-color_range:v",
            "tv",
            "-colorspace:v",
            "bt709",
            "-color_trc:v",
            "bt709",
            "-color_primaries:v",
            "bt709",
            "-chroma_sample_location:v",
            "left",
            "-field_order:v",
            "progressive",
            "-fps_mode",
            "passthrough",
            "-f",
            "segment",
            "-segment_format",
            "matroska",
            "-reference_stream",
            "v:0",
            "-segment_start_number",
            "1",
            "-reset_timestamps",
            "1",
            "-individual_header_trailer",
            "1",
            "-segment_list",
            str(segment_list),
            "-segment_list_type",
            "csv",
            "-segment_frames",
            boundaries,
            str(stage / "part-%06d.mkv"),
        ],
        expected_frames=frame_count,
        progress_total=2 * frame_count,
    )
    partials = _read_segment_list(segment_list, stage, len(leaves))
    produced: list[ProducedOutput] = []
    facts: dict[str, dict[str, object]] = {}
    offset = frame_count
    for partial, leaf in zip(partials, leaves, strict=True):
        actual = run_ffmpeg(
            context,
            [
                "-i",
                str(partial),
                "-map",
                "0:v:0",
                "-an",
                "-sn",
                "-dn",
                "-c:v",
                "copy",
                "-f",
                "null",
                "-",
            ],
            expected_frames=leaf.frame_count,
            progress_total=2 * frame_count,
            progress_offset=offset,
        )
        offset += actual
        target = targets[leaf.leaf_id]
        _promote_attempt_file(partial, target, context.work_dir)
        produced.append(
            ProducedOutput(
                leaf.leaf_id,
                target,
                frame_range=FrameRange(
                    start_frame=leaf.start_frame,
                    end_frame=leaf.end_frame,
                ),
            )
        )
        facts[leaf.leaf_id] = {"output_frames": actual}
    return PythonAdapterResult(
        outputs=tuple(produced),
        producer_metadata=facts,
        validation_summary={"capacity": capacity, "actual_leaf_packet_counts": True},
    )


def merge_video(context: PythonAdapterContext) -> PythonAdapterResult:
    """完整有序叶合并为无重编码增强章；不是旧 AV27 Merge producer。"""

    contract = preflight("merge", context.inputs, context.node.parameters)
    return merge_video_media(context, contract)


def merge_video_media(context: PythonAdapterContext, contract: NodeContract) -> PythonAdapterResult:
    """共享纯媒体包合并；调用者负责自己的 exact 身份及直接绑定。"""
    target = _targets(context, contract)["video"]
    metadata = contract.outputs[0].metadata
    paths = tuple(source_path(item.path) for item in _inputs(context, "videos"))
    size = sum(path.stat().st_size for path in paths)
    rate = Fraction(metadata.frame_rate)
    staging = sum(
        path.stat().st_size
        for path in paths
        if probe_header(path).video.time_base != Fraction(1, rate.numerator)
    )
    capacity = capacity_check(
        context,
        output_bytes=size,
        staging_bytes=staging,
        input_bytes=size,
        input_frames=metadata.frame_count,
        output_frames=metadata.frame_count,
    )
    actual = concat_prores(
        context,
        paths,
        target,
        rate,
        metadata.frame_count,
        source_frame_counts=tuple(item.frame_count for item in contract.input_metadata),
    )
    return _result("video", target, actual, capacity)


def fi_context(context: PythonAdapterContext) -> PythonAdapterResult:
    """逐个读取真实显式邻章交集并复制包，再按源顺序合并为带上下文 MOV。"""

    contract = preflight("context", context.inputs, context.node.parameters)
    return fi_context_media(context, contract)


def fi_context_media(context: PythonAdapterContext, contract: NodeContract) -> PythonAdapterResult:
    """执行已重验的上下文交集，不赋予旧合同语义。"""
    target = _targets(context, contract)["video"]
    metadata = contract.outputs[0].metadata
    binding = metadata.context
    assert binding is not None
    inputs = _inputs(context, "chapters")
    paths = {item.artifact_id: source_path(item.path) for item in inputs}
    estimate = sum(
        (
            paths[part.artifact_id].stat().st_size * (part.end_frame - part.start_frame)
            + part.chapter.end_frame
            - part.chapter.start_frame
            - 1
        )
        // (part.chapter.end_frame - part.chapter.start_frame)
        for part in binding.parts
    )
    capacity = capacity_check(
        context,
        output_bytes=estimate,
        staging_bytes=estimate,
        input_bytes=sum(path.stat().st_size for path in paths.values()),
        input_frames=sum(item.frame_count for item in contract.input_metadata),
        output_frames=binding.input_frame_count,
    )
    rate = Fraction(metadata.frame_rate)
    pieces: list[Path] = []
    completed = 0
    total = 2 * binding.input_frame_count
    for ordinal, part in enumerate(binding.parts):
        piece = context.work_dir / "context-parts" / f"part-{ordinal:04d}.mov"
        actual = copy_prores_range(
            context,
            paths[part.artifact_id],
            piece,
            part.start_frame - part.chapter.start_frame,
            part.end_frame - part.chapter.start_frame,
            rate,
            progress_total=total,
            progress_offset=completed,
        )
        completed += actual
        pieces.append(piece)
    actual = concat_prores(
        context,
        pieces,
        target,
        rate,
        binding.input_frame_count,
        progress_total=total,
        progress_offset=completed,
    )
    return _result("video", target, actual, capacity)


def fi_crop(context: PythonAdapterContext) -> PythonAdapterResult:
    """从已验证 raw 中复制精确 half-frame 责任区间；不覆盖 raw、不加章尾帧。"""

    contract = preflight("crop", context.inputs, context.node.parameters)
    return fi_crop_media(context, contract)


def fi_crop_media(context: PythonAdapterContext, contract: NodeContract) -> PythonAdapterResult:
    """共享裁边包复制，正式身份与解释由调用方自己的 validator 登记。"""
    target = _targets(context, contract)["video"]
    metadata = contract.outputs[0].metadata
    binding = metadata.context
    assert binding is not None
    source = source_path(_inputs(context, "video")[0].path)
    size = source.stat().st_size
    estimate = (
        size * binding.cropped_frame_count + binding.raw_fi_frame_count - 1
    ) // binding.raw_fi_frame_count
    staging = (
        estimate
        if probe_header(source).video.time_base
        != Fraction(1, Fraction(metadata.frame_rate).numerator)
        else 0
    )
    capacity = capacity_check(
        context,
        output_bytes=estimate,
        staging_bytes=staging,
        input_bytes=size,
        input_frames=binding.raw_fi_frame_count,
        output_frames=binding.cropped_frame_count,
    )
    actual = copy_prores_range(
        context,
        source,
        target,
        binding.crop_start_frame,
        binding.crop_end_frame,
        Fraction(metadata.frame_rate),
        source_frame_count=binding.raw_fi_frame_count,
        allow_equivalent_rate=True,
    )
    return _result("video", target, actual, capacity)


def program_encode(context: PythonAdapterContext) -> PythonAdapterResult:
    """单连续 Main10 编码，只在完整有序 2N-1 序列的全局末尾追加一帧。"""

    contract = preflight("program", context.inputs, context.node.parameters)
    return program_encode_media(context, contract)


def program_encode_media(
    context: PythonAdapterContext, contract: NodeContract
) -> PythonAdapterResult:
    """按已明确的固定工作解释连续编码，不自行决定 unknown 的含义。"""
    assert isinstance(contract.params, ProgramParameters)
    target = _targets(context, contract)["video"]
    metadata = contract.outputs[0].metadata
    paths = tuple(source_path(item.path) for item in _inputs(context, "chapters"))
    size = sum(path.stat().st_size for path in paths)
    capacity = capacity_check(
        context,
        output_bytes=size,
        input_bytes=size,
        input_frames=metadata.frame_count - 1,
        output_frames=metadata.frame_count,
    )
    actual = encode_program(
        context,
        paths,
        target,
        Fraction(metadata.frame_rate),
        metadata.frame_count,
        contract.params.encoder,
    )
    return _result("video", target, actual, capacity)


class _FinalProgress:
    """长校验未完成时只传取消心跳；各阶段实测详情写普通日志，不提前声称 100%。"""

    def __init__(self, reporter: ProgressReporter) -> None:
        self.reporter = reporter

    def report(
        self,
        fraction: float,
        *,
        current: int | None = None,
        total: int | None = None,
        unit: ProgressUnit | None = None,
    ) -> None:
        self.reporter.report(0.0)


def final_mux(context: PythonAdapterContext) -> PythonAdapterResult:
    """封装和完整比较都保留可取消心跳；仅实际全部完成后上报完成值。"""
    active = replace(
        context, progress=None if context.progress is None else _FinalProgress(context.progress)
    )
    if active.progress is not None:
        active.progress.report(0.0)
    with progress_log(context.stdout_log_path):
        result = _final_mux(active)
    if context.progress is not None:
        context.progress.report(1.0)
    return result


def _final_mux(context: PythonAdapterContext) -> PythonAdapterResult:
    """由 gate 的显式音频映射复制所选音轨；工作参考重置 PTS 不改变原音画关系。"""

    contract = preflight("final", context.inputs, context.node.parameters)
    from zniku.source_preparation.contracts import read_gate, validate_audio_sources

    return final_mux_media(context, contract, read_gate, validate_audio_sources)


def final_mux_media(
    context: PythonAdapterContext,
    contract: NodeContract,
    gate_reader: Callable[[RunnerInput], Any],
    audio_validator: Callable[..., None],
    *,
    audio_origin: Callable[[Any], Fraction] | None = None,
    audio_integrity: Callable[..., None] | None = None,
) -> PythonAdapterResult:
    """从调用方严格读取的直接 gate 封装所选音轨；不转换旧/新 gate。

    旧入口保持原件起点和完整样本比较。普通工作源可声明参考音频坐标与
    包复制完整度检查，不冒充逐样本恒等；所有 hook 都来自受信 adapter。
    """
    target = _targets(context, contract)["media"]
    video = source_path(_inputs(context, "video")[0].path)
    originals = _inputs(context, "sources")
    gate = gate_reader(_inputs(context, "gate")[0])
    with stage("final_audio_source_check"):
        audio_validator(gate, originals, progress=context.progress)
    sources = tuple(source_path(item.path) for item in originals)
    count = contract.outputs[0].metadata.frame_count
    size = video.stat().st_size + sum(path.stat().st_size for path in sources)
    capacity = capacity_check(
        context,
        output_bytes=size,
        input_bytes=size,
        input_frames=count,
        output_frames=count,
    )
    arguments = ["-copyts", "-i", str(video)]
    mapping = ["-map", "0:v:0"]
    signatures: list[dict[str, object]] = []
    origin = Fraction(gate.original_video_start) if audio_origin is None else audio_origin(gate)
    for index, (_original, source, binding) in enumerate(
        zip(originals, sources, gate.audio_bindings, strict=True), 1
    ):
        require_supported_audio_origins(source)
        arguments.extend(["-itsoffset", offset_decimal(-origin), "-i", str(source)])
        header = probe_header(source)
        by_index = {track.index: track for track in header.audios}
        for track in binding.tracks:
            mapping.extend(["-map", f"{index}:{track.stream_index}"])
            signatures.append(dict(by_index[track.stream_index].signature()))
    with stage("final_mux"):
        actual = run_ffmpeg(
            context,
            [
                *arguments,
                *mapping,
                "-map_metadata",
                "1",
                "-map_chapters",
                "-1",
                *_audio_metadata_options(tuple(signatures)),
                "-c:v",
                "copy",
                "-c:a",
                "copy",
                "-avoid_negative_ts",
                "disabled",
                "-f",
                "matroska",
                str(target),
            ],
            expected_frames=count,
        )
    from .audio import verify_audio_content, verify_video_span

    verify_video_span(
        target,
        count=count,
        rate=Fraction(contract.outputs[0].metadata.frame_rate),
        progress=context.progress,
    )
    compare_audio = verify_audio_content if audio_integrity is None else audio_integrity
    compare_audio(sources[0], target, progress=context.progress)
    return _result("media", target, actual, capacity)


def _split_filter(source: RunnerInput, contract: NodeContract) -> str:
    """按当前显式绑定读取几何，不伪造旧 namespace 来调用旧节点执行器。"""
    media = effective_contract(source, contract.source.expectation())
    width, height = media.geometry
    if width * 9 != height * 16:
        raise OverlapMediaError("E_PREPARED_SOURCE_SPLIT_GEOMETRY", "输入必须为精确 16:9")
    scale = (
        ""
        if (width, height) == (1920, 1080)
        else ("zscale=w=1920:h=1080:filter=spline36:chromalin=left:chromal=left,")
    )
    rate = Fraction(contract.source.frame_rate)
    return (
        "[0:v:0]setparams=range=limited:color_primaries=bt709:"
        "color_trc=bt709:colorspace=bt709,"
        f"{scale}format=pix_fmts=yuv420p10le,setsar=1/1,"
        f"settb=expr={rate.denominator}/{rate.numerator},setpts=N[vsegment]"
    )
