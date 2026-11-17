import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'
import { fx } from '../test/data'
import { API, installFixtureRoutes } from '../test/routes'
import { apiCalls } from '../test/stubs/app-sdk'
import type { Settings, TeamSetup } from '../types'
import type { ViewProps } from './props'
import { RunnerSetupCard, TOKEN_FIRST, TOKEN_HINT } from './RunnerSetup'
import { SETUP_POINTER, SettingsView } from './Settings'

/** The setup before any run: every step todo, nothing typed yet. */
function never(): TeamSetup {
  const s = fx.teamSetup()
  s.started_at = s.ended_at = null
  s.input = { gitlab_base: '', project: '', roles: { product: [], builder: [], security: [], data: [] }, push: 'auto' }
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
    expect(within(card).getByLabelText('GitLab 地址')).toHaveValue('https://gitlab.example.com')
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
      roles: { product: ['carol'], builder: ['dave', 'erin'] } })
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

  test('on 设置, below 团队模式: the pointer to it, and after a run the form shows the team settings it saved', async () => {
    const user = userEvent.setup()
    const on: Settings = { ...fx.settings(), team: { enabled: true, bridge_config: '/Users/alice/.brain/team-setup/bridge.json',
      runnerd_config: '/Users/alice/.brain/team-setup/runnerd.json', dev_same_user: true } }
    installFixtureRoutes({
      [`GET ${API}/settings`]: () => (apiCalls.some((c) => c.method === 'POST') ? on : fx.settings()),
      [`POST ${API}/team/setup`]: () => fx.teamSetup(),     // a run that ended before the answer
    })
    const p: ViewProps = { status: fx.status(), go: vi.fn(), replace: vi.fn(), reloadStatus: vi.fn(), setStatus: vi.fn(), pollMs: 60_000 }
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
