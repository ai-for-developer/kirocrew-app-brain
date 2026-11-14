/**
 * Where the requirement's stage graph (StageGraph.tsx) puts everything (the sample team board's layout.ts, for a
 * requirement's runs instead of a team). Pure: the same snapshot always gives the same picture, and events never move
 * anything.
 *
 * One band per AI-DLC phase, left to right; 0 初始化 holds the orchestrator box (the owner) and the list under it (this
 * time's runs and hand-overs, then what the Brain brought in). A phase with nothing on this way folds into a narrow
 * grey band (the sample's 「1 构思 · 这次不需要」). A band stacks its boxes top to bottom in node order, ST_STACK to a
 * column; 3 构建 gives each stage done per unit a column with a row per unit (a column every unit skipped is not there),
 * then 构建测试 over 审查 in a column of their own. Skipped nodes get no box. The columns share one step, and each
 * column is centred on the tallest one. The compact layout (BOARD-31) stacks each band's boxes in one column, 3 构建's
 * unit by unit.
 *
 * The way runs from the orchestrator box through the boxes in node order. An edge leaves a box's right side and enters
 * the next one's left side, or runs down its column; an edge that skips columns runs across them above or below their
 * boxes. The run branch is a rail along the bottom with a dot per run under the middle of its boxes and each hand-over's
 * pill between two runs; the check ring sits at the rail's right end, like the sample's test station.
 */
import type { ActorStatus, BoardPhase, PhaseKey } from '../types'

export const GW = 1640
export const NODE_W = 184
export const NODE_H = 80
export const STATION_R = 28
/** Room kept free around a box when an edge passes its column; below it, room for its live ticker. */
const ROUTE_PAD = 12
const TICKER_PAD = 26

export interface Pt {
  x: number
  y: number
}
export type Seg = readonly [Pt, Pt, Pt, Pt]
export type Path = Seg[]

const round = (v: number) => Math.round(v * 10) / 10

export function bez(s: Seg, t: number): Pt {
  const u = 1 - t
  const a = u * u * u
  const b = 3 * u * u * t
  const c = 3 * u * t * t
  const d = t * t * t
  return { x: a * s[0].x + b * s[1].x + c * s[2].x + d * s[3].x, y: a * s[0].y + b * s[1].y + c * s[2].y + d * s[3].y }
}

const lerp = (p: Pt, q: Pt, t: number): Pt => ({ x: p.x + (q.x - p.x) * t, y: p.y + (q.y - p.y) * t })

/** A straight piece as a cubic (so a path is one kind of segment). */
export const line = (p: Pt, q: Pt): Seg => [p, lerp(p, q, 1 / 3), lerp(p, q, 2 / 3), q]

/** An S bend with horizontal tangents at both ends. */
export function sCurve(p: Pt, q: Pt): Seg {
  const dx = (q.x - p.x) / 2
  return [p, { x: p.x + dx, y: p.y }, { x: q.x - dx, y: q.y }, q]
}

/** Heading right at *p*, turning to head down (or up) into *q*. */
const turnDown = (p: Pt, q: Pt): Seg => [p, { x: q.x, y: p.y }, { x: q.x, y: p.y }, q]

/** Heading down at *p*, turning to head right into *q*. */
const turnRight = (p: Pt, q: Pt): Seg => [p, { x: p.x, y: q.y }, { x: p.x, y: q.y }, q]

export function pathD(path: Path): string {
  if (!path.length) return ''
  const f = (p: Pt) => `${round(p.x)},${round(p.y)}`
  return `M${f(path[0][0])}${path.map((s) => ` C${f(s[1])} ${f(s[2])} ${f(s[3])}`).join('')}`
}

export interface PathInfo {
  segs: Path
  lens: number[]
  total: number
}

export function measure(path: Path): PathInfo {
  const lens = path.map((s) => {
    let len = 0
    let prev = s[0]
    for (let i = 1; i <= 12; i++) {
      const p = bez(s, i / 12)
      len += Math.hypot(p.x - prev.x, p.y - prev.y)
      prev = p
    }
    return len
  })
  return { segs: path, lens, total: lens.reduce((a, b) => a + b, 0) }
}

/** The point *t* (0…1) of the way along the path, by length. */
export function pointAt(info: PathInfo, t: number): Pt {
  const n = info.segs.length
  if (!n) return { x: 0, y: 0 }
  let d = Math.min(1, Math.max(0, t)) * info.total
  for (let i = 0; i < n; i++) {
    const len = info.lens[i]
    if (d <= len || i === n - 1) return bez(info.segs[i], len > 0 ? Math.min(1, d / len) : 1)
    d -= len
  }
  return info.segs[n - 1][3]
}

/** Out of *start* (a box's right side) into *end* (a later column's box's left side), across the columns between above
 * or below their boxes (later edges through the same spot beside the first). *between*: those columns' index, centre
 * and box centres. */
function across(start: Pt, end: Pt, between: ReadonlyArray<{ index: number; x: number; ys: number[] }>, usage: Map<string, number>): Path {
  const pts: Pt[] = [start]
  for (const st of between) {
    const t = (st.x - start.x) / (end.x - start.x || 1)
    let y = start.y + (end.y - start.y) * t
    const bands = [...st.ys]
      .sort((p, q) => p - q)
      .map((cy) => [cy - NODE_H / 2 - ROUTE_PAD, cy + NODE_H / 2 + ROUTE_PAD + TICKER_PAD] as [number, number])
      .reduce<Array<[number, number]>>((acc, band) => {
        const last = acc[acc.length - 1]
        if (last && band[0] <= last[1]) last[1] = Math.max(last[1], band[1])
        else acc.push([...band])
        return acc
      }, [])
    let side = 0
    for (const [lo, hi] of bands) {
      if (y > lo && y < hi) {
        side = y - lo < hi - y ? -1 : 1
        y = side < 0 ? lo : hi
        break
      }
    }
    const slot = `${st.index}:${Math.round(y)}`
    const used = usage.get(slot) ?? 0
    usage.set(slot, used + 1)
    if (used) y += (side || (used % 2 ? 1 : -1)) * 7 * (side ? used : Math.ceil(used / 2))
    pts.push({ x: st.x - NODE_W / 2 - 10, y }, { x: st.x + NODE_W / 2 + 10, y })
  }
  pts.push(end)
  const path: Path = []
  for (let i = 0; i < pts.length - 1; i++) path.push(i % 2 === 1 ? line(pts[i], pts[i + 1]) : sCurve(pts[i], pts[i + 1]))
  return path
}

/** The graph is never drawn smaller than this (UI问题清单 BOARD-31: its 12 px lines stay about 10 px on screen): an app
 * too narrow for the full layout gets the compact one (each band in one column); only when even that does not fit,
 * the graph keeps this scale and scrolls sideways (GraphFrame's Scroller). The text never changes. */
export const SCALE = 0.84

/** 'run' (the run branch), 'tests' (the check ring), 'orch' (the owner), `relay:<i>` (the i-th hand-over's pill on
 * the rail) or a box's node id. */
export type Anchor = string
export const RUN = 'run'
export const TESTS = 'tests'
export const ORCH = 'orch'
export const relayAnchor = (i: number): Anchor => `relay:${i}`

/** A row of the list under the orchestrator box: this time's runs (a runner run's id under its row) and hand-overs,
 * then what the Brain brought in. */
export type RosterKind = 'head' | 'run' | 'sub' | 'relay' | 'brain-head' | 'brain'

/** What the stage graph's layout reads of a board (structural: a test builds one without a board). */
export interface StageInput {
  phases: ReadonlyArray<Pick<BoardPhase, 'key' | 'state'>>
  nodes: ReadonlyArray<{ id: string; stage: string; unit: string | null; phase: PhaseKey; status: ActorStatus; rounds: ReadonlyArray<{ run: string }> }>
  runs: ReadonlyArray<{ key: string }>
  relays: ReadonlyArray<{ to: string | null }>
  roster: ReadonlyArray<{ kind: RosterKind; run: string | null }>
}

/** All stageLayout reads of *g*, as one string: the picture stays while it does (the board is polled every 2 s, and a
 * box changing its state moves nothing; a stage skipped, a unit, a run or a hand-over more does). */
export function stageKey(g: StageInput): string {
  return JSON.stringify([
    g.phases.map((p) => [p.key, p.state]),
    g.nodes.map((n) => [n.id, n.stage, n.unit, n.phase, n.status === 'skipped', [...new Set(n.rounds.map((r) => r.run))]]),
    g.runs.map((r) => r.key),
    g.relays.map((r) => r.to),
    g.roster.map((r) => [r.kind, r.run]),
  ])
}

export interface StageBox extends Pt {
  id: string
  /** its column in StageLayout.columns */
  col: number
}
export interface StageColumn {
  x: number
  phase: PhaseKey
  /** its boxes, top to bottom */
  ids: string[]
}
export interface StageBand {
  key: PhaseKey
  x1: number
  x2: number
  /** nothing of it is on this way: a narrow grey band */
  folded: boolean
}
export interface StageEdge {
  /** a box, or ORCH: the way starts at the orchestrator box */
  from: string
  to: string
  path: Path
  d: string
}
export interface StageShape {
  width: number
  /** each band's boxes in one column: the full layout does not fit at SCALE (BOARD-31) */
  compact: boolean
}
export interface StageLayout extends StageShape {
  height: number
  /** init first, then the phases in their order */
  bands: StageBand[]
  columns: StageColumn[]
  /** a box per node that is not skipped */
  boxes: Map<string, StageBox>
  edges: StageEdge[]
  /** 3 构建's unit rows, two or more units in the full layout: each name, over its row's first box */
  units: (Pt & { unit: string })[]
  /** the 2 个单元 pill at the top of 3 构建 (two or more units) */
  unitPill: (Pt & { n: number }) | null
  /** the orchestrator box's centre */
  orch: Pt
  /** each row of the list under it, with its centre */
  roster: { kind: RosterKind; run: string | null; y: number }[]
  /** the bands' bottom edge (their foot lines sit just above it) */
  bottom: number
  rail: { x1: number; x2: number; y: number }
  station: Pt & { r: number }
  /** each run's dot on the rail, under the middle of its boxes (a run with no box has none) */
  runX: Map<string, number>
  /** each relay's pill on the rail, in relays order: between the dots of the runs it hands from and to */
  relayX: number[]
}

/** The first row of boxes' top: the band titles and subtitles, the 2 个单元 pill and the command bus are above it. */
const ST_TOP = 110
/** Two rows' centres apart: room under a box for its gate's chips or its ticker. */
const ST_ROW = 124
const ST_PAD = 10
/** Band 0: the orchestrator box and the list under it. */
const ST_INIT = 200
/** The owner's box; the rows of the list under it are as wide (a run's row, 本机会话 · 做完 · 交出去了, at 12 px). Band 0
 * can be no wider: the compact layout of four columns must still fit a 1050 px app at SCALE. */
export const ST_ORCH = { w: 182, h: 78 }
const ST_GAP = 8
/** A folded band. */
export const ST_FOLD = 80
/** Two columns' centres apart: at the closest a box and room for the edges between; at the farthest, so a way of few
 * columns does not spread thin over a wide graph. */
const ST_MIN = NODE_W + 30
const ST_MAX = 300
/** Boxes a band stacks in one column before it starts the next. */
const ST_STACK = 3
/** Under the last row: a gate's chips or a ticker, then the band's foot line. */
const ST_UNDER = 44
const ST_FOOT = 24
/** The command paths' bus: under the band subtitles and the 2 个单元 pill, over the first row's badges. */
export const ST_BUS = 88
/** A hand-over's pill on the rail (交接 ✓ · 桥 ✓ · 签名 ✓ · 11:07). */
export const RELAY_W = 180
const ROSTER_H: Record<RosterKind, number> = { head: 18, run: 19, sub: 15, relay: 19, 'brain-head': 28, brain: 17 }

type StageNodeIn = StageInput['nodes'][number]
interface PlanColumn {
  cells: { id: string; slot: number }[]
  /** the rows it takes: its boxes, or 3 构建's units */
  span: number
}
interface Plan {
  bands: { key: PhaseKey; folded: boolean; cols: PlanColumn[] }[]
  units: string[]
  rows: number
}

/** 3 构建's units in their plan's order: the order of the stage that has the most of them (代码生成 has every planned
 * one), then any other one a stage names. */
function unitOrder(nodes: ReadonlyArray<StageNodeIn>): string[] {
  const by = new Map<string, string[]>()
  for (const n of nodes) if (n.unit !== null) by.set(n.stage, [...(by.get(n.stage) ?? []), n.unit])
  const out = [...([...by.values()].sort((p, q) => q.length - p.length)[0] ?? [])]
  for (const n of nodes) if (n.unit !== null && !out.includes(n.unit)) out.push(n.unit)
  return out
}

function planOf(g: StageInput, compact: boolean): Plan {
  const boxed = g.nodes.filter((n) => n.status !== 'skipped')
  const build = boxed.filter((n) => n.phase === 'construction')
  const units = unitOrder(build)
  const shared = build.filter((n) => n.unit === null)
  const stack = Math.max(ST_STACK, units.length, shared.length)
  const column = (ns: ReadonlyArray<StageNodeIn>): PlanColumn => ({ cells: ns.map((n, i) => ({ id: n.id, slot: i })), span: ns.length })
  const bands = g.phases.filter((p) => p.key !== 'init').map((p) => {
    const its = boxed.filter((n) => n.phase === p.key)
    if (p.state !== 'used' || !its.length) return { key: p.key, folded: true, cols: [] }
    if (compact) {
      const order = p.key === 'construction' ? [...units.flatMap((u) => its.filter((n) => n.unit === u)), ...shared] : its
      return { key: p.key, folded: false, cols: [column(order)] }
    }
    if (p.key === 'construction') {
      const stages = [...new Set(its.filter((n) => n.unit !== null).map((n) => n.stage))]
      const rows = stages.map((s) => ({ cells: its.filter((n) => n.stage === s && n.unit !== null).map((n) => ({ id: n.id, slot: units.indexOf(n.unit!) })), span: units.length }))
      return { key: p.key, folded: false, cols: shared.length ? [...rows, column(shared)] : rows }
    }
    const cols: PlanColumn[] = []
    for (let i = 0; i < its.length; i += stack) cols.push(column(its.slice(i, i + stack)))
    return { key: p.key, folded: false, cols }
  })
  return { bands, units, rows: Math.max(1, ...bands.flatMap((b) => b.cols.map((c) => c.span))) }
}

const fixedOf = (p: Plan) => 2 * ST_PAD + ST_INIT + p.bands.length * ST_GAP + p.bands.filter((b) => b.folded).length * ST_FOLD
const columnsOf = (p: Plan) => p.bands.reduce((n, b) => n + b.cols.length, 0)
const narrowestOf = (p: Plan) => fixedOf(p) + columnsOf(p) * ST_MIN

/** The layout for a graph *avail* px wide (null: not measured): the full one while it fits at SCALE, else the compact
 * one; never narrower than its columns need (the graph then keeps SCALE and scrolls sideways, GraphFrame's Scroller). */
export function stageShape(avail: number | null | undefined, g: StageInput): StageShape {
  const full = narrowestOf(planOf(g, false))
  if (!avail) return { width: Math.max(GW, full), compact: false }
  const compact = full * SCALE > avail
  const least = compact ? narrowestOf(planOf(g, true)) : full
  return { width: Math.round(Math.max(least, Math.min(GW, avail / SCALE))), compact }
}

/** The way through *nodes* (the boxed ones, in node order): from the orchestrator box to the first, each to the next;
 * 3 构建's unit rows run side by side, each from the box before them, all into the box after them. */
export function stageWay(nodes: ReadonlyArray<StageNodeIn>): [string, string][] {
  const out: [string, string][] = []
  let tails: string[] = [ORCH]
  for (let i = 0; i < nodes.length; ) {
    if (nodes[i].unit === null) {
      for (const t of tails) out.push([t, nodes[i].id])
      tails = [nodes[i].id]
      i++
      continue
    }
    let j = i
    while (j < nodes.length && nodes[j].unit !== null) j++
    const block = nodes.slice(i, j)
    const from = tails
    tails = unitOrder(block).map((u) => {
      const chain = block.filter((n) => n.unit === u).map((n) => n.id)
      chain.forEach((id, k) => {
        for (const t of k ? [chain[k - 1]] : from) out.push([t, id])
      })
      return chain[chain.length - 1]
    })
    i = j
  }
  return out
}

/** A stage edge: down its column into the box under it, or round the column's right side past the boxes between;
 * else across to a later column (across()). */
function stageRoute(a: StageBox, b: StageBox, columns: StageColumn[], boxes: ReadonlyMap<string, StageBox>, usage: Map<string, number>): Path {
  if (a.col === b.col) {
    const dir = b.y > a.y ? 1 : -1
    const past = columns[a.col].ids.some((id) => {
      const y = boxes.get(id)!.y
      return (y - a.y) * (y - b.y) < 0
    })
    if (!past) return [line({ x: a.x, y: a.y + (dir * NODE_H) / 2 }, { x: b.x, y: b.y - (dir * NODE_H) / 2 })]
    const side = a.x + NODE_W / 2 + 16
    const q1 = { x: side, y: a.y + dir * 16 }
    const q2 = { x: side, y: b.y - dir * 16 }
    return [turnDown({ x: a.x + NODE_W / 2, y: a.y }, q1), line(q1, q2), turnRight(q2, { x: b.x + NODE_W / 2, y: b.y })]
  }
  const between = columns.slice(a.col + 1, b.col).map((c, i) => ({ index: a.col + 1 + i, x: c.x, ys: c.ids.map((id) => boxes.get(id)!.y) }))
  return across({ x: a.x + NODE_W / 2, y: a.y }, { x: b.x - NODE_W / 2, y: b.y }, between, usage)
}

/** The rail's marks left to right in the runs' order: a run's dot under the middle of its boxes; a hand-over's pill
 * between the dot before it and the dot of the run it hands to (one to nobody after the last dot); each clear of its
 * neighbours and of the check ring. */
function railMarks(g: StageInput, boxes: ReadonlyMap<string, StageBox>, rail: StageLayout['rail']): Pick<StageLayout, 'runX' | 'relayX'> {
  const marks: { run: string | null; relay: number; w: number; want: number | null }[] = []
  const known = new Set(g.runs.map((r) => r.key))
  for (const r of g.runs) {
    g.relays.forEach((rl, i) => rl.to === r.key && marks.push({ run: null, relay: i, w: RELAY_W, want: null }))
    const xs = g.nodes.filter((n) => boxes.has(n.id) && n.rounds.some((x) => x.run === r.key)).map((n) => boxes.get(n.id)!.x).sort((p, q) => p - q)
    if (xs.length) marks.push({ run: r.key, relay: -1, w: 22, want: xs[(xs.length - 1) >> 1] })
  }
  g.relays.forEach((rl, i) => (rl.to === null || !known.has(rl.to)) && marks.push({ run: null, relay: i, w: RELAY_W, want: null }))
  const xs = marks.map((m, i) => {
    if (m.want !== null) return m.want
    const before = marks.slice(0, i).reverse().find((o) => o.want !== null)?.want ?? null
    const after = marks.slice(i + 1).find((o) => o.run !== null)?.want ?? null
    if (before !== null && after !== null) return (before + after) / 2
    if (before !== null) return before + RELAY_W / 2 + 40
    return after !== null ? after - RELAY_W / 2 - 40 : rail.x1 + RELAY_W / 2 + 80
  })
  let edge = rail.x1 + 40
  marks.forEach((m, i) => {
    xs[i] = Math.max(xs[i], edge + 12 + m.w / 2)
    edge = xs[i] + m.w / 2
  })
  let limit = rail.x2 - 40
  for (let i = marks.length - 1; i >= 0; i--) {
    xs[i] = Math.min(xs[i], limit - marks[i].w / 2)
    limit = xs[i] - marks[i].w / 2 - 12
  }
  return {
    runX: new Map(marks.flatMap((m, i) => (m.run !== null ? [[m.run, round(xs[i])] as const] : []))),
    relayX: g.relays.map((_, i) => round(xs[marks.findIndex((m) => m.relay === i)])),
  }
}

export function stageLayout(g: StageInput, shape: StageShape = stageShape(null, g)): StageLayout {
  const { width, compact } = shape
  const plan = planOf(g, compact)
  const n = columnsOf(plan)
  const step = n ? Math.min(ST_MAX, Math.max(ST_MIN, (width - fixedOf(plan)) / n)) : ST_MIN
  const yOf = (row: number) => round(ST_TOP + NODE_H / 2 + row * ST_ROW)
  const bands: StageBand[] = [{ key: 'init', x1: ST_PAD, x2: ST_PAD + ST_INIT, folded: false }]
  const columns: StageColumn[] = []
  const boxes = new Map<string, StageBox>()
  let units: StageLayout['units'] = []
  let unitPill: StageLayout['unitPill'] = null
  let x = ST_PAD + ST_INIT
  for (const b of plan.bands) {
    x += ST_GAP
    const w = b.folded ? ST_FOLD : b.cols.length * step
    bands.push({ key: b.key, x1: round(x), x2: round(x + w), folded: b.folded })
    b.cols.forEach((c, i) => {
      const cx = round(x + step / 2 + i * step)
      const top = (plan.rows - c.span) / 2
      const cells = [...c.cells].sort((p, q) => p.slot - q.slot)
      for (const cell of cells) boxes.set(cell.id, { id: cell.id, col: columns.length, x: cx, y: yOf(top + cell.slot) })
      columns.push({ x: cx, phase: b.key, ids: cells.map((cell) => cell.id) })
    })
    if (b.key === 'construction' && !b.folded && plan.units.length > 1) {
      unitPill = { x: round(x + w / 2), y: 72, n: plan.units.length }
      const top = (plan.rows - plan.units.length) / 2
      if (!compact) units = plan.units.map((unit, u) => ({ unit, x: round(x + 10), y: round(yOf(top + u) - NODE_H / 2 - 7) }))
    }
    x += w
  }
  const orch = { x: round(ST_PAD + ST_INIT / 2), y: ST_TOP - 6 + ST_ORCH.h / 2 }
  let ry = orch.y + ST_ORCH.h / 2 + 8
  const roster = g.roster.map((r) => {
    const h = ROSTER_H[r.kind]
    const y = round(ry + (r.kind === 'head' || r.kind === 'brain-head' ? h - 9 : h / 2))
    ry += h
    return { kind: r.kind, run: r.run, y }
  })
  const bottom = round(Math.max(yOf(plan.rows - 1) + NODE_H / 2 + ST_UNDER + ST_FOOT, ry + 14))
  // the ring's 检查 sits under the bands' foot lines, not on the last one
  const station = { x: width - 42, y: bottom + 50, r: STATION_R }
  const rail = { x1: ST_PAD + 14, x2: station.x - STATION_R, y: station.y }
  const usage = new Map<string, number>()
  const edges = stageWay(g.nodes.filter((nd) => boxes.has(nd.id))).map(([from, to]): StageEdge => {
    const b = boxes.get(to)!
    const path = from === ORCH
      ? [sCurve({ x: orch.x + ST_ORCH.w / 2, y: orch.y }, { x: b.x - NODE_W / 2, y: b.y })]
      : stageRoute(boxes.get(from)!, b, columns, boxes, usage)
    return { from, to, path, d: pathD(path) }
  })
  return { width, compact, height: station.y + 44, bands, columns, boxes, edges, units, unitPill, orch, roster, bottom, rail, station,
    ...railMarks(g, boxes, rail) }
}

/** A run's command line (the reference's dashed orchestrator line): out of its row in the orchestrator's list, up to
 * the bus under the band titles, along it, and into the box it is on: from above when it is its column's top box,
 * else down the gap left of its column into its left side. *lane* lifts the bus of a second line clear of the first. */
export function stageCommandPath(lay: StageLayout, run: string, node: string, lane = 0): { path: Path; bus: { x1: number; x2: number; y: number } } | null {
  const row = lay.roster.find((r) => r.kind === 'run' && r.run === run)
  const b = lay.boxes.get(node)
  if (!row || !b) return null
  const y = ST_BUS - lane * 7
  const p0 = { x: lay.orch.x + ST_ORCH.w / 2, y: row.y }
  const up = { x: p0.x + 26, y }
  if (lay.columns[b.col].ids[0] === node) {
    const over = { x: b.x - 44, y }
    return { path: [sCurve(p0, up), line(up, over), turnDown(over, { x: b.x, y: b.y - NODE_H / 2 })], bus: { x1: up.x, x2: over.x, y } }
  }
  const gap = b.x - NODE_W / 2 - 14
  const turn = { x: gap - 18, y }
  const down = { x: gap, y: b.y - 18 }
  return { path: [sCurve(p0, up), line(up, turn), turnDown(turn, down), turnRight(down, { x: b.x - NODE_W / 2, y: b.y })], bus: { x1: up.x, x2: turn.x, y } }
}

/** The box an anchor of the stage graph takes up (a blip pulses around it): ORCH, RUN (the rail's head), TESTS, a
 * hand-over's pill, a box. */
export function stageAnchorBox(lay: StageLayout, a: Anchor, head: number): (Pt & { w: number; h: number; rx: number }) | null {
  if (a === TESTS) return { x: lay.station.x, y: lay.station.y, w: lay.station.r * 2, h: lay.station.r * 2, rx: lay.station.r }
  if (a === RUN) return { x: head, y: lay.rail.y, w: 22, h: 22, rx: 11 }
  if (a === ORCH) return { ...lay.orch, w: ST_ORCH.w, h: ST_ORCH.h, rx: 18 }
  if (a.startsWith('relay:')) {
    const x = lay.relayX[Number(a.slice(6))]
    return x === undefined ? null : { x, y: lay.rail.y, w: RELAY_W, h: 26, rx: 13 }
  }
  const b = lay.boxes.get(a)
  return b ? { x: b.x, y: b.y, w: NODE_W, h: NODE_H, rx: 16 } : null
}

/** The route a particle takes on the stage graph; null when it does not travel (a blip on *to*). */
export function stageParticlePath(lay: StageLayout, from: Anchor, to: Anchor, head: number): Path | null {
  if (from === to) return null
  const st = lay.station
  if (from === RUN && to === TESTS) return [line({ x: Math.min(head, st.x - st.r - 20), y: lay.rail.y }, { x: st.x - st.r, y: lay.rail.y })]
  const e = lay.edges.find((g) => g.from === from && g.to === to)
  if (e) return e.path
  const a = stageAnchorBox(lay, from, head)
  const b = stageAnchorBox(lay, to, head)
  return a && b ? [sCurve(a, b)] : null
}

/** The run branch's head on the rail: the dot of the run that committed last, else a little way in from its start. */
export function stageHeadX(lay: StageLayout, belt: ReadonlyArray<{ kind: string; run: string | null }>): number {
  for (let i = belt.length - 1; i >= 0; i--) {
    const run = belt[i].kind === 'merge' ? null : belt[i].run
    const x = run ? lay.runX.get(run) : undefined
    if (x !== undefined) return x
  }
  return lay.rail.x1 + 120
}
