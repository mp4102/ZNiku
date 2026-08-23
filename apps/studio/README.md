# ZNIKU Studio 0.2.0 过渡说明

本应用的目标产品形态由
[`graph-core-baseline.md`](../../docs/architecture/graph-core-baseline.md) 唯一定义：Studio 使用同一张自由媒体
节点图完成编辑、运行控制、状态、日志和外部人工 handoff，不再维护 Formal Designer、Expanded Plan、
Run Monitor 与 GUI-0 四套入口。

## Phase 1 当前状态

Phase 1 已提供 Python `zniku.graph` 与 `zniku.project`，并将产品实现版本切换为 `0.2.0`。当前 React UI
仍主要来自 `main@198d802` 的 legacy implementation，尚未接入 Project Service：

- `src/gui0/` 已具备 Palette 添加、拖动、typed 连接、删除和最小 DAG 校验，是 Phase 3 正式 Studio 的
  交互起点；
- GUI-0 目前仍是浏览器内存 mock，不保存 `.zniku`、不调用新 Runtime，也不执行媒体；
- `formal/`、`generated/`、Python authoring bridge、Expanded Plan、Run Monitor 和 Real Acceptance 暂留作
  legacy regression，不是 0.2.0 架构权威；
- Phase 2 完成 Scheduler 与 Node Runner 后，Phase 3 才会把 GUI-0 接入真实 Project Service 和 Runtime，
  并删除双轨入口。

不得把当前 UI 的唯一 Final、scope、Freeze、Compiler digest 或固定运行图继续扩建成 0.2.0 产品合同。

## 本地运行

```powershell
cd apps/studio
npm ci
npm run dev
```

默认只监听 `127.0.0.1`。Phase 1 过渡回归门：

```powershell
npm run typecheck
npm run test:run
npm run build
npm audit --audit-level=low
```

这些命令只证明版本切换没有破坏 legacy Studio；Python Project Store 已完成，但 Runtime 和 Studio 接入仍未
完成。

## 供应链边界

直接运行依赖已经锁定为 React 19.2.8、React DOM 19.2.8、`@xyflow/react` 12.11.3 与 Ajv 8.20.0。
构建与测试工具锁定为 Vite 8.2.1、TypeScript 7.0.2、Vitest 4.1.10、jsdom 29.1.1 及 Testing Library；
`package-lock.json` 固定完整传递依赖树，安装后应保留 `npm audit` 为零漏洞的验证门。

当前原型使用 Node.js 24。jsdom 固定在 29.1.1，是因为 jsdom 30 要求 Node.js 24.15.0 或更高版本，
高于当前锁定开发环境的 Node.js 24.14.0。
