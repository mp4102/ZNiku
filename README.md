# ZNIKU

*Flesh out every frame.*

**让每一帧有血有肉。**

ZNIKU 是面向本地专业媒体处理的可编排工作流平台：以 **ZNIKU Studio** 为主要控制界面，
以确定性 **ZNIKU Runtime** 为执行与状态权威，并允许 Agent 作为可选的编排、诊断和运维助手。

- 当前版本：`0.1.0`
- 当前阶段：Phase 1–4 的 Python authority 与 Phase 6 开发门已建立；GUI-0 交互原型已恢复为显式
  mock 工作区，产品 Phase 5 的正式自由编排、持久 Authoring host 与发布形态仍未完成
- 当前能力：Contract Kernel、Engine SDK/Installed Catalog、纯合成 Demux/Mux conformance、Workflow
  Authoring、Core Operators、Preflight、ExecutionPlan、Freeze、默认工作流、人工 handoff、full
  verification、合成 Runtime、Application Service、受控 Agent 工具、GUI-0 自由编排原型、Studio
  正式 Designer 最小投影、Expanded Plan/Run Monitor、可安装 Decensoring/`new01` 扩展示例、ZBaton vNext draft.2
  开发期投影，以及短真实媒体/目标存储/长片规模验证门均由 Python 提供唯一领域语义
- 当前真实媒体候选：受信 FFmpeg Demux/HEVC Main10 encode/Mux、人工 handoff 验收 fixture、原始音频逐流
  bitstream 证明、no-replace Final、canonical snapshot 恢复及 Studio loopback Monitor 已实现；fixture 只验证
  人工生命周期，不冒充 Starlight/Chronos 画质结果
- 当前限制：正式 Designer 尚无产品级 Authoring host、Draft/EditorState 持久化与完整自由增删节点体验；
  Decensoring/`new01` 仍是合成 Engine，ZBaton vNext 尚待 `ZBatonProtocol-Media` 发布正式 SDK，且尚未
  实现生产模型授权/GPU 调度、产品 CLI、`.zniku` 工程格式、Tauri 桌面封装、目标 NAS 认证或 production release

当前正在实施的真实媒体纵向候选及其 fail-closed 完成门见
[Real Media Acceptance Candidate 基线](docs/architecture/real-media-acceptance-candidate-baseline.md)。

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

当前仓库已建立 `zniku.contracts` 领域内核与 `zniku.authoring` 编排 authority。正式 Studio Designer
只通过受控 authoring gateway 消费 Python 权威响应；宿主未提供桥接时明确显示 authority unavailable，
绝不静默回退 mock。GUI-0 作为单独、显著标记 `MOCK · NO MEDIA I/O` 的交互原型保留，用于自由节点
编排体验验证，但其状态不得冒充 WorkflowSpec、Compiler 或 Runtime authority。`zniku` CLI 与 `.zniku`
工程格式仍只是等待后续实现的正式标识。

## 产品边界

ZNIKU 不是任意脚本工作流编辑器。它围绕媒体 Artifact、typed ports、scope、集合完整性、
证据、恢复和唯一 Final 建立领域约束。

```text
ZNIKU Studio
      ↓
Application Service
      ↓
Workflow Compiler → Frozen ExecutionPlan
      ↓
ZNIKU Runtime
      ↓
Registered Media Engines
      ↓
Artifacts / Evidence / Final / ZBaton projection
```

Agent 可以通过受控接口创建草稿、解释诊断、辅助人工 handoff 和运维，但不是正式状态或完成判定的
权威。Studio 也不直接调用 Engine；所有媒体副作用必须经过 Compiler 与 Runtime。

## 仓库来源与隔离

本仓库由 AVEnhanceFlow 的 v3 架构与 GUI-0 孵化成果独立演进而来。`0.1.0` 是 ZNIKU 自身的
起始版本，不继承 AVEnhanceFlow 的版本号。

- AVEnhanceFlow v2.3.1 继续保留其生产工作流、Skill、工具、任务合同与发布历史；
- 本仓库不迁移 v2 Runtime、真实任务、媒体文件、Evidence、receipt、final 或本机配置；
- 后续复用媒体能力时，应通过 ZNIKU Engine SDK 与 Engine Contract 接入，而不是复制固定流水线。

## 仓库结构

```text
ZNiku/
├── src/zniku/contracts/         # Phase 1A Python 领域合同内核
├── src/zniku/engines/           # Phase 1 Engine SDK、Installed Catalog 与 conformance Engine
├── src/zniku/authoring/         # Phase 2A Draft authority 与 Compiler front-end
├── src/zniku/workflow/          # Phase 2 Core Operators、Plan、Freeze 与 Runtime Core
├── src/zniku/pipelines/         # Phase 3 默认工作流与验证发布纵向切片
├── src/zniku/application/       # Phase 4 Studio/Agent 共用 Application Service
├── src/zniku/agent/             # Phase 4 无状态、窄化 Agent adapter
├── src/zniku/studio/            # Phase 5 Python→Studio 正式 authority 投影
├── src/zniku/history/           # Phase 6 ZBaton vNext draft.2 开发期历史投影
├── src/zniku/validation/        # Phase 6 短媒体、no-replace publication 与性能门
├── src/zniku/realmedia/         # 0.1.0 真实媒体候选、持久 Runtime 与 loopback host
├── tests/                       # 合同、Compiler、Runtime、媒体与 authority 回归测试
├── apps/
│   └── studio/                  # 正式 Designer 最小切片及 Python 单向投影
├── docs/
│   ├── brand-baseline.md        # 品牌、产品与代码标识权威
│   └── architecture/            # 产品与 Studio 正式框架基线
├── pyproject.toml                # Python package 与质量门禁
├── .github/workflows/           # Python 合同/投影与 Studio 持续集成门禁
├── AGENTS.md                    # 项目协作红线
└── VERSION                      # 产品版本
```

## 运行 Studio Designer

需要 Node.js `24.14.0`：

```powershell
cd apps/studio
npm ci
npm run dev
```

浏览器应用需要宿主注入 `window.znikuAuthoringBridge`。未注入时 Designer 会 fail closed 并显示
`AUTHORITY UNAVAILABLE`；测试专用 Python bridge 只用于端到端合同验证，不是产品 CLI 或正式 API。

需要体验自由节点编排时，在顶部进入 `GUI-0 Prototype`。该独立工作区支持从 mock Registry 添加节点、
拖动、typed handle 连线、删除、编译预览、冻结和模拟 Run Monitor，并始终显示
`MOCK · NO MEDIA I/O`；它不会静默替代正式 Designer，也不会执行媒体或写入 Python authority。

真实媒体候选 host 只监听 loopback，参考源和工作根由启动参数固定：

```powershell
uv run --locked --extra dev python tools/run_real_media_candidate_host.py `
  --reference "D:\ABP-811 (2018)\ABP-811.mkv" `
  --root "D:\ZNIKU-runtime-data\real-media-acceptance"
```

Studio 的 `Real Acceptance` 视图通过 `http://127.0.0.1:8765` 启动、推进和监控该 Run。实际媒体、snapshot、
Evidence 与 final 只存在于被 Git 忽略的本地工作根，不得提交仓库。

验证命令：

```powershell
npm run typecheck
npm run test:run
npm run build
npm audit --audit-level=low
```

## 设计基线

- [品牌与命名基线](docs/brand-baseline.md)
- [产品整体框架](docs/architecture/product-framework.md)
- [ZNIKU Studio 整体框架](docs/architecture/studio-framework.md)
- [Workflow Authoring 与 Compiler 基线](docs/architecture/workflow-authoring-compiler-baseline.md)
- [ZBaton vNext 设计基线](docs/architecture/zbaton/design-baseline.md)

当前仓库为私有开发仓库，未授予开源许可证。

## 验证 Contract Kernel

需要 Python 3.12 或更高版本及 `uv`：

```powershell
uv sync --locked --extra dev
uv run --locked --extra dev pytest
uv run --locked --extra dev mypy --no-incremental src tests tools
uv run --locked --extra dev ruff check src tests tools
uv run --locked --extra dev ruff format --check src tests tools
```

Engine Contract 的模型职责、引用关系、失败语义和正式决策见
[Engine Contract Kernel](docs/architecture/engine-contract.md)；Engine package 与调用边界见
[Engine SDK 基线](docs/architecture/engine-sdk-baseline.md)；执行与 Runtime 语义见
[Workflow Execution 与 Runtime Core](docs/architecture/execution-runtime-baseline.md)；默认流程见
[默认工作流与验证发布基线](docs/architecture/default-workflow-baseline.md)；Agent 工具边界见
[Application Service 与 Agent 工具基线](docs/architecture/agent-application-baseline.md)；正式 GUI 见
[Studio 正式工作区基线](docs/architecture/studio-formal-baseline.md)。

Phase 6 的扩展 Registry、ZBaton draft authority 边界、短真实媒体、目标存储、故障注入和长片规模门见
[Phase 6 扩展、历史投影与发布验证基线](docs/architecture/phase6-extension-validation-baseline.md)。独立
开发门可运行：

```powershell
uv run --locked --extra dev python tools/run_phase6_validation.py
```
