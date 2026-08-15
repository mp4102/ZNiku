# ZNIKU 0.1.0 Phase 6 扩展、历史投影与发布验证基线

- 状态：**Phase 6 开发门已实现**
- 日期：2026-08-15
- 上位基线：[`product-framework.md`](./product-framework.md)
- 版本：ZNIKU `0.1.0`

## 1. 定位

本文冻结产品 Phase 6 的开发期实施边界与验收门：以 Decensoring 和 `new01` 证明 Engine 扩展不要求
修改 Runtime 核心分支，以 ZBaton vNext draft 投影验证完整处理历史，并以合成媒体、短真实媒体、
故障注入、目标存储和长片规模场景完成 `0.1.0` 的发布前工程验证。

本阶段不改变产品版本，不修改 AVEnhanceFlow，不复制 AVSplitTool，不把测试用 FFmpeg harness 注册为
生产 Engine，也不执行 tag、release 或生产部署。用户对 commit/push 的授权不等于对生产发布的授权。

## 2. Engine 扩展与 Registry

Phase 6 增加两个受信合成 package：

| Engine | Scope | 作用 |
| --- | --- | --- |
| `zniku.extension.synthetic-decensoring` | `chapter` | 只处理显式选择的章节，保持时间轴与媒体类型 |
| `zniku.extension.synthetic-new01` | `program` | 在 Collect/Reduce 后处理全片视频 |

它们继续使用 Phase 1 的 `EnginePackage`、`EngineManifest`、精确 `EngineBinding`、Installed Catalog 与
conformance gate。Runtime 只看到通用 Engine planned node，不得包含上述 Engine ID 的条件分支。

应用组装根显式注入 package 后，Installed Catalog 成为 Registry authority；Studio 只读投影 enabled
records。新增 package 自动进入 Registry 与 Studio 列表，不要求修改 Runtime 或前端 Engine 词表。0.1.0
仍不扫描任意目录、不下载 package、不接收 executable/entrypoint/argv/shell，也不实现生产安装器。

## 3. 扩展工作流验收

扩展验收 Workflow 必须完整表达：

```text
Source → Demux → Partition → Select(Chapter A)
                         ├→ Map(Decensoring) ┐
                         └→ remainder ───────┤
                                             Collect → Reduce → new01 → Mux → Final
Demux.audio_out ──────────────────────────────────────────────────→ Mux.audio_in
```

Compiler 必须确认 typed ports、scope、selector、Collect 完整性和唯一 Final；ExecutionPlan 只为选中章节
展开 Decensoring 实例。合成 Runtime 应在不理解 Engine 名称的情况下完成同一 Plan。缺失、重复、乱序或
媒体不兼容集合继续由既有合同失败关闭。

## 4. ZBaton vNext 开发期投影

### 4.1 Authority 边界

[`zbaton/design-baseline.md`](./zbaton/design-baseline.md) 已规定：正式规范、Profile、Schema、SDK 和版本
路由由 `ZBatonProtocol-Media` 发布。当前尚无已冻结的正式 vNext SDK，因此本阶段只实现
`1.0.0-draft.2` 的 ZNIKU 开发期投影和一致性门：

- 用于验证 ZNIKU 能从已完成、已验证、已发布的 Stage history authority 生成历史；
- 不作为生产协议、不替代上游 SDK、不迁移旧 ZBaton；
- 正式发布前必须用精确固定的上游 SDK 替换 draft serializer，并重新通过 canonical fixtures；
- AVEnhanceFlow v2.3.0/Core 0.3.0 文档保持原样。

### 4.2 投影内容

开发期投影采用已冻结的四区结构：`current_media`、`processing_history`、`final_history`、
`source_media`。Stage history authority 额外绑定 Evidence、verification 与 publication 状态；这些字段只
用于投影准入，不进入 `processing_history`。

局部章节处理暂不私自增加公共 `scope` 字段，而由稳定 chapter media ID、Decensoring 记录的 inputs 和
outputs，以及 Collect 多输入关系表达。叶片级 Evidence 不进入交付历史。

一致性门至少拒绝：重复 record/media/sequence、悬空 input、同一 output 多 producer、环或非拓扑顺序、
无关旁支、多个终端、source 被生产、current/final 重复、每 Workflow 多 final，以及 `changes` 与
`preserved` 的同路径或祖先/子孙冲突。Validator 不打开、解码或重新哈希媒体。

### 4.3 下游继承

下游 Workflow 投影必须保持父文档 source、record 和 final snapshot 语义不变，将父 current 追加为历史
final，再追加本地记录并产生新的 current。父文档不被原地修改；未知正式扩展的保留策略仍等待上游
协议冻结。

## 5. 短真实媒体验证

真实媒体门是开发期验证 harness，不是生产 Engine：

1. 仅使用明确 argv、`shell=False` 调用本机 FFmpeg/FFprobe；
2. 在测试临时目录生成短小的合成视频与音频，不提交媒体 payload；
3. 用 FFprobe JSON 和文件 SHA-256 形成只读 probe 结果；
4. 验证精确帧率、视频/音频 stream、非零大小与 digest；
5. 工具缺失时正式 Phase 6 gate 失败，不以 skip 冒充通过。

该门只证明媒体工具链和 publication plumbing 可工作，不证明 Decensoring、Enhancement 或其他真实模型
效果，也不把文件名或 FFmpeg 成功消息提升为 Runtime Evidence。

## 6. 目标存储与故障注入

目标存储 publication 使用 same-directory staging、完整复制、size/digest 复核和 no-replace 原子链接：

- 目标存在时在任何写入前失败；
- staging 失败、digest 不匹配或 commit 前注入故障时清理本次临时文件；
- publish 成功后返回只含文件名、size 与 digest 的 receipt，不泄露绝对路径；
- 不覆盖或删除既有目标；
- 本阶段使用本地临时目标模拟可写存储，不宣称已完成特定 NAS/SMB 生产认证。

Runtime 故障场景继续验证 failed 节点不产生 Evidence、必须显式 retry、已完成上游可复用、Final 不可替换。
一次性真实编码的进程级恢复仍由未来真实 Engine 专题实现，本阶段不得用合成 Runtime 冒充。

## 7. 长片规模与资源边界

性能门使用三小时、精确 `24000/1001` 帧率的合成 authority 和 120 个有序 chapter，编译包含多级 Map、
Collect/Reduce 与 program Engine 的 ExecutionPlan。验收同时检查：

- planned node 数量和章节展开符合闭式预期；
- canonical digest 在重建后稳定；
- 编译与序列化在开发门预算内完成；
- 显式资源预算能拒绝过多 chapter、planned node 或 canonical payload。

墙钟阈值只用于发现数量级回退，不承诺生产 SLA。真实长片吞吐、GPU 并发、磁盘容量、NAS 拓扑和模型
速度必须在目标环境另行认证。

## 8. Phase 6 完成门

Phase 6 开发成果必须同时满足：

1. Decensoring/new01 package 通过 Catalog conformance，扩展 Workflow compile/run 成功；
2. Runtime core 不包含 Engine 私有 ID 分支，Studio 从 Python Registry 投影显示两个新 Engine；
3. ZBaton draft 投影、继承、局部章节历史、唯一 current/final 与负例通过；
4. FFmpeg/FFprobe 短真实媒体 gate 实际执行成功；
5. no-replace target publication、故障清理和重复目标失败通过；
6. 120 章节长片规模与资源上限 gate 通过；
7. Python tests、mypy、Ruff、projection drift、Studio tests/typecheck/build 与 wheel 隔离安装全绿；
8. `VERSION`、Python package 与 Studio package 保持 `0.1.0`；
9. 工作树不包含真实媒体、临时目标、Evidence、日志、凭据或本机路径。

完成上述门后可以提交并推送 Phase 6 开发成果。tag、release、生产部署、桌面签名、正式 ZBaton vNext
发布和目标 NAS 认证仍需独立授权与外部 authority。
