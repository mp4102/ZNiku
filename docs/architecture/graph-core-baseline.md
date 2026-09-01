# ZNIKU 0.2.0 自由媒体图核心设计基线

- 状态：**已批准的唯一 0.2.0 目标架构基线；严格不兼容 0.1.0；Phase 0–5 已实施**
- 日期：2026-08-23
- 目标产品：`ZNIKU Studio 0.2.0`
- 重构起点：`main@198d802`
- 实现范围：`src/zniku/`、`apps/studio/`

## 1. 文档定位

本文重新定义 ZNIKU 的产品核心。ZNIKU 不再围绕 AVEnhanceFlow 的固定流程、`full verification`、
Evidence chain 和恢复协议建设，而是成为面向本地工作站与可信局域网的自由媒体节点 Studio。

本文是 0.2.0 的唯一目标架构基线。现有 0.1.0 架构文档只描述已实现现状，不再约束 0.2.0：

- `product-framework.md`
- `engine-contract.md`
- `engine-sdk-baseline.md`
- `workflow-authoring-compiler-baseline.md`
- `execution-runtime-baseline.md`
- `default-workflow-baseline.md`
- `studio-formal-baseline.md`
- `real-media-acceptance-candidate-baseline.md`
- `phase6-extension-validation-baseline.md`
- ZBaton vNext 相关设计

实施 0.2.0 前必须先同步 `AGENTS.md` 与 README，并把旧基线移入 `docs/archive/0.1.0/`，避免两套正式规则
并存。0.1.0 Runtime snapshot、Evidence、真实媒体候选工作根和前端投影不迁移。

## 2. 最终产品定位

> **ZNIKU 是一个自由编排媒体处理节点、执行本地工作流并复用已完成结果的 GUI Studio。**

核心目标只有四个：

1. 用户可以自由添加、删除、连接、复制和组合处理节点；
2. Runtime 按 DAG 依赖执行节点；
3. 单个节点失败或中断后只能从头重跑；
4. 已完成且仍然有效的其他节点继续复用。

AVEnhanceFlow 只是可由节点组成的一份 workflow template，不再是 Runtime 的内置拓扑或验证宪法。

## 3. 已冻结的简化决策

1. 删除全局 `full verification`、Evidence、receipt 和 authority chain。
2. 删除所有节点强制 SHA-256、full decode、packet scan 和 roundtrip。
3. 删除节点内部 checkpoint、断点续跑、分片恢复和进度接管。
4. 保留工作流级状态：失败节点从头重跑，已完成上游不重做。
5. MR、Enhancement、FI 都是普通 `VideoTransform` 节点。
6. Split 的核心合同只有有序 frame range 与总帧数守恒。
7. 删除全局 `program/chapter/leaf` scope 和复杂 ArtifactSet authority。
8. 删除 Partition、Map、Select、Passthrough、Collect、Reduce 等强制 Runtime 代数。
9. 删除唯一 Final；一张图允许零个、一个或多个 Output。
10. 删除 Compiler/Freeze/Revision/ExecutionPlan 多层权威和 canonical digest。
11. ZBaton、Checksum、严格 QC 和归档 Manifest 只能作为可选节点或 Export 插件。
12. 产品默认信任本机用户、已安装工具和可信局域网，不建设公网或多租户安全体系。

## 4. 当前开发进度与取舍

当前 `0.1.0` 已有 199 个 Python 测试通过，但完成的是合同和纵向候选，不是正式自由媒体 Studio。

### 4.1 可直接复用

- React 19、React Flow 12、节点卡片、画布、连线、Inspector 和 diagnostics；
- GUI-0 的添加、拖动、连接、删除和图校验交互组件。

以下内容只能抽取规则或局部实现，不能把旧模块整体迁入 0.2.0：

- typed ports、required input、DAG cycle 和参数 JSON Schema 的校验思路；
- ready-node、attempt、failed/retry 和 completed 上游复用算法；
- `automatic` 与 `manual_external` 两种执行方式；
- FFmpeg argv、FFprobe、Split、Concat、Encode、Mux 等媒体函数；
- loopback Python host 和运行监控的 UI scaffolding。

### 4.2 必须简化或重写

- 正式 Designer 目前只能编辑预置 Draft，GUI-0 才是真正自由编排；0.2.0 以 GUI-0 为产品起点，
  不再维护 Formal Designer 与 GUI-0 双轨；
- 真实媒体候选固定为短片、两章和固定拓扑，不能继续扩建为通用 Runtime；
- `EngineManifest`、`StageRun`、`ArtifactSet`、Workflow Compiler 和 Runtime 合同应由新的最小模型替代；
- Python→Studio 可以保留 DTO/API 共享，但不再生成庞大 digest authority 投影。

### 4.3 必须删除

- `StageEvidence`、`RealStageEvidence`、`RealFullVerification` 及其 UI；
- source/output digest authority 和恢复时全库 fresh hash；
- manifest、implementation、spec、plan、revision 多层 digest；
- `supports_recovery`、resume、lease、reconnect 和 resilient handle；
- 固定音频 bitstream exact、唯一 Final、强制 no-replace 和 ZBaton 核心依赖；
- 当前 Real Acceptance 每次轮询重新加载 Runtime 并哈希全部 Artifact 的路径。

## 5. 总体架构

```text
ZNIKU Studio
    │  编辑图、参数、路径、运行与日志
    ▼
Project Service
    │  保存 .zniku 工程与普通 graph snapshot
    ▼
Graph Validator ──→ ZNIKU Runtime
                       │
                 Scheduler + Node Runner
                       │
                       ▼
                 ┌─────┼──────────┐
                 ▼     ▼          ▼
              Python  Local CLI  Manual External
                 │     / FFmpeg   Handoff
                 └─────┼──────────┘
                       ▼
               Media Files + NodeResult
```

核心只保留四个运行组件：

| 组件 | 职责 |
| --- | --- |
| `ZNIKU Studio` | 图编辑、参数编辑、运行控制、状态、进度和日志 |
| `Project Service` | 保存工程、Run、NodeRun、Artifact 和日志索引 |
| `ZNIKU Runtime` | 通过 Scheduler 与 Node Runner 校验图、计算 ready 节点并启动执行 |
| `ZNIKU Engine SDK` | 把 Python、FFmpeg、CLI 或外部人工流程包装为节点 |

Studio 不直接实现媒体算法。Runtime 也不理解 MR、Enhancement 或 FI 的业务名称，只执行节点定义。

## 6. 最小领域模型

### 6.1 Project

`.zniku` 工程保存：

- 当前 Graph；
- Studio 布局；
- Node 配置；
- Run 历史；
- Artifact 路径与基础媒体信息；
- 日志索引。

首选使用单个 SQLite-backed `.zniku` 工程文件；媒体文件和大日志保持外置。工程允许导出普通 JSON graph，
但 JSON 不成为第二套运行 authority。

### 6.2 Graph

```text
Graph
├─ nodes[]
└─ edges[]
```

Graph 是用户当前编辑的流程。启动 Run 时只复制一份普通 graph snapshot；不计算 canonical digest，
不生成 Freeze 或 ExecutionPlan。运行期间继续编辑 Graph 只影响下一次 Run。

### 6.3 NodeDefinition

```text
NodeDefinition
├─ type_id
├─ version
├─ input_ports[]
├─ output_ports[]
├─ parameter_schema
├─ execution_mode
├─ executor
└─ validator (optional)
```

`version` 用于显示、结果失效和兼容判断，不绑定代码摘要或签名。

### 6.4 NodeInstance 与 Edge

```text
NodeInstance = node_id + type_id + definition_version + parameters + ui_position
Edge         = source_port + target_port + optional ordinal
```

Edge 的 `ordinal` 只用于 Merge 等有序多输入。核心不再使用 scope、coverage authority 或 member digest。
`definition_version` 绑定当前节点定义版本；它用于结果失效，不需要 manifest digest、代码摘要或签名。

### 6.5 Artifact

```text
Artifact
├─ artifact_id
├─ kind
├─ path
├─ producer_node_run_id
├─ producer_port_id
├─ ordinal (optional)
├─ frame_range (optional)
└─ media_info
```

`artifact_id` 是项目内随机 ID，不是内容摘要。`size`、`mtime` 可以作为低成本 dirty hint，但不作为内容身份
或阻断合同。SHA-256 是可选 Checksum 节点的输出，不属于 Core 字段要求。

### 6.6 Run、NodeRun 与 NodeResult

```text
Run
├─ graph_snapshot
├─ selected_targets
├─ state
└─ node_runs[]

NodeRun
├─ node_id
├─ definition_version
├─ attempt
├─ state
├─ input_artifact_ids[]
├─ output_artifact_ids[]
├─ started_at / ended_at
├─ progress / exit_code
├─ log_path / error
└─ reused_from_result_id (optional)

NodeResult
├─ outputs[]
├─ media_summary
└─ validation_summary
```

`NodeResult` 是普通运行结果，不叫 Evidence，不递归引用祖先，也不承担防篡改证明。

## 7. 图与端口规则

### 7.1 Core 只校验

- node、port 和 edge 存在；
- output → input 类型兼容；
- required input 已连接；
-单值 input 没有多条入边；
- 有序多输入的 ordinal 唯一连续；
- 图不存在 cycle。

### 7.2 Core 明确允许

- 多个 Source；
- 多个 Output 或没有 Output 的局部试验图；
- 任意分支与汇合；
- MR、Enhancement、FI 任意排列、重复或省略；
- 运行整张图、指定 Output、选中节点或选中分支；
- 同一上游结果被多个下游节点复用。

### 7.3 首批端口类型

- `MediaFile`
- `VideoFile`
- `AudioFile`
- `DataFile`

只保留 `one` 与 `ordered_many` 两种实用 cardinality。需要新数据类型时由节点插件增加，不扩展全局 scope
代数。

## 8. 首批节点模型

### 8.1 基础节点

- `SourceMedia`
- `ExtractVideo`
- `ExtractAudio`
- `VideoTransform`
- `SplitVideo`
- `MergeVideo`
- `EncodeVideo`
- `MuxMedia`
- `OutputFile`

节点名称只表达能力，不规定在图中的位置。

### 8.2 统一 VideoTransform

MR、Enhancement、FI 统一为：

```text
VideoFile → VideoTransform → VideoFile
```

差异只存在于节点实例配置：

- `execution_mode`: `automatic` 或 `manual_external`；
- tool、model、version 的操作者声明；
- CLI/Python adapter 参数；
- 可选输出约束，例如 resolution、FPS 或 frame relation。

模型声明只是运行记录。ZNIKU 不宣称通过文件属性证明外部工具确实使用了该模型。

### 8.3 SplitVideo

首版 Split 使用设计时已知的 segment 配置。每个 segment 产生一个命名 `VideoFile` 输出端口，例如
`A`、`B`、`C`。这样每章可以直接连接不同的处理链，不需要 Partition/Map/Collect。

Split 只强制：

- 每个 segment 具有稳定顺序；
- 每个 segment 使用 `[start_frame, end_frame)`；
- 区间连续、无重叠、无缺口；
- 每个输出实际 frame count 等于区间长度；
- 全部输出 frame count 总和等于输入 frame count。

帧数优先复用能够精确对应每个输出的 FFmpeg 执行统计；无法取得可靠计数时，Split 节点允许自行增加一次
精确帧计数。它是 Split 的业务正确性校验，不是全局 full Evidence。任一输出失败时，本 attempt 的全部
输出都不登记为 Artifact，已产生的部分章节不能作为 checkpoint 复用。

自动场景发现和运行时动态成员不在 0.2.0 Core 中预设计。

### 8.4 MergeVideo

Merge 使用 `ordered_many<VideoFile>` 输入。Studio 允许拖动调整 ordinal。节点只强制：

- 每个输入存在且可 probe；
- 按 ordinal 消费；
- 输出 frame count 等于输入 frame count 总和。

不同 codec、FPS、resolution 是否允许由该 Merge 节点自己的 validator 决定，不成为 Core 规则。

### 8.5 OutputFile

Output 只是普通 sink，可有多个。它可以复制或引用上游 Artifact，并按用户选择覆盖、改名或保留已有文件。
首版不允许移动上游 Artifact，避免破坏其他分支和 completed 结果复用。Core 不强制 no-replace；UI 必须让
覆盖行为清晰可见。

## 9. 最小媒体校验

所有产生媒体输出的节点，包括 `manual_external` Submit，默认只要求：

1. 进程成功退出；
2. 声明的输出文件存在且非空；
3. FFprobe 能识别节点声明的媒体流；
4. 节点自己的轻量 validator 通过。

`VideoTransform` 默认不做 SHA、full decode、packet scan 或输入输出 exact。Resolution、FPS、frame count、
codec、bit depth、颜色和音频等检查，只有在该节点参数明确要求时才执行。

validator 只决定 `pass/fail`。Warning 仅用于 UI 提示，永不阻断下游，首版不建立 warning policy。

## 10. Runtime 状态与无断点续跑

### 10.1 状态机

```text
pending → running → completed / failed
pending → waiting_external → completed / failed
pending → failed  （executor、attempt 目录或 handoff 准备失败）
```

`ready`、`blocked`、`queued` 和 `validating` 只作为即时计算或 UI 状态，不持久化。`interrupted` 与
`cancelled` 记录为 `failed.reason`，因为后续行为都只是从头 rerun。

准备阶段的直接失败必须把 `started_at` 与 `ended_at` 记录为同一失败时刻，且不能携带 Artifact 或
无效 handoff。它只补齐“尚未进入 executor 就失败”的可审计终态，不表示 checkpoint 或 resume。

### 10.2 强制规则

- 自动节点进程中断后标记为 `failed(reason=interrupted)`；
- 应用重启时，遗留 `running` 一律变成 `failed(reason=interrupted)`；
- failed 节点只能执行 **Rerun from start**；
- rerun 创建新 attempt，从输入文件的第一个字节/第一帧重新开始；
- 不保存 resume offset、segment checkpoint、packet journal 或编码器内部进度；
- Runtime 不接管应用重启前遗留的处理进程；
- partial output 不登记为 Artifact，下次 rerun 可清理对应 attempt 临时目录；
- 已完成的其他节点保持 completed，不因某个下游失败而重做；
- 用户主动重跑 completed 节点后，该节点的旧输出不再用于新 Run，其下游标记为 stale/pending；
- `waiting_external` 可以跨应用重启保存，因为它只表示等待用户提交文件，不代表恢复外部处理进度。

每个 attempt 使用独立工作目录。成功退出并通过最小校验后才登记 Artifact，避免半成品被下游消费；这属于
基本执行正确性，不是 Evidence 或安全协议。

## 11. 复用、失效与缓存

`NodeRun.state` 只属于创建它的 Run，历史 NodeRun 永不因图编辑而回写。`stale` 是 Project 中某个
NodeInstance 最新结果的“不可复用”标志，不是 NodeRun 状态。新 Run 可以引用符合条件的旧 NodeResult，
并在新 NodeRun 中记录 `reused_from_result_id`；正在运行的 graph snapshot 不受 Project 后续编辑影响。

completed 结果可复用的条件：

- NodeDefinition `type_id/version` 未变化；
- 节点参数和入边未修改；
- 直接输入 `artifact_id` 未变化；
- 已登记输出路径仍存在且 quick probe 可读。

每个成功 attempt 必须产生新的 `artifact_id`，不再引入独立 generation 概念。以下操作将 Project 中对应
NodeInstance 的最新结果及全部下游标记为 stale：

- 修改参数；
- 重连或删除入边；
- 上游产生新的 Artifact；
- 输出丢失或 quick probe 失败；
- 用户执行“从此节点重新运行”。

同一路径被外部静默替换且 size/mtime 仍相同时，ZNIKU 可以不发现；这是可信本地模型接受的取舍。用户可以
执行“刷新媒体信息”或显式 Checksum 节点。

## 12. Engine SDK 与扩展

0.2.0 Node executor 只需要支持三种方式：

1. `python`：调用本地 Python adapter；
2. `command`：调用本地 executable 与 argv 数组；
3. `manual_external`：生成输入/输出路径提示并等待用户提交。

本地插件由用户安装并信任：

- 不做插件签名、implementation digest、沙箱或权限隔离；
- 不禁止 executable 和 argv；
- 进程仍使用参数数组和 `shell=False`，避免错误 quoting；
- NodeDefinition 只声明 type、version、ports、parameters、executor 和 validator；
- 插件失败只影响当前 NodeRun。

新增 Engine 不得要求修改 Scheduler 的业务分支。MR、Enhancement、FI 可以作为 `VideoTransform` preset，
也可以由插件提供专用 UI，但 Runtime 语义保持相同。

## 13. Studio 产品形态

0.2.0 以现有 GUI-0 为正式 Designer 起点，保留并接入真实 Project Service：

- Palette 搜索与添加节点；
- 拖动、连接、删除、复制和多选；
- Inspector 参数编辑；
- 端口兼容和 cycle 即时提示；
- 保存、打开 `.zniku`；
- Run all、Run to here、Rerun from here；
- 节点进度、状态、stdout/stderr 和输出路径；
- external handoff 的输入路径、目标路径和 Submit；
- completed/stale/failed 清晰显示，并展示 interrupted/cancelled 等失败原因。

取消 Formal Designer、Expanded Plan、Run Monitor、GUI-0 四套入口。设计与运行使用同一张图：编辑态显示配置，
运行态叠加状态。

“对所有 Split 输出应用同一节点”可以作为 Studio 宏：它生成多个普通 NodeInstance 和 Edge，不向 Runtime
增加 Map/Collect 语义。

## 14. 本地与可信局域网边界

首版只服务单用户本地工作站和操作者明确信任的 LAN：

- 不建设账号、RBAC、TLS、签名、审计防篡改、插件沙箱或多租户隔离；
- 不建设 distributed lease、worker fencing、SMB resilient handle 或 continuous availability；
- mapped drive、UNC 和普通本地路径使用同一文件 I/O 规则；
- 网络文件读写失败时当前节点失败，恢复网络后从头 rerun；
- 本地 host 默认 loopback；若用户显式监听 LAN，则由操作者承担可信网络边界；
- Runtime 不保证检测恶意或静默的外部文件替换。

仍应保留基本正确性卫生：不拼接 shell、删除只限 attempt 工作目录、覆盖输出必须显式、错误路径不得误删
项目或用户媒体。这些是防止误操作，不是安全体系。

## 15. 明确非目标

- 节点内部 resume/checkpoint；
- 自动恢复 FFmpeg/AI 工具的部分进度；
- Evidence chain、法证、防篡改和强制 checksum；
- 分布式 worker、GPU lease、跨主机调度和故障转移；
- 公网 SaaS、多用户协作和权限体系；
- 固定 AVEnhanceFlow stage 顺序；
- 全局媒体 profile 或统一 QC 合同；
- 自动证明外部 AI 模型或主观画质；
- 长片压力矩阵、SMB 断线矩阵和安全攻击测试。

## 16. 与成熟产品的取舍

本设计采用已经被成熟工具验证的简单原则：

- 像 [Mistika Workflows](https://www.sgo.es/doc/workflows/creating-a-project.html) 一样把媒体对象、节点和失败
  路由作为核心，把 checksum 做成独立能力；
- 像 [Tdarr Flows](https://docs.tdarr.io/docs/plugins/flow-plugins/basics/) 一样让 health check、hash 和文件替换
  成为工作流选择，而不是每个节点的固定成本；
- 像 [ComfyUI](https://docs.comfy.org/) 一样把自由节点编辑和只重跑变化分支放在产品体验中心；
- 像 [Vantage](https://www.telestream.net/vantage/vantage-workflow.htm) 一样把 QC 作为明确动作，而不是用一个
  全局 `full=true` 混合所有验证目标。

ZNIKU 的差异化不再是“比其他工具保存更多 Evidence”，而是：

> **对长视频友好的自由编排、人工外部节点和简单可靠的节点级重跑。**

## 17. 实施路线

### Phase 0：切换架构权威

- 更新 `AGENTS.md`、README 和版本方向；
- 归档 0.1.0 架构文档；
- 删除旧基线对 0.2.0 的规范权威；
- 不迁移旧 snapshot、Evidence 或运行目录。

### Phase 1：最小 Project 与 Graph Core

- 实现 Project、Graph、NodeDefinition、NodeInstance、Edge；
- 实现 typed port、required input、ordered_many 和 DAG validator；
- 建立 SQLite `.zniku` Project Store。

### Phase 2：Scheduler 与 Node Runner

- 实现 graph snapshot、ready 计算、attempt 和普通日志；
- 实现 `python`、`command`、`manual_external`；
- 实现 running→failed(reason=interrupted) 和 rerun-from-start；
- 实现 completed reuse 与下游 stale。

### Phase 3：正式 Studio

- 将 GUI-0 接入 Project Service 和真实 Runtime；
- 删除双轨正式投影 UI；
- 完成保存、打开、运行、日志和 external handoff。

### Phase 4：首批真实媒体节点

- SourceMedia、VideoTransform、SplitVideo、MergeVideo、EncodeVideo、MuxMedia、OutputFile；
- 将当前 FFmpeg helper 迁入节点 adapter；
- 提供 MR、Enhancement、FI external presets。

### Phase 5：清理与最小验收

- 删除 Evidence/full/recovery/固定 pipeline 的实现和测试；
- 删除全库 hash 与多层 digest authority；
- 运行 unit、Studio 和短真实媒体 smoke tests；
- 最终长片真实流程由操作者验收，不增加 SMB、攻击或压力矩阵。

## 18. 最小验收标准

0.2.0 Core 只有满足以下条件才算完成：

1. Studio 能自由创建、删除、连接、复制、保存和打开任意合法 DAG；
2. 图允许多个 Source、多个 Output 和任意合法分支/汇合；
3. 能执行 `SourceMedia → VideoTransform → OutputFile`；
4. 能执行 `SourceMedia → SplitVideo → 独立处理链 → MergeVideo → OutputFile`；
5. `VideoTransform` 同时支持 automatic 和 manual_external；
6. Split 的 frame range、顺序与总帧数守恒通过；
7. 中断节点只能从头 rerun，不能 resume；
8. 下游失败或应用重启后，completed 上游仍可复用；
9. 修改节点、入边或上游 Artifact 后，下游正确 stale；
10. 默认 Transform 不产生 SHA、full decode、packet Evidence 或巨型 receipt；
11. Studio 直接显示运行状态、日志和输出，不依赖第二套投影权威；
12. 自动化只要求单元测试、Studio 门禁和短真实媒体 smoke test。

## 19. 一句话基线

> **ZNIKU Core 只负责自由媒体 DAG、节点执行、轻量校验和已完成结果复用；节点中断就从头重跑，运行历史
> 只是记录，不是证明。**
