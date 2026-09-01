/**
 * 将 Project Service 返回的 exact NodeDefinition 组织为 Studio Palette 展示项。
 *
 * 本模块只负责搜索、分组和视觉角色，不创建或改写领域定义。媒体能力、preset 参数、executor 与 validator
 * 始终来自当前 ``.zniku`` Project 的 Python authority；未知 type_id 会保留在工程扩展组，而不是被猜测或拒绝。
 */

import type { NodeDefinitionWire } from './contracts'

export type StudioCatalogGroupId = 'media' | 'video_transform' | 'project'
export type StudioCatalogRole = 'source' | 'processor' | 'sink' | 'utility'

export interface StudioCatalogEntry {
  readonly definition: NodeDefinitionWire
  readonly role: StudioCatalogRole
}

export interface StudioCatalogGroup {
  readonly id: StudioCatalogGroupId
  readonly label: string
  readonly description: string
  readonly entries: ReadonlyArray<StudioCatalogEntry>
}

const GROUP_METADATA: ReadonlyArray<
  Omit<StudioCatalogGroup, 'entries'>
> = [
  {
    id: 'media',
    label: '基础媒体节点',
    description: 'Source、拆分、合并、编码、封装与输出',
  },
  {
    id: 'video_transform',
    label: 'VideoTransform presets',
    description: 'MR、Enhancement、FI 与其他 exact transform definitions',
  },
  {
    id: 'project',
    label: '工程扩展',
    description: '当前 Project 保存的其他 exact definitions',
  },
]

function normalizedTypeId(typeId: string): string {
  return typeId.toLowerCase().replaceAll('-', '_')
}

export function catalogGroupId(definition: NodeDefinitionWire): StudioCatalogGroupId {
  const typeId = normalizedTypeId(definition.type_id)
  if (typeId.startsWith('zniku.media.video_transform.')) return 'video_transform'
  if (typeId.startsWith('zniku.media.')) return 'media'
  return 'project'
}

export function catalogRole(definition: NodeDefinitionWire): StudioCatalogRole {
  if (normalizedTypeId(definition.type_id).startsWith('zniku.media.output_file.')) return 'sink'
  const hasInputs = definition.input_ports.length > 0
  const hasOutputs = definition.output_ports.length > 0
  if (!hasInputs && hasOutputs) return 'source'
  if (hasInputs && !hasOutputs) return 'sink'
  if (hasInputs && hasOutputs) return 'processor'
  return 'utility'
}

function searchableText(definition: NodeDefinitionWire): string {
  const ports = [...definition.input_ports, ...definition.output_ports]
    .flatMap((port) => [port.port_id, port.data_type, port.cardinality])
    .join(' ')
  return `${definition.type_id} ${definition.version} ${definition.execution_mode} ${definition.executor.kind} ${ports}`.toLowerCase()
}

export function groupStudioDefinitions(
  definitions: ReadonlyArray<NodeDefinitionWire>,
  query: string,
): ReadonlyArray<StudioCatalogGroup> {
  const normalizedQuery = query.trim().toLowerCase()
  const entries = definitions
    .filter(
      (definition) => normalizedQuery.length === 0 || searchableText(definition).includes(normalizedQuery),
    )
    .map((definition): StudioCatalogEntry => ({
      definition,
      role: catalogRole(definition),
    }))

  return GROUP_METADATA.flatMap((metadata): StudioCatalogGroup[] => {
    const matching = entries.filter((entry) => catalogGroupId(entry.definition) === metadata.id)
    return matching.length > 0 ? [{ ...metadata, entries: matching }] : []
  })
}
