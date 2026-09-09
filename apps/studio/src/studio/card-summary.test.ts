/** 摘要只改变显示密度，不泄漏对象 JSON，也不篡改精确数值与文件选择。 */
import { describe, expect, it } from 'vitest'
import type { ParameterPresentationWire } from './contracts'
import { cardSummaryValue } from './card-summary'

function presentation(overrides: Partial<ParameterPresentationWire> = {}): ParameterPresentationWire {
  return { parameter_pointer: '/value', label: '参数', description: null, group_id: 'basic', order: 0,
    importance: 'primary', control_hint: 'auto', unit: null, placeholder: null, enum_labels: [], picker: null,
    ...overrides }
}

describe('卡片人类摘要', () => {
  it('数组和嵌套对象只报配置数量，不泄漏对象键、值、路径或 JSON', () => {
    const parameters = { nested: { private: 'do-not-show' }, files: ['synthetic-secret'] }
    expect(cardSummaryValue(parameters, undefined)).toBe('已配置 2 项设置')
    expect(cardSummaryValue([parameters, parameters], undefined)).toBe('共 2 项')
    expect(cardSummaryValue([], undefined)).toBe('尚未添加')
    expect(parameters).toEqual({ nested: { private: 'do-not-show' }, files: ['synthetic-secret'] })
  })
  it('优先使用 Python Presentation 的枚举人类名称，包括显式对象枚举', () => {
    expect(cardSummaryValue({ mode: 'one' }, presentation({ enum_labels: [{ value: { mode: 'one' }, label: '标准方案' }] }))).toBe('标准方案')
  })
  it('单个文件/目录只显示名称，多文件有界但保留实际文件数量', () => {
    for (const hint of ['file_path', 'save_file', 'directory_path'] as const) {
      expect(cardSummaryValue('X:\\synthetic\\Example.mov', presentation({ control_hint: hint }))).toBe('Example.mov')
      expect(cardSummaryValue('/synthetic/Example.mov', presentation({ control_hint: hint }))).toBe('Example.mov')
    }
    expect(cardSummaryValue(['X:/synthetic/A.mov', '/synthetic/B.mov', '/synthetic/C.mov'], presentation({ control_hint: 'file_paths' }))).toBe('A.mov、B.mov 等 3 个文件')
  })
  it('不近似精确帧率/数字；布尔和空值使用人类文案', () => {
    expect(cardSummaryValue('60000/1001', undefined)).toBe('60000/1001')
    expect(cardSummaryValue(899, undefined)).toBe('899')
    expect(cardSummaryValue(true, undefined)).toBe('已开启')
    expect(cardSummaryValue(false, undefined)).toBe('已关闭')
    expect(cardSummaryValue(null, undefined)).toBe('未设置')
    expect(cardSummaryValue(undefined, undefined)).toBe('未设置')
    expect(cardSummaryValue('', undefined)).toBe('未填写')
  })
})
