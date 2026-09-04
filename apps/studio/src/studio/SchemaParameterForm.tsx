/**
 * 将 NodeDefinition 的 Draft 2020-12 JSON Schema 投影为通用参数表单。
 *
 * 控件只消费 Schema 约束与经过严格 wire 校验的 Presentation 文案；无法可靠解释的局部会明确
 * 回退到“高级 → 原始参数”，不会按节点类型猜测值、条件或媒体业务规则。
 */

import { useMemo, useRef, type ChangeEvent, type ReactNode } from 'react'
import type {
  JsonObject,
  JsonValue,
  NodePresentationWire,
  ParameterPresentationWire,
} from './contracts'
import {
  effectiveRenderSchema,
  canonicalJsonKey,
  directRenderIssue,
  deletePointer,
  errorsForPointer,
  getPointer,
  hasErrorsBelow,
  joinPointer,
  jsonEqual,
  localRenderIssue,
  oneOfOptionLabel,
  seedForSchema,
  selectedOneOfIndex,
  setPointer,
  switchOneOfBranch,
  type ParameterDraftValidation,
  type ParameterFieldError,
  type ParameterSchema,
} from './parameter-draft'

export interface SchemaParameterFormProps {
  readonly schema: ParameterSchema
  readonly draft: JsonObject
  readonly validation: ParameterDraftValidation
  readonly presentation: NodePresentationWire | null
  readonly readOnly?: boolean
  readonly onPickPath?: (
    request: ParameterPickerRequest,
  ) => Promise<ReadonlyArray<string> | null>
  readonly onPickError?: (error: unknown) => void
  readonly onChange: (draft: JsonObject) => void
}

export interface ParameterPickerRequest {
  readonly pointer: string
  readonly kind: 'open_file' | 'open_files' | 'select_directory' | 'save_file'
  readonly label: string
  readonly extensions: ReadonlyArray<string>
}

interface FieldContext {
  readonly root: JsonObject
  readonly errors: ReadonlyArray<ParameterFieldError>
  readonly readOnly: boolean
  readonly presentations: ReadonlyMap<string, ParameterPresentationWire>
  readonly onPickPath?: (
    request: ParameterPickerRequest,
  ) => Promise<ReadonlyArray<string> | null>
  readonly onPickError?: (error: unknown) => void
  readonly beginPicker: () => number
  readonly pickerIsCurrent: (expectedRoot: JsonObject, flight: number) => boolean
  readonly applyPickedValue: (
    expectedRoot: JsonObject,
    pointer: string,
    value: JsonValue,
  ) => void
  readonly onChange: (draft: JsonObject) => void
}

interface FieldProps extends FieldContext {
  readonly pointer: string
  readonly name: string
  readonly schema: ParameterSchema
  readonly required: boolean
}

function valueLabel(value: JsonValue): string {
  if (typeof value === 'string') return value
  return JSON.stringify(value)
}

function fieldLabel(name: string, presentation: ParameterPresentationWire | undefined): string {
  return presentation?.label ?? name
}

function FieldErrors({ errors }: { readonly errors: ReadonlyArray<ParameterFieldError> }) {
  const messages = [...new Set(errors.map((error) => error.message))]
  if (messages.length === 0) return null
  return (
    <div className="parameter-field-errors" role="alert">
      {messages.map((message) => <span key={message}>{message}</span>)}
    </div>
  )
}

function OptionalClear({
  context,
  pointer,
  present,
}: {
  readonly context: FieldContext
  readonly pointer: string
  readonly present: boolean
}) {
  if (!present) return null
  return (
    <button
      className="parameter-clear"
      type="button"
      disabled={context.readOnly}
      onClick={() => context.onChange(deletePointer(context.root, pointer))}
    >
      清除此项
    </button>
  )
}

function FieldHelp({
  schema,
  presentation,
}: {
  readonly schema: ParameterSchema
  readonly presentation: ParameterPresentationWire | undefined
}) {
  const constraints = [
    schema.minimum === undefined ? null : `最小 ${schema.minimum}`,
    schema.maximum === undefined ? null : `最大 ${schema.maximum}`,
    schema.minLength === undefined ? null : `最短 ${schema.minLength} 字符`,
    schema.maxLength === undefined ? null : `最长 ${schema.maxLength} 字符`,
    schema.pattern === undefined ? null : `格式 ${schema.pattern}`,
    presentation?.unit ?? null,
  ].filter((item): item is string => item !== null)
  if (!presentation?.description && !schema.description && constraints.length === 0) return null
  return (
    <small className="parameter-field-help">
      {presentation?.description ?? schema.description}
      {constraints.length > 0 && <span>{constraints.join(' · ')}</span>}
    </small>
  )
}

function updateAtPointer(
  context: FieldContext,
  pointer: string,
  value: JsonValue,
): void {
  context.onChange(setPointer(context.root, pointer, value))
}

async function pickAndApply(
  context: FieldContext,
  expectedRoot: JsonObject,
  flight: number,
  request: ParameterPickerRequest,
  apply: (paths: ReadonlyArray<string>) => void,
): Promise<void> {
  try {
    const paths = await context.onPickPath?.(request)
    if (paths && paths.length > 0 && context.pickerIsCurrent(expectedRoot, flight)) apply(paths)
  } catch (error) {
    if (context.pickerIsCurrent(expectedRoot, flight)) context.onPickError?.(error)
  }
}

function ExplicitDefault({ props, value, label }: {
  readonly props: FieldProps
  readonly value: JsonValue | undefined
  readonly label: string
}) {
  if (value !== undefined && value !== null) return null
  if (!('default' in props.schema) || props.schema.default === undefined) return null
  return (
    <button
      className="button button--ghost parameter-use-default"
      type="button"
      disabled={props.readOnly}
      onClick={() => updateAtPointer(props, props.pointer, props.schema.default!)}
    >
      为 {label} 使用 Schema 默认值：{valueLabel(props.schema.default)}
    </button>
  )
}

function UnsupportedField({ pointer, reason }: { readonly pointer: string; readonly reason: string }) {
  return (
    <div className="parameter-fallback" role="note">
      <strong>{pointer || '/'}</strong>
      <span>{reason}；请在“高级 → 原始参数”编辑此局部。</span>
    </div>
  )
}

function ScalarField(props: FieldProps) {
  const { pointer, name, schema, required, root, errors, readOnly, presentations } = props
  const presentation = presentations.get(pointer)
  const value = getPointer(root, pointer)
  const ownErrors = errorsForPointer(errors, pointer)
  const label = fieldLabel(name, presentation)
  const labelled = `${label}${required ? '（必填）' : ''}`
  const hint = presentation?.control_hint ?? 'auto'

  if ('const' in schema && schema.const !== undefined) {
    return (
      <div className={`parameter-field ${ownErrors.length ? 'has-error' : ''}`}>
        <span className="parameter-field-label">{label}{required && <em>必填</em>}</span>
        <code className="parameter-const">{valueLabel(schema.const)}</code>
        {value !== schema.const && (
          <button
            className="button button--ghost parameter-use-const"
            type="button"
            disabled={readOnly}
            onClick={() => updateAtPointer(props, pointer, schema.const!)}
          >
            使用 Schema 固定值
          </button>
        )}
        {!required && <OptionalClear context={props} pointer={pointer} present={value !== undefined} />}
        <FieldHelp schema={schema} presentation={presentation} />
        <FieldErrors errors={ownErrors} />
      </div>
    )
  }

  if (schema.enum) {
    const selected = schema.enum.findIndex((item) => jsonEqual(item, value))
    const enumLabels = new Map(
      (presentation?.enum_labels ?? []).map((item) => [canonicalJsonKey(item.value), item.label]),
    )
    return (
      <label className={`parameter-field ${ownErrors.length ? 'has-error' : ''}`}>
        <span className="parameter-field-label">{label}{required && <em>必填</em>}</span>
        <select
          aria-label={labelled}
          disabled={readOnly}
          value={selected < 0 ? '' : String(selected)}
          onChange={(event) => {
            const index = Number(event.target.value)
            const next = schema.enum?.[index]
            if (next !== undefined) updateAtPointer(props, pointer, next)
          }}
        >
          <option value="">请选择…</option>
          {schema.enum.map((item, index) => (
            <option key={`${index}-${canonicalJsonKey(item)}`} value={index}>
              {enumLabels.get(canonicalJsonKey(item)) ?? valueLabel(item)}
            </option>
          ))}
        </select>
        <ExplicitDefault props={props} value={value} label={label} />
        {!required && <OptionalClear context={props} pointer={pointer} present={value !== undefined} />}
        <FieldHelp schema={schema} presentation={presentation} />
        <FieldErrors errors={ownErrors} />
      </label>
    )
  }

  if (schema.type === 'boolean') {
    if (value === undefined || value === null) {
      return (
        <label className={`parameter-field ${ownErrors.length ? 'has-error' : ''}`}>
          <span className="parameter-field-label">{label}{required && <em>必填</em>}</span>
          <select
            aria-label={labelled}
            disabled={readOnly}
            value=""
            onChange={(event) => updateAtPointer(props, pointer, event.target.value === 'true')}
          >
            <option value="">请选择…</option>
            <option value="true">是</option>
            <option value="false">否</option>
          </select>
          <ExplicitDefault props={props} value={value} label={label} />
          <FieldHelp schema={schema} presentation={presentation} />
          <FieldErrors errors={ownErrors} />
        </label>
      )
    }
    return (
      <label className={`parameter-field parameter-field--boolean ${ownErrors.length ? 'has-error' : ''}`}>
        <span className="parameter-field-label">{label}{required && <em>必填</em>}</span>
        <input
          aria-label={labelled}
          checked={value === true}
          disabled={readOnly}
          type="checkbox"
          onChange={(event) => updateAtPointer(props, pointer, event.target.checked)}
        />
        {!required && <OptionalClear context={props} pointer={pointer} present />}
        <FieldHelp schema={schema} presentation={presentation} />
        <FieldErrors errors={ownErrors} />
      </label>
    )
  }

  if (schema.type === 'integer' || schema.type === 'number') {
    const numeric = typeof value === 'number' ? String(value) : ''
    const slider =
      hint === 'slider' && schema.minimum !== undefined && schema.maximum !== undefined
    return (
      <label className={`parameter-field ${ownErrors.length ? 'has-error' : ''}`}>
        <span className="parameter-field-label">{label}{required && <em>必填</em>}</span>
        <input
          aria-label={labelled}
          disabled={readOnly}
          max={schema.maximum}
          min={schema.minimum}
          step={schema.type === 'integer' ? 1 : 'any'}
          type={slider ? 'range' : 'number'}
          value={numeric}
          onChange={(event) => {
            if (event.target.value === '') {
              if (!required && value !== undefined) props.onChange(deletePointer(root, pointer))
              return
            }
            const next = Number(event.target.value)
            if (Number.isFinite(next)) updateAtPointer(props, pointer, next)
          }}
        />
        <ExplicitDefault props={props} value={value} label={label} />
        {!required && <OptionalClear context={props} pointer={pointer} present={value !== undefined} />}
        {slider && <output>{numeric || '—'}</output>}
        <FieldHelp schema={schema} presentation={presentation} />
        <FieldErrors errors={ownErrors} />
      </label>
    )
  }

  if (schema.type === 'string') {
    if (value !== undefined && value !== null && typeof value !== 'string') {
      return <UnsupportedField pointer={pointer} reason="当前值不是 Schema 声明的 string" />
    }
    const multiline = hint === 'textarea'
    const pickerKind = hint === 'file_path'
      ? 'open_file'
      : hint === 'directory_path'
        ? 'select_directory'
        : hint === 'save_file'
          ? 'save_file'
          : null
    const common = {
      'aria-label': labelled,
      disabled: readOnly,
      maxLength: schema.maxLength,
      minLength: schema.minLength,
      pattern: schema.pattern,
      placeholder: presentation?.placeholder ?? undefined,
      value: value ?? '',
      onChange: (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
        updateAtPointer(props, pointer, event.target.value),
    }
    return (
      <label className={`parameter-field ${ownErrors.length ? 'has-error' : ''}`}>
        <span className="parameter-field-label">{label}{required && <em>必填</em>}</span>
        {multiline ? <textarea {...common} rows={4} /> : <input {...common} type="text" />}
        {pickerKind && (
          <button
            className="button button--ghost parameter-picker"
            disabled={readOnly || !props.onPickPath}
            onClick={() => {
              const expectedRoot = props.root
              const flight = props.beginPicker()
              void pickAndApply(props, expectedRoot, flight, {
                pointer,
                kind: pickerKind,
                label,
                extensions: presentation?.picker?.extensions ?? [],
              }, (paths) => {
                const selected = paths[0]
                if (selected !== undefined) props.applyPickedValue(expectedRoot, pointer, selected)
              })
            }}
            type="button"
          >
            {pickerKind === 'select_directory' ? '选择文件夹' : pickerKind === 'save_file' ? '选择保存位置' : '选择文件'}
          </button>
        )}
        <ExplicitDefault props={props} value={value} label={label} />
        {!required && <OptionalClear context={props} pointer={pointer} present={value !== undefined} />}
        {pickerKind && <small className="parameter-picker-hint">选择只更新当前未应用设置；Python 仍会在应用和运行时验证路径。</small>}
        <FieldHelp schema={schema} presentation={presentation} />
        <FieldErrors errors={ownErrors} />
      </label>
    )
  }

  return <UnsupportedField pointer={pointer} reason={`不支持的 scalar 类型 ${String(schema.type)}`} />
}

function OneOfField(props: FieldProps) {
  const branches = props.schema.oneOf ?? []
  const value = getPointer(props.root, props.pointer)
  const selected = selectedOneOfIndex(branches, value)
  const presentation = props.presentations.get(props.pointer)
  const ownErrors = errorsForPointer(props.errors, props.pointer)
  if (branches.length === 0) return <UnsupportedField pointer={props.pointer} reason="oneOf 没有选项" />
  return (
    <fieldset className={`parameter-object parameter-one-of ${ownErrors.length ? 'has-error' : ''}`}>
      <legend>{fieldLabel(props.name, presentation)}{props.required && <em>必填</em>}</legend>
      <label className="parameter-branch-selector">
        <span>结构</span>
        <select
          aria-label={`${fieldLabel(props.name, presentation)}结构`}
          disabled={props.readOnly}
          value={selected === null ? '' : String(selected)}
          onChange={(event) => {
            const branch = branches[Number(event.target.value)]
            if (branch) updateAtPointer(props, props.pointer, switchOneOfBranch(branch, value))
          }}
        >
          <option value="">请选择结构…</option>
          {branches.map((branch, index) => (
            <option key={index} value={index}>{oneOfOptionLabel(branch, index)}</option>
          ))}
        </select>
      </label>
      <ExplicitDefault props={props} value={value} label={fieldLabel(props.name, presentation)} />
      {selected !== null && (
        <SchemaField {...props} schema={branches[selected]!} name={props.name} required={props.required} />
      )}
      <FieldHelp schema={props.schema} presentation={presentation} />
      <FieldErrors errors={ownErrors} />
    </fieldset>
  )
}

function ObjectField(props: FieldProps) {
  const rawValue = getPointer(props.root, props.pointer)
  const effective = effectiveRenderSchema(props.schema, rawValue)
  if (rawValue !== undefined && (typeof rawValue !== 'object' || rawValue === null || Array.isArray(rawValue))) {
    return <UnsupportedField pointer={props.pointer} reason="当前值不是 Schema 声明的 object" />
  }
  const presentation = props.presentations.get(props.pointer)
  const required = new Set(effective.required ?? [])
  const properties = Object.entries(effective.properties ?? {})
  const unknownKeys = rawValue && !Array.isArray(rawValue)
    ? Object.keys(rawValue).filter((key) => !Object.hasOwn(effective.properties ?? {}, key))
    : []
  return (
    <fieldset className={`parameter-object ${hasErrorsBelow(props.errors, props.pointer) ? 'has-error' : ''}`}>
      {props.pointer !== '' && <legend>{fieldLabel(props.name, presentation)}{props.required && <em>必填</em>}</legend>}
      {properties.length === 0 ? (
        <span className="parameter-empty-object">此节点没有可配置参数。</span>
      ) : properties.map(([name, schema]) => (
        <SchemaField
          {...props}
          key={name}
          name={name}
          pointer={joinPointer(props.pointer, name)}
          required={required.has(name)}
          schema={schema}
        />
      ))}
      <ExplicitDefault props={props} value={rawValue} label={fieldLabel(props.name, presentation)} />
      {unknownKeys.map((key) => {
        const pointer = joinPointer(props.pointer, key)
        return (
          <div className="parameter-unknown-field" key={key}>
            <strong>{key}</strong>
            <span>此字段未在 Schema properties 中声明。</span>
            <button type="button" disabled={props.readOnly} onClick={() => props.onChange(deletePointer(props.root, pointer))}>删除未知字段</button>
            <FieldErrors errors={errorsForPointer(props.errors, pointer)} />
          </div>
        )
      })}
      {props.pointer !== '' && <FieldHelp schema={props.schema} presentation={presentation} />}
      {props.pointer !== '' && !props.required && (
        <OptionalClear context={props} pointer={props.pointer} present={rawValue !== undefined} />
      )}
      <FieldErrors errors={errorsForPointer(props.errors, props.pointer)} />
    </fieldset>
  )
}

function ArrayField(props: FieldProps) {
  const value = getPointer(props.root, props.pointer)
  if (value !== undefined && !Array.isArray(value)) {
    return <UnsupportedField pointer={props.pointer} reason="当前值不是 Schema 声明的 array" />
  }
  const items = Array.isArray(value) ? value : []
  const presentation = props.presentations.get(props.pointer)
  const multipleFiles = presentation?.control_hint === 'file_paths'
  const prefix = props.schema.prefixItems ?? []
  const homogeneous = typeof props.schema.items === 'object' ? props.schema.items : null
  const maxFromTuple = props.schema.items === false ? prefix.length : Number.POSITIVE_INFINITY
  const maxItems = Math.min(props.schema.maxItems ?? Number.POSITIVE_INFINITY, maxFromTuple)
  const canAdd = items.length < maxItems && (items.length < prefix.length || homogeneous !== null)
  const ownErrors = errorsForPointer(props.errors, props.pointer)
  return (
    <fieldset className={`parameter-array ${hasErrorsBelow(props.errors, props.pointer) ? 'has-error' : ''}`}>
      <legend>{fieldLabel(props.name, presentation)}{props.required && <em>必填</em>}</legend>
      {items.map((_, index) => {
        const itemSchema = prefix[index] ?? homogeneous
        if (!itemSchema) {
          return <UnsupportedField key={index} pointer={joinPointer(props.pointer, index)} reason="Schema 不允许此数组位置" />
        }
        return (
          <div className="parameter-array-item" key={index}>
            <header><strong>项目 {index + 1}</strong><button type="button" disabled={props.readOnly} onClick={() => updateAtPointer(props, props.pointer, items.filter((__, itemIndex) => itemIndex !== index))}>删除</button></header>
            <SchemaField {...props} name={`项目 ${index + 1}`} pointer={joinPointer(props.pointer, index)} required schema={itemSchema} />
          </div>
        )
      })}
      {items.length === 0 && <span className="parameter-empty-array">尚无项目。</span>}
      <button
        className="button button--ghost parameter-array-add"
        type="button"
        disabled={props.readOnly || !canAdd}
        onClick={() => {
          const schema = prefix[items.length] ?? homogeneous
          if (schema) updateAtPointer(props, props.pointer, [...items, seedForSchema(schema)])
        }}
      >
        添加项目
      </button>
      {multipleFiles && (
        <button
          className="button button--ghost parameter-picker"
          disabled={props.readOnly || !props.onPickPath}
          onClick={() => {
            const expectedRoot = props.root
            const flight = props.beginPicker()
            void pickAndApply(props, expectedRoot, flight, {
              pointer: props.pointer,
              kind: 'open_files',
              label: fieldLabel(props.name, presentation),
              extensions: presentation?.picker?.extensions ?? [],
            }, (paths) => {
              props.applyPickedValue(expectedRoot, props.pointer, [...paths])
            })
          }}
          type="button"
        >
          选择多个文件
        </button>
      )}
      <ExplicitDefault props={props} value={value} label={fieldLabel(props.name, presentation)} />
      <FieldHelp schema={props.schema} presentation={presentation} />
      {!props.required && <OptionalClear context={props} pointer={props.pointer} present={value !== undefined} />}
      <FieldErrors errors={ownErrors} />
    </fieldset>
  )
}

function SchemaField(props: FieldProps): ReactNode {
  const issue = localRenderIssue(props.schema)
  if (issue) return <UnsupportedField pointer={props.pointer} reason={issue} />
  if (props.schema.oneOf) return <OneOfField {...props} />
  const value = getPointer(props.root, props.pointer)
  const schema = effectiveRenderSchema(props.schema, value)
  if (schema.type === 'object' || schema.properties) return <ObjectField {...props} schema={schema} />
  if (schema.type === 'array' || schema.items !== undefined || schema.prefixItems) {
    return <ArrayField {...props} schema={schema} />
  }
  return <ScalarField {...props} schema={schema} />
}

interface RootField {
  readonly name: string
  readonly pointer: string
  readonly schema: ParameterSchema
  readonly required: boolean
  readonly presentation: ParameterPresentationWire | undefined
  readonly groupId: string
  readonly groupOrder: number
}

function groupFields(
  schema: ParameterSchema,
  draft: JsonObject,
  presentation: NodePresentationWire | null,
): { readonly primary: RootField[]; readonly advanced: RootField[] } {
  const effective = effectiveRenderSchema(schema, draft)
  const required = new Set(effective.required ?? [])
  const byPointer = new Map(presentation?.parameters.map((item) => [item.parameter_pointer, item]) ?? [])
  const groupOrder = new Map(presentation?.parameter_groups.map((item) => [item.group_id, item.order]) ?? [])
  const fields = Object.entries(effective.properties ?? {}).map(([name, fieldSchema]) => {
    const pointer = joinPointer('', name)
    const fieldPresentation = byPointer.get(pointer)
    return {
      name,
      pointer,
      schema: fieldSchema,
      required: required.has(name),
      presentation: fieldPresentation,
      groupId: fieldPresentation?.group_id ?? 'generic',
      groupOrder: groupOrder.get(fieldPresentation?.group_id ?? '') ?? Number.MAX_SAFE_INTEGER,
    }
  })
  const order = (left: RootField, right: RootField) =>
    left.groupOrder - right.groupOrder ||
    (left.presentation?.order ?? Number.MAX_SAFE_INTEGER) - (right.presentation?.order ?? Number.MAX_SAFE_INTEGER) ||
    left.name.localeCompare(right.name)
  return {
    primary: fields.filter((field) => field.presentation?.importance === 'primary').sort(order),
    advanced: fields.filter((field) => field.presentation?.importance !== 'primary').sort(order),
  }
}

export function SchemaParameterForm({
  schema,
  draft,
  validation,
  presentation,
  readOnly = false,
  onPickPath,
  onPickError,
  onChange,
}: SchemaParameterFormProps) {
  const renderIssue = localRenderIssue(schema)
  const deepRenderIssue = directRenderIssue(schema)
  const presentations = useMemo(
    () => new Map(presentation?.parameters.map((item) => [item.parameter_pointer, item]) ?? []),
    [presentation],
  )
  const grouped = useMemo(() => groupFields(schema, draft, presentation), [draft, presentation, schema])
  const latestDraftRef = useRef(draft)
  const pickerFlightRef = useRef(0)
  latestDraftRef.current = draft
  const rootProperties = effectiveRenderSchema(schema, draft).properties ?? {}
  const unknownRootKeys = Object.keys(draft).filter((key) => !Object.hasOwn(rootProperties, key))
  const context: FieldContext = {
    root: draft,
    errors: validation.errors,
    readOnly,
    presentations,
    onPickPath,
    onPickError,
    beginPicker: () => {
      pickerFlightRef.current += 1
      return pickerFlightRef.current
    },
    pickerIsCurrent: (expectedRoot, flight) =>
      latestDraftRef.current === expectedRoot && pickerFlightRef.current === flight,
    applyPickedValue: (expectedRoot, pointer, value) => {
      // 选择器打开期间若用户已编辑或切换 ParameterDraft，迟到路径不得覆盖较新的 session 状态。
      if (latestDraftRef.current !== expectedRoot) return
      onChange(setPointer(expectedRoot, pointer, value))
    },
    onChange,
  }
  if (validation.compileError) {
    return <UnsupportedField pointer="" reason={`Schema 无法编译：${validation.compileError}`} />
  }
  if (renderIssue) return <UnsupportedField pointer="" reason={renderIssue} />
  const renderFields = (fields: ReadonlyArray<RootField>) => {
    const groups = new Map<string, RootField[]>()
    for (const field of fields) groups.set(field.groupId, [...(groups.get(field.groupId) ?? []), field])
    const definitions = new Map(presentation?.parameter_groups.map((item) => [item.group_id, item]) ?? [])
    return [...groups].map(([groupId, groupFields]) => {
      const group = definitions.get(groupId)
      return (
        <section className="parameter-subgroup" key={groupId}>
          {group && <header><h5>{group.title}</h5>{group.description && <p>{group.description}</p>}</header>}
          {groupFields.map((field) => <SchemaField {...context} key={field.pointer} {...field} />)}
        </section>
      )
    })
  }
  const advancedHasErrors = grouped.advanced.some((field) => hasErrorsBelow(validation.errors, field.pointer))
  return (
    <div className="schema-parameter-form" aria-label="节点参数表单">
      <FieldErrors errors={errorsForPointer(validation.errors, '').filter((error) => error.keyword !== 'unsupported')} />
      {deepRenderIssue && !renderIssue && (
        <div className="parameter-fallback" role="note">
          <strong>部分字段使用原始参数</strong>
          <span>{deepRenderIssue}；受支持字段仍可在此编辑，其余局部请使用“高级 → 原始参数”。</span>
        </div>
      )}
      {grouped.primary.length > 0 && <section className="parameter-group"><h4>主要设置</h4>{renderFields(grouped.primary)}</section>}
      {grouped.advanced.length > 0 && (
        <details className="parameter-advanced" open={advancedHasErrors || presentation === null}>
          <summary>高级设置 <span>{grouped.advanced.length}</span></summary>
          <section className="parameter-group">
            {presentation === null && <p className="parameter-generic-note">此节点没有可用的 Presentation，正使用通用 Schema 表单。</p>}
            {renderFields(grouped.advanced)}
          </section>
        </details>
      )}
      {grouped.primary.length === 0 && grouped.advanced.length === 0 && (
        <span className="parameter-empty-object">此节点没有可配置参数。</span>
      )}
      {unknownRootKeys.map((key) => {
        const pointer = joinPointer('', key)
        return (
          <div className="parameter-unknown-field" key={key}>
            <strong>{key}</strong>
            <span>此字段未在 Schema properties 中声明。</span>
            <button type="button" disabled={readOnly} onClick={() => onChange(deletePointer(draft, pointer))}>删除未知字段</button>
            <FieldErrors errors={errorsForPointer(validation.errors, pointer)} />
          </div>
        )
      })}
    </div>
  )
}
