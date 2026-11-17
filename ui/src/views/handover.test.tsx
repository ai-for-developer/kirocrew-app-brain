import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { fx } from '../test/data'
import { API, installFixtureRoutes } from '../test/routes'
import { apiCalls, apiError } from '../test/stubs/app-sdk'
import type { HandoffResult, RequirementDetail } from '../types'
import { DetailView } from './Detail'

// 交接给运行机 on 需求详情 (E2E-10): the fixture is PAY-3, alice's run in its worktree on this machine, in a repo with a
// remote, its session idle

const POST = `${API}/requirements/r1/PAY-3/handoff`
const MR = 'https://gitlab.example.com/pay/shop-pay/-/merge_requests/44'
const HANDED: HandoffResult = {
  ok: true, state: 'handed', text: '交出去了：MR !44 加上了交接标签', todo: '等运行机接着做：它有问题会发在 MR 上，到时在 MR 上回答',
  mr: { iid: 44, url: MR },
  warnings: [{ level: 'WARN', text: '需求材料只存在本机：brain-intake 没有远端', todo: '运行机拿不到材料，桥会拒收这次交接：给本机的 brain-intake 加上远端，再交接一次',
    fix: null, repo: null, raw: 'intake store has no origin: packages are committed locally only' }],
  raw: "WARN: intake store has no origin: packages are committed locally only\nHanded off: MR !44 labelled 'ai-handoff' at 0123456789ab.", code: 0,
}
const REFUSED: HandoffResult = {
  ok: false, state: 'refused', text: '没交出去：交接记录还没写好（没写下一步）',
  todo: '在会话里让 AI 写好交接记录（做到哪了、运行机接着做什么）并提交，再点一次「交接给运行机」', mr: null, warnings: [],
  raw: "brain: hand-over note not ready (aidlc-docs/261107-refund-export/aidlc-state.md): Session notes have no '### Next' items", code: 2,
}

function renderDetail() {
  const reloadStatus = vi.fn()
  render(<DetailView entry={{ repo: 'r1', key: 'PAY-3', from: 'overview' }} status={fx.status()} go={vi.fn()} replace={vi.fn()}
    reloadStatus={reloadStatus} setStatus={vi.fn()} pollMs={60_000} />)
  return { reloadStatus }
}

const withDetail = (change: (d: RequirementDetail) => void, more: Parameters<typeof installFixtureRoutes>[0] = {}) => {
  const d = fx.detail()
  change(d)
  installFixtureRoutes({ [`GET ${API}/requirements/*`]: () => d, ...more })
  return d
}

const card = () => screen.findByRole('region', { name: '交接' })
const posts = () => apiCalls.filter((c) => c.method === 'POST')

describe('交接给运行机', () => {
  beforeEach(() => installFixtureRoutes())

  test('it first says what will happen; 确定交接 posts once, shows that it is at it, then what happened, 做什么, the MR, the warnings, brain-kit’s words under 细节', async () => {
    const user = userEvent.setup()
    let done: (r: HandoffResult) => void = () => {}
    withDetail(() => {}, { [`POST ${POST}`]: () => new Promise<HandoffResult>((resolve) => { done = resolve }) })
    const { reloadStatus } = renderDetail()
    const box = await card()
    await user.click(within(box).getByRole('button', { name: '交接给运行机' }))
    const ask = within(box).getByRole('group', { name: '确定交接' })
    expect(ask).toHaveTextContent('在 MR 上加交接标签。之后运行机接着做')     // one sentence after the other, no stray space
    for (const said of ['提交交接记录', 'ai/alice/PAY-3-refund-export', '推到远端', '在 MR 上加交接标签', '运行机接着做', '在 MR 上回答']) {
      expect(ask).toHaveTextContent(said)
    }
    expect(posts()).toEqual([])
    await user.click(within(ask).getByRole('button', { name: '确定交接' }))
    expect(await within(box).findByText(/正在交接/)).toBeInTheDocument()
    expect(within(box).queryByRole('button', { name: '交接给运行机' })).toBeNull()
    done(HANDED)
    const out = await within(box).findByTestId('handover-result')
    expect(out).toHaveAttribute('data-state', 'handed')
    expect(within(out).getByRole('status')).toHaveTextContent('交出去了：MR !44 加上了交接标签')
    expect(out).toHaveTextContent('做什么等运行机接着做')
    expect(within(out).getByRole('link', { name: /看 MR !44/ })).toHaveAttribute('href', MR)
    expect(within(out).getByText('需求材料只存在本机：brain-intake 没有远端')).toBeInTheDocument()
    expect(within(out).queryByRole('button', { name: /重装|补上|换成/ })).toBeNull()
    const raw = within(out).getByText('细节：brain-kit 的原话').closest('details')!
    expect(raw).not.toHaveAttribute('open')
    expect(raw).toHaveTextContent("Handed off: MR !44 labelled 'ai-handoff'")
    expect(posts()).toEqual([{ method: 'POST', path: POST, body: {} }])
    expect(reloadStatus).toHaveBeenCalled()
  })

  test('the answer stays when the page moves on to 已交接 (no button any more), until 收起', async () => {
    const user = userEvent.setup()
    const d = withDetail(() => {}, { [`POST ${POST}`]: () => {
      d.row.state = 'handed_over'
      d.row.state_label = '已交接'
      d.run!.handoff = { requested_at: '2026-11-08T10:00:00+08:00', by: 'alice' }
      d.row.waits = { ...d.row.waits, who: 'bridge', text: '等桥核对交接', line: null, do: 'none', where: null }
      return HANDED
    } })
    renderDetail()
    await user.click(within(await card()).getByRole('button', { name: '交接给运行机' }))
    await user.click(screen.getByRole('button', { name: '确定交接' }))
    const out = await screen.findByTestId('handover-result')
    await waitFor(() => expect(screen.getByRole('region', { name: '此刻' })).toHaveTextContent('等桥核对交接'))
    expect(out).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '交接给运行机' })).toBeNull()
    await user.click(within(out).getByRole('button', { name: '收起' }))
    expect(screen.queryByRole('region', { name: '交接' })).toBeNull()
  })

  // what the backend answered and the row it read next, after a push that failed once the hand-over commit was made,
  // and after one stopped at the time limit (tests/test_handover.py, a real brain handoff)
  const NOT_PUSHED = '交接提交了，但没推上去：什么也没交出去。弄好以后再点「交接给运行机」'
  test.each<[string, HandoffResult]>([
    ['a failed push', { ok: false, state: 'committed', text: '交接记录提交了，但没推上去：推送失败',
      todo: '看细节里 git 的原话，弄好以后再点一次「交接给运行机」', mr: null, warnings: [], raw: 'error: failed to push some refs', code: 2 }],
    ['a push stopped at the time limit', { ok: false, state: 'timeout', text: '交接没做完：超过 900 秒，停掉了',
      todo: '看一眼 MR 上有没有交接标签：没有就再点一次「交接给运行机」（点两次没关系）', mr: null, warnings: [], raw: '', code: 124 }],
  ])('after %s nothing went out: the page says so and 交接给运行机 is there for the next click, also after 收起', async (_, answer) => {
    const user = userEvent.setup()
    const d = withDetail(() => {}, { [`POST ${POST}`]: () => {
      d.row.state = 'local'
      d.row.state_label = '本地开发中'
      d.run!.handoff = { requested_at: '2026-11-08T10:00:00+08:00', by: 'alice' }
      d.row.waits = { ...d.row.waits, who: 'you', text: NOT_PUSHED, line: null, do: 'push_handoff', where: 'app' }
      d.sessions = []
      d.live_slot = null
      return answer
    } })
    renderDetail()
    await user.click(within(await card()).getByRole('button', { name: '交接给运行机' }))
    await user.click(screen.getByRole('button', { name: '确定交接' }))
    const out = await screen.findByTestId('handover-result')
    expect(out).toHaveTextContent(answer.text)
    await waitFor(() => expect(screen.getByRole('region', { name: '此刻' })).toHaveTextContent(NOT_PUSHED))
    expect(screen.getByRole('region', { name: '此刻' })).not.toHaveTextContent('等桥')
    // the backend opens no session for a run with a hand-over commit (service.open_session): none is offered
    expect(screen.queryByRole('button', { name: '开会话' })).toBeNull()
    expect(screen.queryByTestId('reopen')).toBeNull()
    await user.click(within(out).getByRole('button', { name: '收起' }))
    const again = within(await card()).getByRole('button', { name: '交接给运行机' })
    expect(again).toBeEnabled()
    await user.click(again)
    await user.click(screen.getByRole('button', { name: '确定交接' }))
    await waitFor(() => expect(posts()).toHaveLength(2))
  })

  test('a hand-over commit whose push failed is not handed over on any card: approvals in the session, no 交接以后 notes', async () => {
    withDetail((d) => {
      d.row.state = 'local'
      d.row.state_label = '本地开发中'
      d.run!.handoff = { requested_at: '2026-11-08T10:00:00+08:00', by: 'alice' }
      d.row.waits = { ...d.row.waits, who: 'you', text: NOT_PUSHED, line: null, do: 'push_handoff', where: 'app' }
      d.timeline.missing = ['桥的账本：这台机器上读不到，交接以后桥的核对和转的问题看不全']
    })
    renderDetail()
    const lean = await screen.findByRole('region', { name: 'Lean 进度' })
    expect(lean).toHaveTextContent('审批的那一行由你在会话里自己打')
    expect(lean).not.toHaveTextContent('交接以后')
    expect(screen.getByTestId('br-detail')).not.toHaveTextContent('交接以后')
    expect(within(await card()).getByRole('button', { name: '交接给运行机' })).toBeEnabled()
  })

  test('a refusal says why and what to do; the button is there again for after the fix', async () => {
    const user = userEvent.setup()
    withDetail(() => {}, { [`POST ${POST}`]: () => REFUSED })
    renderDetail()
    const box = await card()
    await user.click(within(box).getByRole('button', { name: '交接给运行机' }))
    await user.click(within(box).getByRole('button', { name: '确定交接' }))
    const out = await within(box).findByTestId('handover-result')
    expect(out).toHaveAttribute('data-state', 'refused')
    expect(out).toHaveTextContent('没交出去：交接记录还没写好（没写下一步）')
    expect(out).toHaveTextContent('做什么在会话里让 AI 写好交接记录')
    expect(within(out).queryByRole('link')).toBeNull()
    expect(within(box).getByRole('button', { name: '交接给运行机' })).toBeEnabled()
  })

  test('the app’s own refusal is said in its words, never as English or a code alone', async () => {
    const user = userEvent.setup()
    withDetail(() => {}, { [`POST ${POST}`]: () => { throw apiError(403, 'not_owner', '只有需求负责人本人能交接（按你在这个仓库的名字认人，在「设置」的「仓库」里填）') } })
    renderDetail()
    const box = await card()
    await user.click(within(box).getByRole('button', { name: '交接给运行机' }))
    await user.click(within(box).getByRole('button', { name: '确定交接' }))
    expect(await within(box).findByRole('alert')).toHaveTextContent('只有需求负责人本人能交接（按你在这个仓库的名字认人，在「设置」的「仓库」里填）（403）')
  })

  test('while the AI is at it in the session, the button waits and says why', async () => {
    withDetail((d) => { d.sessions[0].busy = true })
    renderDetail()
    const box = await card()
    expect(within(box).getByRole('button', { name: '交接给运行机' })).toBeDisabled()
    expect(box).toHaveTextContent('AI 正在会话里做事：等它停下再交接')
  })

  test.each([
    ['handed over, pushed without its label', (d: RequirementDetail) => {
      d.row.state = 'handed_over'
      d.run!.handoff = { requested_at: null, by: 'alice' }
      d.row.waits = { ...d.row.waits, who: 'you', do: 'label_handoff', line: null, where: 'session' }
    }],
    ['refused by the bridge', (d: RequirementDetail) => {
      d.row.state = 'rejected'
      d.run!.handoff = { requested_at: null, by: 'alice' }
      d.row.waits = { ...d.row.waits, who: 'you', do: 'redo_handoff', line: null, where: 'session' }
    }],
  ])('offered again: %s', async (_name, change) => {
    withDetail(change)
    renderDetail()
    expect(within(await card()).getByRole('button', { name: '交接给运行机' })).toBeEnabled()
  })

  test.each([
    ['a repo done only on this machine', (d: RequirementDetail) => { d.row.local_only = true }],
    ['no worktree on this machine', (d: RequirementDetail) => { d.row.worktree = null }],
    ['handed over and waiting for the bridge', (d: RequirementDetail) => {
      d.row.state = 'handed_over'
      d.run!.handoff = { requested_at: null, by: 'alice' }
      d.row.waits = { ...d.row.waits, who: 'bridge', do: 'none', line: null, where: null }
    }],
    ['a small requirement (its MR goes into the big one’s branch)', (d: RequirementDetail) => {
      d.row.family = {
        role: 'child', parent: 'PAY-1', parent_title: '退款大需求', branch: 'ai/alice/PAY-1-big', unit: 'web', state: 'in_progress',
        state_label: 'alice 在做', owner: 'alice', depends_on: [], blocked_by: [], paths: [], contracts: [], children: [], progress: null,
        family_state: null, family_label: '', released: false,
        acts: { split: null, claim: null, release: null, start: null, start_on: null, sync: null }, source: 'brainkit', at: null,
      }
    }],
  ])('not offered: %s', async (_name, change) => {
    withDetail(change)
    renderDetail()
    await screen.findByRole('region', { name: '需求' })
    expect(screen.queryByRole('region', { name: '交接' })).toBeNull()
  })
})
