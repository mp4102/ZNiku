"""以纯合成帧轴验证平均章、时间映射和均衡叶，不读媒体也不执行 FI。

包括最大切点规模、资源预算、原帧覆盖、错误行定位以及反序列化/模型伪造拒绝。数学测试不能
证明 Aion 时序相位或图像质量，也不把新的输入语义注入旧 AV27 定义。
"""

from __future__ import annotations

import json
from fractions import Fraction
from typing import Any, cast

import pytest
from pydantic import ValidationError

import zniku.chapter_overlap.planner as planner
from zniku.avenhance_v27.template import (
    ExactTimesChapterSelector as LegacyTimesSelector,
)
from zniku.avenhance_v27.template import ExpandRequest
from zniku.chapter_overlap import (
    CONTRACT_VERSION,
    MAX_PLANNED_LEAVES,
    PROFILE_ID,
    PROFILE_VERSION,
    AdmittedTimeline,
    AverageChapterSelector,
    ChapterLeafPlan,
    ChapterPlanningError,
    ChapterSettings,
    ExactFramesChapterSelector,
    ExactTimesChapterSelector,
    plan_chapters_and_leaves,
)

SOURCE_ID = "01234567-89ab-4cde-8f01-23456789abcd"


def _source(frames: int, rate: str = "30/1") -> AdmittedTimeline:
    return AdmittedTimeline(artifact_id=SOURCE_ID, frame_count=frames, frame_rate=rate)


def _settings(mode: str = "average", *, minutes: int = 5, **values: Any) -> ChapterSettings:
    return ChapterSettings.model_validate(
        {"chapter_selector": {"mode": mode, **values}, "leaf_max_minutes": minutes}, strict=True
    )


def _assert_coverage(plan: ChapterLeafPlan) -> None:
    """独立按实际序列检查覆盖，不调用被测的区间生成或验证 helper。"""

    position = 0
    global_ordinal = 0
    fps = Fraction(plan.source.frame_rate)
    for chapter_ordinal, chapter in enumerate(plan.chapters):
        assert chapter.ordinal == chapter_ordinal
        assert chapter.chapter_id == f"chapter-{chapter_ordinal + 1:04d}"
        assert chapter.start_frame == position
        lengths = []
        for local_ordinal, leaf in enumerate(chapter.leaves):
            assert leaf.chapter_ordinal == chapter_ordinal
            assert leaf.ordinal == local_ordinal
            assert leaf.global_ordinal == global_ordinal
            assert leaf.leaf_id == f"leaf-{global_ordinal + 1:04d}"
            assert leaf.start_frame == position
            assert leaf.end_frame - leaf.start_frame == leaf.frame_count
            assert 0 < leaf.frame_count <= plan.leaf_frame_cap
            assert Fraction(leaf.duration_seconds) == Fraction(leaf.frame_count, 1) / fps
            assert Fraction(leaf.start_seconds) == Fraction(position, 1) / fps
            position = leaf.end_frame
            assert position <= chapter.end_frame
            lengths.append(leaf.frame_count)
            global_ordinal += 1
        assert chapter.end_frame == position
        assert sum(lengths) == chapter.frame_count
        assert max(lengths) - min(lengths) <= 1
        assert lengths == sorted(lengths, reverse=True)
        assert Fraction(chapter.duration_seconds) == Fraction(chapter.frame_count, 1) / fps
    assert position == plan.source.frame_count
    assert global_ordinal == plan.leaf_count
    assert plan.chapter_count == len(plan.chapters)


def test_profile_identity_and_defaults_are_separate_from_legacy() -> None:
    assert PROFILE_ID == "zniku.chapter-overlap-fi"
    assert PROFILE_VERSION == CONTRACT_VERSION == "0.3.2"
    settings = ChapterSettings()
    assert settings.model_dump() == {
        "chapter_selector": {"mode": "average", "count": 1},
        "leaf_max_minutes": 5,
    }
    plan = plan_chapters_and_leaves(_source(1801), settings)
    assert [(c.start_frame, c.end_frame) for c in plan.chapters] == [(0, 1801)]
    assert plan.cut_points == ()
    assert plan.source.artifact_id == SOURCE_ID
    assert plan.chapters[0].leaves[0].duration_seconds == "1801/30"
    assert plan.chapters[0].duration_timecode == "00:01:00.033"
    _assert_coverage(plan)


def test_average_three_chapters_distributes_remainder_first() -> None:
    plan = plan_chapters_and_leaves(_source(1801), _settings(count=3))
    assert [c.frame_count for c in plan.chapters] == [601, 600, 600]
    assert [c.label for c in plan.chapters] == ["A", "B", "C"]
    assert [c.actual_frame for c in plan.cut_points] == [601, 1201]
    assert all(c.requested_time is None and c.requested_frame is None for c in plan.cut_points)
    _assert_coverage(plan)


def test_exact_frame_899_means_one_boundary_not_fixed_chunk_length() -> None:
    plan = plan_chapters_and_leaves(_source(1801), _settings("exact_frames", frames=[899]))
    assert [c.frame_count for c in plan.chapters] == [899, 902]
    assert plan.cut_points[0].requested_frame == 899
    assert plan.cut_points[0].actual_seconds == "899/30"
    _assert_coverage(plan)


@pytest.mark.parametrize("seconds", [1, 30, 60])
def test_short_video_remains_one_leaf(seconds: int) -> None:
    plan = plan_chapters_and_leaves(_source(seconds * 30), ChapterSettings())
    assert plan.leaf_count == 1
    assert plan.chapters[0].leaves[0].frame_count == seconds * 30
    _assert_coverage(plan)


@pytest.mark.parametrize(
    "frames,lengths", [(9000, [9000]), (9001, [4501, 4500]), (9030, [4515, 4515])]
)
def test_leaf_maximum_is_a_cap_and_remainder_is_balanced(frames: int, lengths: list[int]) -> None:
    plan = plan_chapters_and_leaves(_source(frames), ChapterSettings())
    assert plan.leaf_frame_cap == 9000
    assert [leaf.frame_count for leaf in plan.chapters[0].leaves] == lengths
    _assert_coverage(plan)


def test_fractional_rate_floor_caps_before_balancing() -> None:
    plan = plan_chapters_and_leaves(_source(8992, "30000/1001"), ChapterSettings())
    assert plan.leaf_frame_cap == 8991
    assert [leaf.frame_count for leaf in plan.chapters[0].leaves] == [4496, 4496]
    assert all(Fraction(leaf.duration_seconds) <= 300 for leaf in plan.chapters[0].leaves)
    _assert_coverage(plan)


def test_leaves_never_cross_chapters_and_ordinals_have_distinct_meanings() -> None:
    plan = plan_chapters_and_leaves(_source(36_003), _settings(count=3))
    assert [c.frame_count for c in plan.chapters] == [12001, 12001, 12001]
    assert [[leaf.frame_count for leaf in c.leaves] for c in plan.chapters] == [[6001, 6000]] * 3
    assert [leaf.ordinal for c in plan.chapters for leaf in c.leaves] == [0, 1, 0, 1, 0, 1]
    assert [leaf.global_ordinal for c in plan.chapters for leaf in c.leaves] == list(range(6))
    _assert_coverage(plan)


def test_time_uses_exact_half_up_and_reports_input_and_actual_coordinates() -> None:
    plan = plan_chapters_and_leaves(
        _source(60_000, "30000/1001"), _settings("exact_times", times=["00:30:00"])
    )
    point = plan.cut_points[0]
    assert point.requested_time == "00:30:00" and point.requested_frame is None
    assert point.actual_frame == 53_946
    assert Fraction(point.actual_seconds) == Fraction(53_946 * 1001, 30000)
    assert point.actual_timecode == "00:29:59.998"
    tie = plan_chapters_and_leaves(_source(10, "1/2"), _settings("exact_times", times=["00:00:01"]))
    assert tie.cut_points[0].actual_frame == 1  # .5 明确向上，不是 Python ties-to-even。


def test_timecode_allows_long_hours_without_day_wrapping() -> None:
    plan = plan_chapters_and_leaves(
        _source(100_001, "1/1"), _settings("exact_times", times=["27:00:00"], minutes=60)
    )
    assert plan.cut_points[0].actual_frame == 97_200
    assert plan.cut_points[0].actual_timecode == "27:00:00.000"
    _assert_coverage(plan)


@pytest.mark.parametrize(
    "source,settings,code,path",
    [
        (
            _source(2),
            _settings(count=3),
            "E_CHAPTER_COUNT_EXCEEDS_FRAMES",
            ("chapter_selector", "count"),
        ),
        (
            _source(100),
            _settings("exact_frames", frames=[100]),
            "E_CHAPTER_CUT_RANGE",
            ("chapter_selector", "frames", 0),
        ),
        (
            _source(100),
            _settings("exact_frames", frames=[1, 101]),
            "E_CHAPTER_CUT_RANGE",
            ("chapter_selector", "frames", 1),
        ),
        (
            _source(30),
            _settings("exact_times", times=["00:00:01"]),
            "E_CHAPTER_CUT_RANGE",
            ("chapter_selector", "times", 0),
        ),
        (
            _source(10, "1/3"),
            _settings("exact_times", times=["00:00:01"]),
            "E_CHAPTER_CUT_RANGE",
            ("chapter_selector", "times", 0),
        ),
        (
            _source(10, "1/2"),
            _settings("exact_times", times=["00:00:01", "00:00:02"]),
            "E_CHAPTER_CUT_COLLISION",
            ("chapter_selector", "times", 1),
        ),
        (
            _source(10, "1/120"),
            _settings(minutes=1),
            "E_CHAPTER_LEAF_CAP_EMPTY",
            ("leaf_max_minutes",),
        ),
    ],
)
def test_timeline_dependent_failures_have_stable_code_and_field_path(
    source: AdmittedTimeline, settings: ChapterSettings, code: str, path: tuple[str | int, ...]
) -> None:
    with pytest.raises(ChapterPlanningError) as caught:
        plan_chapters_and_leaves(source, settings)
    assert caught.value.code == code and caught.value.field_path == path


@pytest.mark.parametrize(
    "mode,values",
    [
        ("average", {"count": 0}),
        ("average", {"count": 1001}),
        ("average", {"count": True}),
        ("average", {"count": 3.0}),
        ("average", {"count": "3"}),
        ("average", {"frames": [1]}),
        ("single", {}),
        ("unknown", {}),
        ("exact_frames", {"frames": []}),
        ("exact_frames", {"frames": list(range(1, 1001))}),
        ("exact_frames", {"frames": [True]}),
        ("exact_frames", {"frames": [1.0]}),
        ("exact_frames", {"frames": ["1"]}),
        ("exact_frames", {"frames": [0]}),
        ("exact_frames", {"frames": [-1]}),
        ("exact_frames", {"frames": [1, 1]}),
        ("exact_frames", {"frames": [2, 1]}),
        ("exact_frames", {"frames": [1], "count": 2}),
        ("exact_times", {"times": []}),
        ("exact_times", {"times": [1]}),
        ("exact_times", {"times": [None]}),
        ("exact_times", {"times": ["00:00:00"]}),
        ("exact_times", {"times": ["00:00:02", "00:00:01"]}),
        ("exact_times", {"times": ["00:00:01", "000:00:01"]}),
        ("exact_times", {"times": ["00:00:01"], "frames": [30]}),
    ],
)
def test_selector_rejects_unknown_irrelevant_and_coerced_inputs(
    mode: str, values: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError):
        _settings(mode, **values)


@pytest.mark.parametrize(
    "value",
    [
        "1",
        "1/1",
        "0:00:01",
        "00:0:01",
        "00:00:1",
        "00:60:00",
        "00:00:60",
        "00:00:01.000",
        "00:00:01:00",
        "00:00:01;00",
        " 00:00:01",
        "00:00:01 ",
        "00:00:01\n",
        "-01:00:00",
        "００:００:０１",  # noqa: RUF001 - 明确测试全角数字不能冒充 ASCII 时间。
        "0" * 123 + ":00:01",
    ],
)
def test_time_format_is_strict_and_bounded(value: str) -> None:
    with pytest.raises(ValidationError):
        _settings("exact_times", times=[value])


def test_order_validation_identifies_the_specific_input_row() -> None:
    cases: list[tuple[str, dict[str, Any]]] = [
        ("exact_frames", {"frames": [3, 3]}),
        ("exact_times", {"times": ["00:00:02", "00:00:01"]}),
    ]
    for mode, values in cases:
        with pytest.raises(ValidationError) as caught:
            _settings(mode, **values)
        error = caught.value.errors()[0]
        assert error["type"] == "E_CHAPTER_SELECTOR_ORDER"
        assert error["loc"] == (
            "chapter_selector",
            mode,
            "frames" if mode == "exact_frames" else "times",
            1,
        )


@pytest.mark.parametrize("value", [0, 61, -1, True, 1.0, "5", None])
def test_leaf_minutes_are_strict_one_to_sixty(value: object) -> None:
    with pytest.raises(ValidationError):
        ChapterSettings.model_validate({"leaf_max_minutes": value}, strict=True)


@pytest.mark.parametrize(
    "updates",
    [
        {"artifact_id": ""},
        {"artifact_id": "synthetic"},
        {"artifact_id": "01234567-89ab-5cde-8f01-23456789abcd"},
        {"frame_count": 0},
        {"frame_count": True},
        {"frame_count": 30.0},
        {"frame_rate": 30},
        {"frame_rate": "30"},
        {"frame_rate": "30/01"},
        {"frame_rate": "60/2"},
        {"frame_rate": "0/1"},
        {"frame_rate": "30.0"},
        {"frame_rate": "-30/1"},
        {"frame_rate": "30/0"},
        {"frame_rate": " 30/1"},
        {"frame_rate": "30/1\n"},
        {"source_path": "synthetic.mkv"},
        {"command": "not executable"},
    ],
)
def test_source_facts_are_strict_without_paths_or_commands(updates: dict[str, Any]) -> None:
    payload = {"artifact_id": SOURCE_ID, "frame_count": 100, "frame_rate": "30/1", **updates}
    with pytest.raises(ValidationError):
        AdmittedTimeline.model_validate(payload, strict=True)


@pytest.mark.parametrize(
    "payload",
    [
        {"chapter_selector": None},
        {"chapter_selector": {"count": 1}},
        {"source_frame_count": 10},
        {"leaf_duration_minutes": 5},
        {"shell": "not allowed"},
    ],
)
def test_settings_do_not_accept_client_timeline_or_legacy_fields(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ChapterSettings.model_validate(payload, strict=True)


def test_json_roundtrip_is_plain_strict_and_nested_immutable() -> None:
    plan = plan_chapters_and_leaves(_source(36_003), _settings(count=3))
    payload = json.loads(plan.model_dump_json())
    assert ChapterLeafPlan.model_validate_json(json.dumps(payload), strict=True) == plan
    assert ChapterLeafPlan.model_validate(payload, strict=True) == plan
    assert ChapterSettings.model_validate_json("{}") == ChapterSettings()
    assert isinstance(plan.chapters, tuple) and isinstance(plan.chapters[0].leaves, tuple)
    with pytest.raises(ValidationError):
        plan.chapters[0].leaves[0].ordinal = 99
    with pytest.raises(ValidationError):
        plan.model_copy(update={"leaf_count": plan.leaf_count + 1})
    with pytest.raises(ValidationError):
        plan.settings.model_copy(update={"leaf_max_minutes": True})
    for changes in (
        {"profile_id": "avenhanceflow-v27"},
        {"profile_version": "0.3.1"},
        {"profile_version": "^0.3.2"},
        {"unknown": True},
    ):
        with pytest.raises(ValidationError):
            plan.model_copy(update=changes)
    assert plan.model_copy(deep=True) == plan


@pytest.mark.parametrize(
    "mutation",
    ["order", "count", "label", "leaf_id", "seconds", "clock", "cut", "source", "unknown"],
)
def test_serialized_projection_cannot_forge_coverage_or_binding(mutation: str) -> None:
    plan = plan_chapters_and_leaves(_source(36_003), _settings(count=3))
    payload = plan.model_dump(mode="json")
    if mutation == "order":
        payload["chapters"].reverse()
    elif mutation == "count":
        payload["chapters"][0]["frame_count"] += 1
    elif mutation == "label":
        payload["chapters"][0]["label"] = "B"
    elif mutation == "leaf_id":
        payload["chapters"][1]["leaves"][0]["leaf_id"] = "leaf-0001"
    elif mutation == "seconds":
        payload["chapters"][0]["leaves"][0]["duration_seconds"] = "1/1"
    elif mutation == "clock":
        payload["chapters"][0]["leaves"][0]["duration_timecode"] = "01:00:00.000"
    elif mutation == "cut":
        payload["cut_points"][0]["actual_frame"] += 1
    elif mutation == "source":
        payload["source"]["frame_count"] += 1
    else:
        payload["chapters"][0]["leaves"][0]["execute"] = "not allowed"
    with pytest.raises(ValidationError):
        ChapterLeafPlan.model_validate(payload, strict=True)


def test_typed_entry_revalidates_constructed_and_mutated_models() -> None:
    with pytest.raises(ChapterPlanningError, match="E_CHAPTER_TYPED_INPUT"):
        plan_chapters_and_leaves(cast(AdmittedTimeline, {}), ChapterSettings())
    forged_source = AdmittedTimeline.model_construct(
        artifact_id=SOURCE_ID, frame_count=True, frame_rate="30/1"
    )
    with pytest.raises(ValidationError):
        plan_chapters_and_leaves(forged_source, ChapterSettings())
    forged_selector = AverageChapterSelector.model_construct(mode="average", count=False)
    forged_settings = ChapterSettings.model_construct(
        chapter_selector=forged_selector, leaf_max_minutes=5
    )
    with pytest.raises(ValidationError):
        plan_chapters_and_leaves(_source(30), forged_settings)
    altered = _source(30)
    object.__setattr__(altered, "frame_rate", "60/2")
    with pytest.raises(ValidationError):
        plan_chapters_and_leaves(altered, ChapterSettings())


def test_1000_average_chapters_and_999_exact_points_remain_complete() -> None:
    average = plan_chapters_and_leaves(_source(1000), _settings(count=1000))
    exact = plan_chapters_and_leaves(
        _source(1000), _settings("exact_frames", frames=list(range(1, 1000)))
    )
    assert average.chapter_count == exact.chapter_count == 1000
    assert average.leaf_count == exact.leaf_count == 1000
    assert [c.frame_count for c in average.chapters] == [1] * 1000
    assert [c.label for c in average.chapters[:28]][-3:] == ["Z", "AA", "AB"]
    assert average.chapters[-1].label == "ALL"
    assert average.chapters[-1].chapter_id == "chapter-1000"
    times = [f"00:{i // 60:02d}:{i % 60:02d}" for i in range(1, 1000)]
    by_time = plan_chapters_and_leaves(_source(1000, "1/1"), _settings("exact_times", times=times))
    assert by_time.chapter_count == 1000
    for value in (average, exact, by_time):
        _assert_coverage(value)


def test_resource_budget_checks_before_allocating_any_leaf(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("超预算之前不应创建叶对象")

    monkeypatch.setattr(planner, "LeafProjection", forbidden)
    with pytest.raises(ChapterPlanningError, match="E_CHAPTER_PLAN_RESOURCE_LIMIT"):
        plan_chapters_and_leaves(_source(10**100), ChapterSettings())
    with pytest.raises(ChapterPlanningError, match="E_CHAPTER_PLAN_RESOURCE_LIMIT"):
        plan_chapters_and_leaves(_source(1000 * 18001), _settings(count=1000, minutes=1))


def test_maximum_leaf_budget_is_inclusive() -> None:
    plan = plan_chapters_and_leaves(_source(MAX_PLANNED_LEAVES * 60, "1/1"), _settings(minutes=1))
    assert plan.leaf_count == MAX_PLANNED_LEAVES
    assert plan.chapters[0].leaves[-1].leaf_id == "leaf-10000"
    _assert_coverage(plan)
    with pytest.raises(ChapterPlanningError, match="E_CHAPTER_PLAN_RESOURCE_LIMIT"):
        plan_chapters_and_leaves(_source(MAX_PLANNED_LEAVES * 60 + 1, "1/1"), _settings(minutes=1))


def test_synthetic_property_matrix_preserves_every_original_frame() -> None:
    """覆盖短片、余数、分数 FPS 和各章叶数变化；不用随机种子掩盖失败复现。"""

    for rate in ("1/1", "30/1", "30000/1001"):
        for frames in range(1, 81):
            for count in range(1, min(frames, 7) + 1):
                plan = plan_chapters_and_leaves(
                    _source(frames, rate), _settings(count=count, minutes=1)
                )
                lengths = [c.frame_count for c in plan.chapters]
                assert max(lengths) - min(lengths) <= 1
                assert lengths == sorted(lengths, reverse=True)
                _assert_coverage(plan)
        for frames in (1801, 9001, 27001):
            for count in (1, 2, 3, 7):
                for minutes in (1, 5, 60):
                    _assert_coverage(
                        plan_chapters_and_leaves(
                            _source(frames, rate), _settings(count=count, minutes=minutes)
                        )
                    )


def test_legacy_models_do_not_acquire_new_selector_or_leaf_semantics() -> None:
    with pytest.raises(ValidationError):
        LegacyTimesSelector(mode="exact_times", times=("00:30:00",))
    assert LegacyTimesSelector(mode="exact_times", times=("1800",)).times == ("1800",)
    with pytest.raises(ValidationError) as caught:
        ExpandRequest.model_validate({"chapter_selector": {"mode": "average", "count": 1}})
    assert any(
        error["loc"][:1] == ("chapter_selector",) and error["type"] == "union_tag_invalid"
        for error in caught.value.errors()
    )
    assert "leaf_max_minutes" not in ExpandRequest.model_fields
    assert "leaf_duration_minutes" in ExpandRequest.model_fields
    assert ExactFramesChapterSelector(mode="exact_frames", frames=(1,)).frames == (1,)
    assert ExactTimesChapterSelector(mode="exact_times", times=("00:00:01",)).times == ("00:00:01",)
