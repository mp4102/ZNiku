# ZBaton vNext 设计基线

> [!IMPORTANT]
> **0.1.0 历史归档：** 本文只描述旧 ZBaton 设计上下文，对 ZNIKU 0.2.0 没有规范权威。当前唯一目标
> 架构见 [`graph-core-baseline.md`](../../../architecture/graph-core-baseline.md)。

- 状态：**已采纳的设计基线**
- 基线修订：1
- 日期：2026-08-14
- 目标版本：ZBaton vNext、ZNIKU

## 1. 文档定位

本文冻结 ZBaton vNext 的业务定位、整体结构、处理历史模型、媒体快照、Workflow 继承、DAG
约束和版本边界，作为后续规范、Schema、SDK、Producer、Consumer 与 ZNIKU
实现的共同设计依据。

本文是设计基线，不是当前生产合同。它不修改、不替代，也不追溯解释当前的：

- ZBaton Protocol — Media Core 0.3.0；
- processing-provenance 0.2.0；
- AVEnhanceFlow v2.3.1 package 使用的 v2.3.0 workflow contract；
- 已发布的 ZBaton、Evidence、receipt 或 final 文件。

正式实施时，规范性协议、JSON Schema、validator 与 SDK 应由 `ZBatonProtocol-Media` 仓库统一
发布；本文件负责冻结 ZNIKU 对 vNext 的业务需求与跨仓库设计边界。

本文中的“必须”“不得”表示 vNext 实现必须满足的基线要求；“应”表示默认要求，只有记录明确的
设计理由时才可偏离。

## 2. 核心决策

ZBaton vNext 定位为一份随当前视频交付的**媒体处理历史记录**，优先回答四个问题：

1. 当前视频是什么；
2. 当前视频经过了哪些处理；
3. 处理链中产生过哪些 final；
4. 当前视频最初来自哪些源视频。

ZBaton vNext 不以证明验证关系为中心。它可以记录文件身份和技术信息，但文件哈希、处理声明或
`preserved` 声明本身不等于验证证据。验证结果、Evidence、receipt、日志及验证主体不进入
`processing_history`，未来如有需要，应作为独立结构或独立文档设计。

业务主结构固定为：

```text
ZBaton vNext
├── current_media
├── processing_history
├── final_history
└── source_media
```

根级 `zbaton` 对象是文档信封，保存文档类型、结构版本、文档身份和 Producer 信息；其余四项是
面向用户阅读和业务处理的主体。

## 3. 设计目标

- 一眼看清当前媒体、原始媒体、处理步骤和历代 final。
- 所有处理阶段共用一种记录结构，不为 Enhancement、Frame interpolation、Decensoring、音频
  处理或未来阶段分别建立顶层格式。
- 通过媒体 ID 连接处理记录，同时支持线性流程、分支与汇合 DAG。
- 一个已完成 Workflow 的 final 不被继续修改；新处理由下游 Workflow 承接。
- 下游 ZBaton 完整继承上游处理历史，使当前文件自带从源媒体开始的完整履历。
- 媒体快照在 `current_media`、`final_history` 与 `source_media` 中保持同一种结构。
- 使用精确、稳定、可比较的数据格式，同时保持 JSON 适合人工直接阅读。
- 新增普通处理类型时，只扩展 `stage`、`effects` 和指标路径，不改变通用记录骨架。
- 结构一致性校验不依赖打开、解码或重新哈希媒体文件。

## 4. 非目标

ZBaton vNext 首版不负责：

- 证明模型确实被外部软件加载或执行；
- 保存验证结果、验证等级、Evidence、receipt 或 claim；
- 保存失败尝试、重试、临时文件、事务 journal、lease 或运行日志；
- 调度工作流、表达待办状态、并发状态或下一步动作；
- 保存阶段的全部 UI 参数、模型权重、账号、授权或机器配置；
- 替代媒体文件、Workflow receipt、项目数据库或运行时状态；
- 将旧 ZBaton 文档原地改写成 vNext。

## 5. 整体数据模型

已采纳的根结构如下：

```json
{
  "zbaton": {},
  "current_media": {},
  "processing_history": [],
  "final_history": [],
  "source_media": []
}
```

| 字段 | 基数 | 作用 |
| --- | ---: | --- |
| `zbaton` | 1 | 文档信封、结构版本、文档身份和 Producer |
| `current_media` | 1 | 当前随 ZBaton 交付的唯一终端媒体 |
| `processing_history` | 0..n | 从源媒体到当前媒体的完整处理记录 |
| `final_history` | 0..n | 当前 final 之前产生并被继承的历史 final 快照 |
| `source_media` | 1..n | 整条处理历史的根源媒体快照 |

`processing_history` 构成处理图；另外三个媒体区域为图中的重要节点保存完整快照。普通中间媒体
可以只使用 `media_id` 出现在 `inputs`、`outputs` 中，不要求重复保存完整快照。

## 6. `zbaton` 文档信封

基线信封结构为：

```json
{
  "type": "media-history",
  "version": "1.0.0-draft.2",
  "id": "zbaton:sample-001:final-B",
  "status": "draft",
  "created_at": "2026-08-14T12:30:00+08:00",
  "producer": {
    "name": "ZNIKU",
    "version": "0.1.0"
  },
  "subject": {
    "title": "SAMPLE-001",
    "release_year": 2020
  }
}
```

### 6.1 字段语义

| 字段 | 规则 |
| --- | --- |
| `type` | 固定为 `media-history`，不得用自然语言变体 |
| `version` | ZBaton vNext 文档结构版本，不是 ZNIKU 或 Workflow 版本 |
| `id` | 当前这份历史文档的稳定唯一 ID；生成下游 final 时创建新 ID |
| `status` | 草案使用 `draft`；正式状态词表在协议实现前冻结 |
| `created_at` | 带时区偏移的 RFC 3339 时间 |
| `producer` | 实际生成本文件的软件名称与版本 |
| `subject` | 便于阅读的作品信息，不作为媒体或处理记录的机器身份 |

以下版本互相独立，不要求数值相同：

- `zbaton.version`：文档结构版本；
- `zbaton.producer.version`：生成文档的软件版本；
- `processing_history[].model.version`：实际处理模型版本；
- ZNIKU workflow contract 版本：执行流程合同版本，后续由实现层记录和约束。

`workflow` 是本文件内的 Workflow Run 简明标识，不是版本号。

## 7. 统一媒体快照

`current_media.snapshot`、`final_history[].snapshot` 与 `source_media[].snapshot` 必须采用相同结构。
基线快照字段为：

```text
snapshot
├── media_id
├── filename
├── size_bytes
├── sha256
├── container
├── timeline.duration
├── video
│   ├── codec / profile / pixel_format / bit_depth
│   ├── width / height / sample_aspect_ratio
│   ├── frame_rate / frame_count / scan
│   └── color
└── audio[]
    ├── track_id
    ├── codec / profile
    ├── sample_rate / channels / channel_layout
    └── language / default
```

### 7.1 媒体身份

- `media_id` 是处理图中的逻辑节点 ID，在一份文档内必须唯一指向同一媒体节点。
- `filename` 只保存交付时的文件名，不保存绝对路径。
- `size_bytes` 是非负十进制整数。
- `sha256` 是 64 位小写十六进制字符串。
- 相同 SHA-256 不要求使用相同 `media_id`；内容相同但业务世代不同的节点仍可拥有不同 ID。
- 哈希用于记录媒体身份。字段存在不表示本文件已经证明磁盘文件与该哈希相符。

### 7.2 时间轴与视频

- `timeline.duration` 表示实际节目播放时长，格式为 `HH:MM:SS.mmm`；小时可超过两位。
- `frame_rate` 必须使用约分后的精确有理数字符串，例如 `30000/1001`，不得使用
  `29.97` 代替。
- `width`、`height`、`bit_depth` 与 `frame_count` 使用整数。
- `sample_aspect_ratio` 使用 `N:D` 字符串。
- `color` 只记录可稳定解释的重要颜色字段；未知值不得根据分辨率或 codec 猜测。
- 本基线首先覆盖 ZNIKU 起始范围内的确定帧率媒体；VFR 的完整表达留待正式 Profile 设计。

### 7.3 音频

- `audio` 始终为数组；没有音频时使用空数组。
- 每条音轨使用稳定语义 `track_id`，例如 `audio.main`、`audio.commentary`。
- 同一快照内 `track_id` 必须唯一，不得仅用可能在重新封装后变化的容器 stream index 作为身份。
- `sample_rate` 与 `channels` 使用整数，`default` 使用布尔值。
- 语言代码的正式标准与未知值规则在 Profile 实施前冻结；不得从文件名猜测。

### 7.4 未知值

未知事实不得填入推测值。字段可否为 `null` 或省略由后续 JSON Schema 按字段明确规定；同一字段
不得在不同快照中混用互不兼容的类型。

### 7.5 快照外层结构

当前媒体与历史 final 共用同一种 final 包装结构：

```json
{
  "workflow": "B",
  "final_generation": 2,
  "snapshot": {}
}
```

`current_media` 直接使用该对象；`final_history` 是该对象的数组。`source_media` 不属于任何已完成
Workflow，首版只使用以下包装：

```json
{
  "snapshot": {}
}
```

不得把同一 current snapshot 同时复制到 `final_history`。三个区域的区别只在业务角色和外层信息，
不能各自发展不同的 snapshot 字段。

## 8. `processing_history` 通用记录

每个阶段使用同一种结构：

```json
{
  "record_id": "A.enhancement.001",
  "sequence": 1,
  "workflow": "A",
  "final_generation": 1,
  "stage": "Enhancement",
  "model": {
    "name": "Starlight Precise",
    "version": "2.6"
  },
  "inputs": ["source.original"],
  "outputs": ["A.enhanced"],
  "effects": ["quality-restoration", "super-resolution"],
  "changes": {
    "video.resolution": {
      "from": "1920x1080",
      "to": "3840x2160"
    }
  },
  "preserved": [
    "video.frame_rate",
    "video.frame_count",
    "timeline.duration",
    "audio"
  ]
}
```

### 8.1 字段语义

| 字段 | 规则 |
| --- | --- |
| `record_id` | 处理记录的稳定唯一 ID；用于继承、去重和未来外部引用 |
| `sequence` | 文档内唯一的正整数阅读顺序；必须符合拓扑顺序，但不是依赖权威 |
| `workflow` | 实际执行该阶段的 Workflow Run 简明标识 |
| `final_generation` | 该阶段归属的 final 世代 |
| `stage` | 稳定、易读的处理阶段名称 |
| `model` | 实际使用的模型；无模型时为 `null`，版本未知时 `version` 为 `null` |
| `inputs` | 当前处理消费的一个或多个媒体 ID |
| `outputs` | 当前处理产生的一个或多个媒体 ID |
| `effects` | “做了什么”的稳定语义 ID 列表 |
| `changes` | “重要指标怎样变化”的路径到 `{from, to}` 映射 |
| `preserved` | 明确声明在该处理阶段保持不变的指标或完整组成部分 |

### 8.2 记录规则

- `record_id` 是记录身份，不能依赖解析其字符串片段获得业务语义。
- `inputs` 与 `outputs` 定义图的边；`sequence` 只提供稳定、易读的拓扑排序。
- `effects` 说明处理语义，`changes` 说明可读的重要技术变化，两者不得相互替代。
- 没有发生可表达的技术指标变化时使用空对象 `changes: {}`。
- `changes` 与 `preserved` 未提及的指标属于“未声明”，不能解释为变化或保持不变。
- `changes` 中每个项目必须同时具有 `from` 和 `to`。
- 新增对象使用 `from: null`，删除对象使用 `to: null`。
- 多输入或多输出阶段无法用单一值表达时，`from` 或 `to` 可使用以 `media_id` 为键的对象；无法
  准确比较时应省略该变化，不得编造汇总值。
- 阶段参数只有在解释媒体历史确有必要时才进入通用指标；完整 UI 参数和运行配置不进入本结构。

### 8.3 `preserved` 语义

`preserved` 是 Producer 对处理语义的明确记录，不是独立验证结论。

- 叶子路径表示该指标未变，例如 `video.frame_rate`。
- 聚合路径表示整个逻辑组成部分未被该阶段改变，例如 `audio` 或 `video`。
- 聚合路径覆盖其全部子路径；同一记录不得一边声明 `audio` 保持不变，一边修改
  `audio.main.codec`。
- `audio` 保持不变表示音频节目、音轨集合及其时间关系未被该阶段处理；它不自动声明容器字节
  逐位相同。
- `timeline.duration` 仅在该阶段确实保持节目时长时声明。剪切、合并、变速、插入、删除及影响节目
  终点的音频操作都可能改变它。

### 8.4 通用指标路径

首版核心路径为：

```text
video
video.resolution
video.frame_rate
video.frame_count
video.codec
video.profile
video.pixel_format
video.bit_depth
video.sample_aspect_ratio
video.scan
video.color

audio
audio.track_count
audio.<track_id>
audio.<track_id>.codec
audio.<track_id>.sample_rate
audio.<track_id>.channels
audio.<track_id>.language
audio.<track_id>.integrated_loudness
audio.<track_id>.true_peak

timeline.duration
container
```

其中 `video.resolution` 的值统一写成 `WIDTHxHEIGHT`，帧率沿用精确有理数字符串。新路径应保持
既有命名层级和数据类型；实验性或厂商专属路径应使用命名空间，不能占用通用名称。

## 9. Workflow、final 与继承

### 9.1 final 关闭规则

每个 Workflow Run 必须只有一个 final。Workflow A 一旦生成并发布 `A.final`，A 即关闭，后续不得
在 A 内继续追加媒体处理。需要继续修改时，必须创建下游 Workflow B，以 `A.final` 为输入并生成
新的 `B.final` 与新的 B ZBaton。

“final 是唯一终端节点”有两个层次：

- 在单个 Workflow 内，该 Workflow 的 final 是唯一终端；
- 在当前 ZBaton 所表示的完整处理图内，`current_media` 是唯一终端。历史 `A.final` 被 B 消费后，
  它仍是一个 final 检查点，但不再是全局终端。

`final_generation` 必须为正整数。同一 Workflow 的处理记录及其 final 包装必须使用同一个
`final_generation`。线性下游 Workflow 使用上游最大 generation 加一；多父汇合也使用所有父 final
的最大 generation 加一，因此并行分支可以拥有相同 generation。generation 只用于表达世代和排序，
final 的机器身份仍由 `media_id` 确定。

### 9.2 下游继承算法

Workflow B 承接 A 时必须：

1. 继承 A 的 `source_media`；
2. 继承 A 的全部 `processing_history`，保留原 `record_id`；
3. 继承 A 已有的 `final_history`；
4. 将 A 的 `current_media` 作为一个历史 final 追加到 B 的 `final_history`；
5. 追加 B 本地执行的处理记录；
6. 将 B 的唯一 final 写入新的 `current_media`；
7. 创建新的 `zbaton.id`、`created_at` 和 Producer 信息。

继承关系不在每条处理记录中额外写 `inherited` 或 `local`。记录重点始终是“做过什么处理”；
`workflow`、`final_generation`、媒体引用和 final 快照已经足以还原处理归属。

线性单父继承时，已有记录应保持字段不变，新记录从最大 `sequence + 1` 开始。多父 DAG 汇合时，
应按 `record_id` 去重并保持各父链内部相对顺序；如父文档的 `sequence` 冲突，可以重排文档内
`sequence`，但不得改变 `record_id` 和处理语义。正式 canonical 排序规则由协议规范冻结。

### 9.3 `final_history`

- 只保存当前 final 之前的 final 快照，不重复保存 `current_media`。
- 每项使用 `workflow`、`final_generation` 与统一 `snapshot`。
- 历史 final 的快照描述它形成时的文件；下游复制或改名不能回写历史快照。
- `final_history` 是有意义的交付检查点历史，不保存每个中间文件。
- 处理链从未产生过早期 final 时使用空数组。

### 9.4 `source_media`

- 保存完整处理图的一个或多个根媒体快照。
- source 的 `media_id` 不得同时作为任一处理记录的输出。
- 多源合并时，具体消费关系由处理记录的 `inputs` 表达，不依赖数组位置。
- 下游继承时按 `media_id` 去重，不得因复制文件或改变路径而伪造新的源历史。

## 10. DAG 语义与约束

ZBaton vNext 的处理历史是一个有向无环图，但“有向且无环”只是最低条件。为了让历史可读、可继承
且可确定解释，还必须满足以下约束：

1. `record_id` 在文档内唯一。
2. 每个 output `media_id` 最多由一条处理记录产生。
3. 每个 input 必须解析到 `source_media` 或某条处理记录的 output。
4. source 不能由处理记录产生。
5. 图中不得存在环、自引用或悬空 input。
6. `sequence` 必须是与依赖关系一致的拓扑顺序。
7. 所有被记录的处理节点都必须位于通向 `current_media` 的祖先路径上；失败分支和无关旁支不进入
   当前历史。
8. `current_media.snapshot.media_id` 必须是全图唯一终端节点。
9. 每个 Workflow Run 只能有一个与其 final 对应的媒体节点。
10. `final_history` 中的媒体必须是处理图中已产生、且位于当前媒体祖先路径上的 final。

典型线性继承表现为：

```mermaid
flowchart LR
    S["source.original"] --> E["Enhancement"]
    E --> AE["A.enhanced"]
    AE --> F["Frame interpolation"]
    F --> AF["A.final / Final #1"]
    AF --> D["Decensoring"]
    D --> BF["B.final / Final #2 / current"]
```

`inputs` 和 `outputs` 使用数组，使未来的多源合并、分支处理和再汇合无需改变记录结构。辅助日志、
预览、缓存和不参与当前成片的副产物不属于该业务 DAG。

## 11. 扩展规则

### 11.1 新处理阶段

新增 Decensoring、HDR、调色、降噪、音频修复、声道重构、字幕烧录、剪辑、转码或重新封装时，
继续使用相同的处理记录字段，只增加相应的：

- `stage`；
- `effects`；
- 必要的通用指标路径。

不得仅因新增普通阶段就增加新的根级数组或设计专属记录骨架。

### 11.2 语义 ID

- `effects` 使用稳定的 kebab-case 机器语义，例如 `super-resolution`、
  `frame-interpolation`、`mosaic-removal`。
- 通用语义应由未来 Profile 维护受控词表。
- 实验性或私有语义必须带命名空间，避免与未来通用词冲突。
- 显示名称可以在 UI 本地化，JSON 中的机器语义不得随文案改变。

### 11.3 向前兼容

- Consumer 遇到未知 `stage` 或 `effects` 时，只要通用结构合法，就应保留并展示，不得丢弃整份
  历史。
- 新增可选媒体指标不应破坏旧 Consumer；改变既有字段含义或类型必须升级结构版本。
- 继承 Producer 必须保留它不理解的上游合法记录和扩展字段，不能静默重写为自身理解的子集。

## 12. 文档一致性校验边界

vNext validator 必须验证文档本身是否自洽，包括：

- 根结构、字段类型、必填项和版本路由；
- `record_id`、`media_id` 与音轨 `track_id` 唯一性；
- input/output 引用完整性、DAG 无环和拓扑顺序；
- 当前媒体唯一终端、Workflow 唯一 final 与 final 历史关系；
- 三类媒体快照采用同一 Schema；
- `changes` 的 `{from, to}` 结构和指标值类型；
- `changes` 与 `preserved` 不得在同一路径或祖先/子孙路径上冲突；
- 已有完整快照的输入输出与对应 `changes` 不得自相矛盾；
- 时间、帧率、哈希和数值字段使用规定格式。

上述检查是**结构与历史一致性校验**，不是媒体验证。默认 validator 不得为了验证 JSON 而打开、
探测、解码或重新哈希媒体，也不得声称：

- 文件当前仍与快照相同；
- 某个模型确实运行；
- 处理效果真实有效；
- `preserved` 已通过独立媒体比较证明。

未来验证方案应通过 `zbaton.id`、`record_id`、`media_id` 或 final 身份关联，不改变
`processing_history` 的职责。

## 13. 不可变性、修订与隐私

- 正式 final 与其 ZBaton 发布后应视为不可变记录。
- 下游处理创建新的 final 和新的 ZBaton，不修改上游文件。
- 历史快照与处理记录在继承时保持原始语义；不得根据新工具的推断补写未知旧事实。
- 文档只保存交付文件名，不保存本机绝对路径、共享目录、账号、令牌、模型文件位置或运行日志。
- 文档 ID 的正式生成、已发布文档纠错和 `supersedes` 机制在协议实施前另行冻结；在该机制确定前，
  不允许通过原地覆盖冒充历史修订。

## 14. 版本与迁移边界

### 14.1 vNext 版本

样例当前使用 `1.0.0-draft.2`，仅表示设计期结构。正式版本号由
`ZBatonProtocol-Media` 在规范、Schema、validator、SDK 和 fixtures 同步完成后确定。

版本升级原则：

- 新增向后兼容的可选字段或受控词表条目，可使用兼容升级；
- 改变字段含义、必填关系、数据类型、图语义或继承规则，必须使用不兼容升级；
- Producer 必须显式写入目标结构版本，不依赖 SDK 的隐式默认值；
- Consumer 必须按精确版本路由，不得用“尽量解析”替代版本合同。

### 14.2 与当前 AVEnhanceFlow 生产合同的关系

- AVEnhanceFlow v2.3.1 package 继续按 v2.3.0 workflow contract、Core 0.3.0 及其现有 Profile 生产，
  不受本文影响。
- 已发布旧 ZBaton、Evidence、receipt 和 final 保持原样，不迁移、不补写、不重排。
- ZNIKU 不得直接用 vNext loader 续跑 AVEnhanceFlow v2.3.0 task root。
- 如未来提供旧文档导入，只能作为显式迁移功能；未知事实必须保持未知，不能从文件名、日志或品牌
  文本推测。
- 是否并存旧 Manifest 与 vNext history、文件命名及一次性迁移策略，留待 ZNIKU 专题设计确定。

## 15. 跨仓库职责

### 15.1 ZBatonProtocol-Media

正式实现时负责：

- 规范性 vNext Core/Profile 文档；
- JSON Schema 与版本路由；
- 不可变数据模型、解析、序列化和一致性 validator；
- canonical fixtures 与兼容性测试；
- stage/effect/指标路径的公共注册规则。

Schema、validator 与 fixtures 必须是规范的同步投影，不能各自发展出不同语义。

### 15.2 ZNIKU

负责：

- 从正式 Workflow authority 生成真实处理记录；
- 从正式媒体 authority 生成 source、final 与 current 快照；
- 在人工 Enhancement、Frame interpolation 及未来 Decensoring 阶段绑定实际模型信息；
- 执行 Workflow 关闭、下游继承和唯一 final 规则；
- 使用精确固定版本的 ZBaton SDK 生成并发布文档；
- 保持媒体发布事务和 ZBaton 生成之间清晰的失败边界。

### 15.3 下游 Producer / Consumer

- Consumer 负责读取和展示历史，不得把记录声明自动提升为独立验证事实。
- 下游 Producer 生成新 final 时负责继承完整上游历史、去重记录并追加本地处理。
- 不理解的合法上游扩展必须保留，不能静默删除。

## 16. ZNIKU 实施准入条件

进入正式 ZNIKU Producer 实现前，至少应完成：

1. 在 ZBatonProtocol-Media 中冻结 vNext 规范版本和 Profile ID；
2. 冻结根结构、统一 snapshot、记录字段及 null/optional 规则；
3. 冻结 `stage`、`effects`、指标路径和音轨稳定 ID 的注册规则；
4. 冻结多父 DAG 的 canonical 排序与继承去重规则；
5. 实现 Schema、数据模型、确定性序列化和无媒体 I/O 的一致性 validator；
6. 用 canonical fixtures 覆盖线性处理、下游继承、分支汇合、多源、音频变化和未知扩展；
7. 明确 AVEnhanceFlow v2.3.0 task root 与 ZNIKU project root 的严格版本隔离；
8. 明确 final 与相邻 ZBaton 的事务发布顺序、失败语义和 no-replace 行为；
9. 以真实短任务验证一条完整 ZNIKU 历史，再决定正式发布。

实现验收至少必须证明：

- Enhancement、Frame interpolation、Decensoring 和模拟音频阶段无需改变记录骨架；
- B ZBaton 能完整继承 A 的处理历史与 final 快照；
- 文档可表达多输入、多分支与无环汇合；
- `current_media` 始终是唯一终端，历史 final 不与当前 final 重复；
- validator 能拒绝悬空引用、环、重复 ID、多个当前终端及变化/保持冲突；
- validator 不读取媒体 payload；
- AVEnhanceFlow v2.3.0 历史合同和已发布文件保持不变。

## 17. 已冻结事项与后续待定事项

### 17.1 本基线已冻结

- ZBaton vNext 是媒体处理历史记录，不是验证报告。
- 业务主体采用 `current_media`、`processing_history`、`final_history`、`source_media`。
- 三类完整媒体信息共用统一 `snapshot`。
- 所有处理阶段共用含 `record_id` 的通用记录结构。
- 处理依赖由媒体 ID 构成 DAG，`sequence` 只负责阅读顺序。
- 每个 Workflow 只有一个 final；已关闭 Workflow 通过新下游 Workflow 扩展。
- 下游文档继承完整处理历史，不在记录中增加 `inherited/local` 状态。
- `changes` 只记录重要变化，`preserved` 只记录明确保持项，缺失表示未声明。
- 验证信息与处理历史解耦。
- 当前 v2.3.0 和既有 ZBaton 不被追溯修改。

### 17.2 正式实现前待定

- 正式 Core/Profile ID 与稳定版本号；
- `status`、`stage`、`effects` 和指标路径的完整受控词表；
- 文档 ID、Workflow Run ID、media ID 与 final generation 的正式格式；
- 音轨稳定 ID、语言代码、未知字段和 VFR 的最终规则；
- 多父 DAG canonical 排序、记录冲突和来源去重算法；
- 已发布文档的纠错、替代与 `supersedes` 机制；
- 独立验证文档的结构及关联方式；
- AVEnhanceFlow 与 ZNIKU 的并存、导入和文件命名策略。

待定事项不得由单个 Producer 临时私有化后当作公共协议；必须先回到规范层冻结，再同步 Schema、
validator、fixtures 和各 Producer。

## 18. 参考样例与从属文档

- 完整采纳样例：[`sample.json`](./sample.json)
- `processing_history` 详细草案：
  [`processing-history.md`](./processing-history.md)

样例用于展示本基线，不单独构成规范。后续如样例或处理历史草案与本基线冲突，以本基线为准；
正式协议发布后，再以对应版本的规范性 ZBaton Protocol 文档为最高权威。
