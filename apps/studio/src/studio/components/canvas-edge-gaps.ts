/** 交叉处只裁掉被跨越线的一小段显示；实际 Edge、端点与连接关系保持原样。 */
import type { GeometryRoute } from '../geometry/types'
import { pathWithCrossingGaps, routeCrossings } from '../geometry/primitives'

export function crossingDisplayPaths(routes: readonly GeometryRoute[]): ReadonlyMap<string, string> {
  const crossings = routeCrossings(routes)
  const display = new Map<string, string>()
  for (const route of routes) {
    const path = pathWithCrossingGaps(route, crossings)
    if (path !== route.path) display.set(route.id, path)
  }
  return display
}
