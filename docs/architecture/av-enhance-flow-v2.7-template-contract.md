# ZNIKU Studio v0.2.1 AVEnhanceFlow v2.7.0 模板与节点合同

- 状态：**Phase 0 设计已冻结；Phase 3 节点包与通用 Runner plumbing 已实现；Phase 4 template、Project Service 与 Studio 已实现**
- ZNIKU NodeDefinition 版本：`0.2.1`
- 模板 profile：`AVEnhanceFlow 2.7.0`
- 上位架构权威：[`graph-core-baseline.md`](graph-core-baseline.md)
- 通用媒体节点参考：[`media-node-contract.md`](media-node-contract.md)
- Run/readiness wire：[`studio-run-observability.md`](studio-run-observability.md)
- 流程参考权威：`AVEnhanceFlow main@5c2e055` 的
  `docs/v2.7.0-phase-0-baseline.md` 与 `docs/v2.7.0-lightweight-node-runtime-redesign.md`
- 日期：2026-09-01

## 1. 文档定位

本文冻结 ZNIKU Studio v0.2.1 如何把 AVEnhanceFlow v2.7.0 的固定媒体配方表达为：

1. 一组位于独立 namespace 的普通 `NodeDefinition`；
2. 两次 server-side authoring mutation：先生成 preparation Graph，首个 Run 建立 admitted Artifact，再基于这些
   Artifact 展开完整普通 DAG；
3. 只属于这些节点和 template profile 的媒体 validator 与 authoring preflight；
4. 不带 AVEnhanceFlow 业务分支的通用 Runner metadata、output path 与 validator extension plumbing。

本文是 `graph-core-baseline.md` 的下位设计。发生冲突时，必须修改本文或具体实现，不能借 AVEnhanceFlow 的
固定流程扩大 Graph Core、Scheduler、Artifact、Run 或 stale/reuse 的全局语义。

这里的“兼容”严格指：

> **由模板生成的普通 DAG 保留 AVEnhanceFlow v2.7.0 的阶段形状、媒体输出合同、帧关系和原始音频语义；
> 两者不共享 Task、PlanState、NodeState、Artifact 身份、恢复状态或隐藏执行计划。**

## 2. 权威层级与边界

### 2.1 ZNIKU 继续拥有的唯一运行权威

- `.zniku` Project 保存当前 Graph、精确 `NodeDefinition`、Run、NodeRun、Artifact 与日志索引；
- Run 只复制普通 Graph/definition snapshot；
- Scheduler 只按 DAG 依赖和普通端口规则计算 ready 节点；
- NodeRunner 只按 definition 的 executor/validator 执行；
- 节点中断或失败后从头创建新 attempt；
- 已完成且仍有效的上游仍按普通 reuse/stale 规则处理。

任何实现都不得额外创建或读取以下运行 authority：

- AVEnhanceFlow `task.json`、`state.json` 或其 Task/PlanState/NodeState；
- Compiler、Freeze、Revision 或 ExecutionPlan；
- 全局 Chapter/Leaf scope；
- ArtifactSet、Evidence、receipt、authority chain 或 canonical graph digest；
- template 自己的第三套 Run 或 attempt 状态机。

### 2.2 AVEnhanceFlow 只提供参考语义

从固定引用只采用：

- Source、MR、Split、Enhancement、Merge、FI、ProgramEncode、FinalMux 的阶段语义；
- external MR 必须先完成 admission，再规划 Chapter/Leaf 的两段 authoring；
- 正整数分钟 `Nm` 分叶；
- `N→N`、`N→2N-1` 和逐章补尾到 `2N` 的帧关系；
- FFV1、ProRes、HEVC Main10、BT.709、精确时间轴和原始全部音轨规则；
- completed 上游复用、失败节点从头重跑的产品行为。

不引用 AVEnhanceFlow CLI、任务目录、持久状态、Adopt/relink 命令或 fixed-role Runtime 实现。

### 2.3 通用节点与专用节点并存

现有 `zniku.media.*@0.2.0` 节点保持原义。v2.7.0 专用节点统一使用
`zniku.avenhance.v27.*@0.2.1`。只有完整通过本文 profile preflight 的 preparation Graph 或 expanded Graph
才能显示相应的 v2.7.0 compatible 状态。

用户自由修改生成图以后：

- Graph 仍按普通 ZNIKU 规则保存和运行；
- profile 状态必须从当前 Graph 重新派生；
- profile 不兼容不能使 Graph Core 把合法自由 DAG 判成非法；
- server-side expand 不得猜测、删除或覆盖不能精确识别的用户节点。

## 3. 两段 authoring 与引用关系

本文术语均为 template 局部术语，不是 Graph Core 模型：

| 术语 | 含义 | 持久位置 |
| --- | --- | --- |
| `PrepareRequest` | 生成 preparation Graph 的严格请求 | 请求本身不持久化；所需值进入普通节点参数 |
| `ExpandRequest` | 绑定当前 Project 和一个 completed preparation Run 的严格请求 | 请求本身不持久化 |
| `SourceSpec` | 一个物理 source 及其稳定 ordinal | Source/Admission 节点参数和 Edge ordinal |
| `ResolvedChapterPlan` | Python 从 strict selector 与 admitted effective-video N/FPS 生成的有序半开章区间 | Split/Merge/FI/Program 参数 |
| `DerivedLeafPlan` | Python 按 `leaf_duration_minutes` 确定性派生的叶区间 | Split 参数、动态 ports 和 Edge ordinal |
| `stage declaration` | 操作者声明的 `model_name/model_version` | manual 节点参数和 validator media_info |
| `profile preflight` | preparation/expanded Graph 的专用纯检查 | 只返回状态与 diagnostics |

所有 MR 模式和所有 source mode 统一使用两段 authoring：

```text
PrepareRequest
    │ server-side create
    ▼
preparation Graph: SourceProgram(s) → SourceAdmission barrier → MR?
    │ 普通 Run；external MR 由操作者 Submit
    ▼
completed Source / Admission / MR NodeResult + admitted Artifact
    │ ExpandRequest 绑定 current Project + preparation Run results
    ▼
expanded Graph: 保留 preparation nodes，并追加 Split → Enhancement → Merge → FI → Program → Final → Output
    │ 新普通 Run
    ▼
复用仍有效的 completed preparation nodes，只执行新增或 stale 的下游
```

`mr.mode=off` 也不得一步生成完整图。这样 SourceProgram 的一次完整 packet traversal 同时成为运行时 Source
admission，不需要 authoring preview 再做一遍同等扫描。external MR preparation Graph 中不得出现 Chapter、Leaf、
Split 或其下游参数；不能在原 Source 上预冻计划后冒充“基于 admitted MR 输出规划”。

两次 authoring mutation 都只修改普通 Project Graph/definitions。当前处于 preparation 还是 expanded 状态由当前
Graph 派生，不增加 template phase 表、plan 文件或 Runtime 状态。

禁止反向隐藏引用，例如 Runtime 根据 `profile_id` 重建边、Scheduler 按 chapter 查找节点、validator 扫描同 role
的任意 Artifact，或让 TypeScript 提交 Graph/definitions 冒充 Python builder 结果。

## 4. 所有专用节点的共同合同

### 4.1 Identity 与 Schema

- 所有专用 definition `version` 精确为 `0.2.1`；
- NodeInstance 必须精确绑定 `type_id@0.2.1`；
- 参数 Schema 使用 JSON Schema Draft 2020-12，根 object 一律 `additionalProperties: false`；
- 未知字段、未知 enum、非法 rational、非法路径或缺失必填字段默认失败关闭；
- FPS rational 使用已约分的正整数文本 `numerator/denominator`，分母不得为零；exact time 使用
  `str(Fraction)` 形式的正整数秒或已约分正有理秒；两者都不接受 float；
- frame range 一律为零基半开区间 `[start_frame, end_frame)`；
- ordinal 从 `0` 开始连续；显示 label 不承担身份或排序语义。

这里的 ordinal 是 ZNIKU Graph/Artifact 的零基 ordinal。参考实现内部若使用一基业务编号，只允许在
adapter 的显示或临时命名边界显式转换；不得把一基编号写回 Graph edge ordinal 或混作排序 authority。

`2.7.0` 是外部流程 profile 版本，`0.2.1` 是 ZNIKU NodeDefinition 精确版本，两者不能共用一个 version 字段。

### 4.2 端口与 Artifact

- 只使用 Graph Core 已有 `MediaFile`、`VideoFile` 与 `DataFile` typed ports；
- 只使用 `one` 与 input-only `ordered_many`；
- 每个成功 attempt 产生新的普通随机 Artifact ID；
- `Artifact.media_info` 保存 probe/producer 与节点 validator 建立的轻量 summary；
- frame count、canonical FPS、geometry、signal 和 stage declaration 作为节点局部 metadata 传播；
- 路径、size、mtime 和 SHA 都不是内容身份；
- 同一路径可由 Source 的两个逻辑 output 引用，但仍是两个精确 port binding。

### 4.3 通用 Runner plumbing

以下是 v0.2.1 的通用执行 plumbing，不得按 `zniku.avenhance` type ID 分支，也不改变 Scheduler 或 Graph
cardinality。

#### 完整且唯一的 RunnerInput metadata 路径

`RunnerInput` 除 input binding 外，必须平铺携带所绑定 Artifact 的完整只读 metadata：

```text
port_id / input_ordinal
artifact_id / kind / path
producer_node_run_id / producer_port_id / artifact_ordinal
frame_range / media_info / size / mtime_ns
```

`RuntimeService` 只能从 Repository 中已经登记且与 Run snapshot 精确绑定的 Artifact 构造这些字段；adapter 和
validator 不接受客户端补充 metadata。所有下游 adapter/validator 统一读取 `RunnerInput.media_info`，不得再从
`NodeResult.media_summary`、业务 sidecar、path 重查或另一份专用 input summary 获取同一事实。这样 Admission、
Split、Merge、Program 和 Final 可以复用直接输入的已登记 summary，也不能修改上游 Artifact。

#### producer_metadata → validator → media_info extension

`PythonAdapterResult` 增加默认空的严格 per-port `producer_metadata`：

```text
{output_port_id: JSON object}
```

对本 profile 的 automatic 媒体 producer，该 object 的 exact Schema 固定为：

```json
{"output_frames": 123}
```

`output_frames` 必须是受控进程成功退出前最后的机器可读 progress 计数，类型为严格正整数；
bool、0、负数、缺失值、未知 key 或额外 key 均失败关闭。通用 Runner 只验证 JSON 和 port shape，
`zniku.avenhance.v27` validator 才解释该 exact object。

它只承载本次受控 producer 的实测小型事实，例如 FFmpeg progress 的 `output_frames`；不能携带 path、Artifact
ID、任意对象或客户端声明。Runner 在调用 validator 前完成以下通用流转：

1. 严格解析 `producer_metadata`：key 只能是本次实际声明并产生的 output port，每 port 最多一个 object；value
   必须是严格 JSON，拒绝重复 key、非字符串 key、非有限数、`Path`、bytes 或任意 Python object；
2. 默认 probe 仍独立建立 `ValidatedOutput.media_info`；原始 producer metadata 只放入对应
   `ValidatedOutput.producer_metadata`，不得在 validator 前自动写入 Artifact；
3. `NodeValidatorContext.outputs` 将两者一并交给 validator；validator 必须核对 producer 数值、计划、probe 与
   input `RunnerInput.media_info`；
4. validator 通过后，用 `NodeValidatorResult.media_info_extensions` 返回已经验证和规范化的 per-port metadata；
   Runner 才将其原子并入对应 `RunnerArtifact.media_info`。本 profile 的 exact 持久路径统一为
   `Artifact.media_info["zniku.avenhance.v27"]["frame_count"]`，值为严格正整数；不写顶层
   `frame_count`。

command executor 与 manual submission 在 v0.2.1 不得伪造 `producer_metadata`，默认 `{}`；它们只能使用默认 probe
及 validator 明确允许的 source-level fallback。`PythonAdapterResult.media_summary` 仍只是 NodeResult 诊断摘要，
不能成为 Artifact 或下游 input authority。

`NodeValidatorResult` 的 `media_info_extensions` 形状为：

```text
{output_port_id: {namespace: JSON object}}
```

- output port 必须属于本次 request 的声明 outputs；
- namespace 必须是非空、稳定、点分隔的 identifier；本 profile 只写 `zniku.avenhance.v27`；
- value 必须是严格 JSON object，拒绝非有限数、Path、bytes、任意 Python object 与重复 key；
- extension 只能新增尚不存在的 namespace，不能覆盖默认 probe 或其他 validator 已建立的 key；
- 未知 port、namespace 冲突、未验证的 producer key 或非法 JSON 以稳定 Runner
  configuration/validator error 失败整个 attempt；
- 只有 validator 整体 `passed=true` 后才合并 extension；raw producer metadata 不单独持久化。

该机制只补充节点已验证的局部 metadata，不让 adapter/validator 创建 Artifact、改变 path 或写第二份 result。

#### executor declaration 的 per-port output paths

Graph Core 冻结的 `NodeDefinition` 顶层字段保持不变，不增加顶层 `output_paths`。三个受信 executor spec 均增加
同形的可选 `executor.output_paths[]` declaration；每项与现有 runtime
`OutputPathSpec(port_id, relative_path)` 同构：

- 只能引用本 definition 已声明的 output port，且每个 port 最多一项；
- `relative_path` 必须是非空相对路径，不得包含 NUL、drive、root、UNC、`..` 或解析后的父目录逃逸；
- 路径是 definition 内 executor 的受信 execution declaration，不得来自 Node 参数、PrepareRequest、
  ExpandRequest、Studio 或任意 TS JSON；
- 动态 Split definition 的 output ports 与 `executor.output_paths` 必须由同一个 Python builder 一次生成；
- Project Service 泛化读取 `definition.executor.output_paths`，转换后原样传给现有
  `NodeExecutionRequest.output_paths`；Runtime 不按业务 type ID 分支；
- Runner 继续在 `<attempt>/outputs` 下 resolved containment，先验证 parent containment，再创建受控父目录；
- command/manual/Python adapter 都使用同一分配结果，不能自行以业务 type ID 替换后缀。

#### external output 例外

除以下两个受信 Python adapter 边界，所有成功 output 都必须位于 attempt 工作目录：

1. `SourceProgram` 可用 `ProducedOutput(allow_external=true)` 登记操作者选定 Source 的只读外部引用；
2. 通用 `OutputFile` 可在显式 `copy/reference + target_path + overwrite` 合同下登记发布路径。

这两个例外不进入 NodeDefinition 参数权限，也不授权 manual submission、MR、Split、Enhancement、Merge、FI、
Program 或 Final 写出 attempt。失败清理永远不得删除 Source、上游 Artifact 或发布目标。

### 4.4 执行与安全

- Source、Admission、Split、Merge、ProgramEncode、FinalMux 使用仓库内受信 Python adapter；
- MR、Enhancement、FI 固定为 `manual_external`；
- 参数中不得接受 executable、argv、shell command、Python expression、filter script 或可执行代码；
- FFmpeg/FFprobe 使用结构化 argv 与 `shell=False`；
- 任一 partial、incoming 或验证失败文件都不是 Artifact；
- 删除只允许落在已解析验证的 attempt 工作目录；
- external handoff output 仍位于独立 attempt 的受控 output path。

### 4.5 最小验证与 frame count

所有新接收或新生产的媒体在登记前至少满足：

1. 文件存在、是普通文件且非空；
2. 受控进程成功退出（如适用）；
3. FFprobe 能识别声明媒体流；
4. 节点轻量 validator 通过。

默认不计算 SHA，不生成 Evidence/receipt，不做 raw-frame、packet payload 或 PCM exact 证明。exact frame count
authority 顺序为：

1. 当前直接输入优先复用
   `RunnerInput.media_info["zniku.avenhance.v27"]["frame_count"]` 中已经验证的 exact N；
2. 本次 automatic producer 优先使用经 strict `producer_metadata` 传入并由 validator 闭合的
   `output_frames`；
3. 本次新 external output 才可使用 allowlisted container/codec 的可信 `nb_frames/NUMBER_OF_FRAMES`；
4. 仍缺失时只允许该节点定义的一次 source-level packet/frame traversal；
5. 仍无法建立 exact N 时返回 `E_AV27_FRAME_COUNT_UNKNOWN`，不得用 duration 近似。

AtomicSplit 的特殊计数规则见 §7.4：成功 producer 的每个 leaf count 直接取 DerivedLeafPlan，不做逐 leaf
post-scan；缺 producer count 时最多对对应 effective-video input 做一次 source-level fallback。

### 4.6 进度

- automatic adapter 只上报 FFmpeg producer 或确定性子步骤的实测单调进度；
- completed 固定为 `1.0`，failed 保留最后可信值；
- MR、Enhancement、FI 不伪造外部工具百分比或 ETA；
- manual node 只展示 `waiting_external`、等待时长、handoff 路径和只读 readiness。

## 5. Server-side template 请求与 authoring 协议

### 5.1 PrepareRequest

公开 prepare 请求只接受操作者输入，不接受 probe summary、Graph 或 definitions：

```text
PrepareRequest
├─ profile_version = "2.7.0"
├─ project_path / project_id / project_name
├─ source_mode: program | pre_chaptered
├─ sources[]
│  ├─ source_path (absolute)
│  ├─ source_ordinal
│  └─ chapter_label (pre_chaptered required; program forbidden)
└─ mr
   ├─ mode: off | external
   ├─ model_name (external required; off forbidden)
   └─ model_version (external required; off forbidden)
```

所有 Pydantic 请求模型必须 strict、`extra="forbid"`，拒绝隐式字符串转数字。`sources` 只做路径解析、regular
file、非空、ordinal 和 source_mode 结构检查；preview/create 不执行完整 packet traversal，也不接收
`source_admissions`、expected N/FPS/geometry/signal、chapters、leaves 或 leaf duration。

SourceProgram 在首个普通 Run 中建立实际 N/FPS/geometry/signal/audio summary。`SourceAdmission` 只消费这些
已登记 metadata 做全 Source 闭合，并输出普通 gate Artifact；这避免 authoring probe 与 SourceProgram 重复扫描。

### 5.2 ExpandRequest、ChapterSelector、ChapterPlan 与 LeafPlan

expand 只作用于当前已经打开的 Project，不接受 project path、Graph、definitions、nodes、edges 或 Artifact
metadata：

```text
ExpandRequest
├─ profile_version = "2.7.0"
├─ preparation_run_id
├─ chapter_selector                   (program required; pre_chaptered forbidden)
│  ├─ {mode: single}
│  ├─ {mode: exact_frames, frames: [positive integer, ...]}
│  └─ {mode: exact_times, times: [positive canonical rational seconds, ...]}
├─ leaf_duration_minutes              (strict positive integer; bool forbidden)
├─ enhancement
│  ├─ model_name                      (required)
│  ├─ model_version                   (optional)
│  └─ actual_scale_factor             (strict positive integer)
├─ frame_interpolation
│  ├─ model_name                      (required)
│  └─ model_version                   (optional)
├─ program_encode
│  └─ encoder: gpu | cpu
└─ publication
   ├─ output_root                     (absolute existing directory)
   ├─ title / year
   └─ overwrite                       (explicit boolean)
```

`preparation_run_id` 必须属于当前 Project，其 snapshot 必须精确对应当前可识别的 preparation 部分，并且当前
latest attempts 的所有 Source、Admission 和可选 MR 都已 completed。expand 从 Repository 读取该 Run 的 exact
NodeResult/Artifact；不得信任请求提供 path、N、FPS 或 media_info。

`chapter_selector` 是 strict discriminated union，每个 variant 根 object 都 `additionalProperties: false`：

- `single` 只能有 `mode`，Python 生成唯一 `[0,N)` Chapter；
- `exact_frames.frames` 是非空、严格递增且唯一的正整数数组；每个值是下一章的零基首帧，必须满足
  `0 < F < N`；
- `exact_times.times` 是非空、严格递增且唯一的 canonical `str(Fraction)` 字符串数组；Python 以 admitted
  canonical FPS 计算 `floor(T × FPS + 1/2)`，即正数 half-up 到最近帧；映射后边界仍须严格递增且满足
  `0 < F < N`；
- `near`、hint、search window、black handle 及任意客户端 range 都不存在于 v0.2.1 Schema；出现即
  `E_AV27_TEMPLATE_SELECTOR_UNSUPPORTED`，不得退化为时间乘 FPS。

`program` 的 N/FPS 只能取 admitted effective-video `Artifact.media_info`。server 把解析后的
boundaries 与 `0/N` 组合成有序、无重叠、无缺口、完整覆盖 `[0,N)` 的右开 ranges；客户端不提交
`chapters[]`、label、ID、ordinal 或 range。external MR 因而始终在 admitted MR Artifact 上规划，MR off 则在
admitted SourceProgram video Artifact 上规划。

`pre_chaptered` 显式拒绝 `chapter_selector`：Python 按 admitted effective-video/source ordinal 自动派生一 Source
一 Chapter，range 为各自 `[0,N_source)`，label 来自已经严格验证为连续的 SourceSpec chapter labels。

两种 source mode 的 chapter identity 都由同一个 Python builder 生成：ordinal 从 `0` 连续；program label 使用
Excel 风格 `A…Z/AA…`，pre_chaptered 沿用 SourceSpec label；`chapter_id` 固定为
`chapter-{ordinal + 1:04d}`。相同 preparation Artifact `media_info` 与 selector 必须得到相同 ranges、labels、IDs
和 ordinals；这些派生值进入普通节点参数，不构成额外 plan authority。

Python builder 把规范化 selector 与 ResolvedChapterPlan 一并复制到 AtomicSplit 普通 Node 参数，供下一次
profile preflight 重算核对；不新增 plan 文件、digest 或第三套 authority。

请求模型不存在 `leaves` 字段。Python 从 ResolvedChapterPlan 和 `leaf_duration_minutes` 唯一派生 LeafPlan：

```text
frames_per_leaf = round(Fraction(fps_numerator, fps_denominator)
                        * leaf_duration_minutes * 60)
```

这里使用 Python 对 exact `Fraction` 的 nearest-integer、ties-to-even 规则，与固定引用的 v2.7.0 `Nm` planner
一致；不得使用 float。每章从 `start_frame` 起按 `frames_per_leaf` 生成连续右开 range，最后一 leaf 截到
`end_frame` 并吸收余数。chapter/leaf ID、ordinal、全节目 port 顺序均由 Python 确定性生成；每章至少一个 leaf。

leaf ordinal 在全节目从 `0` 连续，`leaf_id` 固定为 `leaf-{global_ordinal + 1:04d}`，并与 AtomicSplit
`leaf-0001...leaf-NNNN` output port 一一对应。

`00:30:00`、`10m` string、任意 `chapters[]/leaves[]` 或客户端派生 range 均不是请求字段。`--near` 黑场/关键帧
planner 不属于 v0.2.1。

### 5.3 stage declaration

- external MR 的 `model_name`、`model_version` 都是非空必填；
- Enhancement/FI 的 `model_name` 非空必填，`model_version` 可缺失；
- stage-wide version 的缺失/具体值必须全节目一致；builder 从一个 stage object 复制到该 stage 全部节点；
- 不存在 `tool`、`tool_version` 或第二套软件版本 authority；
- `actual_scale_factor` 是全节目正整数；实测 `1` 可省略并自动规范为 `1`，`>1` 必须显式；
- validator 只记录 `operator_declared=true`，不能声称从像素证明实际模型。

### 5.4 Project Service endpoints 与 commands

所有请求/响应使用 exact Project Service wire `0.2.1`：

| HTTP 边界 | 语义 |
| --- | --- |
| `POST /api/studio/templates/av-enhance-v27/preview` | `action=prepare|expand` 的只读 server-side preview |
| `POST /api/studio/command` + `operation=create_av_enhance_v27` | 原子创建 preparation Project |
| `POST /api/studio/command` + `operation=expand_av_enhance_v27` | 原子追加或重建 expanded 子图 |

preview 返回 `phase`、Python 生成的 Graph/definitions/layout 和 profile diagnostics，但不创建 Project、Run、目录
或 Artifact。Studio 只能展示响应；mutation command 只接受严格 PrepareRequest/ExpandRequest，明确拒绝客户端
回传 `definitions`、动态 ports、`output_paths`、Graph 或 preflight pass 标志。

create 满足：

- `.zniku` target 不得存在；任何请求字段都不能授权覆盖既有 Project 或工程文件；
- parent 必须预先存在；
- Graph/definitions/Schema/Core/profile 全部在写入前验证；
- 使用同目录临时 SQLite 和 no-replace 原子发布；失败或竞争时目标保持不存在/原样，不留下半份 Project；
- `mr.mode=off` 也只创建 SourceProgram + SourceAdmission preparation Graph。

expand 满足：

- 只绑定当前 Project session 和请求的 preparation Run results；
- effective video 是 external MR output 或 MR-off SourceProgram `video` output；
- AtomicSplit 参数保存按 source ordinal 排列的 `planned_effective_video_artifact_ids` 和
  `planned_admission_artifact_id`；
- preview 后到 mutation 前任一 graph、latest result、Artifact ID 或 metadata 变化都返回
  `E_AV27_EXPAND_STALE`，Project 不变；
- 首次 expand 只追加下游；再次 replan 只允许当前 Graph 精确匹配 server 生成的 expanded profile，并原子替换
  expansion 子图；Graph 已自由编辑时返回 `E_AV27_EXPAND_GRAPH_DIVERGED`，不得猜测删除用户节点；
- 成功保存是单个 ProjectStore transaction；失败不留下部分 nodes/edges/definitions。

### 5.5 Artifact 变化与 replan

expand 后的第二个 Run 通过普通 reuse 复用 completed Source、Admission 和 MR。新增 outgoing edges 不改变这些节点
的 definition、参数或直接输入，因此不应强迫它们重跑。

若 Source、Admission 或 MR 产生新的 Artifact ID：

1. Studio/profile preflight 把 expanded profile 标记为 `replan_required`；
2. AtomicSplit 在启动 producer 前比较 RunnerInput 与 planned Artifact IDs，返回
   `E_AV27_PLAN_INPUT_CHANGED`，不得沿用旧 Chapter/Leaf range；
3. 操作者重新执行 expand，Python 从新 Artifact `media_info` 重新派生 Chapter/Leaf 和动态 definition；
4. 普通 stale 继续向下传播；历史 Run snapshot 不被改写。

Artifact ID binding 是普通 Node 参数与 direct-input 检查，不是 digest、Evidence 或额外 plan authority。

## 6. 冻结的 NodeDefinition 目录

所有专用 definition version 均为 `0.2.1`，所有列出的 input 都是 `required=true`：

| type_id | input ports | output ports | mode | managed output path |
| --- | --- | --- | --- | --- |
| `zniku.avenhance.v27.source_program` | 无 | `video: VideoFile`、`source_media: MediaFile` | automatic | 外部只读 Source 例外 |
| `zniku.avenhance.v27.source_admission` | `sources: ordered_many<MediaFile>` | `gate: DataFile` | automatic | `admission.json` |
| `zniku.avenhance.v27.mosaic_restoration.external` | `video: VideoFile`、`gate: DataFile` | `video: VideoFile` | manual_external | `mr.mkv` |
| `zniku.avenhance.v27.atomic_split.leaves.<count>` | `videos: ordered_many<VideoFile>`、`gate: DataFile` | `leaf-0001...leaf-NNNN: VideoFile` | automatic | `leaves/leaf-NNNN.mkv` |
| `zniku.avenhance.v27.enhancement.external` | `video: VideoFile` | `video: VideoFile` | manual_external | `enhancement.mov` |
| `zniku.avenhance.v27.merge_video` | `videos: ordered_many<VideoFile>` | `video: VideoFile` | automatic | `merge.mov` |
| `zniku.avenhance.v27.frame_interpolation.external` | `video: VideoFile` | `video: VideoFile` | manual_external | `fi.mov` |
| `zniku.avenhance.v27.program_encode` | `chapters: ordered_many<VideoFile>` | `video: VideoFile` | automatic | `program.mp4` |
| `zniku.avenhance.v27.final_mux` | `video: VideoFile`、`sources: ordered_many<MediaFile>`、`gate: DataFile` | `media: MediaFile` | automatic | `final.mkv` |

表中 managed output path 均位于对应 `definition.executor.output_paths`；它不是 `NodeDefinition` 顶层字段。

SourceAdmission 的 `gate` 必须直接连接到每个 MR（若存在）、AtomicSplit 和 FinalMux 的同名 one input。它只是
普通 `DataFile` dependency barrier；不是 Evidence、receipt、授权令牌或 Core gate 类型。

帧关系总表：

| 节点 | 输入 N | 输出合同 |
| --- | --- | --- |
| SourceProgram | 实际 Source | `video/source_media` 共享已解析视频 N |
| SourceAdmission | ordered Source media | 不改变媒体；输出普通 gate DataFile |
| MR | N | N |
| AtomicSplit | 每 effective Source `N_s` | 该 Source DerivedLeafPlan 总和 `N_s` |
| Enhancement | leaf N | N |
| Merge | leaves `N_1...N_k` | `ΣN_i` |
| FI | chapter N | `2N-1` |
| ProgramEncode | 每章 FI `2N_i-1` | 每章补尾到 `2N_i`，节目总数 `2ΣN_i` |
| FinalMux | Program N | video N；原音轨不改变视频 N |

不同章节的多帧/少帧不得通过节目总数抵消。

### 6.1 AtomicSplit 稳定 shape identity

- type ID 为 `zniku.avenhance.v27.atomic_split.leaves.<decimal-count>`；
- count 是无前导零正十进制；
- output port 按全节目 leaf 顺序固定为 `leaf-0001`、`leaf-0002`……；
- 同 count 必须生成语义相同的 ports 与 `executor.output_paths` per-port relative paths；
- 不使用 digest、随机 UUID、Artifact ID、chapter label 或路径构造 type ID；
- Node 参数 `segments[]` 把固定 port 映射到 source/chapter/leaf/range；
- planned Artifact IDs 只进入 NodeInstance 参数，不进入 type identity。

## 7. 节点详细合同

### 7.1 SourceProgram

职责：对一个物理 Source 建立 canonical 媒体 summary，同时提供视频支路与原始媒体支路。

参数只包含 `source_path`、`source_ordinal` 和可选显示 label；不得包含 authoring 预扫描得出的 expected N/FPS/
geometry/signal。

合同：

- 只读引用 Source，不复制、移动、覆盖或摘要；
- `video` 和 `source_media` 可指向同一外部路径，但分别登记 typed output binding；
- 只接受恰好一条视频和任意数量音频；拒绝 subtitle、attachment、data、unknown stream 与内置 chapters；
- coded geometry 必须为 16:9，SAR 缺失或 1:1；解析 BT.709 SDR limited；
- 每个 Source 最多一次完整 packet timeline traversal，同时取得 exact N 与 cadence；DTS coverage 不足 99% 时
  才使用 decoded presentation timeline fallback；
- 98% medium-confidence 可带 warning，low confidence 或 header 矛盾返回 `E_AV27_SOURCE_FPS_AMBIGUOUS`；
- media_info 同时保存 compact video、container 和逐音轨 codec/profile/config/sample rate/channel/layout/
  `extradata_hash`/language/title/default/forced disposition summary；`extradata_hash` 只是 FFprobe 读取的 codec
  header/config hash，不是媒体 payload SHA；不保存 packet 数组、ledger、内容 SHA 或 Evidence。

“fresh admission”指 Source 首次引用，或 locator 的 size/mtime 变化导致普通 reuse 失效时重新执行上述
接纳；第二个 Run 在 Source 未变化时复用 completed Source/Admission，不为每个 Run 无条件重复整片扫描。

### 7.2 SourceAdmission

职责：在任何 MR external handoff、Split 或 FinalMux 之前，对本次全部 Source 做一次普通 barrier admission。

参数包含 `source_mode` 和按 ordinal 的 Source identity，不接受 path、probe summary 或任意 audio override。

合同：

- `sources` 只接受本 preparation Graph 的 SourceProgram `source_media` outputs，Edge ordinal 等于
  `source_ordinal`；
- 从完整 RunnerInput metadata 读取实际 N/FPS/geometry/signal/audio summary，不重复 packet traversal；
- 所有 Source canonical FPS、signal 与 geometry 必须兼容；
- program 恰好一个 Source；pre_chaptered 每 Source 对应一个未来 chapter；
- pre_chaptered 在任何 MR 可运行前，按 logical audio track ordinal 比较所有 Source 的 codec/profile、
  `extradata_hash`、sample rate、channels/layout、language、title、default/forced disposition；
- 任一 Source 缺轨、多轨或 metadata 不一致都 fail closed；
- 输出 `admission.json` 只包含 source Artifact IDs 与 compact resolved summary；它是普通 DataFile Artifact，可按
  普通 reuse/stale 规则处理，不是 Evidence、receipt、校验承诺或提交授权。

### 7.3 MosaicRestoration external

职责：可选的人工 Mosaic Restoration `VideoFile + DataFile gate → VideoFile`。

参数只包含 `model_name`、`model_version`，两者均必填。N/FPS/geometry/signal 合同直接来自当前 `video`
RunnerInput Artifact metadata；不得在 create 前预扫描后复制进参数，也不得接受操作者 override。

合同：

- required `gate` 必须直接来自 SourceAdmission；
- handoff target 固定 `.mkv`；输入/输出严格 `N→N`；
- FPS、coded geometry、SAR、field order、rotation 与 resolved signal 不变；
- 输出恰好一条视频；可带不作为 Final audio authority 的音频；
- 拒绝 subtitle、attachment、data、unknown stream；
- missing/unspecified BT.709 标签可按 resolver warning 放行，显式冲突失败；
- media_info 记录 `model_name/model_version/operator_declared=true`；validator 不证明实际模型。

`mr.mode=off` 不生成 MR；首个 Run completed 后以 SourceProgram `video` Artifact 作为 expand effective video。

### 7.4 AtomicSplit

职责：把 admitted effective-video inputs 按 server-derived plan 以每物理 Source 单一连续 producer 原子产生全节目
有序 FFV1 leaves。

参数 `segments[]` 每项包含：

```text
port_id
source_ordinal
planned_effective_video_artifact_id
chapter_id / chapter_ordinal
leaf_id / leaf_ordinal
start_frame / end_frame
```

并包含 `planned_admission_artifact_id`。合同：

- required `gate` 直接来自 SourceAdmission；`videos` Edge ordinal 等于 source ordinal；
- 实际 RunnerInput Artifact IDs 必须与 planned IDs 完全一致，否则在消费 payload 前返回
  `E_AV27_PLAN_INPUT_CHANGED`；
- segments 顺序与 output port/`executor.output_paths` 顺序完全一致；
- 每个物理 Source 恰好一个连续 FFmpeg producer；禁止逐 leaf 重新打开 Source 或逐叶 seek；
- producer 前执行 duration-based capacity preflight：每个 current effective-video header duration 必须为正有限数，
  `required_bytes = ceil(max(1.0, sum(duration_seconds)) × 32 MiB + 512 MiB)`；检查 attempt 输出所在卷的
  `free_bytes >= required_bytes`。Runtime 先创建 NodeRun attempt 和受控 work dir；空间不足时在读取视频
  payload 或启动 FFmpeg 前使当前 attempt 立即 `failed`，不登记 Artifact。本模板 v0.2.1 不向请求暴露
  AVEnhanceFlow CLI 的 GiB override；
- 输入 coded geometry 必须精确 16:9 且 SAR 缺失或 1:1；恰好 1920×1080 使用 identity geometry path，绝不
  调用 spatial scaler；其他允许的 16:9 输入只执行一次
  `zscale=w=1920:h=1080:filter=spline36:chromalin=left:chromal=left`；禁止静默拉伸；
- filter 顺序冻结为 BT.709 limited `setparams` → identity/zscale → `format=yuv420p10le` → `setsar=1/1` →
  `settb=expr=fps_denominator/fps_numerator,setpts=N`；不得使用 float time base 或继承输入 PTS；
- FFmpeg 输入使用 `-fflags +genpts` 与 `-xerror`，任何 decode error 或非零退出都使整个 attempt 失败；不得
  忽略损坏帧继续生产；输出使用 `-fps_mode passthrough`，禁止隐式 CFR duplicate/drop；
- 编码参数冻结为 FFV1 `level=3/coder=1/context=1/g=1/slicecrc=1/slices=16`，输出固定 1920×1080、
  SAR 1:1、`yuv420p10le`、progressive、BT.709 SDR limited、left chroma location，容器 `.mkv`；不 map
  audio/subtitle/data/metadata/chapters；
- 同一 Source range 连续、无重叠、无缺口并完整覆盖输入 N；
- adapter 必须向该 Source 的每个 leaf port 写相同
  `producer_metadata[port_id]["output_frames"] = source_total`；validator 按 `segments.source_ordinal` 分组，要求
  组内每个 port 的 source-total 完全相同且等于该输入 N，再令每个 leaf
  `Artifact.media_info["zniku.avenhance.v27"]["frame_count"] = end-start`，不逐 leaf post-scan；
- producer count 缺失时，最多对该 effective-video input 做一次 source-level packet/frame fallback；不得对每个
  leaf 分别扫描；
- 每个 leaf 仍需存在、非空、FFprobe 可识别并通过 codec/geometry/signal validator；
- 任一 output、碰撞、producer count 或 validator 失败时整个 attempt 失败，部分 leaf 不登记；
- rerun 从每个 Source 第一帧开始，不建立 leaf checkpoint authority。

### 7.5 Enhancement external

参数包含 `model_name`、可选 `model_version`、可选 `actual_scale_factor`、expected input/output geometry、N/FPS 和
chapter/leaf identity。

合同：

- handoff target 固定 `.mov`；输入/输出严格 `N→N`，FPS 不变；
- 输出恰好一条 ProRes 422 HQ `yuv422p10le` 视频；
- SAR 缺失或 1:1、progressive、零旋转、无 HDR/显式颜色冲突；
- 相对 1920×1080 横纵倍率相同且为正整数 k；若声明，k 必须等于 `actual_scale_factor`；
- 附加 audio、subtitle 或 `tmcd/timecode` data 可存在，但 Merge 只消费 video；attachment、unknown codec
  和其他 data 失败关闭；
- 实测 `k=1` 时可以省略 `actual_scale_factor`；实测 `k>1` 时必须显式声明且等于 k；
- 同节目所有 leaves 的 geometry、scale 和 model declaration 一致；
- 一个 leaf 失败不使其他 completed leaves 回退。

### 7.6 MergeVideo

职责：每章一次 video-only 有序 concat/remux，输出 `.mov` master。

参数保存 chapter identity、expected N/FPS/geometry、scale 和 Enhancement model declaration，不接受 codec/
preset/shell 参数。

合同：

- `videos` ordinal 等于章内 leaf ordinal；
- 输入均为 ProRes 422 HQ `yuv422p10le`，FPS/SAR/geometry/signal/declaration 一致；
- 只 map video，显式丢弃 Enhancement 附带 stream；
- 一次 concat/remux，不重编码、不空间缩放；
- `producer_metadata["video"]["output_frames"]` 必须存在并等于 input namespaced frame count
  总和与 chapter N；validator 将该 N 写入 output Artifact 的统一 namespaced `frame_count`；
- 任一章失败不使其他章 completed Merge/FI 回退。

### 7.7 FrameInterpolation external

参数包含 `model_name`、可选 `model_version`、chapter identity、expected input/output frames、canonical source
FPS 与 expected geometry/signal。

合同：

- handoff target 固定 `.mov`；输入 N 必须输出精确 `2N-1`，明确拒绝 `2N`；
- 输出唯一 video-only ProRes 422 HQ `yuv422p10le`；
- geometry、SAR、signal、progressive 与零旋转相对 master 不变；
- observed FPS 与 source FPS×2 在相对容差 `2e-6` 内等价；
- `2997/50` 可等价 `60000/1001`，`60/1` 不得冒充 `60000/1001`；
- ProgramEncode FPS authority 始终是 source exact rational×2；
- 同节目所有 chapter 的 model version presence/value 与 geometry 一致。

### 7.8 ProgramEncode

职责：消费按 chapter ordinal 排列的 FI outputs，每章尾补一帧，再用一个连续 FFmpeg 进程产生 `.mp4` HEVC
Main10 video。

参数包含 `encoder=gpu|cpu`、source exact FPS、每章 source N/expected FI `2N-1`/encoded `2N`、expected
geometry/signal。

合同：

- 逐章检查 `2N-1`，不能用节目总数抵消；
- 每章 tail padding 到 `2N`，按帧序号重建 PTS，concat 后统一 exact time base；
- 全节目只启动一个连续 ProgramEncode FFmpeg；章首保留 forced IDR 请求；
- 输出 FPS 固定 source FPS×2，不继承 FI observed rational；
- CPU 固定 `libx265 slow CRF 16 Main10` 及 v2.7.0 GOP/VUI/IDR；
- GPU 固定 `hevc_nvenc p7/uhq Main10 p010le`、VBR CQ 16、80M/320M、full-resolution multipass、
  4 B-frames、AQ/lookahead/GOP/IDR 与 VUI；
- 输出 `.mp4` 保留 `hvc1` 与 exact track timescale，唯一 video-only HEVC Main10 `yuv420p10le`；
- `producer_metadata["video"]["output_frames"]` 必须存在并等于逐章闭合后的 `2ΣN`；validator 将
  该值写入 output Artifact 的统一 namespaced `frame_count`；
- capability probe 先于 FI payload 消费，失败不 fallback；
- 切换 encoder 通过参数变化和普通 stale/rerun；中断后从节目首帧重跑，completed FI 可复用。

### 7.9 FinalMux

职责：把 Program video 与 SourceProgram 原始 media 合成 `.mkv` Final Artifact。

参数包含 source mode、每个 Source 的 admitted exact `N_source/FPS`、expected program frames/geometry/signal 与
diagnostic MR mode；不接受用户 argv 或输出文件名。

合同：

- required `gate` 直接来自 SourceAdmission；`video` 只接受 Program output；
- `sources` ordinal 等于 source ordinal，只接受 SourceProgram `source_media`；
- AI video 支路任何 audio 都不是 Final authority；program source 显式 map 全部原始音轨并 stream-copy；
- pre_chaptered 先对每个 Source 在 FinalMux 当前 attempt 内把全部原始音轨一次 stream-copy 为 audio-only
  Matroska staging；每项 timed concat duration 必须从 admission/ResolvedChapterPlan 的 exact rational
  `Fraction(N_source, FPS)` 计算，禁止采用 container duration、float FPS 或累积估算；仅在 ffconcat 文本边界
  按固定小数点后 12 位序列化；再按 source ordinal 写一个 timed ffconcat 并与 Program video mux，不能把各章
  同 ordinal 音轨错误 map 成并行音轨；
- staging command 使用 `-avoid_negative_ts make_zero`；最终 Matroska mux 固定使用
  `-avoid_negative_ts disabled`，避免 MP4 B-frame 负 DTS 触发 Matroska 自动整体平移 Program PTS；
- staging 不是 Node output，不登记 Artifact/NodeResult，不成为 checkpoint；失败或重跑都在该 FinalMux attempt
  内从头重建；
- 每个 staging 必须是 audio-only Matroska、`chapter_count=0`，且逐音轨 header signature 与 Admission 完全一致；
- 不允许隐式 audio transcode；轨数、顺序、codec/profile/`extradata_hash`、sample rate、channels/layout、
  language、title、default/forced disposition 必须与 Admission 闭合；`extradata_hash` 只表示 codec header，
  不升级为内容 SHA/Evidence；
- 输出恰好一条 Program HEVC video 和全部预期原始音轨，不允许 subtitle/attachment/data；
- Final 必须 `chapter_count=0`：pre_chaptered command 使用 `-map_chapters -1`；program command 可从第二个
  Source input 执行 `-map_chapters 1`，但 SourceAdmission 已强制 Source 无 chapters，最终 validator 仍独立拒绝
  任意 chapter；
- `producer_metadata["media"]["output_frames"]` 必须存在且等于 Program namespaced N；缺失/
  不匹配直接失败，validator 将该值写入 Final Artifact 的统一 namespaced `frame_count`，
  不扫描 Final 补救；
- Final video header duration 与 Program header duration 误差最多一帧；
- FinalMux 失败只重跑 staging+mux，不重做 completed ProgramEncode。

### 7.10 OutputFile 与 canonical Jellyfin naming

模板在 FinalMux 后使用通用 `zniku.media.output_file.media@0.2.0`：

```text
FinalMux.media → OutputFile.in
```

FinalMux 只在 attempt 内产生 `final.mkv`；OutputFile 以显式 `mode=copy`、canonical `target_path` 和
`overwrite` 发布。Output 失败不回滚 Final。

因此 Phase 3 的“最终名称与 overwrite 由显式参数决定”精确落在后续通用 OutputFile，而不进入 FinalMux
参数；Phase 4 的 Python builder 负责生成 canonical `target_path`。FinalMux 本身没有外部发布权限。

canonical path：

```text
MR off:
<Title> (<Year>)/<Title> (<Year>) - Enhanced FI<rate-label> <height>p.mkv

external MR:
<Title> (<Year>)/<Title> (<Year>) - MR Enhanced FI<rate-label> <height>p.mkv
```

Windows path 规则冻结为：

- `title` 先做 Unicode NFC，长度 1..120，不得有前后 whitespace、末尾 dot/space 或路径分隔符；
- 拒绝 ASCII control、`< > : " / \\ | ? *`、`.`、`..`；title 在第一个 dot 前的 stem 也不得大小写不敏感地
  等于 CON/PRN/AUX/NUL/COM1..9/LPT1..9；
- `year` 必须是未转换的 ASCII `[0-9]{4}` string；
- `output_root` 必须 resolved strict 为现有目录；canonical `<Title> (<Year>)` 子目录必须预先存在且为目录；
- canonical parent 使用 strict resolve 并必须严格 contained in resolved output_root；不存在的 target 只在这个
  strict parent 下做 non-strict candidate resolution，已存在 target 的 symlink/reparse-point 或 containment 逃逸失败；
- preview、create、expand、FinalMux 和 OutputFile 都不得隐式 `mkdir` canonical title 子目录；
- target 存在时只有显式 `overwrite=true` 可发布；模板不得自动改名。

rate label 只接受：

| 最终 FPS | label |
| --- | --- |
| `30/1` | `30p` |
| `30000/1001` | `29p97` |
| `60/1` | `60p` |
| `60000/1001` | `59p94` |

其他 rate 失败关闭。canonical naming 只由 Python builder 计算；TS 不复制映射。

## 8. 生成算法与目标图

### 8.1 preparation create

1. 严格解析 PrepareRequest、Project target 与 Source paths，不做 packet traversal；
2. 按 source ordinal 生成 SourceProgram；
3. 生成唯一 SourceAdmission，所有 `source_media` 按 ordinal 接入 `sources`；
4. external MR 为每 Source 生成 MR：Source `video→video`，Admission `gate→gate`；off 不生成 MR；
5. 运行 Graph Core 与 preparation profile preflight；
6. 原子创建 Project。

```text
SourceProgram(s).source_media ─ordered_many→ SourceAdmission ─gate─┬→ MR A.gate
SourceProgram A.video ──────────────────────────────────────────────┤  MR A.video
SourceProgram B.video ──────────────────────────────────────────────┴→ MR B.video

MR off preparation 只有 SourceProgram(s) + SourceAdmission。
```

### 8.2 expand

1. 绑定 current Project、completed preparation Run、Admission Artifact 和 ordered effective-video Artifacts；
2. 严格解析 `chapter_selector`，只从 admitted Artifact media_info 的 N/FPS server-side 生成
   ResolvedChapterPlan；pre_chaptered 则拒绝 selector 并按 admitted Source slots 生成；
3. 按 `leaf_duration_minutes` 派生 DerivedLeafPlan，并一次生成动态 AtomicSplit ports 与
   `executor.output_paths`；
4. 生成 AtomicSplit，effective videos 按 source ordinal 接入，Admission gate 接入 required gate；
5. 每 leaf 生成 Enhancement；每章生成 Merge 和 FI；
6. 生成唯一 ProgramEncode；所有 FI outputs 按 chapter ordinal 接入；
7. 生成唯一 FinalMux：Program video、ordered Source media 和 Admission gate 接入；
8. 生成通用 OutputFile 和 canonical publication 参数；
9. Core/profile/precondition 全部通过后原子保存。

```text
SourceProgram(s)
├─ video → MR? ───────────────────────────────┐
├─ source_media → SourceAdmission ─gate───────┼→ AtomicSplit
│                                             │   ├→ Enhancement leaves → Merge A → FI A ─┐
│                                             │   └→ Enhancement leaves → Merge B → FI B ─┼→ Program
├─ source_media (ordered) ─────────────────────┼───────────────────────────────────────────┐
└─ SourceAdmission.gate ───────────────────────┴──────────────────────────────────────────→ FinalMux → Output
                                                                                  Program ─→ video
```

图仅表达普通 typed dependencies。Runtime 不知道“prepare/expand”，也不按 stage name 分支。

## 9. Profile preflight

preflight 分别识别 `preparation-compatible` 与 `expanded-compatible`，至少验证：

- 专用 type/version、executor declaration 的 output paths、节点数量和 phase shape；
- Source ordinal、SourceAdmission 唯一性与 ordered inputs；
- 每个 MR、AtomicSplit、FinalMux 的 gate 直接来自同一 Admission output；
- MR mode 与节点存在性；MR/Enhancement/FI 只使用 model_name/model_version 规则；
- preparation Graph 不含 Chapter/Leaf/Split 或下游；
- expanded Graph 的 preparation Run/Artifact binding 仍 current，否则 `replan_required`；
- `chapter_selector` variant 严格，ChapterPlan 只能由 admitted N/FPS server-side 派生且完整闭合；
- LeafPlan 只能由 `leaf_duration_minutes` 派生；
- AtomicSplit ports/`executor.output_paths`/segments/Artifact IDs 一一对应；
- 每 leaf 恰好一个 Enhancement；每章 leaves 按 ordinal 汇入一个 Merge 和一个 FI；
- 全节目恰好一个 Program、Final 和 canonical Output，无旁路；
- Source original media 只进入 Admission 与 Final；
- Enhancement scale/geometry 和 stage declaration 全节目一致；
- 所有局部 N 满足 `N`、`2N-1`、`2N`；
- publication rate label、严格 Windows title/year、existing canonical parent、containment 与 overwrite；
- definitions/parameters 不含 executable、argv、shell、code 或客户端注入 output path。

preflight 失败只表示不符合 template profile。若 Graph 仍符合 Core，它仍是合法自由图；Studio 不得混淆 profile
diagnostics 与 Core type/cycle diagnostics。

## 10. 失败语义

| 阶段 | 失败语义 | 影响 |
| --- | --- | --- |
| preview/request | 未知字段、非法 path/stage/plan | 只读失败，无文件/Project mutation |
| create | target 已存在、Core/profile/写入失败 | no-overwrite；无半份 Project |
| preparation Run | Source/Admission/MR 失败 | 当前 attempt failed；不得 expand |
| expand binding | Project/Run/result/Artifact 不匹配或已变化 | Project 不变，要求重新 preview/expand |
| Graph/profile preflight | 普通 Core 或专用 profile 失败 | 不启动 Run/不声称 compatible |
| automatic process/validator | capability、进程、输出或 metadata 失败 | 当前 attempt failed，无 Artifact |
| manual readiness | missing/empty/present/probe_passed/probe_failed | 只读，不改变 waiting attempt |
| explicit Submit | external output validator 失败 | failed(external_submission_invalid)，无 Artifact |
| interruption/cancel | 受控进程终止 | failed(interrupted/cancelled)，从头 rerun |

稳定错误 namespace 至少包括：

- `E_AV27_TEMPLATE_*`、`E_AV27_CREATE_*`、`E_AV27_EXPAND_*`；
- `E_AV27_SOURCE_*`、`E_AV27_ADMISSION_*`、`E_AV27_PLAN_*`；
- `E_AV27_MR_*`、`E_AV27_SPLIT_*`、`E_AV27_ENHANCEMENT_*`；
- `E_AV27_MERGE_*`、`E_AV27_FI_*`、`E_AV27_PROGRAM_*`；
- `E_AV27_FINAL_*`、`E_AV27_NAMING_*`。

未知 codec、stream、pix_fmt、rate、signal 或 metadata 默认失败关闭；只有本文明确允许的 BT.709 missing 标签与
Source medium-confidence cadence 可以 warning 放行。

### 10.1 Handoff readiness 与 Submit

MR/Enhancement/FI handoff 展示 Run/NodeRun/attempt、input Artifact、唯一 target、要求后缀、
`model_name/model_version`、N/FPS/geometry contract 和 `Validate and submit`。

readiness 复用：

```text
missing | empty | present | probe_passed | probe_failed
```

只有显式 Submit 才推进状态。readiness 不创建/修改文件、不登记 Artifact、不触发下游。Submit 重新验证最新
attempt/handoff identity 与全部输出；失败后新 attempt 使用新 target，从完整输入重做。

### 10.2 原子输出与清理

- automatic 多输出只有全部成功才一次登记完整 results/artifacts；
- manual 唯一 output 通过才登记；
- Split partial leaves 和 Final audio staging 都不是 Artifact/checkpoint；
- 失败文件只保留在对应 attempt 供诊断，清理只能定向该目录；
- Final/Output 失败不回滚 Program；Program 失败不回滚 FI/Enhancement；
- 任务外 Source/发布路径不能由失败清理删除。

## 11. 与 ZNIKU Runtime 的绑定

### 11.1 不增加 Scheduler 业务分支

Scheduler 不得判断 type ID 是否含 `avenhance`、当前 stage、chapter/leaf、唯一 Program/Final 或 prepare/expand。
这些事实只由 ordinary edges、required ports、ordinal、adapter/validator 与 authoring preflight 表达。

### 11.2 reuse 与 stale

- type/version、参数、直接输入或入边变化使本节点及下游不可复用；
- successful attempt 产生新 Artifact ID；
- output missing/quick probe failure 禁止复用；
- 第二个 Run 复用未变的 completed Source/Admission/MR；
- effective Artifact 改变先要求 replan，再由普通 stale 传播；
- Enhancement leaf 改变只使其 Merge 及下游 stale；
- encoder 参数改变只使 Program/Final/Output stale；
- 历史 Run/NodeRun 不回写为 template state。

### 11.3 Run snapshot

Run snapshot 保存当时普通 Graph 与 exact definitions。编辑/重新 expand 只影响下一 Run。Submit 必须绑定原
NodeRun snapshot 的 type/version、input Artifact IDs 与 target；不能用当前已编辑 Graph 偷换 handoff。

## 12. 版本与兼容策略

- `.zniku` `PROJECT_SCHEMA_VERSION` 保持 `2`；`executor.output_paths` 随 definition JSON 保存，不要求数据库
  migration；
- 0.2.0 executor spec 读取时 `output_paths` 缺省为空，历史 NodeDefinition snapshot 不回填、不改写；
- v0.2.0 Project 按原 `zniku.media.*@0.2.0` definitions 打开，不自动注入 v2.7 nodes；
- preparation/expanded Project 保存实际需要的 `zniku.avenhance.v27.*@0.2.1` 与通用 Output definition；
- 不迁移改写历史 definitions、Run、Artifact 或 handoff；
- 专用参数/ports/`executor.output_paths` 不兼容变化必须提升 exact NodeDefinition version；
- profile 2.8.0 使用新 namespace/profile，不改写 `v27`；
- ZNIKU patch 与 AVEnhanceFlow profile version 独立；固定参考 commit 不随 main 漂移。

## 13. 明确非目标

- 不实现或导入 AVEnhanceFlow Task、PlanState、NodeState、AdoptedInput、CLI、Skill 状态或目录；
- 不把固定 topology、唯一 Final、chapter/leaf scope 或 admission gate 写进 Graph Core/Scheduler；
- 不实现 `--near`、运行时动态 plan/dynamic ports 或 TypeScript planner；
- 不实现节点内部 resume/checkpoint、segment recovery、encoder session 恢复或旧进程接管；
- 不直接控制外部 GUI、不采集其内部进度、不证明模型真实加载或画质提升；
- 不恢复 SHA、full Evidence、receipt、packet/raw/audio exact proof；
- 不把 ZBaton、Checksum、deep decode 或 archive Manifest 设为 Core/template 完成条件；
- 不建设公网、多用户、分布式 worker、GPU lease、插件签名或沙箱；
- 不在自动测试中运行真实 AI、长媒体或提交真实媒体；
- 不让 TS mock、layout、display label 或客户端 definitions 成为正式合同。

ZBaton 可在 OutputFile 后作为普通可选 Export；失败不回滚 Final/Output。

## 14. 实现与测试完成门

### 14.1 Definition 与通用 plumbing

- 精确测试 type/version/ports/cardinality/mode/executor/validator，以及 output path 只存在于
  `executor.output_paths`；
- Project Service 对三种 executor 都泛化投影到现有 `NodeExecutionRequest.output_paths`，旧 executor 缺省空；
- 所有 root Schema 拒绝未知字段；请求明确拒绝 `tool/tool_version`、source admissions、expected Source N/FPS、
  leaves、Graph 和 definitions；
- RunnerInput 包含完整 Artifact metadata，且只能由 Repository 构造；adapter/validator 只有
  `RunnerInput.media_info` 一条下游 metadata 路径；
- v2.7 automatic 媒体 producer 的 per-port object 只允许严格正整数 `output_frames`；bool、0、
  未知/额外 key、重复 port、非法 JSON 与未验证写入全部 fail closed；
- validator per-port namespace 可新增，冲突/未知 port/非法 JSON fail closed；只有 passed extension 会写入
  Artifact，raw producer metadata 不持久化；
- output path 拒绝 absolute/drive/UNC/`..`/NUL/escape；所有后缀与 containment 正确；
- 只有 SourceProgram 与 OutputFile 可登记 external path。

### 14.2 两段 authoring

- program/pre_chaptered、MR off/external 都只能先创建 preparation Graph；
- preparation Run 前不得存在 Chapter/Leaf/Split/downstream；
- SourceAdmission 在所有 MR 前完成全 Source/audio preflight；
- expand 只绑定 current Project 的 completed preparation Run 与 exact Artifacts；
- 第二 Run 复用 completed Source/Admission/MR；
- 任一 planned Artifact ID 变化必须 replan，不能运行旧 Split plan；
- `single/exact_frames/exact_times` 从 admitted N/FPS 产生稳定 ranges/labels/IDs/ordinals；exact time 覆盖 half-up、
  映射后重复/越界，`near` 和客户端 `chapters[]` 一律拒绝；pre_chaptered 拒绝 selector；
- `leaf_duration_minutes` 正整数边界、common rational FPS 与 ties-to-even case 产生稳定连续 ranges；
- 任意客户端 chapters/leaves/range 缺口/重叠/越界全部失败；
- dynamic Split 同 count shape/path 稳定，不同 count type ID 不同；
- preview 无副作用；create no-overwrite/atomic；expand atomic 且拒绝 Graph divergence；
- TS 只消费 server response，不生成或注入 definitions/ports/paths。

### 14.3 媒体 validator

- Source invalid stream/chapter/FPS/signal fail closed，并且每 Source 只做一次完整 traversal；
- pre_chaptered audio mismatch 在 MR handoff 出现前失败；Admission DataFile 不是 Evidence/receipt；
- MR/Enhancement `N→N` 和固定 `.mkv/.mov`；
- Split 覆盖 identity 与 Spline36 scale 两路、exact `settb/setpts`、`fps_mode=passthrough`、`-xerror`、FFV1
  level 3/16 slices/CRC、duration capacity fail-fast；每 physical Source 单 producer、计划守恒、无逐 leaf count
  scan、source-level fallback 最多一次；
- Merge `.mov`、ordinal、video-only 与 sum count；
- FI `.mov` 接受 `2N-1`、拒绝 `2N`，覆盖 rate tolerance；
- Program `.mp4`、逐章补帧、单进程、总 `2ΣN`、CPU/GPU argv 与无 fallback；
- Final `.mkv`；pre_chaptered 以每 Source `N_source/FPS` exact duration 写 timed concat，audio-only staging 只在
  attempt 内，失败不登记 staging Artifact；
- Final 至少两原始音轨的数量/顺序/codec/profile/`extradata_hash`/metadata/disposition/stream-copy 闭合；覆盖
  final `-avoid_negative_ts disabled`、program/pre_chaptered chapter mapping 和最终 `chapter_count=0` gate；
- Split 按 source 组闭合重复的 producer total；Merge/Program/Final 分别要求
  `producer_metadata["video"|"media"]["output_frames"]` 与 expected N 闭合；所有媒体 Artifact 只向
  `media_info["zniku.avenhance.v27"]["frame_count"]` 写规范 exact N；
- canonical naming 覆盖四个 rate、Windows reserved/escape、4 位 year、missing title directory 与 overwrite。

### 14.4 Runtime 回归

- manual readiness 不改变 NodeRun/文件；非法 Submit 无 Artifact；manual 无假百分比；
- automatic progress 单调、completed=1；
- leaf rerun 不重做独立 leaves；Program 失败/切 encoder 复用 FI；Final/Output 失败复用 Program；
- interruption 从头 rerun，不 adopt partial/staging；
- generic media、Graph Core、Scheduler 与 v0.2.0 Project 全部回归通过。

### 14.5 禁止项检查

静态检查证明没有 Scheduler `avenhance` 分支、AVEnhanceFlow task/state reader、SHA/Evidence/receipt/digest、
shell command/`shell=True`、TS definition injection，以及真实媒体路径、handoff、日志、final、模型或本机配置进 Git。

## 15. 一句话合同

> **ZNIKU v0.2.1 先用普通 Source/Admission/MR 节点建立 admitted Artifact，再由 server-side expand 从这些
> Artifact 的 N/FPS 与 strict selector 确定性派生 Chapter/Leaf 并追加 v2.7 DAG；第二个普通 Run 复用 completed preparation nodes，
> Graph Core 始终只执行 typed DAG，不获得任何 AVEnhanceFlow 业务状态机。**
