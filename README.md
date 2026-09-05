# ZNIKU

*Flesh out every frame.*

**让每一帧有血有肉。**

ZNIKU 是一个自由编排媒体处理节点、执行本地工作流并复用已完成结果的 GUI Studio。

## 0.2.0 Core、v0.2.1 验收与 v0.3.0 开发状态

- 目标版本：`ZNIKU Studio v0.3.0`（package/product 当前仍为 `0.2.0`，只在 v0.3.0 Phase 6 验收候选时收敛）
- 已交付基线：`ZNIKU Studio 0.2.0` Phase 0–5，Core 已完成 legacy 清理与最小自动化验收
- 已验收增量：`v0.2.1` Phase 0–5 已完成，真实 MR-off GUI 闭环与操作者验收通过；external MR 仍是未验证范围
- 当前开发分支：`v0.3.0`
- 当前增量阶段：**v0.3.0 Phase 0–4 已完成；Phase 5–6 尚未实施**
- Graph／Runtime 上位架构权威：[自由媒体图核心设计基线](docs/architecture/graph-core-baseline.md)
- Studio UX 下位设计：[创作者体验基线](docs/architecture/studio-ux-baseline.md)
- 分阶段计划：[v0.3.0 创作者体验重构执行方案](docs/v0.3.0-execution-plan.md)
- Phase 0 证据：[v0.3.0 Phase 0 验收记录](docs/v0.3.0-phase0-acceptance.md)
- Phase 1 证据：[v0.3.0 Phase 1 验收记录](docs/v0.3.0-phase1-acceptance.md)
- Phase 2 证据：[v0.3.0 Phase 2 验收记录](docs/v0.3.0-phase2-acceptance.md)
- Phase 3 证据：[v0.3.0 Phase 3 验收记录](docs/v0.3.0-phase3-acceptance.md)
- Phase 4 证据：[v0.3.0 Phase 4 验收记录](docs/v0.3.0-phase4-acceptance.md)
- 当前实现版本：`0.2.0`；package/product 版本按计划保持不变，公共入口为 `zniku.graph`、`zniku.project`、`zniku.runtime`、
  `zniku.project_service`、`zniku.presentation`、`zniku.media` 与 `zniku.avenhance_v27`
- 历史边界：`main@198d802` 的 `0.1.0` 说明只保存在 `docs/archive/0.1.0/`，不参与产品运行或门禁

Phase 1 已建立 Graph Core 与 `.zniku` Store，Phase 2 已用新的最小 Run、NodeRun、Artifact 与 NodeResult
替换 Runtime 领域入口，Phase 3 已把自由节点画布接到这些 Python authority，Phase 4 已加入首批真实媒体
NodeDefinition、FFmpeg adapters、FFprobe 与轻量 validators。Phase 5 已删除旧 Compiler、Evidence、固定
pipeline、Real Acceptance 和旧生成投影的实现、测试与工具；CI 只验证当前 0.2.0 产品面。

v0.2.1 Phase 1 已补齐 Run 选择、轮询和人工交接闭环；Phase 2 已接通 automatic Python/FFmpeg 的可信
进度、限频持久化、Project Service 实时投影与 Studio determinate/indeterminate 展示。Phase 3 已实现
AVEnhanceFlow v2.7.0 专用节点包及不带业务分支的通用 Runner metadata/output-path plumbing；Phase 4 已实现
两阶段 Python template builder、preview／expand 与 Studio 向导；Phase 5 已完成自动门禁、真实 MR-off 短片
GUI 闭环和操作者验收，证据边界见 [v0.2.1 Phase 5 验收记录](docs/v0.2.1-acceptance.md)。原 v0.2.1 Phase 6
版本收敛已暂停，其必要工作并入 v0.3.0 最终验收阶段。

v0.3.0 保留上述 Graph Core、Runtime 与真实媒体执行能力，重点把 Studio 从工程控制台重构为面向视频编辑者
的创作者工具。创作者模式、高级节点图和高级诊断始终操作同一张 Graph；展示元数据不进入执行语义。

v0.3.0 Phase 1 已加入 Python `PresentationCatalog`、Project Service `0.3.0` 只读展示接口和通用 Draft
2020-12 参数表单。表单与“高级 → 原始参数”编辑同一份 session `ParameterDraft`，只有显式“应用设置”才
写回当前 Graph；缺失或损坏的第三方 Presentation 会隔离并回退通用 Schema 表单，不改变 Runtime 结论。

v0.3.0 Phase 2 已加入工程首页、默认隐藏的工程身份、有界 HostBridge，以及“选择素材 → 处理方案 → 设置 →
分析 → 确认工作流”的 AVEnhanceFlow v2.7.0 创作者流程。原生 picker、最近工程、人类可读媒体摘要和
基础/高级设置都只服务于同一 Project 与 Graph；分析继续绑定 exact preparation Run，取消、迟到响应、已有
工程和素材变化均失败关闭。Phase 2 验证已启动 Studio 的建项闭环；可分发双击 launcher、单实例和真实原生
窗口 E2E 仍属于 Phase 5。

v0.3.0 Phase 3 已加入可撤销的自由画布、兼容端口建议与批量连接、键盘连接、有序输入拖动排序、
别名／分组／折叠／视口，以及基于 storage revision/CAS 的自动保存。安全的不完整图能够保存但不能运行；
Run all、Run to here 和 Rerun from here 都先保存最新编辑再校验会话及存储版本。`.zniku` 新增独立
StudioState 表，旧 schema 2 首次保存时备份并事务迁移到 schema 3；展示不改变执行、reuse 或 stale。

v0.3.0 Phase 4 已实现创作者运行中心、中文问题卡片、任务式历史与外部处理助手；精确身份、运行命令和日志
进入高级层。外部输出必须先检查再显式提交，提交时仍由 Python 完整重验。重跑前展示 Python 的只读影响清单，
确认后才执行正式命令；状态历史读取改为 SQL 有界窗口，OutputFile 字节采样不再每 MiB 触发回调。Python
完整门禁与正式服务合成浏览器闭环已通过，详见 Phase 4 验收记录；这不代表 Phase 5／6 已完成。

## 产品核心

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
                Python / Command / Manual External
                       │
                       ▼
               Media Files + NodeResult
```

0.2.0 只围绕四件事建设：

1. 用户自由添加、删除、连接、复制和组合媒体节点；
2. Runtime 按 DAG 依赖执行节点；
3. 失败或中断的节点只能从头重跑；
4. 已完成且仍然有效的其他节点继续复用。

Graph Core 只校验 node／port／edge 存在、typed output→input、required input、单值 input 单入边、
`ordered_many` ordinal 连续唯一和 DAG 无环。图允许多个 Source、多个 Output、零 Output，以及任意合法
分支与汇合。

## Phase 1–5 公共内核

- `zniku.graph`：公开 `Graph`、`NodeDefinition`、`NodeInstance`、`Edge`、typed `PortSpec` 及
  `GraphValidator`；首批类型是 `MediaFile`、`VideoFile`、`AudioFile`、`DataFile`，插件可增加开放字符串
  类型，Phase 1 只接受大小写敏感的精确类型相等，不做隐式继承或转换。定义和实例使用精确版本，参数按
  NodeDefinition 的 JSON Schema 验证。
- `zniku.project`：公开 `Project`、`ProjectStore` 与独立 `StudioState`；`.zniku` 是带 schema version 的
  单文件 SQLite authority。schema v2 保存普通 Run snapshot、attempt、Artifact、NodeResult、日志索引和
  latest-result head；schema v3 另存纯展示状态与存储 CAS。v1 严格校验后迁移到 v2，v2 首次 authoring
  保存前创建一致备份再事务迁移到 v3，读取或运行不提前升为 v3。
- `zniku.runtime`：公开最小 Runtime 模型、`Scheduler`、completed reuse／downstream stale 分析、支持
  三类 executor 的 `NodeRunner` 与运行编排服务。持久状态只有 `pending`、`running`、
  `waiting_external`、`completed` 和 `failed`；`ready`／`blocked` 只即时计算。
- `zniku.project_service`：提供严格 0.3.0 DTO 与只监听 loopback 的本地 HTTP host；Studio 可创建、打开、
  保存 `.zniku`，启动全图或目标祖先闭包 Run，从节点重新运行，轮询 attempt、日志与输出，并提交
  `manual_external` 声明目标。响应由 Python Schema 校验，浏览器不能替换 NodeDefinition executor、
  work root 或 handoff 路径。
- `zniku.presentation`：提供 `zh-CN` 节点、参数、端口和 Palette 展示目录，通过 exact
  `type_id + definition_version` 绑定 NodeDefinition；它不携带参数约束、executor、validator 或命令，也不
  进入 Project、Graph、Run snapshot、reuse 或 stale。
- `zniku.media`：提供 SourceMedia、VideoTransform、SplitVideo、MergeVideo、EncodeVideo、MuxMedia 与
  OutputFile 的 exact definitions、Python adapters 和轻量 validators；MR、Enhancement、FI 是普通
  `manual_external` VideoTransform presets。详细合同见
  [首批媒体节点合同](docs/architecture/media-node-contract.md)。
- `zniku.avenhance_v27`：提供 `zniku.avenhance.v27.*@0.2.1` 九类专用 definition、automatic
  adapters、manual validators 与严格媒体 probe；固定流程仍只是下一阶段由 Python builder 生成的普通
  DAG，不进入 Scheduler。详细合同见
  [AVEnhanceFlow v2.7.0 模板与节点合同](docs/architecture/av-enhance-flow-v2.7-template-contract.md)。
- Project 保存和读取都使用同一套 Python 模型、精确 definitions 与 Graph Validator。v0.3.0 允许同一个
  `Project.graph` 保存仅缺少 required input 或 Schema `required` 参数的可诊断 authoring draft；创建 Run
  前仍执行完整验证。未知字段／definition／executor、悬空引用、类型或 ordinal 错误、cycle、未知工程 schema
  和损坏数据继续 fail closed；不建立第二张 Graph 或隐形编译结果。
- `.zniku` 可能包含本地路径和节点配置，默认由 Git 忽略；测试只在临时目录创建纯合成工程。
- 新 Project 由正式 launcher 注入 Python 内建媒体目录；Studio Palette 只投影 Project 保存的 exact
  definitions，不复制媒体合同。`demo.text_*` 仍只用于不依赖 FFmpeg 的 Project Service／Runtime 合成回归。

## 0.2.0 明确删除的旧宪法

- 全局 full verification、Evidence chain、receipt 和 authority chain；
- 强制 SHA-256、full decode、packet scan 和 roundtrip；
- 节点内部 checkpoint、resume、lease、进度接管和分片恢复；
- 全局 `program/chapter/leaf` scope 与复杂 ArtifactSet authority；
- Partition／Map／Select／Passthrough／Collect／Reduce 强制代数；
- 唯一 Final；
- Compiler／Freeze／Revision／ExecutionPlan 多层权威与 canonical digest；
- 固定 AVEnhanceFlow 拓扑以及 ZBaton 核心依赖。

Checksum、严格 QC、ZBaton 和归档 Manifest 仍可作为可选节点或 Export 插件，但不属于 Core 完成条件。

## 架构与命名权威

| 文件 | 权威范围 |
| --- | --- |
| [`docs/architecture/graph-core-baseline.md`](docs/architecture/graph-core-baseline.md) | Graph、Project、Runtime 与执行安全边界的唯一上位架构权威 |
| [`docs/architecture/studio-ux-baseline.md`](docs/architecture/studio-ux-baseline.md) | v0.3.0 展示、交互、桌面入口与易用性的正式下位设计 |
| [`docs/architecture/studio-schema-corpus.json`](docs/architecture/studio-schema-corpus.json) | 内建参数 Schema 机器可读盘点；不是独立语义权威 |
| [`docs/architecture/host-bridge-prototype.md`](docs/architecture/host-bridge-prototype.md) | Phase 0 HostBridge 有界 prototype 证据；不定义 Graph／Runtime 语义 |
| [`docs/architecture/media-node-contract.md`](docs/architecture/media-node-contract.md) | Phase 4 首批媒体节点的从属实现合同 |
| [`docs/v0.3.0-execution-plan.md`](docs/v0.3.0-execution-plan.md) | v0.3.0 阶段、门禁与验收顺序；不覆盖 Core |
| [`docs/v0.3.0-phase0-acceptance.md`](docs/v0.3.0-phase0-acceptance.md) | v0.3.0 Phase 0 实施结果、自动门禁与未证明范围 |
| [`docs/v0.3.0-phase1-acceptance.md`](docs/v0.3.0-phase1-acceptance.md) | v0.3.0 Phase 1 Presentation、Schema 表单与组件边界验收 |
| [`docs/v0.3.0-phase2-acceptance.md`](docs/v0.3.0-phase2-acceptance.md) | v0.3.0 Phase 2 创作者建项、HostBridge 与模板引导验收 |
| [`docs/v0.3.0-phase3-acceptance.md`](docs/v0.3.0-phase3-acceptance.md) | v0.3.0 Phase 3 画布编辑、草稿保存、StudioState 与 CAS 验收 |
| [`docs/v0.3.0-phase4-acceptance.md`](docs/v0.3.0-phase4-acceptance.md) | v0.3.0 Phase 4 运行中心、外部处理助手、重跑影响与有界读取验收 |
| [`docs/v0.2.1-acceptance.md`](docs/v0.2.1-acceptance.md) | v0.2.1 Phase 5 已完成验收及未验证范围 |
| [`docs/phase5-acceptance.md`](docs/phase5-acceptance.md) | Phase 5 可重复门禁及其证明边界 |
| [`docs/brand-baseline.md`](docs/brand-baseline.md) | 品牌、产品名与代码标识 |
| [`docs/archive/0.1.0/`](docs/archive/0.1.0/) | 0.1.0 历史实现说明；对 0.2.0 无规范权威 |

旧 Runtime snapshot、Evidence、真实媒体候选工作根、前端投影和运行目录不迁移到 0.2.0，也不得提交 Git。

## 正式命名

| 对象 | 名称 |
| --- | --- |
| 品牌 | `ZNIKU` |
| 产品 | `ZNIKU Studio` |
| 执行核心 | `ZNIKU Runtime` |
| 扩展体系 | `ZNIKU Engine SDK` |
| GitHub 仓库 | `ZNiku` |
| CLI / Python package | `zniku` |
| 工程文件扩展名 | `.zniku` |

## Phase 0–5 交付边界

本阶段已经完成：

- 纳入并启用 0.2.0 Graph Core 基线；
- 同步 `AGENTS.md`、README 与 Studio 开发说明；
- 将全部 0.1.0 架构文档移入历史归档；
- 用自动化测试锁定“单一新权威、旧文档只在归档”的目录与引用规则；
- 实现最小 Project、Graph、NodeDefinition、NodeInstance、Edge 和严格参数模型；
- 实现 typed ports、required input、`one`／`ordered_many` 与 DAG validator；
- 实现事务化 SQLite `.zniku` Project Store 及损坏／未知输入失败语义；
- 实现普通 graph snapshot、稳定 ready 计算、独立 attempt 工作目录与普通日志；
- 实现 trusted Python adapter、`shell=False` command executor 和可跨应用重启的 manual external handoff；
- 实现 interrupted 恢复失败、rerun-from-start、completed result 复用和 downstream stale。
- 将 GUI-0 升为唯一正式 Designer：Palette、拖动、typed 连接、复制、多选、删除、参数编辑和即时 DAG
  提示都作用于同一张 Graph；运行状态只叠加在该画布上。
- 接通 `.zniku` 创建／打开／原子保存、Run all、Run to here、Rerun from here、stdout/stderr、输出路径、
  stale／失败原因和 manual external Submit。
- 删除 Formal Designer、Expanded Plan、Run Monitor、Real Acceptance 与 GUI-0 Prototype 的产品双轨入口；
  Studio 不再提供 Compiler／Freeze／Evidence mock 回退。
- 实现 typed SourceMedia、automatic VideoTransform、SplitVideo、MergeVideo、EncodeVideo、MuxMedia 与 typed
  OutputFile；所有 automatic 媒体进程都使用结构化 argv 与 `shell=False`。
- 实现 Split half-open frame range、输出 `frame_range`、Merge `ordered_many` ordinal 和局部帧数守恒；
  媒体 Artifact 登记前执行存在、非空、FFprobe 流识别和节点轻量 validator。
- 提供 MR、Enhancement、FI external presets，以及 OutputFile 的显式 `copy`／`reference` 发布模式；成功发布
  会在 typed `published` port 登记 Studio 可见、Runtime 可 quick-probe 的 external Artifact。
- 删除 0.1.0 legacy Python packages、专属 tests／tools、旧 Studio generated projections 与第二套 CI 门禁；
  `docs/archive/0.1.0/` 只作为不可执行的历史档案保留。
- 加入 CI 可自行合成的 12 帧 FFV1 端到端 smoke，覆盖两条正式媒体 DAG、Split/Merge 帧守恒、发布结果与
  completed reuse。详细验收范围见 [Phase 5 最小验收](docs/phase5-acceptance.md)。

自动化只证明单元测试和极短合成媒体 smoke；它不代表长片性能、主观画质或最终操作者验收。

## 仓库结构

```text
ZNiku/
├── src/zniku/
│   ├── graph/                   # 0.2.0 Graph 模型与唯一 validator
│   ├── project/                 # 0.2.0 SQLite .zniku Project Store
│   ├── runtime/                 # 0.2.0 Scheduler、Node Runner 与运行历史
│   ├── project_service/         # 0.2.0 Studio DTO、application 与 loopback host
│   ├── media/                   # 0.2.0 首批媒体 definitions、adapters、probe 与 validators
│   ├── avenhance_v27/           # 0.2.1 v2.7 专用节点包；不含模板或第二套 Runtime
├── apps/studio/
│   └── src/studio/              # 0.2.0 唯一正式 Designer 与 Runtime overlay
├── tests/                       # 0.2.0 Core 与 v0.2.1 增量单元、集成和架构权威门禁
├── tools/                       # Schema 一致性、Studio host 与短媒体 smoke 工具
├── docs/
│   ├── architecture/
│   │   ├── graph-core-baseline.md
│   │   ├── host-bridge-prototype.md
│   │   ├── media-node-contract.md
│   │   ├── studio-schema-corpus.json
│   │   └── studio-ux-baseline.md
│   ├── archive/0.1.0/           # 旧架构历史归档
│   ├── phase5-acceptance.md      # 可重复最小验收与证明边界
│   ├── v0.2.1-acceptance.md      # v0.2.1 Phase 5 真实 GUI 操作者验收
│   ├── v0.3.0-execution-plan.md  # v0.3.0 分阶段执行方案
│   ├── v0.3.0-phase0-acceptance.md # v0.3.0 Phase 0 自动门禁与证明边界
│   ├── v0.3.0-phase1-acceptance.md # v0.3.0 Phase 1 展示合同与参数表单证据
│   ├── v0.3.0-phase2-acceptance.md # v0.3.0 Phase 2 建项、HostBridge 与模板引导证据
│   ├── v0.3.0-phase3-acceptance.md # v0.3.0 Phase 3 可撤销画布、自动保存与迁移证据
│   ├── v0.3.0-phase4-acceptance.md # v0.3.0 Phase 4 创作者运行与外部处理证据
│   └── brand-baseline.md
├── AGENTS.md
└── VERSION                      # 0.2.0 产品实现版本
```

## 本地运行与验证

Python 需要 3.12 或更高版本及 `uv`；媒体节点还要求 `ffmpeg` 与 `ffprobe` 可从 `PATH` 解析。以下门禁
验证 0.2.0 Graph Core、Project Store、Runtime 和媒体节点，以及 v0.3.0 Project Service wire 与
Presentation 增量：

```powershell
uv lock --check
uv sync --locked --extra dev
uv run --locked --extra dev python tools/generate_project_service_schema.py --check
uv run --locked --extra dev pytest
uv run --locked --extra dev python tools/run_media_smoke.py
uv run --locked --extra dev mypy --no-incremental src tests tools
uv run --locked --extra dev ruff check src tests tools
uv run --locked --extra dev ruff format --check src tests tools
```

`tools/run_media_smoke.py` 无需输入文件：它会在临时目录自行生成 12 帧视频，运行两条媒体 DAG，并在退出时
删除全部临时工程、attempt 与输出。要在 Studio 中进行交互验收，再准备一个短参考片段，指定输出目录并生成
可自由编辑的示例工程；工具会创建尚不存在的工程父目录与输出目录，但不会覆盖既有 `.zniku`：

```powershell
ffmpeg -version
ffprobe -version
uv run --locked --extra dev python tools/create_media_smoke_project.py `
  D:\ZNIKU\phase5-smoke.zniku `
  D:\Media\short-reference.mkv `
  D:\ZNIKU\phase5-output
uv run --locked --extra dev python tools/run_studio_project_service.py `
  --work-root D:\ZNIKU\runtime-data `
  --project D:\ZNIKU\phase5-smoke.zniku
```

另一个终端运行：

```powershell
cd apps/studio
npm ci
npm run dev
```

浏览器打开 Vite 输出的 loopback 地址。Studio 默认连接 `http://127.0.0.1:18765`；可通过
`window.__ZNIKU_STUDIO_API_BASE__` 为可信 LAN 开发环境显式替换。确认 Graph diagnostics 为 0 后点击
“开始处理”（高级层保留 `Run all`），在同一画布查看状态与发布结果，按需打开高级日志。
`--split-frame N` 可显式调整切分帧；只有明确
允许覆盖两个同名 smoke 输出时才增加 `--overwrite`。生产构建与门禁：

```powershell
npm run typecheck
npm run test:run
npm run build
npm audit --audit-level=low
```

示例工程包含 automatic Transform→Output 与 Split→两条独立 Transform→Merge→Output 两条分支。它只用于
短媒体最小验证，不是固定产品拓扑。也可以在同一 Studio Palette 自由添加 MR、Enhancement 或 FI external
preset，填写 `tool`、`model`、`tool_version` 后按 handoff 目标完成外部处理并 Submit。

## 实施路线

1. Phase 1（已完成）：Project、Graph Core、typed ports、DAG validator 与 SQLite `.zniku` Store；
2. Phase 2（已完成）：Scheduler、Node Runner、attempt、rerun-from-start、结果复用与 stale；
3. Phase 3（已完成）：GUI-0 接入 Project Service 和 Runtime，形成唯一正式 Studio；
4. Phase 4（已完成）：首批真实媒体节点与 automatic／manual_external；
5. Phase 5（已完成）：删除旧 Evidence/full/recovery/fixed pipeline 实现并完成最小验收。

当前仓库为公开开发仓库，但尚未包含开源许可证；公开可见不等于授予复制、修改或分发许可。
