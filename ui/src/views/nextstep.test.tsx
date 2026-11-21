import { cleanup, render, screen, within } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import world from '../../preview/world.json'
import { Kpis } from '../board/Header'
import { ClockContext } from '../board/hooks'
import { API, installFixtureRoutes } from '../test/routes'
import { worldBoard } from '../test/world'
import type { Overview, RequirementDetail, Status } from '../types'
import { DetailView } from './Detail'
import { OverviewView } from './Overview'
import type { ViewProps } from './props'

const w = world as unknown as { status: Status; overview: Overview; details: Record<string, RequirementDetail>; boards: Record<string, unknown> }
const props = (): ViewProps => ({ status: w.status, go: vi.fn(), replace: vi.fn(), reloadStatus: vi.fn(), setStatus: vi.fn(), pollMs: 60_000 })
/** Where the requirement is on its way, as a page says it: Lean's progress (在做：代码生成, 下一步：发布, 全部做完, 还没开工),
 * or a fast run's (flows.flowNow: 在写代码, 代码写好了，等开 MR, MR !12 等合并, 改到风险目录). */
const said = (text: string | null | undefined) => (text ?? '').split(' · ').find((p) => /^(在做|下一步)：|^全部做完|^还没开|^在写代码|^代码写好了|^MR !\d+|^改到风险目录/.test(p)) ?? ''

test('下一步 is one rule: every preview requirement says the same on 交付总览, 需求现状 and the board', async () => {
  const keys = w.overview.rows.map((r) => `${r.repo}/${r.key}`).filter((k) => w.details[k] && w.boards[k])
  expect(keys).toContain('r1/PAY-16')
  installFixtureRoutes({ [`GET ${API}/overview*`]: () => w.overview })
  render(<OverviewView entry={{}} {...props()} />)
  const table = await screen.findByTestId('req-table')
  const overview = Object.fromEntries(Array.from(table.querySelectorAll('tr[data-key]')).map((tr) =>
    [`${w.overview.rows.find((r) => r.key === tr.getAttribute('data-key'))?.repo}/${tr.getAttribute('data-key')}`, said(within(tr as HTMLElement).getByTestId('lean-cell').textContent)]))
  cleanup()
  const pages: Record<string, string[]> = {}
  for (const k of keys) {
    const [repo, key] = k.split('/')
    installFixtureRoutes({ [`GET ${API}/requirements/*`]: () => w.details[k] })
    render(<DetailView entry={{ repo, key, from: 'overview' }} onBack={vi.fn()} {...props()} />)
    await screen.findByRole('region', { name: '此刻' })
    const head = (screen.queryByRole('region', { name: 'Lean 进度' }) ?? screen.queryByTestId('fast-card'))?.querySelector('.cto-chart-head .cto-dim')?.textContent
    cleanup()
    const b = worldBoard(k)
    render(<ClockContext.Provider value={{ at: b.at, receivedAt: Date.now() }}><Kpis board={b} reducedMotion /></ClockContext.Provider>)
    const card = (screen.queryByTestId('br-fast-kpi') ?? screen.getByText('AI-DLC Lean').parentElement!).querySelector('.br-lean-next')!.textContent
    cleanup()
    pages[k] = [overview[k], head && said(head.replace(/ · (交接以后|审批的那一行).*$/, '')), said(card)].filter((x): x is string => !!x)
  }
  const differ = Object.entries(pages).filter(([, p]) => new Set(p).size > 1)
  expect(differ).toEqual([])
  expect(pages['r1/PAY-16']).toEqual(Array(3).fill('下一步：功能设计'))
  expect(pages['r1/PAY-17']).toEqual(Array(3).fill('代码写好了，等开 MR'))
  expect(pages['r1/PAY-18']).toEqual(Array(3).fill('MR !12 等合并'))
})
