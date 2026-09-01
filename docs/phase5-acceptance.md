# ZNIKU 0.2.0 Phase 5 最小验收

- 状态：已实施的自动化验收说明
- 版本：`0.2.0`
- 架构权威：[`architecture/graph-core-baseline.md`](architecture/graph-core-baseline.md)

本文记录 Phase 5 的可重复门禁，不定义新的 Runtime 或媒体语义。旧 `0.1.0` 文档继续只保存在
[`archive/0.1.0/`](archive/0.1.0/)，不参与测试、类型检查、构建或产品运行。

## 自动化门禁

Core CI 只验证当前 `0.2.0` 实现：

1. `uv lock --check` 与 locked dev dependencies；
2. 当前 Python tests；
3. Project Service → Studio Schema 一致性；
4. `mypy --strict`、Ruff lint 与 format check；
5. `tools/run_media_smoke.py` 的短合成媒体端到端运行。

Studio CI 独立运行 dependency install、typecheck、tests、production build 与 dependency audit。不存在旧
authoring projection、Real Acceptance 页面或 legacy regression 的第二套门禁。

## 短媒体 smoke

`tools/run_media_smoke.py` 每次在自动清理的临时目录生成一段 `160×90`、12 fps、12 帧 FFV1 Matroska，
然后通过正式 `RuntimeService` 执行同一张可编辑 Graph 中的两条分支：

```text
SourceMedia → VideoTransform → OutputFile

SourceMedia → SplitVideo → VideoTransform A ─┐
                         → VideoTransform B ─┴→ MergeVideo → OutputFile
```

smoke 强制检查：

- 首次 Run 的全部节点 `completed`；
- Split Artifact 的 `[0,5)`、`[5,12)` frame range；
- 两个发布输出都能被 FFprobe 识别且各为 12 帧；
- 第二次 Run 完整复用全部已验收节点结果；
- 所有媒体、`.zniku`、attempt、日志和发布输出在进程结束时随临时目录删除。

该 Graph 只是验收 fixture，不是产品内置 workflow，也不限制 Studio 自由编排。

本地运行：

```powershell
uv run --locked --extra dev python tools/run_media_smoke.py
```

成功时工具输出一个不含本机路径的 JSON 摘要；任何工具缺失、进程失败、帧数漂移、发布失败或复用失败都以
非零退出结束。

## 证明边界

此门禁证明真实 FFmpeg/FFprobe、Graph Core、Runtime、Split/Merge 帧守恒、OutputFile 发布和 completed 复用
可以在极短媒体上闭环。它不证明：

- 长片性能、磁盘容量、主观画质或模型效果；
- 操作者实际使用了某个 MR、Enhancement 或 FI 工具／模型；
- SMB 断线、压力、安全攻击、公网或多用户场景；
- checksum、full verification、Evidence、receipt 或归档 Manifest。

操作者仍可用 `tools/create_media_smoke_project.py` 从自己选择的短视频创建可在 Studio 中打开、自由修改和运行
的工程。真实长片属于操作者验收，不进入 CI，也不得提交 Git。
