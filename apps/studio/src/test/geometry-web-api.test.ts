/** 只验证 JSDOM 的缩放替身没有把所有视口伪装成 1；不代替浏览器端口几何测试。 */
import { describe, expect, it } from 'vitest'

describe('测量测试环境 CSS 矩阵', () => {
  it('保留 CSS matrix 的两个缩放轴、倾斜和位移', () => {
    const matrix = new DOMMatrixReadOnly('matrix(0.5, 0.1, 0.2, 0.75, 40, 80)')
    expect([matrix.m11, matrix.m12, matrix.m21, matrix.m22, matrix.m41, matrix.m42]).toEqual([0.5, 0.1, 0.2, 0.75, 40, 80])
  })
  it('保留 ReactFlow translate/scale 顺序与非等比缩放', () => {
    const matrix = new DOMMatrixReadOnly('translate(40px, 80px) scale(0.5, 0.75)')
    expect([matrix.m11, matrix.m22, matrix.m41, matrix.m42]).toEqual([0.5, 0.75, 40, 80])
    const reversed = new DOMMatrixReadOnly('scale(0.5) translate(40px, 80px)')
    expect([reversed.m11, reversed.m22, reversed.m41, reversed.m42]).toEqual([0.5, 0.5, 20, 40])
  })
  it('matrix3d、无变换和轴缩放仍保留当前视口值；未知变换失败', () => {
    const matrix = new DOMMatrixReadOnly('matrix3d(0.5,0,0,0,0,0.75,0,0,0,0,1,0,40,80,0,1)')
    expect([matrix.m11, matrix.m22, matrix.m41, matrix.m42]).toEqual([0.5, 0.75, 40, 80])
    expect(new DOMMatrixReadOnly('none').m22).toBe(1)
    expect(new DOMMatrixReadOnly('scaleX(0.4) scaleY(0.7) translateY(10px)').m42).toBe(7)
    expect(() => new DOMMatrixReadOnly('unsupported(1)')).toThrow('未支持')
  })
})
