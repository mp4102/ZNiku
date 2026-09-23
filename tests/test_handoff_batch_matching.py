"""纯合成文件名检验章叶建议；不读取媒体，不把建议变成收件或提交。"""

from pathlib import Path

from zniku.project_service.handoff_batch import (
    HandoffBatchRow,
    _Candidate,
    _suggest_matches,
)
from zniku.project_service.handoff_import import _identity


def _row(root: Path, leaf: int, *, chapter: str = "A", port: str | None = None) -> HandoffBatchRow:
    name = f"Synthetic (2026).{chapter}.leaf-{leaf:04d}.enhancement.mov"
    return HandoffBatchRow(
        port_id=port or f"leaf-{leaf}",
        target_name=name,
        target_path=str(root / name),
        incoming_path=str(root / "incoming" / name),
        collected=False,
        target_exists=False,
        size=None,
    )


def _candidates(root: Path, names: list[str]) -> dict[str, _Candidate]:
    result = {}
    for index, name in enumerate(names):
        path = root / str(index) / name
        path.parent.mkdir()
        path.write_text("synthetic", encoding="utf-8")
        identity = _identity(path)
        assert identity
        result[f"candidate_{index:024d}"] = _Candidate(path, identity, "copy")
    return result


def test_unique_chapter_leaf_suffixes_ignore_selection_order_and_numeric_sort(
    tmp_path: Path,
) -> None:
    """5 份乱序 _slp 自动选满，0002 与 0010 的数字身份不混淆。"""
    leaves = [1, 2, 3, 4, 10]
    order = [10, 4, 1, 3, 2]
    rows = tuple(_row(tmp_path, leaf) for leaf in leaves)
    candidates = _candidates(
        tmp_path, [f"Synthetic (2026).A.leaf-{leaf:04d}_slp.mov" for leaf in order]
    )
    matches = _suggest_matches(rows, candidates)
    assert len(matches) == 5
    for row, match, leaf in zip(rows, matches, leaves, strict=True):
        assert match.port_id == row.port_id
        assert match.state == "matched" and match.basis == "chapter_leaf"
        assert match.candidate_handle
        assert candidates[match.candidate_handle].path.name.endswith(f"leaf-{leaf:04d}_slp.mov")


def test_duplicate_identity_including_exact_name_is_ambiguous(tmp_path: Path) -> None:
    row = _row(tmp_path, 1)
    candidates = _candidates(tmp_path, [row.target_name, "Synthetic (2026).A.leaf-0001_slp.mov"])
    match = _suggest_matches((row,), candidates)[0]
    assert match.state == "ambiguous" and match.candidate_handle is None
    assert match.basis is None


def test_wrong_chapter_title_extension_and_unrecognized_suffix_do_not_guess(tmp_path: Path) -> None:
    rows = tuple(_row(tmp_path, leaf) for leaf in range(1, 7))
    names = [
        "Synthetic (2026).B.leaf-0001_slp.mov",
        "Other (2026).A.leaf-0002_slp.mov",
        "Synthetic (2026).A.leaf-0003_final.mov",
        "Synthetic (2026).A.leaf-0004_slp.mp4",
        "Synthetic (2026).A.leaf-0005.leaf-0005_slp.mov",
        "Synthetic (2026).A.0006_slp.mov",
    ]
    assert all(
        match.state == "missing" and match.candidate_handle is None
        for match in _suggest_matches(rows, _candidates(tmp_path, names))
    )


def test_exact_case_insensitive_and_duplicate_targets_fail_closed(tmp_path: Path) -> None:
    row = _row(tmp_path, 2)
    candidates = _candidates(tmp_path, [row.target_name.upper()])
    assert _suggest_matches((row,), candidates)[0].basis == "canonical_name"
    duplicate = row.model_copy(update={"port_id": "other"})
    assert all(
        match.state == "ambiguous" and match.candidate_handle is None
        for match in _suggest_matches((row, duplicate), candidates)
    )
