# ZNIKU Studio Windows 桌面候选

本入口属于 v0.3.0 Phase 5，复用 `0.2.0` Core 和同一 React production build；不是新 Runtime，
也不是正式 Release。产品版本在 Phase 6 前仍为 `0.2.0`。

## 使用

完整解压本地候选目录，双击 `ZNIKU Studio.exe`。无需 Python、npm 或终端。入口占有 Windows 分配的
loopback 端口，通过本实例健康检查后打开默认浏览器。再次双击只打开已有实例，不启动第二套服务。

- 用界面原生选择器打开或创建 `.zniku`，正常路径不需要手写文件地址。
- 文件、批量文件、文件夹和保存位置选择窗口均临时置顶，包括向导和外部处理导入；关闭选择窗口即释放，
  不让整个应用长期置顶。若提示已有选择窗口打开，先完成或取消该窗口，再发起新的选择，不必重新处理媒体。
- 打开工程默认展示完整的当前工作流，可直接拖动编辑；历史处理记录仅在明确查看时切入只读快照。
  “适应画布”可以找回视口外的节点，不会重排节点或改动参数。
- “选择成片文件夹”默认就是成品保存位置，无需预先创建片名子目录。可选“按片名创建子文件夹”用于
  媒体库整理；设置先检查位置，确认页显示完整成品路径。选择、预览和创建工作流都不建外部目录，只有
  开始处理后的输出步骤才按需创建该直属子目录。目标已存在仍需明确选择覆盖。
- 关闭浏览器标签页不会结束后台服务；再次双击可以返回同一实例。
- 更新到新候选包前，先在旧页面点击“退出应用”并确认，再双击新目录中的 exe；只关闭旧标签不够。
  单实例保护不会用新包强行替换正在运行的旧服务，直接双击新包可能仍回到旧实例。
- 退出使用界面的“退出应用”，确认前可以取消。自动处理、检查或提交仍在工作时明确拒绝退出，任务保持
  运行；这不是 Runtime 的取消按钮。
- `waiting_external` 可以退出，重开工程后继续检查和显式提交。不会自动提交外部文件。
- 外部处理时先选中对应节点；右侧只显示这个任务。同名节点会加展示编号，请结合输入文件名和帧数要求
  认领任务，不要仅凭 `enhancement.mov` 这个通用文件名判断分段。
- 外部软件的成品可以先保存在自己的目录，再点“选择处理好的文件”。确认页会写明文件和目标任务；已有
  目标时须明确确认替换。导入复制原文件，先校验后发布，失败保留旧目标；不会移动原文件或自动继续运行。
  导入后再点“检查输出”，检查通过后点“提交并继续”。也可以手动放到该任务的完整目标路径，再检查提交。
- 意外结束应用时，Windows 只清理本实例自动处理进程树；再次打开工程继续采用 Core 的
  `running → failed(reason=interrupted)`，只能创建新 attempt 从头重跑。浏览器、Explorer 和系统播放器
  不属于此清理树，不会因退出被误杀。
- 本机模式偏好与最近 8 个工程保存于 `%LOCALAPPDATA%/ZNIKU/Studio`，不因随机端口变化丢失；它们不是
  Project/Graph authority，也不影响复用或失效。损坏偏好安全回到默认创作者模式与空列表。

## 开发构建

先通过 Studio 生产构建与 Python 门禁。以下命令只供开发者，不是创作者操作步骤：

```powershell
uv sync --locked --extra dev --extra desktop
uv run --locked --extra desktop python tools/build_desktop.py `
  --media-distribution-root <包含bin/ffmpeg.exe、bin/ffprobe.exe、LICENSE、README.txt的目录> `
  --output .test-tmp-desktop-candidate
```

必须使用全新输出目录，不覆盖、递归删除旧候选。打包输出位于指定目录下的 `dist/ZNIKU Studio`；保留整个
目录及 `_internal`，不能只复制 exe。构建只读取显式提供的媒体工具，不下载二进制，不修改系统 PATH。

使用 [PyInstaller one-folder](https://pyinstaller.org/en/stable/operating-mode.html) 打包 Python、Tk 与生产
静态资源。媒体工具原始 `LICENSE` 与 `README.txt` 保留在包内，具体来源以提供的原始分发文档为准。
`BUILD-INFO.json` 记录构建版本与许可文件位置，不记录真实媒体或个人工程路径。

当前输出仅供本机验收。构建成功不表示已完成第三方工具、编解码器、插件及其完整对应源码的公开再分发
审阅；不得据此自动上传包、创建 Release 或声称发行许可已闭环。

## 本机边界与关闭规则

生产 HTML、资源和 API 同源，只监听 `127.0.0.1`。静态路由验证 exact Host、Origin 和解析后的资源根；
不提供任意文件下载。bootstrap token 每次启动新建，只存在进程与页面内存，不进入 URL、日志、工程或偏好。
同源 GET 的浏览器只有同时提供 `Sec-Fetch-Site: same-origin`、精确 Host 与 token 才可补齐缺省 Origin；
`same-site`、其他 loopback Origin 和无 token 请求不获 HostBridge 权限。

HostBridge 继续使用冻结的六项能力与短时一次性动作票据。桌面生命周期/偏好/交接导入 API 不是新 HostCapability，
不接收 executable、shell 或媒体 raw path。偏好中的最近工程路径仅用于显示和重新发起正式打开命令。

外部产物导入使用独立的同源 `POST /api/studio/handoff-import/preview` 和 `/confirm`，沿用精确 Origin 与
Host token。preview 只接受原生选择句柄和当前工程/Run/NodeRun/handoff/port/ordinal，返回 300 秒一次性
`import_id`、源名称/大小、目标和覆盖提示；confirm 只接受该票据及显式 `overwrite`。服务端最多保留 32 个
未使用意图，选择与目标的身份/大小/修改时间变化即拒绝。当前导入只支持恰好声明一个单值输出的人工节点，
多输出仍按各完整目标放置文件后统一检查；未知字段失败关闭。
暂存限定在解析验证后的 attempt 内，拒绝链接/reparse 目标与被硬链接共享的目标；验证复用 Runtime 的
同一 Node Runner，不在 TypeScript 再实现媒体合同。复制期间 status 仍可读取，切换工程、运行、提交及退出
被同一 busy 门禁拒绝；界面不伪造百分比，网络结果不明不自动重试。内存票据不是新 Project/Runtime authority。

关闭准入和 Project command 使用同一锁，避免检查“空闲”后另一个 Run 抢入。已进入关闭状态后新的 command
全部拒绝。正常退出不会删除工程、上游 Artifact、attempt 或输出。预览仅是有界内存缓存，退出即释放。

[Windows Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects) 管理本实例媒体
子进程归属。无法建立正确归属时明确拒绝启动，不退化为扫描 PID 或无条件杀进程。固定浏览器/播放器/目录
动作以 `shell=False` 和显式 breakaway 启动，不能把用户外部程序纳入本实例清理。

FFmpeg、FFprobe、预览和普通 command executor 在 Windows 只增加 `CREATE_NO_WINDOW` 显示标志，
避免后台工具闪出控制台；其他平台为 `0`。这不修改 argv、shell、超时、进程退出/回收、validator 或
Artifact 登记条件，也不隐藏原有有界 stdout/stderr 日志。

## 当前验收边界

Phase 5 已验证 Windows 包、生产入口、生命周期、原生能力合同、轻量预览和本地自动门禁；Explorer、播放器
及升级后的完整原生交互仍待操作者验收，阶段尚未关闭，详见 [Phase 5 验收记录](v0.3.0-phase5-acceptance.md)。
真实首次用户
五条 Journey、至少 5 名用户的量化验收属于 Phase 6，不以开发者测试替代。此入口不提供 WebView2、NLE
时间线、代理播放、内部节点 resume 或外部 AI 工具进度接管。
