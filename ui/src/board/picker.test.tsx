import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import BrainApp from '../main'
import { parseView, searchOf, type BoardEntry } from '../nav'
import { fx } from '../test/data'
import { API, installFixtureRoutes } from '../test/routes'
import { worldBoard } from '../test/world'
import type { ReqBoard, RunKey } from '../types'
import type { ViewProps } from '../views/props'
import { BoardPage } from './BoardPage'
import { inRun, pickRows } from './picker'

const PAY9: BoardEntry = { repo: 'r1', key: 'PAY-9' }
const LOCAL = 'local' as RunKey
const RUNNER = 'runner-1' as RunKey

function props(over: Partial<ViewProps> = {}): ViewProps {
  return { status: fx.status(), go: vi.fn(), replace: vi.fn(), reloadStatus: vi.fn(), setStatus: vi.fn(), pollMs: 60_000, ...over }
}
const serve = (make: () => ReqBoard) => installFixtureRoutes({ [`GET ${API}/board/*`]: () => make() })
const laneIds = () => screen.queryAllByTestId('kt-lane').map((l) => l.dataset.node)
const graph = () => screen.getByTestId('kt-graph')
const dimmed = () => Array.from(graph().querySelectorAll('g.kt-node.is-dim')).map((n) => n.getAttribute('data-node'))
const pick = (name: RegExp) => {
  fireEvent.click(screen.getByTestId('kt-runpick'))
  fireEvent.click(screen.getByRole('menuitemradio', { name }))
}

describe('选择运行 · the menu', () => {
  test('PAY-9: 全部, then each run with when, its stages (运行机 did 构建测试 5 rounds, 发布 4) and where it is; other branches greyed', () => {
    const b = worldBoard('r1/PAY-9')
    const rows = pickRows(b)
    expect(rows.map((r) => [r.key, r.title, r.count])).toEqual([
      ['all', '全部', '2 次运行'],
      ['local', '本机会话 · brain-dev', '3 个阶段'],
      ['runner-1', '运行机 第 1 次 · RUN-PAY-9-1', '4 个阶段'],      // 代码生成 of its two units is one stage
      [null, '另一条分支上的运行', '没有'],
    ])
    expect(rows[0].sub).toBe('需求的整条路：本机做启动，运行机接着做构建、发布')
    expect(rows[1].sub).toMatch(/^(\d\d-\d\d )?\d\d:\d\d–\d\d:\d\d · 需求分析、应用设计、拆分单元 · 做完 · 交出去了$/)
    expect(rows[2].sub).toMatch(/ 起 · 代码生成、构建测试 5 轮、审查、发布 4 轮 · 等产品验收$/)
    b.other_runs = [{ branch: 'ai/alice/PAY-9-retry', state: 'parked' }]
    expect(pickRows(b).slice(-1)).toEqual([{ key: null, icon: 'merge', title: '在 ai/alice/PAY-9-retry 上', count: 'parked', sub: '在另一条分支上，没读进这张看板' }])
  })

  test('a local run names the agent its session boots: brain-dev-crew since brain-kit renamed it, brain-dev before', () => {
    const b = worldBoard('r1/PAY-9')
    const local = b.runs.find((r) => r.kind === 'local')!
    local.sessions = local.sessions.map((s) => ({ ...s, agent: 'brain-dev-crew' }))
    expect(pickRows(b).find((r) => r.key === 'local')?.title).toBe('本机会话 · brain-dev-crew')
    local.sessions = []
    expect(pickRows(b).find((r) => r.key === 'local')?.title).toBe('本机会话')
  })

  test('a run on another branch cannot be picked; one run only: the button says it and has no menu', async () => {
    const b = worldBoard('r1/PAY-9')
    b.other_runs = [{ branch: 'ai/alice/PAY-9-retry', state: null }]
    serve(() => b)
    render(<BoardPage entry={PAY9} {...props()} />)
    await screen.findByTestId('kt-graph')
    fireEvent.click(screen.getByTestId('kt-runpick'))
    const off = screen.getByRole('menuitemradio', { name: /PAY-9-retry/ })
    expect(off).toHaveAttribute('aria-disabled', 'true')
    fireEvent.click(off)
    expect(screen.getByTestId('kt-runpick')).toHaveTextContent('运行：全部')
    cleanup()
    serve(() => worldBoard('r1/PAY-16'))
    render(<BoardPage entry={{ repo: 'r1', key: 'PAY-16' }} {...props()} />)
    await screen.findByTestId('kt-graph')
    expect(screen.getByTestId('kt-runpick')).toBeDisabled()
    expect(screen.getByTestId('kt-runpick')).toHaveTextContent(/^运行：本机会话$/)
  })
})

describe('选择运行 · the board shows the picked run only', () => {
  test('PAY-9 运行机 第 1 次: the boxes it did no round of dim; lanes, conveyor, feed, ring and cards count only it', async () => {
    const b = worldBoard('r1/PAY-9')
    const p = props()
    serve(() => b)
    render(<BoardPage entry={PAY9} {...p} />)
    await screen.findByTestId('kt-graph')
    const all = laneIds()
    pick(/^运行机 第 1 次/)
    expect(screen.getByTestId('kt-runpick')).toHaveTextContent('运行：运行机 第 1 次')
    expect(dimmed()).toEqual(['requirements', 'application-design', 'units', 'reflect'])
    expect(laneIds()).toEqual(['code-generation/api', 'code-generation/web', 'build-and-test', 'review', 'release'])
    expect(all).toEqual(['requirements', 'application-design', 'units', ...laneIds()])
    const conveyor = within(screen.getByTestId('kt-conveyor'))
    expect(conveyor.getByRole('heading').parentElement).toHaveTextContent('10 次提交 · 运行机 10')
    const feed = screen.getByTestId('kt-feed')
    const shown = Array.from(feed.querySelectorAll<HTMLElement>('.kt-ev')).map((e) => Number(e.dataset.seq))
    expect(shown.length).toBe(b.events.filter((e) => e.run === RUNNER).length)
    expect(shown.every((seq) => b.events.find((e) => e.seq === seq)?.run === RUNNER)).toBe(true)
    expect(screen.getByTestId('kt-station')).toHaveAccessibleName('检查 3/3')
    expect(screen.getByTestId('br-checks-say')).toHaveTextContent('第 5 轮 · 5 轮都过')
    const kpis = within(screen.getByRole('group', { name: '指标' }))
    expect(kpis.getByText('AI-DLC Lean').parentElement).toHaveAccessibleName('AI-DLC Lean（运行机 第 1 次）：做完 2 · 跳过 4 · 在做：发布 · 运维整段不需要')
    expect(kpis.getByTestId('br-steps-counter')).toHaveTextContent('4/5')
    expect(kpis.getByText('credits').parentElement).toHaveTextContent('运行机的花费在运行机上')
    // its command path only
    expect(within(graph()).getAllByTestId('kt-cmd').map((c) => c.getAttribute('data-run'))).toEqual(['runner-1'])
    pick(/^本机会话/)
    expect(laneIds()).toEqual(['requirements', 'application-design', 'units'])
    expect(screen.getByTestId('kt-station')).toHaveAccessibleName('还没有检查结果')
    expect(kpis.getByText('AI-DLC Lean').parentElement).toHaveAccessibleName('AI-DLC Lean（本机会话）：做完 3 · 跳过 2 · 做完 · 交出去了 · 构思整段不需要')
    expect(screen.getByTestId('kt-conveyor')).toHaveTextContent('3 次提交 · 本机 3 · 交接 1')
  })

  test('nothing is renumbered: a round keeps its 第 N 轮 and an event its place when its run is picked', () => {
    const b = worldBoard('r1/PAY-9')
    const ci = b.nodes.find((n) => n.id === 'build-and-test')!
    ci.rounds[0] = { ...ci.rounds[0], run: LOCAL }
    const runner = inRun(b, RUNNER)
    expect(runner.nodes.find((n) => n.id === 'build-and-test')!.rounds.map((r) => r.n)).toEqual([2, 3, 4, 5])
    expect(inRun(b, LOCAL).nodes.find((n) => n.id === 'build-and-test')!.rounds.map((r) => r.n)).toEqual([1])
    expect(runner.events.map((e) => e.seq)).toEqual(b.events.filter((e) => e.run === RUNNER).map((e) => e.seq))
    expect(inRun(b, 'all')).toBe(b)
  })

  test('a lane of a round from the middle says its own number (第 4 轮), not how many it shows', async () => {
    const b = worldBoard('r1/PAY-9')
    const ci = b.nodes.find((n) => n.id === 'build-and-test')!
    ci.rounds = ci.rounds.map((r, i) => (i < 3 ? { ...r, run: LOCAL } : r))
    serve(() => b)
    render(<BoardPage entry={{ ...PAY9, run: RUNNER }} {...props()} />)
    await screen.findByTestId('kt-graph')
    const lane = screen.getAllByTestId('kt-lane').find((l) => l.dataset.node === 'build-and-test')!
    expect(lane).toHaveTextContent('第 5 轮')
    expect(within(lane).getByTestId('kt-rounds')).toHaveTextContent(/第 4 轮.*第 5 轮/)
    expect(within(lane).getByTestId('kt-rounds')).not.toHaveTextContent('第 1 轮')
  })
})

describe('选择运行 · the address', () => {
  test('the run round-trips through the URL; a value that is not a run is left out', () => {
    for (const run of ['local', 'runner-2', 'child-PAY-12', 'parent-PAY-11']) {
      const v = { kind: 'board' as const, repo: 'r1', key: 'PAY-9', run: run as RunKey, out: { node: 'release', round: 2 } }
      expect(parseView(searchOf(v))).toEqual(v)
    }
    expect(parseView('?view=board&repo=r1&key=PAY-9&run=runner-0')).toEqual({ kind: 'board', repo: 'r1', key: 'PAY-9' })
    expect(parseView('?view=board&repo=r1&key=PAY-9&run=%3Cb%3E')).toEqual({ kind: 'board', repo: 'r1', key: 'PAY-9' })
  })

  test('a pick is one history entry and 后退 returns to the run before; opening and closing 产出 adds none', async () => {
    serve(() => worldBoard('r1/PAY-9'))
    window.history.replaceState(null, '', '/apps/brain?view=board&repo=r1&key=PAY-9')
    render(<BrainApp pollMs={60_000} />)
    await screen.findByTestId('kt-graph')
    const n = window.history.length
    pick(/^运行机 第 1 次/)
    await waitFor(() => expect(parseView(window.location.search)).toEqual({ kind: 'board', repo: 'r1', key: 'PAY-9', run: 'runner-1' }))
    expect(window.history.length).toBe(n + 1)
    fireEvent.click(screen.getByRole('button', { name: '产出' }))
    await waitFor(() => expect(parseView(window.location.search)).toMatchObject({ run: 'runner-1', out: { node: 'release' } }))
    fireEvent.keyDown(window, { key: 'Escape' })
    await waitFor(() => expect(parseView(window.location.search)).not.toHaveProperty('out'))
    expect(window.history.length).toBe(n + 1)
    const board = screen.getByTestId('br-board')
    act(() => window.history.back())
    await waitFor(() => expect(screen.getByTestId('kt-runpick')).toHaveTextContent('运行：全部'))
    expect(parseView(window.location.search)).toEqual({ kind: 'board', repo: 'r1', key: 'PAY-9' })
    expect(laneIds()).toContain('requirements')
    expect(screen.getByTestId('br-board')).toBe(board)              // the board followed the entry, it was not opened again
    act(() => window.history.forward())
    await waitFor(() => expect(screen.getByTestId('kt-runpick')).toHaveTextContent('运行：运行机 第 1 次'))
  }, 20_000)   // the whole app with PAY-9's board: slow when every file runs at once

  test('a link with ?run= opens on that run; one the board does not have shows the whole way', async () => {
    serve(() => worldBoard('r1/PAY-9'))
    render(<BoardPage entry={{ ...PAY9, run: LOCAL }} {...props()} />)
    await screen.findByTestId('kt-graph')
    expect(screen.getByTestId('kt-runpick')).toHaveTextContent('运行：本机会话')
    expect(laneIds()).toEqual(['requirements', 'application-design', 'units'])
    cleanup()
    serve(() => worldBoard('r1/PAY-9'))
    render(<BoardPage entry={{ ...PAY9, run: 'runner-3' as RunKey }} {...props()} />)
    await screen.findByTestId('kt-graph')
    expect(screen.getByTestId('kt-runpick')).toHaveTextContent('运行：全部')
    expect(dimmed()).toEqual([])
  })
})

describe('Esc closes what is on top first', () => {
  // a key goes to the focused element and bubbles up to the page, as a real Esc does
  const esc = () => fireEvent.keyDown(document.body, { key: 'Escape' })
  const box = (id: string) => graph().querySelector<SVGGElement>(`g.kt-node[data-node="${id}"]`)!

  test('the 运行 menu over a box looked at: the menu goes, the box stays looked at; the next Esc lets it go', async () => {
    serve(() => worldBoard('r1/PAY-9'))
    render(<BoardPage entry={PAY9} {...props()} />)
    await screen.findByTestId('kt-graph')
    fireEvent.click(box('release'))
    fireEvent.click(screen.getByTestId('kt-runpick'))
    expect(screen.getByRole('menu', { name: '选择运行' })).toBeInTheDocument()
    esc()
    expect(screen.queryByRole('menu', { name: '选择运行' })).toBeNull()
    expect(screen.getByTestId('kt-focusbar')).toHaveTextContent('发布')
    esc()
    expect(screen.queryByTestId('kt-focusbar')).toBeNull()
  })

  test('a box’s card: Esc hides it and nothing else', async () => {
    serve(() => worldBoard('r1/PAY-9'))
    render(<BoardPage entry={PAY9} {...props()} />)
    await screen.findByTestId('kt-graph')
    fireEvent.click(box('release'))
    fireEvent.mouseEnter(box('build-and-test'))
    await screen.findByRole('dialog', { name: '构建测试做了什么' })
    esc()
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '构建测试做了什么' })).toBeNull())
    expect(screen.getByTestId('kt-focusbar')).toHaveTextContent('发布')
  })
})
