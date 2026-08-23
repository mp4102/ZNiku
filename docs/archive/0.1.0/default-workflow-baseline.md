# ZNIKU 0.1.0 默认工作流与验证发布基线

> [!IMPORTANT]
> **0.1.0 历史归档：** 本文只描述 `main@198d802` 的旧实现，对 0.2.0 没有规范权威。当前唯一目标架构
> 见 [`graph-core-baseline.md`](../../architecture/graph-core-baseline.md)。

- 状态：**已批准的正式基线；产品 Phase 3 已实现**
- 日期：2026-08-15
- 实现入口：`src/zniku/pipelines/default.py`
- 上位基线：[`product-framework.md`](./product-framework.md)
- 执行依赖：[`execution-runtime-baseline.md`](./execution-runtime-baseline.md)

## 1. 定位

本文冻结产品 Phase 3 的首个默认 WorkflowSpec 和合成纵向执行合同。目标是证明当前业务流程可以完全由
WorkflowSpec、ExecutionPlan、Runtime state、人工 handoff、Evidence 与 publication authority 表达，
而不是把固定 stage 顺序重新写进 Runtime。

Phase 3 使用纯合成媒体身份与计数验证完整生命周期，不读取、编码或发布真实文件。短真实媒体、目标存储、
长片性能与生产 Engine 验收属于产品 Phase 6；本阶段不复制 AVSplitTool，也不修改 AVEnhanceFlow。

## 2. 默认拓扑

默认流程固定为：

```text
Source(ProgramMedia)
→ Demux
→ Partition(chapter)
→ Map: Manual Enhancement(all chapters)
→ Map: Manual Frame interpolation(all chapters)
→ Reduce(program video)
→ One-shot Video encode
→ Mux
→ Final
```

Demux 的 `audio_out` 以独立、有序 `AudioArtifactSet` 数据边直接连接 Mux 的 `audio_in`。Enhancement、
Frame interpolation 和 Video encode 都是 video-only，不得隐式携带、恢复或修改音频。Mux 与 Final 必须
绑定同一个原始音轨集合身份、成员顺序及 `stream_copy=true` 证明。

## 3. 章节与叶片

- Partition 只按绑定的连续 `ChapterPlan` 展开；
- 连续 Map 的 chapter 实例按相同 `scope_id` 一一依赖，不形成全章节笛卡尔屏障；
- 人工 chapter Engine 的 handoff 显式列出该章的稳定 leaf IDs；
- submission 必须提交完整且顺序一致的 leaf receipt 集合；缺失、重复或乱序一律失败关闭；
- leaf 是 handoff/恢复粒度，不被提升为 Studio 编排节点。

## 4. 人工 Engine 生命周期

人工 Enhancement 与 Frame interpolation 使用 `manual_external` Manifest，正式顺序为：

```text
ready
→ Runtime 签发 handoff
→ 人工工具产生惰性候选
→ submit_external_output
→ source authority 稳定性检查
→ full frame/leaf verification
→ no-replace publication
→ Evidence
→ complete
```

聊天、GUI 或外部工具的“已完成”消息不能推进状态。相同 attempt 的 handoff 可幂等重放；已经发布的
Artifact identity 不能替换，恢复只能从 canonical Runtime snapshot 继续。

## 5. 一次编码与恢复

Video encode Manifest 明确 `one_shot=true` 且 `supports_recovery=false`。一次编码尝试中断时：

- failed attempt 不产生 Evidence 或 publication；
- 只有显式 retry 才能回到 ready；
- retry 从完整 program 输入重新开始，成功记录使用新的 attempt；
- 同一 planned node 最终只能有一份正式 publication。

上游已验证、已发布的 Enhancement、Frame interpolation 与 Reduce 结果保持可复用，不因编码失败重做。

## 6. Final 与 full verification

唯一 Final 只有在以下条件全部成立后才能获得 `FullVerificationRecord`：

1. 冻结 Plan 的全部 planned node 都有 verified Evidence；
2. final Artifact ID/digest 与唯一 Final publication 精确一致；
3. program frame count 与 Mux 输出一致；
4. Mux 使用的 AudioArtifactSet ID 和有序 stream IDs 与 Demux 原始音轨证明一致；
5. source authority digest 从 handoff 到 Final 始终稳定；
6. 全部 publication 为 `verification_mode=full`、`no_replace=true`。

`FullVerificationRecord` 是本阶段的合成验证证明，不冒充真实媒体 probe、hash、decode 或目标存储证据。

## 7. Phase 3 验收

Phase 3 至少证明：

1. 默认流程由 `WorkflowSpec 0.2.0` 完整表达并通过唯一 Python Compiler；
2. 两个 chapter Map 在 Plan 中按同 scope 一一展开；
3. 人工 handoff、候选提交、full verification 与 publication 权限分离；
4. frame/leaf/source authority 错误失败关闭；
5. 原始双音轨 identity 与顺序贯穿 Demux→Mux→Final；
6. 一次编码失败后完整重试，上游 publication 不被替换；
7. canonical snapshot 可在无 GUI/Agent 上下文时恢复并完成同一 Run；
8. Python 测试、类型检查、质量检查、wheel 烟测与 Studio 既有门禁全部通过。
