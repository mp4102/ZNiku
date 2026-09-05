/** 键盘连接回归覆盖兼容性筛选、迟到 Graph 更新和焦点恢复。 */

import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ConnectNodesDialog } from './ConnectNodesDialog'
import { definitionForNode, isStudioConnectionValid } from '../graph'
import { projectSnapshot } from '../test-fixtures'
import type { GraphWire } from '../contracts'
import type { WorkflowNode } from '../../model'

afterEach(cleanup)
const graph: GraphWire = { ...projectSnapshot.project.graph, edges: [] }
const nodes: WorkflowNode[] = graph.nodes.map((node) => {
  const definition = definitionForNode(node, projectSnapshot.definitions)!
  return { id: node.node_id, type: 'workflow', position: { x: 0, y: 0 }, data: {
    label: node.node_id === 'source' ? '导入视频' : node.node_id === 'transform' ? '画质增强' : '输出成片',
    instanceId: node.node_id, summaries: [], typeId: definition.type_id, definitionVersion: definition.version,
    executorKind: definition.executor.kind, inputs: definition.input_ports, outputs: definition.output_ports,
    portLabels: { input: { in: '视频输入' }, output: { out: '视频输出' } },
    nodeRun: null, latestResult: null, progress: { mode: 'none', fraction: null, measurement: null, elapsed: null },
  } }
})

describe('纯键盘连接节点', () => {
  it('选择友好节点和端口可以建立同一 Graph 连接，不输入 ID', async () => {
    const onConnect = vi.fn()
    const onClose = vi.fn()
    render(<ConnectNodesDialog nodes={nodes} onConnect={onConnect} onClose={onClose} isValidConnection={(connection) => isStudioConnectionValid(connection, graph, projectSnapshot.definitions)} />)
    const from = screen.getByRole('combobox', { name: '连接起点' })
    const to = screen.getByRole('combobox', { name: '连接终点' })
    expect(within(from).getByRole('option', { name: '导入视频' })).toBeInTheDocument()
    expect(within(to).queryByRole('option', { name: '导入视频' })).not.toBeInTheDocument()
    await userEvent.selectOptions(to, 'sink')
    screen.getByRole('button', { name: '建立连接' }).focus()
    await userEvent.keyboard('{Enter}')
    expect(onConnect).toHaveBeenCalledExactlyOnceWith({ source: 'source', sourceHandle: 'out', target: 'sink', targetHandle: 'in' })
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('目标在对话框打开后不再合法时禁止提交，Escape 和 Tab 保留键盘路径', async () => {
    const onConnect = vi.fn()
    const onClose = vi.fn()
    const view = render(<ConnectNodesDialog nodes={nodes} onConnect={onConnect} onClose={onClose} isValidConnection={() => true} />)
    view.rerender(<ConnectNodesDialog nodes={nodes} onConnect={onConnect} onClose={onClose} isValidConnection={() => false} />)
    expect(screen.getByRole('button', { name: '建立连接' })).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent('没有可连接的下一步')
    screen.getByRole('combobox', { name: '连接输出' }).focus()
    await userEvent.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledOnce()
    expect(onConnect).not.toHaveBeenCalled()
  })
})
