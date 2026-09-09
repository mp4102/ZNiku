/** 焦点可达性不依赖 jsdom 虚构尺寸；验证真实 HTML/CSS 隐藏语义和明确恢复目标。 */
import { useState } from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import { CanvasDialog } from './ConnectNodesDialog'
import { dialogFocusTargets, focusIfAvailable } from './focus-management'

afterEach(cleanup)

describe('UI 焦点可达性', () => {
  it('排除隐藏祖先、inert、aria-hidden、CSS 和 disabled，但允许显式 tabindex=-1 返回点', () => {
    const { container } = render(<section tabIndex={-1} aria-label="可见画布">
      <div hidden><button>hidden</button></div>
      <div inert><button>inert</button></div>
      <div aria-hidden="true"><button>aria-hidden</button></div>
      <div style={{ display: 'none' }}><button>display</button></div>
      <div style={{ visibility: 'hidden' }}><button>visibility</button></div>
      <fieldset disabled><button>fieldset-disabled</button></fieldset>
      <button disabled>disabled</button>
      <details><summary>折叠说明</summary><button>collapsed</button></details>
      <button>可操作</button>
    </section>)
    expect(dialogFocusTargets(container).map((item) => item.textContent)).toEqual(['折叠说明', '可操作'])
    for (const name of ['hidden', 'inert', 'aria-hidden', 'display', 'visibility', 'fieldset-disabled', 'disabled', 'collapsed']) {
      expect(focusIfAvailable(screen.getByText(name))).toBe(false)
    }
    expect(focusIfAvailable(screen.getByRole('region', { name: '可见画布' }))).toBe(true)
    expect(screen.getByRole('region', { name: '可见画布' })).toHaveFocus()
  })

  it.each(['disabled', 'hidden'] as const)('窗口打开后原入口 %s，关闭退回可见画布而非隐藏控件', async (state) => {
    function Example() {
      const [open, setOpen] = useState(false)
      const [opened, setOpened] = useState(false)
      return <>
        <section id="workflow-canvas" tabIndex={-1} aria-label="可见画布" />
        <button disabled={opened && state === 'disabled'} hidden={opened && state === 'hidden'}
          onClick={() => { setOpen(true); setOpened(true) }}>打开窗口</button>
        {open && <CanvasDialog title="任务说明" onClose={() => setOpen(false)}><p>合成说明</p></CanvasDialog>}
      </>
    }
    render(<Example />)
    await userEvent.click(screen.getByRole('button', { name: '打开窗口' }))
    await userEvent.keyboard('{Escape}')
    expect(screen.getByRole('region', { name: '可见画布' })).toHaveFocus()
  })

  it('子窗口的入口失效时返回父模态可用入口，不穿透到后台画布', async () => {
    function Example() {
      const [open, setOpen] = useState(false)
      const [opened, setOpened] = useState(false)
      return <>
        <section id="workflow-canvas" tabIndex={-1} aria-label="后台画布" />
        <section role="dialog" aria-modal="true" aria-label="父窗口" tabIndex={-1}>
          <button>父窗口返回点</button>
          <button disabled={opened} onClick={() => { setOpen(true); setOpened(true) }}>打开子窗口</button>
          {open && <CanvasDialog title="子窗口" onClose={() => setOpen(false)}><p>合成说明</p></CanvasDialog>}
        </section>
      </>
    }
    render(<Example />)
    await userEvent.click(screen.getByRole('button', { name: '打开子窗口' }))
    await userEvent.keyboard('{Escape}')
    expect(screen.getByRole('button', { name: '父窗口返回点' })).toHaveFocus()
  })
})
