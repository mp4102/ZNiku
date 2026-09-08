# ZNIKU Studio v0.3.0 创作者体验架构基线

- 状态：**已批准的 v0.3.0 Studio 下位设计；Phase 0 已冻结**
- 日期：2026-09-04
- 上位架构权威：[`graph-core-baseline.md`](graph-core-baseline.md)
- 正式执行方案：[`../v0.3.0-execution-plan.md`](../v0.3.0-execution-plan.md)
- 实现起点：`v0.2.1@1d20cedb1154061b6b15a6701252e42d8f8adddd`
- 适用范围：ZNIKU Studio、Project Service 的 Studio wire、Project authoring 状态与本机 HostBridge

## 1. 文档定位与权威关系

本文冻结 ZNIKU Studio v0.3.0 的展示、交互、桌面入口、authoring 状态、易用性与首次用户验收合同，是
`graph-core-baseline.md` 的下位设计。发生冲突时无条件以上位架构权威为准。

本文不得定义或改写：

- NodeDefinition、PortSpec、Graph、Run、NodeRun、Artifact、attempt、reuse 或 stale 的领域语义；
- Scheduler、Node Runner、validator、manual external Submit 或媒体验收结论；
- AVEnhanceFlow v2.7.0 的 Chapter、Leaf、动态端口、输出命名或媒体规划算法；
- 第二张 Graph、Compiler、Freeze、领域级 Revision、ExecutionPlan、Evidence 或 canonical digest。

Python Graph Core、Project Service、Runtime 和节点 validator 继续是唯一正式语义来源。Studio 只允许：

1. 把正式合同投影成人类可理解的界面；
2. 收集用户意图并发起严格 mutation；
3. 展示服务端已经作出的状态、验证和失败结论；
4. 保存明确归类的 Project authoring 状态与本机 UI 偏好。

任何 TypeScript interface、React state、展示文案、图标、模板页面或 HostBridge 都不得成为第二套运行权威。

## 2. 产品目标与目标用户

v0.3.0 的第一目标用户是会使用视频编辑或 AI 视频工具，但不要求理解以下概念的本机视频编辑者：

- JSON、JSON Schema、UUID；
- `type_id`、definition version、executor、typed port、cardinality、ordinal；
- Run、NodeRun、Artifact identity、snapshot、stale reason；
- Python、npm、CLI、HTTP 服务或 stdout/stderr。

技术操作者与节点开发者是第二目标用户。高级能力必须完整保留，但不得成为第一目标用户完成正常任务的
前置知识。

北极星结果是：首次用户能够双击启动、选择本机媒体、创建或调整普通自由 Graph、用表单配置节点、开始
处理、理解真实进度、完成一次人工外部交接、恢复一次失败并找到最终成品。

正常创作者路径不得要求用户手写绝对路径、Project ID、Run ID、Artifact ID 或 raw JSON，也不得要求用户
打开终端或阅读日志。

## 3. 已冻结的产品原则

1. **同一 Graph**：创作者模式和高级模式始终编辑同一 Project、同一 Graph 和同一组正式参数。
2. **渐进披露**：默认只显示创作决策；身份、JSON、完整日志与诊断进入高级层。
3. **任务语言优先**：界面先回答发生了什么和下一步做什么，再提供内部状态名。
4. **真实进度**：automatic 只显示可测量进度；manual external 不伪造百分比或 ETA。
5. **显式副作用**：覆盖、删除、外部 Submit 和昂贵重跑必须说明影响并要求明确动作。
6. **保留自由性**：模板只生成普通 Graph；生成后仍可添加、删除、连接、分支和汇合。
7. **展示不影响执行**：名称、图标、分组、折叠、视口和模式切换不改变 Run snapshot 或 stale。
8. **未知输入不猜测**：展示缺失时安全降级；参数、Graph、状态和外部产物继续 fail closed。
9. **简单不等于隐藏真相**：默认层提供可操作解释，高级诊断永久保留正式错误与原始上下文。
10. **不以换皮冒充重构**：只改配色或按钮文案，而仍要求理解 JSON、ID 和 Runtime，不算完成。

## 4. 信息架构

Studio 只提供一个正式工作区，并在其中渐进披露三个层次：

```text
ZNIKU Studio
├─ 创作者工作区（默认）
├─ 高级节点图（精确编辑）
└─ 高级诊断（精确排错）
        │
        └── 同一 Project / Graph / ParameterDraft / Run authority
```

三者不是三套编辑器，不保存三份状态，也不提供相互转换或导入导出步骤。切换显示层级不得触发 Project
mutation、创建 Run、改变参数、登记 Artifact 或使节点 stale。

### 4.1 创作者工作区

默认层使用“导入视频”“画质增强”“补帧”“合并章节”“输出成片”等任务语言。节点卡片只突出用途、关键
参数、状态、可信进度和下一步；默认隐藏 exact identity、executor、raw JSON、ordinal、Run/Artifact ID 与
stdout/stderr。

任一时刻只突出一个上下文主操作：

| 正式状态 | 创作者主操作 |
| --- | --- |
| 尚未运行且 Graph 可运行 | 开始处理 |
| automatic 正在运行 | 查看当前进度 |
| `waiting_external` | 继续外部处理 |
| `failed` | 查看问题并重试此步骤 |
| `completed` | 查看输出 |
| authoring draft 暂不可运行 | 修复标出的设置或连接 |

### 4.2 高级节点图

高级节点图用于精确编辑，展示 exact `type_id@definition_version`、typed ports、executor、Schema、raw JSON、
ordinal、Run all、Run to here、Rerun from here 与历史 snapshot。raw JSON 仍编辑同一个 ParameterDraft，并
通过同一个 Python 保存与运行门禁。

高级节点图不能绕过 Schema、直接改写 Run/NodeRun 状态、伪造 Artifact、跳过外部 Submit 或执行任意命令。

### 4.3 高级诊断

高级诊断是以只读为主的运行透视层，用于回答“为什么不能运行、为什么失败、Runtime 实际做了什么、应该
从哪里恢复”。它可以展示：

- exact node/definition/port/executor identity；
- Run、NodeRun、Artifact、attempt 与 handoff identity；
- 当前 Graph 与 Run snapshot 差异、reuse/stale 的正式原因；
- Python 稳定错误码、原始 message、validator 结果和有界 stdout/stderr；
- 服务连接、资源通道、请求版本和失败绑定。

高级诊断不得成为第二套日志数据库或状态机。允许的恢复按钮仍只发起 Project Service 已定义的正式 command；
未知错误保留原始信息并失败关闭。敏感 token、凭据和未获授权的环境变量不得显示或复制到诊断内容。

## 5. Presentation 合同

### 5.1 权威与绑定

Python 提供严格、声明式 `PresentationCatalog`，通过 `type_id + definition_version` 精确绑定
NodeDefinition。Project Service 生成其 wire JSON Schema；Studio 必须按 exact contract version 验证后消费。

```text
PresentationCatalog
├─ contract_version = "0.3.0"
├─ locale = "zh-CN"
├─ categories[]
└─ nodes[]

NodePresentation
├─ type_id
├─ definition_version
├─ title / description
├─ category_id / icon_token / palette_level
├─ keywords[]
├─ parameter_groups[] / parameters[]
├─ ports[]
└─ card_summary_paths[]
```

Presentation 不进入 Graph、parameters、Run snapshot、execution signature、result reuse 或 stale 判断。纯展示
变更不要求提升 NodeDefinition version。

### 5.2 ParameterPresentation

ParameterPresentation 只允许提供：

- 已存在参数的 RFC 6901 JSON Pointer；
- label、description、group、order、primary/advanced 层级；
- 闭合的 control hint、单位、placeholder 与已有 enum 的人类标签；
- `file_path`、`file_paths`、`directory_path`、`save_file` 等 picker 提示。

它明确禁止携带或产生：

- required、default、minimum、maximum、pattern 或新的 enum；
- 自定义业务条件表达式、参数计算或值转换；
- executable、argv、Python/JavaScript callback；
- validator、executor、媒体合同或端口类型。

字段合法性、条件分支和默认值只从正式 JSON Schema 解释。路径扩展名过滤只是选择器提示，不代替 Python
probe 或 validator。

### 5.3 失败与降级

- 缺少第三方 Presentation：使用通用 Schema 高级展示，Graph 仍可打开和运行；
- 找不到 exact definition：隔离该 Presentation，不猜测相近版本；
- 引用未知参数、端口、category 或 icon：内建条目使 CI 失败，第三方条目隔离并显示非阻塞 diagnostic；
- 文本按纯文本渲染，不执行 HTML、SVG、脚本、文件路径或 URL；
- Presentation 损坏不得改变参数或 Runtime 结论。

## 6. ParameterDraft 与表单

表单和高级 raw JSON 操作同一份只存在于当前 Studio session 的 ParameterDraft：

1. 选择节点时从当前 NodeInstance parameters 创建副本；
2. 表单与 raw JSON 双向更新同一 draft；
3. 不支持安全渲染的 Schema 局部明确回退到高级 JSON，不猜测；
4. 非法 draft 不写回 Graph；
5. 用户选择“应用设置”后才更新正式 parameters；
6. 正式应用后按 Core 规则使当前节点和下游 stale；
7. 离开节点且有未应用内容时必须明确应用或放弃。

只使用 Schema 明示的 default。控件类型、字段名、旧值、Presentation 或模板文案都不得推测业务默认值。
隐藏字段若 required 或非法，必须自动提升显示具体原因，不能只禁用按钮。

Phase 1 的表单能力必须覆盖 [`studio-schema-corpus.json`](studio-schema-corpus.json) 固化的全部内建
Draft 2020-12 authoring shape。未列入 corpus 的 Schema 能力不在 v0.3.0 中凭空扩张。

## 7. Project authoring 状态

### 7.1 三类状态

| 分类 | 示例 | 保存位置 | 进入 Run snapshot | 导致 stale |
| --- | --- | --- | ---: | ---: |
| 执行 Graph | type/version、parameters、edges、ordinal | `.zniku` Project Graph | 是 | 按 Core 规则 |
| Project authoring | 节点别名、分组、折叠、viewport | `.zniku` StudioState | 否 | 否 |
| 明确非执行 Graph 字段 | 现有 `NodeInstance.ui_position` | `.zniku` Project Graph | 可随 snapshot 复制但不参与执行签名 | 否 |
| 本机偏好 | 创作者/高级模式、最近工程 | 本机设置 | 否 | 否 |
| session 临时状态 | 选区、未应用 ParameterDraft、Inspector tab | 内存 | 否 | 否 |
| Runtime 投影 | progress sample、readiness、日志 tail | Project Service 派生/进程内 | 否 | 否 |

不得把别名、分组、折叠、viewport、模式、选区或未应用 draft 塞入 NodeInstance parameters。

### 7.2 StudioState 与 schema 4 的兼容保存

```text
StudioState
├─ contract_version = "0.3.0"
├─ viewport
├─ groups[]
└─ node_views[]

NodeViewState
├─ node_id
├─ display_name (optional)
├─ collapsed
└─ group_id (optional)

GroupViewState
├─ group_id
├─ title
├─ color_token
└─ collapsed
```

实例显示名不要求唯一，稳定身份始终是 node_id。删除节点必须清理相应 NodeViewState；复制节点产生新的
node_id。StudioState 损坏时回退默认展示并报告 warning，不得损坏或改写 Graph、Run 或 Artifact。

StudioState 在 schema 3 引入；2026-09-08 批准的 schema 4 仅增加独立工程数据配置。旧 schema 2/3 读取保持
只读；显式写入升级必须事务化，升级前自动创建唯一、不覆盖既有备份的可恢复副本。迁移失败时保留
原工程并失败关闭；未知更高版本继续失败关闭。旧 Graph、definitions、历史 Run、Artifact、reuse 和 stale
结论不得被迁移改写。

### 7.3 storage revision 与保存

Project Store 使用单调 `storage_revision/ETag` 做乐观并发控制。它只是防止迟到页面覆盖新状态的存储计数，
不是领域级 Graph Revision、Freeze、digest 或运行权威。

- Graph draft 使用 debounce、single-flight、latest-wins 与 compare-and-swap 自动保存；
- 拖动过程折叠为一次保存和一次 undo action；
- 运行前必须 flush 最新可运行 Graph；
- 冲突必须显式处理，不自动覆盖较新状态；
- 自动保存失败保留本地 draft，不谎报“已保存”；
- 参数仍通过“应用设置”显式生效，避免浏览表单时意外 stale。

### 7.4 authoring draft 保存边界

同一 `Project.graph` 可以保存结构可解析、引用安全，但因缺少 required input 或 required parameter 而暂不可
运行的 authoring draft。确切保存/运行边界由上位 `graph-core-baseline.md` 定义，Studio 不得自行扩大。

Studio 必须区分：

- “已保存且可运行”；
- “已保存但暂不可运行”，并给出定位到节点、端口或字段的修复清单；
- “未保存”，并给出解析、引用、并发或服务失败原因。

不得建立隐藏的可运行 Graph、前端草稿 Graph 或隐形编译结果。Runtime 创建 Run 前始终调用唯一 Python
Graph Validator 完整验证。

## 8. 编辑与自由画布合同

Studio 必须继续支持多个 Source、零个或多个 Output、任意合法分支与汇合。模板和宏只生成普通 NodeInstance
与 Edge，不向 Runtime 增加固定流程语义。

v0.3.0 编辑体验必须具备：

- 友好节点名、业务图标和关键参数摘要；
- 连接时只高亮类型兼容目标；
- 拖到空白处选择兼容的下一步并可自动连接；
- Undo/Redo 覆盖添加、删除、移动、连接、断开、复制、参数应用、ordered_many 重排和批量宏；
- ordered_many 通过有序列表拖动，不要求用户输入 ordinal；
- 自动布局、适应画布、缩放到选区和搜索；
- 纯键盘可完成连接，不把拖线作为唯一入口。

2026-09-08 经操作者批准的画布修订：打开或重新打开工程默认展示当前完整 Graph，不因存在历史 Run
而自动切到旧 snapshot；运行记录、继续处理入口和状态仍可正常读取。显式选择历史记录、定位运行步骤或
发起 Run 时才进入相应只读 snapshot，切回当前图不改写历史。ReactFlow 的尺寸测量与拖动标记只属于画布
session 临时渲染状态，不写入 Graph、StudioState 或参数；持续拖动必须保持节点和连线可见，并继续遵守
一次手势一次 Undo/保存。回归测试必须验证实际可见性和保存重开后的坐标，不能只以交互耗时代替正确性。

UI 可以在提交前预防明显错误，但正式结论仍由 Python 返回。UI 不得按 type_id 手写参数合法性、Chapter/Leaf
规划或媒体算法分支。

## 9. 运行、错误与人工外部处理

### 9.1 真实状态和进度

automatic 节点只显示 executor 提供的可信 current/total/unit 或持久 fraction；没有测量时显示不确定进度，
不得推算假百分比。manual external 只显示已等待时间、目标文件观察和验证状态，不显示倒计时、完成百分比或
外部工具 ETA。

Run 的“已完成节点数”和当前 automatic 节点进度是两个维度，不得平均成媒体总体百分比。

### 9.2 错误翻译

默认问题卡片必须回答：

1. 发生了什么；
2. 哪些结果仍被保留；
3. 用户现在应该做什么；
4. 如何定位到对应节点、端口或字段。

友好文案只翻译稳定 Python diagnostic/Runtime failure，不决定 blocking、retry、Submit 或 command 资格。
未知错误码使用安全通用文案，同时在高级诊断保留 code、原始 message 与绑定；不得吞错或猜测成功。

### 9.3 外部处理助手

固定用户流程为：

```text
选中任务并查看输入与要求 → 在外部工具处理 → 选择处理好的文件并确认导入（或直接放入目标）
→ 检测目标文件 → 完整检查 → 显式提交并继续
```

文件存在只表示“已发现”，不得自动 Submit 或推进 NodeRun。最终仍按当前 Run/NodeRun/handoff identity、正式
目标路径、FFprobe 和节点 validator 完整重验。替换失败文件后必须重新完整检查，提交动作必须再次显式触发。

右侧助手只呈现选中节点的交接，不把整个 Run 的待办卡片嵌入每个节点。同名实例以稳定 Graph 顺序的展示
编号区分，并同时显示输入文件名、Python 提供的要求及任务目标；编号仅辅助显示，不改变 node_id、别名、
Graph 或执行身份。未选中任务时提供简明待办导航。已有 Run 仍以其 snapshot 和最新 attempt 为交接依据。

原生文件选择本身仍无媒体副作用。独立的 Project Service 导入操作采用“只读预览 → 明确确认”，绑定
picker selection handle、当前工程会话、Run、最新 waiting NodeRun、handoff 和输出端口；当前只支持恰好
声明一个单值输出的人工节点，多输出节点仍按各完整目标放置文件后统一检查。不接受源或
目标 raw path。预览说明源文件、目标任务及是否替换，取消不复制。确认只复制原文件到该 attempt 的独立
暂存目录，复用同一 Python 媒体检查和节点 validator，通过后才发布到正式目标；已有目标必须明确确认
替换，失败不得破坏旧目标。源文件不移动、不删除，导入不登记 Artifact、不自动 Submit。完成导入会使旧的
界面检查结果失效，仍需重新检查并显式提交。票据短时、一次性，会话或文件变化失败关闭；导入不扩展
HostBridge 六项 capability，不提供通用文件管理能力，也不改变 Runtime 状态机。

## 10. HostBridge

2026-09-08 经操作者批准的交接补充：新工程默认使用工程旁持久数据目录，并允许选专用磁盘。界面提供
“工程数据”入口，显示实际位置、永久保留策略、占用和工程外依赖；有数据时更改位置必须走明确的迁移
预览/确认，原件不自动删除。介质不可用时禁止静默回退到 AppData。

外部节点进入等待前自动创建独立收件目录。助手提供打开输入/收件目录、选择文件复制以及有界收件候选
列表；任意来件名称不等于任意媒体合同，多个候选必须显式选择。收纳前说明规范名称与是否替换，成功后
仍需正式检查与显式 Submit。复制中、候选换代、过期请求、错误归属或多输出歧义均不猜测完成。
所有命名与路径由 Python 提供，Studio 不解析文件名推导章节、leaf 或任务身份。

### 10.1 正式范围

v0.3.0 正式支持 Windows 同机 loopback。开发时可以使用普通浏览器；正式创作者入口必须支持双击启动、
原生路径选择和打开输出。LAN 可以继续使用 Graph/Runtime API，但不得调用本机选择器、文件管理器或播放器
能力。

HostBridge 使用以下闭合集合：

```text
open_file / open_files / select_directory / save_file
reveal_in_file_manager / open_with_system_player
```

它不能接收任意 executable、argv、shell 字符串、URL、脚本或未声明 action。

### 10.2 已选技术路线

Phase 0 已通过有界 prototype 选择且只保留 **loopback Project Service 原生对话框代理**。薄
WebView2/Python shell 不进入 v0.3.0；选择依据、否决理由和自动验证证据记录在
[`host-bridge-prototype.md`](host-bridge-prototype.md)。prototype 不是稳定 API，Phase 2/5 仍须完成正式
launcher、浏览器接入、真实原生对话框与打包验收。

无论选型为何，正式实现都必须满足：

- 只监听 loopback，使用 launcher 每次启动生成的高熵 session token，并限制精确 Studio Origin；
- 系统动作只接受 POST 和显式用户点击，不允许页面加载、轮询或 GET 触发；
- 路径选择只返回用户明确选择的运行主机绝对路径与句柄，不上传或复制媒体；第 9.3 节的独立导入必须另行确认；
- 取消选择不创建 Project、目录、Run、Artifact 或参数 mutation；
- Windows 的四种原生选择器共用临时置顶 owner，打开时只请求一次前台焦点；选择、取消或异常后销毁 owner，
  不把整个应用永久置顶，不循环抢焦点或修改系统策略。已有选择窗口时明确提示先完成或取消，不自动重试；
- 选择路径只更新 ParameterDraft，不代表文件或媒体合法；
- save picker 不提前创建、覆盖或删除目标；
- `reveal_in_file_manager` 与 `open_with_system_player` 的正式请求必须引用 Project Service 已返回的
  Artifact、handoff 或当次 picker selection handle，再由 Python 解析路径；前端提交的任意 raw path 不能成为
  系统动作 authority；
- 打开目录或播放器使用固定 OS API 或参数数组与 `shell=False`，不拼接 shell；
- capability 不可用时明确失败，不伪装成功或退回任意命令；
- token 不进入 URL、日志、Project、诊断导出或 Git。

## 11. 媒体预览

v0.3.0 只提供代表帧、处理前后 A/B 静帧和“使用系统播放器打开”。不建设 NLE 时间线、完整代理播放、剪辑、
调色或关键帧动画。

Preview cache：

- 不登记为 Artifact，不进入 `.zniku`；
- 不改变 Source、reuse、stale 或 validator 结论；
- 不作为 QC 或主观画质证明；
- 只允许删除经过解析和校验的 preview cache 根；
- 不进入 Git、正式输出或用户媒体目录。

## 12. 当前 GUI 失败基线

以下是 `v0.2.1@1d20cedb` 的已观察事实，用于衡量重构效果，不作为默认 CI 中的长期预期失败测试：

| 当前事实 | 对首次视频编辑者的障碍 | v0.3 关闭阶段 |
| --- | --- | --- |
| 需分别启动 Python Project Service 与 Vite/production server | 用户必须理解开发环境和端口 | Phase 2/5 |
| 顶栏要求输入 `.zniku` 绝对路径 | 用户必须手写主机路径 | Phase 2 |
| 新建工程要求 Project ID 与 Project name | 暴露内部身份并增加无意义决策 | Phase 2 |
| Palette 主显示 type/version/executor/port 数量 | 选择节点前必须理解内部合同 | Phase 1/3 |
| Inspector 以 raw JSON 和完整 parameter_schema 为正常入口 | 非开发者无法安全调参 | Phase 1 |
| Run all/Run to/Rerun、Run ID、Artifact 路径和状态英文占据主界面 | 任务目标被运行内部结构淹没 | Phase 4 |
| external handoff 依赖复制输入/目标路径并阅读 readiness | 人工处理不是被引导的任务 | Phase 4 |
| stdout/stderr 与原始错误直接占据 Inspector | 普通错误缺少原因、保留结果和下一步 | Phase 4 |
| 没有 Undo/Redo、原生 picker、自动布局和轻量预览 | 编辑错误难恢复、媒体选择与结果查看割裂 | Phase 2/3/5 |

当前 GUI 已经能够自由编辑、连接并通过真实 Project Service 运行，且 v0.2.1 Phase 5 的 MR-off 流程已由
操作者完成。这证明底层主路径可用，不证明目标用户易用性；v0.3.0 不得通过删除自由 Graph 或伪造流程来
降低表面复杂度。

## 13. 五条关键用户旅程

### Journey 1：启动、选择素材并生成工作流

首次用户双击启动，选择本机视频和处理方案，修改基础设置，查看 Python 生成的 preview，然后创建一个普通
可编辑 Graph。取消 picker、probe 失败或目标已存在均不产生隐形副作用。

2026-09-05 经操作者批准的输出体验修订：所选输出目录默认就是成品所在目录，不要求事先创建片名子目录。
“按片名创建子文件夹（适合媒体库整理）”为可选项。设置步骤显示 Python 返回的输出目录与只读检查结果，
确认步骤显示最终完整成品路径，并明确目录仅在开始处理后的输出步骤按需创建；选择、预览、创建工作流和
取消均不创建外部输出目录。命名和受控创建规则由 AV27 模板与通用媒体节点合同定义，不能在 TS 复制。
输出位置错误定位到输出设置，不显示成媒体分析失败；已完成分析保留，改位置后复用同一分析记录。

### Journey 2：自由编辑、配置并恢复工程

用户添加、连接、复制、删除和重排节点，以表单修改三个常见参数，使用 Undo/Redo，保存、关闭并重开后得到
同一 Graph、位置、别名、分组和顺序。暂未连接完整的安全 draft 可以保存，但不能运行。

### Journey 3：运行、理解进度并恢复失败

用户开始处理，区分步骤总览与当前节点真实进度；面对一个注入失败时能说明原因和下一步，从该节点创建新
attempt 重跑，同时复用有效上游。

### Journey 4：完成人工外部处理

用户从任务助手打开输入/工作目录，在外部工具完成输出，识别一个故意不合格的文件，替换后重新检查并显式
Submit；任何半成品都不能登记 Artifact。

### Journey 5：理解变更、复用并找到成品

用户修改一个正式参数或入边，理解下游为何 stale；新 Run 复用不受影响节点，完成后用户能预览并用系统
播放器打开最终输出。历史 Run snapshot 不被当前 Graph 编辑改写。

## 14. v0.3.0 首次用户验收门槛

- 至少 5 名未阅读仓库文档、不要求理解 JSON/DAG/Runtime 的真实首次目标用户；
- 另设 1 名技术操作者回归高级自由 Graph，不能替代目标用户；
- 每名首次用户执行五条 Journey 的代表任务；
- 使用纯合成素材或仓库外只读短媒体，真实路径、Project、输出和日志不进入 Git。

| 指标 | 最低门槛 |
| --- | --- |
| 无协助任务完成率 | 25 个任务至少完成 23 个，即 ≥92% |
| 核心端到端流程 | 至少 4/5 用户无人工提示完成 |
| 安全关键动作 | 5/5 不覆盖 Source、不提交非法产物、不误删上游 |
| 技术泄漏 | 默认路径要求 JSON、CLI、绝对路径、Run/Artifact ID 或日志的次数为 0 |
| 新建到合法工作流 | 中位数 ≤5 分钟，不含媒体处理时间 |
| 修改三个参数 | 中位数 ≤90 秒 |
| 添加并连接节点 | 中位数 ≤2 分钟 |
| 定位失败并发起正确重试 | 中位数 ≤2 分钟 |
| 外部产物检查与提交 | 中位数 ≤3 分钟，不含外部处理时间 |
| 错误理解 | 至少 4/5 能说清原因和下一步 |
| Single Ease Question | 各关键任务中位数 ≥5.5/7 |
| System Usability Scale | 总体中位数 ≥80 |
| 无障碍 | critical/serious 自动化问题为 0 |
| 领域正确性 | Source 改写、非法 Artifact、伪完成、伪进度均为 0 |

出现以下任一情况即失败，不能用平均分抵消：

- Source 被覆盖、修改或删除；
- 未验证输出被登记为 Artifact；
- UI 显示完成但 Runtime 未完成；
- Presentation 改变参数合同或验证结论；
- 默认任务必须进入 raw JSON、日志或终端；
- 当前 Run snapshot 被后续 Graph 编辑改变；
- Runtime 新增 AVEnhanceFlow 业务分支；
- 为易用性绕过显式覆盖确认或 manual external Submit。

## 15. 性能与无障碍门槛

- 核心流程必须可纯键盘完成，并有明确 focus-visible、焦点恢复、名称与错误关联；
- 状态不得只用颜色表达，支持 200% 缩放、高 DPI 和 reduced motion；
- 自动化无障碍扫描不得有 critical/serious 问题；
- 50 节点图的常用选择、拖动和参数展开 p95 目标不超过 150 ms；
- 200 节点/1000 Run 合成压力下不得无界加载历史或冻结主界面；
- 不得通过减少正式 validator、删除门禁或伪造完成来换取性能。

## 16. 明确非目标

- 不重写 Scheduler、Node Runner、attempt、reuse/stale 或 rerun-from-start；
- 不把 AVEnhanceFlow 固化进 Runtime，不新增固定全局拓扑；
- 不建设 NLE 时间线、完整代理播放、剪辑、调色或关键帧动画；
- 不控制 Jasna、Topaz 等外部 GUI，不伪造其内部进度；
- 不允许插件注入自定义 React、HTML、SVG 或 JavaScript 参数 UI；
- 不建设插件市场、账号、多用户、云协作、公网或 LAN 系统动作；
- 不恢复 Evidence、receipt、全局 checksum/full verification 或节点内部 checkpoint；
- 不把真实媒体、Project、日志、路径、preview cache、token 或本机配置提交 Git。

## 17. Phase 0 完成条件

Phase 0 只有同时满足以下条件才完成：

1. 本文、上位 Core 窄幅修订、`AGENTS.md`、README 与执行方案互相一致；
2. Presentation、ParameterDraft、StudioState、错误翻译和三类界面边界已冻结；
3. Schema corpus 由当前 Python definitions 生成并有 drift gate；
4. HostBridge 两种候选已完成有界评估，候选 B 已完成 capability prototype，只保留一种正式路线和明确
   失败语义；
5. 五条 Journey 与量化验收门槛已冻结；
6. 当前 GUI 失败基线有事实依据，不作为永久预期失败；
7. 没有修改 Runtime、媒体 adapter、reuse/stale、handoff 或 Artifact 验收行为；
8. 文档、Python gate、类型、格式、构建与仓库卫生检查通过。

## 18. 一句话基线

> **Studio 只把唯一 Python 权威翻译为创作者能够独立完成的任务；界面可以更简单，但事实、失败与自由 Graph
> 不能被复制、隐藏或改写。**
