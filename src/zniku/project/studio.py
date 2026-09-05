"""定义仅用于 Project authoring 的严格 StudioState，不参与 Run 或执行失效判断。

保存时拒绝未知字段、非法引用和非有限视口；读取损坏展示状态时由 Store 报 warning 并回退默认值。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from zniku.graph import Graph
from zniku.graph.models import Identifier
from zniku.project.models import ProjectModel, ProjectSnapshot

ViewText = Annotated[str, StringConstraints(min_length=1, max_length=200)]
StorageRevision = Annotated[int, Field(ge=0, le=9007199254740991)]


class StudioViewport(ProjectModel):
    """保存有限画布坐标和正缩放比例。"""

    x: float
    y: float
    zoom: Annotated[float, Field(gt=0, le=100)]


class NodeViewState(ProjectModel):
    """保存实例别名、折叠和 UI 分组；node_id 始终保持稳定身份。"""

    node_id: Identifier
    display_name: ViewText | None = None
    collapsed: bool = False
    group_id: Identifier | None = None

    @field_validator("display_name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        """保留操作者原文，但拒绝边缘空白和 NUL，不暗中修改别名。"""

        if value is not None and (value.strip() != value or "\x00" in value):
            raise ValueError("E_STUDIO_TEXT_INVALID: 别名不得为空白或含 NUL")
        return value


class GroupViewState(ProjectModel):
    """描述没有执行语义的画布分组。"""

    group_id: Identifier
    title: ViewText
    color_token: Literal["neutral", "blue", "green", "amber", "purple", "rose"] = "neutral"
    collapsed: bool = False

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        """把分组标题约束为非空纯文本，不承担任何业务分类语义。"""

        if value.strip() != value or "\x00" in value:
            raise ValueError("E_STUDIO_TEXT_INVALID: 分组名不得为空白或含 NUL")
        return value


class StudioState(ProjectModel):
    """保存唯一 Graph 对应的纯展示数据，绝不进入 ProjectSnapshot 或 Run。"""

    contract_version: Literal["0.3.0"] = "0.3.0"
    viewport: StudioViewport | None = None
    groups: tuple[GroupViewState, ...] = ()
    node_views: tuple[NodeViewState, ...] = ()

    @field_validator("groups", "node_views", mode="before")
    @classmethod
    def normalize_sequences(cls, value: Any) -> Any:
        """仅将 JSON array 固定为 tuple，不修复或过滤无效成员。"""

        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_references(self) -> StudioState:
        """组和 view 身份必须唯一，分组引用必须在同份展示状态中存在。"""

        groups = {item.group_id for item in self.groups}
        if len(groups) != len(self.groups):
            raise ValueError("E_STUDIO_GROUP_DUPLICATE: group_id 重复")
        if len({item.node_id for item in self.node_views}) != len(self.node_views):
            raise ValueError("E_STUDIO_NODE_DUPLICATE: node_id 重复")
        if any(
            item.group_id is not None and item.group_id not in groups for item in self.node_views
        ):
            raise ValueError("E_STUDIO_GROUP_UNKNOWN: node view 引用未知分组")
        return self

    def validate_graph(self, graph: Graph) -> None:
        """保存准入严格绑定当前 Graph；删除节点须同时清理它的 view。"""

        nodes = {item.node_id for item in graph.nodes}
        if any(item.node_id not in nodes for item in self.node_views):
            raise ValueError("E_STUDIO_NODE_UNKNOWN: node view 引用未知节点")


class StudioWarning(ProjectModel):
    """报告展示状态降级，不改变领域验证结论。"""

    code: str
    message: str


class AuthoringDiagnostic(ProjectModel):
    """投影唯一 GraphValidator 的机器可读诊断。"""

    code: str
    path: str
    message: str
    validator_keyword: str | None = None


class ProjectAuthoringSnapshot(ProjectModel):
    """一次数据库快照读取的 Core 与独立 Studio 状态，不是运行快照。"""

    snapshot: ProjectSnapshot
    storage_revision: StorageRevision
    studio_state: StudioState
    studio_warnings: tuple[StudioWarning, ...] = ()
