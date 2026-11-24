import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'
import { fx } from '../test/data'
import { API, installFixtureRoutes } from '../test/routes'
import { apiCalls } from '../test/stubs/app-sdk'
import type { Settings, TeamSetup } from '../types'
import type { ViewProps } from './props'
import { BOT_FIRST, BOT_HINT, BRIDGE_FIRST, BRIDGE_HINT, ISSUES_HINT, JENKINS_HINT, keptSaid, MODE_HINT, OPTIONAL_TOKEN_HINT,
  PATROL_HINT, ROLES_HINT_NO_TOKEN, RunnerSetupCard, TOKEN_FIRST, TOKEN_HINT } from './RunnerSetup'
import { SETUP_POINTER, SettingsView } from './Settings'

/** The setup before any run: every step todo, nothing typed yet. */
function never(): TeamSetup {
  const s = fx.teamSetup()
  s.started_at = s.ended_at = null
  s.input = { gitlab_base: '', mode: 'project-token', projects: [], roles: { product: [], builder: [], security: [], data: [] }, push: 'auto', patrol: [],
    issues: [], jenkins: { base: '', user: '' } }
  s.steps = s.steps.map((x) => ({ ...x, status: 'todo', message: '', link: null, detail: '' }))
  return s
}

/** The run in progress: the first steps done, the fourth one going. */
function going(): TeamSetup {
  const s = fx.teamSetup()
  s.running = true
  s.ended_at = null
  s.steps = s.steps.map((x, i) => (i < 3 ? x : { ...x, status: i === 3 ? 'running' : 'todo', message: '', link: null, detail: '' }))
  return s
}

const TOKEN = 'fake-' + Math.random().toString(16).slice(2)   // made at run time: no token-looking text in the source
const JENKINS_TOKEN = 'fake-' + Math.random().toString(16).slice(2)

describe('一键建运行机', () => {
  test('GitLab, the project (from a repo on GitLab), a one-time token and the roles; 开始建 posts them once, then the steps are read until the run ends', async () => {
    const user = userEvent.setup()
    const reads = [going(), fx.teamSetup()]
    installFixtureRoutes({
      [`GET ${API}/team/setup`]: () => (apiCalls.some((c) => c.method === 'POST') ? reads.shift() ?? fx.teamSetup() : never()),
      [`POST ${API}/team/setup`]: () => going(),
    })
    const done = vi.fn()
    render(<RunnerSetupCard repos={fx.status().repos} onDone={done} pollMs={5} />)
    const card = await screen.findByRole('region', { name: '一键建运行机' })
    expect(await within(card).findByLabelText('GitLab 地址')).toHaveValue('https://gitlab.example.com')
    expect(within(card).getByLabelText('项目')).toHaveValue('shop/shop-pay')
    const token = within(card).getByLabelText('一次性 token')
    expect(token).toHaveAttribute('type', 'password')
    expect(card).toHaveTextContent(TOKEN_HINT)
    expect(within(card).queryByRole('list', { name: '建运行机的每一步' })).toBeNull()   // nothing run yet
    await user.type(token, TOKEN)
    await user.type(within(card).getByLabelText(/^产品/), 'carol')
    await user.type(within(card).getByLabelText(/^开发/), 'dave， erin')
    await user.click(within(card).getByRole('button', { name: '开始建' }))
    const steps = await within(card).findByRole('list', { name: '建运行机的每一步' })
    await waitFor(() => expect(within(card).getByTestId('setup-state')).toHaveTextContent('等人'))
    const posts = apiCalls.filter((c) => c.method === 'POST')
    expect(posts).toHaveLength(1)
    expect(posts[0].body).toEqual({ gitlab_base: 'https://gitlab.example.com', project: 'shop/shop-pay', admin_token: TOKEN,
      roles: { product: ['carol'], builder: ['dave', 'erin'] }, patrol: ['shop/shop-pay'], issues: [] })
    expect(apiCalls.filter((c) => c.method === 'GET').every((c) => !c.path.includes(TOKEN) && c.body === undefined)).toBe(true)
    expect(done).toHaveBeenCalledTimes(1)
    // each step: its title, ✓ / 等人 / ✕, its words; the MR to merge with its link
    const mr = steps.querySelector('[data-step="kit_mr"]') as HTMLElement
    expect(mr).toHaveAttribute('data-status', 'waiting')
    expect(mr).toHaveTextContent('kit.json 的 MR等人开了 MR !48：请在 GitLab 上合并这个 MR')
    expect(within(mr).getByRole('link', { name: /去 GitLab 合并/ })).toHaveAttribute('href', 'https://gitlab.example.com/shop/shop-pay/-/merge_requests/48')
    const signer = steps.querySelector('[data-step="signer"]') as HTMLElement
    expect(within(signer).getByRole('img', { name: '好了' })).toHaveTextContent('✓')
    expect(steps.querySelector('[data-step="team_mode"]')).toHaveTextContent('交接都会被拒')
    expect(within(card).getByRole('button', { name: '再跑一次' })).toBeEnabled()
  })

  test('the runner’s tokens: whose each is, its account and the day it ends; one ending within two weeks warns and says what to do', async () => {
    const bots = fx.teamSetupBot()
    installFixtureRoutes({ [`GET ${API}/team/setup`]: () => bots })
    const { unmount } = render(<RunnerSetupCard repos={fx.status().repos} pollMs={60_000} />)
    const list = await screen.findByRole('list', { name: '运行机的令牌' })
    const bridge = list.querySelector('[data-token="bridge"]') as HTMLElement
    expect(bridge).toHaveClass('br-prob', 'is-WARN')
    expect(within(bridge).getByRole('img', { name: '快到期了' })).toHaveTextContent('!')
    expect(bridge).toHaveTextContent('桥的令牌：个人令牌，账号 brain-bridge2026-11-18 到期，还有 10 天')
    expect(bridge).toHaveTextContent('做什么在 GitLab 上给 brain-bridge 换一个新的个人令牌，再跑一次填上')
    const bot = list.querySelector('[data-token="bot"]') as HTMLElement
    expect(bot).toHaveClass('is-OK')
    expect(bot).toHaveTextContent('推送的令牌：个人令牌，账号 brain-bot2027-06-30 到期，还有 234 天')
    expect(bot).not.toHaveTextContent('做什么')
    unmount()
    const one = fx.teamSetup()
    one.tokens = [{ ...one.tokens[0], expires_at: '2026-11-01', days_left: -7, soon: true },
      { key: 'bot', kind: 'project', name: 'brain-bot', user: 'project_12_bot_7d1e', expires_at: null, days_left: null, soon: false }]
    installFixtureRoutes({ [`GET ${API}/team/setup`]: () => one })
    render(<RunnerSetupCard repos={fx.status().repos} pollMs={60_000} />)
    const again = await screen.findByRole('list', { name: '运行机的令牌' })
    expect(again.querySelector('[data-token="bridge"]')).toHaveTextContent(
      '桥的令牌：项目令牌 brain-bridge，账号 project_12_bot_5f0c2a9e2026-11-01 已经过期了做什么再跑一次：它给这个项目令牌换上新的值，账号不变')
    expect(again.querySelector('[data-token="bot"]')).toHaveTextContent('推送的令牌：项目令牌 brain-bot，账号 project_12_bot_7d1eGitLab 没说它哪天到期')
  })

  test('巡检所有开着的 MR is ticked unless the last run left it off, says what it does, and goes in the post', async () => {
    const user = userEvent.setup()
    const off = never()
    off.input.projects = ['shop/shop-pay']
    off.input.patrol = []
    installFixtureRoutes({ [`GET ${API}/team/setup`]: () => off, [`POST ${API}/team/setup`]: () => going() })
    const { unmount } = render(<RunnerSetupCard repos={fx.status().repos} pollMs={60_000} />)
    const card = await screen.findByRole('region', { name: '一键建运行机' })
    const patrol = await within(card).findByRole('checkbox', { name: '巡检所有开着的 MR' })
    expect(patrol).not.toBeChecked()
    expect(patrol).toHaveAccessibleDescription(PATROL_HINT)
    unmount()
    installFixtureRoutes({ [`GET ${API}/team/setup`]: () => never(), [`POST ${API}/team/setup`]: () => going() })
    render(<RunnerSetupCard repos={fx.status().repos} pollMs={60_000} />)
    const again = await screen.findByRole('region', { name: '一键建运行机' })
    const box = await within(again).findByRole('checkbox', { name: '巡检所有开着的 MR' })
    expect(box).toBeChecked()
    await user.click(box)
    await user.type(within(again).getByLabelText('一次性 token'), TOKEN)
    await user.click(within(again).getByRole('button', { name: '开始建' }))
    await waitFor(() => expect(apiCalls.filter((c) => c.method === 'POST')).toHaveLength(1))
    expect(apiCalls.find((c) => c.method === 'POST')?.body).toMatchObject({ patrol: [] })
  })

  test('Jenkins: the last run’s address and read-only user are in the form again, its token is not; typed, all three go in the post', async () => {
    const user = userEvent.setup()
    const last = never()
    last.input.jenkins = { base: 'http://127.0.0.1:8080', user: 'brain-reader' }
    installFixtureRoutes({ [`GET ${API}/team/setup`]: () => last, [`POST ${API}/team/setup`]: () => going() })
    render(<RunnerSetupCard repos={fx.status().repos} pollMs={60_000} />)
    const card = await screen.findByRole('region', { name: '一键建运行机' })
    const base = await within(card).findByLabelText('Jenkins 地址')
    expect(base).toHaveValue('http://127.0.0.1:8080')
    expect(base).toHaveAccessibleDescription(JENKINS_HINT)
    expect(within(card).getByLabelText('Jenkins 只读用户')).toHaveValue('brain-reader')
    const secret = within(card).getByLabelText('Jenkins token')
    expect(secret).toHaveAttribute('type', 'password')
    expect(secret).toHaveValue('')
    await user.clear(base)
    await user.type(base, 'https://jenkins.example.com')
    await user.type(secret, JENKINS_TOKEN)
    await user.type(within(card).getByLabelText('一次性 token'), TOKEN)
    await user.click(within(card).getByRole('button', { name: '开始建' }))
    await waitFor(() => expect(apiCalls.filter((c) => c.method === 'POST')).toHaveLength(1))
    expect(apiCalls.find((c) => c.method === 'POST')?.body).toMatchObject({
      jenkins: { base: 'https://jenkins.example.com', user: 'brain-reader', token: JENKINS_TOKEN } })
  })

  test('从打了标签的 issue 接活 is off unless the last run left it on, says what it does, and goes in the post', async () => {
    const user = userEvent.setup()
    const on = never()
    on.input.projects = on.input.issues = ['shop/shop-pay']
    installFixtureRoutes({ [`GET ${API}/team/setup`]: () => on, [`POST ${API}/team/setup`]: () => going() })
    const { unmount } = render(<RunnerSetupCard repos={fx.status().repos} pollMs={60_000} />)
    const card = await screen.findByRole('region', { name: '一键建运行机' })
    const issues = await within(card).findByRole('checkbox', { name: '从打了标签的 issue 接活' })
    expect(issues).toBeChecked()
    expect(issues).toHaveAccessibleDescription(ISSUES_HINT)
    unmount()
    installFixtureRoutes({ [`GET ${API}/team/setup`]: () => never(), [`POST ${API}/team/setup`]: () => going() })
    render(<RunnerSetupCard repos={fx.status().repos} pollMs={60_000} />)
    const again = await screen.findByRole('region', { name: '一键建运行机' })
    const box = await within(again).findByRole('checkbox', { name: '从打了标签的 issue 接活' })
    expect(box).not.toBeChecked()
    await user.click(box)
    await user.click(within(again).getByRole('checkbox', { name: '巡检所有开着的 MR' }))
    await user.type(within(again).getByLabelText('一次性 token'), TOKEN)
    await user.click(within(again).getByRole('button', { name: '开始建' }))
    await waitFor(() => expect(apiCalls.filter((c) => c.method === 'POST')).toHaveLength(1))
    expect(apiCalls.find((c) => c.method === 'POST')?.body).toMatchObject({ patrol: [], issues: ['shop/shop-pay'] })
  })

  test('while a run goes on, its step in progress is said and the form waits', async () => {
    installFixtureRoutes({ [`GET ${API}/team/setup`]: () => going() })
    render(<RunnerSetupCard repos={[]} pollMs={60_000} />)
    const card = await screen.findByRole('region', { name: '一键建运行机' })
    const labels = await within(card).findByText('MR 标签')
    expect(labels.closest('li')).toHaveAttribute('data-status', 'running')
    expect(within(labels.closest('li') as HTMLElement).getByRole('img', { name: '正在做' })).toBeInTheDocument()
    expect(within(card).getByTestId('setup-state')).toHaveTextContent('正在建')
    expect(within(card).getByLabelText('一次性 token')).toBeDisabled()
    expect(within(card).getByRole('button', { name: '正在建…' })).toBeDisabled()
  })

  test('a failed step says 没成 with its words and GitLab’s own under 细节; the steps after it were not reached', async () => {
    const s = fx.teamSetup()
    s.steps = s.steps.map((x, i) => (i < 4 ? x : i === 4
      ? { ...x, status: 'failed', message: '这些人不是 shop/shop-pay 的成员：erin。先在 GitLab 上把他们加进项目，或者改填别人', detail: 'GET /projects/12/members/all -> 200' }
      : { ...x, status: 'todo', message: '', link: null, detail: '' }))
    installFixtureRoutes({ [`GET ${API}/team/setup`]: () => s })
    render(<RunnerSetupCard repos={[]} />)
    const card = await screen.findByRole('region', { name: '一键建运行机' })
    const row = (await within(card).findByText('身份表')).closest('li') as HTMLElement
    expect(row).toHaveTextContent('没成')
    expect(within(row).getByRole('img', { name: '没成' })).toHaveTextContent('✕')
    expect(row).toHaveTextContent('这些人不是 shop/shop-pay 的成员：erin')
    expect(within(row).getByText('细节')).toBeInTheDocument()
    expect(card.querySelector('[data-step="kit_mr"]')).toHaveAttribute('data-status', 'todo')
    expect(within(card).getByTestId('setup-state')).toHaveTextContent('没建完')
    // the last run's input is in the form again; the token is not
    expect(within(card).getByLabelText(/^产品/)).toHaveValue('carol')
    expect(within(card).getByLabelText('一次性 token')).toHaveValue('')
  })

  test.each([
    ['kit_mr', 'kit.json 的 MR', 5],
    ['project', '找到项目', 0],
  ])('a run cut off at %s (the app stopped) says where it stopped, not 建好了, and offers 再跑一次', async (id, title, done) => {
    const s = fx.teamSetup()
    s.ended_at = null
    s.cut_off = id
    s.steps = s.steps.map((x, i) => (i < done ? x : { ...x, status: 'todo', message: '', link: null, detail: '' }))
    installFixtureRoutes({ [`GET ${API}/team/setup`]: () => s })
    render(<RunnerSetupCard repos={[]} />)
    const card = await screen.findByRole('region', { name: '一键建运行机' })
    expect(await within(card).findByTestId('setup-cut-off')).toHaveTextContent(`上次跑到「${title}」就停了，没跑完`)
    expect(within(card).getByTestId('setup-state')).toHaveTextContent('没建完')
    expect(within(card).getByRole('list', { name: '建运行机的每一步' })).toBeInTheDocument()
    expect(within(card).getByRole('button', { name: '再跑一次' })).toBeEnabled()
  })

  test('再跑一次 without the token asks for it and posts nothing', async () => {
    const user = userEvent.setup()
    installFixtureRoutes()
    render(<RunnerSetupCard repos={[]} />)
    const card = await screen.findByRole('region', { name: '一键建运行机' })
    await user.click(await within(card).findByRole('button', { name: '再跑一次' }))
    expect(within(card).getByRole('alert')).toHaveTextContent(TOKEN_FIRST)
    expect(within(card).getByLabelText('一次性 token')).toHaveFocus()
    expect(apiCalls.some((c) => c.method === 'POST')).toBe(false)
  })

  test('账号 机器人账号 turns 项目 into a list, one per line (the repos on GitLab to start with), asks for the bridge’s and the bot’s personal tokens before it posts, the one-time token optional; 项目令牌 gives the one project back', async () => {
    const user = userEvent.setup()
    installFixtureRoutes({ [`GET ${API}/team/setup`]: () => never(), [`POST ${API}/team/setup`]: () => going() })
    render(<RunnerSetupCard repos={fx.status().repos} pollMs={60_000} />)
    const card = await screen.findByRole('region', { name: '一键建运行机' })
    const accounts = await within(card).findByRole('radiogroup', { name: '账号' })
    expect(within(accounts).getByRole('radio', { name: '项目令牌：一个项目' })).toBeChecked()
    expect(accounts).toHaveAccessibleDescription(MODE_HINT['project-token'])
    await user.click(within(accounts).getByRole('radio', { name: '机器人账号：几个项目' }))
    expect(accounts).toHaveAccessibleDescription(MODE_HINT['bot-account'])
    expect(within(card).queryByLabelText('项目')).toBeNull()
    const list = within(card).getByLabelText('项目（一行一个）')
    expect(list.tagName).toBe('TEXTAREA')
    expect(list).toHaveValue('shop/shop-pay')
    await user.type(list, '\nshop/shop-order\n\nshop/shop-stock')
    expect(within(card).getByLabelText('一次性 token（可以不填）')).toHaveAccessibleDescription(OPTIONAL_TOKEN_HINT)
    expect(within(card).getByText(ROLES_HINT_NO_TOKEN)).toBeInTheDocument()
    await user.type(within(card).getByLabelText(/^产品/), 'carol')
    await user.type(within(card).getByLabelText(/^开发/), 'dave')
    const start = within(card).getByRole('button', { name: '开始建' })
    await user.click(start)
    expect(within(card).getByRole('alert')).toHaveTextContent(BRIDGE_FIRST)
    const bridge = within(card).getByLabelText('桥的账号的个人令牌')
    expect(bridge).toHaveFocus()
    expect(bridge).toHaveAttribute('type', 'password')
    await user.type(bridge, TOKEN + '-bridge')
    const push = within(card).getByRole('radiogroup', { name: '运行机怎么推送' })
    expect(within(push).getByRole('radio', { name: '这台机器的 SSH' })).toBeChecked()
    expect(within(card).queryByLabelText('机器人账号的个人令牌')).toBeNull()
    await user.click(within(push).getByRole('radio', { name: '机器人账号' }))
    await user.click(start)
    expect(within(card).getByRole('alert')).toHaveTextContent(BOT_FIRST)
    expect(within(card).getByLabelText('机器人账号的个人令牌')).toHaveFocus()
    await user.type(within(card).getByLabelText('机器人账号的个人令牌'), TOKEN + '-bot')
    expect(apiCalls.some((c) => c.method === 'POST')).toBe(false)
    await user.click(start)
    await waitFor(() => expect(apiCalls.filter((c) => c.method === 'POST')).toHaveLength(1))
    expect(apiCalls.find((c) => c.method === 'POST')?.body).toEqual({
      mode: 'bot-account', gitlab_base: 'https://gitlab.example.com', projects: ['shop/shop-pay', 'shop/shop-order', 'shop/shop-stock'],
      roles: { product: ['carol'], builder: ['dave'] }, patrol: ['shop/shop-pay', 'shop/shop-order', 'shop/shop-stock'], issues: [], push: 'token', bridge_token: TOKEN + '-bridge', bot_token: TOKEN + '-bot' })
  })

  test('项目令牌 again after 机器人账号: the one project field is back, the post is the one-project one', async () => {
    const user = userEvent.setup()
    installFixtureRoutes({ [`GET ${API}/team/setup`]: () => never(), [`POST ${API}/team/setup`]: () => going() })
    render(<RunnerSetupCard repos={fx.status().repos} pollMs={60_000} />)
    const card = await screen.findByRole('region', { name: '一键建运行机' })
    await user.click(await within(card).findByRole('radio', { name: '机器人账号：几个项目' }))
    await user.click(within(card).getByRole('radio', { name: '项目令牌：一个项目' }))
    expect(within(card).queryByLabelText('项目（一行一个）')).toBeNull()
    expect(within(card).queryByLabelText('桥的账号的个人令牌')).toBeNull()
    expect(within(card).getByLabelText('项目')).toHaveValue('shop/shop-pay')
    await user.type(within(card).getByLabelText('一次性 token'), TOKEN)
    await user.click(within(card).getByRole('button', { name: '开始建' }))
    await waitFor(() => expect(apiCalls.filter((c) => c.method === 'POST')).toHaveLength(1))
    expect(apiCalls.find((c) => c.method === 'POST')?.body).toEqual({ gitlab_base: 'https://gitlab.example.com', project: 'shop/shop-pay',
      admin_token: TOKEN, roles: { product: [], builder: [] }, patrol: ['shop/shop-pay'], issues: [] })
  })

  test('a step about several projects lists each one under it with its own mark, words and link; 再跑一次 leaves out the tokens this machine kept', async () => {
    const user = userEvent.setup()
    installFixtureRoutes({ [`GET ${API}/team/setup`]: () => fx.teamSetupBot(), [`POST ${API}/team/setup`]: () => going() })
    render(<RunnerSetupCard repos={[]} pollMs={60_000} />)
    const card = await screen.findByRole('region', { name: '一键建运行机' })
    const mr = (await within(card).findByRole('list', { name: '建运行机的每一步' })).querySelector('[data-step="kit_mr"]') as HTMLElement
    expect(mr).toHaveAttribute('data-status', 'waiting')
    expect(mr).toHaveTextContent('kit.json 的 MR等人2 个项目的 kit.json MR 等你在 GitLab 上合并')
    const each = within(mr).getByRole('list', { name: 'kit.json 的 MR：每个项目' })
    const [pay, order, stock] = within(each).getAllByRole('listitem')
    expect(pay).toHaveTextContent('shop/shop-pay开了 MR !48：请在 GitLab 上合并这个 MR')
    expect(within(pay).getByRole('img', { name: '等人' })).toHaveTextContent('!')
    expect(within(pay).getByRole('link', { name: /去 GitLab 合并/ })).toHaveAttribute('href', 'https://gitlab.example.com/shop/shop-pay/-/merge_requests/48')
    expect(within(order).getByRole('link', { name: /去 GitLab 合并/ })).toHaveAttribute('href', 'https://gitlab.example.com/shop/shop-order/-/merge_requests/31')
    expect(within(stock).getByRole('img', { name: '好了' })).toHaveTextContent('✓')
    expect(within(stock).queryByRole('link')).toBeNull()
    const found = card.querySelector('[data-step="project"]') as HTMLElement
    expect(within(found).getAllByRole('link', { name: /在 GitLab 上看/ }).map((a) => a.getAttribute('href'))).toEqual(
      ['https://gitlab.example.com/shop/shop-pay', 'https://gitlab.example.com/shop/shop-order', 'https://gitlab.example.com/shop/shop-stock'])
    expect(within(card.querySelector('[data-step="bridge_token"]') as HTMLElement).queryByRole('list')).toBeNull()
    // the last run's input is in the form again, and the tokens it kept are said to be used when left empty
    expect(within(card).getByRole('radio', { name: '机器人账号：几个项目' })).toBeChecked()
    expect(within(card).getByLabelText('项目（一行一个）')).toHaveValue('shop/shop-pay\nshop/shop-order\nshop/shop-stock')
    expect(within(card).getByRole('radio', { name: '机器人账号' })).toBeChecked()
    expect(within(card).getByLabelText('桥的账号的个人令牌')).toHaveAccessibleDescription(expect.stringContaining(BRIDGE_HINT))
    expect(within(card).getByLabelText('机器人账号的个人令牌')).toHaveAccessibleDescription(expect.stringContaining(BOT_HINT))
    expect(within(card).getByLabelText('桥的账号的个人令牌')).toHaveAccessibleDescription(expect.stringContaining(keptSaid('brain-bridge')))
    expect(within(card).getByLabelText('机器人账号的个人令牌')).toHaveAccessibleDescription(expect.stringContaining(keptSaid('brain-bot')))
    await user.click(within(card).getByRole('button', { name: '再跑一次' }))
    await waitFor(() => expect(apiCalls.filter((c) => c.method === 'POST')).toHaveLength(1))
    expect(apiCalls.find((c) => c.method === 'POST')?.body).toEqual({
      mode: 'bot-account', gitlab_base: 'https://gitlab.example.com', projects: ['shop/shop-pay', 'shop/shop-order', 'shop/shop-stock'],
      roles: { product: ['carol'], builder: ['dave'] }, patrol: ['shop/shop-pay', 'shop/shop-order', 'shop/shop-stock'], issues: [], push: 'token' })
  })

  test('机器人账号 with Jenkins: one Jenkins for the runner, its address and user posted again on 再跑一次 with the token left empty (the one kept)', async () => {
    const user = userEvent.setup()
    const last = fx.teamSetupBot()
    last.input.jenkins = { base: 'https://jenkins.example.com', user: 'brain-reader' }
    installFixtureRoutes({ [`GET ${API}/team/setup`]: () => last, [`POST ${API}/team/setup`]: () => going() })
    render(<RunnerSetupCard repos={[]} pollMs={60_000} />)
    const card = await screen.findByRole('region', { name: '一键建运行机' })
    expect(await within(card).findByLabelText('Jenkins 地址')).toHaveValue('https://jenkins.example.com')
    expect(within(card).getAllByLabelText('Jenkins token')).toHaveLength(1)
    await user.click(within(card).getByRole('button', { name: '再跑一次' }))
    await waitFor(() => expect(apiCalls.filter((c) => c.method === 'POST')).toHaveLength(1))
    expect(apiCalls.find((c) => c.method === 'POST')?.body).toEqual({
      mode: 'bot-account', gitlab_base: 'https://gitlab.example.com', projects: ['shop/shop-pay', 'shop/shop-order', 'shop/shop-stock'],
      roles: { product: ['carol'], builder: ['dave'] }, patrol: ['shop/shop-pay', 'shop/shop-order', 'shop/shop-stock'], issues: [], push: 'token',
      jenkins: { base: 'https://jenkins.example.com', user: 'brain-reader', token: '' } })
  })

  test('机器人账号 with several projects: 巡检 and 从 issue 接活 are ticked project by project, as the last run left them, and each posts the projects ticked', async () => {
    const user = userEvent.setup()
    const last = fx.teamSetupBot()
    last.input.patrol = ['shop/shop-pay', 'shop/shop-order', 'shop/shop-stock']
    last.input.issues = ['shop/shop-pay']
    installFixtureRoutes({ [`GET ${API}/team/setup`]: () => last, [`POST ${API}/team/setup`]: () => going() })
    render(<RunnerSetupCard repos={[]} pollMs={60_000} />)
    const card = await screen.findByRole('region', { name: '一键建运行机' })
    const issues = await within(card).findByRole('group', { name: '从打了标签的 issue 接活' })
    expect(issues).toHaveAccessibleDescription(ISSUES_HINT)
    expect(within(issues).getAllByRole('checkbox')).toHaveLength(3)
    expect(within(issues).getByRole('checkbox', { name: 'shop/shop-pay' })).toBeChecked()
    expect(within(issues).getByRole('checkbox', { name: 'shop/shop-order' })).not.toBeChecked()
    expect(within(issues).getByRole('checkbox', { name: 'shop/shop-stock' })).not.toBeChecked()
    const patrol = within(card).getByRole('group', { name: '巡检所有开着的 MR' })
    expect(patrol).toHaveAccessibleDescription(PATROL_HINT)
    for (const box of within(patrol).getAllByRole('checkbox')) expect(box).toBeChecked()
    await user.click(within(issues).getByRole('checkbox', { name: 'shop/shop-order' }))
    await user.click(within(patrol).getByRole('checkbox', { name: 'shop/shop-stock' }))
    await user.click(within(card).getByRole('button', { name: '再跑一次' }))
    await waitFor(() => expect(apiCalls.filter((c) => c.method === 'POST')).toHaveLength(1))
    expect(apiCalls.find((c) => c.method === 'POST')?.body).toMatchObject({
      projects: ['shop/shop-pay', 'shop/shop-order', 'shop/shop-stock'],
      patrol: ['shop/shop-pay', 'shop/shop-order'], issues: ['shop/shop-pay', 'shop/shop-order'] })
  })

  test('on 设置, below 团队模式: the pointer to it, and after a run the form shows the team settings it saved', async () => {
    const user = userEvent.setup()
    const on: Settings = { ...fx.settings(), team: { enabled: true, bridge_config: '/Users/alice/.brain/team-setup/bridge.json',
      runnerd_config: '/Users/alice/.brain/team-setup/runnerd.json', dev_same_user: true } }
    installFixtureRoutes({
      [`GET ${API}/settings`]: () => (apiCalls.some((c) => c.method === 'POST') ? on : fx.settings()),
      [`POST ${API}/team/setup`]: () => fx.teamSetup(),     // a run that ended before the answer
    })
    const status = fx.status()
    status.app.role = 'team_server'
    const p: ViewProps = { status, go: vi.fn(), replace: vi.fn(), reloadStatus: vi.fn(), setStatus: vi.fn(), pollMs: 60_000 }
    render(<SettingsView {...p} />)
    expect(await screen.findByTestId('setup-pointer')).toHaveTextContent(SETUP_POINTER)
    const card = screen.getByRole('region', { name: '一键建运行机' })
    await user.type(await within(card).findByLabelText('一次性 token'), TOKEN)
    await user.click(within(card).getByRole('button', { name: '再跑一次' }))
    await waitFor(() => expect(screen.getByRole('checkbox', { name: '这台机器是团队运行机' })).toBeChecked())
    expect(screen.getByLabelText('桥的配置文件')).toHaveValue('/Users/alice/.brain/team-setup/bridge.json')
    expect(screen.queryByTestId('setup-pointer')).toBeNull()
    expect(p.reloadStatus).toHaveBeenCalled()
  })
})
