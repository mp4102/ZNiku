# ZNIKU Studio（0.2.0 实现 / v0.3.0 开发）

Studio 以一张自由媒体节点图同时承担编辑和 Runtime 状态展示。React 应用只维护未保存的画布 Draft；
`.zniku` Project、Graph 校验、Run、Artifact、日志及 external handoff 的正式语义全部来自 Python Project
Service。

Graph 与 Runtime 的唯一上位架构权威是
[`graph-core-baseline.md`](../../docs/architecture/graph-core-baseline.md)；v0.3.0 的展示、交互、桌面入口和
易用性从属于 [`studio-ux-baseline.md`](../../docs/architecture/studio-ux-baseline.md)。本应用不得恢复
0.1.0 Formal Designer 或 Real Acceptance 的第二套语义。

当前产品代码仍保持 `0.2.0` package 版本。v0.3.0 Phase 0–3 已实现独立 Presentation、Schema 参数表单、
创作者建项、HostBridge、Undo/Redo、兼容连接、纯展示分组与 CAS 自动保存。Phase 4 已实现创作者运行中心、
外部处理助手与只读重跑影响预览，完整门禁与合成浏览器闭环已通过，见 [Phase 4 验收记录](../../docs/v0.3.0-phase4-acceptance.md)。
Phase 5 已加入双击桌面候选、轻量预览、无障碍与有界性能门禁；原生窗口点击验收仍待完成，不能宣称阶段关闭。
详见 [Phase 5 验收记录](../../docs/v0.3.0-phase5-acceptance.md) 和 [Windows 桌面说明](../../docs/studio-desktop.md)。
首次用户真实验收属于尚未开始的 Phase 6。

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

## 创作者启动

完整解压本地 Windows 候选目录，双击 `ZNIKU Studio.exe`，无需安装 Python/npm。选择工程和素材使用原生
选择器；退出使用界面“退出应用”，只关浏览器标签页不会停止服务。包的构建、来源与当前验收边界见桌面说明。

## 开发运行

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

## 运行与外部处理

- 默认层使用“开始处理”“继续外部处理”“查看问题并重试”等上下文主操作；精确命令与 identity 在高级层；
- automatic 只展示可信测量，外部工具只显示等待与文件观察，不显示倒计时或伪造百分比；
- 外部文件“已发现”不代表合格；先选择“检查输出”，通过后另行显式选择“提交并继续”；
- 替换文件后重新检查；页面提交前和正式 Runtime Submit 均保留完整验证，不会自动登记 Artifact；
- 重试先查看服务端返回的从头运行／可复用清单，取消没有副作用；确认时重新检查工程会话、存储版本与编辑；
- Run 历史按时间、目标和结果命名；默认轮询使用有界 summary，日志仅按需定向读取；
- 输出目录和播放器使用既有 HostBridge 引用；本阶段不增加任意系统命令能力。

`POST /api/studio/rerun-preview` 使用与正式 `rerun_from_here` 相同的严格请求，仅返回 `0.3.0`
`RerunPreviewEnvelope`。该投影不持久化执行计划，不代替实际 command 的完整校验。

## 验证

```powershell
npm run typecheck
npm run test:run
npm run build
npm audit --audit-level=low
```

`src/service/project-service.schema.json` 由 Python Pydantic 模型生成，是 Studio response 的唯一运行时
Schema；不得手写第二份同义 Schema。
