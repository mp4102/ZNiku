# ZNIKU 0.1.0 Application Service 与 Agent 工具基线

- 状态：**已批准的正式基线；产品 Phase 4 已实现**
- 日期：2026-08-15
- Application 入口：`src/zniku/application/`
- Agent 入口：`src/zniku/agent/`
- 上位基线：[`product-framework.md`](./product-framework.md)
- Runtime 依赖：[`default-workflow-baseline.md`](./default-workflow-baseline.md)

## 1. 定位

本文冻结 Studio 与可选 Agent 共用的 Application Service 用例边界，以及 Phase 4 的窄化 Agent 工具面。
Agent 是无状态客户端，不拥有 WorkflowSpec、Revision、Runtime snapshot、Evidence 或 Final authority；它
只能请求正式命令、解释结果并把人工候选提交给 Runtime 验收。

## 2. Application Service 职责

`ApplicationService` 是客户端与领域核心之间的组装边界，负责：

- 复用唯一 Python Workflow Compiler 执行 validation；
- 绑定 source/chapter authority，产生 Preflight、ExecutionPlan 和冻结 Revision；
- 按 preparation identity 创建 `DefaultWorkflowRuntime`；
- 路由 inspect、ready set、automatic execute、manual handoff、external submission 与 retry；
- 对所有写命令执行稳定 `command_id` 幂等检查；
- 只返回 Runtime 已产生的 `FullVerificationRecord`，不能强制创建 Final。

Service 不自行修改 Runtime state，不直接解释媒体文件，也不向客户端公开 Engine adapter 或可执行入口。

## 3. Agent 工具面

0.1.0 闭合工具集合为：

```text
validate_workflow
compile_workflow
start_run
inspect_run
list_ready_nodes
prepare_node
submit_external_output
execute_node
retry_node
publish_final
```

工具参数只包含正式合同、稳定 identity 与 Schema 已约束的数据。没有 generic shell、任意 command、路径
执行、`invoke_engine` 或状态覆盖工具。`execute_node` 仍由 Runtime 检查 ready set 和 Engine mode；Agent
不能选择未 ready 节点，不能用 automatic 工具执行 manual Engine。

## 4. 幂等与并发边界

- 每个写命令携带稳定 `command_id`；
- 相同 `command_id` 与相同 payload 重放返回第一次结果；
- 相同 ID 对应不同 tool/payload 必须以 `E_COMMAND_IDEMPOTENCY_CONFLICT` 失败关闭；
- Agent 响应中的 snapshot 可能随运行推进而变旧，后续判断必须重新 `inspect_run`；
- `command_id` 不替代 Runtime node attempt、handoff ID、revision ID 或 Artifact identity。

0.1.0 使用单进程内存 command store 验证语义，不承诺跨进程 durable storage。持久命令日志、并发 CAS、
lease 与多客户端冲突将在 Runtime persistence 专题实现，但不得改变上述幂等规则。

## 5. 会话恢复

Agent adapter 不保存 ready、complete、handoff 或 Final 状态。旧 Agent 会话终止后，新 Agent 只需绑定
同一个 Application Service，并按 `workflow_run_id` 拉取 fresh snapshot，即可继续同一 Run。测试必须
证明：

- 新会话看到与 Runtime digest 一致的 snapshot；
- 已完成节点不重做；
- ready set 不从 Agent 对话推断；
- 后续 handoff、submission 和 Final 均继续写入同一 Runtime authority。

Application Service 进程重启后的 durable 恢复属于后续 persistence 工作；Phase 3 已证明 canonical
Runtime snapshot 自身可恢复，不应把 Agent 上下文当作缺失的持久层。

## 6. 错误解释

`AgentErrorExplanation` 只把稳定 `ContractViolation.code` 映射为中文影响和允许动作，例如：

- manual Engine 需要 `prepare_node`；
- node 未 ready 时重新拉取 ready set；
- frame/source authority 错误时停止提交并重新检查；
- Final 未验证时继续执行，不提供强制发布；
- command ID 冲突时使用新的 ID。

错误解释不能降低 severity、忽略 fail-closed 结论、修改旧 Evidence 或生成伪造完成记录。

## 7. Phase 4 验收

Phase 4 至少证明：

1. Agent validation 与 compile 复用唯一 Python Compiler/Application Service；
2. 工具集合闭合且没有 shell、任意 Engine invocation 或状态覆盖入口；
3. 写命令重放幂等，payload 冲突失败关闭；
4. Agent 不能强制 Final、跳过 handoff 或执行非 ready 节点；
5. 更换 Agent 会话后能从 fresh Runtime snapshot 完成同一 Run；
6. typed response、error explanation、unknown field 与 canonical digest 有自动化覆盖；
7. 全部 Python、projection、Studio 和 wheel 门禁保持绿色。
