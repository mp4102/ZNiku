import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { SourcePreparationProgress, type PreparationStageMeasurement } from './SourcePreparationProgress'

afterEach(cleanup)
const measured: PreparationStageMeasurement = {
  stage: '完整视频检查', current: 240, total: 1000, unit: '帧', elapsed_seconds: 12.4, speed: '19.4 帧/秒',
}
describe('素材准备仅显示当前实测阶段', () => {
  it.each([['work_retime', '按确认帧率生成工作副本'], ['admission', '核对工作素材与输入绑定']])('普通%s阶段使用创作者文案', (stage, label) => {
    render(<SourcePreparationProgress mode="working" measurement={{ ...measured, stage }} />)
    expect(screen.getByRole('heading', { name: label })).toBeVisible()
    expect(screen.queryByText(/准入|保内容|T1/)).not.toBeInTheDocument()
  })
  it.each([['color_frames', '检查逐帧色彩与属性'], ['color_bitstream', '检查全片码流声明'], ['color_prores_headers', '检查逐包 ProRes 帧头声明'], ['color_hevc_sps_sei', '检查全片 HEVC 参数集与补充声明']])('新工作解释阶段 %s 使用明确中文标签', (stage, label) => {
    render(<SourcePreparationProgress measurement={{ ...measured, stage, current: null, total: null }} />)
    expect(screen.getByRole('heading', { name: label })).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).not.toHaveAttribute('value')
    expect(screen.queryByText('工作源已通过准入')).not.toBeInTheDocument()
  })
  it('码流时钟初检使用中文标签，不假定已经完成完整帧检查', () => {
    render(<SourcePreparationProgress measurement={{ ...measured, stage: 'bitstream_header', current: null, total: null }} />)
    expect(screen.getByRole('heading', { name: '检查码流时钟' })).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: '检查码流时钟阶段进度' })).not.toHaveAttribute('value')
    expect(screen.queryByText('完整检查视频帧与时钟')).not.toBeInTheDocument()
  })
  it('有可信分母才显示原生确定进度，速度和耗时不推算', () => {
    render(<SourcePreparationProgress measurement={measured} />)
    expect(screen.getByRole('progressbar')).toHaveAttribute('value', '240')
    expect(screen.getByRole('progressbar')).toHaveAttribute('max', '1000')
    expect(screen.getByText('240 / 1,000 帧')).toBeInTheDocument()
    expect(screen.getByText('19.4 帧/秒')).toBeInTheDocument()
    expect(screen.getByText('12.4 秒')).toBeInTheDocument()
  })
  it('没有总量仍可显示已检查帧，不根据文件时长猜分母或 ETA', () => {
    render(<SourcePreparationProgress measurement={{ ...measured, total: null, speed: null }} />)
    expect(screen.getByRole('progressbar')).not.toHaveAttribute('value')
    expect(screen.getByText('240 帧')).toBeInTheDocument()
    expect(screen.queryByText(/%|剩余|预计时间/)).not.toBeInTheDocument()
  })
  it('候选写完仅是当前阶段结束，不称为修复完成或可运行', () => {
    render(<SourcePreparationProgress measurement={{ ...measured, stage: '候选写入', current: 1000 }} />)
    expect(screen.getByText(/仍须完成内容验证和工作源准入/)).toBeInTheDocument()
    expect(screen.queryByText(/修复完成|可直接处理|已可运行/)).not.toBeInTheDocument()
  })
  it('普通准备复用实测进度，不向创作者暴露准入或宣称保内容审计', () => {
    render(<SourcePreparationProgress mode="working" measurement={{ ...measured, stage: '候选写入', current: 1000 }} />)
    expect(screen.getByText(/工作副本写入后仍须检查实际结果/)).toBeInTheDocument()
    expect(screen.queryByText(/保内容|准入|100%|修复完成/)).not.toBeInTheDocument()
  })
  it('无测量或不完整分母安全显示不确定进度', () => {
    render(<SourcePreparationProgress measurement={{ stage: '核对音频关系', current: null, total: null, unit: null, elapsed_seconds: null, speed: null }} />)
    expect(screen.getByRole('progressbar')).not.toHaveAttribute('value')
    expect(screen.getAllByText('暂无测量')).toHaveLength(2)
  })
})
