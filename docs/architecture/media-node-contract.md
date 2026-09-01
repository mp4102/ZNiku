# ZNIKU 0.2.0 首批媒体节点合同

- 状态：Phase 4 已实现；Phase 5 清理后保留的媒体节点合同
- 版本：`0.2.0`
- 上位权威：[`graph-core-baseline.md`](graph-core-baseline.md)

本文说明 `zniku.media` 首批内建节点的职责、typed ports、最小媒体校验和外部副作用边界。本文从属于
`graph-core-baseline.md`；发生冲突时以上位基线为准，不在这里增加新的 Core 不变量、固定拓扑或第二份
Studio authority。

## 1. 目录与绑定

`built_in_media_definitions()` 返回 exact `type_id/version` 的 `NodeDefinition` 目录，Project Service 在新建
`.zniku` 时保存该目录。Studio 只投影当前 Project 返回的 definitions；节点实例仍绑定 exact version，参数
仍由各 definition 的 JSON Schema 校验。打开既有工程时以工程中保存的 definitions 为准，不从浏览器猜测或
替换 executor、validator 和参数默认值。

所有节点都是普通 DAG 节点。目录顺序、示例工程和 Studio Palette 分组都不规定节点在 Graph 中的位置。

## 2. 七类首批节点

| 节点 | typed ports | 职责与关键参数 |
| --- | --- | --- |
| `SourceMedia` | 无输入；`MediaFile`、`VideoFile` 或 `AudioFile` 单输出 | `source_path` 必须是操作者选择的绝对非空文件路径。节点登记只读外部引用，不复制、移动、改写或摘要源文件。 |
| `VideoTransform` | `VideoFile` → `VideoFile` | automatic definition 提供闭合的 `identity`、`scale`、`frame_rate` operation；MR、Enhancement、FI 是相同端口语义的 `manual_external` presets。 |
| `SplitVideo` | `VideoFile` → 命名的多个 `VideoFile` 输出 | 内建 definition 使用设计时已知的 `A`、`B` 输出；`segments` 明确绑定每个输出的 half-open frame range。 |
| `MergeVideo` | `ordered_many<VideoFile>` → `VideoFile` | 严格按入边 ordinal 消费输入，并校验输出帧数等于各输入帧数总和。 |
| `EncodeVideo` | `VideoFile` → `VideoFile` | 显式配置 `codec`、`preset`、`crf` 和 `pixel_format`；可选择要求输出帧数等于输入。 |
| `MuxMedia` | 一个 required `VideoFile` 加可选 `ordered_many<AudioFile>` → `MediaFile` | 按 audio ordinal 做 stream copy，并产生单一 Matroska 媒体。 |
| `OutputFile` | `MediaFile`、`VideoFile` 或 `AudioFile` 单输入；同 kind 的 `published` 单输出 | terminal-style Output 节点，以 `copy` 或 `reference` 发布；不移动上游 Artifact。成功后登记发布路径的 external Artifact。 |

Source 与 Output 的 typed variants 使用不同 exact `type_id`，避免 `MediaFile`、`VideoFile`、`AudioFile` 之间
发生隐式转换。Graph Core 仍只接受大小写敏感的精确 data type 相等。

## 3. Split frame range 与 Merge ordinal

Split 的每个 segment 使用 `[start_frame, end_frame)`：

- `port_id` 顺序必须与 definition 的命名输出端口一致；
- 第一个区间从 0 开始，后一区间从前一区间的 `end_frame` 开始；
- 区间必须连续、无重叠、无缺口，并完整覆盖输入精确帧数；
- 每个实际输出帧数必须等于区间长度，全部输出帧数之和必须等于输入帧数；
- 成功 Artifact 携带对应 `frame_range`；任一输出失败时整个 attempt 失败，部分文件不登记为 Artifact。

Merge 不恢复 ArtifactSet authority。多个输入只是同一 `ordered_many` port 上的普通 Edge；Runtime 按连续且唯一
的 ordinal 形成输入顺序。Merge 分别精确计数输入和输出，并要求输出总帧数守恒。

## 4. FFmpeg、FFprobe 与最小校验

`ffmpeg` 和 `ffprobe` 必须可从进程 `PATH` 解析；缺失、启动失败、非零退出、超时、非法 JSON 或流类型不符
都失败关闭。外部进程使用 executable 与 argv 数组、`shell=False`、禁用交互 stdin，并把输出写入当前
attempt 的普通日志。

automatic Transform、Split 和 Merge 使用 FFV1 Matroska 作为节点工作输出；Encode 使用 definition 允许的
codec 与像素格式；Mux 对声明的视频和有序音轨执行 stream copy。除 Source 的只读引用与 OutputFile 的
显式发布外，产生 Artifact 的节点默认只把文件写入独立 attempt 工作目录。

所有媒体输出在登记 Artifact 前至少满足：

1. 受控进程成功退出（如适用）；
2. 声明文件存在、是常规文件且非空；
3. FFprobe 能识别声明的媒体流；
4. 节点自己的轻量 validator 通过。

精确 frame count 只在 Split、Merge 或节点参数明确要求时执行。默认不计算 SHA-256，不做 full decode、packet
scan、roundtrip、全局 profile 或 Evidence chain。

## 5. OutputFile 的 copy 与 reference

`reference` 保留上游 Artifact 路径，不复制文件，也不允许 `overwrite=true`。它适合把现有 Artifact 作为
该 Output sink 的发布结果记录下来。

`copy` 要求绝对 `target_path` 且目标父目录已经存在：

- `overwrite=false` 使用排他创建，目标已存在即失败；
- `overwrite=true` 先在目标目录创建唯一临时候选，完成复制与 probe 后再原子替换；
- 目标不得等于上游路径，任何模式都不会移动或删除上游 Artifact；
- 外部目标不属于 attempt 清理权限。复制失败时可能存在 partial 或临时候选，Runtime 不越权删除，错误会把
  具体路径交给操作者处理。

OutputFile 的 `published` port 登记一个同 kind external Artifact：`copy` 指向目标路径，`reference` 指向原
上游路径。这样 Studio 可以直接显示发布路径，Runtime 也能用普通 quick probe 判断结果是否仍可复用；
`published_path`、`mode` 与 `overwrite` 同时进入普通 NodeResult summary。它通常作为无下游连接的 terminal-
style Output 使用，但 Core 不强制唯一 Final，也不形成 receipt 或发布证明。

## 6. MR、Enhancement 与 FI external presets

三个 preset 都是普通 `VideoTransform`：`VideoFile → VideoFile`。它们只预置 `manual_external` handoff 和参数
Schema，不改变 Scheduler：

- `tool`、`model`、`tool_version` 是必填的操作者声明；
- resolution、frame rate 与 frame relation 是可选轻量约束；
- FI 默认声明 double frame relation，MR 与 Enhancement 默认不强制输入输出帧关系；
- handoff 明确展示输入 Artifact 与当前 attempt 目标；操作者完成外部处理后显式 Submit；
- Submit 仍执行存在、非空、FFprobe 与节点 validator 校验。

这些字段只记录本次节点配置。ZNIKU 不通过输出属性证明外部工具、模型或版本确实被使用，也不接管外部工具
进度、checkpoint 或 resume。

## 7. 安全与副作用卫生

- 媒体 adapters 是本地受信 Python adapters，不接受任意 shell 字符串；FFmpeg 始终使用结构化 argv。
- Source 只读引用操作者给出的文件；attempt 清理不得触及 Source、上游 Artifact、Project 或外部发布路径。
- FFmpeg 的覆盖参数只作用于 Runtime 分配的 attempt 输出；用户路径覆盖只由 OutputFile 的显式参数授权。
- 节点失败不登记 partial Artifact；从节点重跑会创建新 attempt，从输入第一帧重新开始。
- 产品边界仍是单用户本地工作站和操作者信任的 LAN，不提供插件签名、沙箱、RBAC、TLS 或多租户隔离。

## 8. 当前明确不处理

- 固定 AVEnhanceFlow、唯一 Final 或强制 Source→Output 拓扑；
- 动态场景发现、运行时动态 Split 端口、Map/Collect 或 ArtifactSet authority；
- 节点内部断点续跑、分段 checkpoint、编码 offset 或外部进度接管；
- 全局 checksum、full verification、Evidence、receipt 或归档 Manifest；
- 自动证明 AI 模型和主观画质；
- 公网、分布式 worker、GPU lease、SMB 故障转移或长片压力矩阵；
- 最终操作者长片验收；自动化只执行短合成媒体，不把长片压力测试扩大为 Core 门禁。

仓库提供的短媒体 smoke project 只是一张可编辑的示例 DAG，用于验证七类节点的最小真实执行合同；它不是
产品内置 workflow，也不扩大 Core 完成条件。
