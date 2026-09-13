"""执行六类新重叠 FI 自动节点，不改变旧 AV27、Core 或人工节点含义。

每次先重算直接输入合同，再在独立 attempt 执行。ProRes context/crop 仅复制包，
Program 只追加一个全局尾帧。任何缺失、乱序、空间不足、取消或媒体错误均整节点失败，
保留源/raw/部分文件且不返回部分 Artifact；最终登记仍由 Runtime 的独立 validator 决定。
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from zniku.avenhance_v27.adapters import (
    _audio_metadata_options,
    _promote_attempt_file,
    _read_segment_list,
    _split_filter,
)
from zniku.avenhance_v27.probe import audio_signatures_from_metadata, probe_header
from zniku.runtime import (
    FrameRange,
    ProducedOutput,
    PythonAdapterContext,
    PythonAdapterResult,
    RunnerInput,
)

from .media_io import (
    OverlapMediaError,
    capacity_check,
    concat_prores,
    copy_prores_range,
    encode_program,
    output_path,
    run_ffmpeg,
    source_path,
)
from .node_contracts import NodeContract, ProgramParameters, SplitParameters, preflight


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
    assert isinstance(contract.params, SplitParameters)
    targets = _targets(context, contract)
    source = _inputs(context, "videos")[0]
    path = source_path(source.path)
    frame_count = contract.source.frame_count
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
            _split_filter(source),
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


def final_mux(context: PythonAdapterContext) -> PythonAdapterResult:
    """复制连续 Program 和直接绑定原 Source 的音轨，不重新编码视频或音频。"""

    contract = preflight("final", context.inputs, context.node.parameters)
    target = _targets(context, contract)["media"]
    video = source_path(_inputs(context, "video")[0].path)
    original = _inputs(context, "sources")[0]
    source = source_path(original.path)
    count = contract.outputs[0].metadata.frame_count
    size = video.stat().st_size + source.stat().st_size
    capacity = capacity_check(
        context,
        output_bytes=size,
        input_bytes=size,
        input_frames=count,
        output_frames=count,
    )
    actual = run_ffmpeg(
        context,
        [
            "-i",
            str(video),
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "1:a?",
            "-map_metadata",
            "1",
            "-map_chapters",
            "-1",
            *_audio_metadata_options(audio_signatures_from_metadata(original.media_info)),
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
    return _result("media", target, actual, capacity)
