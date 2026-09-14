/** 展示服务实测的当前准备阶段；没有可信分母时不生成百分比或剩余时间。 */
export interface PreparationStageMeasurement {
  readonly stage: string
  readonly current: number | null
  readonly total: number | null
  readonly unit: string | null
  readonly elapsed_seconds: number | null
  readonly speed: string | null
}

export function SourcePreparationProgress({ measurement, mode = 'strict' }: { readonly measurement: PreparationStageMeasurement; readonly mode?: 'working' | 'strict' }) {
  const labels: Readonly<Record<string, string>> = { bitstream_header: '检查码流时钟', color_frames: '检查逐帧色彩与属性', color_bitstream: '检查全片码流声明', color_prores_headers: '检查逐包 ProRes 帧头声明', color_hevc_sps_sei: '检查全片 HEVC 参数集与补充声明', video_scan: '完整检查视频帧与时钟', packet_scan: '检查视频数据包时间轴',
    audio_scan: '完整检查音频样本', audio_packet_scan: '检查音频数据包时间轴', remux: '生成兼容工作副本',
    video_compare: '完整比对视频内容', audio_compare: '完整比对音频内容', external_verify: '验证外部保内容修复', admission: '独立检查工作源准入', work_retime: '按确认帧率生成工作副本' }
  const ordinaryLabels: Readonly<Record<string, string>> = { admission: '核对工作素材与输入绑定', video_scan: '检查视频帧、时间与可观察属性' }
  const stage = (mode === 'working' ? ordinaryLabels[measurement.stage] : undefined) ?? labels[measurement.stage] ?? measurement.stage
  const determinate = measurement.current !== null && measurement.total !== null && measurement.total > 0
    && measurement.current >= 0 && measurement.current <= measurement.total
  return <section className="source-preparation-progress" aria-label="当前素材准备进度">
    <h4>{stage}</h4>
    <progress aria-label={`${stage}阶段进度`} max={determinate ? measurement.total! : undefined} value={determinate ? measurement.current! : undefined} />
    <dl>
      {measurement.current !== null && <div><dt>当前阶段已处理</dt><dd>{measurement.current.toLocaleString('zh-CN')}{measurement.total !== null ? ` / ${measurement.total.toLocaleString('zh-CN')}` : ''} {measurement.unit ?? ''}</dd></div>}
      <div><dt>当前阶段已用时</dt><dd>{measurement.elapsed_seconds === null ? '暂无测量' : `${measurement.elapsed_seconds.toLocaleString('zh-CN', { maximumFractionDigits: 1 })} 秒`}</dd></div>
      <div><dt>实测速度</dt><dd>{measurement.speed ?? '暂无测量'}</dd></div>
    </dl>
    <p>{mode === 'working' ? '这是当前阶段的进度，不是整项准备的完成比例。工作副本写入后仍须检查实际结果；检查完成才可继续。' : '这是当前阶段的进度，不是整项准备的完成比例。工作副本写入后仍须完成内容验证和工作源准入。'}</p>
  </section>
}
