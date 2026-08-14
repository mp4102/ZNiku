# ZMediaFlow

ZMediaFlow 是面向本地专业媒体处理的可编排工作流平台：以 **Workflow Studio** 为主要控制界面，
以确定性 **Runtime** 为执行与状态权威，并允许 Agent 作为可选的编排、诊断和运维助手。

- 当前版本：`0.1.0`
- 当前阶段：产品架构基线与 GUI-0 交互原型
- 当前能力：可视化演示电影级 DAG、章节展开、音视频独立支线、冻结与模拟运行
- 当前限制：尚未接入正式 Compiler、Runtime、Engine Registry，也不执行媒体 I/O

## 产品边界

ZMediaFlow 不是任意脚本工作流编辑器。它围绕媒体 Artifact、typed ports、scope、集合完整性、
证据、恢复和唯一 Final 建立领域约束。

```text
Workflow Studio
      ↓
Application Service
      ↓
Workflow Compiler → Frozen ExecutionPlan
      ↓
Deterministic Runtime
      ↓
Registered Media Engines
      ↓
Artifacts / Evidence / Final / ZBaton projection
```

Agent 可以通过受控接口创建草稿、解释诊断、辅助人工 handoff 和运维，但不是正式状态或完成判定的
权威。Studio 也不直接调用 Engine；所有媒体副作用必须经过 Compiler 与 Runtime。

## 仓库来源与隔离

本仓库由 AVEnhanceFlow 的 v3 架构与 GUI-0 孵化成果独立演进而来。`0.1.0` 是 ZMediaFlow 自身的
起始版本，不继承 AVEnhanceFlow 的版本号。

- AVEnhanceFlow v2.3.1 继续保留其生产工作流、Skill、工具、任务合同与发布历史；
- 本仓库不迁移 v2 Runtime、真实任务、媒体文件、Evidence、receipt、final 或本机配置；
- 后续复用媒体能力时，应通过 ZMediaFlow Engine Contract 接入，而不是复制固定流水线。

## 仓库结构

```text
ZMediaFlow/
├── apps/
│   └── studio/                  # React + TypeScript + React Flow GUI-0
├── docs/
│   └── architecture/            # 产品与 Studio 正式框架基线
├── .github/workflows/           # Studio 持续集成门禁
├── AGENTS.md                    # 项目协作红线
└── VERSION                      # 产品版本
```

## 运行 GUI-0

需要 Node.js `24.14.0`：

```powershell
cd apps/studio
npm ci
npm run dev
```

验证命令：

```powershell
npm run typecheck
npm run test:run
npm run build
npm audit --audit-level=low
```

## 设计基线

- [产品整体框架](docs/architecture/product-framework.md)
- [Workflow Studio 整体框架](docs/architecture/studio-framework.md)
- [ZBaton vNext 设计基线](docs/architecture/zbaton/design-baseline.md)

当前仓库为私有开发仓库，未授予开源许可证。
