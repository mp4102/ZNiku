# ZMediaFlow 项目约束

- 默认使用中文沟通、文档、模块说明、业务注释和测试说明；代码标识、协议字段及第三方名称保留英文。
- 产品定位固定为：Workflow Studio 是主要控制界面，Compiler 负责合法性与冻结，Runtime 是执行和状态
  权威，Engine 只实现版本化媒体能力，Agent 是可选客户端。
- Studio 不得直接调用 Engine、写正式状态、判定节点完成或绕过 Compiler／Runtime。
- WorkflowSpec、EngineManifest、ExecutionPlan、Runtime state 与 Evidence 必须各有唯一权威实现；前端 mock
  只能用于原型，不得演变为第二套合同。
- 工作流必须保持有向无环、typed ports 与 scope 兼容、集合完整，并且 Final 是唯一终端节点。
- 正式媒体操作默认 fail closed、no replace、可恢复且证据驱动；目录、文件名和完成消息不是完成证明。
- 真实媒体、任务根、Evidence、receipt、final、日志、模型、凭据、密钥和本机配置不得提交 Git。
- 新增或实质修改的 Python 模块使用中文模块 docstring；关键状态迁移、并发、幂等、恢复和外部副作用说明
  原因、边界与失败语义。
- 修改 Studio 后至少运行 typecheck、测试和生产构建；合同与 Runtime 后续建立各自的自动化门禁。
- 使用 Conventional Commits：`type(scope): 中文简短说明`，标题不超过 72 个字符。
- 未经用户明确授权，不执行 commit、push、PR、tag、release 或破坏性 Git 操作。
