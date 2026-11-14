/**
 * 回放: a requirement played again (the sample team board's timeline.ts). The board at any moment *t*, rebuilt from the
 * snapshot itself: each node's status history, its rounds started by then (one that ended later is still open), its
 * gates (one decided later waits while its node waited then), spans and marks up to *t*; each run where its open round
 * was then; each hand-over's steps done by then; the events, the conveyor, the check cells decided by then, Lean's
 * progress at its stage records, whom it waited for then. Runs and hand-overs are never left out, so the graph keeps
 * its picture. What has no time of its own (the decisions, what the Brain was cited for, credits) is not shown until
 * the end. Pure: the same snapshot and *t* always give the same board, so the replay can be scrubbed.
 *
 * A requirement takes hours or days with long quiet gaps (a night, a wait for a person): the replay's clock is
 * compressed — every quiet gap plays as at most GAP_S — so what happened is what plays, and the bar still says the
 * real time.
 */
import type { ActorStatus, CheckCell, Gate, Relay, ReqBoard, ReqState, Round, Run, StageNode, WaitWho } from '../types'
import { ownerIsYou } from './format'

export interface ReplaySpan {
  start: number
  end: number
}

/** A quiet gap between two events plays as at most this many seconds of the requirement's time. */
export const GAP_S = 900

/** When the requirement happened: its first event to its end (or its last event). */
export function replaySpan(b: ReqBoard): ReplaySpan {
  const ats = b.events.map((e) => e.at)
  const start = Math.min(b.started_at ?? Infinity, ...ats)
  const s = Number.isFinite(start) ? start : b.at
  const last = ats.length ? Math.max(...ats) : s
  return { start: s, end: Math.max(b.ended_at ?? last, last, s + 1) }
}

/** The compressed clock: real time ↔ play time (seconds from the start, quiet gaps cut to GAP_S). */
export interface Clock {
  /** total play time */
  total: number
  /** real time → play time */
  toPlay: (t: number) => number
  /** play time → real time */
  toReal: (p: number) => number
}

export function replayClock(b: ReqBoard, gap = GAP_S): Clock {
  const span = replaySpan(b)
  const pts = [...new Set([span.start, ...b.events.map((e) => e.at).filter((t) => t >= span.start && t <= span.end), span.end])].sort((x, y) => x - y)
  // knots: (real, play)
  const knots: [number, number][] = [[pts[0], 0]]
  for (let i = 1; i < pts.length; i++) {
    const d = pts[i] - pts[i - 1]
    knots.push([pts[i], knots[i - 1][1] + Math.min(d, gap)])
  }
  const total = knots[knots.length - 1][1]
  const toPlay = (t: number) => {
    if (t <= knots[0][0]) return 0
    for (let i = 1; i < knots.length; i++) {
      const [r1, p1] = knots[i]
      const [r0, p0] = knots[i - 1]
      if (t <= r1) return r1 > r0 ? p0 + ((t - r0) / (r1 - r0)) * (p1 - p0) : p1
    }
    return total
  }
  const toReal = (p: number) => {
    if (p <= 0) return knots[0][0]
    for (let i = 1; i < knots.length; i++) {
      const [r1, p1] = knots[i]
      const [r0, p0] = knots[i - 1]
      if (p <= p1) return p1 > p0 ? r0 + ((p - p0) / (p1 - p0)) * (r1 - r0) : r1
    }
    return knots[knots.length - 1][0]
  }
  return { total, toPlay, toReal }
}

const OPEN: Round['outcome'] = { kind: 'open', at: null, gate: null, by: null, decision: null, text: null, sha: null, subject: null, checks: [], pipeline: null }
const DECIDED: ReadonlySet<Gate['state']> = new Set<Gate['state']>(['approved', 'sent_back', 'refused'])

/** A node at *t*: its state then (before its first change it waits: with the line it had while waiting, if it still
 * waits, else the plain words; a stage never on this way stays skipped), the rounds started by then, its gates then. */
function nodeAt(n: StageNode, t: number, owner: WaitWho, committed: (sha: string) => boolean): { node: StageNode; who: WaitWho | null } {
  const h = [...n.history].reverse().find((x) => x.at <= t)
  const off = n.status === 'skipped' && !n.history.length
  // in its last state it is as the board says it (since: when the fold says that state began)
  const last = h !== undefined && h === n.history[n.history.length - 1]
  const now = h ? { status: h.status, doing: h.doing, since: last ? n.since : h.at } : off ? { status: n.status, doing: n.doing, since: null } : { status: 'waiting' as ActorStatus, doing: n.status === 'waiting' ? n.doing : '还没轮到', since: null }
  const waits = now.status === 'awaiting_approval'
  const who = waits ? h?.who ?? owner : null
  const rounds = n.rounds.filter((r) => r.started <= t).map((r): Round => (r.ended !== null && r.ended <= t ? r
    : { ...r, ended: null, outcome: OPEN, note: null, outputs: [], commits: r.commits.filter(committed) }))
  const had = new Set(rounds.map((r) => r.n))
  const gates = n.gates.flatMap((g): Gate[] => {
    if (!had.has(g.round)) return []
    if (DECIDED.has(g.state) && g.at !== null && g.at <= t) return [g]
    // asked then and decided later, or still open: it waits while its node waits; nothing to type in a replay
    return waits ? [{ ...g, state: g.state === 'queued' ? 'queued' : 'pending', at: now.since, who: who, by: null, line: null, text: g.state === 'queued' ? g.text : null }] : []
  })
  return {
    node: {
      ...n, ...now, rounds, gates,
      history: n.history.filter((x) => x.at <= t),
      spans: n.spans.filter((x) => x.a <= t).map((x) => ({ ...x, b: x.b !== null && x.b <= t ? x.b : null })),
      marks: n.marks.filter((m) => m.at <= t),
      run: rounds.length ? rounds[rounds.length - 1].run : null,
    },
    who,
  }
}

/** A run at *t*: at the node it had an open round on then (that node's state), done once it had rounds and none is
 * open, else not started; nothing to do in a replay. */
function runAt(r: Run, nodes: StageNode[], waitOn: Map<string, WaitWho>, t: number, sessionAt: (slot: string, kind: Run['kind']) => number, claimed: boolean): Run {
  const open = nodes.flatMap((n) => n.rounds.filter((x) => x.run === r.key && x.ended === null).map((x) => ({ n, started: x.started })))
  const at = open.sort((p, q) => q.started - p.started)[0]?.n
  const did = nodes.some((n) => n.rounds.some((x) => x.run === r.key))
  const now: Run['now'] = at
    ? { status: at.status, doing: at.doing, since: at.since, who: waitOn.get(at.id) ?? null, node: at.id, do: null }
    : { status: did ? 'done' : 'waiting', doing: did ? '做完这一段' : claimed && r.kind === 'local' ? '认领了，还没开工' : '还没开始', since: null, who: null, node: null, do: null }
  return { ...r, now, credits: null, sessions: r.sessions.filter((x) => sessionAt(x.slot, r.kind) <= t) }
}

/** A hand-over at *t*: the steps taken by then; the rest not reached yet. */
function relayAt(r: Relay, t: number): Relay {
  return { ...r, to: r.at <= t ? r.to : null, steps: r.steps.map((x) => (x.at !== null && x.at <= t ? x : { ...x, status: 'waiting', at: null, text: '还没到这一步', todo: null })) }
}

/** The board of *b* as it stood at *t* (epoch seconds, between its start and end). Nothing can be done in a replay:
 * no line to type, no step to take. */
export function replayAt(b: ReqBoard, t: number): ReqBoard {
  const span = replaySpan(b)
  if (t >= span.end) return b
  const owner: WaitWho = b.person.me ? 'you' : 'people'
  const committed = new Set(b.belt.filter((x) => x.at <= t).map((x) => x.sha))
  const known = new Set(b.belt.map((x) => x.sha))
  const rewound = b.nodes.map((n) => nodeAt(n, t, owner, (sha) => committed.has(sha) || !known.has(sha)))
  const nodes = rewound.map((x) => x.node)
  // whom each node's wait was on then
  const waitOn = new Map<string, WaitWho>(rewound.flatMap((x) => (x.who ? [[x.node.id as string, x.who] as const] : [])))
  const kindOf = new Map<string, Run['kind']>(b.runs.map((r) => [r.key as string, r.kind]))
  // a session is there from when it was opened: the local one at the board's 开了本机会话 (else its own time), never before
  // 开工 (a claim has no session); the runner's at its own time, else at the runner's first round. What it cost is known
  // at the end.
  const startAt = b.events.find((e) => e.kind === 'start')?.at ?? -Infinity
  const localAt = b.events.find((e) => e.kind === 'session')?.at
  const first = (kind: 'local' | 'runner') => Math.min(Infinity, ...b.nodes.flatMap((n) => n.rounds.filter((r) => (kindOf.get(r.run) === 'runner') === (kind === 'runner')).map((r) => r.started)))
  const openedAt = (x: { kind: string; opened_at: number | null }) =>
    x.kind === 'runner' ? x.opened_at ?? first('runner') : Math.max(localAt ?? x.opened_at ?? first('local'), startAt)
  const sessionAt = (slot: string, kind: Run['kind']) => openedAt({ kind: kind === 'runner' ? 'runner' : 'local', opened_at: b.sessions.find((s) => s.slot === slot)?.opened_at ?? null })
  const relays = b.relays.map((r) => relayAt(r, t))
  const statusOf = new Map(nodes.map((n) => [n.id as string, n.status]))
  const cells: CheckCell[] = b.checks.cells.map((c) => {
    if (c.at !== null && c.at <= t) return c
    if (c.at === null && c.status === 'wait' && c.node && statusOf.get(c.node) === 'awaiting_approval') return c
    return { ...c, status: 'skip', at: null, detail: '还没到这一步' }
  })
  const passed = cells.filter((c) => c.status === 'pass').length
  const failed = cells.filter((c) => c.status === 'fail').length
  const asking = nodes.filter((n) => n.status === 'awaiting_approval')
  const awaiting = asking.find((n) => waitOn.get(n.id) === 'you') ?? asking[0]
  const working = nodes.filter((n) => n.status === 'running' || n.status === 'fixing')
  const merged = b.ended_at !== null && b.ended_at <= t
  // only claimed: nothing but the claim had happened
  const claimed = !merged && !b.events.some((e) => e.kind !== 'claim' && e.at <= t)
  const runs = b.runs.map((r) => runAt(r, nodes, waitOn, t, sessionAt, claimed))
  const state = stateAt(b, t, statusOf.get('release'), runs, relays, merged)
  // Lean's progress then, as the backend counted it at its stage records (one rule: backend/core/lean_view.py)
  const lean = [...b.lean_steps].reverse().find((s) => s.at <= t)?.lean ?? b.row.lean
  const byRunner = working.some((n) => kindOf.get(n.rounds[n.rounds.length - 1]?.run ?? '') === 'runner')
  const who: WaitWho = awaiting ? waitOn.get(awaiting.id) ?? 'people' : working.length ? (byRunner ? 'runner' : 'agent') : 'nobody'
  // claimed, as a live claim of that owner shows it (deliveries' row, board.py's person): no run branch, no working
  // directory, it waits on its owner to start it
  const claimAt = b.events.find((e) => e.kind === 'claim')?.at ?? null
  const starts = ownerIsYou(b) ? '已认领，还没开工' : `等 ${b.person.owner} 开工`
  return {
    ...b,
    row: {
      ...b.row, state, state_label: STATE_LABEL[state], credits: null,
      // the MR is opened at the hand-over
      mr: b.events.some((e) => e.kind === 'handover' && e.at <= t) ? b.row.mr : null,
      ...(claimed ? { branch: null, worktree: null } : {}),
      lean,
      waits: claimed
        ? { ...b.row.waits, who: ownerIsYou(b) ? 'you' : 'people', text: starts, line: null, raw: '', do: 'start', since: claimAt }
        : { ...b.row.waits, who, text: awaiting ? awaiting.doing : working.length ? 'AI 在做' : '', line: null, raw: '', do: 'none', since: awaiting?.since ?? null },
    },
    branch: claimed ? null : b.branch,
    ended_at: merged ? b.ended_at : null,
    live: working.length > 0,
    // the owner's node asks only when the wait is on them; one on the product or someone else is said (as the live board)
    person: claimed
      ? { ...b.person, status: ownerIsYou(b) ? 'awaiting_approval' : 'waiting', doing: starts }
      : { ...b.person, status: who === 'you' ? 'awaiting_approval' : working.length && !awaiting ? 'running' : 'waiting', doing: awaiting ? awaiting.doing : working.length ? 'AI 在做' : '没有等你的事' },
    nodes,
    runs,
    relays,
    events: b.events.filter((e) => e.at <= t),
    belt: b.belt.filter((x) => x.at <= t),
    sessions: b.sessions.filter((x) => openedAt(x) <= t),
    credits: { ...b.credits, total: null },
    decisions: [],
    brain: { ...b.brain, cited: [], stale: [], unknown: [] },
    checks: { ...b.checks, cells, passed, failed, total: cells.filter((c) => c.status === 'pass' || c.status === 'fail' || c.status === 'wait').length, todo: undefined,
      at: Math.max(0, ...cells.filter((c) => c.at !== null).map((c) => c.at as number)) || null,
      history: b.checks.history.filter((x) => x.at <= t) },
    accept: { ...b.accept, state: acceptAt(nodes.find((n) => n.id === 'release')), step: { text: '', who: '', line: null, mr_url: null, session_url: null }, done: null },
  }
}

const STATE_LABEL: Record<ReqState, string> = {
  claimed: '已认领', local: '本地开发中', handed_over: '已交接', rejected: '接手被拒', running: '运行机在跑', parked: '停下等回答',
  acceptance: '等验收', merged: '已合并', closed: '已关闭', unclaimed: '待认领', merged_into_parent: '已合进大需求',
}

/** 验收 then, by 发布's latest gate: approved, sent back, asked; none decided or asked: as 发布 waits or not. */
function acceptAt(release: StageNode | undefined): ReqBoard['accept']['state'] {
  const g = release?.gates[release.gates.length - 1]
  if (g?.state === 'approved') return 'accepted'
  if (g?.state === 'sent_back' || g?.state === 'refused') return release?.status === 'awaiting_approval' ? 'waiting' : 'rejected'
  return release?.status === 'awaiting_approval' ? 'waiting' : 'not_yet'
}

/** The requirement's state at *t* in a replay (the header's chip): from what had happened by then. */
function stateAt(b: ReqBoard, t: number, release: ActorStatus | undefined, runs: Run[], relays: Relay[], merged: boolean): ReqState {
  if (merged) return b.row.state === 'closed' ? 'closed' : 'merged'
  const seen = (kind: string) => b.events.some((e) => e.kind === kind && e.at <= t)
  if (release === 'awaiting_approval') return 'acceptance'
  if (!seen('handover')) return seen('start') || b.events.some((e) => e.kind !== 'claim' && e.at <= t) ? 'local' : 'claimed'
  const relay = [...relays].reverse().find((r) => r.at <= t)
  if (relay?.steps.some((x) => x.status === 'failed')) return 'rejected'
  const runner = runs.filter((r) => r.kind === 'runner')
  if (runner.some((r) => r.now.status === 'awaiting_approval')) return 'parked'
  return runner.some((r) => r.now.status === 'running' || r.now.status === 'fixing') ? 'running' : 'handed_over'
}
