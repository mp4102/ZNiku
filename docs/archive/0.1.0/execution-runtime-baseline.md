# ZNIKU 0.1.0 Workflow Execution 与 Runtime Core 基线

> [!IMPORTANT]
> **0.1.0 历史归档：** 本文只描述 `main@198d802` 的旧实现，对 0.2.0 没有规范权威。当前唯一目标架构
> 见 [`graph-core-baseline.md`](../../architecture/graph-core-baseline.md)。

- 状态：**已批准的正式基线；产品 Phase 2 已实现**
- 日期：2026-08-14
- 实现入口：`src/zniku/workflow/`
- 上位基线：[`product-framework.md`](./product-framework.md)
- Authoring 依赖：[`workflow-authoring-compiler-baseline.md`](./workflow-authoring-compiler-baseline.md)

## 1. 定位

本文冻结产品 Phase 2 剩余的 Core Operator、Binding/Preflight、ExecutionPlan、WorkflowRevision/Freeze
和 Runtime Core。Phase 2 只使用纯合成 Artifact 与 executor，证明确定性拓扑、ready set、Evidence 和
基础恢复；它不读取或发布真实媒体。

## 2. 合同版本

- 既有 Source→Engine→Final authoring wire 保留 `workflow_contract_version = 0.1.0`；
- 接受 Core Operator 的 WorkflowSpec 使用 `0.2.0`，0.1.0 payload 携带 operator 必须失败关闭；
- Core Operator、Preflight、ExecutionPlan 和 Runtime 合同首版分别使用独立 `0.1.0`；
- 产品/package 版本继续保持 `0.1.0`。

## 3. Core Operator

闭合 operator 集合为 Partition、Map、Select、Passthrough、Collect 与 Reduce：

- Operator 只处理图结构、scope 和 ArtifactSet，不携带 executable、路径或 Runtime 状态；
- Partition 将 program 单值变为 chapter set；Reduce 执行相反的显式 scope 汇合；
- Select 声明稳定 member ID，selected 与 remainder 必须互斥且覆盖完整 authority；
- Map 精确绑定一个 chapter-scope、单值一入一出 Engine，但 Map 自身仍是 Runtime operator；
- Collect 通过 processed/remainder 两个 set input 建立完整性屏障；
- Passthrough 保留未处理分支语义，不冒充媒体 Engine。

## 4. Binding 与 Preflight

`WorkflowBindingSet` 将 Source node 绑定到 Artifact ID 与 digest，并可绑定一个连续、保序的
`ChapterPlan`。Preflight 必须：

1. 复用 authoring Compiler 结论；
2. 核对全部 Source、Artifact identity 和 digest；
3. 拒绝缺失章节 authority、未知 selector 成员和空 remainder；
4. 不从目录、文件名或消息推测事实；
5. 不写媒体、不创建 Plan、Revision 或 Run。

## 5. ExecutionPlan 与 Freeze

只有匹配的 valid preflight 才能产生完整 `ExecutionPlan`。Plan：

- 区分 Source、Engine、Operator 和 Final planned subject；
- 为 Map 的实际 selected chapter 产生独立、稳定的 chapter planned node；
- 把 Collect downstream dependency 绑定到全部相关上游实例；
- 绑定 WorkflowSpec、binding 和 Core Operator contract authority；
- 恰好包含一个 Final，不携带状态、lease、日志或入口路径；
- 相同输入产生相同 canonical plan 与 digest，失败时不返回部分 Plan。

`WorkflowRevision` 原子绑定 spec、binding 与 plan digest。Freeze 不启动 Run、不接触媒体；冻结后只能
创建新 Draft/Revision，不能原地修改。

## 6. Runtime Core 与 Evidence

Phase 2 `SyntheticRuntime` 只消费冻结 Plan：

- ready set 完全由 dependency 完成集合派生；
- blocked node 不能执行，failed node 只能显式 retry；
- 成功节点必须产生 `StageEvidence(verified=true)` 后才能 complete；
- 重复执行 complete node 幂等返回原 snapshot；
- Final identity 唯一且禁止替换；
- snapshot 必须精确匹配 revision 与 plan digest，canonical round-trip 后可以恢复；
- GUI、Agent、目录和完成消息都不能推进状态。

当前 Runtime 不实现并发 lease、GPU 资源、真实 Engine invocation、文件 publication 或事件传输；这些
在真实媒体纵向集成前按相同 authority 扩展，不能改变本阶段的状态派生原则。

## 7. Phase 2 验收

Phase 2 至少证明：

1. 六类 operator 均有 Python exact typed ports；
2. 完整 Demux→Partition→Select→Map→Collect→Reduce→Mux→Final 合成图通过 Compiler；
3. selector、source digest、chapter coverage、端口与 Engine contract 错误失败关闭；
4. ExecutionPlan 只展开 selected Map members，Collect 等待全部依赖；
5. Freeze 精确绑定三层 digest；
6. Runtime ready、failed/retry、Evidence、恢复、幂等和唯一 Final 有自动化测试；
7. Python Schema/Studio projection 更新后保持 fail closed；
8. 全过程不执行真实媒体、不修改 AVEnhanceFlow、不创建产品 CLI。
