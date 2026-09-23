/** 同一展示数据用于全局摘要、节点和右侧；请求没有百分比，外部等待没有忙碌动画。 */
import type { OperationView } from '../operation-presentation'
import './operation-status.css'

export function OperationStatus({ value, compact = false, live = false }: { readonly value: OperationView; readonly compact?: boolean; readonly live?: boolean }) {
  return <span className={`operation-status operation-status--${value.phase}${compact ? ' operation-status--compact' : ''}`}>
    <span className="operation-status-text" aria-live={live ? 'polite' : undefined} aria-atomic={live || undefined} title={`${value.label} · ${value.message}`}>{value.label} · {value.message}</span>
    {value.elapsed && <span className="operation-status-time">{value.elapsed}</span>}
    {value.fraction !== null && <progress aria-label={`${value.label} 实测进度`} max={1} value={value.fraction} />}
    {value.fraction !== null && <span>{Math.round(value.fraction * 100)}%</span>}
    {value.phase === 'running' && value.fraction === null && <span className="operation-status-pulse" aria-label="正在处理，进度未知" />}
  </span>
}
