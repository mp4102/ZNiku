# ZNIKU Studio（v0.3.4 开发线）

Studio 以一张自由媒体节点图同时承担编辑和 Runtime 状态展示。React 应用只维护未保存的画布 Draft；
`.zniku` Project、Graph 校验、Run、Artifact、日志及 external handoff 的正式语义全部来自 Python Project
Service。

Graph 与 Runtime 的唯一上位架构权威是
[`graph-core-baseline.md`](../../docs/architecture/graph-core-baseline.md)；v0.3.0 的展示、交互、桌面入口和
易用性从属于 [`studio-ux-baseline.md`](../../docs/architecture/studio-ux-baseline.md)。本应用不得恢复
0.1.0 Formal Designer 或 Real Acceptance 的第二套语义。

当前 package/product 版本为 `0.3.4`；已有 wire/Presentation/StudioState 仍为 `0.3.0`，SQLite schema 4
不变。保留素材检查与准备的 `0.3.4` 和 `0.3.4-color.1` wire/exact 定义；新建普通路线使用
`0.3.4-work.1`，旧定义的版本与含义保持不变。
新入口遵守[普通工作源政策](../../docs/v0.3.4-working-source-policy.md)，展示“可直接处理”“需要准备
工作副本”“当前处理器不支持”三种后端结论，不要求创作者理解内部 Admission/T1。
正常源不复制；缺色彩本身不要求副本，必要的工作解释常驻显示并由用户明确确认，不能覆盖已知冲突。
内置普通副本显式保留解码帧数和顺序并重新定时；可能改变节奏与时长，必须先确认影响和额外空间，
不声称原时间轴保全。外部结果作为新参考重新检查和规划，明确采用其自身音频，不暗中沿用旧原音轨。
文件出现不自动提交。进度、取消、失败后新 attempt、有效上游复用仍使用同一 Runtime 和外部助手。
“高级 · 旧严格准备路线与兼容流程”可明确选择旧保内容比较路线；旧 T1 仍未晋级并保持禁用，
普通工作副本不是 T1 的改名或放宽。旧工程按实际 exact 恢复，不迁移已有图或等待任务。
当前新候选使用 `Studio-v0.3.4-work-candidate` 本机状态通道，与旧技术包隔离；尚未进行真实媒体验收。
v0.3.0 Phase 0–3 已实现独立 Presentation、Schema 参数表单、
创作者建项、HostBridge、Undo/Redo、兼容连接、纯展示分组与 CAS 自动保存。Phase 4 已实现创作者运行中心、
外部处理助手与只读重跑影响预览，完整门禁与合成浏览器闭环已通过，见 [Phase 4 验收记录](../../docs/v0.3.0-phase4-acceptance.md)。
Phase 5 已加入双击桌面候选、轻量预览、无障碍与有界性能门禁；原生窗口点击验收仍待完成，不能宣称阶段关闭。
详见 [Phase 5 验收记录](../../docs/v0.3.0-phase5-acceptance.md) 和 [Windows 桌面说明](../../docs/studio-desktop.md)。
后续顺序已由 [v0.3.1 A/B/C 执行方案](../../docs/v0.3.1-ui-optimization-plan.md)承接。A/B 已完成，
C 的具体技术候选、原生操作及首次用户证据见[本轮验收](../../docs/v0.3.1-desktop-candidate-acceptance.md)，
不沿用旧包或旧成品替代新界面验收。

历史 [v0.3.2 执行计划](../../docs/v0.3.2-execution-plan.md)提供独立重叠 FI 流程，
三种章节切分与独立1–60分钟分叶(默认5)均由Python规划，GUI只显示预览，不改变旧AV27精确定义。
FI软件v1.0、模型Aion已通过首轮1080p三章及操作者对照；4K本轮暂缓，其他能力范围仍待验，
候选标记不变，详情见[Phase 5统一真实验收](../../docs/v0.3.2-phase5-acceptance.md)。
旧候选保留在各自独立工作树与本机状态通道，不以新版覆盖操作者正在验收的包或数据。

保留的旧方案按 [v0.3.3 执行计划](../../docs/v0.3.3-execution-plan.md)实施原片规划与可选外部前处理：

- 选择此保留方案时使用 `zniku.source-aligned-overlap@0.3.3`；只有真实服务目录提供全部新 exact 定义时
  才启用。旧后端只保留旧方案，不因新版前端存在方法就伪称支持，也不在失败后隐式降级。
- 第三页为“处理与成片设置”：片名/年份，0 可选外部马赛克修复（默认关闭），1 分章分叶，2 增强，
  3 重叠 FI，4 编码。必要声明常驻；外部修复可声明 MP4/MOV/MKV，默认 MP4，不靠更名伪装容器。
- 原片分析始终只创建 Source/Admission，开启外部修复也不阻塞分析。确认后才生成带可选第 0 步的普通图，
  修复结果经同一外部助手导入、检查和明确提交，才开始分章；原音频始终来自原片。
- 可返回前面步骤查看或修改未确认的处理草稿。工程与素材身份创建后只读，处理设置变更复用准确分析，
  不回写已有 Run snapshot。已经展开或自由编辑的图必须在节点图继续配置，向导不会整图覆盖。
- 旧 `0.3.2` overlap 与 AVEnhanceFlow v2.7.0 保留明确选项；旧工程重开默认沿用旧方案，旧 MR-on
  显示真实配置与“打开外部任务”入口，不显示假关闭、不自动转换目标或迁移旧图。
- 当前不支持含音频 priming 的原片直接封装时，预览以 `E_SOURCE_ALIGNED_AUDIO_PRIMING_UNSUPPORTED`
  提前停止；不会自动转码、裁音或猜测补偿。合成门禁不代表真实外部 AI 画面与音画同步验收完成。

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

以下是单实例通用开发方式。与既有验收环境并行时，不复用默认端口/状态目录/工程；必须使用独立实例。
自动化只启动自己拥有的合成 fixture 服务，不操作用户已打开的浏览器、真实媒体或既有服务。

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
- 新工程使用 schema 4；受支持的旧 schema 2/3 在需要写入升级时先保留一致备份；本版分章预览不触发迁移。
  别名、分组、折叠和 viewport 不进入 Run snapshot，刷新／重开保留已保存内容但清空撤销历史；
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
npx playwright test
npx playwright test -c e2e/batch-b.config.ts
npx playwright test -c e2e/batch-c.config.ts
```

`src/service/project-service.schema.json` 由 Python Pydantic 模型生成，是 Studio response 的唯一运行时
Schema；不得手写第二份同义 Schema。

默认 production E2E 包括旧 production/overlap 兼容及新的 `source-aligned.spec.ts`；后者覆盖返回不写入、
MR off/on 原片分析不等待、MP4 导入取消/复制/检查/Submit，以及旧 MR 等待工程重开。
截图与有界诊断只写测试输出目录，不作为真实媒体或外部模型能力的证明。
