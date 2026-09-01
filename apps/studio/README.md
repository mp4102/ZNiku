# ZNIKU Studio 0.2.0

Studio 以一张自由媒体节点图同时承担编辑和 Runtime 状态展示。React 应用只维护未保存的画布 Draft；
`.zniku` Project、Graph 校验、Run、Artifact、日志及 external handoff 的正式语义全部来自 Python Project
Service。

唯一架构权威是 [`graph-core-baseline.md`](../../docs/architecture/graph-core-baseline.md)；本应用不得恢复
0.1.0 Formal Designer 或 Real Acceptance 的第二套语义。

当前 Phase 1–5 已实现产品面：

- 从工程精确版本 `NodeDefinition` 搜索、添加、拖动、连接、复制和多选删除节点；
- 编辑节点 JSON 参数，显示 required input、精确 `data_type`、`one` / `ordered_many` 与 DAG diagnostics；
- 新建、打开、保存 SQLite-backed `.zniku`；
- Run all、Run to here、Rerun from here；
- 在同一节点卡片和 Inspector 显示 progress、completed、failed、stale、错误原因、Artifact 输出路径；
- 显示有界 stdout/stderr 与 external handoff 输入、目标路径，并提交外部输出。
- 按 Project 中的 Python exact definitions 分组展示 Source、Transform、Split、Merge、Encode、
  Mux、Output 与 MR／Enhancement／FI external presets，不复制第二份媒体合同。

Studio 不实现 Compiler、Freeze、ExecutionPlan、scope、唯一 Final、固定 AVEnhanceFlow 拓扑或浏览器 mock
Runtime。Project Service 不可用或响应不符合 Python 生成 Schema 时失败关闭。

## 本地运行

确保 `ffmpeg` 与 `ffprobe` 可从 `PATH` 解析。先在仓库根目录启动 loopback Project
Service（默认 `127.0.0.1:18765`），再启动 Vite：

```powershell
uv run --locked --extra dev python tools/run_studio_project_service.py --work-root D:\ZNIKU\runtime-data
cd apps/studio
npm ci
npm run dev
```

可在加载 Studio 前设置 `window.__ZNIKU_STUDIO_API_BASE__` 覆盖 API 地址；默认只连接
`http://127.0.0.1:18765`。

## 验证

```powershell
npm run typecheck
npm run test:run
npm run build
npm audit --audit-level=low
```

`src/service/project-service.schema.json` 由 Python Pydantic 模型生成，是 Studio response 的唯一运行时
Schema；不得手写第二份同义 Schema。
