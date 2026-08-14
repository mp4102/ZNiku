# ZNIKU 0.1.0 Engine Contract Kernel

- 状态：**已批准的正式基线；Phase 1 Contract Kernel 已实现**
- 日期：2026-08-14
- 实现入口：`src/zniku/contracts/`
- 上位基线：[`product-framework.md`](./product-framework.md)

## 1. 定位

Engine Contract Kernel 是 ZNIKU Studio、Compiler、ZNIKU Runtime 和 ZNIKU Engine SDK 共享的 Python
领域语义来源。本内核只建立可运行、可测试的值对象和跨对象校验；Engine package、安装解析与候选输出
调用边界由 [`engine-sdk-baseline.md`](./engine-sdk-baseline.md) 继续冻结，Runtime 状态机仍不属于本文件。

Studio 的 TypeScript mock 不属于本合同。未来前端类型只能由 Python Schema 或正式 API 投影，不能
复制本模块后独立演化。

## 2. 模型职责与字段理由

| 模型 | 主要字段 | 职责与字段存在原因 |
| --- | --- | --- |
| `ArtifactType` / `MediaKind` | 受控枚举 | 前者区分 media、metadata、control；后者只细分 media，避免把 video、audio 与 program media 混成一个端口 |
| `Scope` | `program`、`chapter`、`leaf` | 表达 Artifact 与端口的处理范围；0.1.0 不做隐式升降级 |
| `PortSpec` | `port_id`、类型、scope、`cardinality` | 稳定标识命名端口，并在连接前进行 typed port 校验 |
| `Artifact` | `artifact_id`、分类、scope target、producer、`attributes` | 表示一份数据的稳定身份和直接来源；`attributes` 只保存惰性 JSON 声明，不执行字符串内容 |
| `CoverageSpan` | unit、左闭右开整数区间 | 以 frame、microsecond 或 item 精确表达集合覆盖，避免浮点时间 |
| `ArtifactSetMember` | `member_id`、Artifact、coverage | 将业务成员身份、Artifact 身份和集合内位置绑定 |
| `ArtifactSet` | set ID、预期成员顺序、实际成员、总体 coverage、producer | 只表示已经完整形成的有序集合；没有 `complete: bool` 这类可漂移状态 |
| `EngineInputContract` | `PortSpec`、preconditions Schema | 声明输入端口与已绑定 Artifact 属性必须满足的前置条件 |
| `EngineOutputContract` | `PortSpec`、guarantees Schema、attribute rules | 声明输出属性保证，以及 preserved、changed、not_guaranteed 语义 |
| `EngineLifecycleCapabilities` | acceptance、publication、recovery 三个支持标记 | 满足 Engine 必须显式声明验收、发布与恢复集成能力；精确生命周期协议仍留给后续专题 |
| `EngineManifest` | contract/Engine 版本、Engine ID、执行模式、lifecycle、scope、I/O、参数 Schema | 构成一项媒体能力的完整声明；digest 由整个 canonical manifest 动态计算，不保存可漂移副本 |
| `EngineBinding` | Engine ID、精确版本、manifest digest | 使 StageRun 不会因 Registry 中同名 Engine 后续变化而漂移 |
| `PortBinding` | port ID、Artifact 或 ArtifactSet 判别引用 | 只引用正式身份，避免在 StageRun 内复制另一份 Artifact authority |
| `StageRun` | run/stage/scope identity、Engine binding、参数、I/O bindings | 表示 Engine-backed StageRun 的最小冻结绑定；输出可以尚未绑定，但空值不代表任何 Runtime 状态 |

## 3. 引用关系

```text
EngineManifest
├─ EngineInputContract  ─ PortSpec
├─ EngineOutputContract ─ PortSpec + MediaAttributeRule
├─ parameter_schema
└─ EngineLifecycleCapabilities

StageRun
├─ EngineBinding ─ engine_id + exact version + EngineManifest digest
├─ input PortBinding  ─ ArtifactRef / ArtifactSetRef
└─ output PortBinding ─ ArtifactRef / ArtifactSetRef

ArtifactSet
└─ ordered ArtifactSetMember
   └─ Artifact
```

`validate_stage_run_bindings()` 需要调用方提供按正式 ID 建立的 Artifact authority mapping。校验器会
核对 mapping key、引用 ID 与对象自身 ID，确认 scope target、cardinality、媒体属性 Schema、输出
producer 和 preserved 规则。它不从目录或文件名猜测任何身份。

## 4. 已实现不变量

1. 所有模型严格拒绝未知字段、未知枚举、非 JSON 值和非 canonical 数字域。
2. Engine 使用小写、非路径式稳定 ID；Engine 版本使用精确 SemVer 2.0.0，不接受范围、通配符、
   `latest` 或 `v` 前缀。
3. output→input 兼容要求 `ArtifactType`、`MediaKind`、`Scope` 精确相同；cardinality 只允许
   `one→one`、`one→optional`、`optional→optional`、`set→set`。
4. 0.1.0 EngineManifest 固定到一个精确 scope；显式 scope 转换属于未来 Runtime 图操作。
5. ArtifactSet 的预期成员与实际成员必须唯一、无缺失、无额外项且顺序完全一致。Sequential coverage
   必须连续无重叠；parallel 成员必须各自覆盖总体区间。
6. 参数 Schema 固定为 Draft 2020-12 的受限安全子集。根和所有参数 object 都必须
   `additionalProperties: false`；unknown keyword、外部引用、组合 Schema、regex、无界 array/string
   和自相矛盾的基本 bounds 均失败关闭。首版把 `maxItems` 封顶为 256；`default` 只校验，不静默物化。
7. 模型没有 command、entrypoint、argv、environment 或 executable 字段。声明式 JSON 对危险语义键
   另做保守拒绝；所有字符串始终是惰性数据，未来 Runtime 不得进行 shell 拼接或解释执行。
8. `model_copy(update=...)` 会重新完整校验和冻结，不能绕过模型不变量。JSON 解析递归拒绝重复对象键。
9. 序列化使用 RFC 8785/JCS；对象键 canonical 排序，数组顺序保留，默认值和 `null` 参与摘要。
   digest 格式为 `sha256:<64 lowercase hex>`。
10. StageRun 绑定的 Engine ID、版本和 manifest digest 必须精确匹配；直接输入输出必须匹配 scope target。
    输出 ArtifactSet 及其每个成员的 producer 都必须是当前 StageRun；no-replace 会递归覆盖成员 Artifact ID。
    SET preserved 还要求两端逻辑成员、顺序与 coverage 对齐，再逐成员比较声明属性。

结构字段错误由 Pydantic `ValidationError` 返回；跨对象关系错误使用带 `code` 的
`ContractViolation`。0.1.0 尚未把两类错误统一成一个公共诊断信封，调用方不得解析中文消息作为
长期协议。

## 5. 本内核明确不处理

- WorkflowSpec、WorkflowRevision、DAG、Compiler 或 ExecutionPlan；
- Runtime 状态机、ready set、并发、lease、重试、恢复执行或副作用；
- Engine package、Installed Catalog、allowlist 和调用 adapter；这些由 Engine SDK 基线独立负责；
- Evidence、receipt、Final、ZBaton producer 或历史继承；
- 媒体路径、媒体探测、解码、哈希、文件发布或任何媒体 I/O；
- Studio/GUI 连接、CLI、`.zniku` 工程格式或 TypeScript 类型生成；
- AVSplitTool、AVEnhanceFlow 迁移或真实任务导入。

当前 `StageRun` 是 Engine-backed 最小子集。未来 Runtime 内建的 Partition、Map、Select、Collect、
Reduce 与 Final 不得伪装成 Engine；其执行主体判别联合须在 Runtime/ExecutionPlan 专题中另行冻结。

## 6. 已冻结的 0.1.0 协议决策

以下选择已经作为 ZNIKU 0.1.0 Contract Kernel 的正式协议冻结：

- `ArtifactType` / `MediaKind` 的首批枚举和 Engine ID grammar；
- SemVer 允许 prerelease/build，但拒绝版本范围；
- EngineManifest 当前只允许一个精确 scope；
- `one→optional` 的 cardinality 兼容；
- coverage 的左闭右开整数区间、三个单位及 sequential/parallel 模式；空集合仅允许零长度 coverage；
- Draft 2020-12 受限关键字子集、64 KiB Schema、1 MiB instance、256 个 array item 上限、default 不物化；
- `attributes` 和参数中的执行语义键保守拒绝规则；它是纵深防御，不替代 Runtime allowlist；
- RFC 8785/JCS、所有默认值与 `null` 入 digest，以及 `sha256:` 文本前缀；
- lifecycle 暂用三个 capability 布尔值，尚未定义验收/发布/恢复协议细节；
- 输出缺省不表示状态，StageRun 状态与 Runtime operator identity 后续另行建模；
- 当前公共 Python 导入面以及 `ValidationError` / `ContractViolation` 两层失败 API。

改变上述任一序列化语义都必须同步提升相应合同版本，并更新模型、Schema、fixtures、Python→Studio
投影和测试；不得在同一个精确合同版本下静默改变。
