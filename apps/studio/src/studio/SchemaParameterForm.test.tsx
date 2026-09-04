/** 验证通用 Schema 表单只投影正式 Schema，并保持 ParameterDraft 类型与分支语义。 */

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { JsonObject, NodePresentationWire } from './contracts'
import { validateParameterDraft, type ParameterSchema } from './parameter-draft'
import { SchemaParameterForm } from './SchemaParameterForm'

afterEach(cleanup)

const presentation: NodePresentationWire = {
  type_id: 'test.form',
  definition_version: '0.3.0',
  title: '测试表单',
  description: '纯 Schema 表单测试。',
  category_id: 'test',
  icon_token: 'transform',
  palette_level: 'primary',
  keywords: [],
  parameter_groups: [
    { group_id: 'quality', title: '画质', description: '主要画质设置', order: 1 },
    { group_id: 'metadata', title: '元数据', description: null, order: 2 },
  ],
  parameters: [
    { parameter_pointer: '/enabled', label: '启用', description: null, group_id: 'quality', order: 2, importance: 'primary', control_hint: 'switch', unit: null, placeholder: null, enum_labels: [], picker: null },
    { parameter_pointer: '/strength', label: '强度', description: null, group_id: 'quality', order: 1, importance: 'primary', control_hint: 'integer', unit: null, placeholder: null, enum_labels: [], picker: null },
    { parameter_pointer: '/mode', label: '模式', description: null, group_id: 'metadata', order: 1, importance: 'advanced', control_hint: 'select', unit: null, placeholder: null, enum_labels: [{ value: 'a', label: '模式 A' }, { value: 'b', label: '模式 B' }], picker: null },
  ],
  ports: [],
  card_summary_paths: [],
}

const schema: ParameterSchema = {
  type: 'object',
  additionalProperties: false,
  properties: {
    enabled: { type: 'boolean' },
    strength: { type: 'integer', minimum: 1, maximum: 9 },
    mode: { type: 'string', enum: ['a', 'b'] },
  },
  required: ['strength'],
}

function Form({ draft, onChange }: { readonly draft: JsonObject; readonly onChange: (value: JsonObject) => void }) {
  return <SchemaParameterForm schema={schema} draft={draft} validation={validateParameterDraft(schema, draft)} presentation={presentation} onChange={onChange} />
}

describe('SchemaParameterForm', () => {
  it('按 group/order/importance 展示，并让缺失 boolean 显式选择 false', () => {
    const onChange = vi.fn()
    render(<Form draft={{ strength: 3 }} onChange={onChange} />)

    const primary = screen.getByRole('heading', { name: '主要设置' }).closest('section')!
    expect(within(primary).getByRole('heading', { name: '画质' })).toBeInTheDocument()
    const labels = [...primary.querySelectorAll('.parameter-field-label')].map((item) => item.textContent)
    expect(labels).toEqual(['强度必填', '启用'])

    fireEvent.change(screen.getByRole('combobox', { name: '启用' }), { target: { value: 'false' } })
    expect(onChange).toHaveBeenLastCalledWith({ strength: 3, enabled: false })
  })

  it('integer 输入保持 number，清空 optional number 恢复 absent', () => {
    const optionalSchema: ParameterSchema = {
      type: 'object', additionalProperties: false,
      properties: { count: { type: 'integer' } },
    }
    const first = vi.fn()
    const { rerender } = render(
      <SchemaParameterForm schema={optionalSchema} draft={{}} validation={validateParameterDraft(optionalSchema, {})} presentation={null} onChange={first} />,
    )
    fireEvent.change(screen.getByRole('spinbutton', { name: 'count' }), { target: { value: '7' } })
    expect(first).toHaveBeenLastCalledWith({ count: 7 })

    const second = vi.fn()
    rerender(<SchemaParameterForm schema={optionalSchema} draft={{ count: 7 }} validation={validateParameterDraft(optionalSchema, { count: 7 })} presentation={null} onChange={second} />)
    fireEvent.change(screen.getByRole('spinbutton', { name: 'count' }), { target: { value: '' } })
    expect(second).toHaveBeenLastCalledWith({})
  })

  it('oneOf 切换不会泄漏旧分支字段，optional 分支可清除恢复 else/not 合法', () => {
    const branchSchema: ParameterSchema = {
      type: 'object',
      additionalProperties: false,
      properties: {
        source_mode: { type: 'string', enum: ['program', 'pre_chaptered'] },
        chapter_selector: {
          oneOf: [
            { type: 'object', additionalProperties: false, properties: { mode: { const: 'exact_frames' }, frames: { type: 'array', items: { type: 'integer' }, minItems: 1 } }, required: ['mode', 'frames'] },
            { type: 'object', additionalProperties: false, properties: { mode: { const: 'exact_times' }, times: { type: 'array', items: { type: 'string' }, minItems: 1 } }, required: ['mode', 'times'] },
          ],
        },
      },
      required: ['source_mode'],
      allOf: [{
        if: { properties: { source_mode: { const: 'program' } }, required: ['source_mode'] },
        then: { required: ['chapter_selector'] },
        else: { not: { required: ['chapter_selector'] } },
      }],
    }
    const first = vi.fn()
    const initial: JsonObject = { source_mode: 'program', chapter_selector: { mode: 'exact_frames', frames: [899] } }
    const { rerender } = render(<SchemaParameterForm schema={branchSchema} draft={initial} validation={validateParameterDraft(branchSchema, initial)} presentation={null} onChange={first} />)
    fireEvent.change(screen.getByRole('combobox', { name: 'chapter_selector结构' }), { target: { value: '1' } })
    expect(first).toHaveBeenLastCalledWith({ source_mode: 'program', chapter_selector: { mode: 'exact_times' } })

    const second = vi.fn()
    const preChaptered: JsonObject = { source_mode: 'pre_chaptered', chapter_selector: { mode: 'exact_times', times: ['00:30:00'] } }
    rerender(<SchemaParameterForm schema={branchSchema} draft={preChaptered} validation={validateParameterDraft(branchSchema, preChaptered)} presentation={null} onChange={second} />)
    expect(screen.getByText('该字段组合被 Schema 明确禁止。')).toBeVisible()
    const selectorGroup = screen.getByRole('combobox', { name: 'chapter_selector结构' }).closest('fieldset')!
    fireEvent.click(within(selectorGroup).getByRole('button', { name: '清除此项' }))
    expect(second).toHaveBeenLastCalledWith({ source_mode: 'pre_chaptered' })
    expect(validateParameterDraft(branchSchema, { source_mode: 'pre_chaptered' }).valid).toBe(true)
  })

  it('unknown property 显示 key 和删除入口，unknown keyword 整表明确回退', () => {
    const onChange = vi.fn()
    render(<SchemaParameterForm schema={schema} draft={{ strength: 3, surprise: true }} validation={validateParameterDraft(schema, { strength: 3, surprise: true })} presentation={presentation} onChange={onChange} />)
    expect(screen.getByText('surprise')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '删除未知字段' }))
    expect(onChange).toHaveBeenLastCalledWith({ strength: 3 })

    const unsupported = { ...schema, dependentSchemas: {} } as ParameterSchema
    const { unmount } = render(<SchemaParameterForm schema={unsupported} draft={{ strength: 3 }} validation={validateParameterDraft(unsupported, { strength: 3 })} presentation={presentation} onChange={vi.fn()} />)
    expect(screen.getByText(/dependentSchemas/)).toBeVisible()
    unmount()
  })

  it('嵌套不支持 keyword 只回退该局部，安全 sibling 与 raw JSON 路径仍可用', () => {
    const mixedSchema = {
      type: 'object', additionalProperties: false,
      properties: {
        safe: { type: 'string' },
        unsupported: { type: 'string', contentMediaType: 'x-test' },
      },
    } as ParameterSchema
    const onChange = vi.fn()
    const validation = validateParameterDraft(mixedSchema, { safe: 'before', unsupported: 'opaque' })
    render(<SchemaParameterForm schema={mixedSchema} draft={{ safe: 'before', unsupported: 'opaque' }} validation={validation} presentation={null} onChange={onChange} />)

    expect(screen.getAllByText(/contentMediaType/)).toHaveLength(2)
    fireEvent.change(screen.getByRole('textbox', { name: 'safe' }), { target: { value: 'after' } })
    expect(onChange).toHaveBeenLastCalledWith({ safe: 'after', unsupported: 'opaque' })
    expect(validation.valid).toBe(true)
  })

  it('invalid advanced 字段自动展开并显示字段级原因', () => {
    render(<Form draft={{ strength: 3, mode: 'unknown' }} onChange={vi.fn()} />)
    const details = screen.getByText(/高级设置/).closest('details')!
    expect(details).toHaveAttribute('open')
    expect(within(details).getByText('请选择 Schema 提供的选项。')).toBeVisible()
  })

  it('homogeneous string array 新增项以未填写值呈现，并可直接输入字符串', () => {
    const arraySchema: ParameterSchema = {
      type: 'object',
      additionalProperties: false,
      properties: {
        times: { type: 'array', items: { type: 'string' }, minItems: 1 },
      },
      required: ['times'],
    }
    const first = vi.fn()
    const { rerender } = render(
      <SchemaParameterForm schema={arraySchema} draft={{ times: [] }} validation={validateParameterDraft(arraySchema, { times: [] })} presentation={null} onChange={first} />,
    )
    fireEvent.click(screen.getByRole('button', { name: '添加项目' }))
    expect(first).toHaveBeenLastCalledWith({ times: [null] })

    const second = vi.fn()
    const pending: JsonObject = { times: [null] }
    rerender(<SchemaParameterForm schema={arraySchema} draft={pending} validation={validateParameterDraft(arraySchema, pending)} presentation={null} onChange={second} />)
    fireEvent.change(screen.getByRole('textbox', { name: '项目 1（必填）' }), { target: { value: '00:30:00' } })
    expect(second).toHaveBeenLastCalledWith({ times: ['00:30:00'] })
  })

  it('复合 enum 使用 JSON 深相等绑定选择与 Presentation label', () => {
    const enumSchema: ParameterSchema = {
      type: 'object', additionalProperties: false,
      properties: {
        choice: { enum: [{ alpha: 1, beta: [2] }, { alpha: 2, beta: [3] }] },
      },
      required: ['choice'],
    }
    const enumPresentation: NodePresentationWire = {
      ...presentation,
      parameters: [{
        parameter_pointer: '/choice', label: '复合选项', description: null,
        group_id: 'quality', order: 1, importance: 'primary', control_hint: 'select',
        unit: null, placeholder: null,
        enum_labels: [{ value: { beta: [2], alpha: 1 }, label: '首选配置' }], picker: null,
      }],
    }
    const onChange = vi.fn()
    render(<SchemaParameterForm schema={enumSchema} draft={{ choice: { beta: [2], alpha: 1 } }} validation={validateParameterDraft(enumSchema, { choice: { beta: [2], alpha: 1 } })} presentation={enumPresentation} onChange={onChange} />)
    const select = screen.getByRole('combobox', { name: '复合选项（必填）' })
    expect(select).toHaveValue('0')
    expect(within(select).getByRole('option', { name: '首选配置' })).toBeInTheDocument()
    fireEvent.change(select, { target: { value: '1' } })
    expect(onChange).toHaveBeenLastCalledWith({ choice: { alpha: 2, beta: [3] } })
  })

  it('default 只在用户显式采用后进入 ParameterDraft，并保留 0/false 类型', () => {
    const defaultSchema: ParameterSchema = {
      type: 'object', additionalProperties: false,
      properties: {
        count: { type: 'integer', default: 0 },
        enabled: { type: 'boolean', default: false },
      },
      required: ['count', 'enabled'],
    }
    const first = vi.fn()
    const { rerender } = render(<SchemaParameterForm schema={defaultSchema} draft={{}} validation={validateParameterDraft(defaultSchema, {})} presentation={null} onChange={first} />)
    expect(first).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '为 count 使用 Schema 默认值：0' }))
    expect(first).toHaveBeenLastCalledWith({ count: 0 })

    const second = vi.fn()
    rerender(<SchemaParameterForm schema={defaultSchema} draft={{ count: 0 }} validation={validateParameterDraft(defaultSchema, { count: 0 })} presentation={null} onChange={second} />)
    fireEvent.click(screen.getByRole('button', { name: '为 enabled 使用 Schema 默认值：false' }))
    expect(second).toHaveBeenLastCalledWith({ count: 0, enabled: false })
  })
})
