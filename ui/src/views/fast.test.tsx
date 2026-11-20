import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'
import { colOf } from '../cto/Flow'
import { fx } from '../test/data'
import { MERGE_STEPS, MR, MR_URL, WAITS, actAnswer as answer, fastBoard, fastDetail, fastRow } from '../test/fast'
import { API, installFixtureRoutes } from '../test/routes'
import { apiCalls, apiError } from '../test/stubs/app-sdk'
import type { FastTime, Requirement, RequirementDetail } from '../types'
import { DetailView } from './Detail'
import { FULL_ASK } from './FastActs'
import { OverviewView } from './Overview'
import type { ViewProps } from './props'

function props(over: Partial<ViewProps> = {}): ViewProps {
  return { status: fx.status(), go: vi.fn(), replace: vi.fn(), reloadStatus: vi.fn(), setStatus: vi.fn(), pollMs: 60_000, ...over }
}

function renderDetail(d: RequirementDetail = fastDetail(), board = fastBoard(), more: Parameters<typeof installFixtureRoutes>[0] = {}) {
  installFixtureRoutes({ [`GET ${API}/requirements/*`]: () => d, [`GET ${API}/board/*`]: () => board, ...more })
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
      '1 · 做完写代码（做完）提交了 3 次看代码 ↗', '2 · 做完自动检查（做完）3 项都过', '3 · 等你开 MR（等你）', '4 · 还没轮到CI（还没轮到）', '5 · 还没轮到合并（还没轮到）',
    ])
    expect(card).toHaveTextContent('代码写好了，等开 MR')
    expect(screen.queryByTestId('handover')).toBeNull()
    expect(screen.queryByText('AI-DLC Lean')).toBeNull()
    expect(screen.queryByTestId('lean-phases')).toBeNull()
    expect(screen.queryByRole('region', { name: '会话记录' })).toBeNull()      // Lean's Session notes: a fast run keeps none
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

  test('写代码’s 看代码 ↗ opens the board with 产出 on all its changes', async () => {
    const p = renderDetail()
    const card = await screen.findByTestId('fast-card')
    await within(card).findAllByTestId('fast-step')
    fireEvent.click(within(card).getByRole('button', { name: '看代码 ↗' }))
    expect(p.go).toHaveBeenLastCalledWith({ kind: 'board', repo: 'r1', key: 'PAY-17', from: 'item', out: { node: 'code' } })
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

describe('需求现状 of a fast run: 时间花在哪, from its sessions on this machine', () => {
  const card = () => screen.getByRole('article', { name: '时间花在哪' })
  const timed = (time: FastTime | null) => ({ ...fastBoard(), time })
  const TIME: FastTime = { agent: 1500, person: 3720, waits: 3, sessions: 2, whole: true }

  test('the AI’s time is the number, 等人 and its waits the line, the bar those two; the legend opens the board', async () => {
    const p = renderDetail(fastDetail(), timed(TIME))
    await screen.findAllByTestId('fast-step')
    expect(card().querySelector('.cto-value')).toHaveTextContent(/^25 分钟$/)
    expect(card().querySelector('.cto-desc')).toHaveTextContent(/^AI 在本机做 · 等人 1 小时 2 分（3 段）$/)
    expect(within(card()).getByRole('img')).toHaveAccessibleName('时间花在哪：AI 在本机做 25 分钟，等人 1 小时 2 分')
    const legend = within(card()).getAllByRole('button')
    expect(legend.map((b) => b.textContent)).toEqual(['AI 在本机做25 分钟', '等人1 小时 2 分'])
    legend.forEach((b, k) => {
      fireEvent.click(b)
      expect(p.go).toHaveBeenCalledTimes(k + 1)
      expect(p.go).toHaveBeenLastCalledWith({ kind: 'board', repo: 'r1', key: 'PAY-17', from: 'item' })
    })
  })

  test('a transcript read only in part, or a session not read yet: 至少 before the numbers', async () => {
    renderDetail(fastDetail(), timed({ ...TIME, whole: false }))
    await screen.findAllByTestId('fast-step')
    expect(card().querySelector('.cto-value')).toHaveTextContent(/^至少25 分钟$/)
    expect(card().querySelector('.cto-desc')).toHaveTextContent(/^AI 在本机做 · 等人至少 1 小时 2 分（3 段）$/)
    expect(within(card()).getAllByRole('button').map((b) => b.textContent)).toEqual(['AI 在本机做至少 25 分钟', '等人至少 1 小时 2 分'])
  })

  test('no session of it read here: one sentence, never a 0', async () => {
    renderDetail(fastDetail(), timed(null))
    await screen.findAllByTestId('fast-step')
    expect(card()).toHaveTextContent('时间从它在这台机器上的会话里算：这里还没读到它的会话')
    expect(card()).not.toHaveTextContent(/\d/)
    expect(within(card()).queryByRole('button')).toBeNull()
  })
})

const ACT_MR = `${API}/requirements/r1/PAY-17/act/mr`
const ACT_FULL = `${API}/requirements/r1/PAY-17/act/full`
const posts = () => apiCalls.filter((c) => c.method === 'POST')

describe('开 MR and 转完整流程 on 需求现状', () => {
  test('开 MR says first what it will do and runs on one click; the answer has the MR, brain-kit’s words under 细节; the page reads it again', async () => {
    const user = userEvent.setup()
    const opened = answer({ text: '推上去了，开了 MR !12（没有交接标签：人来审、合并）', pushed: true, mr: { iid: 12, url: MR_URL, existing: false }, output: 'pushed ai/alice/PAY-17-coupon\nopened !12' })
    const p = renderDetail(fastDetail(), fastBoard(), { [`POST ${ACT_MR}`]: () => opened })
    const card = await screen.findByTestId('fast-acts')
    expect(card).toHaveTextContent('会把分支 ai/alice/PAY-17-coupon 推到远端，在 GitLab 上开 MR：CI 照跑，人来审、合并，不交给运行机。')
    expect(within(card).getByRole('button', { name: '开 MR' })).toHaveClass('is-primary')
    await user.click(within(card).getByRole('button', { name: '开 MR' }))
    const out = await within(card).findByTestId('act-result')
    expect(within(out).getByRole('status')).toHaveTextContent('推上去了，开了 MR !12（没有交接标签：人来审、合并）')
    expect(within(out).getByRole('link', { name: 'MR !12' })).toHaveAttribute('href', MR_URL)
    expect(out.querySelector('details')).not.toHaveAttribute('open')
    expect(out.querySelector('details')).toHaveTextContent('opened !12')
    expect(posts()).toEqual([{ method: 'POST', path: ACT_MR, body: {} }])
    expect(p.reloadStatus).toHaveBeenCalled()
  })

  test('without a GitLab token: what was pushed, GitLab’s new-MR link and the commands to run, 复制 takes them', async () => {
    const user = userEvent.setup()
    const cmd = 'glab mr create --source-branch ai/alice/PAY-17-coupon --target-branch main --fill --yes'
    const link = 'https://gitlab.example.com/shop/shop-pay/-/merge_requests/new?merge_request%5Bsource_branch%5D=ai%2Falice%2FPAY-17-coupon'
    renderDetail(fastDetail(), fastBoard(), { [`POST ${ACT_MR}`]: () => answer({ text: '推上去了：这台机器上没有 GitLab 令牌，MR 要你自己开', pushed: true, commands: [cmd], new_mr_url: link }) })
    await user.click(within(await screen.findByTestId('fast-acts')).getByRole('button', { name: '开 MR' }))
    const out = await screen.findByTestId('act-result')
    expect(within(out).getByRole('link', { name: '在 GitLab 上开 MR' })).toHaveAttribute('href', link)
    expect(within(out).getByTestId('commands')).toHaveTextContent(cmd)
    expect(out).toHaveTextContent('或者在终端里开 MR：')
    await user.click(within(out).getByRole('button', { name: '复制' }))
    expect(await navigator.clipboard.readText()).toBe(cmd)
  })

  test('brain-kit’s warnings are said under the verdict, each on its own line', async () => {
    const user = userEvent.setup()
    const warnings = ['运行机的 MR 巡检在这个分支上已经有 2 个提交：转了以后 CI 要先交接', '风险目录里的改动留着：Lean 的审查会看它']
    renderDetail(fastDetail({ waits: WAITS.risk }), fastBoard(), {
      [`POST ${ACT_FULL}`]: () => answer({ action: 'full', text: '转成完整流程：Lean intent 261120-pay-17-coupon', intent: '261120-pay-17-coupon', warnings }),
    })
    const card = await screen.findByTestId('fast-acts')
    await user.click(within(card).getByRole('button', { name: '转完整流程' }))
    await user.click(within(card).getByRole('button', { name: '确定转' }))
    const out = await within(card).findByTestId('act-result')
    const said = within(out).getAllByTestId('act-warning')
    expect(said.map((w) => w.textContent)).toEqual(warnings)
    expect(said.every((w) => w.compareDocumentPosition(within(out).getByRole('status')) & Node.DOCUMENT_POSITION_PRECEDING)).toBe(true)
  })

  test('a refusal before anything ran says why in the backend’s words', async () => {
    const user = userEvent.setup()
    const why = '这个需求的会话正在写代码：等它停下再开 MR'
    renderDetail(fastDetail(), fastBoard(), { [`POST ${ACT_MR}`]: () => { throw apiError(409, 'busy', why) } })
    await user.click(within(await screen.findByTestId('fast-acts')).getByRole('button', { name: '开 MR' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(why)
    expect(screen.queryByTestId('act-result')).toBeNull()
  })

  test('转完整流程 asks once, says what changes, and runs only after 确定转; the answer says whether the session was told', async () => {
    const user = userEvent.setup()
    renderDetail(fastDetail(), fastBoard(), {
      [`POST ${ACT_FULL}`]: () => answer({ action: 'full', text: '转成完整流程：Lean intent 261120-pay-17-coupon', intent: '261120-pay-17-coupon', told: true }),
    })
    const card = await screen.findByTestId('fast-acts')
    await user.click(within(card).getByRole('button', { name: '转完整流程' }))
    const ask = within(card).getByRole('group', { name: '确定转完整流程' })
    expect(ask).toHaveTextContent(FULL_ASK)
    await user.click(within(ask).getByRole('button', { name: '取消' }))
    expect(within(card).queryByRole('group', { name: '确定转完整流程' })).toBeNull()
    expect(posts()).toEqual([])
    await user.click(within(card).getByRole('button', { name: '转完整流程' }))
    await user.click(within(card).getByRole('button', { name: '确定转' }))
    const out = await within(card).findByTestId('act-result')
    expect(within(out).getByRole('status')).toHaveTextContent('转成完整流程：Lean intent 261120-pay-17-coupon')
    expect(out).toHaveTextContent('已经跟它说了以后按 Lean 走')
    expect(posts()).toEqual([{ method: 'POST', path: ACT_FULL, body: {} }])
  })

  test('a risk path changed: 转完整流程 leads', async () => {
    renderDetail(fastDetail({ waits: WAITS.risk }))
    const card = await screen.findByTestId('fast-acts')
    expect(within(card).getByRole('heading')).toHaveTextContent('转完整流程')
    expect(within(card).getByRole('button', { name: '转完整流程' })).toHaveClass('is-primary')
    expect(within(card).getByRole('button', { name: '开 MR' })).not.toHaveClass('is-primary')
  })

  test('while the AI works in the session, both wait for it to stop', async () => {
    const d = fastDetail({ waits: WAITS.writing })
    d.sessions = d.sessions.map((s) => ({ ...s, busy: true }))
    renderDetail(d)
    const card = await screen.findByTestId('fast-acts')
    for (const name of ['开 MR', '转完整流程']) expect(within(card).getByRole('button', { name })).toBeDisabled()
    expect(card).toHaveTextContent('AI 正在会话里做事：等它停下再开 MR。')
  })

  test.each([
    ['a repo done only on this machine has no 开 MR', fastDetail({ local_only: true }), ['转完整流程']],
    ['merged: nothing', fastDetail({ state: 'merged', mr: { ...MR, state: 'merged' } }), []],
    ['its MR closed: nothing', fastDetail({ state: 'closed' }), []],
    ['its worktree not on this machine: nothing', fastDetail({ worktree: null }), []],
  ] as [string, RequirementDetail, string[]][])('offered only where it can run: %s', async (_, d, names) => {
    renderDetail(d)
    await screen.findByRole('region', { name: '此刻' })
    const card = screen.queryByTestId('fast-acts')
    expect(card ? within(card).getAllByRole('button').map((b) => b.textContent) : []).toEqual(names)
  })

  test('a Lean run has 交接给运行机 and no 开 MR or 转完整流程', async () => {
    renderDetail(fx.detail(), fx.board())
    expect(await screen.findByTestId('handover')).toBeInTheDocument()
    expect(screen.queryByTestId('fast-acts')).toBeNull()
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
