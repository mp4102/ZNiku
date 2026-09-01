import { describe, expect, it } from 'vitest'
import type { NodeDefinitionWire } from './contracts'
import { catalogGroupId, catalogRole, groupStudioDefinitions } from './catalog'
import { sourceDefinition, transformDefinition } from './test-fixtures'

function mediaDefinition(
  typeId: string,
  template: NodeDefinitionWire = transformDefinition,
): NodeDefinitionWire {
  return { ...template, type_id: typeId }
}

describe('Studio NodeDefinition catalog projection', () => {
  it('只按 Python exact type_id 分组，不复制 preset 参数或 executor', () => {
    const source = mediaDefinition('zniku.media.source_media', sourceDefinition)
    const split = mediaDefinition('zniku.media.split_video')
    const mr = mediaDefinition('zniku.media.video_transform.mr.manual_external')
    const enhancement = mediaDefinition('zniku.media.video-transform.enhancement.manual-external')

    expect(catalogGroupId(source)).toBe('media')
    expect(catalogGroupId(split)).toBe('media')
    expect(catalogGroupId(mr)).toBe('video_transform')
    expect(catalogGroupId(enhancement)).toBe('video_transform')
    expect(catalogGroupId(transformDefinition)).toBe('project')

    const grouped = groupStudioDefinitions(
      [source, split, mr, enhancement, transformDefinition],
      '',
    )
    expect(grouped.map((group) => [group.id, group.entries.length])).toEqual([
      ['media', 2],
      ['video_transform', 2],
      ['project', 1],
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
    expect(groupStudioDefinitions([source, fi], 'VideoFile')).toHaveLength(2)
    expect(groupStudioDefinitions([source, fi], 'not-present')).toEqual([])
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
    expect(
      catalogRole({ ...transformDefinition, type_id: 'zniku.media.output_file.video' }),
    ).toBe('sink')
  })
})
