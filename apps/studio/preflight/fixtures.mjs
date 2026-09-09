/** G01–G10 全部为合成几何；不含媒体、路径、Runtime 状态或工程文件。 */
const node = (id, x = 0, y = 0, width = 240, height = 180, count = 1) => ({ id, x, y, width, height,
  inputs: Array.from({ length: count }, (_, i) => `in-${i}`), outputs: Array.from({ length: count }, (_, i) => `out-${i}`) })
const edge = (source, target, index = 0, sourceIndex = 0, targetIndex = 0, ordinal = null) => ({
  id: `edge-${source}-${target}-${index}`, source, target, sourcePort: `out-${sourceIndex}`, targetPort: `in-${targetIndex}`, ordinal })
const graph = (nodes, edges) => ({ version: 1, nodes, edges })
const fixture = (id, description, value, arrange = true, options = {}) => ({ id, description, graph: value, arrange, options })

function large(count) {
  const columns = count / 10
  const nodes = Array.from({ length: count }, (_, i) => node(`n${i}`, Math.floor(i / 10) * 440, (i % 10) * 270,
    220 + (i % 3) * 24, 160 + (i % 4) * 20))
  const edges = []
  for (let col = 1; col < columns; col += 1) for (let row = 0; row < 10; row += 1) {
    edges.push(edge(`n${(col - 1) * 10 + row}`, `n${col * 10 + row}`))
  }
  for (let col = 0; col < columns - 3; col += 3) edges.push(edge(`n${col * 10}`, `n${(col + 3) * 10 + 9}`, 1))
  return graph(nodes, edges)
}

const list = [
  fixture('G01', '普通链', graph([node('s'), node('a'), node('b'), node('o')], [edge('s', 'a'), edge('a', 'b'), edge('b', 'o')])),
  fixture('G02', '长跨层边穿过无关节点', graph([node('s', 0, 0), node('m', 400, 0), node('t', 800, 0)], [edge('s', 't')]), false),
  fixture('G03', '双分支多级汇合', graph(['s', 'a', 'b', 'c', 'd', 'e', 'f', 'o'].map((id) => node(id)),
    [['s', 'a'], ['s', 'b'], ['a', 'c'], ['b', 'c'], ['b', 'd'], ['c', 'e'], ['d', 'e'], ['e', 'f'], ['f', 'o'], ['a', 'f']].map(([a, b], i) => edge(a, b, i)))),
  fixture('G04-multi', '多来源多输出', graph([
    { ...node('s1'), inputs: [] }, { ...node('s2'), inputs: [] }, node('m'),
    { ...node('o1'), outputs: [] }, { ...node('o2'), outputs: [] }], [edge('s1', 'm'), edge('s2', 'm'), edge('m', 'o1'), edge('m', 'o2')])) ,
  fixture('G04-zero', '零输出自由图', graph([{ ...node('s'), inputs: [] }, node('a')], [edge('s', 'a')])) ,
  ...[1, 2, 6, 8, 16].map((count) => fixture(`G05-${count}`, `${count} 端口`, graph([
    node('s', 0, 0, 260, Math.max(180, 56 + count * 24), count),
    node('t', 540, 0, 260, Math.max(180, 56 + count * 24), count)],
  Array.from({ length: count }, (_, i) => edge('s', 't', i, i, i))), false)),
  fixture('G06-tall', '长标题、错误摘要模拟测量后的高卡片', graph([node('s', 0, 0, 290, 560), node('a', 0, 0, 300, 460),
    node('b', 0, 0, 340, 380), node('o', 0, 0, 260, 190)], [edge('s', 'a'), edge('s', 'b'), edge('a', 'o'), edge('b', 'o')])),
  fixture('G06-collapsed', '同一身份折叠后的重新测量', graph([node('s', 0, 0, 240, 88), node('a', 0, 0, 240, 88),
    node('b', 0, 0, 240, 88), node('o', 0, 0, 240, 88)], [edge('s', 'a'), edge('s', 'b'), edge('a', 'o'), edge('b', 'o')])),
  fixture('G07-before', '无关节点未阻挡', graph([node('s', 0, 0), node('m', 400, 380), node('t', 800, 0)], [edge('s', 't')]), false),
  fixture('G07-after', '无关节点移入既有路径', graph([node('s', 0, 0), node('m', 400, 0), node('t', 800, 0)], [edge('s', 't')]), false),
  fixture('G08', '同端口多边及 ordered_many 顺序', graph([node('s', 0, 0), node('t', 620, 0)],
    Array.from({ length: 4 }, (_, i) => edge('s', 't', i, 0, 0, i))), false),
  fixture('G09-overlap', '节点重叠堵住出口', graph([node('s', 0, 0), node('m', 150, 0), node('t', 700, 0)], [edge('s', 't')]), false),
  fixture('G09-surround', '端口邻近障碍封住出口', graph([node('s', 0, 0), node('wall', 245, -50, 80, 300), node('t', 700, 0)], [edge('s', 't')]), false),
  fixture('G09-budget', '故意收紧预算', graph([node('s', 0, 0), node('m', 400, 0), node('t', 800, 0)], [edge('s', 't')]), false, { maxExpanded: 1 }),
  fixture('G10-50', '50 节点', large(50)),
  fixture('G10-200', '200 节点；1000 条历史属于独立服务数据，不进入几何输入', large(200)),
]

export const fixtures = () => structuredClone(list)
