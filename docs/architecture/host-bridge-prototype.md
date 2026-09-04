# ZNIKU Studio v0.3.0 HostBridge Phase 0 技术选型证据

- 状态：**Phase 0 技术选型证据；非架构权威；不定义稳定 API**
- 日期：2026-09-04
- 上位 UX 权威：[`studio-ux-baseline.md`](studio-ux-baseline.md)
- 上位领域权威：[`graph-core-baseline.md`](graph-core-baseline.md)
- 原型：[`../../tools/host_bridge_prototype.py`](../../tools/host_bridge_prototype.py)
- 自动测试：[`../../tests/test_host_bridge_prototype.py`](../../tests/test_host_bridge_prototype.py)

## 1. 结论

Phase 0 选择且只选择 **候选 B：loopback Project Service 原生对话框代理** 作为 v0.3.0 正式 HostBridge
路线。候选 A 的薄 WebView2/Python desktop shell 不进入 v0.3.0 正式实现，也不保留第二套 HostBridge。

这里的“Project Service 代理”表示复用 launcher 管理的 Python loopback host、HTTP transport 和 React
production build。Host capability 必须保持独立闭集，不能进入 `ProjectServiceApplication` 的 Project、Graph
或 Runtime 领域方法，也不能因为未来 Project Service 允许可信 LAN 就随之暴露到 LAN。

原型只证明路线可行和失败边界可自动测试，不是生产实现。正式实现必须遵守
[`studio-ux-baseline.md`](studio-ux-baseline.md)；Graph、Run、Artifact、stale 和 Runtime 语义继续无条件遵守
[`graph-core-baseline.md`](graph-core-baseline.md)。

## 2. 现场条件

2026-09-04 在当前 Windows 开发机完成以下只读探测：

| 项目 | 结果 | 对选型的影响 |
| --- | --- | --- |
| Windows | Windows 11，build 26200 | 符合 v0.3.0 冻结的首个正式平台 |
| Python | 3.13.12 | 满足项目 `>=3.12` 要求 |
| Tcl/Tk | Tk 8.6；Tcl 8.6.15；`filedialog` 可导入 | 可用标准库实现原生路径选择，无需创建测试窗口 |
| WebView2 Runtime | 152.0.4191.53 | 系统运行时存在，但不等于 Python shell 已具备 |
| Python desktop binding | `pywebview`、PySide6、CEF Python 均未安装 | 候选 A 需要新增依赖、生命周期和打包面 |
| Studio | React 19、Vite 8，开发 server 绑定 `127.0.0.1` | 候选 B 可保持开发与 production 使用同一 HTTP 合同 |
| Project Service host | 标准库 `ThreadingHTTPServer`，绑定 `127.0.0.1` | 可复用 transport 与 launcher，不需要第二种前端 bridge |

`probe_windows_tk_without_window()` 只建立未加载 Tk window manager 的 Tcl interpreter，并检查
`filedialog` callable，没有弹窗。真实对话框只能在后续显式人工 smoke 中触发，不进入自动测试。

## 3. 两种候选比较

| 维度 | A：薄 WebView2/Python shell | B：loopback 原生对话框代理 |
| --- | --- | --- |
| 本机绝对路径 | 可以 | 可以 |
| 双击启动 | 可以，但需新增 desktop lifecycle | 可由 launcher 启动 host、静态站点和默认浏览器 |
| 当前浏览器开发路径 | 需要另建兼容 bridge，容易形成双轨 | 与 production 使用同一 HTTP capability 合同 |
| 新依赖 | 需要 Python WebView binding；系统 WebView2 只解决底层 runtime | Tk 为当前 Python 标准库能力，HTTP host 已存在 |
| 打包面 | WebView binding、runtime 检测、window lifecycle、JS 注入 | Python/Studio 既有打包面加 launcher 与 Tk 资源 |
| 自动化 | 原生 window 与 JS bridge 需要桌面 UI 自动化 | HTTP policy、ticket、路径和 argv 可用注入后端无窗口验证 |
| Origin/token | 可不走 HTTP，但要另审 JS bridge 暴露面 | 可用每次启动 token、精确 Origin 和 strict JSON 直接约束 |
| 普通浏览器降级 | 需要维护与 embedded shell 不同的能力路径 | capability 不可用时同一前端明确降级 |
| 与领域层耦合 | 容易把 window host API 直接暴露给 React | 可把闭集 adapter 放在 Project/Runtime 之外 |

候选 A 的主要优点是单窗口产品感和直接宿主调用，但 v0.3.0 仍要求普通浏览器开发。此时要么维护
WebView2 JS bridge 与浏览器 HTTP bridge 两套行为，要么让嵌入窗口仍绕回 HTTP；前者违反“只保留一种正式
路线”，后者没有足够收益抵消新增依赖与生命周期复杂度。

候选 B 复用已经工作的 loopback 架构，绝对路径和取消语义不依赖浏览器 `<input type=file>`，HTTP 策略可以
无窗口自动测试。它以更小的新增面满足当前阶段要求，因此冻结为唯一正式路线。若未来改变桌面容器，不得在
v0.3.0 内暗中恢复候选 A。

## 4. 原型边界

原型能力闭集精确为：

```text
open_file
open_files
select_directory
save_file
reveal_in_file_manager
open_with_system_player
```

前端不能提供 executable、shell 字符串或任意 argv。后端只把两项系统动作映射为固定命令：

- `reveal_in_file_manager` → `explorer.exe` 加后端生成的 argv；
- `open_with_system_player` → `rundll32.exe`、固定 `url.dll,FileProtocolHandler` 加绝对文件路径。

调用使用 argv 数组且 `shell=False`。原型测试替换 `subprocess.Popen`，不会真的打开 Explorer 或播放器。
正式实现仍须重新评估 Windows 打包后的 executable 定位与错误翻译，但不得改成 `cmd /c start`、PowerShell
字符串或由前端指定命令。

四项选择 capability 的返回值只含宿主绝对路径：

- 不上传、读取或复制媒体；
- `save_file` 只返回路径，不提前创建、覆盖或删除目标；
- filter 仅作为对话框提示，不代替 Python validator；
- 后端返回相对路径、不存在的输入、错误路径类型或重复多选结果时失败关闭。

原型不导入或调用 Project Store、Graph、Runtime、Artifact 或媒体 adapter。取消只返回
`{"status":"cancelled","paths":[]}`，不创建文件，不触发 Project mutation，也不把取消伪装为成功。

为隔离验证 argv 与路径失败语义，本原型的两个系统动作直接接收测试用绝对 `path`；这不是正式 wire
authority。正式实现必须接收 Project Service 已返回的 Artifact、handoff 或当次 picker selection handle，
在 Python 端解析受绑定路径，不能把前端提交的任意 raw path 直接交给系统动作。

## 5. 授权与显式用户动作语义

每次 launcher 启动必须生成至少 256 bit 随机 session token，并把当次 Studio Origin 固定为精确
`http://127.0.0.1:<port>`。HostBridge 的有副作用 route 同时要求：

1. TCP peer 是 loopback；
2. `Origin` 与 launcher 固定值逐字相等；
3. `X-ZNIKU-Host-Token` 与进程内 token 恒定时间比较相等；
4. `Content-Type` 是 JSON，body 有界且字段闭合；
5. 方法是 `POST`；
6. capability 属于闭集；
7. invoke 携带 5 秒内签发、绑定同一 capability、仅可消费一次的 `user_action_id`。

待消费票据最多 64 个；每次签发先清理过期票据，达到上限返回 `429`，防止同一授权页面无限签发造成内存
增长。票据无论 capability 不匹配、过期、参数非法、取消或执行失败都会被消费，不能重放。

两步票据的作用是把调用限定为 Studio 的“点击后签发、随即执行”流程，阻止 GET、页面加载、重试或重复提交
意外触发系统动作。**浏览器不会向 HTTP server 提供可密码学证明真实鼠标/键盘点击的凭据**；因此票据本身
不是安全授权。安全授权仍来自 loopback、精确 Origin 和每次启动随机 session token。若同源受信前端自身被
攻陷，票据不能提供额外隔离，不应在文档或 UI 中声称它能证明真人点击。

token 只允许由 launcher 注入当次页面内存：

- 不写入 `.zniku`、localStorage、日志、URL、query 或错误消息；
- 不作为 Project Service 通用 token；
- 页面关闭或 launcher 退出即失效；
- 开发 Vite 与 production 静态站点分别固定各自当次精确 Origin，不接受 `localhost` 别名或任意 loopback
  Origin。

HostBridge CORS 只回显精确 Origin。预检不携带 token 值，只允许后续请求发送 token header；实际 POST
仍必须通过完整 token 验证。缺失 `Origin` 的非浏览器调用也被拒绝。

## 6. LAN 隔离

HostBridge server 只绑定 `127.0.0.1`，并在请求处理前再次验证 peer 是 loopback。原型测试同时验证 server
绑定地址和模拟 LAN peer 被 `E_HOST_BRIDGE_LAN_FORBIDDEN` 拒绝。

未来即使 Project Service 为可信 LAN 显式改为其他监听地址，HostBridge capability 也必须：

- 保持独立的 loopback listener，或完全不在 LAN listener 注册 route；
- 不因 CORS 允许 LAN Studio origin 而放宽系统动作；
- 不通过反向代理、端口转发或远程浏览器触发本机对话框、Explorer 或播放器。

这是一条产品边界，不是可配置偏好。

## 7. HTTP 原型流程

```text
用户点击按钮
  └─ POST /api/host-bridge/user-actions
       Origin + session token + capability
       └─ 5 秒、单 capability、一次性 user_action_id

同一点击 handler 立即继续
  └─ POST /api/host-bridge/invoke
       Origin + session token + user_action_id + capability + strict arguments
       ├─ selected  → 仅返回绝对路径
       ├─ cancelled → 空 paths，无 Project/文件副作用
       └─ launched  → 固定后端 argv，shell=False
```

刷新、预加载、轮询、自动保存、参数校验和 Run 状态变化都不得调用第一步。网络层自动重试也不能重新执行
invoke，因为 ticket 已消费；用户确需重试时必须再次点击并取得新 ticket。

## 8. 自动验证证据

`tests/test_host_bridge_prototype.py` 使用合成临时文件和 `RecordingPlatform`，覆盖：

- 六项 capability 精确闭集与未知 capability 失败；
- 两个 session token 不同且达到 256 bit token 的编码长度；
- exact Origin、缺失/错误 token、非 POST 和 strict CORS；
- 一次性、绑定 capability、5 秒过期、64 个上限和过期清理；
- 取消不创建 save target，也不启动系统动作；
- open/save 只返回绝对路径，save 不预创建；
- 相对路径、路径类型错误、重复/未知 JSON 字段 fail closed；
- `explorer.exe` / `rundll32.exe` 固定 argv 和 `shell=False`；
- `127.0.0.1` 绑定和 LAN peer 拒绝；
- Tk/Tcl/filedialog 可用性无窗口探测。

自动测试禁止实例化真实 Tk 对话框和调用真实系统进程。因此本证据不宣称已经完成打包、双击 launcher、焦点
恢复、系统主题、长路径、UNC 或人工桌面交互验收；这些属于后续正式实现和 Phase 5 production E2E。

## 9. 正式实现前仍须保留的失败条件

- Tk、原生对话框或系统启动能力不可用时必须返回稳定错误并让 Studio 明确降级；
- chooser 返回结果不能直接视为合法媒体，ParameterDraft 应用和 Run 前仍由 Python 正式验证；
- 系统动作必须把 Artifact、handoff 或 picker selection handle 解析为受绑定路径，不接受 raw client path
  作为正式 authority；
- HostBridge 错误不得改变 Runtime 状态、制造 Artifact 或隐式 stale；
- 页面卸载、HTTP 断连或响应丢失不得自动重放动作；
- 关闭 Project 或切换页面不能销毁 launcher 管理的 token/session 边界；
- 正式实现不得复制本原型到另一套 embedded bridge，从而形成行为分叉。

## 10. 冻结选型

> **v0.3.0 HostBridge 复用 launcher 管理的 Python loopback HTTP host，通过随机 session token、精确
> Studio Origin、显式 POST、短时一次性用户动作票据和后端固定 capability 提供原生路径与系统动作；
> WebView2/Python desktop shell 不进入 v0.3.0 正式路线。**
