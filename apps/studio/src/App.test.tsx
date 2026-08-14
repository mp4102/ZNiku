import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import { App } from './App'
import { initialDesignerEdges, planNodes } from './mock-data'

afterEach(cleanup)

describe('ZMediaFlow Studio GUI-0 workflow prototype', () => {
  it('把原始音轨保存为独立的 Demux 到 Mux 数据边', () => {
    const audioEdge = initialDesignerEdges.find(
      (edge) => edge.source === 'demux' && edge.target === 'mux',
    )

    expect(audioEdge).toMatchObject({
      sourceHandle: 'audio',
      targetHandle: 'audio',
      type: 'audioLane',
    })
    expect(planNodes).toHaveLength(18)
  })

  it('显著标记 mock 边界并展示默认编排', () => {
    render(<App />)

    expect(screen.getByText('ZMediaFlow')).toBeInTheDocument()
    expect(screen.getByText('MOCK · NO MEDIA I/O')).toBeInTheDocument()
    expect(screen.getByText('电影级编排 DAG')).toBeInTheDocument()
    expect(screen.getAllByText('Enhancement').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Demux').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Mux').length).toBeGreaterThan(0)
    expect(screen.getByText('Audio lane')).toBeInTheDocument()
    expect(screen.getByText('Video + original audio')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run Monitor' })).toBeDisabled()
  })

  it('编译后允许审阅冻结并进入模拟 Run Monitor', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: '编译预览' }))
    expect(screen.getByText('Compiler 展开的章节执行图')).toBeInTheDocument()
    expect(screen.getByText('模拟编译完成')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '审阅并冻结' }))
    expect(screen.getByRole('dialog', { name: '冻结 WorkflowRevision？' })).toBeInTheDocument()
    expect(screen.getByText('18 instances')).toBeInTheDocument()
    expect(screen.getByText('2 / 2 tracks · ordered · stream copy')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '冻结并开始模拟运行' }))
    expect(screen.getByText('Mock run · step 1/4')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run Monitor' })).toBeEnabled()
  })

  it('可以从 mock Registry 添加 Draft 节点', async () => {
    const user = userEvent.setup()
    render(<App />)

    const buttons = screen.getAllByRole('button', { name: /Decensoring/ })
    await user.click(buttons[0])

    expect(screen.getByText('New draft node')).toBeInTheDocument()
    expect(screen.getByText('mock.decensoring')).toBeInTheDocument()
  })

  it('在 Mux Inspector 中分别展示视频和音频输入端口', async () => {
    render(<App />)

    fireEvent.click(screen.getByLabelText('Mux 节点'))

    expect(screen.getByText('engine.mux')).toBeInTheDocument()
    expect(screen.getByText('encoded_video · one')).toBeInTheDocument()
    expect(screen.getByText('audio_artifact_set · set')).toBeInTheDocument()
    expect(screen.getByText('program_media · one')).toBeInTheDocument()
  })
})
