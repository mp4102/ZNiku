# ZBaton vNext `processing_history` 设计草案

状态：已采纳的从属设计说明，供后续 ZBaton 与 ZMediaFlow 设计使用。整体结构、继承规则与
实现边界以 [`design-baseline.md`](./design-baseline.md) 为准。

本文只定义 ZBaton.json 中 `processing_history` 的业务记录方案，不修改或替代当前
ZBaton Media Core 0.3.0、processing-provenance 0.2.0 或 AVEnhanceFlow v2.3.0 workflow contract。

## 1. 定位

ZBaton vNext 面向“当前视频的处理护照”，优先回答：

1. 当前视频是什么；
2. 当前视频经过了哪些处理；
3. 处理链中产生过哪些 final；
4. 当前视频最初来自哪些源视频。

建议的主要业务结构为：

```text
current_media
processing_history
final_history
source_media
```

本文只讨论其中的 `processing_history`。

## 2. 目标

- 所有处理阶段使用同一种记录结构，不为 Enhancement、Frame interpolation、Decensoring
  或音频处理分别设计不同的顶层对象。
- 优先记录“进行了什么处理”和“重要媒体指标怎样变化”。
- 保持 JSON 简洁，直接阅读时能够快速看到阶段、模型、输入、输出和变化。
- 支持未来增加画质修复、超分辨率、补帧、去马赛克、HDR、调色、音频修复、转码和封装等阶段。
- 使用输入、输出媒体 ID 连接处理记录，使同一结构可以表达线性管线和 DAG。

## 3. 非目标

`processing_history` 暂不记录：

- 验证结果、验证级别或验证主体；
- Evidence、receipt、日志或运行时诊断；
- 重试、失败尝试、lease、事务 journal 或临时文件；
- 工作流调度状态、并发状态或下一步动作；
- 当前 ZBaton 版本的兼容映射。

验证信息后续应设计为独立结构，并通过 `record_id`、媒体 ID 或 final ID 与处理历史关联。

## 4. 通用记录结构

每个 `sequence` 使用相同字段：

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
  "inputs": [
    "source.original"
  ],
  "outputs": [
    "A.enhanced"
  ],
  "effects": [
    "quality-restoration",
    "super-resolution"
  ],
  "changes": {
    "video.resolution": {
      "from": "1920x1080",
      "to": "3840x2160"
    }
  },
  "preserved": [
    "video.frame_rate",
    "video.frame_count",
    "audio"
  ]
}
```

### 4.1 字段语义

| 字段 | 含义 |
| --- | --- |
| `record_id` | 处理记录的稳定唯一 ID，用于继承、去重和未来外部引用 |
| `sequence` | 面向阅读的拓扑顺序，不作为 DAG 依赖权威 |
| `workflow` | 实际执行该阶段的 Workflow Run 简明标识 |
| `final_generation` | 该阶段归属的 final 世代；同一 Workflow Run 的处理通常指向同一世代 |
| `stage` | 处理阶段的稳定语义名称 |
| `model` | 实际使用的模型 name/version；版本未知时为 `null`，不得猜测 |
| `inputs` | 当前处理消费的媒体 ID 列表 |
| `outputs` | 当前处理产生的媒体 ID 列表 |
| `effects` | 对内容执行的处理语义 |
| `changes` | 已确认发生变化的重要指标，统一保存 `from` 与 `to` |
| `preserved` | 已确认保持不变的重要指标或完整组成部分 |

### 4.2 基本规则

- `effects` 说明“做了什么”，`changes` 说明“技术指标怎样变化”。
- `record_id` 在文档内必须唯一；下游继承时保留，不能依赖解析 ID 文本获取业务语义。
- `changes` 与 `preserved` 以外的指标不作声明；缺失不等于保持不变。
- `model` 记录实际模型，不记录初始化计划值，也不从文件名或日志猜测。
- 没有模型的处理阶段使用 `model: null`。
- `inputs` 和 `outputs` 才是处理依赖关系；`sequence` 只提供稳定、易读的拓扑排序。
- 新阶段应复用该结构，通过新的 `stage`、`effects` 和指标路径表达差异。

## 5. 通用指标路径

首版建议支持以下核心路径：

### 5.1 视频

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
```

### 5.2 音频

```text
audio
audio.track_count
audio.main
audio.main.codec
audio.main.sample_rate
audio.main.channels
audio.main.language
audio.main.integrated_loudness
audio.main.true_peak
```

多音轨场景应使用稳定语义 ID，例如 `audio.main`、`audio.commentary`，不能只依赖可能因重新封装而
变化的容器轨道 index。音轨 ID 的正式生成规则留待后续版本确定。

### 5.3 时间轴与容器

```text
timeline.duration
container
```

`timeline.duration` 表示电影实际播放时长，不是容器头中的近似 duration。剪切、合并、加速、慢放、
插入或删除画面、错误解释帧率以及影响节目终点的音频补齐或截断都可能改变它。

该指标不要求出现在每个阶段。阶段可能影响时间轴，或需要明确声明时长保持时，才把它写入
`changes` 或 `preserved`。

## 6. 完整处理历史示例

以下示例表示：Workflow A 完成 Enhancement 与 Frame interpolation 并形成第一代 final；Workflow B
以 A final 为输入执行 Decensoring，形成第二代 current final。

```json
{
  "processing_history": [
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
      "inputs": [
        "source.original"
      ],
      "outputs": [
        "A.enhanced"
      ],
      "effects": [
        "quality-restoration",
        "super-resolution"
      ],
      "changes": {
        "video.resolution": {
          "from": "1920x1080",
          "to": "3840x2160"
        }
      },
      "preserved": [
        "video.frame_rate",
        "video.frame_count",
        "audio"
      ]
    },
    {
      "record_id": "A.frame-interpolation.001",
      "sequence": 2,
      "workflow": "A",
      "final_generation": 1,
      "stage": "Frame interpolation",
      "model": {
        "name": "Aion",
        "version": null
      },
      "inputs": [
        "A.enhanced"
      ],
      "outputs": [
        "A.final"
      ],
      "effects": [
        "frame-interpolation"
      ],
      "changes": {
        "video.frame_rate": {
          "from": "30000/1001",
          "to": "60000/1001"
        },
        "video.frame_count": {
          "from": 123456,
          "to": 246912
        }
      },
      "preserved": [
        "video.resolution",
        "timeline.duration",
        "audio"
      ]
    },
    {
      "record_id": "B.decensoring.001",
      "sequence": 3,
      "workflow": "B",
      "final_generation": 2,
      "stage": "Decensoring",
      "model": {
        "name": "Jasna",
        "version": "0.10.0"
      },
      "inputs": [
        "A.final"
      ],
      "outputs": [
        "B.final"
      ],
      "effects": [
        "mosaic-removal"
      ],
      "changes": {},
      "preserved": [
        "video.resolution",
        "video.frame_rate",
        "video.frame_count",
        "timeline.duration",
        "audio"
      ]
    }
  ]
}
```

直接阅读时应能形成以下摘要：

```text
Workflow A -> Final #1
1. Enhancement - Starlight Precise 2.6
   1920x1080 -> 3840x2160
2. Frame interpolation - Aion
   30000/1001 -> 60000/1001

Workflow B -> Final #2 (current)
3. Decensoring - Jasna 0.10.0
   Mosaic removal
```

## 7. 模拟音频处理

以下记录仅用于展示结构，模型名称和数值均为模拟数据。

```json
{
  "record_id": "C.audio-restoration.001",
  "sequence": 4,
  "workflow": "C",
  "final_generation": 3,
  "stage": "Audio restoration",
  "model": {
    "name": "Demo Audio Restore",
    "version": "1.0.0"
  },
  "inputs": [
    "B.final"
  ],
  "outputs": [
    "C.final"
  ],
  "effects": [
    "noise-reduction",
    "dialogue-enhancement",
    "loudness-normalization"
  ],
  "changes": {
    "audio.main.codec": {
      "from": "aac",
      "to": "flac"
    },
    "audio.main.integrated_loudness": {
      "from": "-23 LUFS",
      "to": "-16 LUFS"
    },
    "audio.main.true_peak": {
      "from": "-3.2 dBTP",
      "to": "-1.0 dBTP"
    }
  },
  "preserved": [
    "video",
    "audio.track_count",
    "audio.main.sample_rate",
    "audio.main.channels",
    "audio.main.language",
    "timeline.duration"
  ]
}
```

新增音轨仍使用相同的 `changes` 结构，以 `null` 表示此前不存在：

```json
{
  "changes": {
    "audio.track_count": {
      "from": 1,
      "to": 2
    },
    "audio.commentary": {
      "from": null,
      "to": {
        "codec": "aac",
        "sample_rate": 48000,
        "channels": 2,
        "language": "jpn"
      }
    }
  }
}
```

删除音轨时方向相反：`from` 保存原音轨摘要，`to` 为 `null`。

## 8. 扩展规则

- 新增处理阶段不改变通用记录字段，只增加新的 `stage` 与 `effects` 语义。
- 新增普通指标路径不应破坏旧记录；是否需要 profile 版本升级由后续规范确定。
- 自定义 effect 应使用稳定、可注册或带命名空间的语义 ID，不能把自然语言描述当作机器身份。
- 阶段特有的大量参数不直接塞入 `processing_history`；这里只保留能解释最终媒体变化的重要参数。
- 文件名、文件大小和 SHA-256 属于 `current_media`、`final_history` 或 `source_media` 的媒体快照，
  不重复放入 `changes`。

## 9. 待确定事项

- `processing_history` 的正式 Profile ID 和版本策略；
- `stage` 是否拆分机器 ID 与显示名称；
- `effects` 的受控词表、注册方式及扩展命名空间；
- 媒体 ID、Workflow Run ID 和 final generation 的正式格式；
- 音轨稳定语义 ID 的生成与跨容器继承规则；
- duration、颜色信息和多音轨对象的最终数据类型；
- 分支与汇合 DAG 的 canonical `sequence` 排序规则；
- `current_media`、`final_history`、`source_media` 与媒体 ID 的引用方式；
- 独立验证结构如何通过 `record_id` 与处理记录关联。
