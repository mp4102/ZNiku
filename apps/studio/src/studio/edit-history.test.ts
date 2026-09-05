import { describe, expect, it } from 'vitest'
import { EditHistory } from './edit-history'

interface EditingValue {
  readonly nodes: ReadonlyArray<{ readonly id: string; readonly x: number; readonly quality: number }>
  readonly edges: ReadonlyArray<{ readonly source: string; readonly target: string; readonly ordinal: number }>
  readonly names: Readonly<Record<string, string>>
}

const empty: EditingValue = { nodes: [], edges: [], names: {} }
const equal = (left: EditingValue, right: EditingValue) => JSON.stringify(left) === JSON.stringify(right)

describe('编辑历史', () => {
  it('节点、边、参数、顺序和别名随同一个不可变快照完整撤销及重做', () => {
    const history = new EditHistory(empty, { equals: equal })
    const edits: ReadonlyArray<[string, EditingValue]> = [
      ['添加节点', { ...empty, nodes: [{ id: 'a', x: 0, quality: 18 }] }],
      ['复制节点', {
        ...empty, nodes: [{ id: 'a', x: 0, quality: 18 }, { id: 'b', x: 300, quality: 18 }],
        names: { a: '导入视频', b: '导入视频 副本' },
      }],
      ['连接节点', {
        ...empty, nodes: [{ id: 'a', x: 0, quality: 18 }, { id: 'b', x: 300, quality: 18 }],
        edges: [{ source: 'a', target: 'b', ordinal: 0 }], names: { a: '素材', b: '成片' },
      }],
      ['应用设置', {
        ...empty, nodes: [{ id: 'a', x: 0, quality: 18 }, { id: 'b', x: 300, quality: 20 }],
        edges: [{ source: 'a', target: 'b', ordinal: 0 }], names: { a: '素材', b: '成片' },
      }],
      ['重排输入', {
        ...empty, nodes: [{ id: 'a', x: 0, quality: 18 }, { id: 'b', x: 300, quality: 20 }],
        edges: [{ source: 'a', target: 'b', ordinal: 1 }], names: { a: '素材', b: '成片' },
      }],
      ['删除节点', empty],
    ]
    for (const [label, value] of edits) expect(history.commit(value, label)).toBe(true)
    for (let index = edits.length - 1; index >= 0; index -= 1) {
      expect(history.getSnapshot().undoLabel).toBe(edits[index][0])
      expect(history.undo()).toEqual(index === 0 ? empty : edits[index - 1][1])
    }
    expect(history.getSnapshot().canUndo).toBe(false)
    for (const [label, value] of edits) {
      expect(history.getSnapshot().redoLabel).toBe(label)
      expect(history.redo()).toEqual(value)
    }
    expect(history.getSnapshot().canRedo).toBe(false)
  })

  it('新编辑清空 redo；无变化编辑保留 redo', () => {
    const history = new EditHistory(empty, { equals: equal })
    const added = { ...empty, nodes: [{ id: 'a', x: 0, quality: 18 }] }
    history.commit(added)
    history.undo()
    expect(history.commit({ nodes: [], edges: [], names: {} })).toBe(false)
    expect(history.getSnapshot().canRedo).toBe(true)
    history.commit({ ...added, names: { a: '另一份素材' } })
    expect(history.getSnapshot().canRedo).toBe(false)
    expect(history.redo()).toEqual({ ...added, names: { a: '另一份素材' } })
  })

  it('多次拖动只在结束时建立一个动作，拖回起点不记历史', () => {
    const initial = { ...empty, nodes: [{ id: 'a', x: 0, quality: 18 }] }
    const history = new EditHistory(initial, { equals: equal })
    history.begin('移动节点')
    for (const x of [10, 20, 50]) history.update({ ...initial, nodes: [{ ...initial.nodes[0], x }] })
    expect(history.getSnapshot()).toMatchObject({ canUndo: false, transactionActive: true })
    expect(history.end()).toBe(true)
    expect(history.undo()).toEqual(initial)
    expect(history.redo().nodes[0].x).toBe(50)
    history.begin()
    history.update({ ...initial, nodes: [{ ...initial.nodes[0], x: 200 }] })
    history.update({ ...initial, nodes: [{ ...initial.nodes[0], x: 50 }] })
    expect(history.end()).toBe(false)
    expect(history.undo()).toEqual(initial)
  })

  it('取消手势恢复开始值及既有 redo，不产生隐形动作', () => {
    const history = new EditHistory(0)
    history.commit(1)
    history.undo()
    history.begin()
    history.update(5)
    expect(history.cancel()).toBe(0)
    expect(history.getSnapshot()).toMatchObject({ canUndo: false, canRedo: true, transactionActive: false })
    expect(history.redo()).toBe(1)
  })

  it('批量宏只有一条记录，updater 失败不污染历史', () => {
    const history = new EditHistory(empty, { equals: equal })
    history.apply('为所有分段添加处理', (value) => ({
      ...value,
      nodes: ['a', 'b', 'c'].map((id, index) => ({ id, x: index * 300, quality: 18 })),
      names: { a: '分段 A', b: '分段 B', c: '分段 C' },
    }))
    expect(history.value.nodes).toHaveLength(3)
    expect(() => history.apply('失败宏', () => { throw new Error('没有修改') })).toThrow('没有修改')
    expect(history.undo()).toEqual(empty)
    expect(history.getSnapshot().canUndo).toBe(false)
    expect(history.redo().nodes).toHaveLength(3)
  })

  it('历史有界，reset 清除旧工程和进行中的拖动', () => {
    const history = new EditHistory(0, { limit: 2 })
    history.commit(1)
    history.commit(2)
    history.commit(3)
    expect(history.undo()).toBe(2)
    expect(history.undo()).toBe(1)
    expect(history.undo()).toBe(1)
    history.begin()
    history.update(10)
    history.reset(100)
    expect(history.getSnapshot()).toEqual({
      value: 100, canUndo: false, canRedo: false, undoLabel: null, redoLabel: null, transactionActive: false,
    })
    expect(history.end()).toBe(false)
    expect(history.undo()).toBe(100)
  })

  it('拒绝重叠手势与非法容量，不把连续预览误记作动作', () => {
    for (const limit of [0, -1, 1.5, Infinity]) expect(() => new EditHistory(0, { limit })).toThrow()
    const history = new EditHistory(0)
    expect(() => history.update(1)).toThrow('尚未开始')
    history.begin()
    expect(() => history.begin()).toThrow('先结束')
    expect(() => history.commit(2)).toThrow('先结束')
    expect(() => history.undo()).toThrow('先结束')
    expect(() => history.redo()).toThrow('先结束')
    expect(history.cancel()).toBe(0)
  })
})
