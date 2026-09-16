import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import corpus from '../../../../docs/architecture/studio-overlap-schema-corpus.json'
import batchCorpus from '../../../../docs/architecture/studio-chapter-batch-schema-corpus.json'
import { SchemaParameterForm } from './SchemaParameterForm'
import { asParameterSchema, directRenderIssue, resolveLocalRenderSchema, validateParameterDraft, type ParameterSchema } from './parameter-draft'
import type { JsonObject } from './contracts'

afterEach(cleanup)
describe('新 Python Schema 展示合同', () => {
  it.each([...corpus.definitions, ...batchCorpus.definitions])('$identity.type_id 全量局部引用和 nullable 可直接展示', (entry) => {
    expect(directRenderIssue(asParameterSchema(entry.parameter_schema as unknown as JsonObject))).toBeNull()
  })
  it.each([
    { $ref: 'https://example.invalid/schema.json' },
    { $defs: { cycle: { $ref: '#/$defs/cycle' } }, $ref: '#/$defs/cycle' },
    { $ref: '#/$defs/missing' },
  ])('远程/递归/悬空引用失败关闭：%j', (schema) => {
    expect(() => resolveLocalRenderSchema(schema as ParameterSchema)).toThrow()
  })
  it('不把任意 anyOf 当作可安全编辑的 nullable', () => {
    expect(directRenderIssue({ anyOf: [{ type: 'string' }, { type: 'integer' }] })).toContain('仅支持')
  })
  it('可空字段不混淆 null、缺失和空字符串，正式验证仍用原始 Schema', () => {
    const schema: ParameterSchema = { type: 'object', additionalProperties: false, $defs: { text: { type: 'string', minLength: 1 } }, properties: { version: { anyOf: [{ $ref: '#/$defs/text' }, { type: 'null' }], default: null } } }
    function Form() {
      const [draft, setDraft] = useState<JsonObject>({ version: null })
      return <><SchemaParameterForm schema={schema} draft={draft} validation={validateParameterDraft(schema, draft)} presentation={null} onChange={setDraft} /><output>{JSON.stringify(draft)}</output></>
    }
    render(<Form />)
    expect(screen.getByText('当前明确未声明 (null)')).toBeVisible()
    fireEvent.change(screen.getByLabelText('version'), { target: { value: 'v1.0' } })
    expect(screen.getByRole('status')).toHaveTextContent('{"version":"v1.0"}')
    fireEvent.click(screen.getByRole('button', { name: '将 version 设为未声明' }))
    expect(screen.getByRole('status')).toHaveTextContent('{"version":null}')
    fireEvent.click(screen.getByRole('button', { name: '清除此项' }))
    expect(screen.getByRole('status')).toHaveTextContent('{}')
  })
  it('引用中的严格数字界限仍由原始 AJV 验证，未知关键字仍保留回退', () => {
    const schema: ParameterSchema = { type: 'object', additionalProperties: false, $defs: { positive: { type: 'integer', exclusiveMinimum: 0, exclusiveMaximum: 3 } }, properties: { n: { $ref: '#/$defs/positive' } }, required: ['n'] }
    expect(validateParameterDraft(schema, { n: 0 }).valid).toBe(false)
    expect(validateParameterDraft(schema, { n: 3 }).valid).toBe(false)
    expect(validateParameterDraft(schema, { n: 1 }).valid).toBe(true)
    expect(directRenderIssue({ ...schema, futureKeyword: true } as ParameterSchema)).toContain('futureKeyword')
  })
  it('数组分页保留全部参数，错误可以导航到第 999 行', () => {
    const schema: ParameterSchema = { type: 'object', additionalProperties: false, properties: { cuts: { type: 'array', items: { type: 'integer' } } } }
    const draft = { cuts: Array.from({ length: 999 }, (_, index) => index) }
    const { rerender } = render(<SchemaParameterForm schema={schema} draft={draft} validation={{ valid: true, errors: [], compileError: null }} presentation={null} onChange={() => {}} />)
    expect(screen.getAllByRole('spinbutton')).toHaveLength(20)
    rerender(<SchemaParameterForm schema={schema} draft={draft} validation={{ valid: false, errors: [{ pointer: '/cuts/998', keyword: 'type', message: '合成行错误' }], compileError: null }} presentation={null} onChange={() => {}} />)
    expect(screen.getByLabelText('项目 999（必填）')).toBeVisible()
    expect(draft.cuts).toHaveLength(999)
  })
})
