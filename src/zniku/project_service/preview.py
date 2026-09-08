"""生成有界的首个可解码视频静帧，仅供 Studio 查看和 A/B 展示。

预览只解析已授权的 Artifact/handoff/picker 引用，既不是媒体 validator，也不提供 QC
或时间对齐保证。PNG 缓存只存在于当前进程的有限 LRU 内存，不写磁盘、不登记 Artifact，
不进入 Project/Run，不执行任何目录删除。失败只使预览不可用，不改变执行或复用结论。
"""

from __future__ import annotations

import base64
import binascii
import os
import struct
import subprocess
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Annotated, Final, Literal, Protocol

from pydantic import Field, StringConstraints, ValidationError, model_validator

from zniku.media.probe import MediaNodeError, resolve_media_tool
from zniku.runtime.process_window import background_creation_flags

from .host_bridge import (
    HostBridgeFailure,
    HostBridgeModel,
    HostBridgeSession,
    HostPathReference,
    HostRandomId,
    PickerSelectionReference,
)

PREVIEW_MAX_WIDTH: Final = 640
PREVIEW_MAX_HEIGHT: Final = 360
PREVIEW_MAX_IMAGE_BYTES: Final = 2 * 1024 * 1024
PREVIEW_MAX_CACHE_BYTES: Final = 16 * 1024 * 1024
PREVIEW_MAX_CACHE_ENTRIES: Final = 32
PREVIEW_TIMEOUT_SECONDS: Final = 15
_PNG_PREFIX: Final = "data:image/png;base64,"
_PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
_MAX_DATA_URL: Final = len(_PNG_PREFIX) + 4 * ((PREVIEW_MAX_IMAGE_BYTES + 2) // 3)
_MEDIA_SUFFIXES: Final = frozenset(
    {
        ".3gp",
        ".avi",
        ".flv",
        ".m2ts",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp4",
        ".mpeg",
        ".mpg",
        ".mts",
        ".ogv",
        ".ts",
        ".webm",
        ".wmv",
    }
)


class MediaPreviewRequest(HostBridgeModel):
    """只接收当前会话与已授权路径引用；不接收路径、时间、命令或编码参数。"""

    contract_version: Literal["0.3.0"]
    project_session_id: HostRandomId | None
    reference: HostPathReference

    @model_validator(mode="after")
    def validate_project_binding(self) -> MediaPreviewRequest:
        """未打开工程时仅允许本次 picker handle，不允许任意历史身份。"""

        if self.project_session_id is None and not isinstance(
            self.reference, PickerSelectionReference
        ):
            raise ValueError("E_PREVIEW_PROJECT_REQUIRED: Artifact/handoff 必须绑定工程会话")
        return self


class MediaPreviewEnvelope(MediaPreviewRequest):
    """返回精确原请求绑定与有限 PNG；浏览器据此隔离迟到或跨工程响应。"""

    image_data_url: Annotated[
        str,
        StringConstraints(
            min_length=1,
            max_length=_MAX_DATA_URL,
            pattern=r"^data:image/png;base64,[A-Za-z0-9+/]+={0,2}$",
        ),
    ]
    width: Annotated[int, Field(ge=1, le=PREVIEW_MAX_WIDTH)]
    height: Annotated[int, Field(ge=1, le=PREVIEW_MAX_HEIGHT)]
    cache_hit: bool

    @model_validator(mode="after")
    def validate_png(self) -> MediaPreviewEnvelope:
        """拒绝非 PNG 和尺寸不一致的响应，不向 img 注入任意 URL 或主动内容。"""

        try:
            data = base64.b64decode(self.image_data_url.removeprefix(_PNG_PREFIX), validate=True)
            size = _png_dimensions(data)
        except (ValueError, binascii.Error) as error:
            raise ValueError("E_PREVIEW_IMAGE: 非法预览 PNG") from error
        if size != (self.width, self.height):
            raise ValueError("E_PREVIEW_IMAGE: PNG 与声明尺寸不一致")
        return self


class PreviewProjectAuthority(Protocol):
    """预览只需要页面会话检查，不读取或写入 Graph/Runtime 存储。"""

    def assert_preview_session(self, project_session_id: str | None) -> None:
        """拒绝已经切换工程的请求。"""


@dataclass(frozen=True, slots=True)
class _Observation:
    path: str
    size: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class _Image:
    data: bytes
    width: int
    height: int


type PreviewRenderer = Callable[[Path], bytes]


def _failure(code: str, message: str, status: HTTPStatus) -> HostBridgeFailure:
    return HostBridgeFailure(code, message, http_status=status)


def _observe(path: Path) -> _Observation:
    """文件 size/mtime 只作预览缓存提示；不写回媒体身份或 dirty/stale authority。"""

    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_file() or resolved.stat().st_size < 1:
            raise OSError("不是非空普通文件")
        if ":" in resolved.name or resolved.suffix.casefold() not in _MEDIA_SUFFIXES:
            raise OSError("不支持此视频文件类型")
        info = resolved.stat()
    except OSError as error:
        raise _failure(
            "E_PREVIEW_MEDIA", "无法读取所选视频的代表帧", HTTPStatus.UNPROCESSABLE_ENTITY
        ) from error
    return _Observation(os.path.normcase(str(resolved)), info.st_size, info.st_mtime_ns)


def _png_dimensions(data: bytes) -> tuple[int, int]:
    if not 45 <= len(data) <= PREVIEW_MAX_IMAGE_BYTES or not data.startswith(_PNG_SIGNATURE):
        raise ValueError("PNG 大小或 signature 无效")
    if data[8:16] != b"\x00\x00\x00\rIHDR":
        raise ValueError("PNG 缺少 IHDR")
    width, height = struct.unpack(">II", data[16:24])
    if not (1 <= width <= PREVIEW_MAX_WIDTH and 1 <= height <= PREVIEW_MAX_HEIGHT):
        raise ValueError("PNG 尺寸超出预览边界")
    offset = 8
    saw_data = False
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError("PNG chunk 截断")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        end = offset + length + 12
        if end > len(data):
            raise ValueError("PNG chunk 内容截断")
        kind = data[offset + 4 : offset + 8]
        expected_crc = struct.unpack(">I", data[end - 4 : end])[0]
        if binascii.crc32(data[offset + 4 : end - 4]) != expected_crc:
            raise ValueError("PNG chunk CRC 无效")
        saw_data = saw_data or kind == b"IDAT"
        if kind == b"IEND":
            if length != 0 or end != len(data) or not saw_data:
                raise ValueError("PNG 结束无效")
            return width, height
        offset = end
    raise ValueError("PNG 缺少 IEND")


def render_representative_frame(path: Path) -> bytes:
    """以固定 argv、单线程与 15 秒超时解码一帧，不运行 shell、不写输出文件。"""

    try:
        executable = resolve_media_tool("ffmpeg")
    except MediaNodeError as error:
        raise _failure(
            "E_PREVIEW_UNAVAILABLE", "预览工具不可用", HTTPStatus.SERVICE_UNAVAILABLE
        ) from error
    argv = [
        executable,
        "-v",
        "error",
        "-nostdin",
        "-threads",
        "1",
        "-protocol_whitelist",
        "file",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-an",
        "-sn",
        "-dn",
        "-map_metadata",
        "-1",
        "-vf",
        "scale=w='min(640,iw)':h='min(360,ih)':force_original_aspect_ratio=decrease",
        "-c:v",
        "png",
        "-threads",
        "1",
        "-f",
        "image2pipe",
        "pipe:1",
    ]
    try:
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            creationflags=background_creation_flags(),
            check=False,
            timeout=PREVIEW_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise _failure(
            "E_PREVIEW_TIMEOUT", "预览生成超时，可使用系统播放器查看", HTTPStatus.GATEWAY_TIMEOUT
        ) from error
    except OSError as error:
        raise _failure(
            "E_PREVIEW_UNAVAILABLE", "无法启动预览工具", HTTPStatus.SERVICE_UNAVAILABLE
        ) from error
    if result.returncode != 0:
        raise _failure(
            "E_PREVIEW_MEDIA",
            "视频无法生成代表帧，可使用系统播放器查看",
            HTTPStatus.UNPROCESSABLE_ENTITY,
        )
    return result.stdout


class PreviewCache:
    """单次 launcher 生命周期的内存 LRU；一个生成任务并发，未知请求失败关闭。"""

    def __init__(self, *, renderer: PreviewRenderer = render_representative_frame) -> None:
        self._renderer = renderer
        self._images: OrderedDict[_Observation, _Image] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()
        self._closed = False

    def close(self) -> None:
        """释放内存缓存；等待至多一个有界生成任务，不遍历或删除任何磁盘路径。"""

        with self._lock:
            self._closed = True
            self._images.clear()
            self._bytes = 0

    def preview(
        self, payload: object, *, session: HostBridgeSession, application: PreviewProjectAuthority
    ) -> MediaPreviewEnvelope:
        """授权由 HTTP host 完成；生成前后确认工程与文件观察，迟到结果不返回。"""

        try:
            request = MediaPreviewRequest.model_validate(payload, strict=True)
        except ValidationError as error:
            raise _failure(
                "E_PREVIEW_REQUEST_INVALID",
                "预览请求字段或身份无效",
                HTTPStatus.UNPROCESSABLE_ENTITY,
            ) from error
        application.assert_preview_session(request.project_session_id)
        path = session.resolve_path_reference(request.reference)
        before = _observe(path)
        if not self._lock.acquire(blocking=False):
            raise _failure(
                "E_PREVIEW_BUSY", "正在生成另一张代表帧，请稍后重试", HTTPStatus.CONFLICT
            )
        try:
            if self._closed:
                raise _failure(
                    "E_PREVIEW_UNAVAILABLE", "预览会话已经关闭", HTTPStatus.SERVICE_UNAVAILABLE
                )
            cached = self._images.get(before)
            if cached is None:
                data = self._renderer(path)
                try:
                    width, height = _png_dimensions(data)
                except ValueError as error:
                    raise _failure(
                        "E_PREVIEW_IMAGE",
                        "生成的代表帧无效或超过大小限制",
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                    ) from error
                image = _Image(data=data, width=width, height=height)
            else:
                image = cached
            application.assert_preview_session(request.project_session_id)
            rebound = session.resolve_path_reference(request.reference)
            if _observe(rebound) != before:
                raise _failure(
                    "E_PREVIEW_CHANGED", "预览期间视频发生变化，请重新预览", HTTPStatus.CONFLICT
                )
            if cached is None:
                # 同一路径的旧观察不保留；size/mtime 提示仅影响预览内存，不修改 Runtime。
                for key in tuple(self._images):
                    if key.path == before.path:
                        self._bytes -= len(self._images.pop(key).data)
                while self._images and (
                    len(self._images) >= PREVIEW_MAX_CACHE_ENTRIES
                    or self._bytes + len(image.data) > PREVIEW_MAX_CACHE_BYTES
                ):
                    _, evicted = self._images.popitem(last=False)
                    self._bytes -= len(evicted.data)
                self._images[before] = image
                self._bytes += len(image.data)
            self._images.move_to_end(before)
            return MediaPreviewEnvelope(
                **request.model_dump(),
                image_data_url=_PNG_PREFIX + base64.b64encode(image.data).decode("ascii"),
                width=image.width,
                height=image.height,
                cache_hit=cached is not None,
            )
        finally:
            self._lock.release()


__all__ = ["MediaPreviewEnvelope", "MediaPreviewRequest", "PreviewCache"]
