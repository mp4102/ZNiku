import { describe, expect, it } from 'vitest'
import type { NodeDefinitionWire, NodePresentationWire, PresentationCatalogWire } from './contracts'
import { catalogRole, groupStudioDefinitions } from './catalog'
import { sourceDefinition, transformDefinition } from './test-fixtures'

function mediaDefinition(
  typeId: string,
  template: NodeDefinitionWire = transformDefinition,
): NodeDefinitionWire {
  return { ...template, type_id: typeId }
}

describe('Studio NodeDefinition catalog projection', () => {
  const nodePresentation = (definition: NodeDefinitionWire, categoryId: string, title: string): NodePresentationWire => ({
    type_id: definition.type_id,
    definition_version: definition.version,
    title,
    description: `${title}说明`,
    category_id: categoryId,
    icon_token: 'transform',
    palette_level: 'primary',
    keywords: ['画质'],
    parameter_groups: [], parameters: [], ports: [], card_summary_paths: [],
  })

  it('只按 exact Presentation category 分组，不按 type_id 猜测或复制执行合同', () => {
    const source = mediaDefinition('zniku.media.source_media', sourceDefinition)
    const split = mediaDefinition('zniku.media.split_video')
    const mr = mediaDefinition('zniku.media.video_transform.mr.manual_external')
    const enhancement = mediaDefinition('zniku.media.video-transform.enhancement.manual-external')

    const catalog: PresentationCatalogWire = {
      contract_version: '0.3.0', locale: 'zh-CN',
      categories: [
        { category_id: 'transform', title: '处理', description: null, order: 2 },
        { category_id: 'source', title: '输入', description: null, order: 1 },
      ],
      nodes: [
        nodePresentation(source, 'source', '视频输入'),
        nodePresentation(mr, 'transform', '马赛克修复'),
        nodePresentation(enhancement, 'transform', '画质增强'),
      ],
    }

    const grouped = groupStudioDefinitions(
      [source, split, mr, enhancement, transformDefinition],
      '',
      catalog,
    )
    expect(grouped.map((group) => [group.id, group.entries.length])).toEqual([
      ['source', 1],
      ['transform', 2],
      ['generic-schema', 2],
    ])
    expect(grouped[1]?.entries[0]?.definition).toBe(mr)
    expect(grouped[1]?.entries[0]?.definition.executor).toBe(mr.executor)
    expect(grouped[1]?.entries[0]?.definition.parameter_schema).toBe(mr.parameter_schema)
  })

  it('搜索覆盖 exact id、execution mode、executor 和 typed ports', () => {
    const source = mediaDefinition('zniku.media.source_media', sourceDefinition)
    const fi = mediaDefinition('zniku.media.video_transform.fi.manual_external')

    expect(groupStudioDefinitions([source, fi], '.fi.').flatMap((group) => group.entries)).toEqual([
      expect.objectContaining({ definition: fi }),
    ])
    expect(groupStudioDefinitions([source, fi], 'manual_external')).toHaveLength(1)
    expect(groupStudioDefinitions([source, fi], 'VideoFile').flatMap((group) => group.entries)).toHaveLength(2)
    expect(groupStudioDefinitions([source, fi], 'not-present')).toEqual([])
  })

  it('搜索 Presentation 标题、说明和关键词，错误版本不会近似匹配', () => {
    const catalog: PresentationCatalogWire = {
      contract_version: '0.3.0', locale: 'zh-CN',
      categories: [{ category_id: 'source', title: '输入', description: null, order: 1 }],
      nodes: [nodePresentation(sourceDefinition, 'source', '导入视频')],
    }
    expect(groupStudioDefinitions([sourceDefinition], '导入视频', catalog)[0]?.entries).toHaveLength(1)
    expect(groupStudioDefinitions([sourceDefinition], '画质', catalog)[0]?.entries).toHaveLength(1)
    const wrongVersion = { ...catalog, nodes: [{ ...catalog.nodes[0]!, definition_version: '9.9.9' }] }
    expect(groupStudioDefinitions([sourceDefinition], '', wrongVersion)[0]?.id).toBe('generic-schema')
  })

  it('由端口形状推导视觉角色，未知零端口 definition 仍可显示', () => {
    expect(catalogRole(sourceDefinition)).toBe('source')
    expect(catalogRole(transformDefinition)).toBe('processor')
    expect(
      catalogRole({ ...transformDefinition, input_ports: [], output_ports: [] }),
    ).toBe('utility')
    expect(
      catalogRole({ ...transformDefinition, output_ports: [] }),
    ).toBe('sink')
    // type_id 不参与视觉角色推测；有输入和 published output 的定义仍是 processor。
    expect(
      catalogRole({ ...transformDefinition, type_id: 'zniku.media.output_file.video' }),
    ).toBe('processor')
  })
})
