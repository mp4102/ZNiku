/** 卡片与选中状态共用正式字节测量；到达总量仍等待 Runtime 确认完成。 */
import type { CopyProgressPresentation } from '../copy-progress'
import './copy-progress.css'

export function CopyProgress({ value }: { readonly value: CopyProgressPresentation }) {
  return <div className="copy-progress-summary" aria-label="文件复制进度">
    <span>{value.label}</span>
    <progress aria-label="文件复制字节进度" value={value.current} max={value.total} />
    <span>{value.volume}</span>
    {value.averageRate && <span title="截至服务端进度采样时间，已复制字节除以本步骤已用时间；包含步骤准备时间，不代表瞬时网速。">{value.averageRate}</span>}
  </div>
}
