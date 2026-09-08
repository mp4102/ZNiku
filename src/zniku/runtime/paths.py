"""把合法领域端口标识映射为受控收件目录名，不限制 Graph 标识语法。

固定长度摘要只用作本机目录编码，不是 Artifact 身份、canonical digest 或执行权威。
全部标识统一编码，避免路径分隔符、Windows 保留名、长组件与编码前缀碰撞。
"""

from __future__ import annotations

from hashlib import sha256


def incoming_directory_name(port_id: str) -> str:
    """返回可跨平台创建的单个目录组件；实际任务绑定仍由 handoff 和 port_id 决定。"""

    return "port-" + sha256(port_id.encode("utf-8")).hexdigest()
