import { act, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import { fx } from '../test/data'
import { API, installFixtureRoutes } from '../test/routes'
import { apiCalls } from '../test/stubs/app-sdk'
import type { BoardEntry } from '../nav'
import type { FileView, NodeOutput, NodeTurns, OutFile, ReqBoard } from '../types'
import type { ViewProps } from '../views/props'
import { BoardPage } from './BoardPage'
import { HOLD_TICKS, PICK_TICKS, TICK_MS, useReplay, type ReplayHandle } from './Replay'
import { decisionStops, replayClock } from './timeline'

afterEach(() => vi.useRealTimers())

const noDiff = () => {}

/** Plays the replay to its end, one tick at a time: where it stood after each tick. */
function playThrough(b: ReqBoard, speed = 1) {
  const h = renderHook(() => useReplay(b, noDiff))
  act(() => h.result.current.start())
  act(() => h.result.current.setSpeed(speed))
  const seen: { p: number; t: number; held: ReplayHandle['held'] }[] = []
  for (let i = 0; i < 4000 && h.result.current.playing; i++) {
    act(() => void vi.advanceTimersByTime(TICK_MS))
    const r = h.result.current
    seen.push({ p: r.p, t: r.t, held: r.held })
  }
  return { h, seen }
}

/** The ticks the replay stood still for: each run of ticks at one play time, past the first. */
function stills(seen: { p: number }[]): { p: number; ticks: number }[] {
  const out: { p: number; ticks: number }[] = []
  for (let i = 1; i < seen.length; i++) {
    if (seen[i].p !== seen[i - 1].p) continue
    const last = out[out.length - 1]
    if (last?.p === seen[i].p) last.ticks++
    else out.push({ p: seen[i].p, ticks: 1 })
  }
  return out
}

describe('回放 holds on each decision a person made', () => {
  test('4 s on each one (approve, reject, answer, the product’s acceptance), the last 2 s with the choice lit; nowhere else', () => {
    vi.useFakeTimers()
    const b = fx.boardMerged()
    const stops = decisionStops(b)
    const { h, seen } = playThrough(b)
    expect(h.result.current.p).toBe(h.result.current.total)
    const still = stills(seen)
    expect(still.map((x) => x.ticks)).toEqual(stops.map(() => HOLD_TICKS - 1))
    // the tick that lands on it and the 15 after it: 16 ticks, 4 s on the decision
    expect(HOLD_TICKS * TICK_MS).toBe(4000)
    for (const [i, x] of still.entries()) {
      const at = seen.filter((s) => s.p === x.p)
      expect(at).toHaveLength(HOLD_TICKS)
      expect(at[0].t).toBeCloseTo(stops[i].at, 3)
      expect(at.map((s) => [s.held?.node, s.held?.choice])).toEqual(at.map(() => [stops[i].node, stops[i].choice]))
      expect(at.map((s) => s.held?.picked)).toEqual([...Array(HOLD_TICKS - PICK_TICKS).fill(false), ...Array(PICK_TICKS).fill(true)])
    }
    expect(stops.map((x) => x.choice)).toEqual(['approve', 'reject', 'approve', 'answer', 'accept'])
    // nothing else stands still or holds
    expect(seen.filter((s) => s.held).length).toBe(stops.length * HOLD_TICKS)
  })

  test('the hold is 4 s at any speed', () => {
    vi.useFakeTimers()
    const b = fx.boardMerged()
    const { seen } = playThrough(b, 4)
    expect(stills(seen).map((x) => x.ticks)).toEqual(decisionStops(b).map(() => HOLD_TICKS - 1))
  })

  test('a jump never holds: scrubbed onto a decision it shows it and plays on; scrubbing lets go of a hold; a pause keeps it', () => {
    vi.useFakeTimers()
    const b = fx.boardMerged()
    const [, back] = decisionStops(b)
    const h = renderHook(() => useReplay(b, noDiff))
    act(() => h.result.current.start(back.at - 120))
    act(() => h.result.current.toggle())
    for (let i = 0; i < 40 && !h.result.current.held; i++) act(() => void vi.advanceTimersByTime(TICK_MS))
    expect(h.result.current.held).toMatchObject({ node: 'application-design', choice: 'reject', picked: false })
    const p = h.result.current.p
    act(() => h.result.current.pause())
    act(() => void vi.advanceTimersByTime(TICK_MS * HOLD_TICKS * 2))
    expect([h.result.current.p, h.result.current.held?.choice]).toEqual([p, 'reject'])
    act(() => h.result.current.seek(p - 10))
    expect(h.result.current.held).toBeNull()
    act(() => h.result.current.seek(p))
    act(() => h.result.current.toggle())
    act(() => void vi.advanceTimersByTime(TICK_MS))
    expect(h.result.current.held).toBeNull()
    expect(h.result.current.p).toBeGreaterThan(p)
  })
})

// ---------------------------------------------------------------- the page

const PAY2: BoardEntry = { repo: 'r1', key: 'PAY-2' }
/** The page loads the board, then replays it, then the drawer reads: slower than the default second when every file runs at once. */
const SLOW = { timeout: 5000 }
const SHA = (s12: string) => s12 + '0'.repeat(28)
const DESIGN = 'aidlc-docs/261102-refund-id/inception/application-design/application-design.md'
const STATE = 'aidlc-docs/261102-refund-id/aidlc-state.md'
const DRAFT = SHA('8bde8a1833b8') // 第 1 轮's commit, 23:35
const FIXED = SHA('f843e8d2a8f6') // 按意见改设计, 00:21: 第 2 轮's version
const ASKED_1 = 1793633760
const BACK = 1793635080
const ASKED_2 = 1793636580

const file = (over: Partial<OutFile>): OutFile => ({ path: DESIGN, output: true, status: 'M', added: 7, removed: 1, ref: FIXED, pin: null, base: DRAFT,
  base_round: 1, vs: [{ round: 1, ref: DRAFT }], why: null, ...over })

/** application-design's rounds as GET /outputs lists them: 第 1 轮 the draft, 第 2 轮 the fix (compared with 第 1 轮). */
const designFiles = (round: number): NodeOutput => ({
  node: 'application-design' as NodeOutput['node'], round, run: 'local' as NodeOutput['run'], branch: 'ai/alice/PAY-2-refund-id', more: 0, why: null,
  head: round === 1 ? DRAFT : FIXED,
  files: round === 1
    ? [file({ ref: DRAFT, status: 'A', added: 13, removed: 0, base: null, base_round: null, vs: [] }), file({ path: STATE, output: false, ref: DRAFT, vs: [], base_round: null })]
    : [file({}), file({ path: STATE, output: false, ref: FIXED, vs: [], base_round: null })],
})

const designTurns = (round: number): NodeTurns => ({
  node: 'application-design' as NodeTurns['node'], round, run: 'local' as NodeTurns['run'], why_not: null, why: null,
  lit: { from: ASKED_1 - 2400, to: ASKED_1 }, meta: { took: 3780, turns: 1, calls: 2 },
  turns: [{ n: round, at: ASKED_1 - 2400, until: ASKED_1, slot: 'brain-PAY-2-1a2b', url: '/chat?sid=brain-PAY-2-1a2b',
    task: { kind: 'app', cause: '开工消息，只有编号', text: '这是需求 PAY-2 的本地开发会话。' }, reply: '设计写好了，等你批。', live: false, more_calls: 0, also: [],
    calls: [{ at: ASKED_1 - 2000, tool: '读文件', detail: 'requirements.md', in_round: true }, { at: ASKED_1 - 60, tool: '写文件', detail: 'application-design.md', in_round: true }] }],
})

function renderPage(entry: BoardEntry) {
  installFixtureRoutes({
    [`GET ${API}/board/*`]: () => fx.boardMerged(),
    [`GET ${API}/outputs/*`]: (_b, path) => {
      const q = new URLSearchParams(path.split('?')[1])
      const round = Number(q.get('round') ?? 2)
      if (path.includes('/turns?')) return designTurns(round)
      if (!path.includes('/file?')) return designFiles(round)
      const v: FileView = { path: q.get('path') ?? '', view: q.get('view') as FileView['view'], at: q.get('ref') ?? '', base: q.get('base'), pinned: false,
        lang: 'markdown', asked: null, binary: false, truncated: false, size: 10, text: `# 应用设计 @ ${q.get('ref')?.slice(0, 7)}\n` }
      return v
    },
  })
  const p: ViewProps = { status: fx.status(), go: vi.fn(), replace: vi.fn(), reloadStatus: vi.fn(), setStatus: vi.fn(), pollMs: 60_000 }
  render(<BoardPage entry={entry} {...p} />)
  return p
}

const bar = () => screen.getAllByRole('group', { name: '回放' })[0]
const drawer = () => within(screen.getByRole('dialog', { name: '每一步的产出' }))
const roundPills = () => Array.from(screen.getByRole('dialog').querySelectorAll('.kt-round')).map((x) => x.textContent)
const shownFiles = () => drawer().queryAllByTestId('out-file').map((x) => x.getAttribute('title'))
const designBox = () => screen.getByTestId('kt-graph').querySelector('.kt-node[data-node="application-design"]') as SVGGElement
/** Scrubbed to the moment t (the scrubber counts play seconds). */
const scrubTo = (t: number) =>
  fireEvent.change(within(bar()).getByRole('slider', { name: '回放进度' }), { target: { value: String(Math.round(replayClock(fx.boardMerged()).toPlay(t))) } })

describe('回放 on the page: the hold and the drawer at the moment', () => {
  test('at the design’s 退回 it holds: 等你批 with the ask open, then 你退回了设计 lit on the box and its chip; then the decision lands', async () => {
    renderPage({ ...PAY2, at: BACK - 30 })
    const rp = within(await screen.findByRole('group', { name: '回放' }, SLOW))
    vi.useFakeTimers()
    fireEvent.click(rp.getByRole('button', { name: '播放' }))
    act(() => void vi.advanceTimersByTime(TICK_MS))
    const chips = () => within(screen.getByTestId('kt-graph')).getAllByTestId('kt-gate-pill').map((x) => x.querySelector('text')?.textContent)
    expect(screen.getByTestId('br-replay-held')).toHaveTextContent('应用设计：等你批')
    expect(designBox().querySelector('[data-testid="kt-node-held"]')).not.toHaveClass('is-bad')
    expect(chips()).toContain('等你批 · 设计')
    act(() => void vi.advanceTimersByTime(TICK_MS * (HOLD_TICKS - PICK_TICKS)))
    expect(screen.getByTestId('br-replay-held')).toHaveTextContent('应用设计：你退回了设计')
    // 退回 in the one colour it has everywhere (the legend's 退回 · 没过, the flash when it lands): red, not 返工's orange
    expect(screen.getByTestId('br-replay-held')).toHaveClass('is-bad')
    expect(designBox().querySelector('[data-testid="kt-node-held"]')).toHaveClass('is-bad')
    expect(chips()).toContain('✗ 退回了')
    expect(designBox()).toHaveAttribute('data-status', 'awaiting_approval')
    act(() => void vi.advanceTimersByTime(TICK_MS * PICK_TICKS))
    expect(screen.queryByTestId('br-replay-held')).toBeNull()
    expect(designBox().querySelector('[data-testid="kt-node-held"]')).toBeNull()
    expect(designBox()).toHaveAttribute('data-status', 'fixing')
  })

  test('a round sent back says it in 退回’s colour on its lane, in the drawer’s pill and banner, as the held choice does', async () => {
    renderPage({ ...PAY2, out: { node: 'application-design', round: 1 } })
    const lane = (await screen.findAllByTestId('kt-lane', {}, SLOW)).find((l) => l.dataset.node === 'application-design')!
    expect(within(lane).getByText(/你退回了/).closest('li')).toHaveClass('tone-bad')
    const pill = await drawer().findByRole('button', { name: /第 1 轮/ }, SLOW)
    expect(pill).toHaveClass('tone-bad')
    expect(drawer().getByTestId('out-banner')).toHaveClass('tone-bad')
    expect(drawer().getByTestId('out-banner')).toHaveTextContent('你退回了这一轮')
  })

  test('the drawer lists only the rounds started by then, each file at that round’s version; a later round is absent', async () => {
    renderPage({ ...PAY2, at: BACK - 0.01, out: { node: 'application-design' } })
    await waitFor(() => expect(shownFiles()).toEqual([DESIGN, STATE]), SLOW)
    expect(drawer().getByTestId('out-replay')).toHaveTextContent('回放：这是')
    expect(drawer().getByRole('group', { name: '回放' })).toBeInTheDocument()
    expect(roundPills()).toEqual(['第 1 轮等你批'])
    expect(drawer().getByTestId('out-banner')).toHaveTextContent('这是请你批的版本')
    expect(drawer().getByTestId('out-banner')).toHaveTextContent(/^✋ 这是请你批的版本设计 · (\d\d-\d\d )?\d\d:\d\d 请批/)
    expect(drawer().getByTestId('out-banner')).not.toHaveTextContent('MR')
    await waitFor(() => expect(drawer().getByTestId('out-at')).toHaveTextContent(`第 1 轮的版本 · 提交 ${DRAFT.slice(0, 7)}`), SLOW)
    expect(await drawer().findByTestId('out-md', {}, SLOW)).toHaveTextContent(`应用设计 @ ${DRAFT.slice(0, 7)}`)
    // 第 2 轮 began later: it is never asked for
    const lists = apiCalls.filter((c) => c.path.includes('/outputs/') && !c.path.includes('/file?')).map((c) => c.path)
    expect(lists.length).toBeGreaterThan(0)
    expect(lists.filter((x) => !x.includes('round=1'))).toEqual([])
  })

  test('scrubbing back and forth keeps the drawer at the moment; 退出回放 brings back the live drawer', async () => {
    renderPage({ ...PAY2, at: ASKED_1 - 600, out: { node: 'application-design' } })
    // the round goes on and had committed nothing yet
    await waitFor(() => expect(drawer().getByTestId('out-none')).toHaveTextContent('这一轮那时还没提交文件'), SLOW)
    expect(roundPills()).toEqual(['第 1 轮在做'])
    scrubTo(ASKED_2 + 120)
    await waitFor(() => expect(roundPills()).toEqual(['第 1 轮退回', '第 2 轮等你批']), SLOW)
    await waitFor(() => expect(drawer().getByTestId('out-at')).toHaveTextContent(`第 2 轮的版本 · 提交 ${FIXED.slice(0, 7)}`), SLOW)
    scrubTo(BACK + 60)
    await waitFor(() => expect(roundPills()).toEqual(['第 1 轮退回']), SLOW)
    expect(drawer().getByTestId('out-banner')).toHaveTextContent('你退回了这一轮')
    await waitFor(() => expect(shownFiles()).toEqual([DESIGN, STATE]), SLOW)
    expect(drawer().getByTestId('out-at')).toHaveTextContent(`第 1 轮的版本 · 提交 ${DRAFT.slice(0, 7)}`)
    scrubTo(ASKED_1 - 600)
    await waitFor(() => expect(roundPills()).toEqual(['第 1 轮在做']), SLOW)
    await waitFor(() => expect(drawer().getByTestId('out-none')).toHaveTextContent('这一轮那时还没提交文件'), SLOW)
    fireEvent.change(within(bar()).getByRole('slider', { name: '回放进度' }), { target: { value: '0' } })
    await waitFor(() => expect(drawer().getByTestId('out-not-yet')).toHaveTextContent('这一步那时还没开始'), SLOW)
    expect(roundPills()).toEqual([])
    fireEvent.click(within(bar()).getByRole('button', { name: '退出回放' }))
    expect(screen.queryByRole('group', { name: '回放' })).toBeNull()
    await waitFor(() => expect(roundPills()).toEqual(['第 1 轮退回', '第 2 轮批了']), SLOW)
    expect(drawer().queryByTestId('out-replay')).toBeNull()
    await waitFor(() => expect(shownFiles()).toEqual([DESIGN, STATE]), SLOW)
    await waitFor(() => expect(drawer().getByTestId('out-at')).toHaveTextContent(`第 2 轮的版本 · 提交 ${FIXED.slice(0, 7)}`), SLOW)
  })

  test('its session tabs at the moment: a turn still going has no reply yet and only the calls made by then', async () => {
    renderPage({ ...PAY2, at: ASKED_1 - 600, out: { node: 'application-design', tab: 'calls' } })
    await screen.findByRole('dialog', { name: '每一步的产出' }, SLOW)
    const calls = await drawer().findByTestId('out-calls', {}, SLOW)
    expect(Array.from(calls.querySelectorAll('.k')).map((x) => x.textContent)).toEqual(['读文件'])
    fireEvent.click(drawer().getByRole('tab', { name: /它的回复/ }))
    expect(await drawer().findByTestId('out-reply-none', {}, SLOW)).toHaveTextContent('它还在做这一回合')
  })

  test('看产出 opens the drawer during the replay too, at the moment', async () => {
    const p = renderPage({ ...PAY2, at: BACK + 60 })
    await screen.findByRole('group', { name: '回放' }, SLOW)
    fireEvent.click(within(screen.getByTestId('kt-lanes')).getAllByTestId('kt-lane-out')[1])
    expect(p.replace).toHaveBeenLastCalledWith(expect.objectContaining({ out: { node: 'application-design' } }))
    await waitFor(() => expect(roundPills()).toEqual(['第 1 轮退回']), SLOW)
  })
})
