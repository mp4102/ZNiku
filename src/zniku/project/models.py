"""定义 0.2.0 Project 的最小只读模型。

Project 只绑定当前可编辑 Graph 与面向操作者的名称；NodeDefinition 作为工程内精确版本目录由
``ProjectSnapshot`` 一同交给 SQLite Project Store。这里不引入 Run、Artifact、日志、旧 snapshot 或
JSON authority，未知字段和宽松类型默认失败关闭。
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

from zniku.graph import Graph, NodeDefinition

ProjectId = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$",
    ),
]
ProjectName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class ProjectModel(BaseModel):
    """提供 Project 领域对象共同的严格、冻结配置。"""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        strict=True,
        validate_default=True,
    )


class Project(ProjectModel):
    """表示一个 0.2.0 工程当前的可编辑 Graph。"""

    project_id: ProjectId
    name: ProjectName
    graph: Graph


class ProjectSnapshot(ProjectModel):
    """表示 Project Store 一次原子读取或保存的完整 Core 内容。

    definitions 保留调用方提供的精确顺序，并以 ``type_id/version`` 绑定 Graph 中的 NodeInstance。
    它不是 Run snapshot，也不计算 canonical digest。
    """

    project: Project
    definitions: tuple[NodeDefinition, ...]

    @field_validator("definitions", mode="before")
    @classmethod
    def normalize_definitions(cls, value: Any) -> Any:
        """接受 JSON round-trip 产生的 list，并在模型内固定为 tuple。"""

        if isinstance(value, list):
            return tuple(value)
        return value
