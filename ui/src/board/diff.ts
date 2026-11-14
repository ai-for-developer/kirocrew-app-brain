/**
 * What changed between two consecutive snapshots of one requirement's board (pure; the sample team board's diff.ts),
 * and how each change looks on the stage graph. Every animation on the board comes from here, so every animation
 * stands for a real change:
 *
 * - events newer than the previous snapshot's last `seq` become particles along the graph (开工: 你 → the first stage;
 *   a request: its stage → 你; 批准 / 退回: 你 → the stage, a tick or a cross; a commit: its stage → the run branch;
 *   交接: the last stage this machine did → the hand-over's pill; a question: its stage → 你, the answer back; verify:
 *   the run branch → the check ring, green or red; 回写: 你 → 回写 Brain) or blips on a box (Lean's stages, a blocked
 *   tool call, the merge), and the loud ones also flash their box red;
 * - check cells whose status changed flip; nodes whose status changed get a one-time highlight on their lane; new
 *   commits slide onto the conveyor; the polite live region gets one sentence.
 *
 * The first snapshot is history: nothing in it animates. So is one taken after the page was hidden (a quiet catch-up),
 * one whose events went backwards, and any event more than LATE_S older than the snapshot (it still shows in the feed).
 */
import type { ActorStatus, BeltItem, BoardEvent, CheckCell, ReqBoard } from '../types'
import type { Item, Prio, PTone, Shape } from './engine'
import { outsidePushes } from './format'
import { ORCH, RUN, TESTS, relayAnchor } from './layout'

export const LATE_S = 30
export const MAX_ITEMS = 24

export interface Flash { key: string; target: string; tone: 'bad' | 'ok'; kind: string }
export interface Flip { id: string; from: CheckCell['status']; to: CheckCell['status'] }
export interface NodeChange { id: string; from: ActorStatus; to: ActorStatus }

export interface BoardDiff {
  /** the requirement (repo/key) */
  req: string
  /** history (first snapshot, catch-up, restart): nothing animates */
  first: boolean
  /** the events after the previous snapshot's last seq, in seq order */
  fresh: BoardEvent[]
  /** the fresh ones recent enough to fly (none more than lateS older than the snapshot) */
  flying: BoardEvent[]
  flips: Flip[]
  /** beltKey() of the conveyor items that are new */
  belt: string[]
  nodes: NodeChange[]
  /** it merged in this poll */
  merged: boolean
  /** its checks turned all green in this poll */
  green: boolean
  announce: string | null
}

export interface DiffOptions {
  at?: number
  resync?: boolean
  lateS?: number
}

export const reqId = (b: ReqBoard) => `${b.row.repo}/${b.row.key}`
export const maxSeq = (b: ReqBoard) => b.events.reduce((m, e) => Math.max(m, e.seq), 0)
export const beltKey = (x: BeltItem) => `${x.kind}@${x.sha}@${x.at}`
export const eventKey = (req: string, seq: number) => `${req}#${seq}`

/** Where an event's end (BoardEvent from / to) is on the stage graph: the owner box, the rail's head, the check ring,
 * the pill of the hand-over it belongs to (the latest asked by then), a box; null when nothing is drawn for it (a
 * skipped stage). */
export function anchorOf(place: string | null, at: number, b: Pick<ReqBoard, 'relays'>, boxes: ReadonlySet<string>): string | null {
  if (!place) return null
  if (place === 'person') return ORCH
  if (place === 'branch') return RUN
  if (place === 'checks') return TESTS
  if (place === 'relay') {
    if (!b.relays.length) return null
    const i = b.relays.filter((r) => r.at <= at + 1).length
    return relayAnchor(Math.max(0, i - 1))
  }
  return boxes.has(place) ? place : null
}

/** What an event carries, drawn next to a travelling head. */
const LABEL: Partial<Record<BoardEvent['kind'], string>> = {
  start: '开工', request: '请批准', approve: '批准', reject: '退回', handover: '交接', accepted: '接手', rejected: '拒绝',
  signed: '合同', question: '问你', answer: '回答', reflect: '回写',
}

/** An event between two anchors (anchorOf) as an engine item; null when nothing on the graph stands for it. *push*: a
 * person's commit after the hand-over (a step outside the judgment points: red); *score*: verify's checks now. */
export function eventItem(e: BoardEvent, req: string, from: string | null, to: string | null, o: { score?: { passed: number; total: number } | null; push?: boolean } = {}): Item | null {
  const base = { key: eventKey(req, e.seq), t: e.at, kind: e.kind }
  const go = (a: string | null, b: string | null, tone: PTone, prio: Prio, label: string | null = LABEL[e.kind] ?? null): Item | null => {
    if (a && b && a !== b) return { ...base, from: a, to: b, tone, shape: 'comet', prio, label }
    const at = b ?? a
    return at ? { ...base, from: at, to: at, tone, shape: tone === 'bounce' || tone === 'danger' || tone === 'fail' ? 'cross' : tone === 'pass' || tone === 'ok' ? 'check' : 'ring', prio } : null
  }
  const blip = (at: string | null, tone: PTone, shape: Shape, prio: Prio): Item | null => (at ? { ...base, from: at, to: at, tone, shape, prio } : null)
  switch (e.kind) {
    case 'start':
    case 'session':
      return go(from, to, 'run', e.kind === 'start' ? 2 : 1)
    case 'stage':
      return blip(from ?? to, 'tool', 'spark', 0)
    case 'request':
    case 'question':
      return go(from, to, 'info', 3)
    case 'approve':
      return go(from, to, 'pass', 3)
    case 'reject':
    case 'rejected':
      return go(from, to, 'bounce', 3)
    case 'refused':
      return blip(to ?? from, 'danger', 'cross', 3)
    case 'blocked':
      return blip(from ?? to, 'danger', 'cross', 2)
    case 'commit':
      return go(from, to, o.push ? 'danger' : 'run', o.push ? 3 : 2, null)
    case 'handover':
    case 'accepted':
    case 'signed':
      return go(from, to, 'handoff', 3)
    case 'answer':
      return go(from, to, from === ORCH ? 'ok' : 'handoff', 3)
    case 'verify': {
      const score = o.score
      const green = !!score && score.total > 0 && score.passed === score.total
      return { ...base, from: RUN, to: TESTS, tone: /没过/.test(e.text) ? 'fail' : green || /通过/.test(e.text) ? 'pass' : 'fail', shape: 'comet', prio: 3,
        label: score ? `${score.passed}/${score.total}` : null }
    }
    case 'merge':
      return blip(RUN, 'merge', 'check', 3)
    case 'reflect':
      return go(from, to, 'ok', 2)
    default:
      return blip(from ?? to, 'info', 'ring', 1)
  }
}

/** The flashes an event causes besides its particle: the loud ones are red, on the box they hit (else where they come
 * from). */
export function eventFlashes(e: BoardEvent, req: string, from: string | null, to: string | null): Flash[] {
  const key = eventKey(req, e.seq)
  const at = e.kind === 'blocked' ? from ?? to : e.kind === 'verify' ? TESTS : to ?? from
  const loud = e.kind === 'reject' || e.kind === 'rejected' || e.kind === 'refused' || e.kind === 'blocked' || (e.kind === 'verify' && /没过/.test(e.text))
  return loud && at ? [{ key, target: at, tone: 'bad', kind: e.kind }] : []
}

/** A poll's particles and flashes on the stage graph whose drawn places are *boxes*: each event that may fly between
 * its two anchors (the loudest MAX_ITEMS when more), its loud flashes; on the merge the rail and every merged box flash
 * green, on all checks green the ring. */
export function effects(d: BoardDiff, b: ReqBoard, boxes: ReadonlySet<string>, max = MAX_ITEMS): { items: Item[]; flashes: Flash[] } {
  if (d.first) return { items: [], flashes: [] }
  const cells = b.checks.cells.filter((c) => c.group === 'verify')
  const score = cells.length ? { passed: cells.filter((c) => c.status === 'pass').length, total: cells.length } : null
  const pushes = outsidePushes(b)
  let items: Item[] = []
  const flashes: Flash[] = []
  for (const e of d.flying) {
    const from = anchorOf(e.from, e.at, b, boxes)
    const to = anchorOf(e.to, e.at, b, boxes)
    const push = e.kind === 'commit' && pushes.some((x) => x.run === e.run && Math.abs(x.at - e.at) < 1)
    const it = eventItem(e, d.req, from, to, { score: e.kind === 'verify' ? score : null, push })
    if (it) items.push(it)
    flashes.push(...eventFlashes(e, d.req, from, to))
  }
  if (d.merged) {
    flashes.push({ key: `${d.req}#merged-run`, target: RUN, tone: 'ok', kind: 'merge' })
    for (const n of b.nodes) if (n.status === 'merged' && boxes.has(n.id)) flashes.push({ key: `${d.req}#merged`, target: n.id, tone: 'ok', kind: 'merge' })
  }
  if (d.green) flashes.push({ key: `${d.req}#green@${b.checks.at ?? ''}`, target: TESTS, tone: 'ok', kind: 'verify' })
  if (items.length > max) items = items.slice().sort((x, y) => y.prio - x.prio || x.t - y.t).slice(0, max).sort((x, y) => x.t - y.t)
  return { items, flashes }
}

function quiet(req: string): BoardDiff {
  return { req, first: true, fresh: [], flying: [], flips: [], belt: [], nodes: [], merged: false, green: false, announce: null }
}

export function diffBoards(prev: ReqBoard | null, next: ReqBoard, opts: DiffOptions = {}): BoardDiff {
  const lateS = opts.lateS ?? LATE_S
  const req = reqId(next)
  if (!prev || reqId(prev) !== req || opts.resync) return quiet(req)
  const before = maxSeq(prev)
  if (maxSeq(next) < before) return quiet(req)
  const fresh = next.events.filter((e) => e.seq > before).sort((a, b) => a.seq - b.seq)
  const flying = fresh.filter((e) => opts.at === undefined || opts.at - e.at <= lateS)
  const merged = prev.ended_at === null && next.ended_at !== null
  const c = next.checks
  const green = c.total > 0 && c.failed === 0 && c.passed === c.total && (prev.checks.failed > 0 || prev.checks.passed !== c.passed)
  const was = new Map(prev.checks.cells.map((x) => [x.id, x.status]))
  const flips: Flip[] = c.cells.filter((x) => was.has(x.id) && was.get(x.id) !== x.status).map((x) => ({ id: x.id, from: was.get(x.id)!, to: x.status }))
  const wasN = new Map(prev.nodes.map((n) => [n.id as string, n.status]))
  const nodes: NodeChange[] = next.nodes.filter((n) => wasN.has(n.id) && wasN.get(n.id) !== n.status).map((n) => ({ id: n.id, from: wasN.get(n.id)!, to: n.status }))
  const had = new Set(prev.belt.map(beltKey))
  const belt = next.belt.map(beltKey).filter((k) => !had.has(k))
  return { req, first: false, fresh, flying, flips, belt, nodes, merged, green, announce: announce(prev, next, fresh) }
}

/** One sentence for the polite live region: the most important change of this poll (or null). */
function announce(prev: ReqBoard, next: ReqBoard, fresh: BoardEvent[]): string | null {
  const parts: string[] = []
  if (prev.row.state !== next.row.state) parts.push(`需求${next.row.state_label}`)
  if (prev.checks.passed !== next.checks.passed || prev.checks.total !== next.checks.total)
    parts.push(`检查 ${prev.checks.passed}/${prev.checks.total} → ${next.checks.passed}/${next.checks.total}`)
  const loud = [...fresh].reverse().find((e) => ['reject', 'rejected', 'refused', 'question', 'request'].includes(e.kind))
  if (loud) parts.push(loud.text)
  if (parts.length < 2) {
    const calm = [...fresh].reverse().find((e) => ['approve', 'handover', 'accepted', 'merge', 'answer'].includes(e.kind))
    if (calm) parts.push(calm.text)
  }
  return parts.length ? parts.slice(0, 2).join('；') : null
}
