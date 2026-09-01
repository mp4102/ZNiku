"""定义 ZNIKU Studio 与本地 Project Service 之间的 0.2.0 wire 合同。

这些模型只封装已经由 ``zniku.graph``、``zniku.project`` 与 ``zniku.runtime`` 定义的领域对象，
不复制图校验、调度或状态迁移语义。所有 command 都拒绝未知字段；HTTP 层只接受本模块解析成功的
discriminated union，避免宽松字典成为第二套命令协议。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter

from zniku.project import Project, ProjectSnapshot
from zniku.runtime import Artifact, LatestNodeResult, Run

PROJECT_SERVICE_CONTRACT_VERSION: Literal["0.2.0"] = "0.2.0"
type ActiveProjectOperation = Literal["run_all", "run_to", "rerun_from_here", "submit_external"]

LocalPath = Annotated[str, StringConstraints(min_length=1, max_length=32767)]


class ProjectServiceModel(BaseModel):
    """Project Service wire 值对象的严格基类。"""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        strict=True,
        validate_default=True,
    )


class ProjectServiceFailure(ProjectServiceModel):
    """向 Studio 暴露稳定错误码与受限操作者说明。"""

    code: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    message: Annotated[str, StringConstraints(min_length=1, max_length=4096)]


class NodeLogProjection(ProjectServiceModel):
    """携带一个 attempt 的有界 stdout/stderr 尾部，而不是任意路径读取能力。"""

    node_run_id: str
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    stdout_available: bool = False
    stderr_available: bool = False


class ProjectServiceEnvelope(ProjectServiceModel):
    """Studio 每次刷新得到的单一 Project/Runtime 读模型。"""

    contract_version: Literal["0.2.0"] = PROJECT_SERVICE_CONTRACT_VERSION
    project_path: LocalPath | None = None
    snapshot: ProjectSnapshot | None = None
    runs: tuple[Run, ...] = ()
    active_run_id: str | None = None
    active_operation: ActiveProjectOperation | None = None
    latest_results: tuple[LatestNodeResult, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
    logs: tuple[NodeLogProjection, ...] = ()
    error: ProjectServiceFailure | None = None


class OpenProjectCommand(ProjectServiceModel):
    """打开一个既有且结构合法的 ``.zniku``。"""

    operation: Literal["open_project"]
    path: LocalPath


class CreateProjectCommand(ProjectServiceModel):
    """创建不覆盖既有文件的空 Project；NodeDefinition 由后续 SDK/插件导入。"""

    operation: Literal["create_project"]
    path: LocalPath
    project_id: str
    name: str


class SaveProjectCommand(ProjectServiceModel):
    """保存当前 Project 的名称、Graph、参数与 Studio 布局。"""

    operation: Literal["save_project"]
    project: Project


class RunAllCommand(ProjectServiceModel):
    """从当前 Project 建立普通全图 Run snapshot。"""

    operation: Literal["run_all"]


class RunToCommand(ProjectServiceModel):
    """只运行目标节点及其祖先闭包。"""

    operation: Literal["run_to"]
    node_id: str


class RerunFromHereCommand(ProjectServiceModel):
    """从指定节点创建全新 attempt；终态 Run 会建立新的普通 Run。"""

    operation: Literal["rerun_from_here"]
    run_id: str
    node_id: str


class SubmitExternalCommand(ProjectServiceModel):
    """提交仍为最新 attempt 的 manual_external 目标文件。"""

    operation: Literal["submit_external"]
    node_run_id: str


type ProjectServiceCommand = Annotated[
    OpenProjectCommand
    | CreateProjectCommand
    | SaveProjectCommand
    | RunAllCommand
    | RunToCommand
    | RerunFromHereCommand
    | SubmitExternalCommand,
    Field(discriminator="operation"),
]

_COMMAND_ADAPTER: TypeAdapter[ProjectServiceCommand] = TypeAdapter(ProjectServiceCommand)


def parse_project_service_command(payload: Any) -> ProjectServiceCommand:
    """严格解析 Project Service command；未知 operation 或字段直接失败。"""

    return _COMMAND_ADAPTER.validate_python(payload, strict=True)


__all__ = [
    "PROJECT_SERVICE_CONTRACT_VERSION",
    "ActiveProjectOperation",
    "CreateProjectCommand",
    "NodeLogProjection",
    "OpenProjectCommand",
    "ProjectServiceCommand",
    "ProjectServiceEnvelope",
    "ProjectServiceFailure",
    "RerunFromHereCommand",
    "RunAllCommand",
    "RunToCommand",
    "SaveProjectCommand",
    "SubmitExternalCommand",
    "parse_project_service_command",
]
