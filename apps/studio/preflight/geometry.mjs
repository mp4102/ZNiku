/** 纯合成几何实验：严格输入、尺寸感知排列与独立碰撞检查；不是领域合同。 */
import { getSmoothStepPath, Position } from '@xyflow/react'

export const PADDING = 12
const keys = (value, allowed) => {
  if (!value || Object.getPrototypeOf(value) !== Object.prototype ||
      Object.keys(value).some((key) => !allowed.includes(key)) ||
      allowed.some((key) => !Object.hasOwn(value, key))) throw new Error('E_GEOMETRY_FIELDS')
}
const id = (value) => typeof value === 'string' && value.length > 0 && value.length <= 120
const finite = (value) => typeof value === 'number' && Number.isFinite(value) && Math.abs(value) <= 1e6

export function validateGeometry(graph) {
  keys(graph, ['version', 'nodes', 'edges'])
  if (graph.version !== 1 || !Array.isArray(graph.nodes) || !Array.isArray(graph.edges) ||
      graph.nodes.length > 200 || graph.edges.length > 800) throw new Error('E_GEOMETRY_LIMIT')
  const nodes = new Map()
  for (const node of graph.nodes) {
    keys(node, ['id', 'x', 'y', 'width', 'height', 'inputs', 'outputs'])
    if (!id(node.id) || nodes.has(node.id) || ![node.x, node.y, node.width, node.height].every(finite) ||
        node.width < 40 || node.height < 40) throw new Error('E_GEOMETRY_NODE')
    for (const ports of [node.inputs, node.outputs]) {
      if (!Array.isArray(ports) || ports.length > 16 || !ports.every(id) || new Set(ports).size !== ports.length) {
        throw new Error('E_GEOMETRY_PORTS')
      }
    }
    nodes.set(node.id, node)
  }
  const edges = new Set()
  for (const edge of graph.edges) {
    keys(edge, ['id', 'source', 'sourcePort', 'target', 'targetPort', 'ordinal'])
    if (!id(edge.id) || edges.has(edge.id) || !nodes.get(edge.source)?.outputs.includes(edge.sourcePort) ||
        !nodes.get(edge.target)?.inputs.includes(edge.targetPort) ||
        (edge.ordinal !== null && (!Number.isSafeInteger(edge.ordinal) || edge.ordinal < 0))) {
      throw new Error('E_GEOMETRY_EDGE')
    }
    edges.add(edge.id)
  }
  return graph
}

export function ranks(graph) {
  validateGeometry(graph)
  const rank = new Map(graph.nodes.map((node) => [node.id, 0]))
  const incoming = new Map(graph.nodes.map((node) => [node.id, 0]))
  const next = new Map(graph.nodes.map((node) => [node.id, new Set()]))
  for (const edge of graph.edges) if (!next.get(edge.source).has(edge.target)) {
    next.get(edge.source).add(edge.target)
    incoming.set(edge.target, incoming.get(edge.target) + 1)
  }
  const ready = graph.nodes.filter((node) => !incoming.get(node.id)).map((node) => node.id)
  for (let index = 0; index < ready.length; index += 1) {
    const source = ready[index]
    for (const target of next.get(source)) {
      rank.set(target, Math.max(rank.get(target), rank.get(source) + 1))
      incoming.set(target, incoming.get(target) - 1)
      if (!incoming.get(target)) ready.push(target)
    }
  }
  if (ready.length !== graph.nodes.length) throw new Error('E_GEOMETRY_CYCLE')
  return rank
}

/** 镜像 graph.ts 的 fixed-grid 公式；测试检测源公式漂移，不加载正式 TS。 */
export function layout(graph, measured = false) {
  const rank = ranks(graph)
  const widths = new Map()
  for (const node of graph.nodes) widths.set(rank.get(node.id), Math.max(widths.get(rank.get(node.id)) ?? 0, node.width))
  const columns = new Map()
  let column = 80
  for (let index = 0; index < widths.size; index += 1) {
    columns.set(index, column)
    column += widths.get(index) + 144
  }
  const rows = new Map()
  return { ...graph, nodes: graph.nodes.map((node) => {
    const layer = rank.get(node.id)
    const row = rows.get(layer) ?? 0
    rows.set(layer, row + (measured ? node.height + 64 : 1))
    return { ...node, x: measured ? columns.get(layer) : 80 + layer * 360, y: 100 + (measured ? row : row * 280) }
  }) }
}

/** 实验输入提供测量后的矩形；row 模式模拟真实端口行的测量，不修改正式卡片。 */
export function anchor(node, side, port, mode = 'row') {
  const ports = side === 'source' ? node.outputs : node.inputs
  const index = ports.indexOf(port)
  if (index < 0) throw new Error('E_GEOMETRY_PORT')
  const y = mode === 'legacy'
    ? node.height * (58 + (index - (ports.length - 1) / 2) * 22) / 100
    : 32 + (node.height - 48) * (index + 1) / (ports.length + 1)
  return { x: node.x + (side === 'source' ? node.width : 0), y: node.y + y }
}

export function endpoints(graph, edge, mode = 'row') {
  const nodes = new Map(graph.nodes.map((node) => [node.id, node]))
  return [anchor(nodes.get(edge.source), 'source', edge.sourcePort, mode),
    anchor(nodes.get(edge.target), 'target', edge.targetPort, mode)]
}

export function smoothPath(start, end) {
  return getSmoothStepPath({ sourceX: start.x, sourceY: start.y, sourcePosition: Position.Right,
    targetX: end.x, targetY: end.y, targetPosition: Position.Left, borderRadius: 5, offset: 20 })[0]
}

export const samePoint = (a, b) => Math.abs(a.x - b.x) < 1e-7 && Math.abs(a.y - b.y) < 1e-7
export const rect = (node, margin = PADDING) => ({ left: node.x - margin, right: node.x + node.width + margin,
  top: node.y - margin, bottom: node.y + node.height + margin })
export const inside = (point, box) => point.x > box.left + 1e-7 && point.x < box.right - 1e-7 &&
  point.y > box.top + 1e-7 && point.y < box.bottom - 1e-7

/** Liang–Barsky 区间检查：矩形内部才算穿越，沿扩展边界的安全通道不算穿越。 */
export function segmentHits(a, b, box) {
  let low = 0, high = 1
  for (const [origin, delta, min, max] of [[a.x, b.x - a.x, box.left + 1e-7, box.right - 1e-7],
    [a.y, b.y - a.y, box.top + 1e-7, box.bottom - 1e-7]]) {
    if (Math.abs(delta) < 1e-12) { if (origin < min || origin > max) return false }
    else {
      const t1 = (min - origin) / delta, t2 = (max - origin) / delta
      low = Math.max(low, Math.min(t1, t2)); high = Math.min(high, Math.max(t1, t2))
      if (low > high) return false
    }
  }
  return high > 0 && low < 1
}

/** 真实 smoothstep 的 M/L/Q 几何，不采样近似：按二次方程边界根分区检查曲线。 */
export function pathSegments(path) {
  const tokens = path.match(/[MLQ]|[-+]?(?:\d*\.)?\d+(?:e[-+]?\d+)?/gi) ?? []
  const segments = []
  let at, index = 0
  const point = () => ({ x: Number(tokens[index++]), y: Number(tokens[index++]) })
  while (index < tokens.length) {
    const command = tokens[index++]
    if (command === 'M') at = point()
    else if (command === 'L') { const end = point(); segments.push({ a: at, b: end }); at = end }
    else if (command === 'Q') { const control = point(), end = point(); segments.push({ a: at, c: control, b: end }); at = end }
    else throw new Error('E_PATH_COMMAND')
  }
  if (!segments.length || segments.some((segment) => !Object.values(segment).every((p) => Number.isFinite(p.x) && Number.isFinite(p.y)))) {
    throw new Error('E_PATH_EMPTY')
  }
  return segments
}

export function curveHits(segment, box) {
  if (!segment.c) return segmentHits(segment.a, segment.b, box)
  const { a, b, c } = segment
  const roots = [0, 1]
  for (const [axis, bounds] of [['x', [box.left, box.right]], ['y', [box.top, box.bottom]]]) {
    const aa = a[axis] - 2 * c[axis] + b[axis], bb = 2 * (c[axis] - a[axis])
    for (const boundary of bounds) {
      const cc = a[axis] - boundary
      if (Math.abs(aa) < 1e-12) { if (Math.abs(bb) > 1e-12) roots.push(-cc / bb) }
      else {
        const discriminant = bb * bb - 4 * aa * cc
        if (discriminant >= 0) roots.push((-bb + Math.sqrt(discriminant)) / (2 * aa), (-bb - Math.sqrt(discriminant)) / (2 * aa))
      }
    }
  }
  const ts = roots.filter((t) => t >= 0 && t <= 1).sort((x, y) => x - y)
  return ts.slice(1).some((t, index) => {
    const u = (t + ts[index]) / 2
    return inside({ x: (1 - u) ** 2 * a.x + 2 * (1 - u) * u * c.x + u ** 2 * b.x,
      y: (1 - u) ** 2 * a.y + 2 * (1 - u) * u * c.y + u ** 2 * b.y }, box)
  })
}

export function polylinePath(points) {
  return points.map((point, index) => `${index ? 'L' : 'M'}${point.x} ${point.y}`).join(' ')
}

/** 仅源/目标的最初/最后出入口段豁免自己的扩展矩形；其余路径仍需检查。 */
export function audit(graph, routes, mode = 'row') {
  let collisions = 0, endpointErrors = 0, outsidePorts = 0, overlaps = 0
  for (const node of graph.nodes) for (const [side, ports] of [['source', node.outputs], ['target', node.inputs]]) {
    for (const port of ports) {
      const point = anchor(node, side, port, mode)
      if (point.y < node.y || point.y > node.y + node.height) outsidePorts += 1
    }
  }
  for (let i = 0; i < graph.nodes.length; i += 1) for (let j = i + 1; j < graph.nodes.length; j += 1) {
    const a = rect(graph.nodes[i], 0), b = rect(graph.nodes[j], 0)
    if (a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top) overlaps += 1
  }
  for (const edge of graph.edges) {
    const route = routes.find((item) => item.id === edge.id)
    const segments = pathSegments(route.path)
    const [start, end] = endpoints(graph, edge, mode)
    if (!samePoint(segments[0].a, start) || !samePoint(segments.at(-1).b, end)) endpointErrors += 1
    for (const node of graph.nodes) {
      // 只豁免从正确锚点水平向右的首段/到正确锚点水平向右的末段。
      // 不允许把整条源/目标矩形都排除，也不允许任意方向的出入口借豁免穿透卡片。
      const box = rect(node)
      let first = 0, last = segments.length - 1
      const head = segments[0], tail = segments.at(-1)
      if (node.id === edge.source && !head.c && samePoint(head.a, start) &&
          head.b.x >= head.a.x && head.b.y === head.a.y) first += 1
      if (node.id === edge.target && !tail.c && samePoint(tail.b, end) &&
          tail.b.x >= tail.a.x && tail.b.y === tail.a.y) last -= 1
      if (segments.slice(first, last + 1).some((segment) => curveHits(segment, box))) collisions += 1
    }
  }
  return { collisions, endpointErrors, outsidePorts, overlaps }
}

export function baseline(graph) {
  validateGeometry(graph)
  return graph.edges.map((edge) => ({ id: edge.id, path: smoothPath(...endpoints(graph, edge, 'legacy')), reason: null }))
}
