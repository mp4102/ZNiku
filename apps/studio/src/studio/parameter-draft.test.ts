/** 验证 ParameterDraft 的类型保真、条件约束与失败关闭边界。 */

import { describe, expect, it } from 'vitest'
import type { JsonObject } from './contracts'
import {
  deletePointer,
  directRenderIssue,
  effectiveRenderSchema,
  getPointer,
  seedForSchema,
  selectedOneOfIndex,
  setPointer,
  splitPointer,
  switchOneOfBranch,
  validateParameterDraft,
  type ParameterSchema,
} from './parameter-draft'

const fullSchema: ParameterSchema = {
  $schema: 'https://json-schema.org/draft/2020-12/schema',
  type: 'object',
  additionalProperties: false,
  properties: {
    mode: { type: 'string', enum: ['simple', 'precise'], default: 'simple' },
    width: { type: 'integer', minimum: 2, maximum: 16_384 },
    ratio: { type: 'number', minimum: 0, maximum: 1 },
    label: { type: 'string', minLength: 2, maxLength: 8, pattern: '^[A-Z]+$' },
    enabled: { type: 'boolean' },
    tags: {
      type: 'array',
      items: { type: 'string', minLength: 1 },
      minItems: 1,
      maxItems: 3,
      uniqueItems: true,
    },
    precise_value: { type: 'integer', minimum: 1 },
    forbidden: { type: 'string' },
  },
  required: ['mode', 'width', 'ratio', 'label', 'enabled', 'tags'],
  allOf: [
    {
      if: { properties: { mode: { const: 'precise' } }, required: ['mode'] },
      then: { required: ['precise_value'] },
      else: { not: { required: ['precise_value'] } },
    },
    { not: { required: ['forbidden'] } },
  ],
}

describe('ParameterDraft Schema authority', () => {
  it('保持 object/string/integer/number/boolean/array 类型并接受完整合法值', () => {
    const draft: JsonObject = {
      mode: 'precise',
      width: 3840,
      ratio: 0.75,
      label: 'MASTER',
      enabled: false,
      tags: ['A', 'B'],
      precise_value: 2,
    }
    expect(validateParameterDraft(fullSchema, draft)).toEqual({
      valid: true,
      errors: [],
      compileError: null,
    })
    expect(JSON.parse(JSON.stringify(draft))).toEqual(draft)
  })

  it('integer 不接受小数或空字符串，范围、pattern 与 deep uniqueItems 失败关闭', () => {
    const invalid: JsonObject = {
      mode: 'simple',
      width: 1.5,
      ratio: 2,
      label: 'bad-value',
      enabled: true,
      tags: ['A', 'A'],
    }
    const result = validateParameterDraft(fullSchema, invalid)
    expect(result.valid).toBe(false)
    expect(result.errors.map((error) => [error.pointer, error.keyword])).toEqual(expect.arrayContaining([
      ['/width', 'type'],
      ['/ratio', 'maximum'],
      ['/label', 'maxLength'],
      ['/label', 'pattern'],
      ['/tags', 'uniqueItems'],
    ]))

    const empty = { ...invalid, width: '' }
    expect(validateParameterDraft(fullSchema, empty).errors).toEqual(
      expect.arrayContaining([expect.objectContaining({ pointer: '/width', keyword: 'type' })]),
    )
  })

  it('合并多个 allOf required，并按 if/then/else 与 not 切换', () => {
    const precise = effectiveRenderSchema(fullSchema, { mode: 'precise' })
    expect(precise.required).toContain('precise_value')
    const simple = effectiveRenderSchema(fullSchema, { mode: 'simple' })
    expect(simple.required).not.toContain('precise_value')

    const illegal: JsonObject = {
      mode: 'simple', width: 2, ratio: 0.5, label: 'OK', enabled: false,
      tags: ['A'], precise_value: 1, forbidden: 'x',
    }
    const keywords = validateParameterDraft(fullSchema, illegal).errors.map((error) => error.keyword)
    expect(keywords.filter((keyword) => keyword === 'not')).toHaveLength(2)
  })

  it('拒绝嵌套未知字段并把 additionalProperties 错误定位到具体 key', () => {
    const schema: ParameterSchema = {
      type: 'object',
      additionalProperties: false,
      properties: {
        nested: {
          type: 'object',
          additionalProperties: false,
          properties: { known: { type: 'string' } },
        },
      },
    }
    const result = validateParameterDraft(schema, { nested: { known: 'ok', surprise: 1 } })
    expect(result.errors).toContainEqual(expect.objectContaining({
      pointer: '/nested/surprise',
      keyword: 'additionalProperties',
    }))
  })

  it('验证 items:false 固定 prefixItems tuple 的长度、const 和位置', () => {
    const schema: ParameterSchema = {
      type: 'object',
      additionalProperties: false,
      required: ['segments'],
      properties: {
        segments: {
          type: 'array',
          minItems: 2,
          maxItems: 2,
          items: false,
          prefixItems: [
            { type: 'object', additionalProperties: false, required: ['port'], properties: { port: { const: 'A' } } },
            { type: 'object', additionalProperties: false, required: ['port'], properties: { port: { const: 'B' } } },
          ],
        },
      },
    }
    expect(validateParameterDraft(schema, { segments: [{ port: 'A' }, { port: 'B' }] }).valid).toBe(true)
    expect(validateParameterDraft(schema, { segments: [{ port: 'A' }] }).valid).toBe(false)
    expect(validateParameterDraft(schema, { segments: [{ port: 'A' }, { port: 'B' }, { port: 'C' }] }).valid).toBe(false)
    expect(validateParameterDraft(schema, { segments: [{ port: 'B' }, { port: 'A' }] }).errors)
      .toEqual(expect.arrayContaining([expect.objectContaining({ keyword: 'const' })]))
  })

  it('oneOf 分支必须由用户显式选择，切换时清除旧字段且目标 const 胜出', () => {
    const branches: ParameterSchema[] = [
      { type: 'object', additionalProperties: false, properties: { mode: { const: 'frames' }, frames: { type: 'array', items: { type: 'integer' } } }, required: ['mode', 'frames'] },
      { type: 'object', additionalProperties: false, properties: { mode: { const: 'times' }, times: { type: 'array', items: { type: 'string' } } }, required: ['mode', 'times'] },
    ]
    expect(seedForSchema({ oneOf: branches })).toBeNull()
    expect(selectedOneOfIndex(branches, { mode: 'frames', frames: [899] })).toBe(0)
    const switched = switchOneOfBranch(branches[1]!, { mode: 'frames', frames: [899] })
    expect(switched).toEqual({ mode: 'times' })
    expect(switched).not.toHaveProperty('frames')
  })

  it('RFC 6901 round-trip 支持转义，并拒绝非法 escape 与数组索引', () => {
    const root: JsonObject = { nested: { 'a/b~c': [1, 2] } }
    const pointer = '/nested/a~1b~0c/1'
    expect(splitPointer(pointer)).toEqual(['nested', 'a/b~c', '1'])
    expect(getPointer(root, pointer)).toBe(2)
    expect(setPointer(root, pointer, 9)).toEqual({ nested: { 'a/b~c': [1, 9] } })
    expect(deletePointer(root, pointer)).toEqual({ nested: { 'a/b~c': [1] } })
    expect(() => splitPointer('/bad~2escape')).toThrow(/RFC 6901/)
    expect(() => setPointer(root, '/nested/a~1b~0c/nope', 9)).toThrow(/数组 pointer/)
    expect(() => deletePointer(root, '/nested/a~1b~0c/9')).toThrow(/数组 pointer/)
  })

  it('RFC 6901 object member 只访问 own property，并安全处理 __proto__ 等特殊 key', () => {
    expect(getPointer({}, '/toString')).toBeUndefined()
    const special = JSON.parse('{"__proto__":{"value":1},"toString":"own"}') as JsonObject
    expect(getPointer(special, '/__proto__/value')).toBe(1)
    expect(getPointer(special, '/toString')).toBe('own')

    const updated = setPointer({}, '/__proto__/polluted', true)
    expect(Object.hasOwn(updated, '__proto__')).toBe(true)
    expect(getPointer(updated, '/__proto__/polluted')).toBe(true)
    expect(Object.getPrototypeOf(updated)).toBe(Object.prototype)
    expect(({} as Record<string, unknown>).polluted).toBeUndefined()
    const cleaned = deletePointer(updated, '/__proto__/polluted')
    expect(Object.hasOwn(cleaned, '__proto__')).toBe(true)
    expect(getPointer(cleaned, '/__proto__')).toEqual({})
  })

  it('未知 keyword、动态 additionalProperties 和无类型 scalar 明确回退', () => {
    expect(directRenderIssue({ type: 'string', format: 'custom' } as ParameterSchema)).toMatch(/format/)
    expect(directRenderIssue({ type: 'object', additionalProperties: true })).toMatch(/additionalProperties/)
    expect(directRenderIssue({ description: '没有类型' })).toMatch(/字段类型/)
    expect(directRenderIssue({
      type: 'object',
      additionalProperties: false,
      properties: { nested: { type: 'string', oneOf: [{ type: 'string', contentMediaType: 'x-test' } as ParameterSchema] } },
    })).toMatch(/contentMediaType/)
  })

  it('renderer 不支持的正式 Draft 2020-12 keyword 只触发 raw fallback，不改写 AJV 合法性', () => {
    const anyOfSchema = {
      type: 'object',
      additionalProperties: false,
      properties: { choice: { anyOf: [{ type: 'string' }, { type: 'integer' }] } },
      required: ['choice'],
    } as ParameterSchema
    expect(directRenderIssue(anyOfSchema)).toMatch(/anyOf/)
    expect(validateParameterDraft(anyOfSchema, { choice: 7 }).valid).toBe(true)
    expect(validateParameterDraft(anyOfSchema, { choice: false }).valid).toBe(false)
  })
})
