# ZNIKU

*Flesh out every frame.*

**让每一帧有血有肉。**

ZNIKU 是一个自由编排媒体处理节点、执行本地工作流并复用已完成结果的 GUI Studio。

## 0.2.0 重构状态

- 目标版本：`ZNIKU Studio 0.2.0`
- 开发分支：`v0.2.0`
- 当前阶段：**Phase 0 已完成——架构权威已切换；Phase 1 Graph Core 尚未实施**
- 唯一目标架构权威：[自由媒体图核心设计基线](docs/architecture/graph-core-baseline.md)
- 当前可运行代码：`main@198d802` 的 `0.1.0` legacy implementation，仅用于重构期间的回归对照

`VERSION`、Python package、旧合同常量和 Studio package 在 Phase 0 继续保持 `0.1.0`。这是刻意的版本
边界：本阶段只切换架构权威，不能把尚未重写的 0.1.0 Compiler、Runtime、Evidence 或前端投影伪装成
0.2.0。Phase 1 建立新的 Project 与 Graph Core 公共入口时再切换实现版本。

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

## Phase 0 交付边界

本阶段已经完成：

- 纳入并启用 0.2.0 Graph Core 基线；
- 同步 `AGENTS.md`、README 与 Studio 开发说明；
- 将全部 0.1.0 架构文档移入历史归档；
- 用自动化测试锁定“单一新权威、旧文档只在归档”的目录与引用规则。

本阶段没有实现 Project、SQLite `.zniku` Store、新 Graph 模型、Scheduler、Node Runner、正式 Studio 或真实
媒体节点；这些分别属于 Phase 1–4。旧实现和旧测试暂时保留为 legacy regression，最终清理属于 Phase 5。

## 过渡期仓库结构

```text
ZNiku/
├── src/zniku/                   # 0.1.0 legacy implementation；后续按 Phase 1–5 重写
├── apps/studio/
│   └── src/gui0/                # 0.2.0 正式 Studio 的交互起点，当前仍是 mock
├── tests/                       # legacy regression + 架构权威一致性门
├── docs/
│   ├── architecture/
│   │   └── graph-core-baseline.md
│   ├── archive/0.1.0/           # 旧架构历史归档
│   └── brand-baseline.md
├── AGENTS.md
└── VERSION                      # Phase 0 仍为 0.1.0 legacy implementation version
```

## 本地验证

Python 需要 3.12 或更高版本及 `uv`。以下门禁在 Phase 0 只证明旧实现未被权威切换破坏，不代表
0.2.0 Graph Core 已经完成：

```powershell
uv lock --check
uv sync --locked --extra dev
uv run --locked --extra dev python tools/generate_authoring_projection.py --check
uv run --locked --extra dev pytest
uv run --locked --extra dev mypy --no-incremental src tests tools
uv run --locked --extra dev ruff check src tests tools
uv run --locked --extra dev ruff format --check src tests tools
```

Studio 当前仍是 0.1.0 过渡实现；GUI-0 将在 Phase 3 接入真实 Project Service，并取代 Formal Designer、
Expanded Plan、Run Monitor 与 GUI-0 四套入口。当前可运行：

```powershell
cd apps/studio
npm ci
npm run dev
npm run typecheck
npm run test:run
npm run build
npm audit --audit-level=low
```

## 实施路线

1. Phase 1：Project、Graph Core、typed ports、DAG validator 与 SQLite `.zniku` Store；
2. Phase 2：Scheduler、Node Runner、attempt、rerun-from-start、结果复用与 stale；
3. Phase 3：GUI-0 接入 Project Service 和 Runtime，形成唯一正式 Studio；
4. Phase 4：首批真实媒体节点与 automatic／manual_external；
5. Phase 5：删除旧 Evidence/full/recovery/fixed pipeline 实现并完成最小验收。

当前仓库为私有开发仓库，未授予开源许可证。
