/** 只维护展示焦点；折叠内容、隐藏控件和失效按钮不得成为模态 Tab 环的入口。 */

const selector = 'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, a[href], [tabindex]:not([tabindex="-1"])'

/** CSS 响应式隐藏、折叠 details 及 disabled 都会让仍连接的 opener 不可恢复。 */
function focusAvailable(element: HTMLElement): boolean {
  if (!element.isConnected || element.matches(':disabled') || element.closest('[hidden], [inert], [aria-hidden="true"]')) return false
  for (let ancestor: HTMLElement | null = element; ancestor; ancestor = ancestor.parentElement) {
    const style = getComputedStyle(ancestor)
    if (style.display === 'none' || style.visibility === 'hidden') return false
    if (ancestor instanceof HTMLDetailsElement && !ancestor.open) {
      const summary = ancestor.querySelector(':scope > summary')
      if (!summary?.contains(element)) return false
    }
  }
  return true
}

/** 允许 tabIndex=-1 的明确导航目标；失败由调用方退回它自己拥有的可见入口。 */
export function focusIfAvailable(element: HTMLElement | null | undefined): boolean {
  if (!element || element === document.body || !focusAvailable(element)) return false
  element.focus()
  return document.activeElement === element
}

export function dialogFocusTargets(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>(selector)].sort((left, right) => {
    const position = left.compareDocumentPosition(right)
    return position & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : position & Node.DOCUMENT_POSITION_PRECEDING ? 1 : 0
  }).filter((element) => element.tabIndex >= 0 && focusAvailable(element))
}
