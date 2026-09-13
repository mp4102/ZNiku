"""验证重叠 FI 实验的三套坐标、来源交集和半帧责任，不处理媒体或证明模型能力。

完整严格模型覆盖输入、篡改及多邻章；60,300 组穷举走同一纯整数几何内核，避免创建数百万
Pydantic 对象。独立 oracle 核对每个输出时刻归属，不仅检查最终数量。
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
from pydantic import ValidationError

import zniku.chapter_overlap.context as context
from zniku.chapter_overlap import (
    AdmittedTimeline,
    ChapterLeafPlan,
    ChapterSettings,
    plan_chapters_and_leaves,
)
from zniku.chapter_overlap.context import (
    ContextPlanningError,
    ExperimentalContextPlan,
    ExperimentalContextSettings,
    plan_experimental_contexts,
)


def _chapters(frames: int, count: int = 1, *, cut: int | None = None) -> ChapterLeafPlan:
    selector: dict[str, Any] = (
        {"mode": "average", "count": count}
        if cut is None
        else {"mode": "exact_frames", "frames": [cut]}
    )
    return plan_chapters_and_leaves(
        AdmittedTimeline(
            artifact_id="01234567-89ab-4cde-8f01-23456789abcd",
            frame_count=frames,
            frame_rate="30/1",
        ),
        ChapterSettings.model_validate({"chapter_selector": selector}),
    )


def _settings(left: int, right: int, minimum: int = 1) -> ExperimentalContextSettings:
    # 测试 fixture 的参数简写不构成生产模型默认值；三项总是显式传入被测设置。
    return ExperimentalContextSettings(
        left_context_frames=left, right_context_frames=right, minimum_input_frames=minimum
    )


def _assert_oracle(result: ExperimentalContextPlan) -> None:
    """直接枚举源/输出时刻并重建交集顺序，不复用被测的几何或 bisect helper。"""

    total = result.chapter_plan.source.frame_count
    global_frames: list[int] = []
    for chapter in result.chapters:
        assert 0 <= chapter.context_start_frame <= chapter.formal_start_frame
        assert (
            chapter.formal_start_frame
            < chapter.formal_end_frame
            <= chapter.context_end_frame
            <= total
        )
        reconstructed_input: list[int] = []
        expected_sources = []
        for source_chapter in result.chapter_plan.chapters:
            shared = sorted(
                set(range(source_chapter.start_frame, source_chapter.end_frame))
                & set(range(chapter.context_start_frame, chapter.context_end_frame))
            )
            if shared:
                expected_sources.append(source_chapter.ordinal)
        assert [source.chapter_ordinal for source in chapter.sources] == expected_sources
        for source in chapter.sources:
            original = result.chapter_plan.chapters[source.chapter_ordinal]
            assert original.chapter_id == source.chapter_id
            chunk = list(range(source.source_start_frame, source.source_end_frame))
            assert chunk == [
                original.start_frame + local
                for local in range(source.chapter_local_start_frame, source.chapter_local_end_frame)
            ]
            assert chunk == [
                chapter.context_start_frame + local
                for local in range(source.context_local_start_frame, source.context_local_end_frame)
            ]
            assert len(chunk) == source.frame_count
            reconstructed_input.extend(chunk)
        assert reconstructed_input == list(
            range(chapter.context_start_frame, chapter.context_end_frame)
        )
        assert len(reconstructed_input) == chapter.input_frame_count
        assert chapter.raw_fi_frame_count == 2 * len(reconstructed_input) - 1
        raw_global = list(range(2 * chapter.context_start_frame, 2 * chapter.context_end_frame - 1))
        kept = raw_global[chapter.crop_start_frame : chapter.crop_end_frame]
        assert kept == list(range(chapter.global_start_half_frame, chapter.global_end_half_frame))
        assert len(kept) == chapter.cropped_frame_count
        global_frames.extend(kept)
    assert global_frames == list(range(2 * total - 1))
    assert result.unpadded_frame_count == len(global_frames)
    assert result.final_tail_clone_frames == 1
    assert result.encoded_frame_count == result.unpadded_frame_count + 1 == 2 * total


def test_899_902_matches_frozen_half_frame_example() -> None:
    result = plan_experimental_contexts(_chapters(1801, cut=899), _settings(0, 1))
    a, b = result.chapters
    assert (a.formal_start_frame, a.formal_end_frame) == (0, 899)
    assert (a.context_start_frame, a.context_end_frame) == (0, 900)
    assert (a.input_frame_count, a.raw_fi_frame_count) == (900, 1799)
    assert (a.crop_start_frame, a.crop_end_frame, a.cropped_frame_count) == (0, 1798, 1798)
    assert (a.global_start_half_frame, a.global_end_half_frame) == (0, 1798)
    assert [(s.chapter_id, s.frame_count) for s in a.sources] == [
        ("chapter-0001", 899),
        ("chapter-0002", 1),
    ]
    assert (b.context_start_frame, b.context_end_frame) == (899, 1801)
    assert (b.raw_fi_frame_count, b.cropped_frame_count) == (1803, 1803)
    assert result.unpadded_frame_count == 3601 and result.encoded_frame_count == 3602
    _assert_oracle(result)


@pytest.mark.parametrize("frames", [1, 2, 899, 1801])
def test_single_chapter_clamps_both_ends_and_describes_only_global_tail(frames: int) -> None:
    result = plan_experimental_contexts(_chapters(frames), _settings(4, 4))
    chapter = result.chapters[0]
    assert (chapter.context_start_frame, chapter.context_end_frame) == (0, frames)
    assert chapter.raw_fi_frame_count == chapter.cropped_frame_count == 2 * frames - 1
    assert chapter.crop_start_frame == 0
    assert result.status == "mathematical-only" and result.assumption == "even-input-2m-minus-1"
    _assert_oracle(result)


@pytest.mark.parametrize("left,right", [(0, 1), (0, 4), (1, 4), (4, 1), (10, 2), (50, 100)])
def test_asymmetric_context_preserves_responsibility_without_shift(left: int, right: int) -> None:
    result = plan_experimental_contexts(_chapters(31, 7), _settings(left, right))
    _assert_oracle(result)
    assert result.chapters[0].context_start_frame == 0
    assert result.chapters[-1].context_end_frame == 31
    for previous, following in zip(result.chapters[:-1], result.chapters[1:], strict=True):
        assert previous.global_end_half_frame == following.global_start_half_frame
        assert previous.global_end_half_frame - 1 == 2 * previous.formal_end_frame - 1


def test_one_frame_chapters_borrow_every_intersecting_chapter_not_only_neighbors() -> None:
    result = plan_experimental_contexts(_chapters(10, 10), _settings(4, 4))
    middle = result.chapters[4]
    assert (middle.formal_start_frame, middle.formal_end_frame) == (4, 5)
    assert (middle.context_start_frame, middle.context_end_frame) == (0, 9)
    assert [source.chapter_ordinal for source in middle.sources] == list(range(9))
    assert (middle.crop_start_frame, middle.crop_end_frame) == (8, 10)
    assert (middle.global_start_half_frame, middle.global_end_half_frame) == (8, 10)
    assert [source.context_local_start_frame for source in middle.sources] == list(range(9))
    _assert_oracle(result)


def test_nonuniform_sources_and_local_offsets_are_exact() -> None:
    plan = _chapters(31, 3)  # 11 / 10 / 10
    result = plan_experimental_contexts(plan, _settings(4, 4))
    middle = result.chapters[1]
    assert [(s.source_start_frame, s.source_end_frame) for s in middle.sources] == [
        (7, 11),
        (11, 21),
        (21, 25),
    ]
    assert [(s.chapter_local_start_frame, s.chapter_local_end_frame) for s in middle.sources] == [
        (7, 11),
        (0, 10),
        (0, 4),
    ]
    assert [(s.context_local_start_frame, s.context_local_end_frame) for s in middle.sources] == [
        (0, 4),
        (4, 14),
        (14, 18),
    ]
    _assert_oracle(result)


@pytest.mark.parametrize(
    "frames,count,left,right,minimum,bad_ordinal",
    [
        (1, 1, 0, 1, 2, 0),
        (2, 1, 100, 100, 3, 0),
        (10, 3, 0, 1, 4, 2),
        (10, 10, 0, 1, 3, 0),
    ],
)
def test_minimum_input_failure_never_adds_fake_context(
    frames: int, count: int, left: int, right: int, minimum: int, bad_ordinal: int
) -> None:
    original = _chapters(frames, count)
    before = original.model_dump_json()
    with pytest.raises(ContextPlanningError) as caught:
        plan_experimental_contexts(original, _settings(left, right, minimum))
    assert caught.value.code == "E_CONTEXT_INPUT_TOO_SHORT"
    assert caught.value.field_path == ("chapters", bad_ordinal)
    assert original.model_dump_json() == before


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"left_context_frames": 0, "right_context_frames": 1},
        {"left_context_frames": 0, "minimum_input_frames": 1},
        {"right_context_frames": 1, "minimum_input_frames": 1},
    ],
)
def test_all_experiment_settings_must_be_explicit(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ExperimentalContextSettings.model_validate(payload, strict=True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("left_context_frames", -1),
        ("left_context_frames", True),
        ("left_context_frames", 0.0),
        ("right_context_frames", 0),
        ("right_context_frames", False),
        ("right_context_frames", "1"),
        ("minimum_input_frames", 0),
        ("minimum_input_frames", -1),
        ("minimum_input_frames", 1.0),
        ("minimum_input_frames", None),
        ("profile", "Aion"),
        ("copy_tail", True),
        ("execute", "not allowed"),
    ],
)
def test_settings_are_closed_strict_and_not_a_production_profile(field: str, value: object) -> None:
    payload = {
        "left_context_frames": 0,
        "right_context_frames": 1,
        "minimum_input_frames": 1,
        field: value,
    }
    with pytest.raises(ValidationError):
        ExperimentalContextSettings.model_validate(payload, strict=True)


def test_roundtrip_remains_immutable_and_revalidates_the_original_plan() -> None:
    plan = _chapters(31, 3)
    result = plan_experimental_contexts(plan, _settings(4, 4))
    assert (
        ExperimentalContextPlan.model_validate_json(result.model_dump_json(), strict=True) == result
    )
    assert (
        ExperimentalContextPlan.model_validate(json.loads(result.model_dump_json()), strict=True)
        == result
    )
    assert result.chapter_plan == plan
    assert result.model_copy(deep=True) == result
    with pytest.raises(ValidationError):
        result.chapters[0].sources[0].frame_count = 1
    with pytest.raises(ValidationError):
        result.settings.model_copy(update={"right_context_frames": False})
    with pytest.raises(ValidationError):
        result.model_copy(update={"unpadded_frame_count": result.unpadded_frame_count + 1})


@pytest.mark.parametrize(
    "mutation",
    [
        "equal_length_crop_shift",
        "context_shift",
        "source_local_shift",
        "source_context_shift",
        "source_order",
        "missing_source",
        "source_count",
        "chapter_order",
        "raw_count",
        "cropped_count",
        "global_range",
        "fake_total",
        "tail",
        "tail_bool",
        "tail_float",
        "production_status",
        "assumption",
        "source_frame_count",
        "settings",
        "unknown",
    ],
)
def test_forged_projection_fails_even_when_total_lengths_match(mutation: str) -> None:
    result = plan_experimental_contexts(_chapters(31, 3), _settings(4, 4))
    payload = result.model_dump(mode="json")
    middle = payload["chapters"][1]
    if mutation == "equal_length_crop_shift":
        middle["crop_start_frame"] += 1
        middle["crop_end_frame"] += 1
    elif mutation == "context_shift":
        middle["context_start_frame"] += 1
        middle["context_end_frame"] += 1
    elif mutation == "source_local_shift":
        middle["sources"][0]["chapter_local_start_frame"] += 1
        middle["sources"][0]["chapter_local_end_frame"] += 1
    elif mutation == "source_context_shift":
        middle["sources"][0]["context_local_start_frame"] += 1
        middle["sources"][0]["context_local_end_frame"] += 1
    elif mutation == "source_order":
        middle["sources"].reverse()
    elif mutation == "missing_source":
        middle["sources"].pop()
    elif mutation == "source_count":
        middle["sources"][0]["frame_count"] += 1
    elif mutation == "chapter_order":
        payload["chapters"].reverse()
    elif mutation == "raw_count":
        middle["raw_fi_frame_count"] += 1
    elif mutation == "cropped_count":
        middle["cropped_frame_count"] += 1
    elif mutation == "global_range":
        middle["global_start_half_frame"] += 1
        middle["global_end_half_frame"] += 1
    elif mutation == "fake_total":
        payload["encoded_frame_count"] += 2
    elif mutation in {"tail", "tail_bool", "tail_float"}:
        payload["final_tail_clone_frames"] = {"tail": 3, "tail_bool": True, "tail_float": 1.0}[
            mutation
        ]
    elif mutation == "production_status":
        payload["status"] = "verified"
    elif mutation == "assumption":
        payload["assumption"] = "automatic"
    elif mutation == "source_frame_count":
        payload["chapter_plan"]["source"]["frame_count"] += 1
    elif mutation == "settings":
        payload["settings"]["left_context_frames"] = 0
    else:
        middle["sources"][0]["path"] = "not-a-bound-artifact.mov"
    with pytest.raises(ValidationError):
        ExperimentalContextPlan.model_validate(payload, strict=True)


def test_typed_entry_rejects_unbound_or_constructed_invalid_input() -> None:
    with pytest.raises(ContextPlanningError, match="E_CONTEXT_TYPED_INPUT"):
        plan_experimental_contexts(cast(ChapterLeafPlan, {}), _settings(0, 1))
    forged = ExperimentalContextSettings.model_construct(
        left_context_frames=0, right_context_frames=False, minimum_input_frames=1
    )
    with pytest.raises(ValidationError):
        plan_experimental_contexts(_chapters(31, 3), forged)
    forged_plan = _chapters(31, 3)
    object.__setattr__(forged_plan, "leaf_count", 999)
    with pytest.raises(ValidationError):
        plan_experimental_contexts(forged_plan, _settings(0, 1))


def test_context_budget_fails_before_building_any_source_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("超预算之前不得开始构造来源投影")

    plan = _chapters(1000, 1000)
    monkeypatch.setattr(context, "_chapter_values", forbidden)
    with pytest.raises(ContextPlanningError) as caught:
        plan_experimental_contexts(plan, _settings(1000, 1000))
    assert caught.value.code == "E_CONTEXT_RESOURCE_LIMIT"


def test_1000_one_frame_chapters_keep_full_multi_neighbor_context_within_budget() -> None:
    result = plan_experimental_contexts(_chapters(1000, 1000), _settings(4, 4))
    assert len(result.chapters) == 1000
    assert sum(len(ch.sources) for ch in result.chapters) == 8980
    assert [source.chapter_ordinal for source in result.chapters[500].sources] == list(
        range(496, 505)
    )
    assert result.unpadded_frame_count == 1999 and result.encoded_frame_count == 2000
    assert result.chapters[-1].cropped_frame_count == 1


def test_complete_model_matrix_checks_input_sources_and_output_frames() -> None:
    for frames in range(1, 21):
        for count in range(1, min(frames, 5) + 1):
            plan = _chapters(frames, count)
            for left, right in ((0, 1), (1, 4), (4, 1)):
                _assert_oracle(plan_experimental_contexts(plan, _settings(left, right)))


def test_all_60300_partition_context_combinations_cover_global_half_frames_once() -> None:
    """N=1..200、所有合法 K、左右1/2/4；生产函数与此处共用唯一纯整数内核。"""

    combinations = 0
    for frames in range(1, 201):
        for count in range(1, frames + 1):
            quotient, remainder = divmod(frames, count)
            for width in (1, 2, 4):
                source_cursor = 0
                output_cursor = 0
                for ordinal in range(count):
                    start = source_cursor
                    end = start + quotient + (ordinal < remainder)
                    geometry = context._context_geometry(frames, start, end, width, width)
                    assert (
                        0 <= geometry.context_start <= start < end <= geometry.context_end <= frames
                    )
                    assert 0 <= geometry.crop_start < geometry.crop_end <= geometry.raw_count
                    assert geometry.input_count == geometry.context_end - geometry.context_start
                    assert geometry.raw_count == 2 * geometry.input_count - 1
                    # 两端的真实时间轴映射相等意味着中间连续区间逐点相等，不只检验 count。
                    assert (
                        2 * geometry.context_start + geometry.crop_start
                        == output_cursor
                        == 2 * start
                    )
                    assert 2 * geometry.context_start + geometry.crop_end == geometry.global_end
                    assert geometry.global_start == output_cursor
                    assert geometry.global_end == 2 * end - (ordinal == count - 1)
                    if ordinal != count - 1:
                        assert geometry.context_end >= end + 1
                        assert geometry.global_end - 1 == 2 * end - 1
                    source_cursor = end
                    output_cursor = geometry.global_end
                assert source_cursor == frames and output_cursor == 2 * frames - 1
                combinations += 1
    assert combinations == 60_300
