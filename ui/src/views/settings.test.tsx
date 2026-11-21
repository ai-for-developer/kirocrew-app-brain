import { useState } from 'react'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'
import { fx } from '../test/data'
import { API, installFixtureRoutes } from '../test/routes'
import { apiCalls, apiError } from '../test/stubs/app-sdk'
import type { FastModeResult, FastModeView, RepoInfo, RepoMe, Settings, Status } from '../types'
import type { ViewProps } from './props'
import { FAST_RULE, FAST_SWITCH_HINT, FETCH_HINT, PERSONAL_RUNNER, PUSH_HINT, ROLE_HINT, ROLE_SWITCH, SettingsView, TO_PERSONAL_ON, TO_SERVER } from './Settings'

const props = (over: Partial<ViewProps> = {}): ViewProps => ({
  status: fx.status(), go: vi.fn(), replace: vi.fn(), reloadStatus: vi.fn(), setStatus: vi.fn(), pollMs: 60_000, ...over,
})
/** The team server (the fixture's machine is a personal one): 设置 has 团队模式 and 一键建运行机 there. */
const server = (): Status => {
  const s = fx.status()
  s.app.role = 'team_server'
  return s
}
/** 设置 as main.tsx holds it: the status a save answers with is the page's from then on. */
function Live({ start, go = vi.fn() }: { start: Status; go?: ViewProps['go'] }) {
  const [status, setStatus] = useState(start)
  return <SettingsView {...props({ status, setStatus, go })} />
}

describe('设置', () => {
  test('save posts the edited settings without the repos (the list is 导入仓库’s and 移出列表’s) and the role (its own switch), puts the answered status in place', async () => {
    const user = userEvent.setup()
    const answered = fx.status()
    installFixtureRoutes({
      [`POST ${API}/settings`]: (body) => ({ ok: true, settings: { ...fx.settings(), ...(body as Partial<Settings>) }, status: answered }),
    })
    const p = props({ status: server() })
    render(<SettingsView {...p} />)
    await screen.findByLabelText('brain-kit 在哪')
    expect(screen.queryByLabelText('项目仓库（一行一个）')).toBeNull()
    await user.click(screen.getByRole('checkbox', { name: '这台机器是团队运行机' }))
    await user.click(screen.getByRole('button', { name: '保存' }))
    expect(await screen.findByText('已保存')).toBeInTheDocument()
    const post = apiCalls.find((c) => c.method === 'POST')
    expect(post?.path).toBe(`${API}/settings`)
    const { repos: _repos, role: _role, ...rest } = fx.settings()
    expect(post?.body).toEqual({ ...rest, team: { ...fx.settings().team, enabled: true } })
    expect(p.setStatus).toHaveBeenCalledWith(answered)
  })

  test('仓库: 导入仓库, and 移出列表 after a confirm that says it changes no file', async () => {
    const user = userEvent.setup()
    const after = fx.status()
    after.repos = after.repos.slice(1).map((r) => ({ ...r, id: 'r1' }))
    installFixtureRoutes({ [`POST ${API}/repos/remove`]: () => ({ ok: true, text: '从列表里移出了 shop-pay（文件、分支都没动）', status: after }) })
    const p = props()
    render(<SettingsView {...p} />)
    const card = screen.getByRole('region', { name: '仓库' })
    await user.click(within(card).getByRole('button', { name: '导入仓库' }))
    expect(p.go).toHaveBeenCalledWith({ kind: 'import' })
    const r1 = card.querySelector('[data-repo="r1"]') as HTMLElement
    await user.click(within(r1).getByRole('button', { name: '移出列表' }))
    const ask = within(r1).getByRole('group', { name: '移出 shop-pay' })
    expect(ask).toHaveTextContent('只从 app 的列表里去掉 shop-pay：不删文件，不动分支和工作目录。')
    expect(apiCalls.some((c) => c.path === `${API}/repos/remove`)).toBe(false)
    await user.click(within(ask).getByRole('button', { name: '移出' }))
    await waitFor(() => expect(p.setStatus).toHaveBeenCalledWith(after))
    expect(apiCalls.find((c) => c.path === `${API}/repos/remove`)?.body).toEqual({ path: '/Users/alice/workplace/shop-pay' })
  })

  test('仓库: a damaged repo_ids.json without a good copy is said in words; 按记录重建编号 rebuilds it and says what each repo got', async () => {
    const user = userEvent.setup()
    const s = fx.status()
    s.repos = []
    s.repo_ids_problem = '仓库编号的记录 /data/repo_ids.json 坏了，也没有好的备份：这次先不认列表里的仓库，免得记录记到别的仓库上。在设置里点「按记录重建编号」'
    const rebuilt = { ok: true, text: '按记录重建了编号：shop-pay 接上了原来的记录', status: fx.status() }
    installFixtureRoutes({ [`POST ${API}/repos/ids/rebuild`]: () => rebuilt })
    const p = props({ status: s })
    render(<SettingsView {...p} />)
    const card = screen.getByRole('region', { name: '仓库' })
    expect(within(card).getByRole('alert')).toHaveTextContent(s.repo_ids_problem)
    expect(card).not.toHaveTextContent('还没有仓库')
    await user.click(within(card).getByRole('button', { name: '按记录重建编号' }))
    await waitFor(() => expect(p.setStatus).toHaveBeenCalledWith(rebuilt.status))
    expect(apiCalls.filter((c) => c.method === 'POST')).toEqual([{ method: 'POST', path: `${API}/repos/ids/rebuild`, body: {} }])
  })

  test('仓库: no word about the ids while their file reads fine', () => {
    render(<SettingsView {...props()} />)
    expect(within(screen.getByRole('region', { name: '仓库' })).queryByText(/仓库编号的记录/)).toBeNull()
  })

  test('a team repo whose brain-kit is on the install branch with an open MR: the MR first; a local merge is 导入仓库’s', async () => {
    const user = userEvent.setup()
    const s = fx.status()
    s.repos[1] = { ...s.repos[1], error: '', pending: { ...fx.importInspectPending().pending!, pushed: true, word: 'MR 等合并',
      mr_url: 'https://gitlab.example.com/shop/shop-web/-/merge_requests/3', text: '装在 brain/install 分支上，MR 开着：合进 main 以后生效',
      next: '在 GitLab 上合并 MR（团队的做法），合并以后点「拉取」', merge: { ok: true, why: '', confirm: true } } }
    const p = props({ status: s })
    render(<SettingsView {...p} />)
    const r2 = screen.getByRole('region', { name: '仓库' }).querySelector('[data-repo="r2"]') as HTMLElement
    expect(r2).toHaveTextContent('MR 等合并')
    expect(r2).toHaveTextContent('装在 brain/install 分支上，MR 开着：合进 main 以后生效。在 GitLab 上合并 MR（团队的做法），合并以后点「拉取」')
    expect(within(r2).getByRole('link', { name: /在 GitLab 上看 MR/ })).toHaveAttribute('href', 'https://gitlab.example.com/shop/shop-web/-/merge_requests/3')
    expect(r2).not.toHaveTextContent('读不到')
    // its 推送 must stay on the page, and a row is redrawn every half minute
    expect(within(r2).queryByRole('button', { name: /合并到 main/ })).toBeNull()
    await user.click(within(r2).getByRole('button', { name: '去导入仓库' }))
    expect(p.go).toHaveBeenCalledWith({ kind: 'import', path: '/Users/alice/workplace/shop-web' })
  })

  test('重装 kit 文件 on a team repo: where it goes is asked (推荐 marked); in place, 推送 stays even when a reload drops the row', async () => {
    const user = userEvent.setup()
    const job = fx.importUpgradeJob()
    job.steps = [{ id: 'check', label: '看能不能直接装', state: 'ok', detail: '在 main 上，检出是干净的' }, ...job.steps.slice(1)]
    job.result = { ...job.result!, mode: 'inplace', branch: 'main', base: 'main', worktree: null, target: 'main',
      remote_url: 'git@gitlab.example.com:shop/shop-pay.git', text: '提交在 main 上了，现在就生效。远端还没有它：要推送就点「推送」' }
    installFixtureRoutes({
      [`POST ${API}/import/choices`]: () => fx.importChoices(),
      [`POST ${API}/import/upgrade`]: () => ({ job }),
    })
    const p = props()
    const { rerender } = render(<SettingsView {...p} />)
    const crew = screen.getByRole('region', { name: 'KiroCrew' })
    await user.click(within(crew).getByRole('button', { name: '重装 kit 文件' }))
    expect(apiCalls.find((c) => c.path === `${API}/import/choices`)?.body).toEqual({ path: '/Users/alice/workplace/shop-pay', purpose: 'upgrade' })
    const pick = await within(crew).findByTestId('mode-pick')
    const rec = pick.querySelector('[data-mode="branch"]') as HTMLElement
    expect(rec).toHaveTextContent('更新到新分支，开 MR 给团队看推荐')
    expect(pick.querySelector('[data-mode="inplace"]')).toHaveTextContent('更新在你现在的检出里，提交在 main 上；推送到远端以后生效（这会直接改团队的 main，没有 MR 评审）')
    await user.click(within(pick).getByRole('button', { name: '直接更新在 main' }))
    expect(apiCalls.find((c) => c.path === `${API}/import/upgrade`)?.body).toEqual({ path: '/Users/alice/workplace/shop-pay', mode: 'inplace' })
    expect(await within(crew).findByText(job.result!.text)).toBeInTheDocument()
    expect(p.reloadStatus).not.toHaveBeenCalled()                       // 推送 first: the page waits
    const fixed = fx.status()
    fixed.crew.rows = fixed.crew.rows.filter((r) => r.fix !== 'kit')     // the next status: the kit files are fine now
    rerender(<SettingsView {...p} status={fixed} />)
    const box = within(screen.getByRole('region', { name: 'KiroCrew' })).getByTestId('push-box')
    await user.click(within(box).getByRole('button', { name: '先不推送' }))
    expect(p.reloadStatus).toHaveBeenCalled()
    await waitFor(() => expect(screen.queryByTestId('push-box')).toBeNull())
    expect(screen.getByRole('region', { name: 'KiroCrew' })).not.toHaveTextContent('适配文件是旧版')
    expect(apiCalls.some((c) => c.path === `${API}/import/push`)).toBe(false)
  })

  test('重装 kit 文件 with one way only (no remote, nothing uncommitted): one click, then the page moves on', async () => {
    const user = userEvent.setup()
    const one = { ...fx.importChoices(), recommend: 'inplace' as const, remote_url: '',
      options: [{ mode: 'inplace' as const, title: '直接更新在 main', line: '更新在你现在的检出里，提交在 main 上，马上生效', ok: true, why: '' }] }
    const job = fx.importUpgradeJob()
    job.result = { ...job.result!, mode: 'inplace', branch: 'main', base: 'main', worktree: null, remote_url: '', text: '提交在 main 上了，现在就生效' }
    installFixtureRoutes({
      [`POST ${API}/import/choices`]: () => one,
      [`POST ${API}/import/upgrade`]: () => ({ job }),
    })
    const p = props()
    render(<SettingsView {...p} />)
    await user.click(within(screen.getByRole('region', { name: 'KiroCrew' })).getByRole('button', { name: '重装 kit 文件' }))
    await waitFor(() => expect(p.reloadStatus).toHaveBeenCalled())
    expect(screen.queryByTestId('mode-pick')).toBeNull()
    expect(apiCalls.find((c) => c.path === `${API}/import/upgrade`)?.body).toEqual({ path: '/Users/alice/workplace/shop-pay', mode: 'inplace' })
    expect(screen.queryByTestId('push-box')).toBeNull()
  })

  test('a repo without a remote, on its install branch: no waiting for a merge, 「合并到 main」 right on the row', async () => {
    const user = userEvent.setup()
    const s = fx.status()
    s.repos[1] = { ...s.repos[1], error: '', pending: { ...fx.importInspectPending().pending!, remote_url: '', gitlab_base: '', gitlab_project: '',
      can_open_mr: false, word: '还没合进 main', text: '装在本机分支 brain/install 上，还没合进 main', next: '点「合并到 main」就能用（快进，不多一个提交）',
      merge: { ok: true, why: '', confirm: false } } }
    const merged = { ...fx.importMerge(), remote_url: '', text: '合进 main 了（快进到 d6342283）：这个仓库现在就能用。' }
    installFixtureRoutes({ [`POST ${API}/import/merge`]: () => merged })
    render(<SettingsView {...props({ status: s })} />)
    const r2 = screen.getByRole('region', { name: '仓库' }).querySelector('[data-repo="r2"]') as HTMLElement
    expect(r2).toHaveTextContent('还没合进 main')
    expect(r2).not.toHaveTextContent('等合并')
    expect(within(r2).queryByRole('button', { name: '去导入仓库' })).toBeNull()
    await user.click(within(r2).getByRole('button', { name: '合并到 main' }))
    expect(await within(r2).findByText(merged.text)).toBeInTheDocument()
    expect(apiCalls.find((c) => c.path === `${API}/import/merge`)?.body).toEqual({ path: '/Users/alice/workplace/shop-web', confirm: false })
    expect(within(r2).queryByTestId('push-box')).toBeNull()
  })

  test('team mode off is one checkbox, no red; on, it asks for bridge.json and runnerd.json only (the signer inside bridge.json, folded)', async () => {
    const user = userEvent.setup()
    const s = server()
    s.team = { ...s.team, enabled: false, error: '运行机模式没打开' }
    installFixtureRoutes({})
    render(<SettingsView {...props({ status: s })} />)
    await screen.findByLabelText('brain-kit 在哪')
    const team = screen.getByText('团队模式').closest('fieldset') as HTMLElement
    expect(within(team).queryAllByRole('textbox')).toEqual([])
    expect(team).not.toHaveTextContent('bridge')
    expect(screen.getByRole('region', { name: '团队运行机' })).not.toHaveTextContent('运行机模式没打开')
    await user.click(within(team).getByRole('checkbox', { name: '这台机器是团队运行机' }))
    expect(within(team).getAllByRole('textbox').map((x) => x.id)).toEqual(['set-bridge_config', 'set-runnerd_config'])
    expect(within(team).getByLabelText('桥的配置文件')).toHaveAttribute('placeholder', '…/bridge.json')
    expect(within(team).getByLabelText('运行机的配置文件')).toHaveAttribute('placeholder', '…/runnerd.json')
    expect(within(team).getByText(/"signer": \{"type": "spool"/).closest('details')).not.toHaveAttribute('open')
    expect(team.textContent?.replace(within(team).getByText(/"signer"/).textContent ?? '', '')).not.toMatch(/bridge |runnerd /) // the code names only under 细节
    expect(team).toHaveTextContent('不写签名私钥')
  })

  test('the same-user trial shows its warning; a save that fails shows errorText', async () => {
    const user = userEvent.setup()
    installFixtureRoutes({ [`POST ${API}/settings`]: () => { throw apiError(400, 'bad_settings', 'work_root 必须在 ~/workplace 或 ~/workspace 下面') } })
    render(<SettingsView {...props({ status: server() })} />)
    await screen.findByLabelText('brain-kit 在哪')
    expect(screen.queryByTestId('same-user-warning')).toBeNull()
    await user.click(screen.getByRole('checkbox', { name: '这台机器是团队运行机' }))
    await user.click(screen.getByRole('checkbox', { name: '本机试用：签名器和 app 用同一个系统用户' }))
    expect(screen.getByTestId('same-user-warning')).toHaveTextContent('只适合本机试用')
    await user.click(screen.getByRole('button', { name: '保存' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('work_root 必须在 ~/workplace 或 ~/workspace 下面（400）')
  })

  test('把引擎换成 kas asks first: it changes KiroCrew for every project (UI-17)', async () => {
    const user = userEvent.setup()
    const engine = { level: 'WARN' as const, text: 'KiroCrew 引擎是 kiro，不是 kas', todo: '点「把引擎换成 kas」：Lean 的拦截只在 kas 上测过。KiroCrew 可能要重开才生效', fix: 'engine' as const, repo: null, raw: '' }
    installFixtureRoutes({
      [`POST ${API}/crew/fix`]: () => ({ ok: true, text: '引擎设成了 kas。KiroCrew 可能要重开才生效。', rows: [{ ...engine, level: 'OK', text: 'KiroCrew 引擎：kas', todo: '', fix: null }] }),
    })
    const status = fx.status()
    render(<SettingsView {...props({ status: { ...status, crew: { ...status.crew, rows: [engine], fix: { agent: false, engine: true } } } })} />)
    const crew = await screen.findByRole('region', { name: 'KiroCrew' })
    await user.click(await within(crew).findByRole('button', { name: '把引擎换成 kas' }))
    const ask = within(crew).getByTestId('engine-confirm')
    expect(ask).toHaveTextContent('这会改 KiroCrew 的全局设置：以后所有项目、所有会话都用 kas 引擎')
    expect(apiCalls.filter((c) => c.method === 'POST')).toEqual([])                      // nothing sent yet
    await user.click(within(ask).getByRole('button', { name: '先不换' }))
    expect(within(crew).queryByTestId('engine-confirm')).toBeNull()
    expect(apiCalls.filter((c) => c.method === 'POST')).toEqual([])
    await user.click(within(crew).getByRole('button', { name: '把引擎换成 kas' }))
    await user.click(within(crew).getByRole('button', { name: '换成 kas' }))
    expect(await within(crew).findByText('引擎设成了 kas。KiroCrew 可能要重开才生效。')).toBeInTheDocument()
    expect(apiCalls.filter((c) => c.method === 'POST')).toEqual([{ method: 'POST', path: `${API}/crew/fix`, body: { what: 'engine' } }])
  })

  test('the status: brain-kit and its modules, each repo, who I am (a token is only 有/没有), KiroCrew’s checks with a fix', async () => {
    const user = userEvent.setup()
    installFixtureRoutes({ [`POST ${API}/crew/fix`]: () => ({ ok: true, text: '给 brain-dev-crew 的设置补上了 Lean 的拦截：调用工具前。别的都没动；改之前的那份留作备份：brain-dev-crew.json.bak-20261110-091500。新开的会话才会用上。', rows: fx.crew().rows.slice(0, 2) }) })
    const p = props()
    render(<SettingsView {...p} />)
    const kit = screen.getByRole('region', { name: 'brain-kit' })
    expect(kit).toHaveTextContent('0.1.0')
    expect(kit).toHaveTextContent('交付评估 有')                                          // what the part does, not its module name
    expect(kit).toHaveTextContent('标准考题 没有')
    expect(kit).not.toHaveTextContent('evaluate')
    expect(within(kit).getByText('找到了')).toHaveClass('tone-ok')
    expect(screen.getByRole('region', { name: '团队运行机' })).toHaveTextContent(`在服务器上${PERSONAL_RUNNER}`)
    const repos = screen.getByRole('region', { name: '仓库' })
    const r1 = repos.querySelector('[data-repo="r1"]') as HTMLElement
    expect(r1).toHaveClass('is-ok')
    expect(Array.from(r1.querySelectorAll('.st-kit .br-tag')).map((t) => t.textContent)).toEqual(['需求号 PAY-n', '跟踪 不用 Jira', '默认分支 main', 'GitLab shop/shop-pay'])
    expect(within(r1).getByText('能用')).toHaveClass('tone-ok')
    const r2 = repos.querySelector('[data-repo="r2"]') as HTMLElement
    expect(r2).not.toHaveClass('is-bad')                                                // not installed is no fault
    expect(within(r2).getByText('没装')).toHaveClass('tone-idle')
    await user.click(within(r2).getByRole('button', { name: '去装' }))
    expect(p.go).toHaveBeenCalledWith({ kind: 'import', path: '/Users/alice/workplace/shop-web' })
    const me = screen.getByRole('region', { name: '你是谁' })
    expect(me).toHaveTextContent('alice')
    expect(me).toHaveTextContent('GitLab 令牌 有')
    expect(me).not.toHaveTextContent('glpat')
    const crew = screen.getByRole('region', { name: 'KiroCrew' })
    expect(crew).toHaveTextContent('brain-dev-crew 的设置缺 Lean 的拦截：调用工具前')
    expect(within(crew).getByRole('img', { name: '1 正常，1 注意，1 有问题' })).toBeInTheDocument()
    expect(crew).toHaveClass('is-bad')
    await user.click(within(crew).getByRole('button', { name: '补上 Lean 的拦截' }))
    expect(await within(crew).findByText('给 brain-dev-crew 的设置补上了 Lean 的拦截：调用工具前。别的都没动；改之前的那份留作备份：brain-dev-crew.json.bak-20261110-091500。新开的会话才会用上。')).toBeInTheDocument()
    expect(apiCalls.find((c) => c.method === 'POST')).toEqual({ method: 'POST', path: `${API}/crew/fix`, body: { what: 'agent' } })
    expect(p.reloadStatus).toHaveBeenCalled()
  })

  test('no repo yet: the 仓库 card says how one gets in', () => {
    const s = fx.status()
    s.repos = []
    installFixtureRoutes({})
    render(<SettingsView {...props({ status: s })} />)
    const card = screen.getByRole('region', { name: '仓库' })
    expect(within(card).getByText('还没有仓库：点「导入仓库」。')).toBeInTheDocument()
    expect(within(within(card).getByRole('list', { name: '导入仓库的步骤' })).getAllByRole('listitem')).toHaveLength(4)
  })

  test('team mode on: the runner card says when the last round ran, the signer’s counts and the way to 运行机', async () => {
    const user = userEvent.setup()
    const s = server()
    s.app.team = true
    s.team = { ...fx.team().status }
    installFixtureRoutes({})
    const p = props({ status: s })
    render(<SettingsView {...p} />)
    const card = screen.getByRole('region', { name: '团队运行机' })
    expect(card).toHaveTextContent('正常')
    expect(card).toHaveTextContent('等签名 1')
    expect(card).toHaveTextContent('拒签 1')
    expect(card).toHaveTextContent('签名服务和 app 是同一个系统用户')
    await user.click(within(card).getByRole('button', { name: '看运行机' }))
    expect(p.go).toHaveBeenCalledWith({ kind: 'team' })
  })

  test('a round that failed and a name never set: said in words; the step’s own words and the command apart', () => {
    const s = server()
    s.app.team = true
    s.team = { ...fx.team().status, loop_error: 'poll: HTTPError 502 Bad Gateway' }
    s.me = { ...s.me, user: null }
    s.repos = s.repos.map((r) => ({ ...r, me: null }))
    installFixtureRoutes({})
    render(<SettingsView {...props({ status: s })} />)
    const runner = screen.getByRole('region', { name: '团队运行机' })
    expect(runner).toHaveTextContent('上一轮有一步没成：点「看运行机」看是哪一步')
    expect(runner.querySelector('details pre')).toHaveTextContent('poll: HTTPError 502 Bad Gateway')
    expect(runner.querySelector('details')).not.toHaveAttribute('open')
    const who = screen.getByRole('region', { name: '你是谁' })
    expect(who).toHaveTextContent('导入仓库时会问你的名字，在那里填上，点「保存」。')
    const cmd = within(who).getByTestId('cmd-line')                                       // the command, folded under 细节
    expect(cmd).toHaveTextContent('git config --global brain.user 你的用户名复制')
    expect(cmd.closest('details')).not.toHaveAttribute('open')
  })
  test('你是谁 per repo: the login name without GitLab; 「你的 GitLab 用户名」 where brain-kit would not know, saved into that repo', async () => {
    const user = userEvent.setup()
    const s = fx.status()
    const login: RepoMe = { name: 'alice', source: 'login', gitlab: '', text: 'alice（这台机器的登录名：这个仓库不用 GitLab）', ask: false,
      label: '', why: '', suggest: '', error: '' }
    const ask: RepoMe = { name: null, source: 'none', gitlab: 'https://gitlab.example.com', text: '', ask: true, label: '你的 GitLab 用户名',
      why: '这个仓库用 GitLab：认领和交接都按这个名字认人，要和你在 GitLab 上的用户名一样', suggest: 'alice', error: '' }
    s.me = { user: null, token: false, how: 'GitLab 令牌（没有）' }
    s.repos = [{ ...s.repos[0], me: login }, { ...s.repos[0], id: 'r2', path: '/Users/alice/workplace/shop-web', name: 'shop-web', me: ask }]
    const after = fx.status()
    installFixtureRoutes({ [`POST ${API}/me/name`]: () => ({ ok: true, text: '这个仓库的 git config brain.user 设成了 alice',
      me: { ...ask, name: 'alice', source: 'git', text: 'alice（这个仓库里记下的名字）', ask: false }, status: after }) })
    const p = props({ status: s })
    render(<SettingsView {...p} />)
    const kpi = screen.getByRole('region', { name: '你是谁' })
    expect(kpi).toHaveTextContent('alice')
    expect(kpi).toHaveTextContent('1 个仓库里 brain-kit 还不知道你是谁：在下面「仓库」那一行看一眼名字，点「保存」')
    const card = screen.getByRole('region', { name: '仓库' })
    const r1 = card.querySelector('[data-repo="r1"]') as HTMLElement
    expect(within(r1).getByTestId('me-line')).toHaveTextContent('alice（这台机器的登录名：这个仓库不用 GitLab）')
    expect(within(r1).queryByRole('textbox')).toBeNull()
    const r2 = card.querySelector('[data-repo="r2"]') as HTMLElement
    const who = within(r2).getByRole('group', { name: '你的 GitLab 用户名' })
    expect(within(who).getByLabelText('你的 GitLab 用户名')).toHaveValue('alice')
    expect(who).toHaveTextContent(ask.why)
    await user.click(within(who).getByRole('button', { name: '保存' }))
    await waitFor(() => expect(p.setStatus).toHaveBeenCalledWith(after))
    expect(apiCalls.find((c) => c.path === `${API}/me/name`)?.body).toEqual({ path: '/Users/alice/workplace/shop-web', name: 'alice' })
    expect(within(r2).getByTestId('me-line')).toHaveTextContent('alice（这个仓库里记下的名字）已保存')
    for (const c of apiCalls) expect(JSON.stringify(c.body ?? '')).not.toMatch(/token/i)    // nothing asks for a token
  })

  test('no brain-kit: its place is asked first, at the top, focused, with why (by the reason) and where to get one', async () => {
    const missing = fx.status()
    missing.brainkit = { ...missing.brainkit, ok: false, version: null, error: '这里没有 brain-kit（找不到 /Users/alice/src/brain-kit/brainkit/__init__.py）' }
    missing.repos[0] = { ...missing.repos[0], ok: false, error: 'brain-kit 没装载', me: null }
    installFixtureRoutes({})
    const { unmount } = render(<SettingsView {...props({ status: missing })} />)
    const card = screen.getByTestId('settings-no-brainkit')
    expect(card).toHaveTextContent('找不到 brain-kit')
    expect(card).toHaveTextContent('做什么在下面的「brain-kit 在哪」写上它的目录，点「保存」')
    expect(within(card).getByTestId('cmd-line')).toHaveTextContent('git clone https://github.com/ai-for-developer/brain-kit.git ~/brain-kit复制')
    const input = await within(card).findByLabelText('brain-kit 在哪')
    await waitFor(() => expect(input).toHaveFocus())
    expect(screen.getAllByLabelText('brain-kit 在哪')).toHaveLength(1)                  // not a second time in the form below
    expect(card.compareDocumentPosition(screen.getByRole('region', { name: '仓库' })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // a repo that waits for brain-kit points at that card, not at 导入仓库
    const waiting = screen.getByRole('region', { name: '仓库' }).querySelector<HTMLElement>('li[data-repo="r1"]') as HTMLElement
    expect(waiting).toHaveTextContent('等 brain-kit')
    expect(waiting).toHaveTextContent('brain-kit 没装载：先在最上面写对 brain-kit 的位置，这个仓库就能读了')
    expect(within(waiting).queryByRole('button', { name: /去检查|去装/ })).toBeNull()
    expect(within(waiting).getByTestId('repo-waits-kit').parentElement?.children).toHaveLength(1)   // one line, no gap before its colon
    // nor can one without brain-kit be installed now: no 去装 to a page that cannot install it
    const bare = screen.getByRole('region', { name: '仓库' }).querySelector<HTMLElement>('li[data-repo="r2"]') as HTMLElement
    expect(bare).toHaveTextContent('还没装 brain-kit：先在最上面写对 brain-kit 的位置，才能装')
    expect(bare).not.toHaveTextContent('在「导入仓库」里检查它')
    expect(within(bare).queryByRole('button', { name: /去检查|去装/ })).toBeNull()
    unmount()
    const old = fx.status()
    old.brainkit = { ...old.brainkit, ok: false, error: 'brain-kit 太旧（没有 sign.SpoolSigner）：要 72b278e 或更新的版本' }
    const { unmount: u2 } = render(<SettingsView {...props({ status: old })} />)
    expect(screen.getByTestId('settings-no-brainkit')).toHaveTextContent('brain-kit 太旧了（位置是对的）')
    expect(screen.getByTestId('settings-no-brainkit')).toHaveTextContent('做什么在它的目录里拉取最新的代码（点「复制」拿命令），再点下面的「保存」重新读它')
    expect(within(screen.getByTestId('settings-no-brainkit')).getByTestId('cmd-line')).toHaveTextContent(`git -C ${old.brainkit.path} pull --ff-only`)
    expect(screen.getByTestId('settings-no-brainkit')).not.toHaveTextContent('git clone')
    u2()
    const broken = fx.status()
    broken.brainkit = { ...broken.brainkit, ok: false, error: "brain-kit 导入失败：ModuleNotFoundError: No module named 'x'" }
    render(<SettingsView {...props({ status: broken })} />)
    const b = screen.getByTestId('settings-no-brainkit')
    expect(b).toHaveTextContent('brain-kit 读不进来')
    expect(b.querySelector('details pre')).toHaveTextContent('ModuleNotFoundError')       // the Python error only under 细节
    expect(b.querySelector('details')).not.toHaveAttribute('open')
  })

  test('a repo says its state by the reason; without GitLab it says so instead of an empty tag', () => {
    const s = fx.status()
    s.repos = [
      { ...s.repos[0], kit: { ...s.repos[0].kit!, gitlab_base: '', gitlab_project: '' } },
      { ...s.repos[1], id: 'r2', error: '找不到这个目录' },
    ]
    installFixtureRoutes({})
    render(<SettingsView {...props({ status: s })} />)
    const r1 = document.querySelector('[data-repo="r1"]') as HTMLElement
    expect(Array.from(r1.querySelectorAll('.st-kit .br-tag')).map((t) => t.textContent)).toContain('不用 GitLab')
    const r2 = document.querySelector('[data-repo="r2"]') as HTMLElement
    expect(within(r2).getByText('找不到目录')).toHaveClass('tone-bad')
    expect(within(r2).getByRole('button', { name: '移出列表' }).closest('.st-repo-top')).toBeTruthy()  // on the name's line, at its end
  })

  test('和队友同步: how often to fetch, and each repo done with the team pushed at checkpoints, each with what it does; saved with the form', async () => {
    const user = userEvent.setup()
    const s = fx.status()
    const pay = s.repos[0]
    const repo = (id: string, name: string, sync: RepoInfo['sync']): RepoInfo => ({ ...pay, id, name, path: `/Users/alice/workplace/${name}`, sync })
    const cmd = 'git -C /Users/alice/workplace/shop-order config brain.user 你的GitLab用户名'
    s.repos = [pay,
      repo('r2', 'shop-order', { auto_push: true, push_cmd: cmd, push_why: 'app 不问 GitLab 你是谁，还不知道你在这个仓库的用户名，分不出哪些分支是你的，所以还没推过：在终端里写一次（把最后一个词换成你的 GitLab 用户名）',
        fetch: { at: pay.sync!.fetch!.at, ok: false, text: '从远端取最新没成功：连不上远端：检查网络或 VPN，再点一次。', raw: "fatal: unable to access 'https://gitlab.example.com/shop/shop-order.git/'" } }),
      repo('r3', 'shop-tools', { auto_push: null, push_why: '', push_cmd: '', fetch: null })]
    installFixtureRoutes({
      [`POST ${API}/settings`]: (body) => ({ ok: true, settings: { ...fx.settings(), ...(body as Partial<Settings>) }, status: s }),
    })
    render(<SettingsView {...props({ status: s })} />)
    const sync = await screen.findByRole('group', { name: '和队友同步' })
    const every = within(sync).getByLabelText('每隔几分钟从远端取最新')
    expect(every).toHaveValue(5)
    expect(every).toHaveAccessibleDescription(FETCH_HINT)
    expect(sync).toHaveTextContent(PUSH_HINT)
    const push = within(sync).getByRole('group', { name: '过关卡时把进度推到远端' })
    expect(within(push).getAllByRole('checkbox').map((c) => c.closest('label')?.textContent)).toEqual(['shop-pay', 'shop-order'])
    expect(within(push).getByRole('checkbox', { name: 'shop-pay' })).toBeChecked()
    // the backend's why, with the line to type; the fetch that did not work, with git's words folded
    expect(push).toHaveTextContent('app 不问 GitLab 你是谁')
    expect(within(push).getByText(cmd)).toBeInTheDocument()
    expect(within(sync).getByTestId('fetch-failed')).toHaveTextContent('shop-order：从远端取最新没成功：连不上远端：检查网络或 VPN，再点一次。细节')
    expect(sync).toHaveTextContent(/上次取到最新：shop-pay \d+ \S*前/)
    await user.click(within(push).getByRole('checkbox', { name: 'shop-order' }))
    expect(push).not.toHaveTextContent('app 不问 GitLab 你是谁')          // off: nothing to fix for it
    await user.clear(every)
    await user.type(every, '10')
    await user.click(screen.getByRole('button', { name: '保存' }))
    expect(await screen.findByText('已保存')).toBeInTheDocument()
    const post = apiCalls.find((c) => c.method === 'POST' && c.path === `${API}/settings`)
    expect(post?.body).toMatchObject({ fetch_every_min: 10, auto_push: { '/Users/alice/workplace/shop-order': false } })
  })

  test('和队友同步 with only repos done on this machine: no push setting, one line that says why', async () => {
    const s = fx.status()
    s.repos = [{ ...s.repos[0], sync: { auto_push: null, push_why: '', push_cmd: '', fetch: null } }]
    installFixtureRoutes()
    render(<SettingsView {...props({ status: s })} />)
    const sync = await screen.findByRole('group', { name: '和队友同步' })
    expect(within(sync).queryAllByRole('checkbox')).toEqual([])
    expect(sync).toHaveTextContent('现在的仓库都只在本机做（没有远端），不用推。')
    expect(within(sync).queryByTestId('fetch-failed')).toBeNull()
  })

  // a box's name is its sentence, with a space where its number is
  const WIP = '每人同时在做的需求，最多 个'
  const WAIT = '等人批准、回答或验收，超过 个工作小时算超时'
  const STALE = '别人在自己机器上做的需求， 个工作小时没有推上新进度算很久没动'
  const VERIFY = '同一项检查 verify 连续失败 次，就该停下来问人'
  const limitsCard = () => screen.findByRole('group', { name: '全队视图' })

  test('全队视图: four lines, each a sentence around its number, whole numbers in the backend’s bounds, after the work directory and before team mode', async () => {
    installFixtureRoutes({})
    render(<SettingsView {...props({ status: server() })} />)
    const limits = await limitsCard()
    const box = (name: string) => within(limits).getByRole('spinbutton', { name })
    expect(box(WIP)).toHaveValue(3)
    expect(box(WAIT)).toHaveValue(4)
    expect(box(STALE)).toHaveValue(8)
    expect(box(VERIFY)).toHaveValue(3)
    expect(within(limits).getAllByRole('spinbutton').map((x) => [x.getAttribute('min'), x.getAttribute('max'), x.getAttribute('step')]))
      .toEqual([['1', '20', '1'], ['1', '200', '1'], ['1', '400', '1'], ['1', '20', '1']])
    const hint = '工作小时只算周一到周五 9 点到 18 点（这台机器的时间）。到了上限只提醒，不拦开工。'
    expect(within(limits).getByText(hint)).toBeInTheDocument()
    expect(box(WIP)).toHaveAccessibleDescription(hint)
    const work = screen.getByLabelText('每个需求的工作目录放在哪')
    expect(work.compareDocumentPosition(limits) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(limits.compareDocumentPosition(screen.getByRole('group', { name: '团队模式' })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  test('a limit changed is saved as a number; the other three go as they were', async () => {
    const user = userEvent.setup()
    installFixtureRoutes({
      [`POST ${API}/settings`]: (body) => ({ ok: true, settings: { ...fx.settings(), ...(body as Partial<Settings>) }, status: fx.status() }),
    })
    render(<SettingsView {...props()} />)
    const box = within(await limitsCard()).getByRole('spinbutton', { name: WIP })
    await user.clear(box)
    await user.type(box, '5')
    await user.click(screen.getByRole('button', { name: '保存' }))
    expect(await screen.findByText('已保存')).toBeInTheDocument()
    const { repos: _repos, role: _role, ...rest } = fx.settings()
    expect(apiCalls.find((c) => c.method === 'POST')?.body).toEqual({ ...rest, wip_limit: 5 })
    expect(box).toHaveValue(5)
  })

  test('a limit out of bounds goes to the backend, and 保存 shows its refusal, which names the field', async () => {
    const user = userEvent.setup()
    installFixtureRoutes({ [`POST ${API}/settings`]: () => { throw apiError(400, 'invalid_settings', '「每人同时在做的需求上限」要写 1 到 20 的整数') } })
    render(<SettingsView {...props()} />)
    const box = within(await limitsCard()).getByRole('spinbutton', { name: WIP })
    await user.clear(box)
    await user.type(box, '25')
    await user.click(screen.getByRole('button', { name: '保存' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('「每人同时在做的需求上限」要写 1 到 20 的整数（400）')
    expect(apiCalls.find((c) => c.method === 'POST')?.body).toMatchObject({ wip_limit: 25 })
    expect(screen.queryByText('已保存')).toBeNull()
  })

  test('a personal install: no 团队模式 and no 一键建运行机; the team runner card leads to 运行机; 这台机器 last, below the form', async () => {
    const user = userEvent.setup()
    installFixtureRoutes({})
    const p = props()
    render(<SettingsView {...p} />)
    const form = await screen.findByRole('region', { name: '设置' })
    expect(within(form).queryByRole('group', { name: '团队模式' })).toBeNull()
    expect(screen.queryByRole('checkbox', { name: '这台机器是团队运行机' })).toBeNull()
    expect(screen.queryByRole('region', { name: '一键建运行机' })).toBeNull()
    expect(apiCalls.some((c) => c.path === `${API}/team/setup`)).toBe(false)
    const role = screen.getByRole('region', { name: '这台机器' })
    expect(role).toHaveTextContent('个人电脑')
    const box = within(role).getByRole('checkbox', { name: ROLE_SWITCH })
    expect(box).not.toBeChecked()
    expect(box).toHaveAccessibleDescription(ROLE_HINT)
    expect(form.compareDocumentPosition(role) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(role.parentElement?.lastElementChild).toBe(role)
    const runner = screen.getByRole('region', { name: '团队运行机' })
    await user.click(within(runner).getByRole('button', { name: '看团队运行机' }))
    expect(p.go).toHaveBeenCalledWith({ kind: 'team' })
  })

  test('这台机器: ticking it asks first, then makes this install the team server, with 团队模式 and 一键建运行机 on the page', async () => {
    const user = userEvent.setup()
    installFixtureRoutes({
      [`POST ${API}/settings`]: (body) => ({ ok: true, settings: { ...fx.settings(), ...(body as Partial<Settings>) }, status: server() }),
    })
    render(<Live start={fx.status()} />)
    const role = await screen.findByRole('region', { name: '这台机器' })
    await user.click(within(role).getByRole('checkbox', { name: ROLE_SWITCH }))
    const ask = within(role).getByRole('group', { name: '设成团队运行机' })
    expect(ask).toHaveTextContent(TO_SERVER)
    expect(ask).not.toHaveClass('is-danger')
    expect(apiCalls.filter((c) => c.method === 'POST')).toEqual([])                     // nothing sent yet
    await user.click(within(ask).getByRole('button', { name: '设成团队运行机' }))
    expect(await screen.findByRole('region', { name: '一键建运行机' })).toBeInTheDocument()
    expect(screen.getByRole('group', { name: '团队模式' })).toBeInTheDocument()
    expect(apiCalls.filter((c) => c.method === 'POST')).toEqual([{ method: 'POST', path: `${API}/settings`, body: { role: 'team_server' } }])
    expect(role).toHaveTextContent('团队运行机（服务器）')
    expect(within(role).getByRole('checkbox', { name: ROLE_SWITCH })).toBeChecked()
    expect(within(role).queryByRole('group')).toBeNull()
  })

  test('back to personal on a team server in team mode: a red confirm says the bridge stops; after it the form saves team mode off', async () => {
    const user = userEvent.setup()
    const on = server()
    on.app.team = true
    on.team = { ...fx.team().status }
    const running: Settings = { ...fx.settings(), role: 'team_server', team: { ...fx.settings().team, enabled: true } }
    const personal: Settings = { ...running, role: 'personal', team: { ...running.team, enabled: false } }
    installFixtureRoutes({
      [`GET ${API}/settings`]: () => running,
      [`POST ${API}/settings`]: (body) => (body as Partial<Settings>).role
        ? { ok: true, settings: personal, status: fx.status() }
        : { ok: true, settings: { ...personal, ...(body as Partial<Settings>) }, status: fx.status() },
    })
    render(<Live start={on} />)
    expect(await screen.findByRole('checkbox', { name: '这台机器是团队运行机' })).toBeChecked()
    const role = screen.getByRole('region', { name: '这台机器' })
    await user.click(within(role).getByRole('checkbox', { name: ROLE_SWITCH }))
    let ask = within(role).getByRole('group', { name: '改回个人电脑' })
    expect(ask).toHaveClass('is-danger')
    expect(ask).toHaveTextContent(TO_PERSONAL_ON)
    await user.click(within(ask).getByRole('button', { name: '取消' }))         // 取消: as it was, nothing sent
    expect(within(role).getByRole('checkbox', { name: ROLE_SWITCH })).toBeChecked()
    expect(apiCalls.filter((c) => c.method === 'POST')).toEqual([])
    await user.click(within(role).getByRole('checkbox', { name: ROLE_SWITCH }))
    ask = within(role).getByRole('group', { name: '改回个人电脑' })
    await user.click(within(ask).getByRole('button', { name: '改回个人电脑' }))
    await waitFor(() => expect(screen.queryByRole('region', { name: '一键建运行机' })).toBeNull())
    expect(screen.queryByRole('group', { name: '团队模式' })).toBeNull()
    await user.click(screen.getByRole('button', { name: '保存' }))
    const saved = apiCalls.filter((c) => c.method === 'POST').map((c) => c.body as Partial<Settings>)
    expect(saved[0]).toEqual({ role: 'personal' })
    expect(saved[1].team?.enabled).toBe(false)                                    // not the old team mode back
    expect(saved[1]).not.toHaveProperty('role')
  })
})

describe('设置: 允许快速模式, a team rule', () => {
  const MR31 = 'https://gitlab.example.com/shop/shop-pay/-/merge_requests/31'
  const withFast = (fast: FastModeView): Status => {
    const s = fx.status()
    s.repos[0] = { ...s.repos[0], fast }
    return s
  }
  const switchOf = async () => within(await screen.findByRole('group', { name: '允许快速模式' })).getByRole('checkbox', { name: 'shop-pay' })
  const flips = () => apiCalls.filter((c) => c.method === 'POST' && c.path === `${API}/repos/fast-mode`)
  const opened = (on: boolean): FastModeResult => ({
    ok: true, text: '开了 MR !31：合并以后生效', mr: { iid: 31, url: MR31, existing: false }, commands: [], new_mr_url: null,
    fast: { on: !on, why: '', pending: { on, iid: 31, url: MR31 } },
  })

  test('in 和队友同步 beside the push setting, checked as the default branch says; a flip asks first and only 确定 opens the MR', async () => {
    const user = userEvent.setup()
    installFixtureRoutes({ [`POST ${API}/repos/fast-mode`]: () => opened(false) })
    const p = props({ status: withFast({ on: true, why: '', pending: null }) })
    render(<SettingsView {...p} />)
    const box = await switchOf()
    const sync = screen.getByRole('group', { name: '和队友同步' })
    expect(within(sync).getByRole('group', { name: '允许快速模式' })).toHaveTextContent(FAST_SWITCH_HINT)
    expect(box).toBeChecked()
    await user.click(box)
    const ask = screen.getByRole('group', { name: '确认不允许快速模式' })
    expect(ask).toHaveTextContent(FAST_RULE)
    expect(ask).toHaveTextContent('关掉以后，开着的快速需求的 MR 再跑 CI 就过不了。')
    expect(box).toBeChecked()
    await user.click(within(ask).getByRole('button', { name: '取消' }))
    expect(flips()).toEqual([])
    await user.click(box)
    await user.click(within(screen.getByRole('group', { name: '确认不允许快速模式' })).getByRole('button', { name: '确定，开 MR' }))
    const out = await screen.findByTestId('fast-result')
    expect(within(out).getByRole('status')).toHaveTextContent('开了 MR !31：合并以后生效')
    expect(within(out).getByRole('link', { name: 'MR !31' })).toHaveAttribute('href', MR31)
    expect(flips().map((c) => c.body)).toEqual([{ repo: 'r1', on: false }])
    expect(p.reloadStatus).toHaveBeenCalled()
    expect(await switchOf()).toBeDisabled()
    expect(screen.getByTestId('fast-pending')).toHaveTextContent('MR !31 等合并：合并以后不允许快速模式')
  })

  test('turning it on says nothing of failing MRs', async () => {
    const user = userEvent.setup()
    installFixtureRoutes()
    render(<SettingsView {...props({ status: withFast({ on: false, why: '', pending: null }) })} />)
    await user.click(await switchOf())
    const ask = screen.getByRole('group', { name: '确认允许快速模式' })
    expect(ask).toHaveTextContent(FAST_RULE)
    expect(ask).not.toHaveTextContent('CI')
  })

  test('its MR waiting to be merged: the link, no second flip; without its number, the branch', async () => {
    installFixtureRoutes()
    const { unmount } = render(<SettingsView {...props({ status: withFast({ on: false, why: '', pending: { on: true, iid: 31, url: MR31 } }) })} />)
    expect(await switchOf()).toBeDisabled()
    expect(await switchOf()).not.toBeChecked()
    expect(within(screen.getByTestId('fast-pending')).getByRole('link', { name: 'MR !31' })).toHaveAttribute('href', MR31)
    expect(screen.getByTestId('fast-pending')).toHaveTextContent('MR !31 等合并：合并以后允许快速模式')
    unmount()
    render(<SettingsView {...props({ status: withFast({ on: false, why: '', pending: { on: true, iid: null, url: null } }) })} />)
    expect(await screen.findByTestId('fast-pending')).toHaveTextContent('分支 brain/fast-mode 等合并')
  })

  test('off with brain-kit’s why where nothing else lets a fast run here', async () => {
    const why = '合规模式的仓库（lean.json 里 regulated 是 true）每个改动都走 Lean：不能用快速模式'
    installFixtureRoutes()
    render(<SettingsView {...props({ status: withFast({ on: true, why, pending: null }) })} />)
    expect(await switchOf()).toBeDisabled()
    expect(screen.getByRole('group', { name: '允许快速模式' })).toHaveTextContent(why)
  })

  test('without a GitLab token: the commands and GitLab’s new-MR link', async () => {
    const user = userEvent.setup()
    const cmd = 'git -C /Users/alice/workplace/shop-pay push -u origin brain/fast-mode'
    const link = 'https://gitlab.example.com/shop/shop-pay/-/merge_requests/new?merge_request%5Bsource_branch%5D=brain%2Ffast-mode'
    installFixtureRoutes({ [`POST ${API}/repos/fast-mode`]: () => ({ ...opened(false), text: '提交在分支 brain/fast-mode 上了：这台机器上没有 GitLab 令牌，推送和 MR 要你自己来', mr: null, commands: [cmd], new_mr_url: link,
      fast: { on: true, why: '', pending: { on: false, iid: null, url: null } } }) })
    render(<SettingsView {...props({ status: withFast({ on: true, why: '', pending: null }) })} />)
    await user.click(await switchOf())
    await user.click(screen.getByRole('button', { name: '确定，开 MR' }))
    const out = await screen.findByTestId('fast-result')
    expect(within(out).getByTestId('commands')).toHaveTextContent(cmd)
    expect(within(out).getByRole('link', { name: '在 GitLab 上开 MR' })).toHaveAttribute('href', link)
  })

  test('保存 never carries it, and a repo done only on this machine has no switch', async () => {
    const user = userEvent.setup()
    const s = withFast({ on: true, why: '', pending: null })
    s.repos = [...s.repos, { ...s.repos[0], id: 'r9', name: 'shop-local', path: '/Users/alice/workplace/shop-local', sync: { auto_push: null, push_why: '', push_cmd: '', fetch: null } }]
    installFixtureRoutes({ [`POST ${API}/settings`]: (body) => ({ ok: true, settings: { ...fx.settings(), ...(body as Partial<Settings>) }, status: s }) })
    render(<SettingsView {...props({ status: s })} />)
    const group = await screen.findByRole('group', { name: '允许快速模式' })
    expect(within(group).getAllByRole('checkbox').map((c) => c.closest('label')?.textContent)).toEqual(['shop-pay'])
    await user.click(screen.getByRole('button', { name: '保存' }))
    await screen.findByText('已保存')
    expect(JSON.stringify(apiCalls.find((c) => c.path === `${API}/settings` && c.method === 'POST')?.body)).not.toMatch(/fast/)
    expect(flips()).toEqual([])
  })
})
