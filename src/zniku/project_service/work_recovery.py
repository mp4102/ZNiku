"""显式重试普通准备时结束被替代的失败批次，不改变 Runtime 的通用重试语义。

只在普通 rerun command 已成功创建新 Run 后调用；只读查看、选择参数、旧 exact 和任意其他
节点图不触发本策略。使用既有 abandon_run 保留历史、已完成 Artifact 和外部交付，不删除文件。
"""

from zniku.runtime import NodeRunState, Run, RunState, RuntimeService
from zniku.source_preparation.work_definitions import definition_role


def _work_graph(run: Run) -> bool:
    definitions = {(d.type_id, d.version): d for d in run.definitions_snapshot}
    roles = []
    for node in run.graph_snapshot.nodes:
        definition = definitions.get((node.type_id, node.definition_version))
        role = None if definition is None else definition_role(definition)
        if role is None or role in roles:
            return False
        roles.append(role)
    return {"source", "diagnostics", "admission"} <= set(roles) and not (
        "builtin" in roles and "external" in roles
    )


def can_retire_replaced_work_run(previous: Run, replacement: Run, node_id: str) -> bool:
    """闭合窄门禁：失败目标、无活动交接、新准备批次已创建，且双方均是完整受信 work 图。"""
    if (
        previous.run_id == replacement.run_id
        or previous.project_id != replacement.project_id
        or previous.state is not RunState.RUNNING
        or replacement.state is not RunState.RUNNING
        or not replacement.node_runs
        or any(a.state is not NodeRunState.PENDING for a in replacement.node_runs)
        or not _work_graph(previous)
        or not _work_graph(replacement)
    ):
        return False
    if {n.node_id for n in previous.graph_snapshot.nodes} != {
        n.node_id for n in replacement.graph_snapshot.nodes
    }:
        return False
    # 历史中仍有运行/等待交接时也不猜测其有效性；由用户显式处理该批次。
    if any(
        a.state in {NodeRunState.RUNNING, NodeRunState.WAITING_EXTERNAL} for a in previous.node_runs
    ):
        return False
    targets = [a for a in previous.node_runs if a.node_id == node_id]
    return bool(targets) and max(targets, key=lambda a: a.attempt).state is NodeRunState.FAILED


def retire_replaced_work_run(
    runtime: RuntimeService, previous: Run, replacement: Run, node_id: str
) -> None:
    """服务会话锁内调用，重新读取双方持久状态；替代创建失败时绝不能执行到这里。"""
    if not can_retire_replaced_work_run(previous, replacement, node_id):
        return
    previous = runtime.repository.get_run(previous.run_id)
    replacement = runtime.repository.get_run(replacement.run_id)
    if can_retire_replaced_work_run(previous, replacement, node_id):
        # Repository 自己仍原子检查非终态/无实际 running；失败直接暴露，不吞错或伪造成功。
        runtime.abandon_run(previous.run_id)
