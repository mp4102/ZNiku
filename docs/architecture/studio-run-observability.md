# ZNIKU Studio v0.2.1 Run 可观察性设计

- 状态：**v0.2.1 Phase 0 已冻结；Phase 1–2 已实施并通过门禁**
- 日期：2026-09-01
- 上位架构权威：[`graph-core-baseline.md`](graph-core-baseline.md)
- 实施计划：[`../v0.2.1-execution-plan.md`](../v0.2.1-execution-plan.md)
- 适用范围：Project Service、ZNIKU Runtime progress 投影、ZNIKU Studio

## 1. 文档定位

本文冻结 ZNIKU Studio v0.2.1 的 Run 选择、实时刷新、人工交接预检和 automatic 节点进度合同，供
Phase 1–2 直接实现和测试。本文是 `graph-core-baseline.md` 的下位设计，不修改以下上位规则：

- Run 仍然只绑定启动瞬间的一份普通 Graph snapshot；
- `NodeRun` 仍然只有 `pending`、`running`、`waiting_external`、`completed`、`failed` 五种持久状态；
- failed、interrupted 或 cancelled 节点仍然只能创建新 attempt 并从头执行；
- `waiting_external` 可以跨 Project Service 重启保留，但不代表 ZNIKU 接管了外部工具进度；
- Artifact 仍然只有在输出通过节点的最小验收后才登记；
- Studio 是 Project/Runtime authority 的读写客户端，不产生第二套运行状态；
- 不增加 Evidence、receipt、checkpoint、resume、全局 digest 或固定媒体流程。

本文解决的核心错误是：`active_run_id` 目前同时承担后台 command 绑定和 GUI 展示选择，后启动的局部 Run
因此可以隐藏仍在 `waiting_external` 的整图 Run。v0.2.1 必须把“服务正在操作哪个 Run”和“操作者正在查看
哪个 Run”彻底分离。

## 2. 已冻结的设计目标

实现完成后，操作者必须能够直接从一个 Studio 工作区回答以下问题：

1. 当前查看的是哪个 Run，它是 Run all、Run to 还是 rerun 产生的；
2. 该 Run 有多少节点处于每一种持久状态；
3. 当前 automatic 节点是否有可信百分比，以及最后一次可信观测是什么；
4. 哪些节点在等待人工外部处理，输入和目标路径分别是什么；
5. handoff 目标是缺失、空文件、已出现、预检通过还是预检失败；
6. 下一步是等待、提交、重跑、放弃 Run，还是处理服务错误；
7. 页面显示是否仍为服务端最新可信数据。

同时必须满足以下约束：

- 后启动的 completed 局部 Run 不得改变操作者已选择的 waiting Run；
- `active_operation=null` 不得被解释为“没有需要观察的 Run”；
- 网络慢响应不得使 GUI 回退到更旧的状态；
- 普通 Run all/Run to 不得在已有非终态 Run 时静默创建重复 Run；
- readiness 预检无论成功或失败都不得改变 Run、NodeRun、Artifact、日志或下游；
- manual external 没有可信外部接口时不得显示伪百分比或 ETA；
- 历史数量增长不得使每次轮询重复传输全部 Run、Artifact 和日志。

## 3. 权威边界

### 3.1 持久权威

`.zniku` SQLite Project 是以下数据的唯一持久权威：

- Project 当前 Graph 与 definitions；
- Run 与其 Graph/definition snapshot；
- NodeRun attempt、持久状态、`progress` fraction、错误和 handoff；
- Artifact、latest result 与 stale 标志；
- attempt 日志路径。

RunSummary、handoff readiness、日志 tail 和细粒度 progress sample 都是从上述数据及当前文件系统派生的
Project Service 读模型，不写回第二套状态。

### 3.2 进程内投影

下列数据允许只存在于当前 Project Service 进程：

- automatic executor 最近一次 `current/total/unit` progress sample；
- 当前后台 `active_operation`；
- Studio 请求顺序和轮询退避状态。

这些数据不是恢复信息。服务重启后，遗留 `running` attempt 仍按上位基线失败为
`failed(reason=interrupted)`；`waiting_external` 原样保留。丢失细粒度 sample 不允许恢复进程，也不影响
已持久化的 `NodeRun.progress`。

### 3.3 Studio 本地状态

`viewRunId` 只属于当前 Studio 页面：

- 不写入 `.zniku`；
- 不回写 Project Service；
- 不改变 Runtime 当前 Run；
- 不决定 Scheduler、reuse 或 stale；
- 只决定 Run selector、画布状态叠加、Inspector、handoff、日志和下一动作绑定到哪个 Run。

## 4. 0.2.1 Project Service wire

### 4.1 版本策略

Project 文件格式与浏览器 wire 是两个独立版本轴，v0.2.1 冻结如下：

| 版本轴 | v0.2.1 决策 |
| --- | --- |
| `.zniku` `PRAGMA user_version` | 保持 `PROJECT_SCHEMA_VERSION = 2` |
| Project 打开行为 | 0.2.0 Project 无 migration 直接打开 |
| Project Service wire | `contract_version` 统一升为 exact `0.2.1` |
| Studio 配对 | 0.2.1 Studio 只接受 0.2.1 Project Service |
| 双版本 wire | 不提供内容协商、兼容字段或第二套 endpoint |
| 产品与 package version | 在 Phase 6 统一收敛到 `0.2.1` |
| NodeDefinition version | 新 v2.7 专用 definitions 使用 exact `0.2.1`；既有 generic definitions 保持 exact `0.2.0` |
| 历史 definition snapshot | 保持原 exact `0.2.0`，不自动改写 |

0.2.0 Studio 遇到 `contract_version="0.2.1"` 必须按现有严格 Schema 失败关闭；0.2.1 Studio 遇到
0.2.0 Service 同样失败关闭并提示版本不匹配。Project 文件可兼容打开不表示浏览器 wire 可以跨版本混用。

打开 0.2.0 Project 不得改写 Project、Graph、definition snapshot 或历史终态 Run。唯一允许的启动期状态
变化是上位基线已经规定的 recovery：遗留 `running` attempt 失败为 `interrupted`，而
`waiting_external` 保留。

### 4.2 路由拆分

v0.2.1 使用以下配对接口，所有成功响应都包含 exact `contract_version: "0.2.1"`：

| 方法与路由 | 职责 |
| --- | --- |
| `GET /api/studio/status?view_run_id=<optional>` | 当前 Project snapshot、有界 RunSummary、后台 operation 与 latest result |
| `GET /api/studio/runs?cursor=<optional>&limit=<1..100>` | 按稳定游标分页读取 terminal 历史 RunSummary；`limit` 默认 20 |
| `GET /api/studio/runs/{run_id}` | 一个明确 Run 的完整 detail 及其引用 Artifact |
| `GET /api/studio/runs/{run_id}/node-runs/{node_run_id}/logs` | 一个明确 attempt 的有界日志 tail |
| `GET /api/studio/runs/{run_id}/node-runs/{node_run_id}/handoff-readiness?probe=false\|true` | 一个明确 handoff 的只读文件预检 |
| `POST /api/studio/command` | 严格 discriminated mutation command |

`status` 不再携带全部历史 `Run.node_runs`、全 Project Artifact 和全部日志。`runs/{run_id}` 只返回该 Run
引用的输入/输出 Artifact；日志和 readiness 按明确 Run/NodeRun 绑定单独读取。服务不得接受客户端提供任意
日志路径、attempt 工作目录或 handoff 输出路径。

成功 mutation command 返回 fresh `StatusEnvelope`；创建新 Run 的 command 通过其中的
`active_run_id` 告知发起页面应切换的 Run。command 响应不重新嵌入完整 Run detail，Studio 随后按明确
`run_id` 拉取 detail。

### 4.3 StatusEnvelope

```text
StatusEnvelope
├─ contract_version: "0.2.1"
├─ project_path: string | null
├─ snapshot: ProjectSnapshot | null
├─ run_summaries: RunSummary[]
├─ next_run_cursor: string | null
├─ active_run_id: string | null
├─ active_operation: ActiveProjectOperation | null
├─ latest_results: LatestNodeResult[]
└─ error: ProjectServiceFailure | null
```

`run_summaries` 按 `created_at` 从新到旧排列；相同时间以 `run_id` 降序作为稳定 tie-breaker。本文的
terminal 精确等于 Run state 为 `completed` 或 `failed`。status 只携带以下
去重并集，不能随终态历史无限增长：

- 全部 `actionable=true` Run；
- 最新 20 个 terminal Run；
- `active_run_id` 对应 Run；
- 可选 `view_run_id` 对应 Run。

`next_run_cursor` 只绑定“最新 20 个 terminal Run”这个连续窗口的末项 `created_at + run_id`；额外纳入的旧
`view_run_id`、active 或 actionable summary 不参与 cursor 计算，避免跳过中间历史。历史 selector 通过分页
endpoint 按需继续加载，并按 `run_id` 去重。cursor 是只由 Project Service 解释的 opaque token；非法
cursor/limit 失败关闭。没有更多 terminal 历史时 cursor 为 null。`view_run_id` 不存在返回 404，不允许悄悄
回退到其他 Run。

`active_operation` 仍只表示当前 Project Service 后台 command。`active_run_id` 只表示最近一次后台 command
绑定的 Run；当 `active_operation` 非空时二者必须同时出现并相互对应。后台 operation 结束后
`active_run_id` 可以保留，供发起 command 的页面定位新 Run，但不得成为普通 refresh 的展示选择权威。

`ProjectServiceFailure` 保留 `code`、`message`，并增加严格的
`related_run_ids: string[] = []`。它只用于把重复 Run 等冲突定向到既有 Run，不接受任意 JSON details。

### 4.4 RunSummaryPageEnvelope

```text
RunSummaryPageEnvelope
├─ contract_version: "0.2.1"
├─ run_summaries: RunSummary[]
└─ next_run_cursor: string | null
```

分页响应只包含 terminal Run，使用与 status 相同的排序/tie-breaker；返回数量不得超过请求 `limit`。第一页从
最新 terminal Run 开始，后续页严格位于 cursor 之后，不包含 cursor 本身。`next_run_cursor` 绑定本页最后一项，
没有更多项时为 null。Studio 把页面与 status extras 按 `run_id` 去重，不能用后加载的历史页抢占
`viewRunId`。

### 4.5 RunDetailEnvelope

```text
RunDetailEnvelope
├─ contract_version: "0.2.1"
├─ run: Run
├─ artifacts: Artifact[]
├─ progress_samples: NodeProgressProjection[]
└─ handoff_contracts: ExternalHandoffContractProjection[]
```

`run` 是已有领域模型的完整只读序列化；`artifacts` 是该 Run 所有 NodeRun 的 input/output Artifact ID 的
去重闭包。任何 Artifact ID 无法解析都视为 Project 数据错误并失败关闭，不得返回半份 detail。

`progress_samples` 只包含当前进程仍可提供细粒度观测的 automatic Python NodeRun；command 与
manual_external 不进入该投影。Python attempt 不存在 sample 时，Studio 从 `NodeRun.progress` 显示最后持久
fraction；command 保持 indeterminate。该字段不改变 `Run` 或 `NodeRun` 模型。

Phase 5 可用性补丁增加只读 `handoff_contracts`，不增加 Runtime 或持久化字段。每项严格包含
`node_run_id / handoff_id / input_artifact_id（可空） / title / fields[{label,value}]`，只绑定该 Run
唯一最新 `waiting_external` attempt。Python 从原 Run snapshot 与已登记 input Artifact metadata
生成有限纯文本行；AV27 显示模型声明、N/FPS、geometry/signal、帧关系和容器/codec/stream 要求。
缺失 metadata 显示“不可用”，不重新 probe、推测数值或改变状态。Studio 只展示这些行，不能把它们
提交成参数、验收通过标志或第二套运行权威。generic/历史 handoff 没有该投影时返回空数组。
Inspector 同时提供已登记 Artifact 的直观 N/FPS/geometry、observed header duration、signal/audio
摘要与折叠的完整 `media_info`；header duration 不冒充 exact timeline duration。

### 4.6 定向日志

日志响应固定绑定 `run_id + node_run_id`：

```text
NodeLogEnvelope
├─ contract_version: "0.2.1"
├─ run_id: string
└─ log: NodeLogProjection
   ├─ node_run_id: string
   ├─ stdout: string
   ├─ stderr: string
   ├─ stdout_available / stderr_available: boolean
   └─ stdout_truncated / stderr_truncated: boolean
```

`node_run_id` 不属于声明的 `run_id` 时返回 conflict，不得回退到同 node 的其他 attempt。每个
stdout/stderr 固定最多读取文件末尾 128 KiB；超出时对应 `*_truncated=true`，按 UTF-8
`errors="replace"` 解码返回。服务器先验证日志文件仍位于对应 attempt 工作目录内。轮询 Run detail 不自动
读取日志；只有
选中 Inspector 或打开日志面板时才读取。

## 5. RunSummary 合同

### 5.1 字段

```text
RunSummary
├─ run_id: string
├─ project_id: string
├─ target_mode: "all" | "selected"
├─ selected_targets: string[]
├─ state: "pending" | "running" | "completed" | "failed"
├─ node_count: integer >= 0
├─ state_counts: RunNodeStateCounts
│  ├─ pending: integer >= 0
│  ├─ running: integer >= 0
│  ├─ waiting_external: integer >= 0
│  ├─ completed: integer >= 0
│  └─ failed: integer >= 0
├─ actionable: boolean
├─ requires_operator_action: boolean
├─ created_at / started_at / ended_at
├─ latest_activity_at
└─ error: RuntimeFailure | null
```

字段语义冻结如下：

- `target_mode="all"` 当且仅当 `selected_targets` 为空；否则为 `selected`；
- `selected_targets` 保留 Run authority 中的稳定顺序，不生成 UI label；
- `node_count` 是执行闭包内不同 `node_id` 的数量；
- `state_counts` 只统计每个 `node_id` 的最高 `attempt`，历史失败 attempt 不重复计数；尚未物化 attempt 的
  pending Run 闭包节点计入 `pending`；
- `node_count` 必须等于五个 count 之和；
- `latest_activity_at` 是 Run 与其全部 NodeRun 的 created/started/ended 时间最大值，至少等于
  `created_at`；
- `error` 直接投影 Run error，不用 NodeRun error 猜测另一套 Run 失败原因。

`actionable` 的 exact 定义为 Run state 是 `pending` 或 `running`。它用于页面重载时选取仍需继续观察的 Run，
不增加 Runtime 状态；合法的 queued pending Run 即使尚无 NodeRun 也必须可选择。

`requires_operator_action` 当最新 attempts 中存在 `waiting_external` 或 `failed`，或 Run 自身为 `failed`
时为 true。它只驱动高优先级提示；它不自动 Submit、不自动 rerun，也不宣称 pending automatic 节点需要
人工处理。

### 5.2 latest attempt 规则

Run 可以因 `rerun_from_start` 为同一 `node_id` 保存多个 attempt。Summary、画布 overlay、Next action 和
handoff queue 均只使用最高 `attempt`；历史 attempt 只在 Run detail/history 中查看。`Run.state=pending` 时，
执行闭包节点允许尚未物化 attempt，并按上一节计入 pending；Run 进入 running 后，每个闭包节点都必须至少有
attempt 1。若最高 attempt 存在重复、attempt 小于 1、running Run 缺 attempt，或 NodeRun 与 Run 绑定不一致，
Repository/DTO 必须失败关闭，不得由 Studio 自行猜测。

### 5.3 展示规则

Run selector 至少显示：

```text
{created_at} · {target_mode/selected_targets} · {state}
{completed}/{node_count} completed · {running} running ·
{waiting_external} waiting external · {failed} failed
```

不得把节点完成数平均成媒体总体百分比。Run summary 的“8/11 completed”和 automatic 节点的“42%”是
两个不同维度，必须分开显示。

## 6. `viewRunId` 选择算法

### 6.1 初始化

打开或创建 Project 后，Studio 按以下顺序确定 `viewRunId`：

1. 若页面仍在同一个 `project_path`，且当前 `viewRunId` 仍存在，保留它；
2. 否则选择 `run_summaries` 中排序最前的 `actionable=true` Run；
3. 若没有 actionable Run，选择排序最前的最新 Run；
4. 若没有 Run，设为 null。

这里的“同一个 Project”以 Project Service 返回的已解析 `project_path` 为准，不以用户输入文本或
`project_id` 猜测。

### 6.2 后续更新

- 用户通过 selector 手动选择后，普通 status/detail refresh 不得改变 `viewRunId`；
- 本页面成功发起会创建新 Run 的 `run_all`、`run_to` 或终态替代 rerun 后，切换到命令响应的
  `active_run_id`；
- `submit_external`、同一 Run 的 rerun 和 `abandon_run` 保持当前选择；
- 其他客户端或旧页面创建的新 Run 只出现在 selector，不抢占当前选择；
- 当前选择意外不存在时，重新执行初始化的第 2–4 步并显示明确提示；
- 切换 Project 必须先使旧 Project 的所有未完成响应失效，再选择新 Project 的 Run。

带 `view_run_id` 的 status 返回 404 时，Studio 只允许立即重试一次不带该参数的 status，再按第 2–4 步选择；
不能无限重试旧 ID，也不能把 404 当成空 Project。

`active_run_id` 不能作为普通 refresh 的 fallback；它只有在本页面确认某个 mutation command 成功后才可
用于切换。

### 6.3 画布、Inspector 与日志绑定

运行视图默认直接渲染 `viewRunId` 的 `graph_snapshot + definitions_snapshot`，NodeRun 只按该 snapshot 的
`node_id` 和最高 attempt 叠加。Designer 仍编辑 Project 当前 Graph；两者不同时必须显示“Run snapshot / 当前
Graph 已变化”，不能把历史状态伪装成当前配置的执行结果。

若 Studio 提供“在当前 Graph 上对照”视图，一个 NodeRun 只有在以下条件全部满足时才能叠加：

- `node_id` 相同；
- 当前选择的是该 Run 的最高 attempt；
- 当前与 Run snapshot 的 node execution signature 精确相同：`type_id`、`definition_version`、严格参数与全部
  直接入边（source node/port、target port、ordinal）一致。

只比较 `definition_version` 不足以识别参数或重连变化。signature 不一致时只显示 `graph changed/stale`，不得
显示 completed/running overlay。Inspector、Artifact、日志和 Submit 必须使用同一
`viewRunId + node_run_id` 绑定；找不到精确绑定时显示不可用，不搜索其他 Run 或 attempt 兜底。

## 7. 实时轮询

### 7.1 single-flight

v0.2.1 不引入 SSE/WebSocket。Studio 使用完成后再调度下一次的 `setTimeout` 循环：

1. 同一个 poll channel 任一时刻最多一个请求；
2. 一轮先刷新 status；只在所选 summary/detail 发生变化、所选 Run 非终态或用户显式刷新时读取 Run detail；
3. 若所选 Run 的最新 attempts 含 waiting handoff，再执行 `probe=false` readiness；
4. 上一轮全部结束后才安排下一轮；
5. 页面卸载、Project 切换或 `viewRunId` 改变时使旧轮次失效。

禁止使用可能重叠的 `setInterval`。日志按选中 attempt 独立按需刷新，不阻塞主 Run 轮询。

### 7.2 轮询条件与频率

| channel 与条件 | 下一轮目标间隔 |
| --- | --- |
| status：`active_operation != null` 或任一 RunSummary 含 running attempt | 750 ms |
| status：任一 RunSummary actionable，但都只停在 waiting/pending | 1,500 ms |
| status：没有 active operation 且没有 actionable Run | 5 s 低频 idle discovery |
| selected detail：所选 Run 含 running | 750 ms |
| selected detail：所选 Run 非终态且只停在 waiting/pending | 1,500 ms |
| selected detail：所选 Run 已终态 | 停止定时 detail，只在 status 摘要变化或用户显式刷新时读取 |
| 任一请求失败 | 750 ms、1.5 s、3 s、5 s，之后保持 5 s |

active operation 从非空变为空时必须额外执行一次不等待间隔的 final refresh。即使 operation 在线程到达
`waiting_external` 后结束，Project 中仍有 actionable Run，status 轮询也只会降频而不会停止。操作者即使
手动查看 terminal 历史 Run，也不能停止其他 waiting/running Run 的 status 与全局 Next action 更新。

### 7.3 乱序防护

v0.2.1 不在 Project 数据库增加 revision，也不伪造全局事务序号。Studio 对每个资源 channel 维护：

- `projectGeneration`：打开、创建、关闭或替换 Project 时递增；
- `requestSequence`：发出 status、Run detail、readiness 或 log 请求时分别单调递增；
- `lastAcceptedSequence`：每个 channel 最近接纳的响应序号。

响应只有在 generation 仍相同、资源 ID 仍相同且 sequence 大于该 channel 的
`lastAcceptedSequence` 时才可写入状态。Mutation command 开始时使相关 poll generation 失效，成功或失败
后从 fresh status 重新建立轮询。不同资源不得共享一个会误丢有效响应的 sequence。

即使 response sequence 更新，同一个 `node_run_id + attempt` 的 determinate progress 也不得低于页面已接纳
的值。若新 payload 对同一 attempt 报告 progress 回退，Studio 保留最后可信值、标记合同边界错误并立即重新
inspect；只有出现更高 attempt 时才建立新的 progress 生命周期。Repository 本应在写入时拒绝回退，这一
客户端检查用于防止 mock、代理缓存或错误 Service 投影把可见进度倒退。

status 与 Run detail 是两个读事务，节点可能恰好在二者之间推进，因此同一轮内允许 detail 比 summary 更新。
画布与 Inspector 以 detail 为准，selector 以 summary 为准；发现计数暂时不一致时不得拼接另一 Run 的数据，
下一轮自然收敛。

### 7.4 网络失败

status/detail/readiness/log 各自是独立资源 channel；任一读取失败时：

- 保留最后一次可信数据；
- 只标记该 channel `stale/offline` 和其最后成功刷新时间；
- status stale 时禁用所有 mutation；detail stale 只禁用依赖该 Run/NodeRun 的 Submit、rerun 和 abandon；
  readiness stale 只禁用 Submit；log stale 不禁用与日志无关的 mutation；
- 每个 channel 独立按上表退避，不拉长其他健康 channel 的间隔；
- 成功后清除 stale 标记并恢复正常频率。

网络错误不得清空 Run、把节点改成 pending、或把旧的 completed 状态重新动画一次。

## 8. External handoff readiness

### 8.1 读模型

```text
ExternalHandoffReadiness
├─ contract_version: "0.2.1"
├─ run_id: string
├─ node_run_id: string
├─ handoff_id: string
├─ checked_at: UTC timestamp
├─ probe_requested: boolean
├─ ready_for_submit: boolean
└─ targets: ExternalOutputReadiness[]
   ├─ port_id: string
   ├─ ordinal: integer | null
   ├─ path: string
   ├─ state: "missing" | "empty" | "present" | "probe_passed" | "probe_failed"
   ├─ size: integer | null
   ├─ mtime_ns: integer | null
   └─ message: string | null
```

readiness 只能检查 NodeRun 已持久化 handoff 中的 server-declared target；请求不得携带或替换 path。
`targets` 保留 handoff 的 port/ordinal 顺序。`size`、`mtime_ns` 只是本次只读观测，不成为 Artifact 身份或
防篡改证明。

### 8.2 target state

| state | exact 含义 |
| --- | --- |
| `missing` | 声明路径不存在 |
| `empty` | 路径是普通文件但 size 为 0 |
| `present` | 普通非空文件存在，且本次 `probe_requested=false` |
| `probe_passed` | 本次 `probe_requested=true`，最小媒体/声明校验与节点 validator 均通过 |
| `probe_failed` | 路径不可读、不是普通文件、probe 失败或节点 validator 失败 |

`missing` 和 `empty` 即使 `probe=true` 也保持原状态，便于 UI 提供准确动作。一个 handoff 只有
`probe_requested=true`、所有 target 都为 `probe_passed`，并且 NodeRun 仍是最新
`waiting_external` attempt 时，`ready_for_submit` 才为 true。

环境轮询使用 `probe=false`，只做低成本存在性、regular-file 与 size 检查。用户点击
“Validate and submit”时先使用 `probe=true`；只有返回 `ready_for_submit=true`，Studio 才发送正式
`submit_external` command。

Studio 必须显示 target 的服务端 `message`，不能把 codec、帧数等具体失败原因缩成 `probe_failed`。
页面独立保留精确 `run_id/node_run_id/handoff_id` 的“上次完整预检失败”及检查时间，普通 status 或
`probe=false` 刷新不得清掉诊断。它明确标为历史检查结果，不冒充当前 readiness，也不参与 Submit
资格判断；下一次显式完整检查开始、该 handoff 终止/被取代或 Project 切换时清除，不持久化到工程。
等待时长从 NodeRun `started_at` 计算；`created_at` 只是缺失时 fallback，不能把排队或上游执行时间
冒充人工等待时间。

### 8.3 无副作用保证

`inspect_external_readiness(run_id, node_run_id, probe)` 必须是纯读取入口。无论结果为何，都不得：

- 更新 Run/NodeRun state、progress、时间戳、error 或 handoff；
- 创建 NodeResult 或登记 Artifact；
- 创建、删除、移动或截断 target；
- 创建 attempt/log 目录或写日志；
- 触发 Scheduler 或下游；
- 更新 latest result 或 stale 状态。

readiness 的 `missing`、`empty`、`present` 和 `probe_failed` 都是 HTTP 200 的业务读模型，不是 NodeRun
失败。查询对象不是当前 Run 的最新 `waiting_external` attempt、Run/NodeRun/handoff 绑定不一致或请求对象
不存在时，才返回稳定 404/409 failure。

### 8.4 Submit 的重新校验

正式 0.2.1 command 绑定：

```json
{
  "operation": "submit_external",
  "run_id": "...",
  "node_run_id": "...",
  "handoff_id": "..."
}
```

Submit 不信任较早的 readiness。Runtime 必须重新验证最新 attempt、handoff identity、文件存在/非空、
声明媒体流和节点 validator，以关闭检查与提交之间的变化窗口。若文件在预检后发生变化并导致正式提交失败，
本 attempt 按既有语义收敛为 `failed(reason=external_submission_invalid)`，不登记 Artifact、不触发下游；
操作者只能从头创建新 attempt。

## 9. ProgressReporter 合同

### 9.1 写入边界

`PythonAdapterContext` 在 automatic Python adapter 执行期间提供只写 `ProgressReporter`。Reporter 由
Runtime 创建并绑定唯一 `run_id + node_run_id + attempt`；adapter 只能上报测量值，不能访问 Repository、
修改其他 NodeRun 或读取其他 Project 状态。

概念接口如下：

```text
report(
    fraction: float,
    *,
    current: integer | null = null,
    total: integer | null = null,
    unit: "frames" | "bytes" | "microseconds" | "items" | null = null,
) -> None
```

规则如下：

- `fraction` 必须是有限数且位于 `0.0..1.0`；
- 同一 attempt 的 fraction 只能单调不减；
- `current/total/unit` 必须全部出现或全部为 null；
- 出现时 `0 <= current <= total` 且 `total > 0`，fraction 必须与 `current/total` 在实现冻结的数值容差内
  一致，absolute tolerance 固定为 `1e-9`；
- reporter 只在绑定的最新 `running` automatic attempt 生命周期内有效；
- manual_external 不获得 reporter；
- command executor 第一版只有定义了明确机器可读协议时才可获得 determinate reporter，不能解析任意
  stdout 文本猜进度。

非法、非有限、越界、回退或单位组合错误的 sample 以稳定 `E_PROGRESS_*` executor contract error 失败
关闭；同步 adapter 因该错误退出时，本 attempt 收敛为 `failed(reason=execution_error)` 并保留此前最后可信
progress。attempt 终态后的迟到 callback 被拒绝且不得改变终态、Artifact 或下游。

### 9.2 持久化与限频

Reporter 接受的每个 sample 都可以更新进程内 `NodeProgressProjection`，但 SQLite 只在以下任一条件满足时
调用现有 `RuntimeRepository.update_progress()`：

- 第一个可信 determinate sample；
- fraction 确有增加，且距离上次持久化至少 400 ms；
- fraction 比上次持久化至少增加 `0.01`；
- executor 正在收敛到终态。

completed 必须在终态事务中精确写为 `1.0`；failed 保留最后一次已持久化可信 fraction，不能伪装为
100%。若进程内 sample 比最后持久值更新，失败收敛前先 flush 该 sample；flush 自身失败时按 Repository
失败语义结束 attempt，不得声称成功。

该策略不增加 SQLite 字段，Project Schema 仍为 2。

兼容打开的 0.2.0 历史 completed NodeRun 允许 `progress=null`，Studio 直接按 completed state 展示完成，
不得为了补写 `1.0` 改写历史。这里的 exact `1.0` 要求适用于 v0.2.1 新执行并收敛的 attempt；Phase 6
收敛模型时也必须保留旧值的读取兼容。

### 9.3 读投影

```text
NodeProgressProjection
├─ node_run_id: string
├─ fraction: float
├─ current: integer | null
├─ total: integer | null
├─ unit: "frames" | "bytes" | "microseconds" | "items" | null
└─ observed_at: UTC timestamp
```

`fraction` 必须不小于同一响应中持久化的 `NodeRun.progress`。`current/total/unit` 全有或全无；该投影在
Project Service 重启后允许消失。Studio 展示优先级为：

1. 当前进程 determinate projection；
2. `NodeRun.progress` 的最后持久 fraction；
3. running automatic 的 indeterminate spinner；
4. waiting_external 的等待时长和 readiness，不显示 0%；
5. completed/reused 显示完成语义，不模拟执行动画；
6. failed 显示最后可信 fraction 和失败原因，不补到 100%。

### 9.4 FFmpeg 进度来源

媒体 adapter 使用受控 `Popen`、argv 数组与 `shell=False`，并通过 FFmpeg
`-progress pipe:1 -nostats` 或独立 progress pipe 读取机器字段。stderr 继续进入 attempt 日志。进度只可
根据节点已知的总帧数、总时长或总字节计算；无法建立可信 denominator 时显示 indeterminate。

不得从人类可读 stderr、任意 stdout 行、文件大小猜测编码百分比。Split、Merge、Encode、Mux 与复制节点
各自如何选择 denominator 属于对应 adapter 合同，但都必须经过同一个 Reporter 单调与限频边界。

## 10. Run command 与失败语义

### 10.1 重复 Run

普通 `run_all` 或 `run_to` 在当前 Project 已存在 state 为 `pending` 或 `running` 的 Run 时，不创建新 Run，
返回：

```text
HTTP 409
code = "E_PROJECT_SERVICE_RUN_CONFLICT"
related_run_ids = 所有非终态 Run，按 created_at 新到旧
```

若 Project Service 此刻仍有后台 command，则继续优先返回现有
`E_PROJECT_SERVICE_BUSY`。这样双击的第二个请求无论发生在 worker 活跃期还是 worker 已停在 handoff 后，
都不会静默创建重复 Run。未来显式并行实验必须使用另行设计的 `start_another_run`，不复用普通按钮。

### 10.2 abandon Run

0.2.1 增加严格 command：

```json
{"operation":"abandon_run","run_id":"..."}
```

第一版只允许没有 automatic `running` NodeRun 的非终态 Run：

- 每个 waiting/pending 节点的最新 attempt 记录为 `failed(reason=cancelled)`；pending 使用同一取消时刻填充
  `started_at/ended_at`，不伪称 executor 已经执行；
- 若 queued pending Run 的闭包仍有未物化 attempt 的 `node_id`，只为这些节点原子创建 attempt 1，再在
  同一取消时刻直接记录为
  `failed(reason=cancelled)`；Run 的原 null `started_at` 与 `ended_at`、以及这些 attempts 的
  `created_at/started_at/ended_at` 都使用该取消时刻；不启动 executor，也不创建 attempt 目录、日志或输出；
- 已 completed 或已 failed NodeRun 保持不变；
- Run 收敛为 `state=failed`、`error.reason=cancelled`；
- 不删除 handoff、Artifact、日志、输出文件或 attempt 目录；
- 有 automatic running attempt 时返回 409，不发送进程终止信号。

abandon 是显式历史收敛，不是 resume、rollback 或 destructive cleanup。

### 10.3 稳定错误表

| 情况 | 结果 |
| --- | --- |
| Project Service/Studio contract version 不匹配 | 客户端 boundary fail closed，禁用 mutation |
| status/detail 响应慢于更新请求 | 按 requestSequence 丢弃，不覆盖 UI |
| status/detail 网络失败 | 保留最后可信状态，标 stale/offline 并退避 |
| Run 不存在 | 404 `E_PROJECT_SERVICE_RUN_NOT_FOUND` |
| NodeRun 不存在 | 404 `E_PROJECT_SERVICE_NODE_RUN_NOT_FOUND` |
| NodeRun 不属于声明 Run | 409 `E_PROJECT_SERVICE_NODE_RUN_OUTSIDE_RUN` |
| readiness 对象不是最新 waiting attempt | 409 `E_PROJECT_SERVICE_HANDOFF_NOT_ACTIONABLE` |
| readiness target 缺失或为空 | HTTP 200 `missing/empty`，无状态副作用 |
| readiness probe/validator 失败 | HTTP 200 `probe_failed`，无状态副作用 |
| Submit 的 handoff identity 过期 | 409 `E_PROJECT_SERVICE_HANDOFF_STALE`，无错误 attempt 冒认 |
| Submit fresh validation 失败 | attempt `failed(external_submission_invalid)`，无 Artifact |
| 普通 Run 与既有非终态 Run 冲突 | 409 `E_PROJECT_SERVICE_RUN_CONFLICT` |
| abandon 遇到 automatic running | 409 `E_PROJECT_SERVICE_RUN_ACTIVE` |
| progress 越界或回退 | automatic attempt `failed(execution_error)`，保留最后可信值 |
| 终态后的 progress callback | 拒绝且无状态变化 |

所有未知字段、未知 operation、未知 readiness state、非法版本和非法数值都必须由 Python/生成 JSON Schema
以及 Studio runtime validation 失败关闭。

## 11. Studio 操作状态

Studio 顶部至少展示：

- 当前 `viewRunId` 与 target；
- Run state 和最新 attempt state counts；
- stale/offline 与最后成功刷新时间；
- `requires_operator_action` 对应的 Next action；
- active operation，但明确标为服务后台 command，而不是当前查看选择。

waiting_external banner 必须可以定位到同一 Run 的最新 waiting NodeRun，并展示：节点友好名称、输入路径、
目标路径、instructions、readiness 和等待时长。Copy path 只复制服务返回路径；“Validate and submit”必须执行
第 8 节的两阶段流程。

mutation 按钮在以下任一条件成立时禁用：

- command 请求在途；
- Project Service active operation 非空；
- 当前 detail/readiness 已 stale；
- selection 与 command 需要的 `run_id/node_run_id/handoff_id` 不完整；
- Graph diagnostics 不允许对应 run command；
- readiness 显示 missing、empty 或 probe_failed。

## 12. Phase 0–2 测试门禁

### 12.1 Phase 0 失败回归

纯合成 fixture 必须包含至少三个 Run：

1. 较早的局部 Source Run：completed；
2. 中间的整图 Run：Source completed、manual transform waiting_external、sink pending；
3. 最新的局部 Source Run：completed，且 `active_run_id` 故意指向该 Run。

回归必须固定：

- 初始化选择最新 actionable Run，而不是最新 completed Run；
- 用户手动选择整图 waiting Run 后，refresh 不改变选择；
- active operation 为 null 但 view Run 非终态时仍轮询；
- 手动查看 terminal Run 时，只要其他 Run actionable，status 仍持续轮询；
- 两个乱序响应只接受 sequence 新的响应；
- 普通 Run all 返回 `E_PROJECT_SERVICE_RUN_CONFLICT` 且 Run 数量不变；
- missing readiness 返回只读模型，Run、NodeRun、Artifact 与文件系统均不变化。

这些测试在 Phase 0 应准确复现并以 strict expected-failure 记录当前缺口；Phase 1 实现后必须转为普通通过，
不得长期保留宽泛 xfail。

### 12.2 Phase 1 验收

- status 只返回 summaries，Run detail、日志和 readiness 定向读取；
- reload、manual select、新 command、其他客户端新 Run 的选择规则全部通过；
- waiting banner 和 handoff queue 不串 Run/attempt；
- readiness 五种状态及无副作用保证全部通过；
- abandon 与重复 Run conflict 通过；
- 0.2.0 Project Schema 2 fixture 无 migration 打开；
- 0.2.0/0.2.1 wire 交叉配对均失败关闭。

### 12.3 Phase 2 验收

- progress 单调、范围、单位组合和终态拒绝通过；
- 受控慢 FFmpeg 至少观测两个位于 0 与 1 之间的值；
- 写入限频可验证，completed 为 1，failed 保留最后值；
- command/unknown executor 没有可信协议时显示 indeterminate；
- manual_external 从不获得 reporter，也不显示伪百分比；
- single-flight、乱序防护、final refresh 和 offline 退避通过。

## 13. 明确不处理

- SSE、WebSocket 或跨进程事件总线；
- Jasna、Topaz 或其他人工 GUI 的内部百分比采集；
- 节点内部 pause/resume/checkpoint；
- Run 并行实验、优先级或资源调度；
- 用户账号、跨设备同步或多用户 view selection；
- 将 progress sample 保存成历史遥测数据库；
- 通过文件增长估计 external handoff 进度；
- 把 AVEnhanceFlow 阶段名称写入 RunSummary、Scheduler 或 Reporter；
- 为 v0.2.0 浏览器保留双版本 Project Service wire。

## 14. 一句话决策

> **v0.2.1 以显式 `viewRunId` 和轻量 RunSummary 稳定展示 Run，以只读 readiness 保护人工提交，以受控
> ProgressReporter 展示可信 automatic 进度；这些都只是现有 Project/Runtime authority 的投影，不增加
> 第二套状态、恢复协议或 Project 数据库版本。**
