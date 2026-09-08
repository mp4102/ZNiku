"""提供无文件 I/O 的 Windows 文件组件校验，供存储元数据和命名投影共同复用。

不规范化或截断输入，不猜测标题和扩展名；非法字符、设备名、边界点号及 Unicode 长度异常
失败关闭。路径 confinement、磁盘可用性及目录权限不属于这个纯字符串层。
"""

from __future__ import annotations

MEDIA_BASENAME_MAX_UNITS = 180

_RESERVED_COMPONENTS = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"{prefix}{number}" for prefix in ("COM", "LPT") for number in "123456789¹²³"}
)


def validate_filename_component(value: str, *, max_units: int = 255) -> str:
    """验证安全 Windows 单组件，长度按 UTF-16 单位计数；成功时原样返回。"""

    try:
        units = len(value.encode("utf-16-le")) // 2
    except UnicodeEncodeError as error:
        raise ValueError("E_PROJECT_PATH_COMPONENT: 文件名包含非法 Unicode") from error
    if (
        not value
        or value.strip() != value
        or value in {".", ".."}
        or value.endswith((".", " "))
        or any(ord(character) < 32 or character in '<>:"/\\|?*' for character in value)
        or value.split(".", 1)[0].upper() in _RESERVED_COMPONENTS
        or units > max_units
    ):
        raise ValueError("E_PROJECT_PATH_COMPONENT: 名称必须是长度受限的安全 Windows 文件组件")
    return value
