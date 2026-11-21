import { render, screen, within } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { FIXTURE_AT, fx } from '../test/data'
import { API, installFixtureRoutes } from '../test/routes'
import { apiCalls } from '../test/stubs/app-sdk'
import type { RunnerStatus } from '../types'
import type { ViewProps } from './props'
import { NO_RUNNER, NO_RUNNER_YET, RunnerStatusPage, WHO_UNKNOWN } from './RunnerStatus'

const props = (over: Partial<ViewProps> = {}): ViewProps => ({
  status: fx.status(), go: vi.fn(), replace: vi.fn(), reloadStatus: vi.fn(), setStatus: vi.fn(), pollMs: 60_000, ...over,
})

/** The fixture as an older team server leaves it: no repo's kit.json names a status project (状态中转), so each repo
 * with a runner is its card as GitLab shows it. */
function gitlabOnly(st: RunnerStatus = fx.teamStatus()): RunnerStatus {
  return { ...st, relays: [], repos: st.repos.map((r) => ({ ...r, relay: null })) }
}

/** That, its times moved to now: read 10 s ago, the runner's last act 3 minutes ago. */
function now(): RunnerStatus {
  const st = gitlabOnly()
  const t = Date.now() / 1000
  const seen = st.repos[0].runner!.seen!
  seen.at = t - 10
  seen.last_at = t - 180
  seen.at_work[0].at = t - 180
  seen.at_work[2].at = t - 2 * 3600
  seen.waiting[0].at = t - 3 * 3600
  return st
}

describe('运行机 on a personal install, its runner as GitLab shows it (no 状态中转)', () => {
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
    installFixtureRoutes({ [`GET ${API}/team/status`]: () => gitlabOnly() })
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
    const st: RunnerStatus = { recent_h: 24, at: FIXTURE_AT, stale_after_s: 2400, relays: [],
      repos: [{ repo: 'r1', name: 'shop-pay', runner: null, relay: null }, { repo: 'r4', name: 'shop-tools', runner: null, relay: null }] }
    installFixtureRoutes({ [`GET ${API}/team/status`]: () => st })
    render(<RunnerStatusPage {...props()} />)
    expect(await screen.findByText(NO_RUNNER_YET)).toBeInTheDocument()
    expect(screen.getAllByText(NO_RUNNER)).toHaveLength(2)
  })
})

describe('团队运行机 on a personal install, from the team server’s 状态中转', () => {
  const keys = (table: HTMLElement) => Array.from(table.querySelectorAll('tbody tr')).map((r) => r.getAttribute('data-key'))
  const rowOf = (table: HTMLElement, key: string) => table.querySelector(`tbody tr[data-key="${key}"]`) as HTMLElement

  test('the server’s strip, the runner at work, 全队交付 with what waits on me first, and the questions waiting on me', async () => {
    const user = (await import('@testing-library/user-event')).default.setup()
    installFixtureRoutes()
    const p = props()
    render(<RunnerStatusPage {...p} />)
    const strip = await screen.findByTestId('relay-strip')
    expect(within(strip).getByTestId('relay-version')).toHaveTextContent('版本 0.4.2')
    expect(within(strip).getByTestId('relay-written')).toHaveTextContent('状态写于 3 分钟前')
    expect(within(strip).queryByTestId('relay-stale')).toBeNull()
    const projects = within(strip).getAllByRole('listitem').map((li) => li.textContent)
    expect(projects).toEqual(['shop/shop-pay在接交接交接 · 巡检 · 测试环境报错', 'shop/shop-order在接交接交接 · 巡检'])
    expect(strip).toHaveTextContent('从 GitLab 的 shop/brain-status 读（30 秒前）')
    // the runner at work: its hand-overs and what each waits on; MR 巡检 and 测试环境报错 without the server's sessions
    const runner = screen.getByTestId('relay-runner')
    expect(runner).toHaveTextContent('交接过来的需求 3 个')
    expect(runner).toHaveTextContent('brain-patrol 会话在做 2/2')
    const ho = within(runner).getByTestId('relay-handovers')
    expect(keys(ho)).toEqual(['PAY-4', 'PAY-9', 'ORD-3'])
    expect(rowOf(ho, 'PAY-4')).toHaveTextContent('PAY-4 !41退款审核 · alice · shop/shop-pay已派给会话等产品在 MR 上验收 D-7已等 4 小时')
    expect(within(rowOf(ho, 'ORD-3')).getByTestId('waits-me')).toHaveTextContent('等你')
    expect(within(rowOf(ho, 'PAY-4')).queryByTestId('waits-me')).toBeNull()
    expect(within(screen.getByTestId('patrol')).getAllByRole('row')[0]).not.toHaveTextContent('会话')
    expect(within(screen.getByTestId('staging')).getAllByRole('row')[0]).not.toHaveTextContent('会话')
    expect(screen.queryByRole('button', { name: /打开会话/ })).toBeNull()
    // 全队交付: every requirement, what waits on me first, then the open ones newest first, then the merged
    const delivery = screen.getByTestId('team-delivery')
    expect(within(delivery).getByTestId('delivery-count')).toHaveTextContent('9 个需求 · 等你的 1 个 · 在做的和近 14 天合并的')
    const table = within(delivery).getByTestId('delivery-table')
    expect(keys(table)).toEqual(['ORD-3', 'PAY-9', 'PAY-11', 'PAY-4', 'PAY-12', 'ORD-5', 'PAY-6', 'ORD-1', 'PAY-2'])
    expect(rowOf(table, 'ORD-3')).toHaveAttribute('data-mine', 'true')
    expect(rowOf(table, 'ORD-3')).toHaveTextContent('ORD-3 订单导出carol · shop/shop-order交给运行机!14CI 通过等你等开发的人在 MR 上回答 D-2（设计）已等 2 小时')
    expect(rowOf(table, 'PAY-4')).toHaveTextContent('你（alice） · shop/shop-pay发布 · 验收!41CI 通过等产品在 MR 上验收 D-7')
    expect(within(rowOf(table, 'PAY-9')).getByTestId('ci')).toHaveTextContent('CI 在跑')
    expect(within(rowOf(table, 'PAY-12')).getByTestId('ci')).toHaveTextContent('CI 没过')
    expect(rowOf(table, 'PAY-6')).toHaveTextContent('PAY-6 对账单导出没人认领 · shop/shop-pay构建没开 MR待认领：谁开工就归谁')
    expect(within(rowOf(table, 'PAY-2')).getByTestId('deployed')).toHaveTextContent('已部署到测试环境')
    expect(within(rowOf(table, 'ORD-1')).getByTestId('deployed')).toHaveTextContent('还没部署到测试环境')
    expect(within(rowOf(table, 'PAY-11')).queryByTestId('deployed')).toBeNull()
    expect(Array.from(table.querySelectorAll('[data-testid="stage"]')).map((x) => x.textContent))
      .toEqual(['交给运行机', '交给运行机', '设计', '发布 · 验收', '交给运行机', '构建', '构建', '已合并', '已合并'])
    // a key of a project listed here opens its 需求现状, and 返回 comes back here
    await user.click(within(rowOf(table, 'PAY-11')).getByRole('button', { name: 'PAY-11' }))
    expect(p.go).toHaveBeenCalledWith({ kind: 'item', repo: 'r1', key: 'PAY-11', from: 'team' })
    // the questions waiting on me, as GitLab says them to this app; the repo GitLab did not answer for, said
    const waiting = screen.getByTestId('relay-waiting')
    expect(within(waiting).getByTestId('question')).toHaveTextContent('D-7')
    expect(waiting).toHaveTextContent('shop-pay !41 退款审核')
    expect(waiting).toHaveTextContent('shop-order：这次没读到 GitLab（问题没读到）')
    // the repos that name no status project keep their own lines
    expect(screen.getByRole('region', { name: 'shop-tools' })).toHaveTextContent(`shop-tools${NO_RUNNER}`)
    expect(screen.queryByRole('region', { name: 'shop-pay' })).toBeNull()
    expect(apiCalls.filter((c) => c.method !== 'GET')).toEqual([])
  })

  test('全队交付 by person and by project', async () => {
    const user = (await import('@testing-library/user-event')).default.setup()
    installFixtureRoutes()
    render(<RunnerStatusPage {...props()} />)
    const table = () => screen.getByTestId('delivery-table')
    const person = await screen.findByTestId('filter-person')
    expect(Array.from((person as HTMLSelectElement).options).map((o) => o.textContent)).toEqual(['全部', '你（alice）', 'bob', 'carol', 'dave', '没人认领'])
    await user.selectOptions(person, 'bob')
    expect(keys(table())).toEqual(['PAY-11', 'ORD-1'])
    await user.selectOptions(screen.getByTestId('filter-project'), 'shop/shop-order')
    expect(keys(table())).toEqual(['ORD-1'])
    await user.selectOptions(person, '')
    expect(keys(table())).toEqual(['ORD-3', 'ORD-5', 'ORD-1'])
    await user.selectOptions(person, (person as HTMLSelectElement).options[5].value)
    expect(screen.getByTestId('team-delivery')).toHaveTextContent('这个筛选下没有需求。')
    await user.selectOptions(screen.getByTestId('filter-project'), '')
    expect(keys(table())).toEqual(['PAY-6'])
  })

  test('a snapshot older than forty minutes: the team server may have stopped, and what it last wrote is still shown', async () => {
    const st = fx.teamStatus()
    const r = st.relays[0]
    r.stale = true
    st.at = r.snapshot!.generated_at + 45 * 60
    installFixtureRoutes({ [`GET ${API}/team/status`]: () => st })
    render(<RunnerStatusPage {...props()} />)
    const stale = await screen.findByTestId('relay-stale')
    expect(stale).toHaveTextContent('团队服务器可能停了：上次写状态是 45 分钟前')
    expect(stale).toHaveTextContent('做什么请管团队服务器的人看一下')
    expect(screen.getByTestId('relay-strip')).toHaveClass('is-bad')
    expect(screen.getByTestId('team-delivery')).toBeInTheDocument()
  })

  test('a read that did not work: why and what to do, the last snapshot kept; before any, only the strip and the questions', async () => {
    const st = fx.teamStatus()
    st.relays[0].problem = { text: '这次没读到 GitLab', todo: '过一会儿会再读；一直这样就看看网络，GitLab 要单点登录的先登录', raw: 'GET … -> HTTP 502' }
    installFixtureRoutes({ [`GET ${API}/team/status`]: () => st })
    const { unmount } = render(<RunnerStatusPage {...props()} />)
    const problem = await screen.findByTestId('relay-problem')
    expect(problem).toHaveTextContent('这次没读到 GitLab：下面是 30 秒前读到的')
    expect(screen.getByTestId('team-delivery')).toBeInTheDocument()
    unmount()
    const none = fx.teamStatus()
    Object.assign(none.relays[0], { snapshot: null, read_at: null, problem: { text: '团队服务器还没在 shop/brain-status 写过状态，或者你看不到这个项目', todo: '等团队服务器跑一轮', raw: '' } })
    installFixtureRoutes({ [`GET ${API}/team/status`]: () => none })
    render(<RunnerStatusPage {...props()} />)
    expect(await screen.findByTestId('relay-problem')).toHaveTextContent(/^团队服务器还没在 shop\/brain-status 写过状态，或者你看不到这个项目做什么等团队服务器跑一轮$/)
    expect(screen.getByTestId('relay-written')).toHaveTextContent('还没读到状态')
    expect(screen.queryByTestId('relay-runner')).toBeNull()
    expect(screen.queryByTestId('team-delivery')).toBeNull()
    expect(screen.getByTestId('relay-waiting')).toBeInTheDocument()
  })
})
