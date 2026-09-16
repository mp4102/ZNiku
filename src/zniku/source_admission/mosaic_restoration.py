"""新 MR 合同只读识别实际容器，按直入视频命名；旧固定容器定义保持原样。

候选检查可读取宿主已经授权的任意文件，但不搬移、改名或创建 Artifact。正式 validator
复用同一媒体检查，并要求最终文件名为输入 stem + .RM + 实际扩展名；路径权限仍由 Runner
控制。支持 MP4/MOV/MKV 不意味着任意媒体属性可放行，也不证明外部模型或逐帧画面。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
from pathlib import Path, PureWindowsPath
from typing import Literal, Self, cast

from pydantic import Field, JsonValue, ValidationError, model_validator

from zniku.avenhance_v27 import validators as media
from zniku.avenhance_v27.probe import Av27MediaError, Av27MediaHeader
from zniku.graph import (
    ExecutionMode,
    ExecutorOutputPathSpec,
    ManualExternalExecutorSpec,
    NodeDefinition,
    PortSpec,
    ValidatorSpec,
)
from zniku.project.paths import validate_filename_component
from zniku.runtime import NodeValidatorContext, NodeValidatorResult, RunnerInput
from zniku.source_aligned import validators as aligned
from zniku.source_aligned.node_contracts import (
    DeclaredContainer,
    ExternalParameters,
    Geometry,
    Signal,
    fail,
)

from .contracts import NAMESPACE, VERSION, ExternalMetadata, external_preflight
from .probe import admit_source

TYPE_ID = "zniku.source-admitted.mosaic-restoration.external"
VALIDATOR = "zniku.source_admission.mosaic_restoration:validate"
CONTAINER_HELP = "仅建议初始输出封装；交回时按实际 MP4/MOV/MKV 识别，不转码、不靠改后缀转换。"
CONTAINERS: tuple[DeclaredContainer, ...] = ("mp4", "mov", "mkv")


class MosaicRestorationParameters(ExternalParameters):
    """保留旧向导 wire 的封装建议，但新 exact 不把它当作实际容器限制。"""

    declared_container: DeclaredContainer = Field(default="mp4", description=CONTAINER_HELP)


class MosaicRestorationMetadata(ExternalMetadata):
    """明确记录新生产者身份和实测容器，不冒充任何旧固定容器节点。"""

    producer_type_id: Literal["zniku.source-admitted.mosaic-restoration.external"] = (
        "zniku.source-admitted.mosaic-restoration.external"
    )

    @model_validator(mode="after")
    def identity(self) -> Self:
        # 覆盖父模型的固定容器 type_id 关系；其余 N/FPS/来源字段与本版准入严格一致。
        if self.producer_type_id != TYPE_ID:
            fail("EXTERNAL_IDENTITY", "MR 自动封装生产者身份不匹配")
        return self


@dataclass(frozen=True, slots=True)
class CandidateInspection:
    """一次只读候选验收事实；不是可跳过正式 Submit 的凭据或 Artifact。"""

    container: DeclaredContainer
    archive_name: str
    media_info: Mapping[str, object]
    summary: Mapping[str, object]
    warnings: tuple[str, ...]


@lru_cache(maxsize=1)
def definition() -> NodeDefinition:
    """固定输入/输出端口的新 exact；默认路径仅为生成 handoff 时的建议。"""
    schema = MosaicRestorationParameters.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return NodeDefinition(
        type_id=TYPE_ID,
        version=VERSION,
        input_ports=(
            PortSpec(port_id="video", data_type="VideoFile"),
            PortSpec(port_id="gate", data_type="DataFile"),
        ),
        output_ports=(PortSpec(port_id="video", data_type="VideoFile"),),
        parameter_schema=cast(dict[str, JsonValue], schema),
        execution_mode=ExecutionMode.MANUAL_EXTERNAL,
        executor=ManualExternalExecutorSpec(
            output_paths=(
                ExecutorOutputPathSpec(port_id="video", relative_path="restoration.mp4"),
            ),
            instructions="处理当前输入的完整视频，保持 N→N、帧序、等价 FPS、几何和色彩。"
            "交回文件名不限，按实际 MP4/MOV/MKV 识别；先检查并收纳，再显式提交继续。",
        ),
        validator=ValidatorSpec(adapter=VALIDATOR),
    )


def is_definition(value: NodeDefinition) -> bool:
    """完整 exact 比较，不能仅以 type_id 冒认受控合同。"""
    return value == definition()


def archive_basename(inputs: tuple[RunnerInput, ...], container: DeclaredContainer) -> str:
    """从唯一直接 video 输入取 stem，不使用工程标题或候选名替代来源。"""
    videos = [item for item in inputs if item.port_id == "video" and item.kind == "VideoFile"]
    if len(videos) != 1 or container not in {"mp4", "mov", "mkv"}:
        fail("EXTERNAL_NAME", "MR 需要唯一直接 video 输入及受支持的实际封装")
    source_name = PureWindowsPath(str(videos[0].path)).stem
    return validate_filename_component(f"{source_name}.RM.{container}")


def detect_container(path: Path, header: Av27MediaHeader) -> DeclaredContainer:
    """由 demuxer 与 BMFF brand 识别容器，候选文件名和扩展名不作为判断依据。"""
    formats = media._format_names(header)
    if "matroska" in formats:
        aligned._require_container(path, header, "mkv")
        return "mkv"
    if formats & {"mov", "mp4"}:
        try:
            aligned._require_container(path, header, "mov")
            return "mov"
        except Av27MediaError:
            aligned._require_container(path, header, "mp4")
            return "mp4"
    fail("CONTAINER", "当前交回支持实际 MP4、MOV 或 MKV；不能仅改扩展名转换封装")


def inspect_candidate(
    inputs: tuple[RunnerInput, ...], parameters: Mapping[str, object], path: Path
) -> CandidateInspection:
    """在原位置只读验收已授权候选；失败不更改文件，也不扫描或重读源媒体内容。

    宿主负责选择权限和检查前后的文件变化；本函数复用正式节点全部媒体规则，不用候选
    自述 metadata 绕过 N/FPS/几何/信号或参考来源。Submit 必须再次检查。
    """
    params, _source, expected = external_preflight(inputs, parameters)
    admitted = admit_source(path)
    container = detect_container(path, admitted.header)
    if (
        admitted.timeline.frame_count != params.source.frame_count
        or admitted.frame_rate != Fraction(params.source.frame_rate)
        or (admitted.header.video.width, admitted.header.video.height) != expected.geometry
        or media._canonical_signal(admitted.signal, role="MR") != dict(expected.signal)
    ):
        fail("EXTERNAL_CONTRACT", "MR 的 N/FPS/几何/信号与参考源不符；禁止重规划掩盖变化")
    metadata = MosaicRestorationMetadata(
        source=params.source,
        declared_container=container,
        model_name=params.model_name,
        model_version=params.model_version,
        geometry=Geometry(width=expected.geometry[0], height=expected.geometry[1]),
        signal=Signal.model_validate(dict(expected.signal)),
    )
    return CandidateInspection(
        container=container,
        archive_name=archive_basename(inputs, container),
        media_info={
            "video": admitted.header.video.to_summary(),
            "container": {"format_name": admitted.header.format_name},
            NAMESPACE: metadata.model_dump(mode="json"),
        },
        summary={
            "role": "external",
            "frame_count": params.source.frame_count,
            "container": container,
        },
        warnings=(*admitted.warnings, "帧数与时间轴不证明逐帧内容或 AI 模型身份。"),
    )


def inspect_legacy_candidate(
    exact_definition: NodeDefinition,
    inputs: tuple[RunnerInput, ...],
    parameters: Mapping[str, object],
    path: Path,
) -> CandidateInspection:
    """给 0.3.5 旧固定封装任务提供同规则只读预检，不升级其 handoff 或正式归档名。

    返回的 archive_name 只是新规则建议，旧任务必须继续采用已持久化的正式目标名称。
    该入口不接纳 0.3.3 的严格 CFR 合同，避免把新准入误当作旧合同的放宽替代。
    """
    from .definitions import external_definition

    params = ExternalParameters.model_validate(dict(parameters))
    if exact_definition != external_definition(params.declared_container):
        fail("DEFINITION", "只读兼容检查需要原 0.3.5 固定封装 exact definition")
    inspected = inspect_candidate(inputs, parameters, path)
    if inspected.container != params.declared_container:
        fail("CONTAINER", "此旧任务仍要求已声明的固定封装；不能改变等待任务合同")
    return inspected


def validate(context: NodeValidatorContext) -> NodeValidatorResult:
    """正式 Submit 要求规范归档名；固定 video 端口的实际路径由现有 Runner 约束。"""
    try:
        if not is_definition(context.request.definition):
            fail("DEFINITION", "MR 自动封装 exact definition 不一致")
        output = media._single_output(context, "video")
        media._require_no_producer_metadata(output)
        if output.kind != "VideoFile" or output.size <= 0 or output.frame_range is not None:
            fail("EXTERNAL_OUTPUT", "MR 必须为完整非空 VideoFile")
        inspected = inspect_candidate(
            context.request.inputs, context.request.node.parameters, output.path
        )
        if output.path.name != inspected.archive_name:
            fail("EXTERNAL_NAME", f"正式 MR 文件名必须为 {inspected.archive_name}")
        metadata = inspected.media_info[NAMESPACE]
        assert isinstance(metadata, Mapping)
        return NodeValidatorResult(
            passed=True,
            summary=inspected.summary,
            warnings=inspected.warnings,
            media_info_extensions={"video": {NAMESPACE: metadata}},
        )
    except (Av27MediaError, ValidationError, OSError, ValueError) as error:
        code = error.code if isinstance(error, Av27MediaError) else "E_SOURCE_ADMISSION_VALIDATION"
        return NodeValidatorResult(passed=False, summary={"code": code}, message=str(error))
