import { cleanup, render, screen, within } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { FIXTURE_AT, fx } from '../test/data'
import { API, installFixtureRoutes } from '../test/routes'
import { apiCalls } from '../test/stubs/app-sdk'
import type { RunnerStatus } from '../types'
import type { ViewProps } from './props'
import { NO_RUNNER, NO_RUNNER_YET, READ_FROM, RunnerStatusPage, WHO_UNKNOWN } from './RunnerStatus'
import { ISSUES_ALL_STARTED } from './Team'

const props = (over: Partial<ViewProps> = {}): ViewProps => ({
  status: fx.status(), go: vi.fn(), replace: vi.fn(), reloadStatus: vi.fn(), setStatus: vi.fn(), pollMs: 60_000, ...over,
})

/** The fixture as an older team server leaves it: no project has brain/status (状态中转), so each repo with a runner
 * is its card as GitLab shows it. */
function gitlabOnly(st: RunnerStatus = fx.teamStatus()): RunnerStatus {
  return { ...st, relays: [], repos: st.repos.map((r) => ({ ...r, relay: false })) }
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
    const st: RunnerStatus = { recent_h: 24, at: FIXTURE_AT, stale_after_s: 4500, relays: [],
      repos: [{ repo: 'r1', name: 'shop-pay', runner: null, relay: false }, { repo: 'r4', name: 'shop-tools', runner: null, relay: false }] }
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
    expect(projects).toEqual(['shop/shop-pay在接交接交接 · 巡检 · 测试环境报错 · 从 issue 接活', 'shop/shop-order在接交接交接 · 巡检'])
    expect(strip).toHaveTextContent(READ_FROM)
    expect(within(strip).queryByTestId('project-stale')).toBeNull()
    expect(within(strip).queryByTestId('delivery-lag')).toBeNull()
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
    expect(within(delivery).getByTestId('delivery-count')).toHaveTextContent('11 个需求 · 等你的 1 个 · 在做的和近 14 天合并、关掉的')
    const table = within(delivery).getByTestId('delivery-table')
    expect(keys(table)).toEqual(['ORD-3', 'PAY-9', 'PAY-11', 'PAY-4', 'PAY-12', 'ORD-5', 'PAY-6', 'ORD-6', 'ORD-1', 'PAY-10', 'PAY-2'])
    expect(rowOf(table, 'ORD-3')).toHaveAttribute('data-mine', 'true')
    expect(rowOf(table, 'ORD-3')).toHaveTextContent('ORD-3 订单导出carol · shop/shop-order交给运行机!14CI 通过等你等开发的人在 MR 上回答 D-2（设计）已等 2 小时')
    expect(rowOf(table, 'PAY-4')).toHaveTextContent('你（alice） · shop/shop-pay发布 · 验收!41CI 通过等产品在 MR 上验收 D-7')
    expect(within(rowOf(table, 'PAY-9')).getByTestId('ci')).toHaveTextContent('CI 在跑')
    expect(within(rowOf(table, 'PAY-12')).getByTestId('ci')).toHaveTextContent('CI 没过')
    expect(rowOf(table, 'PAY-6')).toHaveTextContent('PAY-6 对账单导出没人认领 · shop/shop-pay需求没开 MR待认领：谁开工就归谁')
    expect(within(rowOf(table, 'PAY-2')).getByTestId('deployed')).toHaveTextContent('已部署到测试环境')
    expect(within(rowOf(table, 'ORD-1')).getByTestId('deployed')).toHaveTextContent('还没部署到测试环境')
    expect(within(rowOf(table, 'PAY-11')).queryByTestId('deployed')).toBeNull()
    expect(Array.from(table.querySelectorAll('[data-testid="stage"]')).map((x) => x.textContent))
      .toEqual(['交给运行机', '交给运行机', '设计', '发布 · 验收', '交给运行机', '构建', '需求', '已合进大需求', '已合并', '已关闭', '已合并'])
    // a small requirement merged into its big one's branch, and a hand-over whose MR was closed: done, waiting on nobody
    expect(rowOf(table, 'ORD-6')).toHaveTextContent('ORD-6 导出接口carol · shop/shop-order已合进大需求!15CI 通过已合进大需求 ORD-3 的分支：等它评审、发布')
    expect(rowOf(table, 'PAY-10')).toHaveTextContent('PAY-10 退款对账你（alice） · shop/shop-pay已关闭!45CI 没过MR 已关闭')
    for (const k of ['ORD-6', 'PAY-10']) {
      expect(within(rowOf(table, k)).queryByTestId('waits-me')).toBeNull()
      expect(within(rowOf(table, k)).queryByTestId('deployed')).toBeNull()
    }
    // a key of a project listed here opens its 需求现状, and 返回 comes back here
    await user.click(within(rowOf(table, 'PAY-11')).getByRole('button', { name: 'PAY-11' }))
    expect(p.go).toHaveBeenCalledWith({ kind: 'item', repo: 'r1', key: 'PAY-11', from: 'team' })
    // the questions waiting on me, as GitLab says them to this app; the repo GitLab did not answer for, said
    const waiting = screen.getByTestId('relay-waiting')
    expect(within(waiting).getByTestId('question')).toHaveTextContent('D-7')
    expect(waiting).toHaveTextContent('shop-pay !41 退款审核')
    expect(waiting).toHaveTextContent('shop-order：这次没读到 GitLab（问题没读到）')
    // the repos whose project has no brain/status keep their own lines
    expect(screen.getByRole('region', { name: 'shop-tools' })).toHaveTextContent(`shop-tools${NO_RUNNER}`)
    expect(screen.queryByRole('region', { name: 'shop-pay' })).toBeNull()
    expect(apiCalls.filter((c) => c.method !== 'GET')).toEqual([])
  })

  test('a hand-over accepted and done says 等合并 on its chip, as its 在等 does, not brain-kit’s 已派给会话', async () => {
    const st = fx.teamStatus()
    const ho = st.relays[0].view!.runner.handovers
    ho[0] = { ...ho[0], state: 'to_merge', waits: { ...ho[0].waits, who: 'people', do: 'merge', text: '验收通过了：等 alice 在 GitLab 上合并', on: ['alice'] } }
    installFixtureRoutes({ [`GET ${API}/team/status`]: () => st })
    render(<RunnerStatusPage {...props()} />)
    const row = rowOf(await screen.findByTestId('relay-handovers'), 'PAY-4')
    const chip = row.querySelector('.br-chip') as HTMLElement
    expect(chip).toHaveTextContent('等合并')
    expect(chip).toHaveClass('tone-wait')
    expect(chip).not.toHaveClass('is-live')
    expect(row).not.toHaveTextContent('已派给会话')
    expect(row).toHaveTextContent('等合并等你验收通过了：等 alice 在 GitLab 上合并')
  })

  test('从 issue 接活: the labelled issues not started yet, mine said as 你, why each waits or was not taken; none left says where they went', async () => {
    installFixtureRoutes()
    const { unmount } = render(<RunnerStatusPage {...props()} />)
    const card = await screen.findByTestId('issues')
    expect(card).toHaveTextContent('shop/shop-pay：Developer 以上的人给 issue 打上标签 brain，运行机就认领成需求、开 MR、开工；这里是还没开工的')
    const rows = within(card).getAllByRole('row').slice(1)
    expect(rows.map((r) => r.getAttribute('data-state'))).toEqual(['new', 'refused'])
    expect(within(rows[0]).getByRole('link', { name: 'shop/shop-pay#23' })).toHaveAttribute('href', 'https://gitlab.example.com/shop/shop-pay/-/issues/23')
    expect(rows[0]).toHaveTextContent('退款单导出加上退款原因你（@alice）')
    expect(rows[0]).toHaveTextContent('刚打上标签这一轮没做完：GET /projects/shop%2Fshop-pay/issues/23/resource_label_events -> HTTP 502')
    expect(rows[1]).toHaveTextContent('退款按钮改成红色@rita')
    expect(rows[1]).toHaveTextContent('没接@rita 在项目里是 Reporter：要 Developer 以上才能让 Brain 接 issue')
    unmount()
    const started = fx.teamStatus()
    started.relays[0].view!.runner.issues = []
    installFixtureRoutes({ [`GET ${API}/team/status`]: () => started })
    const again = render(<RunnerStatusPage {...props()} />)
    expect(await screen.findByTestId('issues')).toHaveTextContent(ISSUES_ALL_STARTED)
    again.unmount()
    const none = fx.teamStatus()
    for (const p of none.relays[0].projects) p.state = { ...p.state!, sources: p.state!.sources.filter((x) => x !== 'issue-label'), issue_label: '' }
    none.relays[0].view!.runner.issues = []
    installFixtureRoutes({ [`GET ${API}/team/status`]: () => none })
    render(<RunnerStatusPage {...props()} />)
    await screen.findByTestId('relay-runner')
    expect(screen.queryByTestId('issues')).toBeNull()
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
    expect(keys(table())).toEqual(['ORD-3', 'ORD-5', 'ORD-6', 'ORD-1'])
    await user.selectOptions(person, (person as HTMLSelectElement).options[5].value)
    expect(screen.getByTestId('team-delivery')).toHaveTextContent('这个筛选下没有需求。')
    await user.selectOptions(screen.getByTestId('filter-project'), '')
    expect(keys(table())).toEqual(['PAY-6'])
  })

  test('MR 巡检 and 测试环境报错 count the sessions the team server counts, and say under the count what holds one no row shows at work', async () => {
    installFixtureRoutes()
    render(<RunnerStatusPage {...props()} />)
    const runner = await screen.findByTestId('relay-runner')
    expect(runner).toHaveTextContent('brain-patrol 会话在做 2/2')
    const pt = screen.getByTestId('patrol')
    expect(pt).toHaveTextContent('在修 2/2')
    expect(pt.querySelectorAll('tbody tr[data-state="working"]')).toHaveLength(1)
    expect(within(pt).getByTestId('held-hint')).toHaveTextContent('在修的里有 1 个下面没列出：shop/shop-pay!50 的会话还在做上一轮（MR 有了新的提交）')
    const st = screen.getByTestId('staging')
    expect(st).toHaveTextContent('在修 0/2')
    expect(within(st).queryByTestId('held-hint')).toBeNull()
  })

  test('a session the team server holds in a project not listed here is counted, and said to be in another project', async () => {
    const st = fx.teamStatus()
    const view = st.relays[0].view!
    view.runner.patrol = view.runner.patrol.map((i) => (i.iid === 52 ? { ...i, state: 'ok', problems: [] } : i))
    installFixtureRoutes({ [`GET ${API}/team/status`]: () => st })
    render(<RunnerStatusPage {...props()} />)
    const pt = await screen.findByTestId('patrol')
    expect(pt).toHaveTextContent('在修 2/2')
    expect(pt.querySelectorAll('tbody tr[data-state="working"]')).toHaveLength(0)
    expect(within(pt).getByTestId('held-hint')).toHaveTextContent('在修的里有 2 个下面没列出：shop/shop-pay!50 的会话还在做上一轮（MR 有了新的提交）；另外 1 个在别的项目里')
    view.runner.patrol_hidden = []
    installFixtureRoutes({ [`GET ${API}/team/status`]: () => st })
    cleanup()
    render(<RunnerStatusPage {...props()} />)
    expect(within(await screen.findByTestId('patrol')).getByTestId('held-hint')).toHaveTextContent('在修的里有 2 个下面没列出：在别的项目里')
  })

  test('a snapshot of an older team server, without each source’s count: the cards count their rows at work, as before', async () => {
    const st = fx.teamStatus()
    const view = st.relays[0].view!
    view.server.held = null
    view.runner.patrol_hidden = []
    view.runner.staging_hidden = []
    installFixtureRoutes({ [`GET ${API}/team/status`]: () => st })
    render(<RunnerStatusPage {...props()} />)
    const runner = await screen.findByTestId('relay-runner')
    expect(runner).toHaveTextContent('brain-patrol 会话在做 2/2')
    expect(screen.getByTestId('patrol')).toHaveTextContent('在修 1/2')
    expect(screen.getByTestId('staging')).toHaveTextContent('在修 0/2')
    expect(screen.queryByTestId('held-hint')).toBeNull()
  })

  test('a snapshot two heartbeats old: the team server may have stopped, and what it last wrote is still shown', async () => {
    const st = fx.teamStatus()
    const r = st.relays[0]
    r.stale = true
    r.projects.forEach((x) => { x.stale = true })
    st.at = r.view!.generated_at + 80 * 60
    installFixtureRoutes({ [`GET ${API}/team/status`]: () => st })
    render(<RunnerStatusPage {...props()} />)
    const stale = await screen.findByTestId('relay-stale')
    expect(stale).toHaveTextContent('团队服务器可能停了：上次写状态是 1 小时前')
    expect(stale).toHaveTextContent('做什么请管团队服务器的人看一下')
    expect(screen.getByTestId('relay-strip')).toHaveClass('is-bad')
    expect(screen.getByTestId('team-delivery')).toBeInTheDocument()
  })

  test('a read that did not work: why and what to do, on its project, the last snapshot kept; before any, the strip says why and each repo keeps its card as GitLab shows it', async () => {
    const st = fx.teamStatus()
    st.relays[0].projects[1].problem = { text: '这次没读到 GitLab', todo: '过一会儿会再读；一直这样就看看网络，GitLab 要单点登录的先登录', raw: 'GET … -> HTTP 502' }
    installFixtureRoutes({ [`GET ${API}/team/status`]: () => st })
    const { unmount } = render(<RunnerStatusPage {...props()} />)
    const problem = await screen.findByTestId('relay-problem')
    expect(problem.closest('li')).toHaveAttribute('data-project', 'shop/shop-order')
    expect(problem).toHaveTextContent('这次没读到 GitLab：下面是 30 秒前读到的')
    expect(screen.getByTestId('relay-strip')).toHaveClass('is-bad')
    expect(screen.getByTestId('team-delivery')).toBeInTheDocument()
    unmount()
    const none = fx.teamStatus()
    const first = none.relays[0]
    first.view = null
    first.projects = first.projects.map((x) => ({ ...x, read_at: null, written_at: null, state: null, delivery_at: null, version: null,
      problem: { text: '团队服务器还没在这个项目的 brain/status 上写过状态', todo: '等团队服务器跑一轮', raw: '' } }))
    none.repos = none.repos.map((r) => ({ ...r, relay: false }))      // the backend shows them from GitLab until one is read
    installFixtureRoutes({ [`GET ${API}/team/status`]: () => none })
    render(<RunnerStatusPage {...props()} />)
    const said = await screen.findAllByTestId('relay-problem')
    expect(said.map((x) => x.textContent)).toEqual(Array(2).fill('团队服务器还没在这个项目的 brain/status 上写过状态做什么等团队服务器跑一轮'))
    expect(screen.getByTestId('relay-written')).toHaveTextContent('还没读到状态')
    expect(screen.queryByTestId('relay-version')).toBeNull()
    expect(screen.queryByTestId('relay-runner')).toBeNull()
    expect(screen.queryByTestId('team-delivery')).toBeNull()
    const pay = screen.getByRole('region', { name: 'shop-pay' })
    expect(within(pay).getByRole('region', { name: '在做的 MR' })).toBeInTheDocument()
    expect(within(pay).getByRole('region', { name: '等你回答' })).toHaveTextContent('D-7')
    expect(screen.getByRole('region', { name: 'shop-order' })).toBeInTheDocument()
    expect(screen.queryByTestId('relay-waiting')).toBeNull()      // each card has its own 等你回答: said once
  })

  test('one project whose state is old says so on its line, and rows read rounds before say when', async () => {
    const st = fx.teamStatus()
    const [pay, order] = st.relays[0].projects
    order.stale = true
    order.written_at = st.at - 3 * 3600
    pay.delivery_at = pay.written_at! - 20 * 60
    installFixtureRoutes({ [`GET ${API}/team/status`]: () => st })
    render(<RunnerStatusPage {...props()} />)
    const strip = await screen.findByTestId('relay-strip')
    expect(within(strip).queryByTestId('relay-stale')).toBeNull()
    const old = within(strip).getByTestId('project-stale')
    expect(old.closest('li')).toHaveAttribute('data-project', 'shop/shop-order')
    expect(old).toHaveTextContent('这个项目的状态是 3 小时前写的：团队服务器可能没在写它，看它的「运行机」页')
    const lag = within(strip).getByTestId('delivery-lag')
    expect(lag.closest('li')).toHaveAttribute('data-project', 'shop/shop-pay')
    expect(lag).toHaveTextContent('全队交付是 23 分钟前读完的')
  })
})
