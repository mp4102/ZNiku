# v0.3.1 几何实施前门禁

独立纯合成实验，不被正式 `src/` 导入、不接入 UI、不新增依赖、不改 Graph/Core 或 `.zniku`。
现有锁定的 `@xyflow/react@12.11.3` 仅用于真实 smoothstep 基准及明确标记的降级线。

在 `apps/studio` 安装既有锁定依赖后运行：

```powershell
npm ci
node --test preflight/geometry.test.mjs
node preflight/run.mjs
```

测试独立于 Vitest，由 Studio CI 直接运行 Node 命令。基准脚本只向 stdout 输出合成 JSON；不接受文件路径
参数，不探测用户目录或媒体。每组 3 次预热、20 次重复；时间数据会随机器和负载变化。

- `fixtures.mjs`：G01–G10 共 20 个变体，包含 1/2/6/8/16 口、动态尺寸、长跳线、重叠、故意超预算。
- `geometry.mjs`：闭合实验输入、生产 fixed-grid 公式镜像、尺寸感知分层布局、锚点和独立相交检查。
- `router.mjs`：坐标压缩正交通道 + 有界 A*，确定性并列排序，单项有界缓存和会话结果版本栅栏。
- `geometry.test.mjs`：严格输入、变体不变量、实际曲线相交、缓存失效、端点、平行边和迟到结果测试。
- `run.mjs`：基准/替代方案的确定性、几何正确性、耗时、预算与源码 raw/gzip 统计。

报告和选型边界见 [图形实施前报告](../../../docs/v0.3.1-graph-preflight.md)。这不是产品发布或批次 B 验收：
不测真实 DOM 尺寸、拖动 p95、字体变化、屏幕阅读器、Undo/保存，也不声称测过 1000 条 Run 的服务分页。
纯几何试验中的行锚点是拟议测量结果，不能作为正式卡片已修复的证据。
