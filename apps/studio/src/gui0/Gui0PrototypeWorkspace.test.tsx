import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import { App } from '../App'
import { initialDesignerEdges, initialDesignerNodes } from '../mock-data'
import type { WorkflowEdge, WorkflowNode } from '../model'
import { isGui0ConnectionValid, validateGui0Graph } from './Gui0PrototypeWorkspace'

afterEach(cleanup)

async function openGui0(): Promise<ReturnType<typeof userEvent.setup>> {
  const user = userEvent.setup()
  render(<App />)
  await user.click(screen.getByRole('button', { name: 'GUI-0 Prototype' }))
  return user
}

describe('ZNIKU Studio GUI-0 Prototype workspace', () => {
  it('从正式工作区显式进入和返回，不发生静默 mock 回退', async () => {
    const user = await openGui0()

    expect(screen.getAllByText('MOCK · NO MEDIA I/O').length).toBeGreaterThan(0)
    expect(screen.getByText('自由编排 · drag · typed connect · delete')).toBeInTheDocument()
    expect(screen.getByRole('generic', { name: 'GUI-0 mock Registry' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '返回正式工作区' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Python Authoring authority 不可用')
    expect(screen.queryByText('自由编排 · drag · typed connect · delete')).not.toBeInTheDocument()
  })

  it('从 mock Registry 添加并显式删除 Draft 节点', async () => {
    const user = await openGui0()
    const registry = screen.getByRole('generic', { name: 'GUI-0 mock Registry' })

    await user.click(within(registry).getByRole('button', { name: /Decensoring/ }))

    expect(screen.getByText('New GUI-0 draft node')).toBeInTheDocument()
    expect(screen.getByText('mock.decensoring')).toBeInTheDocument()
    expect(screen.getAllByText('chapter_video · chapter · one')).toHaveLength(2)

    await user.click(screen.getByRole('button', { name: '删除所选节点' }))
    expect(screen.queryByText('New GUI-0 draft node')).not.toBeInTheDocument()
  })

  it('编译、冻结并推进浏览器内 mock Run Monitor', async () => {
    const user = await openGui0()

    await user.click(screen.getByRole('button', { name: '编译预览' }))
    expect(screen.getByText('只读 mock compile snapshot · 14 nodes · 15 edges')).toBeInTheDocument()
    expect(screen.getByText('模拟编译完成')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '审阅并冻结' }))
    expect(screen.getByRole('dialog', { name: '冻结 WorkflowRevision？' })).toBeInTheDocument()
    expect(screen.getByText('14 nodes · 15 edges')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '冻结并开始模拟运行' }))

    expect(screen.getByText('mock Run Monitor · step 1/4')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '推进模拟状态' }))
    expect(screen.getByText('mock Run Monitor · step 2/4')).toBeInTheDocument()
  })

  it('typed handle 只接受精确 type、scope、cardinality 且拒绝占用输入', () => {
    expect(
      isGui0ConnectionValid(
        {
          source: 'source',
          sourceHandle: 'program',
          target: 'demux',
          targetHandle: 'program',
        },
        initialDesignerNodes,
        [],
      ),
    ).toBe(true)

    expect(
      isGui0ConnectionValid(
        {
          source: 'demux',
          sourceHandle: 'audio',
          target: 'partition',
          targetHandle: 'in',
        },
        initialDesignerNodes,
        [],
      ),
    ).toBe(false)

    expect(
      isGui0ConnectionValid(
        {
          source: 'source',
          sourceHandle: 'program',
          target: 'demux',
          targetHandle: 'program',
        },
        initialDesignerNodes,
        initialDesignerEdges,
      ),
    ).toBe(false)

    expect(
      isGui0ConnectionValid(
        {
          source: 'partition',
          sourceHandle: 'out',
          target: 'enhance',
          targetHandle: 'in',
        },
        initialDesignerNodes,
        initialDesignerEdges,
      ),
    ).toBe(false)

    const source = initialDesignerNodes.find((node) => node.id === 'source') as WorkflowNode
    const demux = initialDesignerNodes.find((node) => node.id === 'demux') as WorkflowNode
    const optionalTarget: WorkflowNode = {
      ...demux,
      id: 'optional-target',
      data: {
        ...demux.data,
        inputs: demux.data.inputs.map((port) => ({ ...port, cardinality: 'optional' })),
      },
    }
    expect(
      isGui0ConnectionValid(
        {
          source: source.id,
          sourceHandle: 'program',
          target: optionalTarget.id,
          targetHandle: 'program',
        },
        [source, optionalTarget],
        [],
      ),
    ).toBe(true)
  })

  it('自由编辑后的缺失输入失败关闭，不能冻结陈旧预览', async () => {
    const user = await openGui0()
    const registry = screen.getByRole('generic', { name: 'GUI-0 mock Registry' })
    await user.click(within(registry).getByRole('button', { name: /Decensoring/ }))

    await user.click(screen.getByRole('button', { name: '编译预览' }))
    expect(screen.getByRole('alert')).toHaveTextContent('必需输入 draft-decensoring-1.in 尚未连接')
    expect(screen.getByRole('button', { name: '审阅并冻结' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: '删除所选节点' }))
    await user.click(screen.getByRole('button', { name: '编译预览' }))
    expect(screen.getByText('只读 mock compile snapshot · 14 nodes · 15 edges')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Designer' }))
    fireEvent.click(screen.getByLabelText('Mux 节点'))
    fireEvent.keyDown(window, { key: 'Delete' })
    expect(screen.queryByLabelText('Mux 节点')).not.toBeInTheDocument()
    expect(screen.getByText('在画布中选择一个节点或连接查看属性。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Compile Preview' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '审阅并冻结' })).toBeDisabled()
  })

  it('最小 mock compile gate 拒绝 cycle', () => {
    const incomingEnhance = initialDesignerEdges.find(
      (edge) => edge.source === 'partition' && edge.target === 'enhance',
    ) as WorkflowEdge
    const cycleEdge: WorkflowEdge = {
      ...incomingEnhance,
      id: 'edge.mock.cycle',
      source: 'collect',
      sourceHandle: 'out',
      target: 'enhance',
      targetHandle: 'in',
    }
    const cyclicEdges = [
      ...initialDesignerEdges.filter((edge) => edge.id !== incomingEnhance.id),
      cycleEdge,
    ]
    expect(validateGui0Graph(initialDesignerNodes, cyclicEdges)).toBe(
      'GUI-0 Draft 必须保持有向无环。',
    )
  })

  it('选择连接后 Delete 删除 edge 并让预览失败关闭', async () => {
    const user = await openGui0()
    const finalEdge = initialDesignerEdges.find(
      (edge) => edge.source === 'mux' && edge.target === 'final',
    ) as WorkflowEdge
    await user.selectOptions(screen.getByLabelText('GUI-0 connection list'), finalEdge.id)
    expect(screen.getByRole('heading', { name: 'Data edge' })).toBeInTheDocument()

    fireEvent.keyDown(window, { key: 'Delete' })
    expect(screen.getByLabelText('GUI-0 connection list')).not.toHaveValue(finalEdge.id)
    fireEvent.click(screen.getByRole('button', { name: '编译预览' }))
    expect(screen.getByRole('alert')).toHaveTextContent('必需输入 final.program 尚未连接')
  })

  it('Inspector 展示多 typed ports，画布节点可被选择', async () => {
    await openGui0()
    fireEvent.click(screen.getByLabelText('Mux 节点'))

    expect(screen.getByText('engine.mux')).toBeInTheDocument()
    expect(screen.getByText('encoded_video · program · one')).toBeInTheDocument()
    expect(screen.getByText('audio_artifact_set · program · set')).toBeInTheDocument()
    expect(screen.getByText('program_media · program · one')).toBeInTheDocument()
  })
})
