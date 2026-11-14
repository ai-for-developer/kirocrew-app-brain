import { act, fireEvent, render, renderHook, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import { fx } from '../test/data'
import { API, installFixtureRoutes } from '../test/routes'
import type { BoardEntry } from '../nav'
import type { ReqBoard } from '../types'
import type { ViewProps } from '../views/props'
import { BoardPage } from './BoardPage'
import { HOLD_TICKS, PICK_TICKS, TICK_MS, useReplay, type ReplayHandle } from './Replay'
import { decisionStops } from './timeline'

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
const BACK = 1793635080

function renderPage(entry: BoardEntry) {
  installFixtureRoutes({ [`GET ${API}/board/*`]: () => fx.boardMerged() })
  const p: ViewProps = { status: fx.status(), go: vi.fn(), replace: vi.fn(), reloadStatus: vi.fn(), setStatus: vi.fn(), pollMs: 60_000 }
  render(<BoardPage entry={entry} {...p} />)
  return p
}

const designBox = () => screen.getByTestId('kt-graph').querySelector('.kt-node[data-node="application-design"]') as SVGGElement

describe('回放 on the page: the hold', () => {
  test('at the design’s 退回 it holds: 等你批 with the ask open, then 你退回了设计 lit on the box and its chip; then the decision lands', async () => {
    renderPage({ ...PAY2, at: BACK - 30 })
    const rp = within(await screen.findByRole('group', { name: '回放' }))
    vi.useFakeTimers()
    fireEvent.click(rp.getByRole('button', { name: '播放' }))
    act(() => void vi.advanceTimersByTime(TICK_MS))
    const chips = () => within(screen.getByTestId('kt-graph')).getAllByTestId('kt-gate-pill').map((x) => x.querySelector('text')?.textContent)
    expect(screen.getByTestId('br-replay-held')).toHaveTextContent('应用设计：等你批')
    expect(designBox().querySelector('[data-testid="kt-node-held"]')).not.toHaveClass('is-fix')
    expect(chips()).toContain('等你批 · 设计')
    act(() => void vi.advanceTimersByTime(TICK_MS * (HOLD_TICKS - PICK_TICKS)))
    expect(screen.getByTestId('br-replay-held')).toHaveTextContent('应用设计：你退回了设计')
    expect(screen.getByTestId('br-replay-held')).toHaveClass('is-fix')
    expect(designBox().querySelector('[data-testid="kt-node-held"]')).toHaveClass('is-fix')
    expect(chips()).toContain('✗ 退回了')
    expect(designBox()).toHaveAttribute('data-status', 'awaiting_approval')
    act(() => void vi.advanceTimersByTime(TICK_MS * PICK_TICKS))
    expect(screen.queryByTestId('br-replay-held')).toBeNull()
    expect(designBox().querySelector('[data-testid="kt-node-held"]')).toBeNull()
    expect(designBox()).toHaveAttribute('data-status', 'fixing')
  })
})
