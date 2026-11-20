import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'
import { fx } from '../test/data'
import { MERGE_STEPS, MR, MR_URL, PIPELINE_URL, READY_STEPS, WAITS, fastBoard } from '../test/fast'
import { API, installFixtureRoutes } from '../test/routes'
import { navigations } from '../test/stubs/app-sdk'
import type { ReqBoard } from '../types'
import type { ViewProps } from '../views/props'
import { BoardPage } from './BoardPage'

function renderBoard(b: ReqBoard = fastBoard()) {
  installFixtureRoutes({ [`GET ${API}/board/*`]: () => b })
  const p: ViewProps = { status: fx.status(), go: vi.fn(), replace: vi.fn(), reloadStatus: vi.fn(), setStatus: vi.fn(), pollMs: 60_000 }
  render(<BoardPage entry={{ repo: 'r1', key: 'PAY-17' }} {...p} />)
  return p
}
const steps = () => screen.findByTestId('br-fast-steps')
const box = (graph: HTMLElement, id: string) => graph.querySelector<SVGGElement>(`g.kt-node[data-node="${id}"]`)!
const merging = () => fastBoard({ key: 'PAY-18', waits: WAITS.merge, mr: MR }, MERGE_STEPS)

describe('运行过程 of a fast run (快速)', () => {
  test('its five steps where the stage graph goes, in the graph’s look; no stage lanes and no 验收', async () => {
    renderBoard()
    const graph = await steps()
    expect(screen.queryByTestId('kt-graph')).toBeNull()
    expect(Array.from(graph.querySelectorAll('g.kt-node')).map((g) => [g.getAttribute('data-node'), g.getAttribute('class')])).toEqual([
      ['code', 'kt-node is-done'], ['verify', 'kt-node is-done'], ['mr', 'kt-node is-awaiting_approval'], ['ci', 'kt-node is-waiting'], ['merge', 'kt-node is-waiting'],
    ])
    expect(Array.from(graph.querySelectorAll('path.kt-edge')).map((e) => [e.getAttribute('data-edge'), e.getAttribute('class')])).toEqual([
      ['code>verify', 'kt-edge is-used'], ['verify>mr', 'kt-edge is-ready'], ['mr>ci', 'kt-edge is-pending'], ['ci>merge', 'kt-edge is-pending'],
    ])
    expect(box(graph, 'mr')).toHaveAccessibleName('开 MR：等你')
    expect(within(graph).getByRole('group')).toHaveAccessibleName('快速需求的路：5 步，2 步做完，1 步等你。')
    expect(screen.queryByTestId('kt-lanes')).toBeNull()
    expect(screen.queryByTestId('br-accept')).toBeNull()
    expect(screen.getByTestId('kt-conveyor')).toBeInTheDocument()
  })

  test('写代码 goes to its commits on the conveyor, 自动检查 to the check wall', async () => {
    const user = userEvent.setup()
    renderBoard()
    const graph = await steps()
    const conveyor = screen.getByTestId('kt-conveyor')
    const wall = screen.getByTestId('kt-wall')
    conveyor.scrollIntoView = vi.fn()
    wall.scrollIntoView = vi.fn()
    await user.click(box(graph, 'code'))
    expect(conveyor.scrollIntoView).toHaveBeenCalledTimes(1)
    box(graph, 'verify').focus()
    await user.keyboard('{Enter}')
    expect(wall.scrollIntoView).toHaveBeenCalledTimes(1)
    expect(conveyor.scrollIntoView).toHaveBeenCalledTimes(1)
  })

  test('its MR open and CI green, people to merge: 开 MR, CI and 合并 open their page on GitLab; 合并 waits on someone else', async () => {
    renderBoard(merging())
    const graph = await steps()
    const href = (id: string) => box(graph, id).closest('a')?.getAttribute('href')
    expect([href('mr'), href('ci'), href('merge')]).toEqual([MR_URL, PIPELINE_URL, MR_URL])
    expect(box(graph, 'merge')).toHaveClass('is-awaiting_approval', 'is-other')
    expect(box(graph, 'merge').closest('a')).toHaveAccessibleName('合并：等别人，等人审、合并 ↗')
    expect(box(graph, 'code').closest('a')).toBeNull()
  })

  test('the counters: 检查通过 is its newest verify on the wall, 快速 says where it is, 这条路走到哪了 counts its steps', async () => {
    renderBoard()
    await steps()
    expect(screen.getByTestId('br-checks-counter')).toHaveAccessibleName('检查通过 3/3')
    expect(screen.getByTestId('br-checks-say')).toHaveTextContent('3 项都过')
    const fast = screen.getByTestId('br-fast-kpi')
    expect(fast).toHaveAccessibleName('快速：代码写好了，等开 MR')
    expect(fast).toHaveTextContent(`${fx.board().belt.filter((b) => b.kind === 'commit').length}次提交`)
    expect(screen.queryByText('AI-DLC Lean')).toBeNull()
    expect(screen.getByTestId('br-steps-counter')).toHaveTextContent('2/5')
    const way = screen.getByTestId('br-steps-counter').closest('.kt-kpi') as HTMLElement
    expect(within(way).getAllByRole('button').map((b) => b.getAttribute('aria-label'))).toEqual(['写代码：做完', '自动检查：做完', '开 MR：等你', 'CI：还没轮到', '合并：还没轮到'])
    expect(way).toHaveTextContent('没有在做的 · 等你')
  })

  test('a verify that failed: 检查通过 says so in its 自动检查 step’s words', async () => {
    const b = fastBoard({ waits: WAITS.stopped }, READY_STEPS.map((s) => (s.id === 'verify' ? { ...s, status: 'failed', text: '没过：pytest（第 2 次）' } : s)))
    b.checks.cells = b.checks.cells.map((c) => (c.id === 'verify-pytest' ? { ...c, status: 'fail' } : c))
    renderBoard(b)
    const graph = await steps()
    expect(screen.getByTestId('br-checks-counter')).toHaveAccessibleName('检查通过 2/3')
    expect(screen.getByTestId('br-checks-say')).toHaveTextContent('没过：pytest（第 2 次）')
    expect(box(graph, 'verify')).toHaveClass('is-failed')
    expect(box(graph, 'verify').querySelector('.kt-node-x')).not.toBeNull()
  })

  test('the check wall has its 自动检查, CI and 风险目录, then the Brain’s', async () => {
    renderBoard()
    await steps()
    const wall = screen.getByTestId('kt-wall')
    expect(within(wall).getAllByRole('group').map((g) => g.getAttribute('aria-label'))).toEqual(['自动检查', 'CI', '风险目录', 'Brain 和仓库'])
  })

  test('打开会话 is this machine’s session for the run, though a fast board has no runs', async () => {
    const user = userEvent.setup()
    const b = fastBoard()
    expect(b.runs).toEqual([])
    renderBoard(b)
    await steps()
    await user.click(screen.getByRole('button', { name: '打开会话' }))
    expect(navigations).toEqual([b.sessions[0].url])
  })
})
