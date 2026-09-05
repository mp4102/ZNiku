# ZNIKU Studio（0.2.0 实现 / v0.3.0 开发）

Studio 以一张自由媒体节点图同时承担编辑和 Runtime 状态展示。React 应用只维护未保存的画布 Draft；
`.zniku` Project、Graph 校验、Run、Artifact、日志及 external handoff 的正式语义全部来自 Python Project
Service。

Graph 与 Runtime 的唯一上位架构权威是
[`graph-core-baseline.md`](../../docs/architecture/graph-core-baseline.md)；v0.3.0 的展示、交互、桌面入口和
易用性从属于 [`studio-ux-baseline.md`](../../docs/architecture/studio-ux-baseline.md)。本应用不得恢复
0.1.0 Formal Designer 或 Real Acceptance 的第二套语义。

当前产品代码仍保持 `0.2.0` package 版本。v0.3.0 Phase 0–3 已实现独立 Presentation、Schema 参数表单、
创作者建项、HostBridge、Undo/Redo、兼容连接、纯展示分组与 CAS 自动保存。运行中心重构、双击交付和
新用户真实验收仍属于 Phase 4–6，完整证据见 [Phase 3 验收记录](../../docs/v0.3.0-phase3-acceptance.md)。

当前 0.2.0 Phase 1–5 与 v0.2.1 Phase 1–5 已实现产品面：

- 从工程精确版本 `NodeDefinition` 搜索、添加、拖动、连接、复制和多选删除节点；
- 使用 Python Schema 驱动的中文表单编辑参数，显示字段级错误、primary/advanced 分组，并在高级区保留同一
  `ParameterDraft` 的 raw JSON；
- 新建、打开、保存 SQLite-backed `.zniku`；
- Run all、Run to here、Rerun from here；
- 在同一节点卡片和 Inspector 显示 progress、completed、failed、stale、错误原因、Artifact 输出路径；
- 显示有界 stdout/stderr 与 external handoff 输入、目标路径，并提交外部输出。
- 按 Project 中的 Python exact definitions 分组展示 Source、Transform、Split、Merge、Encode、
  Mux、Output 与 MR／Enhancement／FI external presets，不复制第二份媒体合同。
- 通过独立 `GET /api/studio/presentations` 消费 exact `0.3.0` Presentation；缺失或损坏的第三方条目只产生
  diagnostic，并回退通用 Schema 表单。

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

## 画布编辑与保存

- Ctrl+Z 撤销，Ctrl+Shift+Z／Ctrl+Y 重做，Ctrl+S 保存。每次拖动或批量添加只有一条历史；
- 从输出拖到空白选择下一步，或使用“连接节点”键盘入口；选择多输出节点可一次为所有输出添加同一步骤；
- Inspector 编辑节点别名、展示分组与有序输入列表；顺序可以拖动或通过上下移动按钮调整；
- 参数表单必须显式“应用设置”。缺必填参数或 required input 的安全草稿可自动保存，但创建 Run 前仍由
  Python 完整校验；其余错误不允许写入工程；
- 冲突时保留本地编辑并停止自动保存；不要连续强制覆盖，使用“重新载入磁盘版本”明确放弃本地更改后恢复；
- 新工程使用 schema 3；旧 schema 2 首次保存时在工程旁保留 `.zniku-schema2-backup-<UUID>.zniku` 一致
  备份。别名、分组、折叠和 viewport 不进入 Run snapshot，普通刷新／重开保留已保存内容但清空撤销历史；
- 前后端需要同时更新；save、expand 与三个运行入口要求 exact session 和 storage revision，旧请求失败关闭。

## 验证

```powershell
npm run typecheck
npm run test:run
npm run build
npm audit --audit-level=low
```

`src/service/project-service.schema.json` 由 Python Pydantic 模型生成，是 Studio response 的唯一运行时
Schema；不得手写第二份同义 Schema。
