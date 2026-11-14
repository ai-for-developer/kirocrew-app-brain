import { describe, expect, it } from 'vitest'
import { fx } from '../test/data'
import type { ReqBoard } from '../types'
import { GAP_S, replayAt, replayClock, replaySpan } from './timeline'

// board-merged.json (PAY-2): claimed at START, merged at MERGE, written back into the Brain at END (its last event)
const START = 1793620800
const BEGIN = 1793628000 // 开工, two quiet hours after the claim
const MERGE = 1793661540
const END = 1793715540
const RELEASE_ASKED = 1793655180
const RELEASE_OK = 1793659500
const VERIFY_OK = 1793654580

/** What only grows while a replay plays forward. */
function counts(r: ReqBoard) {
  return {
    events: r.events.length,
    belt: r.belt.length,
    passed: r.checks.passed,
    rounds: r.checks.history.length,
    started: r.nodes.filter((n) => n.status !== 'waiting').length,
    nodeRounds: r.nodes.reduce((k, n) => k + n.rounds.length, 0),
    ended: r.nodes.reduce((k, n) => k + n.rounds.filter((x) => x.ended !== null).length, 0),
    gates: r.nodes.reduce((k, n) => k + n.gates.filter((g) => g.state !== 'pending').length, 0),
    spans: r.nodes.reduce((k, n) => k + n.spans.length, 0),
    closed: r.nodes.reduce((k, n) => k + n.spans.filter((x) => x.b !== null).length, 0),
    marks: r.nodes.reduce((k, n) => k + n.marks.length, 0),
    steps: r.relays.reduce((k, x) => k + x.steps.filter((y) => y.at !== null).length, 0),
  }
}

/** A board with nothing on record: no events, never started. */
function noTrace(): ReqBoard {
  const b = fx.board()
  b.events = []
  b.started_at = null
  b.ended_at = null
  return b
}

describe('replaySpan', () => {
  it('runs from the claim to the last event, which may come after the merge (the 回写)', () => {
    const b = fx.boardMerged()
    expect(b.ended_at).toBe(MERGE)
    expect(replaySpan(b)).toEqual({ start: START, end: END })
  })

  it('ends at the last event of a requirement still going (not at the snapshot time)', () => {
    const b = fx.board()
    expect(replaySpan(b)).toEqual({ start: 1794038400, end: 1794146040 })
    expect(b.at).toBe(1794146400)
  })

  it('starts at started_at when that is before the first event', () => {
    const b = fx.boardMerged()
    b.started_at = START - 600
    expect(replaySpan(b).start).toBe(START - 600)
  })

  it('a board with nothing on record gets a one-second span at its snapshot time', () => {
    const b = noTrace()
    expect(replaySpan(b)).toEqual({ start: b.at, end: b.at + 1 })
  })
})

describe('replayClock', () => {
  it('cuts every quiet gap to GAP_S: the two hours before 开工 and the 15 hours after the merge', () => {
    const clk = replayClock(fx.boardMerged())
    expect(clk.toPlay(START)).toBe(0)
    expect(clk.toPlay(BEGIN)).toBe(GAP_S)
    expect(clk.toPlay(END) - clk.toPlay(MERGE)).toBe(GAP_S)
    expect(clk.total).toBe(22080)
    expect(clk.total).toBeLessThan(END - START)
  })

  it('plays each step between two events as its real length, at most GAP_S', () => {
    const b = fx.boardMerged()
    const clk = replayClock(b)
    const ats = [...new Set(b.events.map((e) => e.at))].sort((x, y) => x - y)
    for (let i = 1; i < ats.length; i++) expect(clk.toPlay(ats[i]) - clk.toPlay(ats[i - 1])).toBeCloseTo(Math.min(ats[i] - ats[i - 1], GAP_S), 6)
  })

  it('is clamped at the span\'s ends both ways', () => {
    const clk = replayClock(fx.boardMerged())
    expect(clk.toPlay(START - 3600)).toBe(0)
    expect(clk.toPlay(END + 3600)).toBe(clk.total)
    expect(clk.toReal(-5)).toBe(START)
    expect(clk.toReal(0)).toBe(START)
    expect(clk.toReal(clk.total)).toBe(END)
    expect(clk.toReal(clk.total + 5)).toBe(END)
  })

  it('toReal undoes toPlay at every event', () => {
    const b = fx.boardMerged()
    const clk = replayClock(b)
    for (const e of b.events) expect(clk.toReal(clk.toPlay(e.at))).toBeCloseTo(e.at, 3)
  })

  it('played at 1×, 2× and 4×, the board time only moves forward, a faster speed is never behind, and it stops at the end', () => {
    const clk = replayClock(fx.boardMerged())
    const rate = clk.total / 60 // the whole requirement in about a minute at 1×
    const at = (elapsed: number, speed: number) => clk.toReal(elapsed * speed * rate)
    for (const speed of [1, 2, 4]) {
      let last = -Infinity
      for (let e = 0; e <= 70; e += 0.25) {
        const t = at(e, speed)
        expect(t).toBeGreaterThanOrEqual(last)
        expect(t).toBeGreaterThanOrEqual(START)
        expect(t).toBeLessThanOrEqual(END)
        if (speed > 1) expect(t).toBeGreaterThanOrEqual(at(e, speed / 2))
        last = t
      }
      expect(at(60 / speed, speed)).toBe(END)
      expect(at(70, speed)).toBe(END)
    }
  })

  it('with no cap on gaps it is the real time from the start', () => {
    const clk = replayClock(fx.boardMerged(), Infinity)
    expect(clk.total).toBe(END - START)
    expect(clk.toPlay(MERGE)).toBe(MERGE - START)
    expect(clk.toReal(MERGE - START)).toBe(MERGE)
  })

  it('a board with nothing on record plays one second, at finite times', () => {
    const b = noTrace()
    const clk = replayClock(b)
    expect(clk.total).toBe(1)
    expect(clk.toReal(0.5)).toBe(b.at + 0.5)
    expect(clk.toReal(clk.total)).toBe(b.at + 1)
  })
})

describe('replayAt', () => {
  it('at the start nothing has happened yet but the claim, and it looks as a live claim of that owner does', () => {
    const b = fx.boardMerged()
    const r = replayAt(b, START)
    expect(r.events.map((e) => [e.seq, e.kind])).toEqual([[1, 'claim']])
    expect(r.belt).toEqual([])
    for (const n of r.nodes)
      expect({ id: n.id, status: n.status, doing: n.doing, since: n.since, spans: n.spans, marks: n.marks, rounds: n.rounds, gates: n.gates, run: n.run })
        .toEqual({ id: n.id, status: 'waiting', doing: '还没轮到', since: null, spans: [], marks: [], rounds: [], gates: [], run: null })
    // the runs and the hand-over stay (the graph keeps its picture), none started yet
    expect(r.runs.map((x) => [x.key, x.now.status, x.now.doing, x.now.node])).toEqual([['local', 'waiting', '认领了，还没开工', null], ['runner-1', 'waiting', '还没开始', null]])
    expect(r.relays.map((x) => [x.to, x.steps.map((y) => [y.status, y.at, y.text])])).toEqual([[null, b.relays[0].steps.map(() => ['waiting', null, '还没到这一步'])]])
    expect(r.checks.cells.map((c) => [c.status, c.at, c.detail])).toEqual(b.checks.cells.map(() => ['skip', null, '还没到这一步']))
    expect(r.checks).toMatchObject({ passed: 0, failed: 0, total: 0, at: null, history: [] })
    // no run branch and no working directory before 开工; it waits on its owner (alice, looking) to start it
    expect([r.branch, r.row.branch, r.row.worktree]).toEqual([null, null, null])
    expect(r.row.waits).toMatchObject({ who: 'you', do: 'start', text: '已认领，还没开工', since: START })
    expect(r.person).toMatchObject({ status: 'awaiting_approval', doing: '已认领，还没开工' })
    expect(r.live).toBe(false)
    expect(r.ended_at).toBeNull()
    expect(r.accept.state).toBe('not_yet')
  })

  it('before its first change a node that still waits keeps its own line; one that moves on later says the plain words; one never on the way stays skipped', () => {
    const b = fx.board()
    const r = replayAt(b, replaySpan(b).start)
    const node = (id: string) => r.nodes.find((x) => x.id === id)
    const still = b.nodes.filter((n) => n.status === 'waiting')
    expect(still.map((n) => n.id)).toEqual(['build-and-test', 'review', 'release', 'reflect'])
    for (const n of still) expect(node(n.id)).toMatchObject({ status: 'waiting', doing: n.doing, since: null })
    for (const n of b.nodes.filter((x) => x.status !== 'waiting' && x.history.length)) expect(node(n.id)).toMatchObject({ status: 'waiting', doing: '还没轮到', since: null })
    for (const n of b.nodes.filter((x) => x.status === 'skipped' && !x.history.length)) expect(node(n.id)?.status).toBe('skipped')
    expect(r.runs[0].now).toMatchObject({ status: 'waiting', doing: '认领了，还没开工', node: null })
  })

  it('someone else’s claim, replayed, waits on them as their live claim does (等 erin 开工), with no run branch yet', () => {
    const b = fx.boardMerged()
    b.person = { ...b.person, owner: 'erin', me: false, you: false }
    const r = replayAt(b, START + 1)
    expect(r.row.state).toBe('claimed')
    expect(r.branch).toBeNull()
    expect(r.row.waits).toMatchObject({ who: 'people', do: 'start', text: '等 erin 开工' })
    expect(r.person).toMatchObject({ status: 'waiting', doing: '等 erin 开工' })
    // once it is started the branch is there again
    expect(replayAt(b, BEGIN + 1).branch).toBe(b.branch)
  })

  it('before the start not even the claim is there', () => {
    expect(replayAt(fx.boardMerged(), START - 60).events).toEqual([])
  })

  it('at and after the end it is the final board itself', () => {
    const b = fx.boardMerged()
    expect(replayAt(b, END)).toBe(b)
    expect(replayAt(b, END + 3600)).toBe(b)
  })

  it('a moment before the end, the rebuilt record is the final one: events, conveyor, lanes, decided checks', () => {
    const b = fx.boardMerged()
    b.ended_at = END + 60 // the span now ends a minute after the last event
    const r = replayAt(b, END + 59)
    expect(r).not.toBe(b)
    expect(r.events).toEqual(b.events)
    expect(r.belt).toEqual(b.belt)
    expect(r.checks.history).toEqual(b.checks.history)
    const lane = (x: ReqBoard['nodes'][number]) => ({ id: x.id, status: x.status, doing: x.doing, since: x.since, spans: x.spans, marks: x.marks, rounds: x.rounds, gates: x.gates, run: x.run })
    expect(r.nodes.map(lane)).toEqual(b.nodes.map(lane))
    expect(r.relays).toEqual(b.relays)
    expect(r.checks.cells.filter((c) => c.at !== null)).toEqual(b.checks.cells.filter((c) => c.at !== null))
  })

  it('only moves forward: sampled across the span, nothing done is ever undone, and it ends on the final board', () => {
    const b = fx.boardMerged()
    const clk = replayClock(b)
    const N = 80
    let prev = counts(replayAt(b, clk.toReal(0)))
    for (let i = 1; i <= N; i++) {
      const now = counts(replayAt(b, clk.toReal((clk.total * i) / N)))
      for (const k of Object.keys(now) as (keyof typeof now)[]) expect(now[k], `${k} at sample ${i}`).toBeGreaterThanOrEqual(prev[k])
      prev = now
    }
    expect(prev).toEqual(counts(b))
    expect(prev).toMatchObject({ events: 52, belt: 12, passed: 9, rounds: 2, steps: 4 })
  })

  it('shows a check cell only once it is decided, with its final status', () => {
    const b = fx.boardMerged()
    const verify = (r: ReqBoard) => r.checks.cells.filter((c) => c.group === 'verify').map((c) => c.status)
    const before = replayAt(b, VERIFY_OK - 1)
    expect(verify(before)).toEqual(['skip', 'skip', 'skip'])
    expect(before.checks).toMatchObject({ passed: 4, failed: 0, total: 4, at: 1793639480, history: [{ at: 1793652660, passed: 2, failed: 1 }] })
    const after = replayAt(b, VERIFY_OK)
    expect(verify(after)).toEqual(['pass', 'pass', 'pass'])
    expect(after.checks).toMatchObject({ passed: 7, total: 7, at: VERIFY_OK })
    expect(after.checks.history).toHaveLength(2)
  })

  it('shows an undated cell that waits for a person only while its node waits for one', () => {
    const b = fx.board()
    b.events.push({ seq: 38, at: b.at, kind: 'app', from: null, to: null, text: '看板刷新', detail: false, src: null, node: null, run: null, round: null })
    const plan = (r: ReqBoard) => r.checks.cells.find((c) => c.id === 'gate-PLAN')
    const api = (r: ReqBoard) => r.nodes.find((n) => n.id === 'code-generation/refund-api')
    const waiting = replayAt(b, 1794146100)
    expect(api(waiting)?.status).toBe('awaiting_approval')
    expect(plan(waiting)).toEqual(plan(b))
    expect(plan(waiting)?.status).toBe('wait')
    const earlier = replayAt(b, 1794130000)
    expect(api(earlier)?.status).toBe('running')
    expect(plan(earlier)).toMatchObject({ status: 'skip', at: null, detail: '还没到这一步' })
  })

  it('glows while a node is at work, its run is at it, and the person is told the AI is on it', () => {
    const r = replayAt(fx.boardMerged(), 1793640000)
    expect(r.nodes.find((n) => n.id === 'code-generation/refund-api')).toMatchObject({ status: 'running', run: 'runner-1' })
    expect(r.nodes.find((n) => n.id === 'code-generation/refund-api')?.rounds).toMatchObject([{ n: 1, ended: null, outcome: { kind: 'open' }, outputs: [] }])
    expect(r.runs.map((x) => [x.key, x.now.status, x.now.node])).toEqual([['local', 'done', null], ['runner-1', 'running', 'code-generation/refund-api']])
    expect(r.row.state).toBe('running')
    expect(r.live).toBe(true)
    expect(r.person).toMatchObject({ status: 'running', doing: 'AI 在做' })
  })

  it('while 发布 waits for the product’s approval, it waits on the product (not 等你), the person is told, and 验收 waits too', () => {
    const r = replayAt(fx.boardMerged(), RELEASE_ASKED + 20)
    const release = r.nodes.find((n) => n.id === 'release')!
    expect(release).toMatchObject({ status: 'awaiting_approval', doing: '等产品验收（D-2）', since: RELEASE_ASKED })
    // decided later: then it was asked, of the product, with nobody's decision yet
    expect(release.gates).toMatchObject([{ kind: 'RELEASE', state: 'pending', at: RELEASE_ASKED, who: 'product', by: null, line: null }])
    expect(r.runs[1].now).toMatchObject({ status: 'awaiting_approval', who: 'product', node: 'release', do: null })
    expect(r.row.waits).toMatchObject({ who: 'product', text: '等产品验收（D-2）' })
    expect(r.row.state).toBe('acceptance')
    expect(r.person).toMatchObject({ status: 'waiting', doing: '等产品验收（D-2）' })
    expect(r.accept.state).toBe('waiting')
  })

  it('a wait on the person looking is theirs: 等你, and their node asks', () => {
    const r = replayAt(fx.boardMerged(), 1793629860 + 20)
    expect(r.nodes.find((n) => n.id === 'requirements')).toMatchObject({ status: 'awaiting_approval', doing: '等你批需求', gates: [{ state: 'pending', who: 'you' }] })
    expect(r.row.waits.who).toBe('you')
    expect(r.person).toMatchObject({ status: 'awaiting_approval', doing: '等你批需求' })
  })

  it('a round sent back is over once it was; the next one opens when it starts', () => {
    const design = (t: number) => replayAt(fx.boardMerged(), t).nodes.find((n) => n.id === 'application-design')!
    expect(design(1793635080 + 60).rounds.map((x) => [x.n, x.outcome.kind])).toEqual([[1, 'sent_back']])
    expect(design(1793635080 + 60).gates.map((g) => g.state)).toEqual(['sent_back'])
    expect(design(1793636580 + 60).rounds.map((x) => [x.n, x.outcome.kind])).toEqual([[1, 'sent_back'], [2, 'open']])
  })

  it('验收 is accepted once 发布 is approved', () => {
    const b = fx.boardMerged()
    expect(replayAt(b, RELEASE_OK - 1).accept.state).toBe('waiting')
    expect(replayAt(b, RELEASE_OK).accept.state).toBe('accepted')
  })

  it('counts Lean’s stages the live board’s way: a moment before the end, the numbers the board says', () => {
    for (const b of [fx.board(), fx.boardMerged()]) {
      const { lean } = replayAt(b, replaySpan(b).end - 0.5).row
      expect([lean.done_n, lean.skipped_n, lean.next], b.row.key).toEqual([b.row.lean.done_n, b.row.lean.skipped_n, b.row.lean.next])
    }
  })

  it('offers nothing to do: no line to type, no step, no facts or credits', () => {
    const later = fx.board()
    later.events.push({ seq: 38, at: later.at, kind: 'app', from: null, to: null, text: '看板刷新', detail: false, src: null, node: null, run: null, round: null })
    for (const [b, t] of [[later, 1794146100], [fx.boardMerged(), 1793629880], [fx.boardMerged(), RELEASE_ASKED + 60]] as const) {
      const r = replayAt(b, t)
      expect(r.nodes.some((n) => n.gates.some((g) => g.state === 'pending')), `${b.row.key} at ${t}`).toBe(true)
      expect(r.nodes.flatMap((n) => n.gates.map((g) => g.line))).toEqual(r.nodes.flatMap((n) => n.gates.map(() => null)))
      expect(r.runs.map((x) => [x.now.do, x.credits])).toEqual(r.runs.map(() => [null, null]))
      expect(r.accept.step).toEqual({ text: '', who: '', line: null, mr_url: null, session_url: null })
      expect(r.accept.done).toBeNull()
    }
  })
})

describe('replayAt: what was there then', () => {
  it('a session is there once it was opened, never before 开工; what it cost is known at the end; the MR comes with the hand-over', () => {
    const b = fx.board()
    const claim = b.events.find((e) => e.kind === 'claim')!.at
    expect(b.sessions[0].opened_at).toBeLessThanOrEqual(claim) // the record says so; a claim has no session
    expect(replayAt(b, claim + 1).runs[0].sessions).toEqual([])
    const opened = b.events.find((e) => e.kind === 'session')!.at
    const before = replayAt(b, opened - 1)
    expect(before.runs[0].sessions).toEqual([])
    expect(before.sessions).toEqual([])
    expect(before.credits.total).toBeNull()
    expect(before.row.credits).toBeNull()
    const after = replayAt(b, opened + 60)
    expect(after.runs[0].sessions).toEqual(b.runs[0].sessions)
    expect(after.sessions).toHaveLength(1)
    expect(after.credits.total).toBeNull()
    const m = fx.boardMerged()
    const hand = m.events.find((e) => e.kind === 'handover')!.at
    expect(replayAt(m, hand - 1).row.mr).toBeNull()
    expect(replayAt(m, hand + 1).row.mr).toEqual(m.row.mr)
  })
})
