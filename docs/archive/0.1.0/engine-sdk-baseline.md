# ZNIKU 0.1.0 Engine SDK、Package 与 Installed Catalog 基线

> [!IMPORTANT]
> **0.1.0 历史归档：** 本文只描述 `main@198d802` 的旧实现，对 0.2.0 没有规范权威。当前唯一目标架构
> 见 [`graph-core-baseline.md`](../../architecture/graph-core-baseline.md)。

- 状态：**已批准的正式基线；Phase 1 已实现**
- 日期：2026-08-14
- 实现入口：`src/zniku/engines/`
- 上位基线：[`product-framework.md`](./product-framework.md)
- 领域依赖：[`engine-contract.md`](./engine-contract.md)

## 1. 定位

本文冻结 Phase 1 中 Engine package、已安装 Engine authority 和受信调用 adapter 的最小边界。它让
Compiler 和未来 Runtime 能按精确 `EngineBinding` 找到同一个 manifest 与实现 package，同时保持：

- WorkflowSpec、Studio 和 Agent 永远不能提交 executable、entrypoint、argv 或 shell；
- Engine 不能修改 WorkflowSpec、Runtime state、Evidence 或 Final；
- Engine 返回值只是候选 Artifact，必须由 Runtime 验收、验证和 no-replace publication；
- Phase 1 conformance 不读取或写入真实媒体。

## 2. Authority 与模型

| 对象 | 职责 |
| --- | --- |
| `EnginePackageDescriptor` | 绑定 package ID、精确 package 版本、implementation digest 与唯一 EngineBinding |
| `EnginePackage` | 由受信 Python 组装根持有 descriptor、EngineManifest 与 adapter；它不是 wire model |
| `InstalledEngineRecord` | 对 package enabled/disabled 状态的确定性只读投影 |
| `InstalledEngineCatalog` | 按精确 Engine ID、版本和 manifest digest 解析 package，供 Compiler/Runtime 使用 |
| `EngineInvocationRequest` | 绑定 invocation ID、无输出 StageRun 和完整只读输入 Artifact authority |
| `EngineInvocationResult` | 绑定 invocation、Engine identity 与候选输出，不携带完成状态 |
| `EngineAdapter` | automatic Engine 的窄 Python Protocol；不暴露任意命令字符串 |

Manifest digest 证明声明合同，implementation digest 证明安装 package 的代码身份；两者不能相互替代。
Catalog 的匹配键始终是完整 `EngineBinding`，不得按 display name、latest 或部分版本解析。

## 3. 安装与 allowlist 边界

0.1.0 Catalog 只接受应用组装根显式注入的受信 `EnginePackage`：

- 不递归扫描任意目录；
- 不从 WorkflowSpec、Manifest 或 Studio 接收入口路径；
- 不下载或动态安装 package；
- package ID 和精确 EngineBinding 均不可重复；
- disabled package 不参与 Compiler 解析，也不能被调用；
- descriptor 与 manifest identity 不一致时构造即失败关闭。

物理安装器、签名、发行源、Windows 进程隔离和远程 Engine 留待发布专题；它们不能改变本文的精确
authority 语义。

## 4. 调用与 conformance

automatic Engine 调用顺序固定为：

```text
exact EngineBinding
→ require enabled package
→ validate StageRun inputs / parameters / media preconditions
→ trusted adapter.invoke
→ validate invocation and Engine identity
→ require non-optional outputs
→ validate output typed ports / scope / media guarantees / producer
→ reject input-output identity reuse
→ return candidate outputs
```

候选输出没有 `complete`、`published` 或 `verified` 字段。SDK 不创建 Evidence，不判断 Runtime 状态，
也不写 Final。`manual_external` Engine 不能通过 automatic `invoke` 调用；其 handoff、接收和恢复协议必须
在 Runtime/Evidence 专题中冻结。

## 5. 内建 conformance Engine

Phase 1 提供两个纯合成 package：

- `zniku.builtin.synthetic-demux`：ProgramMedia → program video + parallel AudioArtifactSet；
- `zniku.builtin.synthetic-mux`：program video + 完整 AudioArtifactSet → ProgramMedia。

它们只转换合成 Artifact 声明，不接触文件。存在目的包括验证多命名端口、audio set 成员唯一和顺序、
精确 scope target、producer lineage、确定性重放和 no-replace。它们不得在 Registry 或 Studio 中显示为
生产可执行媒体 Engine。

真实 Demux/Mux 必须等待 ExecutionPlan、Runtime、Evidence、full verification 和 publication authority
完成后再进入 Phase 3 纵向试点；不得复制 AVSplitTool 或修改 AVEnhanceFlow。

## 6. 失败语义

下列输入默认失败关闭：

- package ID 或 EngineBinding 重复；
- descriptor、manifest 或调用 identity 不一致；
- 未安装、disabled 或 manual Engine 被 automatic 调用；
- request 输入值与 StageRun bindings 不一致；
- 必需候选输出缺失、端口未知、cardinality/type/media/scope 不兼容；
- 输出 producer 不属于当前 StageRun；
- 输入与输出复用同一 Artifact/ArtifactSet/member identity；
- 输出属性不满足 Manifest guarantee。

## 7. Phase 1 验收

Phase 1 完成要求：

1. Engine Contract 决策正式冻结；
2. Engine package 同时绑定 manifest 与 implementation digest；
3. Installed Catalog 精确解析、禁用和冲突语义有测试；
4. 调用前后均复用 Contract Kernel 校验，不复制 typed port 或 Schema 规则；
5. Demux/Mux conformance chain 使用纯合成 Artifact 完整通过；
6. JSON round-trip、digest、unknown field 和非法 identity 失败关闭；
7. wheel 包含 `zniku.engines` 与 `py.typed`；
8. 不实现 Runtime、真实媒体 I/O、CLI、安装器或 Studio Engine 执行。
