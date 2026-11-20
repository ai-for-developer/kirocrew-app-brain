import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'
import type { BoardEntry } from '../nav'
import { fx } from '../test/data'
import { C1, C2, C3, C4, COMMITS, FORK, MERGE_STEPS, MR, MR_URL, PIPELINE_URL, READY_STEPS, WAITS, actAnswer, beltOf, fastBoard, fastCommits } from '../test/fast'
import { API, installFixtureRoutes } from '../test/routes'
import { apiCalls, apiError, navigations, type RouteHandler } from '../test/stubs/app-sdk'
import type { CommitOut, CommitsOutput, FileView, ReqBoard } from '../types'
import { FULL_ASK } from '../views/FastActs'
import type { ViewProps } from '../views/props'
import { BoardPage } from './BoardPage'

/** 产出's reads: the run's commits, and a file as asked (its diff says which commit it was read at). */
const outputs = (list: () => CommitsOutput = fastCommits): RouteHandler => (_b, path) => {
  if (path.endsWith('/commits')) return list()
  const q = new URLSearchParams(path.split('?')[1])
  const v: FileView = { path: q.get('path') ?? '', view: q.get('view') as FileView['view'], at: q.get('ref') ?? '', base: q.get('base'), pinned: false,
    lang: 'python', asked: null, binary: false, truncated: false, size: 10,
    text: q.get('view') === 'diff' ? `diff --git a/x b/x\n@@ -1 +1 @@\n-old\n+new ${q.get('ref')?.slice(0, 2)}\n` : 'def apply(code):\n    return code\n' }
  return v
}

function renderBoard(b: ReqBoard | (() => ReqBoard) = fastBoard(), { entry = {}, routes = {}, pollMs = 60_000 }: {
  entry?: Partial<BoardEntry>; routes?: Record<string, RouteHandler>; pollMs?: number
} = {}) {
  installFixtureRoutes({ [`GET ${API}/board/*`]: typeof b === 'function' ? b : () => b, [`GET ${API}/outputs/*`]: outputs(), ...routes })
  const p: ViewProps = { status: fx.status(), go: vi.fn(), replace: vi.fn(), reloadStatus: vi.fn(), setStatus: vi.fn(), pollMs }
  render(<BoardPage entry={{ repo: 'r1', key: 'PAY-17', ...entry }} {...p} />)
  return p
}
const steps = () => screen.findByTestId('br-fast-steps')
const box = (graph: HTMLElement, id: string) => graph.querySelector<SVGGElement>(`g.kt-node[data-node="${id}"]`)!
const merging = () => fastBoard({ key: 'PAY-18', waits: WAITS.merge, mr: MR }, MERGE_STEPS)

describe('运行过程 of a fast run (快速)', () => {
  test('its five steps where the stage graph goes, in the graph’s look; no stage lanes and no 验收', async () => {
    renderBoard()
    const graph = await steps()
    expect(screen.queryByTestId('kt-graph')).toBeNull()
    expect(Array.from(graph.querySelectorAll('g.kt-node')).map((g) => [g.getAttribute('data-node'), g.getAttribute('class')])).toEqual([
      ['code', 'kt-node is-done'], ['verify', 'kt-node is-done'], ['mr', 'kt-node is-awaiting_approval'], ['ci', 'kt-node is-waiting'], ['merge', 'kt-node is-waiting'],
    ])
    expect(Array.from(graph.querySelectorAll('path.kt-edge')).map((e) => [e.getAttribute('data-edge'), e.getAttribute('class')])).toEqual([
      ['code>verify', 'kt-edge is-used'], ['verify>mr', 'kt-edge is-ready'], ['mr>ci', 'kt-edge is-pending'], ['ci>merge', 'kt-edge is-pending'],
    ])
    expect(box(graph, 'mr')).toHaveAccessibleName('开 MR：等你')
    expect(within(graph).getByRole('group')).toHaveAccessibleName('快速需求的路：5 步，2 步做完，1 步等你。')
    expect(screen.queryByTestId('kt-lanes')).toBeNull()
    expect(screen.queryByTestId('br-accept')).toBeNull()
    expect(screen.getByTestId('kt-conveyor')).toBeInTheDocument()
  })

  test('写代码 opens its 产出 (a click or Enter), 自动检查 goes to the check wall', async () => {
    const user = userEvent.setup()
    const p = renderBoard()
    const graph = await steps()
    const conveyor = screen.getByTestId('kt-conveyor')
    const wall = screen.getByTestId('kt-wall')
    conveyor.scrollIntoView = vi.fn()
    wall.scrollIntoView = vi.fn()
    box(graph, 'code').focus()
    await user.keyboard('{Enter}')
    expect(p.replace).toHaveBeenLastCalledWith({ kind: 'board', repo: 'r1', key: 'PAY-17', out: { node: 'code' } })
    await user.click(await screen.findByRole('button', { name: '关掉产出' }))
    box(graph, 'verify').focus()
    await user.keyboard('{Enter}')
    expect(wall.scrollIntoView).toHaveBeenCalledTimes(1)
    expect(conveyor.scrollIntoView).not.toHaveBeenCalled()
  })

  test('its MR open and CI green, people to merge: 开 MR, CI and 合并 open their page on GitLab; 合并 waits on someone else', async () => {
    renderBoard(merging())
    const graph = await steps()
    const href = (id: string) => box(graph, id).closest('a')?.getAttribute('href')
    expect([href('mr'), href('ci'), href('merge')]).toEqual([MR_URL, PIPELINE_URL, MR_URL])
    expect(box(graph, 'merge')).toHaveClass('is-awaiting_approval', 'is-other')
    expect(box(graph, 'merge').closest('a')).toHaveAccessibleName('合并：等别人，等人审、合并 ↗')
    expect(box(graph, 'code').closest('a')).toBeNull()
  })

  test('the counters: 检查通过 is its newest verify on the wall, 快速 says where it is, 这条路走到哪了 counts its steps', async () => {
    renderBoard()
    await steps()
    expect(screen.getByTestId('br-checks-counter')).toHaveAccessibleName('检查通过 3/3')
    expect(screen.getByTestId('br-checks-say')).toHaveTextContent('3 项都过')
    const fast = screen.getByTestId('br-fast-kpi')
    expect(fast).toHaveAccessibleName('快速：代码写好了，等开 MR')
    expect(fast).toHaveTextContent(`${fx.board().belt.filter((b) => b.kind === 'commit').length}次提交`)
    expect(screen.queryByText('AI-DLC Lean')).toBeNull()
    expect(screen.getByTestId('br-steps-counter')).toHaveTextContent('2/5')
    const way = screen.getByTestId('br-steps-counter').closest('.kt-kpi') as HTMLElement
    expect(within(way).getAllByRole('button').map((b) => b.getAttribute('aria-label'))).toEqual(['写代码：做完', '自动检查：做完', '开 MR：等你', 'CI：还没轮到', '合并：还没轮到'])
    expect(way).toHaveTextContent('没有在做的 · 等你')
  })

  test('a verify that failed: 检查通过 says so in its 自动检查 step’s words', async () => {
    const b = fastBoard({ waits: WAITS.stopped }, READY_STEPS.map((s) => (s.id === 'verify' ? { ...s, status: 'failed', text: '没过：pytest（第 2 次）' } : s)))
    b.checks.cells = b.checks.cells.map((c) => (c.id === 'verify-pytest' ? { ...c, status: 'fail' } : c))
    renderBoard(b)
    const graph = await steps()
    expect(screen.getByTestId('br-checks-counter')).toHaveAccessibleName('检查通过 2/3')
    expect(screen.getByTestId('br-checks-say')).toHaveTextContent('没过：pytest（第 2 次）')
    expect(box(graph, 'verify')).toHaveClass('is-failed')
    expect(box(graph, 'verify').querySelector('.kt-node-x')).not.toBeNull()
  })

  test('the check wall has its 自动检查, CI and 风险目录, then the Brain’s', async () => {
    renderBoard()
    await steps()
    const wall = screen.getByTestId('kt-wall')
    expect(within(wall).getAllByRole('group').map((g) => g.getAttribute('aria-label'))).toEqual(['自动检查', 'CI', '风险目录', 'Brain 和仓库'])
  })

  test('打开会话 is this machine’s session for the run, though a fast board has no runs', async () => {
    const user = userEvent.setup()
    const b = fastBoard()
    expect(b.runs).toEqual([])
    renderBoard(b)
    await steps()
    await user.click(screen.getByRole('button', { name: '打开会话' }))
    expect(navigations).toEqual([b.sessions[0].url])
  })
})

describe('产出 of a fast run: the code it wrote, by commit', () => {
  const HERE = { kind: 'board', repo: 'r1', key: 'PAY-17' } as const
  const drawer = () => screen.findByRole('dialog', { name: '写代码的产出' })
  const pills = (d: HTMLElement) => within(d).getAllByTestId('out-step').map((s) => s.textContent)
  const heads = (pane: HTMLElement) => Array.from(pane.querySelectorAll('.kt-flist-h')).map((h) => h.textContent)
  const listed = (pane: HTMLElement) => within(pane).getAllByTestId('out-file').map((f) => f.getAttribute('title'))
  const reads = () => apiCalls.filter((c) => c.path.includes('/outputs/r1/PAY-17/file?')).map((c) => Object.fromEntries(new URLSearchParams(c.path.split('?')[1])))
  const listReads = () => apiCalls.filter((c) => c.path === `${API}/outputs/r1/PAY-17/commits`).length
  const ALL = ['全部改动', '1c1c1c1c优惠码：校验和折扣', '2c2c2c2c优惠码：下单时用上', '3c3c3c3c优惠码：补下单的测试', '4c4c4c4cMR 说明（只改记录）']

  test('写代码 opens it on 全部改动 in place of the current entry: the commits oldest first, the code before the records, the biggest change open', async () => {
    const user = userEvent.setup()
    const p = renderBoard()
    await user.click(box(await steps(), 'code'))
    expect(p.replace).toHaveBeenLastCalledWith({ ...HERE, out: { node: 'code' } })
    const d = await drawer()
    await waitFor(() => expect(pills(d)).toEqual(ALL))
    expect(within(d).getAllByTestId('out-step')[0]).toHaveAttribute('aria-current', 'step')
    const pane = within(d).getByTestId('out-pane')
    expect(within(pane).getByRole('heading', { level: 3 })).toHaveTextContent('全部改动')
    expect(heads(pane)).toEqual(['代码改动', '其他改动'])
    expect(listed(pane)).toEqual(['tests/test_coupon.py', 'src/pay/coupon.py', 'tests/test_order.py', 'src/pay/order.py', 'runs/PAY-17/MR.md'])
    expect(within(pane).getAllByTestId('out-file')[0]).toHaveAttribute('aria-pressed', 'true')
    expect(within(pane).getByTestId('out-at')).toHaveTextContent(/^全部改动 · 到最后一个提交 c4c4c4c$/)
    await waitFor(() => expect(reads().at(-1)).toEqual({ ref: C1, base: FORK, path: 'tests/test_coupon.py', view: 'diff' }))
    expect(await within(pane).findByTestId('out-code')).toHaveTextContent('+new c1')
    expect(p.go).not.toHaveBeenCalled()
  })

  test('a commit on the conveyor opens it on that commit; a step, ‹ › and a file move it in place; Esc closes it; the list is read once', async () => {
    const p = renderBoard()
    await steps()
    const chips = within(screen.getByTestId('kt-conveyor')).getAllByRole('button', { name: /本机/ })
    expect(chips).toHaveLength(4)
    fireEvent.click(chips[1])
    expect(p.replace).toHaveBeenLastCalledWith({ ...HERE, out: { node: 'code', commit: C2.slice(0, 12) } })
    const d = await drawer()
    const pane = await within(d).findByTestId('out-pane')
    await waitFor(() => expect(within(pane).getByRole('heading', { level: 3 })).toHaveTextContent('提交 c2c2c2c'))
    expect(within(d).getAllByTestId('out-step')[2]).toHaveAttribute('aria-current', 'step')
    expect(within(pane).getByTestId('out-scope')).toHaveTextContent('优惠码：下单时用上')
    expect(heads(pane)).toEqual(['代码改动'])
    expect(listed(pane)).toEqual(['src/pay/order.py', 'src/pay/coupon.py'])
    expect(within(pane).getByTestId('out-at')).toHaveTextContent(/^提交 c2c2c2c$/)
    await waitFor(() => expect(reads().at(-1)).toEqual({ ref: C2, base: C1, path: 'src/pay/order.py', view: 'diff' }))

    fireEvent.click(within(d).getAllByTestId('out-step')[3])
    expect(p.replace).toHaveBeenLastCalledWith({ ...HERE, out: { node: 'code', commit: C3.slice(0, 12) } })
    fireEvent.click(within(d).getByRole('button', { name: '下一步 ›' }))
    expect(p.replace).toHaveBeenLastCalledWith({ ...HERE, out: { node: 'code', commit: C4.slice(0, 12) } })
    expect(await within(d).findByText('只改记录')).toBeInTheDocument()
    expect(heads(within(d).getByTestId('out-pane'))).toEqual(['其他改动'])
    expect(within(d).getByRole('button', { name: '下一步 ›' })).toBeDisabled()
    fireEvent.click(within(d).getAllByTestId('out-step')[0])
    expect(p.replace).toHaveBeenLastCalledWith({ ...HERE, out: { node: 'code' } })
    expect(within(d).getByRole('button', { name: '‹ 上一步' })).toBeDisabled()
    fireEvent.click((await within(d).findAllByTestId('out-file'))[3])
    expect(p.replace).toHaveBeenLastCalledWith({ ...HERE, out: { node: 'code', file: 'src/pay/order.py' } })
    await waitFor(() => expect(reads().at(-1)).toEqual({ ref: C2, base: FORK, path: 'src/pay/order.py', view: 'diff' }))
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(p.replace).toHaveBeenLastCalledWith(HERE)
    expect(screen.queryByTestId('out-drawer')).toBeNull()
    expect(listReads()).toBe(1)
    expect(p.go).not.toHaveBeenCalled()
  })

  test('the top bar’s 产出 and 这条路走到哪了’s 写代码 open it on 全部改动 too', async () => {
    const user = userEvent.setup()
    const p = renderBoard()
    await steps()
    await user.click(screen.getByRole('button', { name: '产出' }))
    expect(p.replace).toHaveBeenLastCalledWith({ ...HERE, out: { node: 'code' } })
    await user.click(within(await drawer()).getByRole('button', { name: '关掉产出' }))
    expect(screen.queryByTestId('out-drawer')).toBeNull()
    const way = screen.getByTestId('br-steps-counter').closest('.kt-kpi') as HTMLElement
    await user.click(within(way).getByRole('button', { name: '写代码：做完' }))
    expect(p.replace).toHaveBeenLastCalledWith({ ...HERE, out: { node: 'code' } })
    expect(await drawer()).toBeInTheDocument()
  })

  test('the list is read again when the board has another commit, not on every poll', async () => {
    const fifth: CommitOut = { sha: 'c5'.repeat(20), at: COMMITS[3].at + 300, subject: '优惠码：过期的提示', by: 'person', records_only: false, more: 0,
      files: [{ ...COMMITS[1].files[0], ref: 'c5'.repeat(20), base: C4, added: 3, removed: 1 }] }
    let polls = 0
    const later = () => polls > 2
    renderBoard(() => {
      polls++
      return { ...fastBoard(), belt: beltOf(later() ? [...COMMITS, fifth] : COMMITS) }
    }, { pollMs: 30, entry: { out: { node: 'code' } }, routes: { [`GET ${API}/outputs/*`]: outputs(() => (later() ? { ...fastCommits(), head: fifth.sha, commits: [...COMMITS, fifth] } : fastCommits())) } })
    const d = await drawer()
    await waitFor(() => expect(pills(d)).toEqual([...ALL, '5c5c5c5c优惠码：过期的提示']), { timeout: 3000 })
    const seen = polls
    await waitFor(() => expect(polls).toBeGreaterThan(seen + 3), { timeout: 3000 })
    expect(listReads()).toBe(2)
  })

  test('a list that cannot be read says why; a branch without a commit, and a commit it does not have, say so', async () => {
    renderBoard(fastBoard(), { entry: { out: { node: 'code' } }, routes: { [`GET ${API}/outputs/*`]: () => { throw apiError(502, 'git', '读不到运行分支的提交') } } })
    expect(await within(await drawer()).findByRole('alert')).toHaveTextContent('读不到运行分支的提交')
    cleanup()
    const none: CommitsOutput = { branch: 'ai/alice/PAY-17-coupon', head: null, commits: [], files: [], more: 0, why: '运行分支上还没有提交：AI 还在写' }
    renderBoard(fastBoard({ waits: WAITS.writing }), { entry: { out: { node: 'code' } }, routes: { [`GET ${API}/outputs/*`]: outputs(() => none) } })
    let d = await drawer()
    expect(await within(d).findByTestId('out-none')).toHaveTextContent(/^运行分支上还没有提交：AI 还在写$/)
    expect(pills(d)).toEqual(['全部改动'])
    cleanup()
    renderBoard(fastBoard(), { entry: { out: { node: 'code', commit: 'deadbee' } } })
    d = await drawer()
    expect(await within(d).findByTestId('out-none')).toHaveTextContent('运行分支上没有提交 deadbee')
    expect(within(d).queryAllByTestId('out-step').filter((s) => s.getAttribute('aria-current'))).toEqual([])
  })

  test('in a replay: only the commits made by then, and 全部改动 of the files whose last change was made by then', async () => {
    renderBoard(fastBoard(), { entry: { at: COMMITS[1].at + 60, out: { node: 'code' } } })
    const d = await screen.findByRole('dialog', { name: '写代码的产出' }, { timeout: 5000 })
    expect(within(d).getByTestId('out-replay')).toHaveTextContent('只有那时已经做了的提交')
    await waitFor(() => expect(pills(d)).toEqual(ALL.slice(0, 3)))
    const pane = within(d).getByTestId('out-pane')
    expect(listed(pane)).toEqual(['tests/test_coupon.py', 'src/pay/coupon.py', 'src/pay/order.py'])
    expect(within(pane).getByTestId('out-later')).toHaveTextContent('那以后才改（或又改了）的 2 个文件这里没列')
    expect(within(pane).getByTestId('out-at')).toHaveTextContent(/^全部改动 · 到最后一个提交 c2c2c2c$/)
  })
})

describe('你要做的一步 of a fast run: its step right there', () => {
  const ACT_MR = `${API}/requirements/r1/PAY-17/act/mr`
  const ACT_FULL = `${API}/requirements/r1/PAY-17/act/full`
  const OPENED = actAnswer({ text: '推上去了，开了 MR !12（没有交接标签：人来审、合并）', pushed: true, mr: { iid: 12, url: MR_URL, existing: false }, output: 'pushed ai/alice/PAY-17-coupon\nopened !12' })
  const strip = () => screen.findByRole('region', { name: '你要做的一步' })
  const posts = () => apiCalls.filter((c) => c.method === 'POST')

  test('开 MR runs on one click as on 需求现状: the answer under the strip (the MR, 细节, 收起), the board read again; 去需求现状 → second', async () => {
    const user = userEvent.setup()
    let reads = 0
    const p = renderBoard(() => { reads++; return fastBoard() }, { routes: { [`POST ${ACT_MR}`]: () => OPENED } })
    const s = await strip()
    expect(within(s).getAllByRole('button').map((b) => [b.textContent, b.className])).toEqual([['开 MR', 'kt-btn br-act is-primary'], ['去需求现状 →', 'kt-btn']])
    const before = reads
    await user.click(within(s).getByRole('button', { name: '开 MR' }))
    const out = await screen.findByTestId('act-result')
    expect(within(out).getByRole('status')).toHaveTextContent('推上去了，开了 MR !12（没有交接标签：人来审、合并）')
    expect(within(out).getByRole('link', { name: 'MR !12' })).toHaveAttribute('href', MR_URL)
    expect(out.querySelector('details')).not.toHaveAttribute('open')
    expect(out.querySelector('details')).toHaveTextContent('opened !12')
    expect(posts()).toEqual([{ method: 'POST', path: ACT_MR, body: {} }])
    await waitFor(() => expect(reads).toBeGreaterThan(before))
    expect(p.reloadStatus).toHaveBeenCalled()
    await user.click(within(out).getByRole('button', { name: '收起' }))
    expect(screen.queryByTestId('act-result')).toBeNull()
    expect(p.go).not.toHaveBeenCalled()
  })

  test('the answer stays when the board moves on after it, until 收起', async () => {
    const user = userEvent.setup()
    let opened = false
    renderBoard(() => (opened ? fastBoard({ waits: WAITS.merge, mr: MR }, MERGE_STEPS) : fastBoard()), {
      routes: { [`POST ${ACT_MR}`]: () => { opened = true; return OPENED } },
    })
    await user.click(within(await strip()).getByRole('button', { name: '开 MR' }))
    await waitFor(() => expect(screen.getByTestId('br-next')).toHaveTextContent('此刻不用你做什么'))
    expect(screen.getByTestId('act-result')).toHaveTextContent('推上去了，开了 MR !12')
  })

  test('a risk path changed: 转完整流程 asks once, runs only after 确定转, and says whether the session was told', async () => {
    const user = userEvent.setup()
    renderBoard(fastBoard({ waits: WAITS.risk }), {
      routes: { [`POST ${ACT_FULL}`]: () => actAnswer({ action: 'full', text: '转成完整流程：Lean intent 261120-pay-17-coupon', intent: '261120-pay-17-coupon' }) },
    })
    const s = await strip()
    expect(within(s).getAllByRole('button').map((b) => b.textContent)).toEqual(['转完整流程', '去需求现状 →'])
    await user.click(within(s).getByRole('button', { name: '转完整流程' }))
    const ask = screen.getByRole('group', { name: '确定转完整流程' })
    expect(ask).toHaveTextContent(FULL_ASK)
    await user.click(within(ask).getByRole('button', { name: '取消' }))
    expect(screen.queryByRole('group', { name: '确定转完整流程' })).toBeNull()
    expect(posts()).toEqual([])
    await user.click(within(s).getByRole('button', { name: '转完整流程' }))
    await user.click(screen.getByRole('button', { name: '确定转' }))
    const out = await screen.findByTestId('act-result')
    expect(within(out).getByRole('status')).toHaveTextContent('转成完整流程：Lean intent 261120-pay-17-coupon')
    expect(out).toHaveTextContent('在会话里跟它说一声以后按 Lean 走')
    expect(posts()).toEqual([{ method: 'POST', path: ACT_FULL, body: {} }])
  })

  test('a refusal says why in the backend’s words; while the AI writes, 开 MR waits with why', async () => {
    const user = userEvent.setup()
    const why = '这个需求的会话正在写代码：等它停下再开 MR'
    renderBoard(fastBoard(), { routes: { [`POST ${ACT_MR}`]: () => { throw apiError(409, 'busy', why) } } })
    await user.click(within(await strip()).getByRole('button', { name: '开 MR' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(why)
    expect(screen.queryByTestId('act-result')).toBeNull()
    cleanup()
    const b = fastBoard()
    b.sessions = b.sessions.map((x) => ({ ...x, busy: true }))
    renderBoard(b)
    expect(within(await strip()).getByRole('button', { name: '开 MR' })).toBeDisabled()
    expect(within(screen.getByTestId('act-follow')).getByRole('status')).toHaveTextContent(/^AI 正在会话里做事：等它停下再开 MR。$/)
  })

  test.each([
    ['a repo done only on this machine', () => fastBoard({ local_only: true })],
    ['its worktree not on this machine', () => fastBoard({ worktree: null })],
    ['its step a reply in the session', () => fastBoard({ waits: WAITS.stopped })],
    ['a Lean run', () => fx.board()],
  ] as [string, () => ReqBoard][])('no act where it cannot run, 去需求现状 → alone and first: %s', async (_, b) => {
    renderBoard(b())
    expect(within(await strip()).getAllByRole('button').map((x) => [x.textContent, x.className])).toEqual([['去需求现状 →', 'kt-btn is-primary']])
    expect(screen.queryByTestId('act-follow')).toBeNull()
  })
})

