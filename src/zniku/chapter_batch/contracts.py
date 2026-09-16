"""章内多叶共用增强声明，整章完成；逐叶来源与精确区间仍逐项校验。

新 namespace 保存真实批量生产者身份。复用既有区间、增强媒体和重叠 FI 数学，不修改旧
exact 节点接受集合，不引入 Core collection、动态端口或分叶级 checkpoint。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Annotated, Any, Self

from pydantic import Field, field_validator, model_validator

from zniku.chapter_overlap.models import PositiveInt
from zniku.runtime import RunnerInput
from zniku.source_admission import contracts as admitted
from zniku.source_aligned import node_contracts as shared

VERSION = admitted.VERSION
NAMESPACE = "zniku.chapter.batch"
BATCH_PREFIX = "zniku.source-admitted.enhancement-batch."
ROLE_TYPES = {
    role: f"zniku.source-admitted.chapter-batch.{role}"
    for role in ("merge", "context", "fi", "crop", "program", "final")
}


def port_ids(count: int) -> tuple[str, ...]:
    """设计时固定章内 output shape；严格区分 bool 与整数。"""
    if type(count) is not int or not 1 <= count <= 10000:
        raise ValueError("E_CHAPTER_BATCH_COUNT: count 必须为 1..10000 严格整数")
    return tuple(f"leaf-{ordinal:04d}" for ordinal in range(1, count + 1))


class BatchParameters(shared.ChapterParameters):
    """一个章节的有序分叶清单与共享外部增强参数；缺叶、重复或跨章均失败。"""

    leaves: Annotated[tuple[shared.LeafBinding, ...], Field(min_length=1, max_length=10000)]
    expected_input_geometry: shared.Geometry
    expected_output_geometry: shared.Geometry
    model_name: shared.Label
    model_version: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    actual_scale_factor: PositiveInt | None = None

    @field_validator("leaves", mode="before")
    @classmethod
    def array_to_tuple(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def coverage(self) -> Self:
        cursor, previous = self.chapter.start_frame, None
        for ordinal, leaf in enumerate(self.leaves):
            if (
                leaf.ordinal != ordinal
                or leaf.count != len(self.leaves)
                or leaf.start_frame != cursor
                or (previous is not None and leaf.global_ordinal != previous + 1)
            ):
                shared.fail("BATCH_COVERAGE", "章内叶必须按规范顺序连续、唯一、完整")
            cursor, previous = leaf.end_frame, leaf.global_ordinal
        if cursor != self.chapter.end_frame:
            shared.fail("BATCH_COVERAGE", "叶范围没有完整覆盖本章")
        return self


class BatchMetadata(admitted.OverlapMetadata):
    """以新 exact producer 保存逐叶绑定，旧单叶/下游 validator 不接受此族。"""

    @classmethod
    def producer_type(
        cls, role: shared.Role, split_count: int = 0, leaf: shared.LeafBinding | None = None
    ) -> str:
        if role == "split":
            return super().producer_type(role, split_count, leaf)
        if role == "enhancement":
            if leaf is None:
                shared.fail("BATCH_LEAF", "批量增强输出必须绑定自己的叶")
            return BATCH_PREFIX + str(leaf.count)
        return ROLE_TYPES[role]

    def producer_port(self) -> str:
        if self.role == "enhancement" and self.leaf is not None:
            return f"leaf-{self.leaf.ordinal + 1:04d}"
        return super().producer_port()


def preflight(
    role: str, inputs: tuple[RunnerInput, ...], parameters: Mapping[str, object]
) -> shared.NodeContract:
    """只读取直接已登记输入；全部叶映射确定后才允许整章检查或下游媒体执行。"""
    if role != "enhancement":
        return shared.preflight(
            role, inputs, parameters, metadata_model=BatchMetadata, namespace=NAMESPACE
        )
    params = BatchParameters.model_validate(shared._plain(parameters))
    if {item.port_id for item in inputs} != {"videos"}:
        shared.fail("BATCH_INPUT_PORTS", "批量增强只接受 videos 有序输入")
    values = shared._inputs(inputs, "videos", many=True)
    if len(values) != len(params.leaves):
        shared.fail("BATCH_INPUT_COUNT", "直接输入数量必须与本章全部分叶一致")
    outputs: list[shared.OutputContract] = []
    metadata: list[shared.OverlapMetadata] = []
    source: shared.SourceBinding | None = None
    common = params.model_dump(mode="json", exclude={"leaves"})
    for port, item, leaf in zip(port_ids(len(values)), values, params.leaves, strict=True):
        contract = shared.preflight(
            "enhancement",
            (replace(item, port_id="video", ordinal=None),),
            {**common, "leaf": leaf.model_dump(mode="json")},
            metadata_model=BatchMetadata,
            namespace=admitted.NAMESPACE,
        )
        source = contract.source
        metadata.extend(contract.input_metadata)
        outputs.append(shared.OutputContract(port, contract.outputs[0].metadata))
    shared._uniform(tuple(metadata))
    assert source is not None
    return shared.NodeContract(params, source, tuple(outputs), tuple(metadata))
