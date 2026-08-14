import '@testing-library/jest-dom/vitest'

class ResizeObserverMock implements ResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

globalThis.ResizeObserver = ResizeObserverMock

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
