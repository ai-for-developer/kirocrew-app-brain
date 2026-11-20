import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'
import { colOf } from '../cto/Flow'
import { fx } from '../test/data'
import { MERGE_STEPS, MR, MR_URL, WAITS, fastBoard, fastDetail, fastRow } from '../test/fast'
import { API, installFixtureRoutes } from '../test/routes'
import type { Requirement, RequirementDetail } from '../types'
import { DetailView } from './Detail'
import { OverviewView } from './Overview'
import type { ViewProps } from './props'

function props(over: Partial<ViewProps> = {}): ViewProps {
  return { status: fx.status(), go: vi.fn(), replace: vi.fn(), reloadStatus: vi.fn(), setStatus: vi.fn(), pollMs: 60_000, ...over }
}

function renderDetail(d: RequirementDetail = fastDetail(), board = fastBoard()) {
  installFixtureRoutes({ [`GET ${API}/requirements/*`]: () => d, [`GET ${API}/board/*`]: () => board })
  const p = props()
  render(<DetailView entry={{ repo: 'r1', key: 'PAY-17', from: 'overview' }} {...p} />)
  return p
}
const now = () => screen.getByRole('region', { name: '此刻' })

describe('需求现状 of a fast run (快速)', () => {
  test('no 交接给运行机: a fast run ends with its MR, never with the runner; the Lean card gives way to its five steps', async () => {
    renderDetail()
    const card = await screen.findByTestId('fast-card')
    expect((await within(card).findAllByTestId('fast-step')).map((li) => li.textContent)).toEqual([
      '1 · 做完写代码（做完）提交了 3 次', '2 · 做完自动检查（做完）3 项都过', '3 · 等你开 MR（等你）', '4 · 还没轮到CI（还没轮到）', '5 · 还没轮到合并（还没轮到）',
    ])
    expect(card).toHaveTextContent('代码写好了，等开 MR')
    expect(screen.queryByTestId('handover')).toBeNull()
    expect(screen.queryByText('AI-DLC Lean')).toBeNull()
    expect(screen.queryByTestId('lean-phases')).toBeNull()
  })

  test('its way in words where Lean’s progress was, and the MR it does not have yet says how it comes', async () => {
    renderDetail()
    await screen.findAllByTestId('fast-step')
    const cycle = screen.getByRole('article', { name: '周期' })
    expect(within(cycle).getByTestId('lean-progress')).toHaveTextContent(/^快速 · 代码写好了，等开 MR$/)
    expect(within(cycle).getByRole('img')).toHaveAccessibleName('快速：代码写好了，等开 MR')
    expect(within(cycle).getByRole('img').querySelectorAll('i.is-pass')).toHaveLength(2)
    expect(document.querySelector('dl.br-detail-facts')).toHaveTextContent('MR还没有：做完了点「开 MR」')
  })

  test('开审查会话 never shows on a fast run, even when its waits say a review', async () => {
    renderDetail(fastDetail({ waits: { ...WAITS.stopped, review: true } }))
    await screen.findByTestId('fast-card')
    expect(within(now()).queryByRole('button', { name: '开审查会话' })).toBeNull()
    expect(now()).not.toHaveTextContent('审查')
  })

  test('waiting for people to merge its MR: 此刻 says so and links the MR', async () => {
    renderDetail(fastDetail({ waits: WAITS.merge, mr: MR }), fastBoard({ waits: WAITS.merge, mr: MR }, MERGE_STEPS))
    await screen.findByTestId('fast-card')
    expect(now()).toHaveTextContent('MR !12：CI 过了，等人审、合并')
    expect(within(now()).getByRole('link', { name: '看 MR' })).toHaveAttribute('href', MR_URL)
    expect(screen.getByTestId('fast-card')).toHaveTextContent('MR !12 等合并')
  })
})

describe('交付总览 with fast runs', () => {
  const rows = (): Requirement[] => [
    fastRow(), fastRow({ key: 'PAY-18', title: '优惠码过期提醒', waits: WAITS.merge, mr: MR }),
    fastRow({ key: 'PAY-19', title: '退款页文案', waits: { ...WAITS.stopped, review: true }, session_url: '/chat?sid=brain-PAY-19-dev' }),
  ]

  test('the Lean column says each fast run’s way; its step on 需求现状 (开 MR there); never 开审查会话', async () => {
    const ov = fx.overview()
    ov.rows = rows()
    installFixtureRoutes({ [`GET ${API}/overview*`]: () => ov })
    const p = props()
    render(<OverviewView entry={{}} {...p} />)
    const table = await screen.findByTestId('req-table')
    const cell = (key: string) => within(table.querySelector(`tr[data-key="${key}"]`) as HTMLElement).getByTestId('lean-cell')
    expect(cell('PAY-17')).toHaveTextContent(/^快速 · 代码写好了，等开 MR$/)
    expect(cell('PAY-18')).toHaveTextContent(/^快速 · MR !12 等合并$/)
    const yours = screen.getByTestId('your-turn')
    expect(within(yours).queryByRole('button', { name: '开审查会话' })).toBeNull()
    await userEvent.setup().click(within(yours.querySelector('li[data-key="PAY-17"]') as HTMLElement).getByRole('button', { name: '开 MR ↗' }))
    expect(p.go).toHaveBeenLastCalledWith({ kind: 'item', repo: 'r1', key: 'PAY-17', from: 'overview' })
  })

  test('on the flow board a fast run is 构建 until its MR is open, then 发布验收, merged 已合并', () => {
    const [ready, merge] = rows()
    expect([colOf(ready), colOf(merge), colOf({ ...merge, state: 'merged' })]).toEqual(['构建', '发布验收', '已合并'])
  })
})
