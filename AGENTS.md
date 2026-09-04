# ZNIKU 项目约束

## 架构权威

- `docs/architecture/graph-core-baseline.md` 是 ZNIKU Graph、Project、Runtime 与执行安全边界的唯一上位架构
  权威；`0.2.0` Core 及 `v0.3.0` 创作者体验重构都不得覆盖它。
- `docs/architecture/studio-ux-baseline.md` 是 `v0.3.0` 展示、交互、桌面入口和易用性的正式下位设计；与
  `graph-core-baseline.md` 冲突时无条件以上位基线为准。`docs/architecture/studio-schema-corpus.json` 只是内建
  Schema 的机器可读盘点，不是新的语义权威。
- `docs/v0.3.0-execution-plan.md` 规定阶段、门禁和验收顺序，不另行定义 Graph 或 Runtime 语义。
- `docs/archive/0.1.0/` 只保存 `main@198d802` 的历史实现说明，对 `0.2.0` 没有规范权威；不得从归档
  文档恢复旧约束，除非先修订并批准 `graph-core-baseline.md`。
- `docs/brand-baseline.md` 只负责正式命名与品牌语义，不另行定义运行架构。
- `v0.3.0` 开发线只重构 Studio 创作者体验，继续复用已经验收的 `0.2.0` Core 与 `v0.2.1` Runtime／
  AVEnhanceFlow v2.7.0 能力；不得以 UX 改造为由重新引入 `0.1.0` legacy architecture。

## 产品与领域边界

- 正式命名固定为：品牌 `ZNIKU`、产品 `ZNIKU Studio`、执行核心 `ZNIKU Runtime`、扩展体系
  `ZNIKU Engine SDK`、仓库 `ZNiku`、CLI／Python package `zniku`、工程文件扩展名 `.zniku`。
- 产品定位固定为：ZNIKU Studio 自由编辑和运行媒体节点图；Project Service 保存 `.zniku` 工程、Run、
  Artifact 与日志索引；ZNIKU Runtime 通过 Scheduler 与 Node Runner 执行 DAG；ZNIKU Engine SDK 将
  Python、命令行工具和人工外部流程包装为节点。
- Studio 不直接实现媒体算法；Runtime 不理解 MR、Enhancement、FI 等业务名称，只按 NodeDefinition
  执行节点。
- 创建 Run 时，Core 仍完整强制 node／port／edge 存在、output→input 类型兼容、required input、单值 input
  单入边、`ordered_many` ordinal 连续唯一和 DAG 无环。
- 同一个 `Project.graph` 可以保存仅缺少 required input 或 Schema `required` 参数的 authoring draft；这只是
  保存准入，不是运行放宽。创建 Run 前必须由同一 Python `GraphValidator` 完整校验，存在任何 diagnostic
  都不得创建 Run、snapshot、attempt、handoff 或 Artifact。
- authoring draft 采用闭合 allowlist。未知字段／定义／executor、悬空 node 或 port、类型不兼容、多入边、
  ordinal 错误、cycle 及其他参数 Schema violation 必须失败关闭并保持原工程不变；不得另存第二张 Graph、
  隐藏的可运行副本、Compiler、Freeze、ExecutionPlan 或领域级 Revision authority。
- 图允许多个 Source、多个 Output、零 Output、任意合法分支与汇合；不得重新引入全局
  `program/chapter/leaf` scope、复杂 ArtifactSet authority、唯一 Final 或固定 AVEnhanceFlow 拓扑。
- Graph 启动 Run 时只复制普通 snapshot；不得重新引入 Compiler／Freeze／Revision／ExecutionPlan 多层
  权威、canonical digest、全局 full verification、Evidence chain 或 receipt。
- 节点中断、取消或失败后只能从头创建新 attempt；不得实现节点内部 resume、checkpoint 或进度接管。
  已完成且仍有效的其他节点可以复用，直接输入或节点配置变化时下游必须 stale。
- SHA-256、Checksum、严格 QC、ZBaton 和归档 Manifest 只能作为可选节点或 Export 插件，不属于 Core
  完成条件。
- 创作者模式、高级节点图和高级诊断只改变信息密度，必须读写同一 Project、Graph 和参数。Presentation、
  实例别名、分组、折叠、视口及其他 UI-only 状态不得改变执行、reuse／stale 或验证结论。
- Presentation 只能是 Python 声明并经严格校验的纯数据，不允许插件注入 React、HTML、JavaScript、任意
  callback、命令模板或新的参数约束。

## 执行与安全卫生

- 目标环境是单用户本地工作站和操作者明确信任的 LAN，不建设公网、多租户、分布式 lease、插件签名
  或沙箱体系。
- `command` executor 必须传 executable 与 argv 数组并使用 `shell=False`；不得拼接任意 shell 字符串。
- 每个 attempt 使用独立工作目录。产生媒体输出时，只有受控进程成功退出（如适用）、声明输出存在且
  非空、FFprobe 可识别声明的媒体流并且节点轻量 validator 通过后，才登记 Artifact；非媒体输出按
  NodeDefinition 声明及其 validator 验收。
- 删除只允许落在已解析并验证的 attempt 工作目录；覆盖用户输出必须显式。不得误删工程、上游 Artifact
  或用户媒体。
- 真实媒体、任务根、旧 snapshot、Evidence、receipt、final、日志、模型、凭据、密钥和本机配置不得
  提交 Git，也不迁移到 0.2.0 Project。

## 开发协作

- 默认使用中文沟通、文档、模块说明、业务注释和测试说明；代码标识、API、协议字段及第三方名称保留
  English。
- 新增或实质修改的 Python 模块使用中文模块 docstring；关键状态迁移、并发、幂等、重试和外部副作用
  说明原因、边界与失败语义，不逐行翻译代码。
- Python 和 Markdown 文件使用 UTF-8。
- 修改 Studio 后至少运行 typecheck、测试和生产构建；Graph Core 与 Runtime 建立各自的自动化门禁。
