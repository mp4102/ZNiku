"""锁定工程元数据与输出命名复用同一安全组件规则，不允许保存后才因规则分歧失败。"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from zniku.avenhance_v27.naming import validate_media_basename
from zniku.project.paths import validate_filename_component
from zniku.project.storage import new_project_storage


@pytest.mark.parametrize("value", ["COM¹", "lpt².mov", "CONIN$", "CONOUT$", "\ud800", "😀" * 91])
def test_storage_and_output_naming_reject_same_unsafe_component(tmp_path: Path, value: str) -> None:
    with pytest.raises(ValueError, match="E_AV27_MEDIA_BASENAME"):
        validate_media_basename(value)
    with pytest.raises(ValidationError):
        new_project_storage(tmp_path / "synthetic.zniku", media_basename=value)


@pytest.mark.parametrize("value", ["Synthetic (2026)", "合成.测试", "COM10", "😀" * 90, "x" * 180])
def test_storage_and_output_naming_preserve_identical_safe_component(
    tmp_path: Path, value: str
) -> None:
    assert validate_media_basename(value) == value
    assert (
        new_project_storage(tmp_path / "synthetic.zniku", media_basename=value).media_basename
        == value
    )


def test_generated_filename_limit_stays_255_utf16_units() -> None:
    assert validate_filename_component("x" * 255) == "x" * 255
    with pytest.raises(ValueError, match="E_PROJECT_PATH_COMPONENT"):
        validate_filename_component("😀" * 128)
