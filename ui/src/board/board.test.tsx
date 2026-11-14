import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { paths } from '../api'
import { fx } from '../test/data'
import { API, fixtureRoutes, installFixtureRoutes } from '../test/routes'
import { apiCalls, apiError, navigations, setApiRoutes } from '../test/stubs/app-sdk'
import type { BoardEntry } from '../nav'
import { pay16, round, run } from '../test/stageBoards'
import type { ChildStartResult, FamilyActs, NodeId, ReqBoard, ReqFamily, RunKey, SplitResult } from '../types'
import { TYPE_IT_YOURSELF } from '../ui'
import type { ViewProps } from '../views/props'
import { optionText } from '../views/ReqSwitch'
import { BoardPage } from './BoardPage'
import { tickLabel } from './Lanes'
import { CELL_GROUPS, actorMeta, fmtCredits, fmtSpan, fmtWhen } from './format'

const PAY3: BoardEntry = { repo: 'r1', key: 'PAY-3' }
const PAY2: BoardEntry = { repo: 'r1', key: 'PAY-2' }

function props(over: Partial<ViewProps> = {}): ViewProps {
  return { status: fx.status(), go: vi.fn(), replace: vi.fn(), reloadStatus: vi.fn(), setStatus: vi.fn(), pollMs: 60_000, ...over }
}

function renderBoard(entry: BoardEntry = PAY3, over: Partial<ViewProps> = {}, onBack?: () => void) {
  const p = props(over)
  render(<BoardPage entry={entry} onBack={onBack} {...p} />)
  return p
}

/** the board answers with this snapshot (a fresh copy per request) */
const boardRoute = (make: () => ReqBoard) => ({ [`GET ${API}/board/*`]: () => make() })
const boardCalls = (entry: BoardEntry = PAY3) => apiCalls.filter((c) => c.method === 'GET' && c.path === paths.board(entry.repo, entry.key))
const region = (name: string) => screen.getByRole('region', { name })
const loaded = () => screen.findByTestId('kt-graph')
const crumb = () => within(screen.getByRole('navigation', { name: '位置' }))
/** a stage's box on the graph (null: it has none) */
const stageBox = (graph: HTMLElement, id: string) => graph.querySelector<SVGGElement>(`g.kt-node[data-node="${id}"]`)!
/** a stage's lane (null: it has none) */
const lane = (id: string) => screen.getAllByTestId('kt-lane').find((l) => l.dataset.node === id) as HTMLElement
/** the stages with a lane, in order */
const laneIds = () => screen.getAllByTestId('kt-lane').map((l) => l.dataset.node)
/** what each SVG row says (its <title> left out) */
const said = (els: Iterable<Element>) => Array.from(els).map((e) => e.querySelector('text')?.textContent ?? '')

/** PAY-3 with refund-api's code generation plan approved at *at*: no approval asks any more. */
function planApproved(b: ReqBoard, at = b.at - 60): ReqBoard {
  b.nodes = b.nodes.map((n) => (n.id !== 'code-generation/refund-api' ? n : {
    ...n, status: 'merged' as const, doing: '你批了', since: at,
    gates: n.gates.map((g) => ({ ...g, state: 'approved' as const, at, who: null, by: '你', line: null })),
    rounds: n.rounds.map((r) => ({ ...r, ended: at, outcome: { ...r.outcome, kind: 'approved' as const, at, gate: 'PLAN' as const, by: '你' } })),
  }))
  return b
}

describe('需求看板', () => {
  test('the header: the requirement’s title, its key, state and branch; the crumb names the key', async () => {
    installFixtureRoutes()
    renderBoard()
    await loaded()
    const r = fx.board().row
    expect(screen.getByRole('button', { name: r.title })).toHaveClass('kt-run-title')
    expect(screen.getByTestId('br-board-key')).toHaveTextContent(r.key)
    expect(screen.getByText(r.state_label)).toBeInTheDocument()
    expect(screen.getByText(`${r.branch} ← main · ${r.owner}`)).toBeInTheDocument()
    expect(crumb().getByRole('button', { name: 'PAY-3' })).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: '位置' })).toHaveTextContent(/需求看板$/)
    expect(screen.getByRole('button', { name: '跟随' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.queryByText('此刻有人在做')).toBeNull() // live is false
    expect(apiCalls.find((c) => !c.path.startsWith(`${API}/overview`))).toMatchObject({ method: 'GET', path: `${API}/board/r1/PAY-3` })
  })

  test('the KPIs: checks passed, Lean’s stages and the approvals, a person’s acts, the steps of the way, credits', async () => {
    installFixtureRoutes()
    renderBoard()
    await loaded()
    const b = fx.board()
    const kpis = within(screen.getByRole('group', { name: '指标' }))
    expect(kpis.getByTestId('br-checks-counter')).toHaveAccessibleName(`检查通过 ${b.checks.passed}/${b.checks.total}`)
    expect(kpis.getByTestId('br-checks-counter')).toHaveTextContent('2/4')
    expect(kpis.getByTestId('br-checks-say')).toHaveTextContent('1 项没过')
    const lean = kpis.getByText('AI-DLC Lean').parentElement as HTMLElement
    expect(lean).toHaveAccessibleName('AI-DLC Lean：做完 4 · 跳过 4 · 在做：代码生成 · 构思整段不需要')   // the stage it works on is 在做, not 下一步
    expect(within(lean).getByTestId('br-lean-counter')).toHaveTextContent('4做完 · 跳过 4')
    expect(lean).toHaveTextContent('在做：代码生成')
    expect(lean).toHaveTextContent('批准 3退回 0拦下 1')
    // the person's acts: 3 approvals, and the answer they decided (D-3) counts as a judgment point too
    const people = kpis.getByText('人介入').parentElement as HTMLElement
    expect(people).toHaveTextContent('4次')
    expect(people).toHaveTextContent('判断点上 4（审批 3 · 回答 1） · 判断点之外 0')
    // 11 stages are on this way, 6 of them through; the question answerable here, the PLAN approval and the tool call wait
    const on = b.nodes.filter((n) => n.status !== 'skipped')
    expect(kpis.getByTestId('br-steps-counter')).toHaveTextContent(`6/${on.length}`)
    expect(kpis.getByText('· 3 个等你', { exact: false })).toBeInTheDocument()
    expect(kpis.getAllByRole('button').map((x) => x.getAttribute('aria-label')))
      .toEqual(on.map((n) => `${n.unit ? `${n.title} · ${n.unit}` : n.title}：${n.status === 'awaiting_approval' ? '等你' : actorMeta(n.status).label}`))
    const credits = kpis.getByText('credits').parentElement as HTMLElement
    expect(credits).toHaveTextContent(fmtCredits(b.credits.total))
    expect(credits).toHaveTextContent(`${b.sessions.length} 个会话合计`)
  })

  test('你要做的一步: every thing that waits for the person, one row each, the means beside it; nothing is sent', async () => {
    installFixtureRoutes()
    renderBoard()
    await loaded()
    const b = fx.board()
    const strip = region('你要做的一步')
    expect(strip).toHaveTextContent('3 件事等你')
    const steps = within(strip).getAllByTestId('br-next-step')
    // the question answerable right here comes first, then 验收's step (the line to type), then the tool call
    expect(steps.map((x) => x.dataset.do)).toEqual(['answer', 'approve', 'tool'])
    expect(steps[0]).toHaveTextContent(b.questions[0].question)
    expect(steps[1]).toHaveTextContent(b.accept.step.text)
    const line = within(steps[1]).getByTestId('type-line')
    expect(within(line).getByText(b.accept.step.line as string)).toHaveClass('br-mono')
    expect(line).toHaveAttribute('title', TYPE_IT_YOURSELF)
    fireEvent.click(within(line).getByRole('button', { name: '复制那一行' }))
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith('approve PLAN 5d1e7a90'))
    expect(await within(line).findByText('已复制')).toBeInTheDocument()
    fireEvent.click(within(line).getByRole('button', { name: '打开会话' }))
    expect(steps[2]).toHaveTextContent('会话里有一个工具调用等你批准')
    fireEvent.click(within(steps[2]).getByRole('button', { name: '去批准工具调用' }))
    expect(navigations).toEqual([b.accept.step.session_url, b.runs[0].sessions[0].url])
    expect(apiCalls.filter((c) => c.method !== 'GET')).toEqual([])
  })

  test('before acceptance the step strip is above the graph and 验收 under the lanes; at acceptance 验收 comes up', async () => {
    installFixtureRoutes()
    renderBoard()
    await loaded()
    const order = () => Array.from(screen.getByTestId('br-board').querySelectorAll('[data-testid="br-next"], [data-testid="kt-graph"], [data-testid="kt-lanes"], #br-accept')).map((e) => e.getAttribute('data-testid'))
    expect(order()).toEqual(['br-next', 'kt-graph', 'kt-lanes', 'br-accept'])
    cleanup()
    const b = fx.board()
    b.accept = { ...b.accept, state: 'waiting', step: { text: '等你验收：在 MR 上回答 D-3', who: '你', line: null, mr_url: 'https://gitlab.example.com/shop/shop-pay/-/merge_requests/44', session_url: null } }
    b.row.waits = { ...b.row.waits, do: 'accept', where: 'mr', line: null }
    b.nodes = b.nodes.map((n) => (n.status !== 'awaiting_approval' ? n : { ...n, status: 'done', gates: n.gates.map((g) => ({ ...g, line: null })) }))
    b.runs = b.runs.map((r) => ({ ...r, now: { ...r.now, status: 'done', who: null, do: null } }))
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    renderBoard()
    await loaded()
    expect(order()).toEqual(['br-next', 'br-accept', 'kt-graph', 'kt-lanes'])
    expect(within(region('你要做的一步')).getByRole('link', { name: '去 MR 上验收 ↗' })).toHaveAttribute('href', b.accept.step.mr_url)
  })

  test('验收: the requirement, its 变更说明, what the checks say, who decided (in words), what it cost', async () => {
    installFixtureRoutes()
    renderBoard()
    await loaded()
    const b = fx.board()
    const card = region('验收')
    expect(card).toHaveTextContent('还没到验收')
    expect(card).toHaveTextContent(`需求${b.row.key} ${b.row.title}`)
    expect(within(card).getByTestId('accept-first')).toHaveTextContent(b.mr_desc.first as string)
    // the same sentence as the counter's, not a second count
    expect(within(card).getByTestId('accept-checks')).toHaveTextContent('1 项没过')
    expect(card).toHaveTextContent('人 1照 Brain 2没归类（「谁定的」没按规定写） 1')
    expect(card).not.toHaveTextContent('Who')
    expect(card).toHaveTextContent('金额列带币种，单独一列 currency · 一位组员')
    expect(card).not.toHaveTextContent('P-1A2B')
    expect(card).toHaveTextContent(`花了多少${fmtCredits(b.credits.total)} credits · ${b.sessions.length} 个会话`)
    expect(within(card).queryByTestId('accept-step')).toBeNull()
  })

  test('验收: 复制 beside 变更说明 copies the whole text', async () => {
    installFixtureRoutes()
    renderBoard()
    await loaded()
    const desc = within(region('验收')).getByText('变更说明', { exact: false })
    fireEvent.click(within(desc).getByRole('button', { name: '复制' }))
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith(fx.board().mr_desc.text))
    expect(await within(region('验收')).findByText('已复制')).toBeInTheDocument()
  })

  test('the graph: a box per stage on the way, none for a skipped one; its state in data attributes and in words; the approval asked of you has its chip', async () => {
    installFixtureRoutes()
    renderBoard()
    const graph = await loaded()
    const b = fx.board()
    expect(Array.from(graph.querySelectorAll<SVGGElement>('g.kt-node')).map((n) => [n.dataset.node, n.dataset.status])).toEqual([
      // a finished stage is 完成 (merged) once a later stage's round began: everything before refund-api's 代码生成
      ['requirements', 'merged'], ['application-design', 'merged'], ['units', 'merged'], ['functional-design/refund-export', 'merged'],
      ['functional-design/refund-api', 'merged'], ['code-generation/refund-export', 'merged'], ['code-generation/refund-api', 'awaiting_approval'],
      ['build-and-test', 'waiting'], ['review', 'waiting'], ['release', 'waiting'], ['reflect', 'waiting'],
    ])
    const plan = stageBox(graph, 'code-generation/refund-api')
    expect(plan).toHaveAttribute('role', 'button')
    expect(plan).toHaveAttribute('aria-pressed', 'false')
    expect(plan).toHaveAccessibleName('代码生成（refund-api 的代码生成计划）：等你，本机会话，等你批代码生成计划')
    expect(plan).toHaveTextContent('代码生成3.5')
    expect(plan.querySelector('.kt-node-sub')).toHaveTextContent(/^✋ 本机 · 等你$/)
    expect(said(plan.querySelectorAll('.kt-node-badge'))).toEqual(['等你'])
    expect(plan.querySelector('.kt-node-halo')).not.toBeNull()
    expect(stageBox(graph, 'requirements').querySelector('.kt-node-sub')).toHaveTextContent(/^✓ 本机 · \d+:\d\d$/)
    expect(said(stageBox(graph, 'requirements').querySelectorAll('.kt-node-badge'))).toEqual(['✓ 你批了'])
    expect(stageBox(graph, 'build-and-test').querySelector('.kt-node-sub')).toHaveTextContent(/^等代码生成$/)
    // the approval asked of you: one chip under its box; it brings the step strip, where the line to type is
    const scroll = vi.mocked(HTMLElement.prototype.scrollIntoView)
    scroll.mockClear()
    expect(said(within(graph).getAllByTestId('kt-gate-pill'))).toEqual(['等你批 · 代码生成计划'])
    fireEvent.click(within(graph).getByRole('button', { name: '代码生成：等你批 · 代码生成计划' }))
    expect(scroll.mock.contexts).toContain(screen.getByTestId('br-next'))
    expect(plan).toHaveAttribute('aria-pressed', 'true')
    // the dashed line from 本机会话's row to the box it waits at, magenta: it waits on you
    const cmds = within(graph).getAllByTestId('kt-cmd')
    expect(cmds.map((c) => c.getAttribute('data-run'))).toEqual(['local'])
    expect(cmds[0].querySelector('path')).toHaveClass('is-awaiting_approval')
    expect(cmds[0]).toHaveTextContent('本机 → 代码生成：等你')
    const roster = within(graph).getByTestId('kt-roster')
    expect(roster).toHaveTextContent('这次谁在做 · 1 次运行')
    expect(said(within(roster).getAllByTestId('kt-roster-row'))).toEqual(['本机会话 · 等你'])
    expect(within(roster).getAllByTestId('kt-roster-row')[0]).toHaveClass('is-mine')
    expect(within(graph).getByTestId('kt-station')).toHaveAccessibleName('检查 2/4')
    expect(within(graph).getByTestId('kt-orch')).toHaveTextContent(`你 · ${b.person.owner}`)
    expect(within(graph).getByRole('note', { name: '图例' })).toHaveTextContent('等你')
    // nothing here waits on anyone else: the blue key keeps its general word
    expect(within(graph).getByTestId('kt-legend-other')).toHaveTextContent('等别人')
  })
  test('the legend names whom the blue waits are on, as the lanes’ pills and spans do (等产品, 等 carol), not 等别人', async () => {
    installFixtureRoutes(boardRoute(() => fx.boardMerged()))
    renderBoard(PAY2)
    let graph = await loaded()
    expect(within(graph).getByTestId('kt-legend-other')).toHaveTextContent(/^等产品$/)
    expect(region('每一步的泳道').querySelector('.kt-track-span.is-other')?.getAttribute('title')).toMatch(/^等产品：/)
    cleanup()
    // carol's requirement waiting on her, read by alice
    const b = fx.board()
    Object.assign(b.row, { owner: 'carol', waits: { ...b.row.waits, who: 'people', line: null } })
    b.person = { ...b.person, owner: 'carol', me: false, you: false }
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    renderBoard()
    graph = await loaded()
    expect(within(graph).getByTestId('kt-legend-other')).toHaveTextContent('等 carol')
    expect(within(graph).getByTestId('kt-legend-other')).not.toHaveTextContent('等别人')
  })

  test('a box’s card on hover: what it hands in, its rounds and who did them, the line to type; 打开会话 opens its run’s session', async () => {
    installFixtureRoutes()
    renderBoard()
    const graph = await loaded()
    fireEvent.mouseEnter(stageBox(graph, 'code-generation/refund-api'))
    const card = await screen.findByRole('dialog', { name: '代码生成做了什么' })
    expect(card.querySelector('.kt-ncard-head')).toHaveTextContent('代码生成 · refund-api等你')
    expect(card).toHaveTextContent('交什么 refund-api 的代码生成计划')
    expect(card).toHaveTextContent('等你批代码生成计划')
    expect(within(card).getAllByRole('listitem').map((li) => li.textContent)).toEqual([expect.stringMatching(/^第 1 轮本机 · (\d\d-\d\d )?\d\d:\d\d · 等你批$/)])
    expect(within(card).getByText('approve PLAN 5d1e7a90').tagName).toBe('CODE')
    fireEvent.click(within(card).getByRole('button', { name: '打开会话' }))
    expect(navigations).toEqual(['/chat?sid=brain-PAY-3-7f3a'])
    fireEvent.mouseLeave(stageBox(graph, 'code-generation/refund-api'))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '代码生成做了什么' })).toBeNull())
    // a stage not reached yet: what it hands in and what it waits for; no round, nothing to open
    fireEvent.mouseEnter(stageBox(graph, 'build-and-test'))
    const next = await screen.findByRole('dialog', { name: '构建测试做了什么' })
    expect(next).toHaveTextContent('交什么 lean verify')
    expect(next).toHaveTextContent('等代码生成')
    expect(within(next).queryByRole('listitem')).toBeNull()
    expect(within(next).queryByRole('button', { name: '打开会话' })).toBeNull()
  })
  test('a click on a box looks at that stage everywhere: the crumb, the focus bar, the lanes and the feed; again or Esc lets go; a lane’s avatar does the same', async () => {
    installFixtureRoutes()
    renderBoard()
    const graph = await loaded()
    const feed = () => Array.from(screen.getByTestId('kt-feed').querySelectorAll('li[data-seq]')).map((li) => Number(li.getAttribute('data-seq')))
    const all = feed()
    const node = stageBox(graph, 'application-design')
    fireEvent.click(node)
    expect(node).toHaveAttribute('aria-pressed', 'true')
    expect(stageBox(graph, 'requirements')).toHaveClass('is-dim')
    expect(screen.getByRole('navigation', { name: '位置' })).toHaveTextContent(/需求看板 · 应用设计$/)
    expect(screen.getByTestId('kt-focusbar')).toHaveTextContent('应用设计')
    // every part of the page looks at that stage: its lane stands out, the others dim, the feed keeps its events
    expect(lane('application-design')).toHaveClass('is-focus')
    expect(lane('requirements')).toHaveClass('is-dim')
    expect(feed()).toEqual(fx.board().events.filter((e) => e.node === 'application-design').map((e) => e.seq).reverse())
    expect(feed().length).toBeLessThan(all.length)
    fireEvent.click(node)
    expect(node).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(node)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(node).toHaveAttribute('aria-pressed', 'false')
    expect(screen.queryByTestId('kt-focusbar')).toBeNull()
    expect(feed()).toEqual(all)
    // a lane's avatar: the same focus, so its box is looked at too
    fireEvent.click(within(lane('requirements')).getByRole('button', { name: '只看需求分析' }))
    expect(lane('requirements')).toHaveClass('is-focus')
    expect(lane('application-design')).toHaveClass('is-dim')
    expect(stageBox(graph, 'requirements')).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('navigation', { name: '位置' })).toHaveTextContent(/需求看板 · 需求分析$/)
    expect(feed()).toEqual(fx.board().events.filter((e) => e.node === 'requirements').map((e) => e.seq).reverse())
    fireEvent.click(within(screen.getByTestId('kt-focusbar')).getByRole('button', { name: '看全部' }))
    expect(lane('requirements')).not.toHaveClass('is-focus')
  })
  test('the lanes: one per stage a run did, who did it, what it waits for or handed in, the line to type there', async () => {
    installFixtureRoutes()
    renderBoard()
    await loaded()
    const b = fx.board()
    const lanes = within(region('每一步的泳道'))
    const withRound = b.nodes.filter((n) => n.status !== 'skipped' && n.rounds.length > 0)
    const rows = lanes.getAllByTestId('kt-lane')
    expect(rows.map((l) => [l.dataset.node, l.dataset.status])).toEqual(withRound.map((n) => [n.id, n.status]))
    // a stage with no round yet (构建测试, 审查, 发布, 回写 Brain) has no lane: the graph draws it dashed
    expect(laneIds()).not.toContain('build-and-test')
    const plan = lane('code-generation/refund-api')
    expect(plan).toHaveTextContent('代码生成 · refund-api')
    expect(plan).toHaveTextContent('✋等你批代码生成计划')
    expect(plan).toHaveTextContent('本机会话')   // who did it
    expect(within(within(plan).getByTestId('type-line')).getByText('approve PLAN 5d1e7a90')).toBeInTheDocument()
    // one approval covering two stages is typed once: in the last lane it covers, as its chip stands under the last box
    expect(screen.getAllByTestId('type-line')).toHaveLength(2)   // this one and 验收's step
    expect(within(lane('code-generation/refund-export')).queryByTestId('type-line')).toBeNull()
    expect(within(plan).getByRole('img')).toHaveAccessibleName(/^代码生成 · refund-api的时间线：/)
    // one that is through says what it handed in (its own note of the last round) and how long it took
    const req = lane('requirements')
    expect(req).toHaveTextContent('✔ 交付需求写好了，6 条验收标准')
    expect(req).toHaveTextContent('用时')
    expect(within(req).queryByTestId('type-line')).toBeNull()
    // 本机 did every round here, so its session is the one to open, once per lane that has it
    expect(lanes.getAllByRole('button', { name: '打开会话 ›' })).toHaveLength(rows.length)
    fireEvent.click(within(plan).getByRole('button', { name: '打开会话 ›' }))
    expect(navigations).toEqual([b.runs[0].sessions[0].url])
  })

  test('one approval covering two stages: its line in the last lane it covers, and once in the step strip', async () => {
    installFixtureRoutes(boardRoute(() => pay16()))
    renderBoard({ repo: 'r1', key: 'PAY-16' })
    await loaded()
    // PAY-16: one INCEPTION ask covers 需求分析 and 应用设计; both lanes wait, one holds the line
    expect(lane('requirements')).toHaveAttribute('data-status', 'awaiting_approval')
    expect(within(lane('requirements')).queryByTestId('type-line')).toBeNull()
    expect(within(within(lane('application-design')).getByTestId('type-line')).getByText('approve INCEPTION 3fa2c1d0')).toBeInTheDocument()
    // and the step strip asks for it once, not once per stage
    expect(within(region('你要做的一步')).getAllByText('approve INCEPTION 3fa2c1d0')).toHaveLength(1)
  })

  test('a lane names its stage as its box does: the unit whole after the stage, 发布 without its phase’s ✓', async () => {
    installFixtureRoutes(boardRoute(() => fx.boardMerged()))
    renderBoard(PAY2)
    await loaded()
    const name = (id: string) => lane(id).querySelector('.kt-lane-name')?.textContent
    expect(name('code-generation/refund-api')).toBe('代码生成 · refund-api3.5')
    expect(lane('code-generation/refund-api').querySelector('.kt-lane-unit')).toHaveTextContent(/^refund-api$/)
    expect(name('release')).toBe('发布')
    expect(name('reflect')).toBe('回写 Brain4')
  })

  test('someone else’s requirement: the box, the dashed line, the conveyor and the feed name its owner’s session by the owner, never 本机', async () => {
    const b = fx.board()
    b.person = { ...b.person, owner: 'bob', me: false, you: false }
    b.runs = b.runs.map((r) => (r.kind === 'local'
      ? { ...r, label: 'bob 的会话', readable: 'elsewhere', sessions: [], credits: null, now: { ...r.now, status: 'running', who: null, doing: 'bob 在自己的机器上做' } }
      : r))
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    renderBoard()
    const graph = await loaded()
    expect(stageBox(graph, 'requirements').querySelector('.kt-node-sub')).toHaveTextContent(/^✓ bob · \d+:\d\d$/)
    expect(within(graph).getAllByTestId('kt-cmd')[0]).toHaveTextContent('bob → 代码生成：在做')
    // the roster row's doing does not fit beside the run's name: the state's word says it, never a word cut short
    expect(said(within(graph).getAllByTestId('kt-roster-row'))).toEqual(['bob 的会话 · 在做'])
    expect(screen.getByTestId('kt-conveyor')).not.toHaveTextContent('本机')
    expect(screen.getAllByTestId('kt-ev-run').map((e) => e.textContent?.trim())).toContain('bob')
    expect(screen.getAllByTestId('kt-ev-run').map((e) => e.textContent?.trim())).not.toContain('本机')
  })

  test('a stage done in more than one round: its rounds in order, each with how it ended and who did it', async () => {
    installFixtureRoutes(boardRoute(() => fx.boardMerged()))
    renderBoard(PAY2)
    await loaded()
    const design = lane('application-design')
    expect(design).toHaveTextContent('第 2 轮')
    const rounds = within(within(design).getByTestId('kt-rounds')).getAllByRole('listitem')
    expect(rounds.map((r) => r.textContent)).toEqual([
      expect.stringMatching(/^第 1 轮 ✕ 你退回了 · (\d\d-\d\d )?\d\d:\d\d$/),
      expect.stringMatching(/^第 2 轮 ✓ 你批了 · (\d\d-\d\d )?\d\d:\d\d$/),
    ])
    expect(rounds[1]).toHaveClass('is-now')
    // a finished step with no note of its own says the fold's own line, never 「有人批了」
    expect(lane('reflect')).toHaveTextContent('✔ 交付已合进 Brain')
    // 构建测试 was done by the runner twice: the rounds name it, and the last round's checks are its 改动 block
    const test2 = lane('build-and-test')
    const two = within(within(test2).getByTestId('kt-rounds')).getAllByRole('listitem')
    expect(two.map((r) => r.textContent)).toEqual([
      expect.stringMatching(/^第 1 轮 ✕ pytest 没过 · (\d\d-\d\d )?\d\d:\d\d$/),
      expect.stringMatching(/^第 2 轮 ✓ 检查全过（3 项） · (\d\d-\d\d )?\d\d:\d\d$/),
    ])
    expect(test2).toHaveTextContent('运行机 第 1 次')
    const checks = within(test2).getByTestId('kt-lane-checks')
    expect(checks).toHaveTextContent('这一轮的检查')
    expect(checks).toHaveTextContent('pytest ✓')
  })

  test('the graph’s name says its steps as its boxes do: done, at work, what waits and on whom, the checks; nothing waiting is not said', async () => {
    installFixtureRoutes()
    renderBoard()
    const graph = await loaded()
    expect(within(graph).getByRole('group', { name: /^需求的路/ })).toHaveAccessibleName('需求的路：11 步，0 步在做，1 步等你，6 步做完；检查 2/4。点一步只看它。')
    cleanup()
    const c = planApproved(fx.board())
    c.nodes = c.nodes.map((n) => (n.id === 'build-and-test' ? { ...n, status: 'running' as const, doing: '在做', run: 'local' as RunKey, rounds: [round(1, 'local', c.at - 120, null)] } : n))
    c.runs = c.runs.map((r) => ({ ...r, now: { status: 'running' as const, doing: 'AI 在做', since: c.at - 120, who: null, node: 'build-and-test' as NodeId, do: null } }))
    Object.assign(c.row, { waits: { ...c.row.waits, who: 'agent', do: 'none', line: null, text: 'AI 在做' } })
    installFixtureRoutes(boardRoute(() => structuredClone(c)))
    renderBoard()
    const g2 = await loaded()
    expect(within(g2).getByRole('group', { name: /^需求的路/ })).toHaveAccessibleName('需求的路：11 步，1 步在做，7 步做完；检查 2/4。点一步只看它。')
  })
  test('a run is said as the 运行机 page says it (运行机 第 1 次), its id only in the graph’s list: the feed, the lanes, the conveyor', async () => {
    const b = fx.boardMerged()
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    renderBoard(PAY2)
    const graph = await loaded()
    const id = b.row.run_ids[0]
    const feed = screen.getByTestId('kt-feed')
    const signed = feed.querySelector('.kt-ev.is-signed') as HTMLElement
    expect(signed).toHaveTextContent('签名服务签了接手合同（第 1 次运行）')
    // the feed and the lanes say which run did it, in the run's own words
    expect(within(signed).getByTestId('kt-ev-run')).toHaveTextContent('运行机')
    expect(lane('code-generation/refund-api')).toHaveTextContent('运行机 第 1 次')
    expect(region('提交传送带')).toHaveTextContent('运行机')
    // the run id is on its row in the graph's list only
    expect(within(graph).getByTestId('kt-roster')).toHaveTextContent(id)
    expect(feed).not.toHaveTextContent(id)
    expect(region('每一步的泳道')).not.toHaveTextContent(id)
  })

  test('the lanes’ time starts at 开工, the moment 需求详情 says it started; the claim before it is 认领, not 开工', async () => {
    installFixtureRoutes()
    renderBoard()
    await loaded()
    const b = fx.board()
    const claim = b.events.find((e) => e.kind === 'claim')!
    const start = b.events.find((e) => e.kind === 'start')!
    expect(start.at).toBeGreaterThan(claim.at)
    expect(screen.getByTestId('kt-axis-title')).toHaveTextContent(`时间线（从 ${fmtWhen(start.at)} 开工起）`)
    const span = region('每一步的泳道').querySelector('.kt-track-span') as HTMLElement
    const a = b.nodes.flatMap((x) => x.spans).sort((p, q) => p.a - q.a).find((x) => x.a >= start.at)!
    expect(span.getAttribute('title')).toContain('（开工后 ')
    expect(span.getAttribute('title')).not.toContain(`（开工后 ${fmtSpan(a.a - claim.at)}）`)
    cleanup()
    // a board with no 开工 recorded (the run's record not read) starts at the claim and says so
    const c = fx.board()
    c.events = c.events.filter((e) => e.kind !== 'start')
    c.started_at = claim.at
    installFixtureRoutes(boardRoute(() => structuredClone(c)))
    renderBoard()
    await loaded()
    expect(screen.getByTestId('kt-axis-title')).toHaveTextContent(`时间线（从 ${fmtWhen(claim.at)} 认领起）`)
    expect(region('每一步的泳道').querySelector('.kt-track-span')?.getAttribute('title')).toContain('（认领后 ')
  })

  test('a stage Lean skipped has no box and no lane; a requirement with no step done yet says so where the lanes go', async () => {
    installFixtureRoutes()
    renderBoard()
    await loaded()
    // every lane is a stage with a round, in the way's order; the skipped ones are not there
    expect(laneIds()).toEqual(['requirements', 'application-design', 'units', 'functional-design/refund-export', 'functional-design/refund-api',
      'code-generation/refund-export', 'code-generation/refund-api'])
    const graph = screen.getByTestId('kt-graph')
    for (const id of ['reverse-engineering', 'user-stories', 'nfr-design/refund-api']) expect(stageBox(graph, id)).toBeNull()
    expect(graph.querySelector('[data-phase="inception"]')).toHaveTextContent('跳过：逆向工程、用户故事')
    cleanup()
    const b = fx.board()
    b.nodes = b.nodes.map((n) => ({ ...n, rounds: [], gates: [], spans: [], marks: [], status: n.status === 'skipped' ? n.status : 'waiting' as const }))
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    renderBoard()
    await loaded()
    expect(screen.queryAllByTestId('kt-lane')).toEqual([])
    expect(screen.getByTestId('kt-lanes-empty')).toHaveTextContent('还没有哪一步开始：开工以后，每做一步，这里多一条泳道')
  })

  test('the conveyor: every commit of the run branch in order, from the base to the branch head', async () => {
    installFixtureRoutes()
    renderBoard()
    await loaded()
    const b = fx.board()
    const belt = region('提交传送带')
    expect(belt).toHaveTextContent(`${b.belt.length} 次提交`)
    const chips = Array.from(belt.querySelectorAll<HTMLElement>('.kt-belt-chip'))
    expect(chips).toHaveLength(b.belt.length)
    b.belt.forEach((x, i) => {
      expect(chips[i]).toHaveTextContent(`本机${x.sha.slice(0, 8)} · ${x.files} 个文件 · `)
      expect(chips[i]).toHaveTextContent(x.text)
    })
    expect(chips[0]).toHaveTextContent('（只改记录）') // records_only
    expect(chips[1]).not.toHaveTextContent('（只改记录）')
    expect(belt.querySelector('.kt-belt-end.is-base')).toHaveTextContent(`从 ${b.base}`)
    expect(belt.querySelector('.kt-belt-end.is-head')).toHaveTextContent(b.branch as string)
    expect(belt).not.toHaveTextContent('已合并')
    // every chip says the run that made it, and looks at the stage it was made in
    expect(chips[0]).toHaveTextContent('本机')
    fireEvent.click(within(chips[0]).getByRole('button'))
    expect(screen.getByTestId('kt-focusbar')).toHaveTextContent('需求分析')
    expect(lane('requirements')).toHaveClass('is-focus')
  })

  test('the check wall: its groups in wall order, a cell per check with its state; focusing a cell says why', async () => {
    installFixtureRoutes()
    renderBoard()
    await loaded()
    const b = fx.board()
    const wall = region('检查墙')
    expect(wall).toHaveTextContent(`检查墙${b.checks.passed}/${b.checks.total}`)
    const groups = within(wall).getAllByRole('group')
    const present = CELL_GROUPS.filter((g) => b.checks.cells.some((c) => c.group === g.key))
    expect(groups.map((g) => g.getAttribute('aria-label'))).toEqual(present.map((g) => g.label))
    expect(groups.map((g) => g.getAttribute('aria-label'))).toEqual(['Lean 审批', '自动检查', '交接', 'Brain 和仓库'])
    present.forEach((g, i) => {
      const cells = within(groups[i]).getAllByTestId('kt-cell')
      expect(cells.map((c) => [c.dataset.case, c.dataset.status])).toEqual(b.checks.cells.filter((c) => c.group === g.key).map((c) => [c.id, c.status]))
    })
    // nothing hovered: the one that failed
    const inspect = within(wall).getByTestId('kt-inspect')
    expect(inspect).toHaveTextContent('1 项没过 · 还剩 发布审批、自动检查 · 没过的是 引用')
    const plan = b.checks.cells.find((c) => c.id === 'gate-PLAN')!
    const cell = within(wall).getByRole('button', { name: `${plan.name}：等你，${plan.detail}` })
    fireEvent.focus(cell)
    expect(inspect).toHaveTextContent(`✋ ${plan.name}${plan.detail}`)
    fireEvent.blur(cell)
    expect(inspect).toHaveTextContent('1 项没过 · 还剩 发布审批、自动检查 · 没过的是 引用')
  })

  test('the event feed: the trace newest first; Lean 细节 hides the many quiet steps', async () => {
    installFixtureRoutes()
    renderBoard()
    await loaded()
    const b = fx.board()
    const feed = region('事件')
    expect(feed).toHaveTextContent('来自 brain-kit 的记录')
    expect(feed).not.toHaveTextContent('trace')
    const items = () => Array.from(feed.querySelectorAll<HTMLElement>('li[data-seq]'))
    expect(items().map((li) => Number(li.dataset.seq))).toEqual(b.events.map((e) => e.seq).reverse())
    expect(items()[0]).toHaveTextContent(b.events[b.events.length - 1].text)
    expect(items()[items().length - 1]).toHaveTextContent(b.events[0].text)
    fireEvent.click(within(feed).getByRole('checkbox', { name: 'Lean 细节' }))
    expect(items().map((li) => Number(li.dataset.seq))).toEqual(b.events.filter((e) => !e.detail).map((e) => e.seq).reverse())
  })

  test('the open questions: one answerable here is at the top (回答 opens its form there); the others beside the wall', async () => {
    installFixtureRoutes()
    renderBoard()
    await loaded()
    const b = fx.board()
    const [here, lean] = b.questions.filter((q) => q.state === 'waiting' || q.state === 'open')
    const strip = region('你要做的一步')
    expect(within(strip).queryByTestId('question')).toBeNull()
    fireEvent.click(within(strip).getByRole('button', { name: '回答' }))
    expect(within(strip).getByTestId('question')).toHaveTextContent(here.question)
    expect(within(strip).getByRole('button', { name: '收起' })).toHaveAttribute('aria-expanded', 'true')
    const qs = region('等回答的问题')
    expect(within(qs).getByText('1')).toHaveClass('kt-badge')
    const cards = within(qs).getAllByTestId('question')
    expect(cards).toHaveLength(1)
    // the PLAN approval is Lean's: typed in the session, not answered here
    expect(cards[0]).toHaveTextContent(lean.question)
    expect(cards[0]).toHaveTextContent('这是 Lean 的代码生成计划审批请求：在会话里自己打批准那一行，这里不能回答。')
    expect(qs).not.toHaveTextContent(b.questions.find((q) => q.state === 'delivered')!.question)
  })

  test('it polls the board path and shows each new snapshot; a box whose state changed stays where it was', async () => {
    let n = 0
    installFixtureRoutes(boardRoute(() => {
      const b = fx.board()
      if (++n >= 2) planApproved(b)
      return b
    }))
    renderBoard(PAY3, { pollMs: 30 })
    const at = stageBox(await loaded(), 'code-generation/refund-api').getAttribute('transform')
    await waitFor(() => expect(boardCalls().length).toBeGreaterThanOrEqual(3))
    expect(apiCalls.every((c) => c.method === 'GET' && (c.path === paths.board('r1', 'PAY-3') || c.path === paths.overview('all')))).toBe(true)
    const plan = () => stageBox(screen.getByTestId('kt-graph'), 'code-generation/refund-api')
    await waitFor(() => expect(plan()).toHaveAttribute('data-status', 'merged'))
    expect(plan()).toHaveAttribute('transform', at)
    expect(lane('code-generation/refund-api')).toHaveAttribute('data-status', 'merged')
  })
  test('a failed refresh keeps the last board and says so', async () => {
    let n = 0
    installFixtureRoutes(boardRoute(() => {
      if (++n >= 2) throw apiError(500, 'internal', '读不到 trace')
      return fx.board()
    }))
    renderBoard(PAY3, { pollMs: 30 })
    await loaded()
    expect(await screen.findByRole('alert')).toHaveTextContent('刷新没成：读不到 trace（500），显示的是上一次拿到的数据')
    expect(screen.getByTestId('kt-graph')).toBeInTheDocument()
  })

  test('the 详情 buttons go to the requirement’s 需求详情, from the board; 交付总览 and ← 返回 go back', async () => {
    installFixtureRoutes()
    const onBack = vi.fn()
    const p = renderBoard({ ...PAY3, from: 'item' }, {}, onBack)
    await loaded()
    const item = { kind: 'item', repo: 'r1', key: 'PAY-3', from: 'board' }
    fireEvent.click(screen.getByRole('button', { name: '需求详情 ↗' }))
    expect(p.go).toHaveBeenLastCalledWith(item)
    fireEvent.click(screen.getByRole('button', { name: fx.board().row.title }))
    expect(p.go).toHaveBeenLastCalledWith(item)
    fireEvent.click(crumb().getByRole('button', { name: 'PAY-3' }))
    expect(p.go).toHaveBeenLastCalledWith(item)
    expect(p.go).toHaveBeenCalledTimes(3)
    // the crumb's first step is the way back, named as ← 返回 names it
    fireEvent.click(crumb().getByRole('button', { name: '需求详情' }))
    expect(onBack).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: '← 返回需求详情' }))
    expect(onBack).toHaveBeenCalledTimes(2)
    expect(p.go).toHaveBeenCalledTimes(3)
    expect(p.replace).not.toHaveBeenCalled()
  })

  test('opened from 待办: the crumb and ← 返回 both say 待办; opened from nowhere, the crumb starts at 交付总览', async () => {
    installFixtureRoutes()
    const onBack = vi.fn()
    renderBoard({ ...PAY3, from: 'todo' }, {}, onBack)
    await loaded()
    expect(screen.getByRole('navigation', { name: '位置' })).toHaveTextContent(/^待办›PAY-3›需求看板$/)
    fireEvent.click(crumb().getByRole('button', { name: '待办' }))
    fireEvent.click(screen.getByRole('button', { name: '← 返回待办' }))
    expect(onBack).toHaveBeenCalledTimes(2)
    cleanup()
    const p = renderBoard()
    await loaded()
    fireEvent.click(crumb().getByRole('button', { name: '交付总览' }))
    expect(p.go).toHaveBeenLastCalledWith({ kind: 'overview' })
    expect(screen.queryByRole('button', { name: /^← 返回/ })).toBeNull()
  })

  test('跟随 scrolls to the stage that moved last', async () => {
    installFixtureRoutes()
    renderBoard()
    await loaded()
    const scroll = vi.mocked(HTMLElement.prototype.scrollIntoView)
    scroll.mockClear()
    fireEvent.click(screen.getByRole('button', { name: '跟随' }))
    expect(screen.getByRole('button', { name: '跟随' })).toHaveAttribute('aria-pressed', 'true')
    // refund-api's 代码生成 waits for the person; its state is the newest of the stages at work or waiting
    await waitFor(() => expect(scroll.mock.contexts).toContain(lane('code-generation/refund-api')))
  })

  test('a board GET that fails says why, in an alert', async () => {
    installFixtureRoutes(boardRoute(() => { throw apiError(500, 'internal', '读不到 trace') }))
    renderBoard()
    expect(await screen.findByRole('alert')).toHaveTextContent(/^读不到 trace（500）$/)
    expect(screen.queryByTestId('kt-graph')).toBeNull()
    expect(screen.queryByRole('region', { name: '验收' })).toBeNull()
  })

  test('a requirement the repo does not have (404): which repo by its name, what to do, back to 交付总览; no repo id, no HTTP code', async () => {
    installFixtureRoutes(boardRoute(() => { throw apiError(404, 'not_found', '这个仓库里没有 PAY-9') }))
    const p = renderBoard({ repo: 'r1', key: 'PAY-9' })
    const card = await screen.findByRole('region', { name: '没有这个需求' })
    expect(card).toHaveTextContent('shop-pay 仓库里没有 PAY-9')
    expect(card).toHaveTextContent('做什么：在上面「切换需求」里换一个，或者回交付总览找。')
    expect(screen.getByTestId('br-board')).not.toHaveTextContent(/404|r1 ·/)
    expect(screen.queryByTestId('kt-graph')).toBeNull()
    // the crumb says the key and links nothing: there is no 需求详情 of it to open
    expect(crumb().queryByRole('button', { name: 'PAY-9' })).toBeNull()
    expect(crumb().getByTestId('kt-where-key')).toHaveTextContent('PAY-9')
    fireEvent.click(within(card).getByRole('button', { name: '回交付总览' }))
    expect(p.go).toHaveBeenCalledWith({ kind: 'overview', repo: 'r1' })
    expect(boardCalls({ repo: 'r1', key: 'PAY-9' })).toHaveLength(1)
    cleanup()
    // opened from 待办: back there, as the crumb says
    const back = vi.fn()
    renderBoard({ repo: 'r1', key: 'PAY-9', from: 'todo' }, {}, back)
    fireEvent.click(within(await screen.findByRole('region', { name: '没有这个需求' })).getByRole('button', { name: '回待办' }))
    expect(back).toHaveBeenCalledTimes(1)
  })

  test('team mode, a requirement handed to the runner opened at its repo: it says so and opens the runner’s 需求看板; never that it is not there', async () => {
    const o = fx.overview()
    o.rows = o.rows.map((r) => (r.key === 'PAY-4' ? { ...r, repo: 'runner', where: 'runner' } : r))
    const s = fx.status()
    s.app.team = true
    installFixtureRoutes({ [`GET ${API}/overview*`]: () => o })
    const p = renderBoard({ repo: 'r1', key: 'PAY-4' }, { status: s })
    const note = await screen.findByTestId('on-runner')
    expect(note).toHaveTextContent('PAY-4 已交给运行机')
    fireEvent.click(within(note).getByRole('button', { name: '打开运行机上的需求看板' }))
    expect(p.go).toHaveBeenCalledWith({ kind: 'board', repo: 'runner', key: 'PAY-4' })
    cleanup()
    installFixtureRoutes({ [`GET ${API}/overview*`]: () => o, ...boardRoute(() => { throw apiError(404, 'not_found', '这个仓库里没有 PAY-4') }) })
    renderBoard({ repo: 'r1', key: 'PAY-4' }, { status: s })
    expect(await screen.findByTestId('on-runner')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '没有这个需求' })).toBeNull()
    expect(screen.getByTestId('br-board')).not.toHaveTextContent(/shop-pay (仓库)?里没有/)
  })

  test('a backend without the /board route (a 404 without an error body) is the same missing state', async () => {
    const routes = fixtureRoutes()
    delete routes[`GET ${API}/board/*`]
    setApiRoutes(routes)
    renderBoard()
    expect(await screen.findByRole('region', { name: '没有这个需求' })).toHaveTextContent('shop-pay 仓库里没有 PAY-3')
  })

  test('the loading line while the first snapshot is on its way', async () => {
    let release: (b: ReqBoard) => void = () => {}
    installFixtureRoutes(boardRoute(() => new Promise<ReqBoard>((r) => (release = r)) as unknown as ReqBoard))
    renderBoard()
    expect(screen.getByText('正在读取…')).toBeInTheDocument()
    await waitFor(() => expect(boardCalls()).toHaveLength(1))
    expect(screen.getByText('正在读取…')).toBeInTheDocument()
    release(fx.board())
    await loaded()
    expect(screen.queryByText('正在读取…')).toBeNull()
  })
})

const H = 3600

/** PAY-3 as an idle session that waits for the person to talk (UI问题清单 #2, #4). */
function idle(): ReqBoard {
  const b = fx.board()
  const since = b.at - 11 * H - 30 * 60
  b.row.waits = { who: 'you', text: '会话空着：轮到你回话', line: null, raw: '', do: 'reply', since, where: 'session', review: false }
  b.row.lean = { ...b.row.lean, stage: 'release', stage_label: '发布', stage_n: '✓', stage_state: 'todo', done: 10, done_n: 4, skipped_n: 6, next: { id: 'release', label: '发布', n: '✓' } }
  b.accept.step = { text: '构建和测试做完了，下一步是发布：在会话里让它接着做', who: '你', line: null, mr_url: null, session_url: '/chat?sid=brain-PAY-3-7f3a' }
  b.questions = []
  b.person = { ...b.person, doing: '会话空着：轮到你回话' }
  b.nodes = b.nodes.map((n) => ({
    ...n, marks: n.marks.filter((m) => m.at <= since),
    spans: n.spans.filter((x) => x.a <= since).map((x) => ({ ...x, b: x.b === null || x.b > since ? since : x.b })),
  }))
  b.events = b.events.filter((e) => e.at <= since)
  b.belt = b.belt.filter((x) => x.at <= since)
  // the plan approved, 构建测试 and 审查 done in the session; it stopped before 发布, the next box on its way
  planApproved(b, since - 3 * H)
  b.nodes = b.nodes.map((n) => {
    if (n.id === 'build-and-test') return { ...n, status: 'done' as const, doing: '检查都过了', since: since - H, run: 'local' as RunKey,
      rounds: [round(1, 'local', since - 2 * H, since - H, { kind: 'passed', checks: [{ name: 'pytest', ok: true, secs: 41 }] })] }
    if (n.id === 'review') return { ...n, status: 'done' as const, doing: '写好了审查', since, run: 'local' as RunKey, rounds: [round(1, 'local', since - H, since)] }
    // 发布 is the box the session stopped before: it waits on the person, with no gate asked
    if (n.id === 'release') return { ...n, status: 'awaiting_approval' as const, doing: '会话空着：轮到你回话', since, run: 'local' as RunKey,
      rounds: [round(1, 'local', since, null)], spans: [{ a: since, b: null, kind: 'wait' as const, run: 'local' as RunKey }],
      history: [{ at: since, status: 'awaiting_approval' as const, doing: '会话空着：轮到你回话', who: 'you' as const }] }
    return n
  })
  b.runs = b.runs.map((r) => ({ ...r, now: { status: 'awaiting_approval' as const, doing: '会话空着：轮到你回话', since, who: 'you' as const, node: 'release' as NodeId, do: 'reply' as const } }))
  return b
}

/** PAY-3 waiting for product's answer on the MR, after the hand-over. */
function forProduct(): ReqBoard {
  const b = fx.board()
  b.row.waits = { who: 'product', text: '停在 D-2：等产品在 MR 上回答', line: null, raw: '', do: 'answer', since: b.at - H, where: 'mr' }
  b.accept.step = { text: '停在 D-2：等产品在 MR 上回答', who: '产品', line: null, mr_url: 'https://gitlab.example.com/shop/shop-pay/-/merge_requests/41', session_url: null }
  b.person = { ...b.person, doing: '停在 D-2：等产品在 MR 上回答' }
  b.questions = []
  const hand = b.at - 3 * H
  planApproved(b, hand - H)
  b.nodes = b.nodes.map((n) => (n.id === 'build-and-test' ? { ...n, status: 'awaiting_approval' as const, doing: '停在 D-2：等产品在 MR 上回答', since: b.at - H,
    run: 'runner-1' as RunKey, rounds: [round(1, 'runner-1', hand + H, null)],
    spans: [{ a: hand + H, b: b.at - H, kind: 'work' as const, run: 'runner-1' as RunKey }, { a: b.at - H, b: null, kind: 'wait' as const, run: 'runner-1' as RunKey }],
    history: [{ at: hand + H, status: 'running' as const, doing: '在做', who: null },
      { at: b.at - H, status: 'awaiting_approval' as const, doing: '停在 D-2：等产品在 MR 上回答', who: 'product' as const }] } : n))
  b.runs = [{ ...b.runs[0], now: { status: 'done', doing: '做完 · 交出去了', since: hand, who: null, node: null, do: null } },
    run('runner-1', 'runner', '运行机 第 1 次', { status: 'awaiting_approval', doing: '停在 D-2：等产品在 MR 上回答', since: b.at - H, who: 'product', node: 'build-and-test' as NodeId },
      { n: 1, run_id: 'RUN-PAY-3-1' })]
  const steps = [['handover', '交接'], ['bridge', '桥核对'], ['signer', '签名'], ['workspace', '运行机建工作区']] as const
  b.relays = [{ to: 'runner-1' as RunKey, at: hand, sha: 'e7d2b11c4a90', steps: steps.map(([key, title], i) => ({ key, title, status: 'done' as const, at: hand + i * 60, text: '', todo: null })) }]
  return b
}

/** A repo done only on this machine: the runner's part is off the way. */
function localOnly(b: ReqBoard = fx.board()): ReqBoard {
  b.row.local_only = true
  b.relays = []
  b.runs = b.runs.filter((r) => r.kind === 'local')
  b.checks.cells = b.checks.cells.map((c) => (c.group === 'handover' ? { ...c, status: 'not_needed' as const, detail: '这个仓库只在本机做' } : c))
  return b
}

describe('需求看板 · 轮到谁、做什么', () => {
  test('a claimed requirement: 开工 opens 开需求 with its key and title; no English error about a run it does not have yet', async () => {
    const b = fx.board()
    Object.assign(b.row, { state: 'claimed', state_label: '已认领' })
    b.error = 'brainkit.trace 读不出这个需求：runs/PAY-3/run.json not found in working tree (a merged run: --ref origin/main)'
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    const p = renderBoard()
    const intro = await screen.findByTestId('br-board-intro')
    expect(screen.queryByTestId('br-board-problem')).toBeNull()
    expect(screen.getByTestId('br-board')).not.toHaveTextContent('not found')
    fireEvent.click(within(intro).getByRole('button', { name: '去开工 PAY-3' }))
    expect(p.go).toHaveBeenCalledWith({ kind: 'new', repo: 'r1', key: 'PAY-3', title: b.row.title })
  })

  test('someone else’s claim: it says whose it is and waits for them; no 开工', async () => {
    const b = fx.board()
    Object.assign(b.row, { state: 'claimed', state_label: '已认领', owner: 'carol' })
    b.person = { ...b.person, owner: 'carol', me: false, you: false }
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    renderBoard()
    const intro = await screen.findByTestId('br-board-intro')
    expect(within(intro).getByTestId('br-intro-start')).toHaveTextContent('carol 认领的，等 carol 开工')
    expect(screen.queryByRole('button', { name: /^去开工/ })).toBeNull()
    // the way's first step is carol's: 「0 认领 · carol · 认领、交文档、开工」, no 你 anywhere in it (BOARD-11)
    const claim = intro.querySelector('.kt-intro-phase') as HTMLElement
    expect(claim).toHaveTextContent('0 认领一个需求号，文档过五关carol认领、交文档、开工')
    expect(intro).not.toHaveTextContent('你')
  })

  test('the owner’s own claim speaks to them: 0 认领 · 你; a board that names its owner offers no 开工, whoever the repo thinks is looking', async () => {
    const b = fx.board()
    Object.assign(b.row, { state: 'claimed', state_label: '已认领' })
    b.person = { ...b.person, you: true }
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    renderBoard()
    let intro = await screen.findByTestId('br-board-intro')
    expect(intro.querySelector('.kt-intro-phase')).toHaveTextContent('0 认领一个需求号，文档过五关你认领、交文档、开工')
    expect(within(intro).getByRole('button', { name: '去开工 PAY-3' })).toBeInTheDocument()
    cleanup()
    // erin's claim in a repo where 你是谁 is not set yet: the backend named erin, so does the introduction
    Object.assign(b.row, { owner: 'erin' })
    b.person = { ...b.person, owner: 'erin', me: false, you: false }
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    const status = fx.status()
    status.repos = status.repos.map((r) => ({ ...r, me: r.me ? { ...r.me, name: null } : r.me }))
    renderBoard(PAY3, { status })
    intro = await screen.findByTestId('br-board-intro')
    expect(screen.queryByRole('button', { name: /^去开工/ })).toBeNull()
    expect(intro).toHaveTextContent('需求和设计由 erin 在会话里自己批准')
    expect(within(intro).getByTestId('br-intro-start')).toHaveTextContent('erin 认领的，等 erin 开工')
    expect(intro).not.toHaveTextContent('你')
  })

  test('a board brain-kit could not read whole: one Chinese sentence and what to do, its own words under 细节', async () => {
    const b = fx.board()
    b.error = 'brainkit.trace 读不出这个需求：runs/PAY-3/run.json not found in working tree'
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    renderBoard()
    await loaded()
    const banner = screen.getByTestId('br-board-problem')
    expect(banner).toHaveTextContent('这个需求的运行记录没读全')
    expect(banner).toHaveTextContent('做什么：')
    const raw = within(banner).getByText('细节').closest('details') as HTMLElement
    expect(raw).not.toHaveAttribute('open')
    expect(raw).toHaveTextContent('run.json not found in working tree')
  })

  test('an idle session: the step says what to say and where, 去回话 right there; the lane draws the wait as a wait', async () => {
    const b = idle()
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    renderBoard()
    const graph = await loaded()
    const strip = region('你要做的一步')
    expect(strip).toHaveTextContent('已经等了 11 小时 30 分')
    expect(strip).toHaveTextContent(b.accept.step.text)
    fireEvent.click(within(strip).getByRole('button', { name: '去回话' }))
    // the chip under the box the session stopped before says the same step and opens the session too, never 去回答
    fireEvent.click(within(graph).getByRole('button', { name: '发布：去回话' }))
    expect(navigations).toEqual([b.accept.step.session_url, b.runs[0].sessions[0].url])
    expect(graph).not.toHaveTextContent('去回答')
    const release = lane('release')
    expect(release).toHaveTextContent('⏱ 等了 11 小时 30 分')
    expect(within(release).getByRole('img')).toHaveAccessibleName(/等你 11 小时 30 分$/)
    expect(release.querySelector('.kt-track-span.is-wait.is-live')).not.toBeNull()
    expect(release.querySelector('.kt-track-span.is-work.is-live')).toBeNull()
    // the axis ends at the last activity; the eleven quiet hours are drawn short at its end
    expect(screen.getByTestId('kt-axis-tail')).toHaveTextContent('之后 11 小时 30 分')
    expect(screen.getByTestId('kt-axis-tail')).toHaveAttribute('title', '最后一次动静以后又过了 11 小时 30 分，一直在等：这一段画短了')
  })

  test('waiting for product: 等产品, never 等你; no chip, the dashed line calm, nothing blinks; the strip says it is not your turn', async () => {
    const b = forProduct()
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    renderBoard()
    const graph = await loaded()
    const bt = stageBox(graph, 'build-and-test')
    expect(bt.querySelector('.kt-node-sub')).toHaveTextContent(/^运行机 · 等产品$/)
    expect(said(bt.querySelectorAll('.kt-node-badge'))).toEqual(['等产品'])
    expect(bt).toHaveClass('is-other')
    expect(graph.querySelectorAll('.kt-node-halo')).toHaveLength(0)
    const cmds = within(graph).getAllByTestId('kt-cmd')
    expect(cmds.map((c) => c.getAttribute('data-run'))).toEqual(['runner-1'])
    expect(cmds[0].querySelector('path')).toHaveClass('kt-cmd', 'is-other')
    expect(within(graph).queryAllByTestId('kt-gate-pill')).toHaveLength(0)
    expect(within(graph).queryAllByTestId('kt-step-pill')).toHaveLength(0)
    expect(graph.querySelector('svg')).not.toHaveTextContent('等你')
    expect(within(graph).getAllByTestId('kt-roster-row')[2]).toHaveClass('is-other')
    expect(screen.getByTestId('br-next')).toHaveTextContent('此刻不用你做什么停在 D-2：等产品在 MR 上回答')
    expect(screen.getByRole('group', { name: '指标' })).toHaveTextContent('1 个等产品')
    const bts = lane('build-and-test')
    expect(bts).toHaveAttribute('aria-label', '构建测试：等产品')
    expect(bts).toHaveTextContent('运行机 第 1 次')
    expect(bts.querySelector('.kt-track-span.is-wait.is-other')).not.toBeNull()
  })
  test('a refused hand-over: why first, then what to do and where (the command to copy), the bridge’s words under 细节', async () => {
    const b = fx.board()
    Object.assign(b.row, { state: 'rejected', state_label: '接手被拒' })
    b.row.waits = { who: 'you', text: '接手被拒：标签是 bob 打的，不是负责人 alice', line: null, raw: "label 'ai-handoff' was added by bob, not by the branch owner alice", do: 'redo_handoff', since: b.at - H, where: null }
    b.accept.step = { text: '改好后在分支上重新 brain handoff（它提交交接、推送并打标签）', who: '你', line: null, mr_url: 'https://gitlab.example.com/shop/shop-pay/-/merge_requests/46', session_url: '/chat?sid=brain-PAY-3-7f3a' }
    b.questions = []
    planApproved(b)
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    renderBoard()
    await loaded()
    const strip = region('你要做的一步')
    expect(strip).toHaveTextContent('为什么被拒标签是 bob 打的，不是负责人 alice')
    expect(strip).toHaveTextContent(`怎么办${b.accept.step.text}`)
    expect(strip).toHaveTextContent('在哪做在这个需求的工作目录里开一个终端，敲：brain handoff')
    fireEvent.click(within(strip).getByRole('button', { name: '复制命令' }))
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith(`cd "${b.row.worktree}" && brain handoff`))
    expect(within(strip).getByText('细节').closest('details')).toHaveTextContent('was added by bob')
    expect(within(strip).getByRole('link', { name: '看 MR ↗' })).toHaveAttribute('href', b.accept.step.mr_url)
  })

  test('a refused hand-over as the backend says it (the reason, then what to do): the reason once; no wait time it does not know', async () => {
    const b = fx.board()
    Object.assign(b.row, { state: 'rejected', state_label: '接手被拒' })
    b.row.waits = { who: 'you', text: '接手被拒：标签是 bob 打的，不是负责人 alice', line: null, raw: '', do: 'redo_handoff', since: null, where: 'session' }
    b.accept.step = { text: '接手被拒：标签是 bob 打的，不是负责人 alice\n改好后，在终端里、这个需求的分支上重新跑 brain handoff', who: '你', line: null, mr_url: null, session_url: null }
    b.questions = []
    planApproved(b)
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    renderBoard()
    await loaded()
    const strip = region('你要做的一步')
    expect(strip.textContent!.match(/标签是 bob 打的/g)).toHaveLength(1)
    expect(strip).toHaveTextContent('怎么办改好后，在终端里、这个需求的分支上重新跑 brain handoff')
    expect(strip).not.toHaveTextContent('已经等了')
  })

  test('an idle session whose next step is Lean’s review in a new session: that step, 开审查会话 right there, the line it says folded', async () => {
    const said = 'Review intent 261107-refund-export against its requirements, design and plans, using the diff, and write construction/build-and-test/review.md.'
    const b = idle()
    b.row.waits = { ...b.row.waits, text: '构建测试做完了，下一步是发布：先开一个新会话做审查（这个会话写了代码，不能自己审）', review: false }
    b.nodes = b.nodes.map((n) => (n.id === 'review' ? { ...n, status: 'waiting' as const, doing: '等构建测试', since: null, run: null, rounds: [] } : n))
    b.runs = b.runs.map((r) => ({ ...r, now: { ...r.now, node: 'review' as NodeId } }))
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    renderBoard()
    await loaded()
    // the backend's judgment, not the words
    expect(within(region('你要做的一步')).queryByRole('button', { name: '开审查会话' })).toBeNull()
    cleanup()
    b.row.waits.review = true
    b.accept.step = { ...b.accept.step, line: said }
    installFixtureRoutes({ ...boardRoute(() => structuredClone(b)), [`POST ${API}/requirements/r1/PAY-3/session`]: () => ({ ok: true, slot: 'brain-PAY-3-r2', url: '/chat?sid=brain-PAY-3-r2' }) })
    renderBoard()
    await loaded()
    const strip = region('你要做的一步')
    expect(strip).toHaveTextContent('先开一个新会话做审查（这个会话写了代码，不能自己审）')
    expect(strip).toHaveTextContent('开审查会话会把 Lean 的审查要求发给新会话')
    expect(within(strip).queryByTestId('type-line')).toBeNull()
    // never the session that wrote the code under another name (REST-18)
    expect(within(strip).queryByRole('button', { name: /^(去回话|打开会话)$/ })).toBeNull()
    const fold = within(strip).getByTestId('review-say')
    expect(fold.tagName).toBe('DETAILS')
    expect(fold).toHaveTextContent(`细节：开审查会话对新会话说的那一句${said}`)
    fireEvent.click(within(fold).getByRole('button', { name: '复制那一句' }))
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith(said))
    // the chip under 审查 names the same step and brings the strip, not the session that wrote the code
    fireEvent.click(within(screen.getByTestId('kt-graph')).getByRole('button', { name: '审查：开审查会话' }))
    expect(navigations).toEqual([])
    fireEvent.click(within(strip).getByRole('button', { name: '开审查会话' }))
    await waitFor(() => expect(apiCalls.find((c) => c.method === 'POST')).toMatchObject({ body: { trust: true, kickoff: true, review: true } }))
    await waitFor(() => expect(navigations).toEqual(['/chat?sid=brain-PAY-3-r2']))
  })

  test('a repo done only here: no hand-over on the graph, only 本机会话 in its list and on its rail; the top says so', async () => {
    installFixtureRoutes(boardRoute(() => structuredClone(localOnly())))
    renderBoard()
    const graph = await loaded()
    expect(within(graph).queryByTestId('kt-relay')).toBeNull()
    expect(said(within(graph).getAllByTestId('kt-roster-row'))).toEqual(['本机会话 · 等你'])
    expect(within(graph).getAllByTestId('kt-rail-dot').map((d) => d.getAttribute('data-run'))).toEqual(['local'])
    expect(within(graph).getByRole('note', { name: '图例' })).not.toHaveTextContent('交接')
    expect(screen.getByTestId('br-local-only')).toHaveTextContent('这个仓库只在本机做')
    // the hand-over's checks are not needed either, and the wall says so in the same words
    const cells = within(region('检查墙')).getAllByTestId('kt-cell').filter((c) => c.dataset.status === 'not_needed')
    expect(cells.map((c) => c.textContent)).toEqual(['桥–', '签名–'])
  })
  test('a claimed requirement of a repo done only here: its introduction is the way it will go, without the runner', async () => {
    const b = localOnly()
    Object.assign(b.row, { state: 'claimed', state_label: '已认领' })
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    renderBoard()
    const intro = await screen.findByTestId('br-board-intro')
    expect(intro).not.toHaveTextContent('运行机')
    expect(intro).not.toHaveTextContent('桥')
    expect(intro).toHaveTextContent('发布审批')
  })

  test('the checks: what is left is said (目前 … 都过 · 还剩 …), not 全过; the 14 days after the merge are 观察中, nobody’s turn', async () => {
    const b = fx.board()
    b.checks.cells = b.checks.cells.map((c) => (c.id === 'brain-cite' || c.id === 'gate-PLAN' ? { ...c, status: 'pass' } : c))
    Object.assign(b.checks, { passed: 4, failed: 0, total: 4, todo: ['发布审批'] })
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    renderBoard()
    await loaded()
    expect(screen.getByTestId('br-checks-say')).toHaveTextContent('目前 4 项都过 · 还剩 发布审批')
    expect(screen.getByTestId('kt-station')).toBeInTheDocument()
    expect(screen.getByRole('group', { name: '指标' }).querySelector('.kt-kpi-tests')).not.toHaveClass('is-green')
    cleanup()
    const m = fx.boardMerged()
    m.checks.cells = m.checks.cells.map((c) => (c.id === 'after-14d' ? { ...c, status: 'watch' } : c))
    Object.assign(m.checks, { total: 9 })
    installFixtureRoutes(boardRoute(() => structuredClone(m)))
    renderBoard(PAY2)
    await loaded()
    // the backend's todo names the 14 days too: they are said once, as 在观察, not as something left to do
    expect(screen.getByTestId('br-checks-say')).toHaveTextContent(/^9 项全过 · 1 项在观察$/)
    expect(screen.getByRole('group', { name: '指标' })).not.toHaveTextContent('等人')
    expect(within(region('检查墙')).getByRole('button', { name: /^合并后 14 天没有回滚或热修：观察中，/ })).toHaveAttribute('data-status', 'watch')
  })

  test('the wall asks to fix the repo’s kit: 去设置修 right there opens 设置', async () => {
    const b = fx.board()
    b.checks.cells.push({ id: 'kit-0', group: 'brain', label: 'kit', name: 'kit 文件', status: 'fail', at: null, detail: '仓库里的 kit 是旧的：重装 kit 文件', node: null, run: null })
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    const p = renderBoard()
    await loaded()
    const fix = screen.getByTestId('kt-wall-fix')
    expect(fix).toHaveTextContent('仓库的 kit 有 1 处要修')
    fireEvent.click(within(fix).getByRole('button', { name: '去设置修 ›' }))
    expect(p.go).toHaveBeenCalledWith({ kind: 'settings' })
  })

  test('Lean’s progress the one way: done, skipped, the next stage lit; the session’s lane says the same', async () => {
    installFixtureRoutes(boardRoute(() => structuredClone(idle())))
    renderBoard()
    await loaded()
    const lean = screen.getByText('AI-DLC Lean').parentElement as HTMLElement
    expect(lean).toHaveAccessibleName('AI-DLC Lean：做完 4 · 跳过 6 · 下一步：发布 · 构思整段不需要')
    // one cell per stage on the way: Ideation, skipped as a whole by `lean new`, is said once instead of 7 grey cells
    expect(lean.querySelectorAll('.kt-segs i')).toHaveLength(11)
    expect(lean.querySelectorAll('.kt-segs .is-pass')).toHaveLength(4)
    expect(lean.querySelectorAll('.kt-segs .is-skip')).toHaveLength(6)
    expect(lean.querySelectorAll('.kt-segs .is-next')).toHaveLength(1)
    expect(within(lean).getByTestId('br-lean-whole')).toHaveTextContent('构思整段不需要')
    expect(lean).not.toHaveTextContent('10/11')
  })

  test('a lane shows what its own step changed: the files and their lines, its commits', async () => {
    installFixtureRoutes()
    renderBoard()
    await loaded()
    const changes = within(lane('code-generation/refund-export')).getByTestId('kt-changes')
    const head = changes.querySelector('.kt-files-head') as HTMLElement
    // the two commits of that stage only (the 需求分析 and refund-api ones are in their own lanes)
    expect(head.querySelector('.kt-add')).toHaveTextContent(/^\+147$/)
    expect(head.querySelector('.kt-del')).toHaveTextContent(/^−7$/)
    expect(head).toHaveTextContent('5 个文件 · 提交 2 次')
    // a file both commits changed is one row, its lines summed; the biggest first, five shown
    const rows = Array.from(changes.querySelectorAll<HTMLElement>('.kt-files li:not(.kt-more)'))
    expect(rows.map((r) => r.querySelector('.kt-file-path')!.textContent)).toEqual([
      'src/refund/api.py', 'tests/test_refund.py', 'src/refund/jobs.py', 'tests/test_refund_api.py', 'src/refund/export.py',
    ])
    expect(rows[1]).toHaveTextContent('+31 −7')
    expect(rows[2]).toHaveTextContent('+35')
    // its commits, newest first
    expect(within(changes).getByRole('list', { name: '提交' })).toHaveTextContent(/^193239b7PAY-3：refund-export：分批写 CSV93b2dea5PAY-3：导出接口的骨架$/)
    // and the 需求分析 lane holds the commit of its own round, with its own count
    expect(within(lane('requirements')).getByTestId('kt-changes')).toHaveTextContent('2 个文件 · 提交 1 次')
  })

  test('a backend that read no files: each commit says how many files it changed', async () => {
    const b = fx.board()
    for (const x of b.belt) delete x.changes
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    renderBoard()
    await loaded()
    const changes = within(lane('code-generation/refund-api')).getByTestId('kt-changes')
    expect(changes.querySelector('.kt-files-head')).toHaveTextContent(/^改动提交 1 次$/)
    expect(within(changes).getByRole('list', { name: '提交' })).toHaveTextContent('61ddbc30PAY-3：refund-api 的代码计划1 个文件 · 只改记录')
  })

  test('small words: one credits number everywhere, 运行记录里的合计 without sessions; +N for more Brain entries; one verify round is said, not drawn', async () => {
    const b = fx.board()
    b.credits = { ...b.credits, total: 36.954 }
    b.sessions = []
    b.brain = { ...b.brain, cited: ['P-PAY-2-1', 'T-PAY-2-1', 'T-12', 'T-13', 'T-14', 'T-15'] }
    b.checks.history = [{ at: b.at - H, passed: 3, failed: 0 }]
    b.mr_desc = { ...b.mr_desc, first: '给 `calc.py` 加乘法（D01#S002 · 097f2e9e）' }
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    renderBoard()
    const graph = await loaded()
    const credits = within(screen.getByRole('group', { name: '指标' })).getByText('credits').parentElement as HTMLElement
    expect(credits).toHaveTextContent('37.0运行记录里的合计')
    expect(region('验收')).toHaveTextContent('花了多少37.0 credits · 运行记录里的合计')
    expect(within(graph).getByTestId('kt-roster')).toHaveTextContent('+2 条')
    expect(region('检查墙').querySelector('.kt-spark')).toBeNull()
    expect(region('检查墙')).toHaveTextContent('自动检查跑了 1 次：3 项过了')
    const first = within(region('验收')).getByTestId('accept-first')
    expect(first).toHaveTextContent('给 calc.py 加乘法')
    expect(within(first).getByText('calc.py').tagName).toBe('CODE')
    expect(first).not.toHaveTextContent('D01#S002')
  })

  test('the owner’s card on hover has the whole sentence their node can only start', async () => {
    installFixtureRoutes()
    renderBoard()
    const graph = await loaded()
    fireEvent.mouseEnter(within(graph).getByTestId('kt-orch'))
    const card = await screen.findByRole('dialog', { name: '负责人' })
    expect(card).toHaveTextContent(`你（alice）等你${fx.board().person.doing}`)
  })

  test('the top bar says 打开会话, the word used everywhere', async () => {
    installFixtureRoutes()
    renderBoard()
    await loaded()
    expect(within(screen.getByTestId('br-board').querySelector('.kt-top') as HTMLElement).getByRole('button', { name: '打开会话' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '本机会话' })).toBeNull()
  })
})

test('the time axis names each tick once: half hours are said, not rounded onto the next hour', () => {
  expect([0, 1800, 3600, 5400, 7200, 3 * 3600, 86400 + 6 * 3600].map(tickLabel)).toEqual(['0', '30 分', '1 小时', '1 小时 30 分', '2 小时', '3 小时', '1 天 6 时'])
})

describe('需求看板 · 回放', () => {
  const merged = () => installFixtureRoutes(boardRoute(() => fx.boardMerged()))
  const bar = () => screen.getByRole('group', { name: '回放' })
  const feedSeqs = () => Array.from(screen.getByTestId('kt-feed').querySelectorAll('li[data-seq]')).length

  test('▶ 回放 opens the controls: playing, the scrubber, 1× 2× 4×, 退出回放', async () => {
    merged()
    renderBoard(PAY2)
    await loaded()
    expect(screen.queryByRole('group', { name: '回放' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '▶ 回放' }))
    const rp = within(bar())
    expect(rp.getByRole('button', { name: '暂停' })).toBeInTheDocument()
    expect(rp.getByRole('slider', { name: '回放进度' })).toBeInTheDocument()
    expect(rp.getAllByRole('radio').map((r) => [r.textContent, r.getAttribute('aria-checked')])).toEqual([['1×', 'true'], ['2×', 'false'], ['4×', 'false']])
    expect(rp.getByRole('button', { name: '退出回放' })).toBeInTheDocument()
    expect(bar()).toHaveTextContent('按记录重放：安静的时段缩短了，不调用 agent')
    expect(screen.getByRole('navigation', { name: '位置' })).toHaveTextContent(/需求看板 · 回放$/)
    expect(screen.queryByRole('button', { name: '▶ 回放' })).toBeNull()
    // it starts at the beginning: fewer events than the whole record
    expect(feedSeqs()).toBeLessThan(fx.boardMerged().events.length)
  })

  test('暂停 and 播放; the speeds are a radio group', async () => {
    merged()
    renderBoard(PAY2)
    await loaded()
    fireEvent.click(screen.getByRole('button', { name: '▶ 回放' }))
    const rp = within(bar())
    fireEvent.click(rp.getByRole('button', { name: '暂停' }))
    expect(rp.getByRole('button', { name: '播放' })).toBeInTheDocument()
    fireEvent.click(rp.getByRole('radio', { name: '4×' }))
    expect(rp.getByRole('radio', { name: '4×' })).toHaveAttribute('aria-checked', 'true')
    expect(rp.getByRole('radio', { name: '1×' })).toHaveAttribute('aria-checked', 'false')
    expect(rp.getByRole('radio', { name: '4×' })).toHaveClass('is-on')
    fireEvent.click(rp.getByRole('radio', { name: '2×' }))
    expect(rp.getAllByRole('radio').map((r) => r.getAttribute('aria-checked'))).toEqual(['false', 'true', 'false'])
    fireEvent.click(rp.getByRole('button', { name: '播放' }))
    expect(rp.getByRole('button', { name: '暂停' })).toBeInTheDocument()
  })

  test('the scrubber: to the end shows the board as it ended, to the start the beginning', async () => {
    merged()
    renderBoard(PAY2)
    await loaded()
    const b = fx.boardMerged()
    fireEvent.click(screen.getByRole('button', { name: '▶ 回放' }))
    const rp = within(bar())
    fireEvent.click(rp.getByRole('button', { name: '暂停' }))
    const scrub = rp.getByRole('slider', { name: '回放进度' }) as HTMLInputElement
    fireEvent.change(scrub, { target: { value: scrub.max } })
    expect(scrub.value).toBe(scrub.max)
    expect(rp.getByRole('button', { name: '从头再放' })).toBeInTheDocument()
    expect(bar()).toHaveTextContent('放完了：下面是这个需求最后的样子')
    expect(feedSeqs()).toBe(b.events.length)
    const withRound = b.nodes.filter((n) => n.status !== 'skipped' && n.rounds.length > 0)
    expect(laneIds()).toEqual(withRound.map((n) => n.id))
    expect(screen.getAllByTestId('kt-lane').map((l) => l.dataset.status)).toEqual(withRound.map((n) => n.status))
    fireEvent.change(scrub, { target: { value: '0' } })
    expect(scrub.value).toBe('0')
    expect(bar()).toHaveTextContent('第 0 秒')
    // nothing had a round then: no lane, and the graph's boxes are all waiting
    expect(screen.queryAllByTestId('kt-lane')).toEqual([])
    expect(feedSeqs()).toBe(b.events.filter((e) => e.at <= Math.min(b.started_at ?? Infinity, ...b.events.map((x) => x.at))).length)
    expect(rp.getByRole('button', { name: '播放' })).toBeInTheDocument()
  })

  test('退出回放 goes back to the live board; the replay sends nothing; the URL says replay=1 while it replays', async () => {
    merged()
    const p = renderBoard(PAY2)
    await loaded()
    const b = fx.boardMerged()
    fireEvent.click(screen.getByRole('button', { name: '▶ 回放' }))
    expect(p.replace).toHaveBeenLastCalledWith({ kind: 'board', repo: 'r1', key: 'PAY-2', replay: true })
    fireEvent.click(within(bar()).getByRole('button', { name: '退出回放' }))
    expect(p.replace).toHaveBeenLastCalledWith({ kind: 'board', repo: 'r1', key: 'PAY-2' })
    expect(screen.queryByRole('group', { name: '回放' })).toBeNull()
    expect(screen.getByRole('button', { name: '▶ 回放' })).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: '位置' })).toHaveTextContent(/需求看板$/)
    expect(feedSeqs()).toBe(b.events.length)
    expect(screen.getAllByTestId('kt-lane').map((l) => l.dataset.status)).toEqual(b.nodes.filter((n) => n.status !== 'skipped' && n.rounds.length > 0).map((n) => n.status))
    expect(apiCalls.filter((c) => c.method !== 'GET')).toEqual([])
    expect(p.replace).toHaveBeenCalledTimes(2)
    expect(p.go).not.toHaveBeenCalled()
  })

  test('while it plays nothing can be done: no line to type, no step, no questions, no chip under a box to press', async () => {
    installFixtureRoutes()
    renderBoard()
    await loaded()
    const ask = { name: '代码生成：等你批 · 代码生成计划' }
    expect(screen.getAllByTestId('type-line')).toHaveLength(2) // 验收's step and 代码生成 · refund-api's lane
    expect(within(screen.getByTestId('kt-graph')).getByRole('button', ask)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '▶ 回放' }))
    expect(bar()).toBeInTheDocument()
    expect(screen.queryAllByTestId('type-line')).toHaveLength(0)
    expect(screen.queryByTestId('accept-step')).toBeNull()
    expect(screen.queryByRole('region', { name: '等回答的问题' })).toBeNull()
    expect(within(screen.getByTestId('kt-graph')).queryByRole('button', ask)).toBeNull()
    // at the start of the replay nothing had asked yet; scrubbed to the end the chips are back, and none can be pressed
    expect(screen.queryAllByTestId('kt-gate-pill')).toEqual([])
    fireEvent.click(within(bar()).getByRole('button', { name: '暂停' }))
    const scrub = within(bar()).getByRole('slider', { name: '回放进度' }) as HTMLInputElement
    fireEvent.change(scrub, { target: { value: scrub.max } })
    expect(screen.getAllByTestId('kt-gate-pill').every((x) => x.classList.contains('is-static'))).toBe(true)
    expect(screen.queryByRole('button', { name: '本机会话' })).toBeNull()
    fireEvent.click(within(bar()).getByRole('button', { name: '退出回放' }))
    expect(screen.getAllByTestId('type-line')).toHaveLength(2)
    expect(region('等回答的问题')).toBeInTheDocument()
  })

  test('every counter rewinds: Lean from its stage events, credits and citations not shown; an unfinished one ends 「放到现在了」', async () => {
    installFixtureRoutes()
    renderBoard()
    await loaded()
    fireEvent.click(screen.getByRole('button', { name: '▶ 回放' }))
    fireEvent.click(within(bar()).getByRole('button', { name: '暂停' }))
    fireEvent.change(within(bar()).getByRole('slider', { name: '回放进度' }), { target: { value: '0' } })
    const kpis = within(screen.getByRole('group', { name: '指标' }))
    expect(kpis.getByTestId('br-lean-counter')).toHaveTextContent('0做完')
    expect(kpis.getByText('credits').parentElement).toHaveTextContent('—回放时不算')
    expect(within(screen.getByTestId('kt-roster')).getByText('引用 —')).toBeInTheDocument()
    const slider = within(bar()).getByRole('slider', { name: '回放进度' })
    fireEvent.change(slider, { target: { value: slider.getAttribute('max') } })
    expect(bar()).toHaveTextContent('放到现在了：它还在进行')
  })

  test('?replay=1 opens the board replaying, scrolled to the graph', async () => {
    merged()
    renderBoard({ ...PAY2, replay: true })
    await loaded()
    expect(await screen.findByRole('group', { name: '回放' })).toBeInTheDocument()
    expect(within(bar()).getByRole('button', { name: '暂停' })).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: '位置' })).toHaveTextContent(/需求看板 · 回放$/)
    expect(screen.queryByRole('button', { name: '▶ 回放' })).toBeNull()
    const scroll = vi.mocked(HTMLElement.prototype.scrollIntoView)
    await waitFor(() => expect(scroll.mock.contexts).toContain(screen.getByTestId('kt-graph')))
  })

  test('?at= opens the replay stopped at that moment (a decision, a part of 价值流); 退出回放 drops the moment from the URL', async () => {
    merged()
    const b = fx.boardMerged()
    const at = b.events.find((e) => e.kind === 'approve' && e.node === 'application-design')!.at
    const p = renderBoard({ ...PAY2, from: 'item', at })
    await loaded()
    const rp = within(await screen.findByRole('group', { name: '回放' }))
    expect(rp.getByRole('button', { name: '播放' })).toBeInTheDocument()
    expect(bar()).toHaveTextContent(`${fmtWhen(at)} · 第`)
    expect(bar()).toHaveTextContent('停在这一刻：按 ▶ 接着放')
    expect(feedSeqs()).toBe(b.events.filter((e) => e.at <= at).length)
    // that very minute it is still in its second round: the fold ends the round (and turns the box green) a minute later
    expect(lane('application-design')).toHaveAttribute('data-status', 'fixing')
    expect(within(lane('application-design')).getByTestId('kt-rounds')).toHaveTextContent('第 2 轮')
    expect(p.replace).not.toHaveBeenCalled()
    fireEvent.click(rp.getByRole('button', { name: '退出回放' }))
    expect(p.replace).toHaveBeenLastCalledWith({ kind: 'board', repo: 'r1', key: 'PAY-2', from: 'item' })
    expect(feedSeqs()).toBe(b.events.length)
  })

  test('a merged requirement’s 验收: signed, who merged it, and 需求详情 from the card', async () => {
    merged()
    const p = renderBoard(PAY2)
    await loaded()
    const b = fx.boardMerged()
    const card = region('验收')
    expect(card).toHaveClass('is-signed')
    expect(card).toHaveTextContent('已合并')
    // who merged it, said as a person, never as a pseudonym code
    expect(within(card).getByRole('status')).toHaveTextContent(b.accept.done!.who as string)
    expect(within(card).getByRole('status')).not.toHaveTextContent(/P-[0-9A-F]{4}/)
    expect(within(card).getByRole('status')).toHaveTextContent(b.accept.done!.how)
    expect(within(card).queryByTestId('accept-step')).toBeNull()
    fireEvent.click(within(card).getByRole('button', { name: '需求详情 ↗' }))
    expect(p.go).toHaveBeenCalledWith({ kind: 'item', repo: 'r1', key: 'PAY-2', from: 'board' })
  })
})

describe('需求看板 · 谁做的、等谁、看得清', () => {
  const placeOf = (seq: number) => within(screen.getByTestId('kt-feed').querySelector(`[data-seq="${seq}"]`) as HTMLElement).queryAllByTestId('kt-ev-place').map((e) => e.textContent)

  /** PAY-3 handed over, then a question on the MR, the product's release approval there and an answer there. */
  function onTheMr(me: boolean): ReqBoard {
    const b = fx.board()
    b.person = { ...b.person, me, owner: me ? 'alice' : 'erin' }
    const t = Math.max(...b.events.map((e) => e.at))
    const ev = (seq: number, dt: number, kind: ReqBoard['events'][number]['kind'], from: string, to: string, text: string, node: string | null = null) =>
      ({ seq, at: t + dt, kind, from, to, text, detail: false, src: null, node: node as NodeId | null, run: null, round: null })
    b.relays = [{ to: 'runner-1' as RunKey, at: t + 10, sha: null, steps: [] }]
    b.events.push(ev(900, 10, 'handover', 'units', 'relay', '交给运行机'), ev(901, 20, 'question', 'relay', 'person', 'D-1 在 MR 上问人：验收', 'release'),
      ev(902, 30, 'approve', 'person', 'release', '产品在 MR 上批准了发布', 'release'), ev(903, 40, 'answer', 'person', 'relay', 'D-2 回答了：approve'),
      ev(904, 50, 'question', 'release', 'person', '停下问人：验收', 'release'), ev(905, 60, 'question', 'build-and-test', 'person', '停下问人：澄清', 'build-and-test'),
      ev(906, 70, 'answer', 'person', 'build-and-test', '澄清的问题回答了，接着做', 'build-and-test'))
    return b
  }

  test('event chips name who acted: the owner on their own way (你 only to them), 产品 or the MR for what happened on the MR', async () => {
    installFixtureRoutes(boardRoute(() => onTheMr(false)))
    renderBoard()
    await loaded()
    const claim = fx.board().events.find((e) => e.kind === 'claim')!.seq
    expect(placeOf(claim)).toEqual(['erin'])
    expect(placeOf(901)).toEqual(['交接', '产品'])
    expect(placeOf(902)).toEqual(['产品'])
    expect(placeOf(903)).toEqual(['MR', '交接'])
    // the runner's own questions after the hand-over are asked on the MR too, and answered there
    expect(placeOf(904)).toEqual(['产品'])
    expect(placeOf(905)).toEqual(['MR'])
    expect(placeOf(906)).toEqual(['MR'])
    expect(screen.getByTestId('kt-feed').querySelectorAll('[data-testid="kt-ev-place"]').length).toBeGreaterThan(3)
    expect([...screen.getByTestId('kt-feed').querySelectorAll('[data-testid="kt-ev-place"]')].map((e) => e.textContent)).not.toContain('你')
    cleanup()
    installFixtureRoutes(boardRoute(() => onTheMr(true)))
    renderBoard()
    await loaded()
    expect(placeOf(claim)).toEqual(['你'])
    expect(placeOf(902)).toEqual(['产品'])
    expect(placeOf(904)).toEqual(['产品'])
  })

  test('a wait that is over says whom it was on, by the same rule as the one going on (等产品, 等你)', async () => {
    const b = fx.board()
    const t0 = b.started_at!, t1 = t0 + 3600, t2 = t0 + 7200
    b.nodes = b.nodes.map((n) => (n.id !== 'code-generation/refund-api' ? n : {
      ...n,
      spans: [{ a: t0, b: t1, kind: 'work' as const, run: 'local' as RunKey }, { a: t1, b: t2, kind: 'wait' as const, run: 'local' as RunKey },
        { a: t2, b: null, kind: 'wait' as const, run: 'local' as RunKey }],
      history: [{ at: t0, status: 'running' as const, doing: '在做', who: null }, { at: t1, status: 'awaiting_approval' as const, doing: '等产品回答', who: 'product' as const },
        { at: t2, status: 'awaiting_approval' as const, doing: '会话里有一个工具调用等你批准', who: 'you' as const }],
    }))
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    renderBoard()
    await loaded()
    const row = lane('code-generation/refund-api')
    const waits = [...row.querySelectorAll('.kt-track-span.is-wait')]
    expect(waits.map((w) => w.getAttribute('title')?.split('：')[0])).toEqual(['等产品', '等你'])
    expect(waits.map((w) => w.classList.contains('is-other'))).toEqual([true, false])
    expect(row.querySelector('.kt-track')?.getAttribute('aria-label')).not.toContain('等人')
  })

  test('a wait on the owner said to the owner is never 「等 <their own name>」 (a replay of their own requirement)', async () => {
    const b = fx.board()
    b.nodes = b.nodes.map((n) => (n.id === 'code-generation/refund-api' ? { ...n, gates: n.gates.map((g) => ({ ...g, who: 'people' as const, line: null })) } : n))
    b.runs = b.runs.map((r) => ({ ...r, now: { ...r.now, who: 'people' as const } }))
    b.row.waits = { ...b.row.waits, who: 'people', do: 'answer', where: 'mr', line: null }
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    renderBoard()
    const graph = await loaded()
    expect(said(stageBox(graph, 'code-generation/refund-api').querySelectorAll('.kt-node-badge'))).toEqual(['等别人'])
    expect(graph).not.toHaveTextContent('等 alice')
  })
  test('a pushed hand-over without its label: the counter counts it as waiting on you, the button is 补上交接标签', async () => {
    const b = fx.board()
    const text = '推上去了，但 MR 上没有交接标签：什么也没交出去。看一眼 MR，再交接一次'
    Object.assign(b.row, { state: 'handed_over', state_label: '已交接' })
    b.row.waits = { who: 'you', text, line: null, raw: '', do: 'label_handoff', since: b.at - H, where: 'session' }
    b.accept.step = { text, who: '你', line: null, mr_url: 'https://gitlab.example.com/shop/shop-pay/-/merge_requests/50', session_url: null }
    b.questions = []
    planApproved(b)
    b.runs = b.runs.map((r) => ({ ...r, now: { ...r.now, status: 'done', doing: '交出去了', who: null, do: null } }))
    b.relays = [{ to: null, at: b.at - H, sha: null, steps: [{ key: 'handover', title: '交接', status: 'stalled', at: b.at - H, text, todo: '看一眼 MR，再交接一次' }] }]
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    renderBoard()
    await loaded()
    expect(screen.getByRole('group', { name: '指标' })).toHaveTextContent('没有在做的 · 1 个等你')
    expect(within(region('你要做的一步')).getByRole('link', { name: '补上交接标签 ↗' })).toHaveAttribute('href', b.accept.step.mr_url)
    expect(screen.queryByRole('link', { name: '去 MR ↗' })).toBeNull()
  })

  test('the introduction keeps every word whole: what a step does breaks only after a 、', async () => {
    const b = fx.board()
    Object.assign(b.row, { state: 'claimed', state_label: '已认领' })
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    renderBoard()
    const intro = await screen.findByTestId('br-board-intro')
    for (const w of ['把分支交出去', '核对、', '转问题', '读 Brain、', '会话里打一行']) expect(within(intro).getAllByText(w)[0]).toHaveClass('br-phrase')
  })

  test('the conveyor without the trace says what it shows, not a module name', async () => {
    const b = fx.board()
    Object.assign(b, { source: 'app', belt: [] })
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    renderBoard()
    await loaded()
    const belt = screen.getByTestId('kt-conveyor')
    expect(belt).toHaveTextContent('这里读不到运行分支的提交：brain-kit 没读出这个需求的记录')
    expect(belt).not.toHaveTextContent(/trace|模块/)
  })

  test('a run whose working directory is on the runner: the conveyor says that is why, and links the MR', async () => {
    const b = fx.board()
    Object.assign(b, { source: 'app', belt: [] })
    Object.assign(b.row, { where: 'runner', worktree: null, mr: { iid: 9, url: 'https://gitlab.example.com/x/-/merge_requests/9', state: 'opened' } })
    b.runs = [...b.runs, run('runner-1', 'runner', '运行机 第 1 次', { status: 'running', doing: 'AI 在做' }, { n: 1 })]
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    renderBoard()
    await loaded()
    const belt = screen.getByTestId('kt-conveyor')
    expect(belt).toHaveTextContent('运行机的工作目录不在这台机器上，这里读不到它的提交')
    expect(within(belt).getByRole('link', { name: '看 MR' })).toHaveAttribute('href', b.row.mr!.url)
  })

  test('a hand-over the runner has not started on: the conveyor says so with what it waits for, as the board says it, not that its directory is elsewhere', async () => {
    const b = fx.board()
    Object.assign(b, { source: 'app', belt: [] })
    Object.assign(b.row, { where: 'runner', worktree: null, waits: { ...b.row.waits, who: 'nobody', text: '签名服务在签接手合同' } })
    b.runs = [...b.runs, run('runner-1', 'runner', '运行机 第 1 次', { status: 'waiting', doing: '还没开始' }, { n: 1 })]
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    renderBoard()
    await loaded()
    const belt = screen.getByTestId('kt-conveyor')
    expect(belt).toHaveTextContent('运行机还没开始做，还没有它的提交：签名服务在签接手合同')
    expect(belt).not.toHaveTextContent('不在这台机器上')
  })

  test('the runner’s page of a hand-over it cannot read yet tells one story: no title twice, no 还没开工, no session to report', async () => {
    const b = fx.board()
    Object.assign(b, { source: 'app', belt: [], branch: null, events: [], sessions: [], credits: { ...b.credits, total: null } })
    Object.assign(b.row, { where: 'runner', worktree: null, title: b.row.key, state: 'handed_over', state_label: '已交接',
      waits: { ...b.row.waits, who: 'bridge', do: 'none', line: null, text: '签名服务在签接手合同' } })
    b.mr_desc = { ...b.mr_desc, text: '', first: null, error: '运行机还没开始做：它做完提交以后才有变更说明' }
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    renderBoard()
    const graph = await loaded()
    const top = document.querySelector('.kt-top') as HTMLElement
    expect(top.querySelector('.kt-run-title')).toBeNull()
    expect(top).toHaveTextContent('运行分支还读不到：运行机还没建工作区')
    expect(top).not.toHaveTextContent('还没有运行分支')
    expect(graph.querySelector('.kt-rail-label')).toHaveTextContent('运行分支 （运行机还读不到）')
    const kpis = within(screen.getByRole('group', { name: '指标' }))
    expect(kpis.getByText('credits').parentElement).toHaveTextContent('还没开过会话')
    expect(kpis.getByText('AI-DLC Lean').parentElement).toHaveTextContent('运行机还读不到 Lean 的记录')
    const accept = screen.getByTestId('br-accept')
    expect(accept).toHaveTextContent(new RegExp(`需求${b.row.key}\\s*变更说明`))
    expect(accept).toHaveTextContent('运行机还没开始做')
    expect(screen.getByTestId('br-board')).not.toHaveTextContent('还没开工')
    expect(kpis.getByText('AI-DLC Lean').parentElement!.querySelector('.kt-segs .is-next, .kt-segs .is-run')).toBeNull()
    // 切换需求 names it once too
    expect(optionText({ ...fx.overview().rows[0], key: 'PAY-9', title: 'PAY-9', state_label: '已交接', waits: { ...b.row.waits } })).toBe('PAY-9 · 已交接')
  })

  test('a repo done only here: its 发布 is Lean’s 发布审批 in the session, on the graph and below it, never 验收; its introduction says the same way', async () => {
    installFixtureRoutes(boardRoute(() => structuredClone(localOnly())))
    renderBoard()
    const graph = await loaded()
    expect(graph.querySelector('[data-phase="release"] .kt-phase-stages')).toHaveTextContent(/^发布审批$/)
    expect(graph.querySelector('svg')).not.toHaveTextContent('验收')
    // its sign-off is Lean's 发布审批 in the session: no 验收 card
    expect(screen.queryByRole('region', { name: '验收' })).toBeNull()
    const sign = region('发布审批')
    expect(within(sign).getByRole('heading', { level: 2 })).toHaveTextContent('发布审批')
    expect(sign.querySelector('.kt-sign-head')).toHaveTextContent('发布审批还没到发布审批')
    expect(sign.querySelector('.kt-sign-head')).not.toHaveTextContent('验收')
    cleanup()
    const b = fx.board()
    Object.assign(b.row, { state: 'claimed', state_label: '已认领', local_only: true })
    installFixtureRoutes(boardRoute(() => structuredClone(b)))
    renderBoard()
    const intro = await screen.findByTestId('br-board-intro')
    expect([...intro.querySelectorAll('.kt-intro-phase-head b')].map((x) => x.textContent)).toEqual(['0 认领', '1 本机 · Lean', '2 检查 · 发布', '3 回写'])
    expect(intro).toHaveTextContent('人只在判断点上出手：批准需求、设计和发布，回答问题。')
    expect(intro).not.toHaveTextContent('验收')
  })
})

// ---------------------------------------------------------------- 大需求拆成小需求 on the board

const NO_ACTS: FamilyActs = { split: null, claim: null, release: null, start: null, start_on: null, sync: null }
const famBase = (role: ReqFamily['role']): ReqFamily => ({
  role, parent: 'PAY-1', parent_title: '退款大需求', branch: 'ai/alice/PAY-1-big', unit: null, state: null, state_label: '', owner: null,
  depends_on: [], blocked_by: [], paths: [], contracts: [], children: [], progress: null, family_state: null, family_label: '', released: false,
  acts: { ...NO_ACTS }, source: 'brainkit', at: fx.board().at,
})
/** PAY-3, the web unit of PAY-1: in carol's hands (its run going), or nobody's yet */
function childBoard(state: 'in_progress' | 'unclaimed' = 'in_progress', acts: Partial<FamilyActs> = {}): ReqBoard {
  const b = fx.board()
  if (state === 'unclaimed') {
    b.row.state = 'unclaimed'
    b.row.state_label = '待认领'
    b.row.owner = null
  }
  b.row.family = {
    ...famBase('child'), unit: 'web', state, state_label: state === 'unclaimed' ? '待认领' : 'carol 在做', owner: state === 'unclaimed' ? null : 'carol',
    paths: ['web/**'], depends_on: [{ key: 'PAY-2', unit: 'api', state: 'merged', owner: 'alice', merged: true, text: 'PAY-2（api，已合并）' }],
    acts: { ...NO_ACTS, ...acts },
  }
  return b
}
function parentBoard(): ReqBoard {
  const b = fx.board()
  b.row.key = 'PAY-1'
  b.row.title = '退款大需求'
  b.row.family = {
    ...famBase('parent'), family_state: 'in_progress', family_label: '小需求在做', acts: { ...NO_ACTS, split: { ok: true, why: '' } },
    progress: { merged: 1, total: 3, unclaimed: 1, claimed: 0, in_progress: 1, closed: 0, text: '1/3 merged' },
    children: [
      { key: 'PAY-2', unit: 'api', title: '通过接口提交退款', state: 'merged', state_label: '已合并', owner: 'alice', depends_on: [], blocked_by: [] },
      { key: 'PAY-3', unit: 'web', title: '在订单页看到退款状态', state: 'in_progress', state_label: 'carol 在做', owner: 'carol', depends_on: ['PAY-2'], blocked_by: [] },
      { key: 'PAY-4', unit: 'jobs', title: '每晚自动对账', state: 'unclaimed', state_label: '待认领', owner: null, depends_on: [], blocked_by: [] },
    ],
  }
  const n = (commits: number, credits: number | null) => ({ stages_done: 5, verify_rounds: 2, verify_failed: 0, commits_bot: commits, commits_person: 0, blocked: 0,
    human_at_judgment: 2, human_outside_judgment: 0, decisions: 1, questions: 0, credits })
  b.rollup = {
    own: n(4, 10), total: n(15, null), unknown: ['PAY-4 credits'],
    children: [{ key: 'PAY-2', unit: 'api', merged: true, numbers: n(6, 8.2) }, { key: 'PAY-3', unit: 'web', merged: false, numbers: n(5, 3.1) }],
  }
  return b
}
const PAY1: BoardEntry = { repo: 'r1', key: 'PAY-1' }
const famCard = () => screen.getByTestId('fam-panel')
const topBar = () => within(document.querySelector('header.kt-top') as HTMLElement)

describe('需求看板 · 大需求拆成小需求', () => {
  test('a small one names its big one in the crumb and the top bar (that one’s board) with its unit; its family card sits under the conveyor, before its lanes', async () => {
    installFixtureRoutes(boardRoute(() => childBoard()))
    const p = renderBoard()
    await loaded()
    const top = topBar()
    expect(top.getByTestId('br-board-unit')).toHaveTextContent('单元 web')
    fireEvent.click(top.getByRole('button', { name: '大需求 PAY-1' }))
    expect(p.go).toHaveBeenLastCalledWith({ kind: 'board', repo: 'r1', key: 'PAY-1' })
    expect(screen.getByRole('navigation', { name: '位置' })).toHaveTextContent('交付总览›PAY-1›PAY-3›需求看板')
    fireEvent.click(crumb().getByRole('button', { name: 'PAY-1' }))
    expect(p.go).toHaveBeenCalledTimes(2)
    expect(famCard()).toHaveClass('kt-card')
    expect(screen.getByTestId('kt-conveyor').compareDocumentPosition(famCard()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(famCard().compareDocumentPosition(screen.getByTestId('kt-lanes')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(within(screen.getByRole('complementary', { name: '检查、问题和事件' })).queryByTestId('fam-panel')).toBeNull()
    expect(within(famCard()).getByRole('heading', { level: 2, name: 'PAY-1 的小需求 · 单元 web' })).toBeInTheDocument()
    expect(famCard()).toHaveTextContent('PAY-2（api，已合并）')
  })

  test('the actions are there too: 同步 posts and shows its answer; the board reads itself again', async () => {
    installFixtureRoutes({
      ...boardRoute(() => childBoard('in_progress', { sync: { ok: true, why: '' } })),
      [`POST ${API}/requirements/r1/PAY-3/sync`]: () => ({ ok: true, result: 'up_to_date', text: '已经是最新的', conflicts: [], problem: null, output: '' }),
    })
    const p = renderBoard()
    await loaded()
    const before = boardCalls().length
    fireEvent.click(within(famCard()).getByRole('button', { name: '同步' }))
    expect(await within(famCard()).findByTestId('fam-sync-result')).toHaveTextContent('已经是最新的')
    expect(apiCalls.filter((c) => c.method === 'POST')).toEqual([{ method: 'POST', path: `${API}/requirements/r1/PAY-3/sync`, body: {} }])
    await waitFor(() => expect(boardCalls().length).toBeGreaterThan(before))
    expect(p.reloadStatus).toHaveBeenCalled()
  })

  test('a small one nobody has: its own way, no 开需求; 开工 from its card, and the answer (打开会话) stays when the board becomes the run’s', async () => {
    const started: ChildStartResult = {
      ok: true, key: 'PAY-3', branch: 'ai/carol/PAY-3-web', worktree: '/Users/carol/.brain/worktrees/shop-pay/PAY-3', on: null, text: '开工了：PAY-3',
      blocked: [], can_on: null, problem: null, output: '', slot: 'brain-PAY-3-a1b2', url: '/chat?sid=brain-PAY-3-a1b2', session_error: '',
    }
    let snap = childBoard('unclaimed', { claim: { ok: true, why: '' }, start: { ok: true, why: '' } })
    installFixtureRoutes({
      [`GET ${API}/board/*`]: () => snap,
      [`POST ${API}/requirements/r1/PAY-3/start`]: () => {
        snap = childBoard('in_progress', { sync: { ok: true, why: '' } })
        return started
      },
    })
    renderBoard()
    const intro = await screen.findByTestId('br-board-intro')
    expect(within(intro).getByRole('heading', { name: 'PAY-3 待认领' })).toBeInTheDocument()
    expect(intro).toHaveTextContent('MR 交给大需求的负责人合并（小需求不交给运行机）')
    const way = within(intro).getByRole('list', { name: '一个小需求的路，从左到右' })
    expect(Array.from(way.querySelectorAll('.kt-intro-phase-head b')).map((b) => b.textContent)).toEqual(['0 认领', '1 开工', '2 做 · 同步', '3 交接', '4 合并'])
    expect(screen.queryByRole('button', { name: '去开需求，开工' })).toBeNull()
    expect(screen.queryByRole('button', { name: '去开工' })).toBeNull()
    // the card comes first, with 认领 and 开工
    expect(famCard().compareDocumentPosition(intro) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(within(famCard()).getByRole('button', { name: '认领' })).toHaveClass('is-primary')
    fireEvent.click(within(famCard()).getByRole('button', { name: '开工' }))
    await loaded()   // the run's board now: graph, counters, the card under the conveyor
    const out = within(famCard()).getByTestId('fam-start-result')
    expect(screen.queryByTestId('br-board-intro')).toBeNull()
    expect(out).toHaveTextContent('开工了：PAY-3')
    expect(within(out).queryByRole('button', { name: '需求看板' })).toBeNull()   // it is this one
    fireEvent.click(within(out).getByRole('button', { name: '打开会话' }))
    expect(navigations).toEqual(['/chat?sid=brain-PAY-3-a1b2'])
  })

  test('a big one split into small ones: the small ones as lanes with the roll-up and the trace numbers, under the conveyor, before its own lanes', async () => {
    installFixtureRoutes(boardRoute(parentBoard))
    const p = renderBoard(PAY1)
    await loaded()
    const card = screen.getByRole('region', { name: '拆成的小需求' })
    expect(card).toHaveClass('kt-card')
    expect(within(card).getByTestId('fam-merged')).toHaveAccessibleName('已合并 1/3')
    expect(within(card).getAllByTestId('fam-child').map((l) => `${l.dataset.key} ${l.dataset.state}`)).toEqual(['PAY-2 merged', 'PAY-3 in_progress', 'PAY-4 unclaimed'])
    expect(within(card).getAllByTestId('fam-child')[0]).toHaveClass('kt-lane')
    expect(within(card).getByRole('group', { name: '整个大需求加起来' })).toHaveTextContent('提交15')
    expect(within(card).getByTestId('fam-unknown')).toHaveTextContent('PAY-4 credits')
    const belt = screen.getByTestId('kt-conveyor')
    expect(belt.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(card.compareDocumentPosition(screen.getByTestId('kt-lanes')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // the top bar says how far, and jumps there
    const scroll = vi.mocked(HTMLElement.prototype.scrollIntoView)
    scroll.mockClear()
    fireEvent.click(topBar().getByRole('button', { name: '大需求 · 已合并 1/3' }))
    expect(scroll.mock.contexts[0]).toBe(card)
    fireEvent.click(within(card).getAllByTestId('fam-child')[2])
    expect(p.go).toHaveBeenLastCalledWith({ kind: 'board', repo: 'r1', key: 'PAY-4' })
    expect(within(card).getByRole('button', { name: '拆成小需求' })).toBeEnabled()
  })

  test('a big one not split yet: what comes next under the counters; 拆成小需求 previews first', async () => {
    const b = fx.board()
    b.row.key = 'PAY-1'
    b.row.family = { ...famBase('big'), acts: { ...NO_ACTS, split: { ok: true, why: '' } } }
    const preview: SplitResult = {
      ok: true, dry_run: true, parent: 'PAY-1', branch: 'ai/alice/PAY-1-big', units_sha: '31369169', design_sha: '4a037c33', warnings: [], problems: [], output: '',
      text: '会拆成 2 个小需求。',
      actions: ['api', 'web'].map((unit) => ({ unit, kind: 'new', kind_label: '新登记（待认领）', key: null, title: unit, paths: [`${unit}/**`], depends_on: [], contracts: [], why: [], attention: false })),
    }
    installFixtureRoutes({ ...boardRoute(() => b), [`POST ${API}/requirements/r1/PAY-1/split`]: () => preview })
    renderBoard(PAY1)
    await loaded()
    const card = screen.getByRole('region', { name: '大需求' })
    expect(screen.getByRole('group', { name: '指标' }).compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(card.compareDocumentPosition(screen.getByTestId('kt-graph')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(topBar().getByRole('button', { name: '大需求 · 还没拆' })).toBeInTheDocument()
    fireEvent.click(within(card).getByRole('button', { name: '拆成小需求' }))
    expect(await within(card).findByRole('table', { name: '会拆成这些小需求' })).toBeInTheDocument()
    expect(apiCalls.filter((c) => c.method === 'POST').map((c) => c.body)).toEqual([{ apply: false }])
  })

  test('while it replays the family card offers nothing; after the replay its actions are back', async () => {
    installFixtureRoutes(boardRoute(() => childBoard('in_progress', { sync: { ok: true, why: '' }, release: { ok: true, why: '' }, start_on: 'PAY-2' })))
    renderBoard()
    await loaded()
    expect(within(famCard()).getByRole('group', { name: '小需求的操作' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '▶ 回放' }))
    expect(screen.getByRole('group', { name: '回放' })).toBeInTheDocument()
    expect(within(famCard()).queryByRole('group', { name: '小需求的操作' })).toBeNull()
    expect(within(famCard()).getAllByRole('button').map((b) => b.textContent)).toEqual(['PAY-1'])
    fireEvent.click(within(screen.getByRole('group', { name: '回放' })).getByRole('button', { name: '退出回放' }))
    expect(within(famCard()).getByRole('button', { name: '同步' })).toBeEnabled()
    expect(apiCalls.some((c) => c.method === 'POST')).toBe(false)
  })

  test('a requirement outside a family: no family card, no family pill', async () => {
    installFixtureRoutes()
    renderBoard()
    await loaded()
    expect(screen.queryByTestId('fam-panel')).toBeNull()
    expect(screen.queryByTestId('br-board-unit')).toBeNull()
    expect(topBar().queryByRole('button', { name: /大需求/ })).toBeNull()
  })
})
