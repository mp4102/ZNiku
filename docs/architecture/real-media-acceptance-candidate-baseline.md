# ZNIKU 0.1.0 Real Media Acceptance Candidate 基线

- 状态：**本地真实媒体候选已实现并通过参考媒体门**
- 日期：2026-08-15
- 产品版本：`0.1.0`
- 实现入口：`src/zniku/realmedia/`
- 完整门：`tools/run_real_media_acceptance.py`
- 上位基线：[产品整体框架](./product-framework.md)、[Execution 与 Runtime](./execution-runtime-baseline.md)、
  [默认工作流](./default-workflow-baseline.md)、[Studio 正式工作区](./studio-formal-baseline.md)

## 1. 定位

本候选把已经通过纯合成验证的默认工作流推进为可由操作者实际验收的本地纵向切片。它必须消费正式
`WorkflowSpec`、通过唯一 Compiler 产生冻结 `ExecutionPlan`，再由持久化 ZNIKU Runtime 调用受信媒体
适配器；Studio 只通过本地 Application host 发命令和读取 fresh snapshot。

本候选不是生产 release：不执行 tag、release、安装器、桌面签名、NAS 认证或正式 ZBaton vNext 发布，
也不声称通过人工 passthrough fixture 验证了 Enhancement 或 Frame interpolation 的模型画质。

## 2. 验收素材与章节 authority

验收源由操作者在本机显式提供，只读打开，不进入 Git。候选先记录源文件 SHA-256 与 FFprobe 技术事实，
再从源时间 `00:00:28.000` 到约 `00:00:32.000` 生成隔离工作媒体。操作者建议的 `00:30:00` 在当前
60 秒素材语境中解释为第 30 秒、0 帧；隔离片段以该点分为两个连续 chapter。

时间文本只用于选择片段。正式 `ChapterPlan` 使用隔离媒体实际探测到的精确 frame coverage；若帧率、
帧数、stream 或 digest 与预期不符，必须 fail closed，不修约后继续。

## 3. 冻结工作流

候选工作流固定表达：

```text
Source(ProgramMedia)
→ real Demux
→ Partition(two chapters)
→ Map: Manual Enhancement(two chapters)
→ Map: Manual Frame interpolation(two chapters)
→ Reduce(program video)
→ real one-shot HEVC Main10 encode
→ real Mux(original ordered audio streams, stream copy)
→ unique Final(no replace)
```

Demux、Encode 和 Mux 是具有精确 `EngineBinding` 的受信本地 FFmpeg adapter。候选 authority 还从正式
Spec edge 冻结每个 planned node 的 source/target port binding，避免把同一上游节点未连接的其他输出误当
直接输入。Runtime 只按 Engine binding
解析已注入 adapter，不按 Engine ID 编写流程分支。Partition、Reduce 和 Final 仍由 Runtime operator
执行器负责。参数和状态合同不得携带 shell、entrypoint 或任意 argv；FFmpeg argv 只由受信 adapter 内部
固定构造，并始终以 `shell=False` 执行。

## 4. 人工 handoff

Enhancement 与 Frame interpolation 保持 `manual_external`：

```text
ready → prepare handoff → 外部工具写 candidate → submit
→ source/input authority 复核 → full decode/probe/hash
→ no-replace publish → Evidence → complete
```

Handoff 只给出 Runtime 工作根内的明确输入和候选位置、精确 Engine/attempt、输入 digest、期望帧数与帧率。
完成文字不推进状态。验收自动化可以用 FFmpeg 生成 passthrough/duplicate-frame candidate 来证明 handoff
机制，但记录必须明确标注 `acceptance_fixture`，不得冒充 Starlight 或 Chronos 的画质输出。

## 5. Evidence、音频与 Final

每个 complete planned node 必须绑定不可变 Evidence，至少包含 plan/revision/run、attempt、直接输入与输出
digest、实际媒体 probe、验证模式和 publication receipt。目录存在、进程退出码或 GUI 显示都不是 Evidence。

Mux 必须逐路消费 Demux 发布的原始 AudioArtifactSet，保持顺序并使用 stream copy。Full verification 对
Demux 音频候选和 Final 对应 audio stream 分别计算 bitstream SHA-256；数量、顺序或任一 digest 不一致即
拒绝 Final。Final 使用同目录 staging、digest 复核和原子 no-replace publication，已存在目标不覆盖。

## 6. 持久化与恢复

Runtime 在每个权威迁移后原子写入 canonical JSON snapshot。Snapshot 绑定 WorkflowSpec、binding、
ExecutionPlan、revision、source authority、节点 attempt、Evidence、handoff 和 artifact publication。

重启时必须重新加载同一冻结 authority，并对 source 与全部已完成 output 做 fresh size/digest/probe 复核；
任何漂移均 fail closed。`running` 或未形成 Evidence 的一次性 encode 尝试恢复为 `failed`，只能显式 retry，
已验证上游不得重做或替换。

## 7. Studio / Application host

本地 host 提供窄化 JSON API：创建候选、读取 snapshot、执行 ready 自动节点、签发 handoff、提交外部输出、
retry。host 持有路径 policy、Runtime 和 command idempotency；Studio 不接触 FFmpeg、不写 snapshot、不判定
完成。Run Monitor 轮询 fresh snapshot，在 host 不可用或响应不符合闭合 Schema 时显式 unavailable。

host 是 0.1.0 本地验收入口，不是公网服务；仅监听 `127.0.0.1`，不提供任意路径浏览、任意命令或网络
Engine 下载。

## 8. 完成门

候选交付必须同时证明：

1. 参考源保持只读且 digest 稳定，隔离片段跨越第 30 秒切分点；
2. Spec/Plan/Revision digest 冻结，两个 chapter coverage 连续完整；
3. real Demux、两段人工 handoff、Reduce、one-shot encode、Mux 和唯一 Final 全部产生 full Evidence；
4. Final 视频可完整解码，帧数/帧率符合计划，原始 audio bitstream digest 按顺序相等；
5. Final target 已存在时 no-replace 失败，故障尝试不产生 Evidence；
6. 进程重启后从同一 snapshot 继续，且上游 publication identity/digest 不变；
7. Studio 能通过 host 启动、监控、提交验收 fixture，并只展示 Runtime authority；
8. Python tests、mypy、Ruff、Studio test/typecheck/build、projection drift 与真实参考媒体 gate 全绿；
9. Git 不包含源媒体、隔离片段、artifact、Evidence、日志、本机路径或凭据；版本仍为 `0.1.0`。

## 9. 明确不冻结

- 人工 Engine 的生产模型安装、授权、画质验收阈值与 GPU 调度；
- 长片吞吐、NAS/SMB、跨主机执行、lease 和多进程并发；
- 通用 `.zniku` 工程格式、产品 CLI、Tauri 安装器和服务发现；
- 正式 ZBaton vNext SDK、签名 release 与生产 deployment。
