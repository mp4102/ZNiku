# ZNIKU Studio Phase 2A 正式 Designer 切片

本应用依据 [`workflow-authoring-compiler-baseline.md`](../../docs/architecture/workflow-authoring-compiler-baseline.md)
接入 Python 正式权威，当前验证：

- Source → Engine → Final 的最小正式图子集；
- Python `WorkflowDraftSnapshot`、Compiler diagnostics 与 revision authority；
- typed port 连线、断线、完整参数替换及 stale revision 失败语义；
- Python Schema 生成的 TypeScript DTO、Ajv runtime validator 与投影 digest 对账；
- Diagnostic 到 node、edge、port 或 parameter 的定位。

Studio 不是领域权威。宿主必须注入 `window.znikuAuthoringBridge`；缺少桥接、未知字段、版本或 digest
不一致时均 fail closed，不回退 `mock-data.ts`。测试 harness 会启动真实 Python authority 进程验证传输链，
但它不是产品 CLI、FastAPI 路由或正式持久化服务。

当前不实现 Registry、ExecutionPlan、Freeze、Runtime、真实 Engine 或媒体 I/O，也不生成 Evidence、
receipt、Final publication 或 ZBaton。

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

直接运行依赖已经锁定为 React 19.2.8、React DOM 19.2.8、`@xyflow/react` 12.11.3 与 Ajv 8.20.0。
构建与测试工具锁定为 Vite 8.2.1、TypeScript 7.0.2、Vitest 4.1.10、jsdom 29.1.1 及
Testing Library；直接开发依赖使用 MIT 或 Apache-2.0 许可证。`package-lock.json` 固定完整传递依赖树，
安装后应保留 `npm audit` 为零漏洞的验证门。

当前原型使用 Node.js 24。jsdom 固定在 29.1.1，是因为 jsdom 30 要求 Node.js 24.15.0 或更高版本，
高于本轮开发环境的 Node.js 24.14.0。
