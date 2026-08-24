# ZNIKU

*Flesh out every frame.*

**让每一帧有血有肉。**

ZNIKU 是一个自由编排媒体处理节点、执行本地工作流并复用已完成结果的 GUI Studio。

## 0.2.0 重构状态

- 目标版本：`ZNIKU Studio 0.2.0`
- 开发分支：`v0.2.0`
- 当前阶段：**Phase 0–4 已完成——正式 Studio 已接入首批真实媒体节点；Phase 5 尚未实施**
- 唯一目标架构权威：[自由媒体图核心设计基线](docs/architecture/graph-core-baseline.md)
- 当前实现版本：`0.2.0`，公共入口为 `zniku.graph`、`zniku.project`、`zniku.runtime`、
  `zniku.project_service` 与 `zniku.media`
- 过渡期代码：`main@198d802` 的 `0.1.0` legacy implementation 仍保留为回归对照，Phase 5 删除

Phase 1 已建立 Graph Core 与 `.zniku` Store，Phase 2 已用新的最小 Run、NodeRun、Artifact 与 NodeResult
替换 Runtime 领域入口，Phase 3 已把自由节点画布接到这些 Python authority，Phase 4 已加入首批真实媒体
NodeDefinition、FFmpeg adapters、FFprobe 与轻量 validators。尚未清理的 Compiler、Evidence 与旧生成投影
继续保留各自的 `0.1.0` legacy contract version，只用于 Phase 5 前的回归，不再有 Studio 产品入口。

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

## Phase 1–4 公共内核

- `zniku.graph`：公开 `Graph`、`NodeDefinition`、`NodeInstance`、`Edge`、typed `PortSpec` 及
  `GraphValidator`；首批类型是 `MediaFile`、`VideoFile`、`AudioFile`、`DataFile`，插件可增加开放字符串
  类型，Phase 1 只接受大小写敏感的精确类型相等，不做隐式继承或转换。定义和实例使用精确版本，参数按
  NodeDefinition 的 JSON Schema 验证。
- `zniku.project`：公开 `Project` 与 `ProjectStore`；`.zniku` 是带 schema version 的单文件 SQLite
  authority，schema v2 在当前 Graph 之外保存普通 Run snapshot、attempt、Artifact、NodeResult、日志索引
  和 latest-result head；Phase 1 schema v1 工程会在严格结构校验后事务化迁移。
- `zniku.runtime`：公开最小 Runtime 模型、`Scheduler`、completed reuse／downstream stale 分析、支持
  三类 executor 的 `NodeRunner` 与运行编排服务。持久状态只有 `pending`、`running`、
  `waiting_external`、`completed` 和 `failed`；`ready`／`blocked` 只即时计算。
- `zniku.project_service`：提供严格 0.2.0 DTO 与只监听 loopback 的本地 HTTP host；Studio 可创建、打开、
  保存 `.zniku`，启动全图或目标祖先闭包 Run，从节点重新运行，轮询 attempt、日志与输出，并提交
  `manual_external` 声明目标。响应由 Python Schema 校验，浏览器不能替换 NodeDefinition executor、
  work root 或 handoff 路径。
- `zniku.media`：提供 SourceMedia、VideoTransform、SplitVideo、MergeVideo、EncodeVideo、MuxMedia 与
  OutputFile 的 exact definitions、Python adapters 和轻量 validators；MR、Enhancement、FI 是普通
  `manual_external` VideoTransform presets。详细合同见
  [首批媒体节点合同](docs/architecture/media-node-contract.md)。
- Project 保存前与读取后都会执行同一 Graph Validator；非法图、未知工程 schema、损坏数据和缺失的精确
  NodeDefinition 默认 fail closed。
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
| [`docs/architecture/graph-core-baseline.md`](docs/architecture/graph-core-baseline.md) | 0.2.0 唯一目标架构权威 |
| [`docs/architecture/media-node-contract.md`](docs/architecture/media-node-contract.md) | Phase 4 首批媒体节点的从属实现合同 |
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

## Phase 0–4 交付边界

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

Phase 4 自动化只证明单元测试和短真实媒体 smoke；它不代表长片性能、主观画质或最终操作者验收。Python 旧
实现和旧测试暂时保留为 legacy regression，最终清理属于 Phase 5。

## 过渡期仓库结构

```text
ZNiku/
├── src/zniku/
│   ├── graph/                   # 0.2.0 Graph 模型与唯一 validator
│   ├── project/                 # 0.2.0 SQLite .zniku Project Store
│   ├── runtime/                 # 0.2.0 Scheduler、Node Runner 与运行历史
│   ├── project_service/         # 0.2.0 Studio DTO、application 与 loopback host
│   ├── media/                   # 0.2.0 首批媒体 definitions、adapters、probe 与 validators
│   └── 其余模块                 # 0.1.0 legacy regression；Phase 5 清理
├── apps/studio/
│   └── src/studio/              # 0.2.0 唯一正式 Designer 与 Runtime overlay
├── tests/                       # legacy regression + 架构权威一致性门
├── docs/
│   ├── architecture/
│   │   └── graph-core-baseline.md
│   ├── archive/0.1.0/           # 旧架构历史归档
│   └── brand-baseline.md
├── AGENTS.md
└── VERSION                      # 0.2.0 产品实现版本
```

## 本地运行与验证

Python 需要 3.12 或更高版本及 `uv`；Phase 4 媒体节点还要求 `ffmpeg` 与 `ffprobe` 可从 `PATH` 解析。以下
门禁验证 0.2.0 Graph Core、Project Store、Runtime、媒体节点和过渡期 legacy regression：

```powershell
uv lock --check
uv sync --locked --extra dev
uv run --locked --extra dev python tools/generate_authoring_projection.py --check
uv run --locked --extra dev python tools/generate_project_service_schema.py --check
uv run --locked --extra dev pytest
uv run --locked --extra dev mypy --no-incremental src tests tools
uv run --locked --extra dev ruff check src tests tools
uv run --locked --extra dev ruff format --check src tests tools
```

无需真实媒体时，可以继续生成 Phase 3 合成工程验证 Project Service 与 handoff。要运行 Phase 4 短媒体
smoke，请先准备一个短参考片段，再指定输出目录并生成可自由编辑的示例工程；工具会创建尚不存在的工程父
目录与输出目录，但不会覆盖既有 `.zniku`：

```powershell
ffmpeg -version
ffprobe -version
uv run --locked --extra dev python tools/create_media_smoke_project.py `
  D:\ZNIKU\phase4-smoke.zniku `
  D:\Media\short-reference.mkv `
  D:\ZNIKU\phase4-output
uv run --locked --extra dev python tools/run_studio_project_service.py `
  --work-root D:\ZNIKU\runtime-data `
  --project D:\ZNIKU\phase4-smoke.zniku
```

另一个终端运行：

```powershell
cd apps/studio
npm ci
npm run dev
```

浏览器打开 Vite 输出的 loopback 地址。Studio 默认连接 `http://127.0.0.1:18765`；可通过
`window.__ZNIKU_STUDIO_API_BASE__` 为可信 LAN 开发环境显式替换。确认 Graph diagnostics 为 0 后点击
`Run all`，在同一画布查看各节点状态、日志与两个发布路径。`--split-frame N` 可显式调整切分帧；只有明确
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
5. Phase 5：删除旧 Evidence/full/recovery/fixed pipeline 实现并完成最小验收。

当前仓库为公开开发仓库，但尚未包含开源许可证；公开可见不等于授予复制、修改或分发许可。
