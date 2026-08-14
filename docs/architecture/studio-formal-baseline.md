# ZNIKU Studio 0.1.0 正式工作区基线

- 状态：**已批准的正式基线；产品 Phase 5 已实现**
- 日期：2026-08-15
- 前端入口：`apps/studio/`
- Python 投影入口：`src/zniku/studio/`
- 上位基线：[`studio-framework.md`](./studio-framework.md)
- Application 依赖：[`agent-application-baseline.md`](./agent-application-baseline.md)

## 1. 定位

本文冻结产品 Phase 5 的正式图形化工作区：Designer、默认流程 Expanded Plan 和 Runtime snapshot Monitor。
Studio 是 Python authority 的客户端，不执行 Engine、不写 Runtime 状态、不判定节点完成，也不维护第二套
WorkflowSpec、typed port、Compiler 或状态机。

Phase 5 交付开发期浏览器形态；Tauri 2 桌面封装、Windows 安装、签名、SBOM、后台服务发现和真实媒体
GUI E2E 属于 Studio GUI-5/GUI-6 与产品 Phase 6，不在本阶段冒充完成。

## 2. 单向合同投影

`zniku.authoring.projection` 从 Python Pydantic authority 确定生成并纳入 drift manifest：

- authoring wire、EngineManifest、CoreNodeContractSet 和 Studio authority JSON Schema；
- 对应 readonly TypeScript DTO；
- Source/Final exact ports；
- Partition、Map、Select、Passthrough、Collect、Reduce 的 video/audio exact ports；
- 默认 WorkflowSpec、Registry manifests、ChapterPlan、ExecutionPlan、Revision 与合成 Runtime snapshot。

Studio 对所有入站 payload 运行 Ajv 2020-12 fail-closed 校验。未知字段、未知 union variant、缺失版本或
manifest identity/digest 不一致均不得进入 Graph View Model。

## 3. Designer

Designer 通过 `AuthoringGateway` 与 Python bridge 交互：

- 默认请求 `draft.default`；
- EngineStage 与 Map Engine 都按精确 manifest digest hydrate；
- Core Operator 节点和端口只使用 Python `operator_contracts` 投影；
- 连线、断线和参数修改提交 typed AuthoringCommand；
- 0.2.0 默认 Draft 编辑后继续保持 0.2.0，不降级为 legacy 0.1.0；
- Map 参数可以通过同一 `replace_parameters` 命令和 Compiler Schema gate 修改；
- Canvas 只保留坐标、选中等 EditorState，不改变 ArtifactSet 顺序或执行语义。

Inspector 同时提供 Manifest Schema 基础表单与完整 JSON 专家视图；前者只改善输入体验，最终完整对象仍
提交 Python Service 重新验证。

## 4. Expanded Plan

Expanded Plan 是只读视图，展示 Python 生成的默认流程 authority：

- 三个 chapter member 的稳定 ID、scope target 和 frame coverage；
- 13 个 planned node、依赖、scope、EngineBinding 与 execution mode；
- 两段人工 Engine 展开为 6 个 chapter handoff 实例；
- Revision 绑定的 ExecutionPlan digest。

前端不重新编译 WorkflowSpec，不自行展开 chapter/leaf，也不允许在 Plan 视图拖线或改参数。

## 5. Run Monitor

Run Monitor 只读投影一个由 Python Application Service 启动并推进 source 后生成的合成 snapshot：

- 展示 complete、ready、blocked、failed 计数和每个 planned node attempt/Evidence；
- 显示 Runtime revision 与 plan digest；
- 显式显示 Demux→Mux 原始 AudioArtifactSet、音轨顺序及 `stream_copy=true`；
- Final 未 full-verified 时不显示伪完成或 GUI 进度推断。

当前 checked-in snapshot 用于合同、布局和跨语言验收，不是活动 Run 的实时事件流。Application Service
实时 API、断线补发、SSE/WebSocket 与 durable persistence 必须在后续专题接入，不能由静态投影冒充。

## 6. 安全与权限

- Python bridge 缺失时 Designer 显式 `AUTHORITY UNAVAILABLE`，不回退 mock Compiler；
- 只读 Plan/Monitor 仍可展示 checked-in Python synthetic projection，但清楚标注其来源；
- Studio 不接受 shell、entrypoint、argv、路径执行或任意 Engine invocation；
- Freeze/Run 的按钮不能在浏览器本地制造 authority；
- 正式媒体路径、日志、模型、凭据、Evidence 和 final 不进入前端 bundle 或 Git。

## 7. Phase 5 验收

Phase 5 至少证明：

1. live Python bridge 可加载并编辑含 Core Operator 的默认 0.2.0 Draft；
2. Map manifests、operator ports、Engine parameter Schema 全部来自 Python projection；
3. 默认音频边、章节展开、manual handoff 数量和 plan digest 可视化正确；
4. Runtime snapshot 状态、Evidence 与原始音轨 authority 只读显示；
5. bridge 缺失、未知字段、manifest drift 与非法 command fail closed；
6. Python projection drift、Python 测试、mypy、Ruff、Studio typecheck/test/build 全部通过；
7. 本地浏览器对 Designer fail-closed、Expanded Plan 和 Run Monitor 完成视觉及交互烟测。
