import '@testing-library/jest-dom/vitest'

class ResizeObserverMock implements ResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

globalThis.ResizeObserver = ResizeObserverMock

// JSDOM 没有 DOMMatrix；ReactFlow 的真实测量从该 Web API 读取 CSS 缩放。
// 此替身仅覆盖测试中的 2D/3D matrix 及 translate/scale；未知变换失败而非伪造尺寸。
// 真实端口、缩放、字体和 DPI 仍由生产浏览器测试验收。
if (typeof window.DOMMatrixReadOnly === 'undefined') {
  class DOMMatrixReadOnlyMock {
    readonly m11: number
    readonly m12: number
    readonly m21: number
    readonly m22: number
    readonly m41: number
    readonly m42: number
    constructor(transform = 'none') {
      const matrix = /^matrix\(([^)]+)\)$/.exec(transform)
      const matrix3d = /^matrix3d\(([^)]+)\)$/.exec(transform)
      let values = [1, 0, 0, 1, 0, 0]
      if (matrix) values = matrix[1]!.split(',').map(Number)
      else if (matrix3d) {
        const all = matrix3d[1]!.split(',').map(Number)
        if (all.length !== 16 || all.some((value) => !Number.isFinite(value))) throw new Error('非法测试 matrix3d')
        values = [all[0]!, all[1]!, all[4]!, all[5]!, all[12]!, all[13]!]
      } else if (transform.trim() && transform !== 'none') {
        const functions = [...transform.matchAll(/(translate|translateX|translateY|scale|scaleX|scaleY)\(([^)]+)\)/g)]
        const remainder = transform.replace(/(translate|translateX|translateY|scale|scaleX|scaleY)\(([^)]+)\)/g, '').trim()
        if (functions.length === 0 || remainder) throw new Error(`未支持的测试 CSS transform: ${transform}`)
        for (const part of functions) {
          const numbers = part[2]!.split(/[,\s]+/).map((value) => Number.parseFloat(value))
          const kind = part[1]!
          const [a, b, c, d, e, f] = values as [number, number, number, number, number, number]
          if (kind.startsWith('translate')) {
            const x = kind === 'translateY' ? 0 : numbers[0]!
            const y = kind === 'translateX' ? 0 : kind === 'translateY' ? numbers[0]! : numbers[1] ?? 0
            values = [a, b, c, d, a * x + c * y + e, b * x + d * y + f]
          } else {
            const x = kind === 'scaleY' ? 1 : numbers[0]!
            const y = kind === 'scaleX' ? 1 : kind === 'scaleY' ? numbers[0]! : numbers[1] ?? numbers[0]!
            values = [a * x, b * x, c * y, d * y, e, f]
          }
        }
      }
      if (values.length !== 6 || values.some((value) => !Number.isFinite(value))) throw new Error('非法测试 matrix')
      ;[this.m11, this.m12, this.m21, this.m22, this.m41, this.m42] = values as [number, number, number, number, number, number]
    }
  }
  Object.defineProperty(window, 'DOMMatrixReadOnly', { configurable: true, value: DOMMatrixReadOnlyMock })
}

if (typeof HTMLElement !== 'undefined') {
  Object.defineProperty(HTMLElement.prototype, 'getBoundingClientRect', {
    configurable: true,
    value: () => ({
      width: 1280,
      height: 720,
      top: 0,
      left: 0,
      bottom: 720,
      right: 1280,
      x: 0,
      y: 0,
      toJSON: () => undefined,
    }),
  })
}
