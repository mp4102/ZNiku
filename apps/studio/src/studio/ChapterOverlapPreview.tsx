/** 有界展示服务计算的章叶与 FI 窗口；分页不生成或改变媒体计划。 */
import { useState } from 'react'
import type { OverlapFullEnvelope } from './chapter-overlap-contracts'

export function ChapterOverlapPreview({ preview }: { readonly preview: OverlapFullEnvelope }) {
  const [page, setPage] = useState(0)
  const pageCount = Math.max(1, Math.ceil(preview.plan.chapters.length / 20))
  const currentPage = Math.min(page, pageCount - 1)
  const contexts = new Map(preview.contexts.chapters.map((item) => [item.chapter_id, item]))
  return <section className="creator-step creator-confirm" aria-label="确认重叠补帧工作流">
    <header><span>05</span><div><h3>确认重叠补帧工作流</h3><p>以下边界由 Python 根据这次素材分析计算；确认只创建普通节点图，不启动外部软件。</p></div></header>
    <div className="creator-profile-result" role="status"><strong>ZNIKU 重叠 FI 候选 · 待真实验收</strong><span>{preview.node_count} 个节点 · {preview.edge_count} 条连线 · {preview.plan.chapter_count} 章 · {preview.plan.leaf_count} 个处理段</span></div>
    <p>Aion · 软件 v1.0。当前是可执行候选，尚未完成真实模型边界验收；数学帧数正确不代表 AI 画质或全片像素一致。</p>
    <section className="creator-media-summary" aria-label="重叠流程素材摘要"><h4>素材与处理</h4><dl>
      <div><dt>源帧数 / 帧率</dt><dd>{preview.plan.source.frame_count} 帧 · {preview.plan.source.frame_rate} fps</dd></div>
      <div><dt>每段最长时长</dt><dd>{preview.plan.settings.leaf_max_minutes} 分钟 · 不跨章节</dd></div>
      <div><dt>画质增强</dt><dd>{preview.processing.enhancement.model_name} · {preview.processing.enhancement.actual_scale_factor ?? 1} 倍</dd></div>
      <div><dt>最终编码帧数</dt><dd>{preview.contexts.encoded_frame_count} 帧 · 全片最后补 {preview.contexts.final_tail_clone_frames} 帧</dd></div>
    </dl></section>
    <section className="creator-workflow-summary"><h4>处理顺序</h4><p>分章与分叶 → 外部逐叶增强 → 章内合并 → 收集相邻章节增强上下文 → 外部 Aion 补帧 → 精确裁边 → 连续编码与原音轨封装。</p><p>上下文步骤需要所引用的相邻章节先完成增强。等待没有估算倒计时；外部原始结果会保留，裁边结果另存。</p></section>
    <section aria-label="章节计划"><h4>章节与处理段</h4>
      <div className="chapter-cut-pagination" aria-label="章节预览分页"><button type="button" disabled={currentPage === 0} onClick={() => setPage(0)}>第一页</button><button type="button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>上一页</button><span>第 {currentPage + 1} / {pageCount} 页</span><button type="button" disabled={currentPage + 1 >= pageCount} onClick={() => setPage(currentPage + 1)}>下一页</button><button type="button" disabled={currentPage + 1 >= pageCount} onClick={() => setPage(pageCount - 1)}>最后一页</button></div>
      {preview.plan.chapters.slice(currentPage * 20, (currentPage + 1) * 20).map((chapter) => {
        const context = contexts.get(chapter.chapter_id)
        const cut = preview.plan.cut_points.find((item) => item.actual_frame === chapter.start_frame)
        return <article className="creator-target" key={chapter.chapter_id}>
          <strong>{chapter.label} 章 · {chapter.frame_count} 帧 · {chapter.leaves.length} 个处理段</strong>
          <span>{chapter.start_timecode} → {chapter.end_timecode} · 时长 {chapter.duration_timecode}</span>
          {cut && <small>请求切点：{cut.requested_time ?? cut.requested_frame ?? '平均分章'} → 实际第 {cut.actual_frame} 帧（{cut.actual_timecode}）</small>}
          <details><summary>精确帧与补帧窗口</summary><p>正式章节 [{chapter.start_frame}, {chapter.end_frame})</p>
            {context && <><p>上下文输入 [{context.context_start_frame}, {context.context_end_frame}) · {context.input_frame_count} 帧；引用章节 {context.sources.map((source) => preview.plan.chapters.find((item) => item.chapter_id === source.chapter_id)?.label ?? source.chapter_id).join('、')}</p><p>外部原始结果要求 {context.raw_fi_frame_count} 帧；保留区间 [{context.crop_start_frame}, {context.crop_end_frame}) → {context.cropped_frame_count} 帧。</p></>}
            <p>叶的精确区间保存在 Python 计划中；此处不逐条展开大量数据，可在生成后的节点参数中查看。</p>
          </details>
        </article>
      })}
    </section>
    <div className="creator-target"><span>预计成片</span><strong>{preview.publication.output_target_path}</strong><small>{preview.publication.output_directory_to_create ? `输出步骤将创建：${preview.publication.output_directory_to_create}；确认前不创建。` : '保存至已确认目录；默认不覆盖已有文件。'}</small></div>
  </section>
}
