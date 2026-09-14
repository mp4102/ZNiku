"""色彩解释版的严格局部合同，帧区间计算复用但不伪造旧 namespace。

源实际声明与操作者工作解释完整分离；每个输出另存自己的观测。内部 planned metadata 尚无
输出观测，只有 validator 补齐实际观测后才能作为下一节点直接输入。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from fractions import Fraction
from typing import Annotated, Any, Literal, Self, cast

from pydantic import Field, ValidationError, model_validator

from zniku.avenhance_v27 import validators as legacy
from zniku.chapter_overlap.models import ChapterModel, ExactSeconds, PositiveInt
from zniku.prepared_source import node_contracts as previous
from zniku.prepared_source.node_contracts import (
    ATOMIC_SPLIT_TYPE_PREFIX as ATOMIC_SPLIT_TYPE_PREFIX,
)
from zniku.prepared_source.node_contracts import (
    EXTERNAL_TYPE_PREFIX as EXTERNAL_TYPE_PREFIX,
)
from zniku.prepared_source.node_contracts import (
    PARAMETER_MODELS as PARAMETER_MODELS,
)
from zniku.prepared_source.node_contracts import (
    ROLE_TYPES as ROLE_TYPES,
)
from zniku.prepared_source.node_contracts import (
    CandidateFiProfile as CandidateFiProfile,
)
from zniku.prepared_source.node_contracts import (
    ChapterBinding as ChapterBinding,
)
from zniku.prepared_source.node_contracts import (
    ContextBinding as ContextBinding,
)
from zniku.prepared_source.node_contracts import (
    DeclaredContainer as DeclaredContainer,
)
from zniku.prepared_source.node_contracts import (
    EnhancementDeclaration as EnhancementDeclaration,
)
from zniku.prepared_source.node_contracts import (
    ExternalParameters as ExternalParameters,
)
from zniku.prepared_source.node_contracts import (
    Geometry as Geometry,
)
from zniku.prepared_source.node_contracts import (
    LeafBinding as LeafBinding,
)
from zniku.prepared_source.node_contracts import (
    NodeContract as NodeContract,
)
from zniku.prepared_source.node_contracts import (
    Role as Role,
)
from zniku.prepared_source.node_contracts import (
    Signal as Signal,
)
from zniku.prepared_source.node_contracts import (
    SourceBinding as SourceBinding,
)
from zniku.prepared_source.node_contracts import (
    SourceExpectation as SourceExpectation,
)
from zniku.prepared_source.node_contracts import (
    fail as fail,
)
from zniku.runtime import RunnerInput
from zniku.source_color.models import (
    SIGNAL_NAMES,
    ColorField,
    FrameSignalSummary,
    InterpretationPolicy,
    ObservedSignal,
    SignalBasis,
    SignalLayer,
    SignalName,
    SourceGate,
)
from zniku.source_preparation.models import SourceSignal

OVERLAP_NAMESPACE = "zniku.prepared.color"
OVERLAP_NODE_VERSION = "0.3.4-color.1"


class SourceColorInterpretation(ChapterModel):
    """来自直接准入的已确定工作解释；永远不改写原始观察。"""

    working_signal: SourceSignal
    observed_signal: ObservedSignal
    interpretation_policy: InterpretationPolicy
    policy_version: Literal["color-interpretation/1"]
    basis: Annotated[tuple[SignalBasis, ...], Field(min_length=5, max_length=5)]

    @model_validator(mode="after")
    def proved_interpretation(self) -> Self:
        from zniku.source_color.policy import combine_observations, resolve_interpretation

        actual = combine_observations(
            self.observed_signal.container,
            self.observed_signal.bitstream,
            self.observed_signal.frames,
        )
        if actual != self.observed_signal:
            fail("COLOR_INTERPRETATION", "来源分层事实与汇总不一致")
        signal, basis = resolve_interpretation(self.observed_signal, self.interpretation_policy)
        if signal != self.working_signal or basis != self.basis:
            fail("COLOR_INTERPRETATION", "工作解释及逐字段依据不符合实际来源观察")
        return self

    @classmethod
    def from_gate(cls, gate: SourceGate) -> Self:
        return cls(
            working_signal=gate.working_signal,
            observed_signal=gate.observed_signal,
            interpretation_policy=gate.interpretation_policy,
            policy_version=gate.policy_version,
            basis=gate.basis,
        )


class HeaderSignal(ChapterModel):
    """仅为 FFprobe 合并 stream 读数，None/unknown 不宣称某层原始字段 absent。"""

    color_primaries: str | None
    color_transfer: str | None
    color_space: str | None
    color_range: str | None
    chroma_location: str | None


class CodecSignalObservation(ChapterModel):
    """对应实际 codec 的完整参数集/逐帧头；不借用 H.264 默认规则解释 ProRes。"""

    codec: Literal["h264", "hevc", "prores", "ffv1"]
    scope: Literal["all-sps-sei-eof", "all-prores-frame-headers-eof", "ffv1-no-color-fields"]
    complete: Literal[True] = True
    fields: Annotated[tuple[ColorField, ...], Field(min_length=5, max_length=5)]
    records: Annotated[int, Field(ge=0)]
    changes: Annotated[tuple[str, ...], Field(max_length=32)] = ()
    unsupported: Annotated[tuple[str, ...], Field(max_length=32)] = ()

    @model_validator(mode="after")
    def identity(self) -> Self:
        scopes = {
            "h264": "all-sps-sei-eof",
            "hevc": "all-sps-sei-eof",
            "prores": "all-prores-frame-headers-eof",
            "ffv1": "ffv1-no-color-fields",
        }
        if (
            self.scope != scopes[self.codec]
            or tuple(field.field for field in self.fields) != SIGNAL_NAMES
        ):
            fail("COLOR_CODEC", "codec 观察字段顺序或覆盖范围不合法")
        if self.codec != "ffv1" and self.records < 1:
            fail("COLOR_CODEC", "码流观察没有实际记录")
        return self


class OutputSignalObservation(ChapterModel):
    """当前实际输出的有界 header 与完整帧聚合，不以工作解释填补读数。"""

    scope: Literal["ffprobe-stream-and-full-frames"] = "ffprobe-stream-and-full-frames"
    header: HeaderSignal
    frames: FrameSignalSummary
    container: SignalLayer
    codec: CodecSignalObservation


class OverlapMetadata(ChapterModel):
    """独立新 namespace；source 只描述直接依赖，interpretation 不是实测输出 signal。"""

    producer_type_id: str
    producer_version: Literal["0.3.4-color.1"] = "0.3.4-color.1"
    role: Role
    source: SourceBinding
    chapter: ChapterBinding | None = None
    leaf: LeafBinding | None = None
    context: ContextBinding | None = None
    fi_profile: CandidateFiProfile | None = None
    enhancement: EnhancementDeclaration | None = None
    frame_count: PositiveInt
    frame_rate: ExactSeconds
    geometry: Geometry
    interpretation: SourceColorInterpretation
    observed_signal: OutputSignalObservation | None = None
    inherited_interpretation: Annotated[tuple[SignalName, ...], Field(max_length=5)] = ()

    @property
    def signal(self) -> Signal:
        """仅供共用数学/helper 读取明确工作解释；该兼容属性不序列化为实测字段。"""
        return Signal.model_validate(self.interpretation.working_signal.model_dump())

    @model_validator(mode="after")
    def all_bindings(self) -> Self:
        # 此方法只计算 producer角色/章叶/半帧区间，不读取namespace、不核发旧validator结果。
        cast(Any, previous.OverlapMetadata.all_bindings)(self)
        if self.interpretation.observed_signal.frames.frame_count != self.source.frame_count:
            fail("COLOR_SOURCE", "源色彩观察与局部源帧数不一致")
        if (
            self.observed_signal is not None
            and self.observed_signal.frames.frame_count != self.frame_count
        ):
            fail("COLOR_OUTPUT", "输出色彩扫描与输出帧数不一致")
        return self


class ExternalMetadata(ChapterModel):
    """可选 MR 的实际观察与来源工作解释；不证明 AI 模型或逐帧内容。"""

    producer_type_id: str
    producer_version: Literal["0.3.4-color.1"] = "0.3.4-color.1"
    role: Literal["external"] = "external"
    source: SourceExpectation
    declared_container: DeclaredContainer
    model_name: previous.Label
    model_version: str | None = None
    geometry: Geometry
    interpretation: SourceColorInterpretation
    observed_signal: OutputSignalObservation
    inherited_interpretation: Annotated[tuple[SignalName, ...], Field(max_length=5)] = ()
    alignment: Literal["relative-presentation-cfr"] = "relative-presentation-cfr"
    content_correspondence: Literal["operator-declared-not-proven"] = "operator-declared-not-proven"

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.producer_type_id != EXTERNAL_TYPE_PREFIX + self.declared_container:
            fail("EXTERNAL_IDENTITY", "MR 声明与实际 producer 不一致")
        if self.observed_signal.frames.frame_count != self.source.frame_count:
            fail("EXTERNAL_COUNT", "MR 完整色彩扫描未覆盖源 N 帧")
        return self


def _decode(model: type[Any], value: object) -> Any:
    return model.model_validate_json(json.dumps(previous._plain(value)), strict=True)


def read_metadata(item: RunnerInput) -> OverlapMetadata:
    try:
        metadata = _decode(OverlapMetadata, item.media_info.get(OVERLAP_NAMESPACE))
    except ValidationError as error:
        fail("COLOR_METADATA", f"缺少或非法的新色彩输出合同：{error}")
    expected_port = metadata.leaf.leaf_id if metadata.role == "split" and metadata.leaf else "video"
    if (
        item.kind != "VideoFile"
        or item.producer_port_id != expected_port
        or metadata.observed_signal is None
    ):
        fail("COLOR_PRODUCER", "输入不是具备实际输出观测的当前色彩节点产物")
    from .signal import check_output_observation

    inherited = check_output_observation(
        metadata.observed_signal, metadata.interpretation, metadata.frame_count
    )
    if inherited != metadata.inherited_interpretation:
        fail("COLOR_INHERITANCE", "继承字段必须由实际读数重算，不信任产物自报")
    return cast(OverlapMetadata, metadata)


def _check_admission(item: RunnerInput, source: SourceExpectation) -> SourceGate:
    from zniku.source_color.contracts import read_gate

    gate = read_gate(item)
    if (
        item.artifact_id != source.admission_artifact_id
        or gate.original_media_artifact_id != source.original_media_artifact_id
        or gate.reference_media_artifact_id != source.reference_media_artifact_id
        or gate.diagnosis_artifact_id != source.diagnosis_artifact_id
        or gate.source_frame_count != source.frame_count
        or gate.frame_rate != source.frame_rate
        or tuple(binding.artifact_id for binding in gate.audio_bindings)
        != source.audio_source_artifact_ids
    ):
        fail("COLOR_ADMISSION_BINDING", "新版 gate 与直接来源参数不一致")
    return gate


def _reference_gate(item: RunnerInput, source: SourceExpectation) -> SourceGate:
    from zniku.source_color.models import NAMESPACE

    namespace = previous._plain(item.media_info.get(NAMESPACE))
    if (
        not isinstance(namespace, dict)
        or set(namespace) != {"role", "admission"}
        or namespace["role"] != "reference"
    ):
        fail("COLOR_REFERENCE", "参考视频必须来自新版色彩准入，不转换旧 namespace")
    gate = cast(SourceGate, _decode(SourceGate, namespace["admission"]))
    if (
        gate.original_media_artifact_id != source.original_media_artifact_id
        or gate.reference_media_artifact_id != source.reference_media_artifact_id
        or gate.diagnosis_artifact_id != source.diagnosis_artifact_id
        or gate.source_frame_count != source.frame_count
        or gate.frame_rate != source.frame_rate
        or tuple(a.artifact_id for a in gate.audio_bindings) != source.audio_source_artifact_ids
    ):
        fail("COLOR_REFERENCE_BINDING", "参考视频的身份、N/FPS 或音频来源不一致")
    return gate


def effective_contract(item: RunnerInput, source: SourceExpectation) -> legacy._MediaContract:
    if item.kind != "VideoFile" or item.producer_port_id != "video":
        fail("COLOR_REFERENCE", "有效视频必须来自 video 端口")
    if item.artifact_id == source.reference_video_artifact_id:
        gate = _reference_gate(item, source)
        geometry = (gate.geometry.width, gate.geometry.height)
        signal = gate.working_signal.model_dump()
    else:
        metadata = cast(
            ExternalMetadata, _decode(ExternalMetadata, item.media_info.get(OVERLAP_NAMESPACE))
        )
        if metadata.source != source:
            fail("COLOR_MR_BINDING", "MR 绑定了其他参考或准入")
        from .signal import check_output_observation

        inherited = check_output_observation(
            metadata.observed_signal, metadata.interpretation, source.frame_count
        )
        if inherited != metadata.inherited_interpretation:
            fail("COLOR_INHERITANCE", "MR 继承解释与实际读数不一致")
        geometry = (metadata.geometry.width, metadata.geometry.height)
        signal = metadata.interpretation.working_signal.model_dump()
    return legacy._MediaContract(
        source.frame_count,
        Fraction(source.frame_rate),
        geometry,
        signal,
        float(Fraction(source.frame_count) / Fraction(source.frame_rate)),
    )


def preflight(
    role: str, inputs: tuple[RunnerInput, ...], parameters: Mapping[str, object]
) -> NodeContract:
    """先核对新色彩来源，再调用共用区间计算；绝不把新产物伪装为旧产物。"""
    from zniku.source_color.contracts import check_reference_binding

    if role not in PARAMETER_MODELS:
        fail("COLOR_ROLE", "未知色彩链职责")
    parsed = PARAMETER_MODELS[role].model_validate(previous._plain(parameters))
    source = cast(SourceExpectation, cast(Any, parsed).source)
    if role == "split":
        gate_input = previous._inputs(inputs, "gate", many=False, kind="DataFile")[0]
        interpretation = SourceColorInterpretation.from_gate(_check_admission(gate_input, source))
        video = previous._inputs(inputs, "videos", many=True)[0]
        if video.artifact_id != source.reference_video_artifact_id:
            mr = cast(
                ExternalMetadata, _decode(ExternalMetadata, video.media_info.get(OVERLAP_NAMESPACE))
            )
            if mr.interpretation != interpretation:
                fail("COLOR_MIXED", "MR 与当前准入工作解释不一致")
    else:
        port = (
            "videos"
            if role == "merge"
            else "chapters"
            if role in {"context", "program"}
            else "video"
        )
        values = tuple(
            read_metadata(item)
            for item in previous._inputs(inputs, port, many=role in {"merge", "context", "program"})
        )
        interpretation = values[0].interpretation
        if any(item.interpretation != interpretation for item in values):
            fail("COLOR_MIXED", "直接输入来自不同工作色彩解释")
        if role == "final":
            gate_input = previous._inputs(inputs, "gate", many=False, kind="DataFile")[0]
            if (
                SourceColorInterpretation.from_gate(_check_admission(gate_input, source))
                != interpretation
            ):
                fail("COLOR_MIXED", "Program 与 Final 直接准入的工作解释不一致")

    def metadata_factory(
        current_role: Role, bound: SourceBinding, **values: Any
    ) -> OverlapMetadata:
        working = values.pop("signal")
        if working.model_dump() != interpretation.working_signal.model_dump():
            fail("COLOR_MIXED", "计算所用工作信号与准入解释不一致")
        count, split_count = values.pop("count"), values.pop("split_count", 0)
        rate = Fraction(bound.frame_rate) * (
            2 if current_role in {"fi", "crop", "program", "final"} else 1
        )
        return OverlapMetadata(
            producer_type_id=f"{ATOMIC_SPLIT_TYPE_PREFIX}{split_count}"
            if current_role == "split"
            else ROLE_TYPES[current_role],
            role=current_role,
            source=bound,
            frame_count=count,
            frame_rate=f"{rate.numerator}/{rate.denominator}",
            interpretation=interpretation,
            **values,
        )

    def check_gate(item: RunnerInput, expected: SourceExpectation) -> None:
        _check_admission(item, expected)

    return previous.preflight(
        role,
        inputs,
        parameters,
        _kernel=previous.ContractKernel(
            read_metadata,
            effective_contract,
            check_gate,
            check_reference_binding,
            metadata_factory,
        ),
    )


def external_preflight(
    inputs: tuple[RunnerInput, ...], parameters: Mapping[str, object]
) -> tuple[ExternalParameters, RunnerInput, legacy._MediaContract, SourceColorInterpretation]:
    from zniku.source_color.contracts import check_reference_binding

    params = ExternalParameters.model_validate(previous._plain(parameters))
    if {item.port_id for item in inputs} != {"video", "gate"}:
        fail("COLOR_INPUT_PORTS", "MR 必须直接绑定参考视频及新版 gate")
    video = previous._inputs(inputs, "video", many=False)[0]
    gate = previous._inputs(inputs, "gate", many=False, kind="DataFile")[0]
    if (
        video.artifact_id != params.source.reference_video_artifact_id
        or gate.producer_port_id != "gate"
    ):
        fail("COLOR_REFERENCE_BINDING", "MR 参考或 gate 输出端口不匹配")
    admission = _check_admission(gate, params.source)
    check_reference_binding(video, gate)
    return (
        params,
        video,
        effective_contract(video, params.source),
        SourceColorInterpretation.from_gate(admission),
    )
