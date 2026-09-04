/**
 * 提供 Schema 参数表单使用的纯数据操作与 Draft 2020-12 验证投影。
 *
 * 本模块只解释 NodeDefinition 已声明的 JSON Schema，不按 ``type_id`` 推测业务规则，
 * 也不把前端校验提升为 Graph 或 Runtime authority。非法值保留在 session draft 中，
 * 直到用户修正；只有完整 Schema 验证通过的 object 才能交给正式“应用设置”。
 */

import Ajv2020, { type ErrorObject, type ValidateFunction } from 'ajv/dist/2020.js'
import type { JsonObject, JsonValue } from './contracts'

export interface ParameterSchema {
  readonly $schema?: string
  readonly type?: 'object' | 'array' | 'string' | 'integer' | 'number' | 'boolean'
  readonly title?: string
  readonly description?: string
  readonly properties?: Readonly<Record<string, ParameterSchema>>
  readonly required?: ReadonlyArray<string>
  readonly additionalProperties?: boolean | ParameterSchema
  readonly enum?: ReadonlyArray<JsonValue>
  readonly const?: JsonValue
  readonly default?: JsonValue
  readonly minimum?: number
  readonly maximum?: number
  readonly minLength?: number
  readonly maxLength?: number
  readonly pattern?: string
  readonly items?: ParameterSchema | boolean
  readonly prefixItems?: ReadonlyArray<ParameterSchema>
  readonly minItems?: number
  readonly maxItems?: number
  readonly uniqueItems?: boolean
  readonly allOf?: ReadonlyArray<ParameterSchema>
  readonly oneOf?: ReadonlyArray<ParameterSchema>
  readonly not?: ParameterSchema
  readonly if?: ParameterSchema
  readonly then?: ParameterSchema
  readonly else?: ParameterSchema
}

export interface ParameterFieldError {
  readonly pointer: string
  readonly keyword: string
  readonly message: string
}

export interface ParameterDraftValidation {
  readonly valid: boolean
  readonly errors: ReadonlyArray<ParameterFieldError>
  readonly compileError: string | null
}

const validatorCache = new WeakMap<object, ValidateFunction | Error>()
const ajv = new Ajv2020({
  allErrors: true,
  strict: false,
  validateFormats: false,
})

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function ownValue(object: JsonObject, key: string): JsonValue | undefined {
  return Object.hasOwn(object, key) ? object[key] : undefined
}

function setOwnValue(object: JsonObject, key: string, value: JsonValue): void {
  Object.defineProperty(object, key, {
    value,
    enumerable: true,
    configurable: true,
    writable: true,
  })
}

export function asParameterSchema(value: JsonObject): ParameterSchema {
  return value as unknown as ParameterSchema
}

export function cloneJson<T extends JsonValue>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

/**
 * 为 JSON 值生成与 object key 插入顺序无关的稳定键。
 *
 * Presentation 的 enum value 允许复合 JSON；表单必须按 JSON 深相等匹配，不能依赖
 * React render 期间的对象引用，也不能让同一对象的键顺序影响 label 绑定。
 */
export function canonicalJsonKey(value: JsonValue): string {
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJsonKey(item)).join(',')}]`
  }
  if (isRecord(value)) {
    const members = Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJsonKey(value[key] as JsonValue)}`)
    return `{${members.join(',')}}`
  }
  return JSON.stringify(value)
}

export function jsonEqual(left: JsonValue | undefined, right: JsonValue | undefined): boolean {
  if (left === undefined || right === undefined) return left === right
  return canonicalJsonKey(left) === canonicalJsonKey(right)
}

export function escapePointerPart(value: string): string {
  return value.replaceAll('~', '~0').replaceAll('/', '~1')
}

export function unescapePointerPart(value: string): string {
  if (/~(?:[^01]|$)/.test(value)) throw new Error(`无效 RFC 6901 escape：${value}`)
  return value.replaceAll('~1', '/').replaceAll('~0', '~')
}

export function joinPointer(parent: string, part: string | number): string {
  return `${parent}/${escapePointerPart(String(part))}`
}

export function splitPointer(pointer: string): string[] {
  if (pointer === '') return []
  if (!pointer.startsWith('/')) throw new Error(`无效 RFC 6901 pointer：${pointer}`)
  return pointer.slice(1).split('/').map(unescapePointerPart)
}

export function getPointer(root: JsonValue, pointer: string): JsonValue | undefined {
  let current: JsonValue | undefined = root
  for (const part of splitPointer(pointer)) {
    if (Array.isArray(current)) {
      if (!/^0$|^[1-9][0-9]*$/.test(part)) return undefined
      current = current[Number(part)]
    } else if (isRecord(current)) {
      current = ownValue(current as JsonObject, part)
    } else {
      return undefined
    }
  }
  return current
}

function mutableClone(value: JsonValue): JsonValue {
  return cloneJson(value)
}

/** 以不可变方式更新一个 JSON Pointer；中间容器只按下一段是否为数组下标建立。 */
export function setPointer(root: JsonObject, pointer: string, value: JsonValue): JsonObject {
  const parts = splitPointer(pointer)
  if (parts.length === 0) {
    if (!isRecord(value)) throw new Error('参数根必须是 JSON object')
    return cloneJson(value as JsonObject)
  }
  const result = mutableClone(root) as JsonObject
  let current: JsonObject | JsonValue[] = result
  parts.forEach((part, index) => {
    const last = index === parts.length - 1
    if (last) {
      if (Array.isArray(current)) {
        if (!/^0$|^[1-9][0-9]*$/.test(part) || Number(part) >= current.length) {
          throw new Error(`数组 pointer 超出范围：${pointer}`)
        }
        current[Number(part)] = cloneJson(value)
      }
      else setOwnValue(current, part, cloneJson(value))
      return
    }
    const nextPart = parts[index + 1]!
    if (Array.isArray(current) && (!/^0$|^[1-9][0-9]*$/.test(part) || Number(part) >= current.length)) {
      throw new Error(`数组 pointer 超出范围：${pointer}`)
    }
    const existing = Array.isArray(current) ? current[Number(part)] : ownValue(current, part)
    const next = Array.isArray(existing)
      ? cloneJson(existing)
      : isRecord(existing)
        ? cloneJson(existing as JsonObject)
        : /^0$|^[1-9][0-9]*$/.test(nextPart)
          ? []
          : {}
    if (Array.isArray(current)) current[Number(part)] = next
    else setOwnValue(current, part, next)
    current = next as JsonObject | JsonValue[]
  })
  return result
}

/** 删除一个 JSON Pointer，不把删除解释成 null，也不会修改调用者持有的 draft。 */
export function deletePointer(root: JsonObject, pointer: string): JsonObject {
  const parts = splitPointer(pointer)
  if (parts.length === 0) return {}
  const result = mutableClone(root) as JsonObject
  let current: JsonObject | JsonValue[] = result
  for (let index = 0; index < parts.length - 1; index += 1) {
    const part = parts[index]!
    if (Array.isArray(current) && (!/^0$|^[1-9][0-9]*$/.test(part) || Number(part) >= current.length)) {
      throw new Error(`数组 pointer 超出范围：${pointer}`)
    }
    const next = Array.isArray(current) ? current[Number(part)] : ownValue(current, part)
    if (!Array.isArray(next) && !isRecord(next)) return result
    current = next as JsonObject | JsonValue[]
  }
  const leaf = parts.at(-1)!
  if (Array.isArray(current)) {
    if (!/^0$|^[1-9][0-9]*$/.test(leaf) || Number(leaf) >= current.length) {
      throw new Error(`数组 pointer 超出范围：${pointer}`)
    }
    current.splice(Number(leaf), 1)
  }
  else delete current[leaf]
  return result
}

const supportedKeywords = new Set([
  '$schema',
  'type',
  'title',
  'description',
  'properties',
  'required',
  'additionalProperties',
  'enum',
  'const',
  'default',
  'minimum',
  'maximum',
  'minLength',
  'maxLength',
  'pattern',
  'items',
  'prefixItems',
  'minItems',
  'maxItems',
  'uniqueItems',
  'allOf',
  'oneOf',
  'not',
  'if',
  'then',
  'else',
])

/**
 * 判断当前局部是否属于 Phase 0 corpus 冻结的可视化子集。
 * 未知 keyword 即使 AJV 在兼容模式下能够忽略，也必须显式回退，避免表单伪装完整。
 */
export function localRenderIssue(
  schema: ParameterSchema,
  requireClosedObject = true,
): string | null {
  const unknown = Object.keys(schema).filter((key) => !supportedKeywords.has(key))
  if (unknown.length > 0) {
    return `包含尚未冻结的 Schema keyword：${unknown.join(', ')}`
  }
  const objectLike = schema.type === 'object' || schema.properties !== undefined
  if (objectLike && requireClosedObject && schema.additionalProperties !== false) {
    return 'object 未声明 additionalProperties: false'
  }
  if (schema.items === true) return 'items: true 缺少可安全生成控件的项目 Schema'
  if (Array.isArray(schema.type)) return '多类型 Schema 不在 Phase 1 corpus 范围内'
  if (
    requireClosedObject &&
    schema.type === undefined &&
    schema.enum === undefined &&
    !('const' in schema) &&
    schema.oneOf === undefined &&
    schema.properties === undefined &&
    schema.items === undefined &&
    schema.prefixItems === undefined
  ) {
    return 'Schema 未提供可安全渲染的字段类型'
  }
  return null
}

function renderIssueAt(
  schema: ParameterSchema,
  location: string,
  requireClosedObject: boolean,
): string | null {
  const local = localRenderIssue(schema, requireClosedObject)
  if (local) return `${location} ${local}`

  const children: Array<readonly [ParameterSchema, string, boolean]> = []
  for (const [key, child] of Object.entries(schema.properties ?? {})) {
    children.push([child, `${location}/properties/${key}`, true])
  }
  if (typeof schema.items === 'object') children.push([schema.items, `${location}/items`, true])
  schema.prefixItems?.forEach((child, index) => children.push([child, `${location}/prefixItems/${index}`, true]))
  schema.oneOf?.forEach((child, index) => children.push([child, `${location}/oneOf/${index}`, true]))
  // 条件和组合分支只增加断言；最终可编辑 object 仍由其所在字段的闭合 Schema 负责。
  schema.allOf?.forEach((child, index) => children.push([child, `${location}/allOf/${index}`, false]))
  if (schema.not) children.push([schema.not, `${location}/not`, false])
  if (schema.if) children.push([schema.if, `${location}/if`, false])
  if (schema.then) children.push([schema.then, `${location}/then`, false])
  if (schema.else) children.push([schema.else, `${location}/else`, false])
  for (const [child, childLocation, childRequiresClosed] of children) {
    const issue = renderIssueAt(child, childLocation, childRequiresClosed)
    if (issue) return issue
  }
  return null
}

export function directRenderIssue(schema: ParameterSchema): string | null {
  return renderIssueAt(schema, '/', true)
}

function validatorFor(schema: ParameterSchema): ValidateFunction | Error {
  const key = schema as object
  const cached = validatorCache.get(key)
  if (cached) return cached
  try {
    const validator = ajv.compile(schema)
    validatorCache.set(key, validator)
    return validator
  } catch (error) {
    const failure = error instanceof Error ? error : new Error('未知 JSON Schema compile 错误')
    validatorCache.set(key, failure)
    return failure
  }
}

function errorPointer(error: ErrorObject): string {
  if (error.keyword === 'required') {
    const missing = (error.params as { readonly missingProperty?: unknown }).missingProperty
    return typeof missing === 'string' ? joinPointer(error.instancePath, missing) : error.instancePath
  }
  if (error.keyword === 'additionalProperties') {
    const extra = (error.params as { readonly additionalProperty?: unknown }).additionalProperty
    return typeof extra === 'string' ? joinPointer(error.instancePath, extra) : error.instancePath
  }
  return error.instancePath
}

function friendlyError(error: ErrorObject): string {
  switch (error.keyword) {
    case 'required':
      return '此项为必填项。'
    case 'additionalProperties':
      return '包含 Schema 未声明的字段。请删除后再应用。'
    case 'type':
      return `值类型必须是 ${String((error.params as { readonly type?: unknown }).type ?? '声明类型')}。`
    case 'enum':
      return '请选择 Schema 提供的选项。'
    case 'const':
      return '值必须与 Schema 声明的固定值一致。'
    case 'minimum':
      return `值不得小于 ${String((error.params as { readonly limit?: unknown }).limit)}。`
    case 'maximum':
      return `值不得大于 ${String((error.params as { readonly limit?: unknown }).limit)}。`
    case 'minLength':
      return `长度不得少于 ${String((error.params as { readonly limit?: unknown }).limit)}。`
    case 'maxLength':
      return `长度不得超过 ${String((error.params as { readonly limit?: unknown }).limit)}。`
    case 'pattern':
      return '格式不符合 Schema 声明的规则。'
    case 'minItems':
      return `至少需要 ${String((error.params as { readonly limit?: unknown }).limit)} 项。`
    case 'maxItems':
      return `最多允许 ${String((error.params as { readonly limit?: unknown }).limit)} 项。`
    case 'uniqueItems':
      return '数组项目必须唯一。'
    case 'oneOf':
      return '值必须且只能匹配一个可选结构。'
    case 'not':
      return '该字段组合被 Schema 明确禁止。'
    default:
      return error.message ? `Schema 校验失败：${error.message}` : `Schema 校验失败：${error.keyword}`
  }
}

export function validateParameterDraft(
  schema: ParameterSchema,
  draft: JsonObject,
): ParameterDraftValidation {
  const validator = validatorFor(schema)
  if (validator instanceof Error) {
    return { valid: false, errors: [], compileError: validator.message }
  }
  const valid = validator(draft)
  const errors = (validator.errors ?? []).map((error) => ({
    pointer: errorPointer(error),
    keyword: error.keyword,
    message: friendlyError(error),
  }))
  return { valid: valid === true, errors, compileError: null }
}

export function schemaMatches(schema: ParameterSchema, value: JsonValue | undefined): boolean {
  const validator = validatorFor(schema)
  return !(validator instanceof Error) && validator(value) === true
}

function mergeProperties(
  left: Readonly<Record<string, ParameterSchema>> | undefined,
  right: Readonly<Record<string, ParameterSchema>> | undefined,
): Readonly<Record<string, ParameterSchema>> | undefined {
  if (!left) return right
  if (!right) return left
  const result: Record<string, ParameterSchema> = { ...left }
  for (const [key, schema] of Object.entries(right)) {
    result[key] = result[key] ? mergeRenderSchema(result[key]!, schema) : schema
  }
  return result
}

/**
 * 合并只供渲染使用的约束视图。正式合法性仍由 AJV 对原 Schema 判断。
 * corpus 中 allOf 只叠加属性/required/条件，不需要实现一般 JSON Schema 归约器。
 */
export function mergeRenderSchema(left: ParameterSchema, right: ParameterSchema): ParameterSchema {
  return {
    ...left,
    ...right,
    properties: mergeProperties(left.properties, right.properties),
    required: [...new Set([...(left.required ?? []), ...(right.required ?? [])])],
    allOf: [...(left.allOf ?? []), ...(right.allOf ?? [])],
  }
}

function conditionalContribution(schema: ParameterSchema, value: JsonValue | undefined): ParameterSchema {
  let result: ParameterSchema = { ...schema, allOf: undefined, if: undefined, then: undefined, else: undefined }
  if (schema.if) {
    const branch = schemaMatches(schema.if, value) ? schema.then : schema.else
    if (branch) result = mergeRenderSchema(result, effectiveRenderSchema(branch, value))
  }
  for (const item of schema.allOf ?? []) {
    result = mergeRenderSchema(result, conditionalContribution(item, value))
  }
  return result
}

export function effectiveRenderSchema(
  schema: ParameterSchema,
  value: JsonValue | undefined,
): ParameterSchema {
  return conditionalContribution(schema, value)
}

/** 只从明示 const/default 构造值；容器脚手架仅在用户显式新增项目时使用。 */
export function seedForSchema(schema: ParameterSchema): JsonValue {
  if ('const' in schema && schema.const !== undefined) return cloneJson(schema.const)
  if ('default' in schema && schema.default !== undefined) return cloneJson(schema.default)
  // oneOf 没有“第一个就是默认”的语义；必须等待用户显式选择分支。
  if (schema.oneOf?.length) return null
  if (schema.type === 'object' || schema.properties) {
    return Object.fromEntries(
      Object.entries(schema.properties ?? {}).flatMap(([key, child]) => {
        if ('const' in child && child.const !== undefined) return [[key, cloneJson(child.const)]]
        if ('default' in child && child.default !== undefined) return [[key, cloneJson(child.default)]]
        return []
      }),
    )
  }
  if (schema.type === 'array') return []
  // null 是尚未填写的 session draft 占位，会继续被 Schema 判为非法；它不冒充业务默认。
  return null
}

export function oneOfOptionLabel(schema: ParameterSchema, index: number): string {
  for (const [property, child] of Object.entries(schema.properties ?? {})) {
    if ('const' in child) return `${property}：${String(child.const)}`
  }
  if ('const' in schema) return String(schema.const)
  return schema.title ?? `选项 ${index + 1}`
}

export function selectedOneOfIndex(
  branches: ReadonlyArray<ParameterSchema>,
  value: JsonValue | undefined,
): number | null {
  const exact = branches.flatMap((branch, index) => (schemaMatches(branch, value) ? [index] : []))
  if (exact.length === 1) return exact[0]!
  if (isRecord(value)) {
    const structural = branches.flatMap((branch, index) => {
      const match = Object.entries(branch.properties ?? {}).some(
        ([key, child]) => 'const' in child && value[key] === child.const,
      )
      return match ? [index] : []
    })
    if (structural.length === 1) return structural[0]!
  }
  return null
}

/** 切换 oneOf 时只保留目标分支声明的字段，防止上一分支字段泄漏。 */
export function switchOneOfBranch(
  branch: ParameterSchema,
  current: JsonValue | undefined,
): JsonValue {
  if (branch.type !== 'object' && !branch.properties) return seedForSchema(branch)
  const source = isRecord(current) ? current : {}
  const seeded = seedForSchema(branch)
  const allowed = new Set(Object.keys(branch.properties ?? {}))
  const fixed = new Set(
    Object.entries(branch.properties ?? {})
      .filter(([, schema]) => 'const' in schema)
      .map(([key]) => key),
  )
  const retained = Object.fromEntries(
    Object.entries(source).filter(([key]) => allowed.has(key) && !fixed.has(key)),
  ) as JsonObject
  return { ...retained, ...(seeded as JsonObject) }
}

export function errorsForPointer(
  errors: ReadonlyArray<ParameterFieldError>,
  pointer: string,
): ReadonlyArray<ParameterFieldError> {
  return errors.filter((error) => error.pointer === pointer)
}

export function hasErrorsBelow(
  errors: ReadonlyArray<ParameterFieldError>,
  pointer: string,
): boolean {
  const prefix = pointer === '' ? '/' : `${pointer}/`
  return errors.some((error) => error.pointer === pointer || error.pointer.startsWith(prefix))
}
