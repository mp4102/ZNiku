"""严格直接绑定与 metadata 反例；只在临时目录写合成准入 JSON。"""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from test_prepared_color_observation import interpretation
from test_source_preparation_kernel import media as media
from zniku.avenhance_v27.probe import Av27MediaError
from zniku.chapter_overlap import (
    AdmittedTimeline,
    AverageChapterSelector,
    ChapterSettings,
    plan_chapters_and_leaves,
)
from zniku.graph import NodeInstance
from zniku.media.probe import MediaNodeError
from zniku.prepared_color import validators
from zniku.prepared_color.definitions import external_definition
from zniku.prepared_color.node_contracts import SourceExpectation, preflight
from zniku.runtime import NodeExecutionRequest, NodeValidatorContext, RunnerInput, ValidatedOutput
from zniku.source_color.models import NAMESPACE, SourceGate
from zniku.source_preparation.models import AudioBinding, SourceGeometry


def binding(media: Path, tmp_path: Path) -> tuple[tuple[RunnerInput, ...], dict[str, Any]]:
    origin, diagnosis, gate_id, video_id = (str(uuid4()) for _ in range(4))
    color = interpretation(media)
    gate = SourceGate(
        original_media_artifact_id=origin,
        reference_media_artifact_id=origin,
        diagnosis_artifact_id=diagnosis,
        source_frame_count=30,
        frame_rate="30/1",
        original_video_start="0/1",
        reference_start="0/1",
        geometry=SourceGeometry(width=64, height=48),
        **color.model_dump(),
        preparation_strategy="original",
        audio_policy="none",
        audio_bindings=(AudioBinding(artifact_id=origin, ordinal=0, tracks=()),),
    )
    path = tmp_path / "gate.json"
    path.write_text(gate.model_dump_json(), encoding="utf-8")
    gate_input = RunnerInput(
        "gate",
        gate_id,
        "DataFile",
        path,
        producer_port_id="gate",
        media_info={NAMESPACE: {"role": "admission", "admission": gate.model_dump(mode="json")}},
    )
    video = RunnerInput(
        "videos",
        video_id,
        "VideoFile",
        media,
        producer_port_id="video",
        ordinal=0,
        media_info={NAMESPACE: {"role": "reference", "admission": gate.model_dump(mode="json")}},
    )
    source = SourceExpectation(
        reference_video_artifact_id=video_id,
        original_media_artifact_id=origin,
        reference_media_artifact_id=origin,
        diagnosis_artifact_id=diagnosis,
        audio_source_artifact_ids=(origin,),
        admission_artifact_id=gate_id,
        frame_count=30,
        frame_rate="30/1",
    )
    plan = plan_chapters_and_leaves(
        AdmittedTimeline(artifact_id=video_id, frame_count=30, frame_rate="30/1"),
        ChapterSettings(chapter_selector=AverageChapterSelector(count=3), leaf_max_minutes=5),
    )
    return (video, gate_input), {
        "source": source.model_dump(mode="json"),
        "plan": plan.model_dump(mode="json"),
    }


def test_split_origin_interpretation_and_future_ids_not_rewritten(
    media: Path, tmp_path: Path
) -> None:
    inputs, params = binding(media, tmp_path)
    result = preflight("split", inputs, params)
    assert len(result.outputs) == 3
    assert result.source.reference_video_artifact_id == inputs[0].artifact_id
    for output in result.outputs:
        dumped = output.metadata.model_dump(mode="json")
        assert dumped["producer_version"] == "0.3.4-color.1"
        assert "signal" not in dumped and dumped["observed_signal"] is None
        assert dumped["interpretation"]["working_signal"]["color_space"] == "bt709"


@pytest.mark.parametrize(
    "change", ["foreign_gate", "old_namespace", "new_reference", "false_interpretation"]
)
def test_split_rejects_old_foreign_or_tampered_binding(
    media: Path, tmp_path: Path, change: str
) -> None:
    inputs, params = binding(media, tmp_path)
    video, gate = inputs
    if change == "foreign_gate":
        gate = replace(gate, artifact_id=str(uuid4()))
    elif change == "old_namespace":
        video = replace(video, media_info={"zniku.source.prepared": video.media_info[NAMESPACE]})
    elif change == "new_reference":
        video = replace(video, artifact_id=str(uuid4()))
    else:
        namespace = json.loads(json.dumps(video.media_info[NAMESPACE]))
        namespace["admission"]["interpretation_policy"] = "operator_confirmed_bt709_limited_left"
        video = replace(video, media_info={NAMESPACE: namespace})
    with pytest.raises((Av27MediaError, MediaNodeError, ValueError)):
        preflight("split", (video, gate), params)


@pytest.mark.parametrize("mismatch", [None, "sample_aspect_ratio", "pixel_format"])
def test_manual_mr_checks_full_frame_sar_and_pixel_format(
    media: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str | None,
) -> None:
    from zniku.prepared_color.signal import observe_output

    inputs, params = binding(media, tmp_path)
    video, gate = inputs
    definition = external_definition("mkv")
    parameters = {
        "source": params["source"],
        "declared_container": "mkv",
        "model_name": "synthetic",
        "operator_frame_order_confirmed": True,
    }
    node = NodeInstance(
        node_id="mr",
        type_id=definition.type_id,
        definition_version=definition.version,
        parameters=parameters,
    )
    target = tmp_path / "restoration.mkv"
    shutil.copyfile(media, target)
    stat = target.stat()
    observed = observe_output(target)
    if mismatch is not None:
        variants = (
            observed.frames.variants[0].model_copy(
                update={mismatch: "2:1" if mismatch == "sample_aspect_ratio" else "yuv444p"}
            ),
        )
        observed = observed.model_copy(
            update={"frames": observed.frames.model_copy(update={"variants": variants})}
        )
    monkeypatch.setattr(validators, "observe_output", lambda path: observed)
    context = NodeValidatorContext(
        NodeExecutionRequest(
            str(uuid4()), 1, definition, node, (replace(video, port_id="video", ordinal=None), gate)
        ),
        tmp_path,
        (ValidatedOutput("video", "VideoFile", target, stat.st_size, stat.st_mtime_ns, {}),),
    )
    result = validators.validate_external(context)
    assert result.passed is (mismatch is None), result.message
    if mismatch is not None:
        assert result.summary["code"] == "E_PREPARED_SOURCE_COLOR_EXTERNAL"
