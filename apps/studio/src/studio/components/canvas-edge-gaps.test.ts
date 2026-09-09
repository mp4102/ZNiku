/** 交叉断口只改临时 SVG stroke，不制造连接点，不剪掉真实首尾端口。 */
import { describe, expect, it } from 'vitest'
import type { GeometryRoute, Point } from '../geometry/types'
import { crossingDisplayPaths } from './canvas-edge-gaps'

function route(id: string, points: readonly Point[], reason: GeometryRoute['reason'] = null): GeometryRoute {
  return { id, points, reason, path: points.map((point, index) => `${index === 0 ? 'M' : 'L'}${point.x} ${point.y}`).join(' '),
    bounds: { left: Math.min(...points.map((point) => point.x)), right: Math.max(...points.map((point) => point.x)),
      top: Math.min(...points.map((point) => point.y)), bottom: Math.max(...points.map((point) => point.y)) } }
}
const horizontal = route('horizontal', [{ x: 0, y: 50 }, { x: 100, y: 50 }])
const vertical = route('vertical', [{ x: 50, y: 0 }, { x: 50, y: 100 }])

describe('临时跨线断口', () => {
  it('下穿线仅在交点留出断口，上跨线不增加节点或合流；输入路线保持不变', () => {
    const routes = [horizontal, vertical], before = JSON.stringify(routes)
    const display = crossingDisplayPaths(routes)
    expect(display.size).toBe(1)
    expect(display.get('horizontal')).toBe('M0 50 L45 50 M55 50 L100 50')
    expect(display.has('vertical')).toBe(false)
    expect(JSON.stringify(routes)).toBe(before)
    expect(display.get('horizontal')).not.toMatch(/[ACQZ]/)
  })
  it('交叉层次按既有边顺序稳定；不会重排实际 Edge 或把平行线合成总线', () => {
    expect(crossingDisplayPaths([horizontal, vertical]).get('horizontal')).toBe(crossingDisplayPaths([horizontal, vertical]).get('horizontal'))
    expect(crossingDisplayPaths([vertical, horizontal]).get('vertical')).toBe('M50 0 L50 45 M50 55 L50 100')
    const parallel = route('parallel', [{ x: 0, y: 60 }, { x: 100, y: 60 }])
    expect(crossingDisplayPaths([horizontal, parallel]).size).toBe(0)
  })
  it('真实端点、共端口和折点相触不裁断，不把接触伪造成交点', () => {
    const endpoint = route('endpoint', [{ x: 100, y: 0 }, { x: 100, y: 100 }])
    const sharedPort = route('shared-port', [{ x: 100, y: 50 }, { x: 100, y: 100 }])
    const elbow = route('elbow', [{ x: 50, y: 0 }, { x: 50, y: 50 }, { x: 80, y: 50 }])
    expect(crossingDisplayPaths([horizontal, endpoint]).size).toBe(0)
    expect(crossingDisplayPaths([horizontal, sharedPort]).size).toBe(0)
    expect(crossingDisplayPaths([horizontal, elbow]).size).toBe(0)
  })
  it('相邻断口合并但保持整条边首尾；降级路线不伪装成已审计正交路径', () => {
    const second = route('second', [{ x: 55, y: 0 }, { x: 55, y: 100 }])
    expect(crossingDisplayPaths([horizontal, vertical, second]).get('horizontal')).toBe('M0 50 L45 50 M60 50 L100 50')
    const degraded = { ...horizontal, reason: 'work_budget' as const, points: [] }
    expect(crossingDisplayPaths([degraded, vertical]).size).toBe(0)
    expect(degraded.path).toBe('M0 50 L100 50')
  })
})
