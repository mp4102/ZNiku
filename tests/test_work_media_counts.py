"""普通外部来件缺 header 帧数时仍只扫描一次；旧 exact 的计数默认不变。"""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from test_prepared_source_node_contracts import pipeline, validator_context
from test_source_preparation_kernel import _tool
from zniku.avenhance_v27 import validators as legacy
from zniku.avenhance_v27.probe import probe_header
from zniku.prepared_source import validators as shared_validators
from zniku.prepared_source import work_execution, work_timeline
from zniku.prepared_source.node_contracts import OVERLAP_NAMESPACE as OLD_NAMESPACE
from zniku.prepared_source.node_contracts import OutputContract, _plain
from zniku.prepared_source.work_contracts import OVERLAP_NAMESPACE, WorkOverlapMetadata
from zniku.prepared_source.work_definitions import built_in_overlap_definitions
from zniku.source_preparation.inspection import _scan


@pytest.mark.parametrize("role", ["enhancement", "fi"])
@pytest.mark.parametrize("header_count", [None, "present"])
def test_external_necessary_scan_does_not_fall_back_to_old_counter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    role: str,
    header_count: str | None,
) -> None:
    step = next(s for s in pipeline(tmp_path, n=9, chapters=3, rate="30/1") if s.role == role)
    context, headers = validator_context(step, tmp_path)
    definition = next(
        d for d in built_in_overlap_definitions() if d.type_id == context.request.definition.type_id
    )
    inputs = tuple(
        replace(
            item,
            media_info={
                OVERLAP_NAMESPACE: WorkOverlapMetadata.model_validate(
                    {
                        **_plain(item.media_info[OLD_NAMESPACE]),  # type: ignore[dict-item]
                        "producer_version": "0.3.4-work.1",
                        "observation_scope": "ffprobe-stream",
                    }
                ).model_dump(mode="json")
            },
        )
        for item in context.request.inputs
    )
    context = replace(
        context,
        request=replace(
            context.request,
            definition=definition,
            node=context.request.node.model_copy(update={"definition_version": definition.version}),
            inputs=inputs,
        ),
    )
    output = context.outputs[0]
    metadata = step.contract.outputs[0].metadata
    (tmp_path / "logs").mkdir()
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=s=1920x1080:r={metadata.frame_rate}",
            "-frames:v",
            str(metadata.frame_count),
            "-vf",
            "format=yuv422p10le,setsar=1/1",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "3",
            "-threads",
            "2",
            "-video_track_timescale",
            str(metadata.frame_rate.split("/")[0]),
            str(output.path),
        ]
    )
    original_header = headers[output.path]
    header = replace(
        original_header,
        videos=(
            replace(
                original_header.video,
                frame_count=None if header_count is None else metadata.frame_count,
            ),
        ),
    )
    monkeypatch.setattr(shared_validators, "probe_header", lambda path: header)
    scans: list[tuple[Any, ...]] = []

    def scan(*args: Any, **kwargs: Any) -> bool:
        scans.append(args)
        return _scan(*args, **kwargs)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("普通必要扫描结束后不能再次调用旧计数器")

    monkeypatch.setattr(work_timeline, "_scan", scan)
    monkeypatch.setattr(legacy, "_external_frame_count", forbidden)
    result = work_execution._validate(context, role)
    assert result.passed, result.message
    assert len(scans) == 1 and scans[0][3] is True


@pytest.mark.parametrize("stale_header", [False, True])
def test_split_mkv_nanosecond_header_policy_is_narrow_and_wired(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stale_header: bool,
) -> None:
    """实际 FFV1 MKV 的纳秒表示可以通过，伪造继承旧 30fps 声明必须失败。"""
    step = next(
        s for s in pipeline(tmp_path, n=3, chapters=1, rate="60000/1001") if s.role == "split"
    )
    context, _ = validator_context(step, tmp_path)
    definition = next(
        d for d in built_in_overlap_definitions() if d.type_id == context.request.definition.type_id
    )
    contract = replace(
        step.contract,
        outputs=tuple(
            OutputContract(
                output.port_id,
                WorkOverlapMetadata.model_validate(
                    {**output.metadata.model_dump(), "producer_version": "0.3.4-work.1"}
                ),
            )
            for output in step.contract.outputs
        ),
    )
    context = replace(
        context,
        request=replace(
            context.request,
            definition=definition,
            node=context.request.node.model_copy(update={"definition_version": definition.version}),
        ),
    )
    path = context.outputs[0].path
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=1920x1080:r=60000/1001",
            "-frames:v",
            "3",
            "-vf",
            "format=yuv420p10le,setsar=1/1,"
            "setparams=field_mode=prog:range=limited:color_primaries=bt709:"
            "color_trc=bt709:colorspace=bt709",
            "-c:v",
            "ffv1",
            "-pix_fmt",
            "yuv420p10le",
            "-threads",
            "2",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-colorspace",
            "bt709",
            "-color_range",
            "tv",
            "-chroma_sample_location",
            "left",
            "-field_order",
            "progressive",
            str(path),
        ]
    )
    header = probe_header(path)
    assert header.video.frame_rate == Fraction(19001, 317)
    if stale_header:
        header = replace(
            header,
            videos=(
                replace(
                    header.video,
                    frame_rate=Fraction(30),
                    avg_frame_rate=Fraction(30),
                    r_frame_rate=Fraction(30),
                ),
            ),
        )
    # 本测试聚焦新 hook 的真实媒体头行为；直接输入绑定由已有 kernel 专项覆盖。
    monkeypatch.setattr(work_execution, "preflight", lambda *args: contract)
    monkeypatch.setattr(shared_validators, "probe_header", lambda _: header)
    result = work_execution.validate_atomic_split(context)
    assert result.passed is not stale_header, result.message
    if stale_header:
        assert result.summary["code"] == "E_PREPARED_SOURCE_WORK_SPLIT_HEADER"
