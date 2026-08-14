# ZNIKU Studio GUI-0 原型

这是 [`studio-framework.md`](../../docs/architecture/studio-framework.md) 的首个可点击交互原型，用于验证：

- Workflow Designer、Expanded Plan 与 Run Monitor 三种图形视图；
- Engine / Operator 节点、多命名 typed ports、章节分支和 program 汇合；
- Demux、独立原始 AudioArtifactSet 支线、一次 Video encode、Mux 与 Final 分责；
- Palette、Inspector、Diagnostics 与冻结确认的信息密度；
- 冻结后只读和模拟 Runtime 状态叠加。

原型中的 Registry、Compiler、ExecutionPlan、digest 和 Runtime event 全部是内存中的 `mock` 数据。
它不读取或写入媒体，不生成正式 WorkflowSpec、Evidence、receipt 或 ZBaton，也不连接
AVEnhanceFlow v2.3.1 生产工作流。

## 本地运行

```powershell
cd apps/studio
npm ci
npm run dev
```

默认只监听 `127.0.0.1`。验证命令：

```powershell
npm run typecheck
npm run test:run
npm run build
```

## 供应链边界

直接运行依赖已经锁定为 React 19.2.8、React DOM 19.2.8 与 `@xyflow/react` 12.11.3，许可证均为
MIT。构建与测试工具锁定为 Vite 8.2.1、TypeScript 7.0.2、Vitest 4.1.10、jsdom 29.1.1 及
Testing Library；直接开发依赖使用 MIT 或 Apache-2.0 许可证。`package-lock.json` 固定完整传递依赖树，
安装后应保留 `npm audit` 为零漏洞的验证门。

当前原型使用 Node.js 24。jsdom 固定在 29.1.1，是因为 jsdom 30 要求 Node.js 24.15.0 或更高版本，
高于本轮开发环境的 Node.js 24.14.0。
