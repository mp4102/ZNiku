/** 卡片参数值的人类摘要；只压缩展示，不解析业务对象、不修改精确参数或 Schema。 */
import type { ParameterPresentationWire } from './contracts'

function fileName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).at(-1) ?? '已选择'
}

/** 对象/数组不能退回 JSON 长串；完整结构仍由同一 Inspector 表单和诊断展示。 */
export function cardSummaryValue(
  value: unknown,
  presentation: ParameterPresentationWire | undefined,
): string {
  const enumLabel = presentation?.enum_labels.find((entry) => JSON.stringify(entry.value) === JSON.stringify(value))
  if (enumLabel) return enumLabel.label
  if (presentation?.control_hint === 'file_path' || presentation?.control_hint === 'save_file' ||
      presentation?.control_hint === 'directory_path') {
    return typeof value === 'string' ? fileName(value) : '已选择'
  }
  if (presentation?.control_hint === 'file_paths') {
    if (!Array.isArray(value)) return '已选择文件'
    const names = value.flatMap((item) => typeof item === 'string' ? [fileName(item)] : [])
    if (names.length === 0) return '尚未选择'
    return names.length <= 2 ? names.join('、') : `${names.slice(0, 2).join('、')} 等 ${names.length} 个文件`
  }
  if (Array.isArray(value)) return value.length ? `共 ${value.length} 项` : '尚未添加'
  if (value !== null && typeof value === 'object') return `已配置 ${Object.keys(value).length} 项设置`
  if (typeof value === 'boolean') return value ? '已开启' : '已关闭'
  if (value === null || value === undefined) return '未设置'
  if (typeof value === 'string') return value || '未填写'
  return String(value)
}
