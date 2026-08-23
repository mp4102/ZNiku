# ZNIKU 0.1.0 架构历史归档

- 状态：**只读历史说明；对 0.2.0 没有规范权威**
- 归档基点：`main@198d802`
- 归档日期：2026-08-23
- 当前权威：[`graph-core-baseline.md`](../../architecture/graph-core-baseline.md)

本目录保存 ZNIKU 0.1.0 已实现合同、Compiler、Runtime、Studio、真实媒体候选和 ZBaton 设计的历史
上下文。文档中的“正式基线”“已冻结”和类似措辞只描述当时的 0.1.0 实现，不得用于约束严格不兼容的
0.2.0 重构。

## 归档内容

- [`product-framework.md`](product-framework.md)
- [`studio-framework.md`](studio-framework.md)
- [`engine-contract.md`](engine-contract.md)
- [`engine-sdk-baseline.md`](engine-sdk-baseline.md)
- [`workflow-authoring-compiler-baseline.md`](workflow-authoring-compiler-baseline.md)
- [`execution-runtime-baseline.md`](execution-runtime-baseline.md)
- [`default-workflow-baseline.md`](default-workflow-baseline.md)
- [`agent-application-baseline.md`](agent-application-baseline.md)
- [`studio-formal-baseline.md`](studio-formal-baseline.md)
- [`real-media-acceptance-candidate-baseline.md`](real-media-acceptance-candidate-baseline.md)
- [`phase6-extension-validation-baseline.md`](phase6-extension-validation-baseline.md)
- [`zbaton/`](zbaton/)

## 迁移边界

0.1.0 Runtime snapshot、Evidence、receipt、真实媒体候选工作根、前端 authority 投影和本机运行目录不迁移
到 0.2.0，也不得提交 Git。旧源码和回归测试会在重构期间暂时保留，最终实现清理由 0.2.0 Phase 5 处理。
