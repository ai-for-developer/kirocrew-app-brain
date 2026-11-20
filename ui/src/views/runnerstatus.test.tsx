import { render, screen, within } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { fx } from '../test/data'
import { API, installFixtureRoutes } from '../test/routes'
import { apiCalls } from '../test/stubs/app-sdk'
import type { RunnerStatus } from '../types'
import type { ViewProps } from './props'
import { NO_RUNNER, NO_RUNNER_YET, RunnerStatusPage, WHO_UNKNOWN } from './RunnerStatus'

const props = (over: Partial<ViewProps> = {}): ViewProps => ({
  status: fx.status(), go: vi.fn(), replace: vi.fn(), reloadStatus: vi.fn(), setStatus: vi.fn(), pollMs: 60_000, ...over,
})

/** The fixture, its times moved to now: read 10 s ago, the runner's last act 3 minutes ago. */
function now(): RunnerStatus {
  const st = fx.teamStatus()
  const t = Date.now() / 1000
  const seen = st.repos[0].runner!.seen!
  seen.at = t - 10
  seen.last_at = t - 180
  seen.at_work[0].at = t - 180
  seen.at_work[2].at = t - 2 * 3600
  seen.waiting[0].at = t - 3 * 3600
  return st
}

describe('运行机 on a personal install', () => {
  test('each repo’s team runner: the MRs at work and why, the questions waiting on me, when it last acted; a repo without one in one line', async () => {
    installFixtureRoutes({ [`GET ${API}/team/status`]: () => now() })
    render(<RunnerStatusPage {...props()} />)
    const pay = await screen.findByRole('region', { name: 'shop-pay' })
    expect(pay).toHaveTextContent('在做 3')
    expect(pay).toHaveTextContent('等你回答 1')
    expect(pay).toHaveTextContent('上次动作 3 分钟前')
    expect(within(pay).getByTitle('运行机在 GitLab 上的账号：@brain-bridge')).toHaveTextContent('shop/shop-pay')
    const work = within(pay).getByRole('region', { name: '在做的 MR' })
    const rows = within(work).getAllByRole('row').slice(1)
    expect(rows.map((r) => r.getAttribute('data-why'))).toEqual(['running', 'running', 'patrol'])
    expect(within(rows[0]).getByRole('link', { name: /!41/ })).toHaveAttribute('href', 'https://gitlab.example.com/shop/shop-pay/-/merge_requests/41')
    expect(rows[0]).toHaveTextContent('退款审核在做交接过来的需求，运行机接着做3 分钟前')
    expect(rows[2]).toHaveTextContent('折扣按百分比算巡检MR 巡检最近 24 小时在上面回复过2 小时前')
    const waiting = within(pay).getByRole('region', { name: '等你回答' })
    expect(waiting).toHaveTextContent('!41 退款审核')
    const q = within(waiting).getByTestId('question')
    expect(q).toHaveTextContent('D-7')
    expect(q).toHaveTextContent('澄清')
    expect(q).toHaveTextContent('退款超过 500 元要不要人工审核？')
    expect(within(q).getByRole('list', { name: '选项' })).toHaveTextContent('A 要，客服审核以后再退')
    // answered on the MR, at the question's own note; nothing is answered here
    expect(within(q).getByRole('link', { name: '去 MR 上回答' })).toHaveAttribute('href', 'https://gitlab.example.com/shop/shop-pay/-/merge_requests/41#note_7101')
    expect(within(q).queryByRole('button')).toBeNull()
    expect(pay).toHaveTextContent('10 秒前从 GitLab 读的')
    const tools = screen.getByRole('region', { name: 'shop-tools' })
    expect(tools).toHaveTextContent(`shop-tools${NO_RUNNER}`)
    expect(within(tools).queryByRole('region')).toBeNull()
    expect(screen.queryByText(NO_RUNNER_YET)).toBeNull()
    expect(apiCalls.filter((c) => c.method !== 'GET')).toEqual([])
    expect(apiCalls.some((c) => c.path === `${API}/team` || c.path === `${API}/team/setup`)).toBe(false)
  })

  test('GitLab not read this time: what is wrong and what to do, its own words under 细节, the last read kept with when', async () => {
    installFixtureRoutes()
    render(<RunnerStatusPage {...props()} />)
    const order = await screen.findByRole('region', { name: 'shop-order' })
    const problem = within(order).getByTestId('runner-problem')
    expect(problem).toHaveTextContent(/^这次没读到 GitLab：下面是 .+前读到的/)
    expect(problem).toHaveTextContent('做什么过一会儿会再读；一直这样就看看网络，GitLab 要单点登录的先登录')
    expect(within(problem).getByText(/HTTP 502/).closest('details')).not.toHaveAttribute('open')
    expect(within(order).getByRole('region', { name: '在做的 MR' })).toHaveTextContent('现在没有在做的 MR。')
    expect(within(order).getByRole('region', { name: '等你回答' })).toHaveTextContent('没有等你回答的问题。')
    expect(screen.queryByRole('region', { name: 'shop-pay' })?.querySelector('[data-testid="runner-problem"]')).toBeNull()
  })

  test('who the person is unknown: no question is said to wait on them, and where to say who they are', async () => {
    const st = now()
    st.repos[0].runner!.me = null
    st.repos[0].runner!.seen!.waiting = []
    installFixtureRoutes({ [`GET ${API}/team/status`]: () => st })
    render(<RunnerStatusPage {...props()} />)
    const waiting = within(await screen.findByRole('region', { name: 'shop-pay' })).getByRole('region', { name: '等你回答' })
    expect(waiting).toHaveTextContent(WHO_UNKNOWN)
    expect(within(waiting).queryByTestId('question')).toBeNull()
  })

  test('no repo with a team runner yet: each in one line, and how one gets here', async () => {
    const st: RunnerStatus = { recent_h: 24, repos: [{ repo: 'r1', name: 'shop-pay', runner: null }, { repo: 'r4', name: 'shop-tools', runner: null }] }
    installFixtureRoutes({ [`GET ${API}/team/status`]: () => st })
    render(<RunnerStatusPage {...props()} />)
    expect(await screen.findByText(NO_RUNNER_YET)).toBeInTheDocument()
    expect(screen.getAllByText(NO_RUNNER)).toHaveLength(2)
  })
})
