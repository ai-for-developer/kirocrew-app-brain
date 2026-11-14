import { describe, expect, it } from 'vitest'
import { fx } from '../test/data'
import type { BeltItem, BoardEvent, NodeId, ReqBoard, RunKey } from '../types'
import { diffBoards, effects, maxSeq, type DiffOptions } from './diff'

// board.json (PAY-3): the code-generation plan of refund-api (PLAN) waits for the owner's approval. N is the seq of the
// first event the next poll brings; API is that node.
const REQ = 'r1/PAY-3'
const AT = 1794146400
const N = maxSeq(fx.board()) + 1
const API = 'code-generation/refund-api'
const QUIET = { req: REQ, first: true, fresh: [], flying: [], flips: [], belt: [], nodes: [], merged: false, green: false, announce: null }
const NOTHING = { ...QUIET, first: false }

function ev(over: Partial<BoardEvent> & Pick<BoardEvent, 'kind'>): BoardEvent {
  return { seq: N, at: AT, from: null, to: null, text: '', detail: false, src: null, node: null, run: 'local' as RunKey, round: null, ...over }
}

/** The next snapshot: a fresh copy of board.json with *change* made to it. */
function after(change: (b: ReqBoard) => void): ReqBoard {
  const b = fx.board()
  change(b)
  return b
}

function handedOver(b: ReqBoard) {
  b.row.state = 'handed_over'
  b.row.state_label = '已交接'
}

const node = (b: ReqBoard, id: string) => b.nodes.find((n) => n.id === id)!
const cell = (b: ReqBoard, id: string) => b.checks.cells.find((c) => c.id === id)!
/** The boxes the stage graph draws: every node that is not skipped. */
const boxes = (b: ReqBoard) => new Set<string>(b.nodes.filter((n) => n.status !== 'skipped').map((n) => n.id))

/** A poll from board.json to *next*, as the stage graph plays it. */
function played(next: ReqBoard, opts: DiffOptions = {}, max?: number) {
  return effects(diffBoards(fx.board(), next, opts), next, boxes(next), max)
}

describe('diffBoards: history, nothing animates', () => {
  it('the first snapshot', () => {
    expect(diffBoards(null, fx.board())).toEqual(QUIET)
  })

  it('a snapshot of another requirement before this one', () => {
    expect(diffBoards(fx.boardMerged(), fx.board())).toEqual(QUIET)
  })

  it('a resync (the page was hidden), however much changed meanwhile', () => {
    const next = after((b) => {
      b.events.push(ev({ kind: 'reject', from: 'person', to: API, text: '你退回了代码生成计划' }))
      cell(b, 'gate-PLAN').status = 'fail'
    })
    expect(diffBoards(fx.board(), next, { resync: true })).toEqual(QUIET)
  })

  it('events that went backwards (a restart)', () => {
    expect(diffBoards(fx.board(), after((b) => void (b.events = b.events.slice(0, N - 2))))).toEqual(QUIET)
  })
})

describe('diffBoards: one poll', () => {
  it('the same snapshot again: nothing fresh, nothing moves, nothing said', () => {
    expect(diffBoards(fx.board(), fx.board())).toEqual(NOTHING)
  })

  it('only events after the last seen seq are fresh, in seq order', () => {
    const next = after((b) => b.events.push(ev({ seq: N + 1, at: AT + 1, kind: 'stage', from: API, to: API }), ev({ kind: 'stage', from: API, to: API })))
    expect(diffBoards(fx.board(), next).fresh.map((e) => e.seq)).toEqual([N, N + 1])
  })

  it('a fresh approval is a comet from 你 to its stage, with its word', () => {
    const got = played(after((b) => b.events.push(ev({ kind: 'approve', from: 'person', to: API, node: API as NodeId, text: '你批准了代码生成计划' }))))
    expect(got.items).toEqual([{ key: `${REQ}#${N}`, t: AT, kind: 'approve', from: 'orch', to: API, tone: 'pass', shape: 'comet', prio: 3, label: '批准' }])
    expect(got.flashes).toEqual([])
  })

  it('a Lean stage is a spark on its box', () => {
    const got = played(after((b) => b.events.push(ev({ kind: 'stage', from: API, to: API, text: '做完「代码生成（refund-api）」' }))))
    expect(got.items).toEqual([{ key: `${REQ}#${N}`, t: AT, kind: 'stage', from: API, to: API, tone: 'tool', shape: 'spark', prio: 0 }])
  })

  it('an event with no place on the graph (a skipped stage, no end at all) is fresh but flies nothing', () => {
    const skipped = ev({ kind: 'stage', from: 'nfr-design/refund-api', to: 'nfr-design/refund-api', text: '跳过「非功能设计」' })
    const app = ev({ seq: N + 1, kind: 'app', text: '看板刷新' })
    const next = after((b) => b.events.push(skipped, app))
    expect(diffBoards(fx.board(), next).fresh).toEqual([skipped, app])
    expect(played(next)).toEqual({ items: [], flashes: [] })
  })

  it('a 退回 bounces to its stage and flashes it red', () => {
    const got = played(after((b) => b.events.push(ev({ kind: 'reject', from: 'person', to: API, text: '你退回了代码生成计划' }))))
    expect(got.items).toEqual([{ key: `${REQ}#${N}`, t: AT, kind: 'reject', from: 'orch', to: API, tone: 'bounce', shape: 'comet', prio: 3, label: '退回' }])
    expect(got.flashes).toEqual([{ key: `${REQ}#${N}`, target: API, tone: 'bad', kind: 'reject' }])
  })

  it('a blocked tool call is a cross on the box it happened on, which flashes red', () => {
    const got = played(after((b) => b.events.push(ev({ kind: 'blocked', from: API, to: API, text: 'Lean 拦下了写 src/refund/api.py' }))))
    expect(got.items).toEqual([{ key: `${REQ}#${N}`, t: AT, kind: 'blocked', from: API, to: API, tone: 'danger', shape: 'cross', prio: 2 }])
    expect(got.flashes).toEqual([{ key: `${REQ}#${N}`, target: API, tone: 'bad', kind: 'blocked' }])
  })

  it('a commit runs from its stage to the run branch; a person’s push after the hand-over is red', () => {
    const commit = ev({ kind: 'commit', from: API, to: 'branch', text: '提交 1a2b3c4d：PAY-3：导出' })
    expect(played(after((b) => b.events.push(commit))).items).toEqual([expect.objectContaining({ from: API, to: 'run', tone: 'run', prio: 2 })])
    const push: BeltItem = { kind: 'commit', at: AT, sha: '1a2b3c4d0000', by: 'person', text: '修了一个打字错误', files: 1, records_only: false, run: 'runner-1' as RunKey, node: API as NodeId }
    const pushed = after((b) => {
      b.runs.push({ ...b.runs[0], key: 'runner-1' as RunKey, kind: 'runner', label: '运行机 第 1 次' })
      b.belt.push(push)
      b.events.push({ ...commit, run: 'runner-1' as RunKey })
    })
    expect(played(pushed).items).toEqual([expect.objectContaining({ from: API, to: 'run', tone: 'danger', prio: 3 })])
  })

  it('a hand-over flies from the stage it left to its pill on the rail; the bridge’s steps blip there', () => {
    // board-merged.json (PAY-2): seq 21 the hand-over (from units, skipped there: no box), 22 the bridge took it
    const merged = fx.boardMerged()
    const upTo = (seq: number, from = 'units') => ({ ...merged, events: merged.events.filter((e) => e.seq <= seq).map((e) => (e.kind === 'handover' ? { ...e, from } : e)) })
    const play = (from?: string) => effects(diffBoards(upTo(20), upTo(22, from)), upTo(22, from), boxes(merged)).items.map((i) => [i.kind, i.from, i.to, i.shape])
    expect(play('application-design')).toEqual([['handover', 'application-design', 'relay:0', 'comet'], ['accepted', 'relay:0', 'relay:0', 'ring']])
    expect(play()[0]).toEqual(['handover', 'relay:0', 'relay:0', 'ring'])
  })

  it('a failed verify flies from the run branch to the checks with its score and flashes them red', () => {
    const got = played(after((b) => b.events.push(ev({ kind: 'verify', from: 'branch', to: 'checks', node: 'build-and-test' as NodeId, text: 'verify 没过：pytest' }))))
    // board.json has one verify cell, not passed yet
    expect(got.items).toEqual([{ key: `${REQ}#${N}`, t: AT, kind: 'verify', from: 'run', to: 'tests', tone: 'fail', shape: 'comet', prio: 3, label: '0/1' }])
    expect(got.flashes).toEqual([{ key: `${REQ}#${N}`, target: 'tests', tone: 'bad', kind: 'verify' }])
  })

  it('a passing verify is green, carries the score, and flashes nothing red', () => {
    const next = after((b) => {
      cell(b, 'verify').status = 'pass'
      b.events.push(ev({ kind: 'verify', from: 'branch', to: 'checks', text: 'verify 通过：1 项都过了' }))
    })
    const got = played(next)
    expect(got.items).toEqual([expect.objectContaining({ from: 'run', to: 'tests', tone: 'pass', label: '1/1' })])
    expect(got.flashes).toEqual([])
  })

  it('an event more than lateS older than the snapshot is fresh but does not fly or flash', () => {
    const reject = (at: number) => ev({ at, kind: 'reject', from: 'person', to: API, text: '你退回了代码生成计划' })
    const lateNext = after((b) => b.events.push(reject(AT - 31)))
    expect(diffBoards(fx.board(), lateNext, { at: AT, lateS: 30 }).fresh.map((e) => e.seq)).toEqual([N])
    expect(played(lateNext, { at: AT, lateS: 30 })).toEqual({ items: [], flashes: [] })
    const justInTime = played(after((b) => b.events.push(reject(AT - 30))), { at: AT, lateS: 30 })
    expect(justInTime.items.map((i) => i.key)).toEqual([`${REQ}#${N}`])
    expect(justInTime.flashes).toHaveLength(1)
  })

  it('over max items the most important stay, in the order they happened', () => {
    const next = after((b) => b.events.push(
      ev({ seq: N, at: AT, kind: 'commit', from: API, to: 'branch', text: '提交 1a2b3c4d：PAY-3：导出' }), // prio 2
      ev({ seq: N + 1, at: AT + 1, kind: 'stage', from: API, to: API, text: '做完「代码生成」' }), // prio 0
      ev({ seq: N + 2, at: AT + 2, kind: 'approve', from: 'person', to: API, text: '你批准了代码生成计划' }), // prio 3
    ))
    expect(diffBoards(fx.board(), next).fresh).toHaveLength(3)
    expect(played(next, {}, 2).items.map((i) => [i.key, i.prio])).toEqual([[`${REQ}#${N}`, 2], [`${REQ}#${N + 2}`, 3]])
  })

  it('the merge flashes the run branch and every merged box green, once', () => {
    const next = after((b) => void (b.ended_at = AT))
    expect(played(next).flashes).toEqual([
      { key: `${REQ}#merged-run`, target: 'run', tone: 'ok', kind: 'merge' },
      // every stage the way moved past is merged (完成), so every one of them flashes; refund-api's still waits
      ...['requirements', 'application-design', 'units', 'functional-design/refund-export', 'functional-design/refund-api', 'code-generation/refund-export']
        .map((target) => ({ key: `${REQ}#merged`, target, tone: 'ok', kind: 'merge' })),
    ])
    expect(effects(diffBoards(next, after((b) => void (b.ended_at = AT))), next, boxes(next)).flashes).toEqual([])
  })

  it('checks that turn all green flash the check ring', () => {
    const next = after((b) => Object.assign(b.checks, { passed: 4, failed: 0, total: 4, at: AT }))
    expect(played(next).flashes).toEqual([{ key: `${REQ}#green@${AT}`, target: 'tests', tone: 'ok', kind: 'verify' }])
  })

  it('the first snapshot plays nothing', () => {
    expect(effects(diffBoards(null, fx.board()), fx.board(), boxes(fx.board()))).toEqual({ items: [], flashes: [] })
  })

  it('a check cell whose status changed flips; a new cell does not', () => {
    const next = after((b) => {
      cell(b, 'gate-PLAN').status = 'pass'
      b.checks.cells.push({ ...cell(b, 'verify'), id: 'verify-ruff-1', status: 'pass', at: AT })
    })
    expect(diffBoards(fx.board(), next).flips).toEqual([{ id: 'gate-PLAN', from: 'wait', to: 'pass' }])
  })

  it('a node whose status changed is listed with from and to', () => {
    const d = diffBoards(fx.board(), after((b) => void (node(b, API).status = 'merged')))
    expect(d.nodes).toEqual([{ id: API, from: 'awaiting_approval', to: 'merged' }])
  })

  it('a new commit on the conveyor is listed by its belt key, the old ones are not', () => {
    const commit: BeltItem = { kind: 'commit', at: AT, sha: 'abcdef123456', by: 'person', text: 'PAY-3：导出按钮', files: 2, records_only: false, run: null, node: null }
    expect(diffBoards(fx.board(), after((b) => b.belt.push(commit))).belt).toEqual(['commit@abcdef123456@1794146400'])
  })
})

describe('diffBoards: what the live region says', () => {
  it('the requirement changing state', () => {
    const next = after(handedOver)
    expect(diffBoards(fx.board(), next).announce).toBe('需求已交接')
  })

  it('the checks changing, before → after', () => {
    const next = after((b) => void (b.checks.passed = 3))
    expect(diffBoards(fx.board(), next).announce).toBe('检查 2/4 → 3/4')
  })

  it('the latest loud event, in its own words', () => {
    const next = after((b) => b.events.push(
      ev({ seq: N, kind: 'question', from: API, to: 'person', text: '停下问你：导出的时区' }),
      ev({ seq: N + 1, at: AT + 1, kind: 'request', from: API, to: 'person', text: '请你批准代码生成计划（PLAN · 9c0ffee1）' }),
    ))
    expect(diffBoards(fx.board(), next).announce).toBe('请你批准代码生成计划（PLAN · 9c0ffee1）')
  })

  it('a calm event when there is room for it, after the loud one', () => {
    const calm = diffBoards(fx.board(), after((b) => b.events.push(ev({ kind: 'approve', from: 'person', to: API, text: '你批准了代码生成计划' }))))
    expect(calm.announce).toBe('你批准了代码生成计划')
    const both = diffBoards(fx.board(), after((b) => b.events.push(
      ev({ seq: N, kind: 'approve', from: 'person', to: API, text: '你批准了代码生成计划' }),
      ev({ seq: N + 1, at: AT + 1, kind: 'question', from: API, to: 'person', text: '停下问你：导出的时区' }),
    )))
    expect(both.announce).toBe('停下问你：导出的时区；你批准了代码生成计划')
  })

  it('two things at most, in order: the state, the checks, the loud event', () => {
    const next = after((b) => {
      handedOver(b)
      b.checks.passed = 3
      b.events.push(ev({ kind: 'reject', from: 'person', to: API, text: '你退回了代码生成计划' }))
    })
    expect(diffBoards(fx.board(), next).announce).toBe('需求已交接；检查 2/4 → 3/4')
  })

  it('nothing for events nobody needs to be told about', () => {
    const next = after((b) => b.events.push(
      ev({ seq: N, kind: 'stage', from: API, to: API, text: '做完「代码生成」' }),
      ev({ seq: N + 1, at: AT + 1, kind: 'commit', from: API, to: 'branch', text: '提交 1a2b3c4d：PAY-3：导出' }),
    ))
    const d = diffBoards(fx.board(), next)
    expect(d.fresh).toHaveLength(2)
    expect(d.announce).toBeNull()
  })
})
