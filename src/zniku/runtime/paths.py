"""从明确的存储格式与持久输出绑定计算受控收件目录，不改变 Graph 端口语法。

旧格式保留端口摘要目录；英文格式优先平铺，重名输出使用稳定可读子目录。
名称仅为本机定位，不是 Artifact 身份或执行权威；未知格式和不唯一的端口失败关闭。
"""

from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path
from typing import Literal

IncomingLayout = Literal["port_hash", "english"]


def incoming_directory_name(port_id: str) -> str:
    """返回可跨平台创建的单个目录组件；实际任务绑定仍由 handoff 和 port_id 决定。"""

    return "port-" + sha256(port_id.encode("utf-8")).hexdigest()


def incoming_directories(
    work_dir: Path,
    outputs: Sequence[tuple[str, str]],
    *,
    layout: IncomingLayout = "port_hash",
) -> dict[str, Path]:
    """以完整输出集合固定收件位置；不扫描磁盘、不猜测文件与任务的关系。

    单输出或唯一 basename 的批量输出共用平铺 incoming。任一 basename 在 Windows
    大小写语义下重名时，所有输出按端口排序使用 output-001 等子目录，避免文件与目录
    同名及合法端口中的分隔符逃逸。顺序来源是已持久的端口集合，不依赖画布或来件排序。
    """

    if layout not in {"port_hash", "english"}:
        raise ValueError("E_INCOMING_LAYOUT: 未知收件布局")
    ports = [port for port, _ in outputs]
    if len(ports) != len(set(ports)):
        raise ValueError("E_INCOMING_BINDING: 收件必须绑定唯一的声明输出端口")
    root = work_dir / "incoming"
    if layout == "port_hash":
        return {port: root / incoming_directory_name(port) for port in ports}
    names = [Path(path).name.casefold() for _, path in outputs]
    if len(names) == len(set(names)):
        return dict.fromkeys(ports, root)
    return {port: root / f"output-{index:03d}" for index, port in enumerate(sorted(ports), 1)}
