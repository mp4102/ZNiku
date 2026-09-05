/** 稳定状态及错误分类的纯文本投影；不决定阻塞、重试、提交或复用资格。 */
import type { RunSummaryWire } from './contracts'

const stateLabels: Readonly<Record<string, string>> = {
  pending: '等待开始', running: '正在处理', waiting_external: '等待外部处理',
  completed: '已完成', failed: '需要处理问题', stale: '结果需要更新', reused: '已复用完成结果',
}
export function nodeStateLabel(state: string): string { return Object.hasOwn(stateLabels, state) ? stateLabels[state]! : '状态暂时无法识别' }
/** 只翻译既有耗时投影，不重算时间、不产生 ETA，也不把未知文本当作测量。 */
export function creatorElapsedLabel(elapsed: string, waiting = false): string {
  if (/^(已等待|已用时|耗时) /.test(elapsed)) return elapsed
  const match = /^elapsed (\d+)([smh])(?: (\d+)([sm]))?$/.exec(elapsed)
  if (!match || (match[4] && !((match[2] === 'h' && match[4] === 'm') || (match[2] === 'm' && match[4] === 's')))) {
    return waiting ? '等待时长暂不可用' : '耗时暂不可用'
  }
  const unit = (value: string) => value === 'h' ? '小时' : value === 'm' ? '分钟' : '秒'
  const first = `${match[1]} ${unit(match[2]!)}`
  const second = match[3] && match[4] ? ` ${match[3]} ${unit(match[4])}` : ''
  return `${waiting ? '已等待' : '已用时'} ${first}${second}`
}
export function runTargetLabel(summary: RunSummaryWire, nodeLabel?: (nodeId: string) => string): string {
  if (summary.target_mode === 'all') return '完整工作流'
  return nodeLabel ? `处理到 ${summary.selected_targets.map(nodeLabel).join('、')}` : '处理到所选步骤'
}
export function runHistoryLabel(summary: RunSummaryWire, nodeLabel?: (nodeId: string) => string): string {
  const date = new Date(summary.created_at)
  const time = Number.isNaN(date.valueOf()) ? '时间不可用' : new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(date)
  // 等待提示只读 Python 计数，不改写 Run 的正式 state。
  const result = summary.state_counts.failed > 0 ? '需要处理问题'
    : summary.state_counts.waiting_external > 0 ? '等待外部处理' : nodeStateLabel(summary.state)
  return `${time} · ${runTargetLabel(summary, nodeLabel)} · ${result} · 已完成 ${summary.state_counts.completed}/${summary.node_count} 步`
}
export interface FailurePresentation {
  readonly known: boolean
  readonly title: string
  readonly cause: string
  readonly preserved: string
  readonly recovery: string
}
const retained = '已登记的处理结果不会因此被删除；是否仍可复用，由运行服务重新确认。'
const copy = (title: string, cause: string, recovery: string, preserved = retained): FailurePresentation => ({ known: true, title, cause, preserved, recovery })
const missingInput = copy('还缺少输入连接', '这个步骤需要的输入尚未连接，因此不能开始处理。', '定位步骤，把兼容的上游输出连接到标出的输入。')
const parameters = copy('有设置需要补全或修正', '设置未通过该节点的参数检查。', '定位步骤，查看标出的字段，修正后点击“应用设置”。')
const unknownBinding = copy('工作流引用已失效', '节点、版本或连接引用无法被当前工程识别。', '定位相关位置，恢复可用节点或重新连接；不要直接修改工程文件。')
const order = copy('合并输入顺序需要修正', '多路输入的次序不完整或重复，当前不能运行。', '定位合并步骤，在输入列表中重新整理顺序；必要时移除错误连接后重新连接。')
const outdated = copy('工程内容已变化', '当前页面与保存的工程或外部任务不再一致，操作已被拒绝。', '先保留未保存的编辑，再重新打开工程并查看当前任务；不要重复提交旧页面的操作。')
const busy = copy('已有处理正在进行', '当前操作与正在执行的任务冲突，暂时不能开始。', '先查看当前处理进度或完成正在等待的外部任务，再重试。')
const failures: Readonly<Record<string, FailurePresentation>> = {
  E_REQUIRED_INPUT_MISSING: missingInput, E_PARAMETERS_INVALID: parameters,
  E_NODE_DUPLICATE: unknownBinding, E_DEFINITION_UNKNOWN: unknownBinding,
  E_EDGE_SOURCE_NODE_UNKNOWN: unknownBinding, E_EDGE_TARGET_NODE_UNKNOWN: unknownBinding,
  E_EDGE_SOURCE_PORT_UNKNOWN: unknownBinding, E_EDGE_TARGET_PORT_UNKNOWN: unknownBinding,
  E_PORT_TYPE_INCOMPATIBLE: copy('这两个接口不能直接连接', '上游输出类型与下游需要的输入类型不同。', '定位连接，改接高亮兼容的接口，或添加合适的中间步骤。'),
  E_INPUT_MULTIPLE_EDGES: copy('输入连接过多', '这个输入只接受一路连接。', '定位步骤，保留需要的一路输入并移除其他连接。'),
  E_GRAPH_CYCLE: copy('连接形成了循环', '工作流不能让一个步骤直接或间接连接回自身。', '检查最近的连接并移除回环，让处理从输入流向后续步骤。'),
  E_ORDINAL_REQUIRED: order, E_ORDINAL_DUPLICATE: order, E_ORDINAL_NON_CONTIGUOUS: order, E_ORDINAL_NOT_ALLOWED: order,
  E_PROJECT_STORAGE_CONFLICT: outdated, E_PROJECT_SESSION_CONFLICT: outdated,
  E_PROJECT_SERVICE_HANDOFF_STALE: outdated, E_PROJECT_SERVICE_HANDOFF_NOT_ACTIONABLE: outdated,
  E_PROJECT_SERVICE_RUN_CONFLICT: outdated, E_SERVICE_INPUT_BINDING_CHANGED: outdated,
  E_PROJECT_SERVICE_BUSY: busy, E_PROJECT_SERVICE_RUN_ACTIVE: busy, E_SERVICE_RERUN_ACTIVE: busy,
  E_PROJECT_SERVICE_NO_PROJECT: copy('请先打开工程', '当前没有可以处理的工程。', '返回工程首页，打开已有工程或新建视频工程。'),
  E_SERVICE_GRAPH_INVALID: copy('工作流还不能运行', '运行前的正式检查没有通过，没有开始新的处理。', '打开问题清单，修复标出的设置或连接后再开始。'),
  execution_error: copy('这一步处理失败', '处理工具没有正常完成，本次输出不能当作成功结果。', '定位失败步骤，检查输入和设置；修正后先查看重试影响，再从头重跑该步骤。'),
  validation_failed: copy('处理输出未通过检查', '处理已结束，但输出不满足这个步骤的要求。', '定位步骤检查输出要求，修正设置或输入后，从头重新运行该步骤。'),
  external_submission_invalid: copy('外部输出未通过检查', '文件还不能作为本步骤的有效输出，处理尚未继续。', '定位外部处理步骤，按检查清单修正或替换文件，然后重新完整检查并显式提交。'),
  interrupted: copy('处理被中断', '上次处理在完成前结束，不能接管中间进度。', '定位步骤，确认重试影响后从头重新运行；有效的已完成上游仍可复用。'),
  cancelled: copy('处理已取消', '这一步未完成，不能继续中间进度。', '需要继续时，定位步骤并确认重试影响后从头重新运行。'),
}
export function failurePresentation(code: string): FailurePresentation {
  return (Object.hasOwn(failures, code) ? failures[code] : undefined) ?? { known: false, title: '出现尚未识别的问题',
    cause: '当前无法可靠解释这个错误，不能据此认定操作成功或安全继续。',
    preserved: '原始错误信息已保留。界面不会因此改写运行状态或自动重试。',
    recovery: '展开“高级详情”查看原始信息，定位相关步骤；无法确认原因时，请保留现场并寻求帮助。' }
}
