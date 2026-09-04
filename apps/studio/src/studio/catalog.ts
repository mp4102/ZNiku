/**
 * 将 Project Service 返回的 exact NodeDefinition 组织为 Studio Palette 展示项。
 *
 * 本模块只做 exact Presentation join、搜索与展示分组，不创建或改写领域定义。缺少 Presentation 的
 * 第三方节点进入通用分组；绝不按 ``type_id`` 猜测分类、名称或业务角色。
 */

import type {
  NodeDefinitionWire,
  NodePresentationWire,
  PresentationCatalogWire,
} from './contracts'

export type StudioCatalogRole = 'source' | 'processor' | 'sink' | 'utility'

export interface StudioCatalogEntry {
  readonly definition: NodeDefinitionWire
  readonly presentation: NodePresentationWire | null
  readonly role: StudioCatalogRole
}

export interface StudioCatalogGroup {
  readonly id: string
  readonly label: string
  readonly description: string
  readonly entries: ReadonlyArray<StudioCatalogEntry>
}

export function catalogRole(definition: NodeDefinitionWire): StudioCatalogRole {
  const hasInputs = definition.input_ports.length > 0
  const hasOutputs = definition.output_ports.length > 0
  if (!hasInputs && hasOutputs) return 'source'
  if (hasInputs && !hasOutputs) return 'sink'
  if (hasInputs && hasOutputs) return 'processor'
  return 'utility'
}

function searchableText(
  definition: NodeDefinitionWire,
  presentation: NodePresentationWire | null,
): string {
  const ports = [...definition.input_ports, ...definition.output_ports]
    .flatMap((port) => [port.port_id, port.data_type, port.cardinality])
    .join(' ')
  const display = presentation
    ? `${presentation.title} ${presentation.description} ${presentation.keywords.join(' ')}`
    : ''
  return `${display} ${definition.type_id} ${definition.version} ${definition.execution_mode} ${definition.executor.kind} ${ports}`.toLowerCase()
}

export function groupStudioDefinitions(
  definitions: ReadonlyArray<NodeDefinitionWire>,
  query: string,
  catalog: PresentationCatalogWire | null = null,
): ReadonlyArray<StudioCatalogGroup> {
  const normalizedQuery = query.trim().toLowerCase()
  const presentations = new Map(
    (catalog?.nodes ?? []).map((item) => [
      `${item.type_id}\u0000${item.definition_version}`,
      item,
    ]),
  )
  const entries = definitions
    .map((definition) => ({
      definition,
      presentation:
        presentations.get(`${definition.type_id}\u0000${definition.version}`) ?? null,
    }))
    .filter(
      ({ definition, presentation }) =>
        normalizedQuery.length === 0 || searchableText(definition, presentation).includes(normalizedQuery),
    )
    .map(({ definition, presentation }): StudioCatalogEntry => ({
      definition,
      presentation,
      role: catalogRole(definition),
    }))

  const categories = [...(catalog?.categories ?? [])].sort(
    (left, right) => left.order - right.order || left.category_id.localeCompare(right.category_id),
  )
  const groups = categories.flatMap((category): StudioCatalogGroup[] => {
    const matching = entries.filter((entry) => entry.presentation?.category_id === category.category_id)
    return matching.length > 0
      ? [{
          id: category.category_id,
          label: category.title,
          description: category.description ?? '',
          entries: matching,
        }]
      : []
  })
  const fallback = entries.filter((entry) => entry.presentation === null)
  if (fallback.length > 0) {
    groups.push({
      id: 'generic-schema',
      label: '通用 Schema 节点',
      description: '缺少 exact Presentation；仍可使用正式 Schema 配置。',
      entries: fallback,
    })
  }
  return groups
}
