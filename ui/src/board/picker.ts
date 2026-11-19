/**
 * The run picker (选择运行): the runs a requirement had, and the board as one of them saw it. The board's payload is one
 * and every round, lane span, event, commit and check cell says its run, so a pick is a filter on the page. Nothing is
 * renumbered: a round keeps its 第 N 轮 and an event its place in the whole way.
 */
import type { CheckWall, ReqBoard, Round, Run, RunKey, StageNode } from '../types'
import { RUN_ICON, actorMeta, clockAt, gateVerb, openGate, runShort, sessionOf } from './format'
import { through, wayAt } from './way'

export type RunFilter = 'all' | RunKey

/** One row of the picker's menu: 全部, a run, a run on another branch (greyed: not read into this board). */
export interface PickRow {
  key: RunFilter | null
  icon: string
  title: string
  /** 2 次运行 · 3 个阶段 · 没有 */
  count: string
  /** 10:05–11:05 · 需求分析、应用设计、拆分单元 · 做完 · 交出去了 */
  sub: string
}

/** The stages brain records by other means than Lean (types.ts NodeId): not counted on the AI-DLC Lean card. */
export const NOT_LEAN: ReadonlySet<string> = new Set(['review', 'reflect'])
/** a runner run's agent when its session is not here (bridge.json's kirocrew.agent); a local one names the agent of its
 * session here (brain-dev-crew, or brain-dev for one opened before brain-kit renamed it), else none */
const AGENT: Partial<Record<Run['kind'], string>> = { runner: 'brain-run' }
const OVER: ReadonlySet<string> = new Set(['done', 'merged', 'skipped'])

const mine = (run: RunFilter) => (x: { run: RunKey | null }) => run === 'all' || x.run === run

/** The box has a round of *run* (under 全部: always): the graph dims the others. */
export const hasRound = (n: StageNode, run: RunFilter): boolean => run === 'all' || n.rounds.some(mine(run))

/** The picked run, when the board has it (a link to a run it does not have shows the whole way). */
export function pickedRun(b: ReqBoard, run: RunFilter): Run | null {
  return run === 'all' ? null : b.runs.find((r) => r.key === run) ?? null
}

/** The stages *run* did (not skipped), in the way's order, with the most rounds it did of each (a unit's stage once). */
function stagesOf(b: ReqBoard, run: RunKey): { title: string; rounds: number }[] {
  const out = new Map<string, number>()
  for (const n of b.nodes) {
    const k = n.rounds.filter((r) => r.run === run && r.outcome.kind !== 'skipped').length
    if (k) out.set(n.title, Math.max(k, out.get(n.title) ?? 0))
  }
  return [...out].map(([title, rounds]) => ({ title, rounds }))
}

/** What a run is at now, short: the gate it asked (等产品验收), else its own words. */
function runNow(run: Run, b: ReqBoard): string {
  const g = run.now.node ? openGate(b.nodes.find((n) => n.id === run.now.node) ?? { gates: [] }) : null
  return g ? gateVerb(g, b) : run.now.doing || actorMeta(run.now.status).label
}

/** The phases a run worked in, by name, in order (启动 · 构建 · 发布). */
function phasesOf(b: ReqBoard, run: RunKey): string[] {
  const names = new Map(b.phases.map((p) => [p.key, p.title.split(' · ')[0]]))
  const did = new Set(b.nodes.filter((n) => n.rounds.some((r) => r.run === run && r.outcome.kind !== 'skipped')).map((n) => n.phase))
  return b.phases.filter((p) => did.has(p.key)).map((p) => names.get(p.key) ?? p.key)
}

export function pickRows(b: ReqBoard): PickRow[] {
  const way = b.runs.map((r, i) => `${runShort(r, b)}${i ? '接着' : ''}做${phasesOf(b, r.key).join('、') || '准备'}`)
  const rows: PickRow[] = [{ key: 'all', icon: 'list', title: '全部', count: `${b.runs.length} 次运行`, sub: `需求的整条路${way.length > 1 ? `：${way.join('，')}` : ''}` }]
  for (const r of b.runs) {
    const did = stagesOf(b, r.key)
    const rounds = b.nodes.flatMap((n) => n.rounds.filter((x) => x.run === r.key))
    const from = Math.min(...rounds.map((x) => x.started))
    const to = Math.max(...rounds.map((x) => x.ended ?? x.started))
    const span = !rounds.length ? '' : OVER.has(r.now.status) ? `${clockAt(from, b.at)}–${clockAt(to, b.at).slice(-5)}` : `${clockAt(from, b.at)} 起`
    const stages = did.map((s) => (s.rounds > 1 ? `${s.title} ${s.rounds} 轮` : s.title)).join('、')
    rows.push({
      key: r.key, icon: RUN_ICON[r.kind],
      title: [r.label, r.run_id ?? (sessionOf(r)?.agent || AGENT[r.kind])].filter(Boolean).join(' · '),
      count: did.length ? `${did.length} 个阶段` : '还没有阶段',
      sub: [span, stages, runNow(r, b)].filter(Boolean).join(' · '),
    })
  }
  if (!b.other_runs.length) rows.push({ key: null, icon: 'merge', title: '另一条分支上的运行', count: '没有', sub: '这个需求没在别的分支上重跑过' })
  for (const o of b.other_runs) {
    rows.push({ key: null, icon: 'merge', title: `在 ${o.branch} 上`, count: o.state ?? '', sub: '在另一条分支上，没读进这张看板' })
  }
  return rows
}

/** The board as *run* saw it: its rounds, lane spans, gates and marks on each box (the boxes stay: the graph and the
 * feed name them), its events, commits and check cells (and the requirement's own: the kit, the Brain, the 14 days). */
export function inRun(b: ReqBoard, run: RunFilter): ReqBoard {
  if (run === 'all') return b
  const its = mine(run)
  const nodes = b.nodes.map((n): StageNode => {
    const rounds = n.rounds.filter(its)
    if (rounds.length === n.rounds.length) return n
    const within = (t: number) => rounds.some((r) => t >= r.started && (r.ended === null || t <= r.ended))
    // a round holds its run's commits after it ended too (its work, committed after Lean recorded it over)
    const held = new Set(rounds.flatMap((r) => r.commits))
    const madeAt = new Set(b.belt.filter((x) => x.node === n.id && held.has(x.sha)).map((x) => x.at))
    return { ...n, rounds, spans: n.spans.filter(its), gates: n.gates.filter((g) => rounds.some((r) => r.n === g.round)),
      marks: n.marks.filter((m) => within(m.at) || (m.kind === 'commit' && madeAt.has(m.at))) }
  })
  const cells = b.checks.cells.filter((c) => c.run === run || c.run === null)
  const verify = nodes.find((n) => n.stage === 'build-and-test')?.rounds.filter((r) => r.outcome.kind === 'passed' || r.outcome.kind === 'failed') ?? []
  const checks: CheckWall = {
    ...b.checks, cells,
    passed: cells.filter((c) => c.status === 'pass').length,
    failed: cells.filter((c) => c.status === 'fail').length,
    total: cells.filter((c) => c.status === 'pass' || c.status === 'fail' || c.status === 'wait').length,
    history: verify.map((r) => ({ at: r.ended ?? r.started, passed: r.outcome.checks.filter((c) => c.ok).length, failed: r.outcome.checks.filter((c) => !c.ok).length })),
  }
  // a decision (decisions.md) says no run: the run's answers are its events
  return { ...b, nodes, events: b.events.filter(its), belt: b.belt.filter(its), checks, decisions: [] }
}

export interface RunLean {
  done: number
  skipped: number
  total: number
  /** the box it is on and not through yet */
  at: StageNode | null
  /** its phases it skipped as a whole, by name (构思): said once, not counted */
  whole: string[]
}

/** The AI-DLC Lean card for one run (*b*: inRun's board): the Lean stages it did, a unit's stage once. */
export function runLean(b: ReqBoard): RunLean {
  const did = b.nodes.filter((n) => n.rounds.length > 0 && !NOT_LEAN.has(n.stage))
  const phases = new Map(b.phases.map((p) => [p.key, p.title.split(' · ')[0]]))
  const whole = [...new Set(did.map((n) => n.phase))].filter((p) => did.every((n) => n.phase !== p || n.status === 'skipped'))
  const counted = did.filter((n) => !whole.includes(n.phase))
  const by = new Map<string, StageNode[]>()
  for (const n of counted) by.set(n.stage, [...(by.get(n.stage) ?? []), n])
  const all = [...by.values()]
  return {
    done: all.filter((ns) => ns.every(through)).length,
    skipped: all.filter((ns) => ns.every((n) => n.status === 'skipped')).length,
    total: all.length,
    at: wayAt(did).at,
    whole: whole.map((p) => phases.get(p) ?? p),
  }
}

/** The last round of a box, for a lane's 第 N 轮 (the whole way's number, also under a picked run). */
export const lastRound = (n: StageNode): Round | undefined => n.rounds[n.rounds.length - 1]
