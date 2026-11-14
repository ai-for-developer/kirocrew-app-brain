import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { fx } from '../test/data'
import { API, installFixtureRoutes } from '../test/routes'
import { pay16, pay9 } from '../test/stageBoards'
import { apiCalls } from '../test/stubs/app-sdk'
import type { BoardEntry } from '../nav'
import type { FileView, NodeOutput, NodeTurns, OutFile, ReqBoard, TurnView } from '../types'
import type { ViewProps } from '../views/props'
import { BoardPage } from './BoardPage'

const PAY9: BoardEntry = { repo: 'r1', key: 'PAY-9' }
const RELEASE = 'aidlc-docs/261111-refund-sms/operations/release.md'
const [R1, R3, R4] = ['a', 'c', 'd'].map((x) => x.repeat(40))

const file = (over: Partial<OutFile>): OutFile => ({
  path: RELEASE, output: true, status: 'M', added: 6, removed: 4, ref: R4, pin: null, base: R3, base_round: 3,
  vs: [{ round: 3, ref: R3 }, { round: 1, ref: R1 }], why: null, ...over,
})

const listed: NodeOutput = {
  node: 'release' as NodeOutput['node'], round: 4, run: 'runner-1' as NodeOutput['run'], branch: 'ai/alice/PAY-9-refund-sms', head: R4, more: 0, why: null,
  files: [file({}), file({ path: 'runs/PAY-9/MR.md', output: false, added: 41, removed: 0, base_round: null, vs: [] }),
    file({ path: 'services/api/notify.py', output: false, ref: null, vs: [], why: 'Lean 记下了这一版（指纹 1234abcd），这台机器的提交里还没有它：等运行机把它推上来，取回以后再看' })],
}

const DOC = '# Release: 退款通知短信\n\n## 检查\n\n- [x] pytest\n- [ ] 产品验收\n  - D-3 在 MR 上\n'

const KICKOFF = '这是需求 PAY-9 的本地开发会话。分支 ai/alice/PAY-9-refund-sms，Lean intent 261111-refund-sms。\n有问题一次问完，每题带推荐答案。'
const at = (hm: string) => {
  const d = new Date(pay9().at * 1000)
  const [h, m, s = 0] = hm.split(':').map(Number)
  d.setHours(h, m, s, 0)
  return d.getTime() / 1000
}
const call = (hm: string, tool: string, detail: string, inRound = true) => ({ at: at(hm), tool, detail, in_round: inRound })
const turn = (over: Partial<TurnView>): TurnView => ({
  n: 1, at: at('10:05:20'), until: at('10:20'), slot: 'brain-PAY-9-7f2c', url: '/chat?sid=brain-PAY-9-7f2c',
  task: { kind: 'app', cause: '开工消息，只有编号', text: KICKOFF }, reply: '先读了 AGENTS.md。', live: false, calls: [], more_calls: 0, also: [], ...over,
})
/** application-design's round 1 on this machine: the kickoff, then what alice typed */
const designTurns: NodeTurns = {
  node: 'application-design' as NodeTurns['node'], round: 1, run: 'local' as NodeTurns['run'], why_not: null, why: null,
  meta: { took: 1920, turns: 2, calls: 25 },
  turns: [
    turn({ calls: [call('10:05:31', '读文件', 'AGENTS.md', false), call('10:06:02', '跑命令', 'python3 .kiro/aidlc-lean/aidlc_lean.py stage start application-design')] }),
    turn({ n: 2, at: at('10:21'), until: at('10:52'), task: { kind: 'person', chars: 64, url: '/chat?sid=brain-PAY-9-7f2c' },
      reply: '需求和设计都写好了，等你批：\n\n- `requirements.md`：7 条验收标准\n\n请在会话里打这一行：`approve INCEPTION 3fa2c1d0`',
      calls: [call('10:24:05', '写文件', 'aidlc-docs/261111-refund-sms/inception/application-design/application-design.md')], more_calls: 21,
      also: ['需求分析', '拆分单元'] }),
  ],
}
const releaseTurns: NodeTurns = {
  node: 'release' as NodeTurns['node'], round: 1, run: 'runner-1' as NodeTurns['run'], turns: [], why: null,
  meta: { took: 4560, turns: null, calls: null },
  why_not: { title: '这一轮是运行机做的（RUN-PAY-9-1）', missing: '它的回复、接到的消息和工具调用留在运行机上，这台机器读不到。',
    here: ['它的 2 次提交（「产出文件」里都能看）', '它在 MR 上问的 D-2'], do: '要看它的回复和工具调用：请管运行机的人在运行机上的 Brain 交付里打开 PAY-9。' },
}

function routes(board: () => ReqBoard) {
  installFixtureRoutes({
    [`GET ${API}/board/*`]: () => board(),
    [`GET ${API}/outputs/*`]: (_b, path) => {
      const q = new URLSearchParams(path.split('?')[1])
      if (path.includes('/turns?')) return q.get('node') === 'release' ? { ...releaseTurns, round: Number(q.get('round') ?? 1) } : designTurns
      if (!path.includes('/file?')) return { ...listed, round: Number(q.get('round') ?? 4) }
      const v: FileView = { path: q.get('path') ?? '', view: q.get('view') as FileView['view'], at: q.get('ref') ?? '', base: q.get('base'), pinned: false,
        lang: (q.get('path') ?? '').endsWith('.md') ? 'markdown' : 'python', asked: q.get('ref') === R4 ? { kind: 'RELEASE', sha8: '73b5061f' } : null,
        binary: false, truncated: false, size: 10,
        text: q.get('view') === 'diff' ? `diff --git a/x b/x\n@@ -1 +1 @@\n-old ${q.get('base')?.charAt(0)}\n+new\n` : DOC }
      return v
    },
  })
}

function renderBoard(entry: BoardEntry, board: () => ReqBoard = pay9) {
  routes(board)
  const p: ViewProps = { status: fx.status(), go: vi.fn(), replace: vi.fn(), reloadStatus: vi.fn(), setStatus: vi.fn(), pollMs: 60_000 }
  render(<BoardPage entry={entry} {...p} />)
  return p
}

const fileCalls = () => apiCalls.filter((c) => c.path.includes('/outputs/r1/PAY-9/file?')).map((c) => new URLSearchParams(c.path.split('?')[1]))

describe('产出', () => {
  test('a lane’s 看产出 and the top bar’s 产出 open the drawer in place of the current entry', async () => {
    const p = renderBoard(PAY9)
    await screen.findByTestId('kt-graph')
    const lane = screen.getAllByTestId('kt-lane').find((l) => l.dataset.node === 'release') as HTMLElement
    fireEvent.click(within(lane).getByTestId('kt-lane-out'))
    expect(p.replace).toHaveBeenLastCalledWith({ kind: 'board', repo: 'r1', key: 'PAY-9', out: { node: 'release' } })
    // the app's replace only rewrites the address: the board opens the drawer itself
    expect((await screen.findByTestId('out-pane')).dataset.node).toBe('release')
    fireEvent.click(screen.getByRole('button', { name: '关掉产出' }))
    expect(p.replace).toHaveBeenLastCalledWith({ kind: 'board', repo: 'r1', key: 'PAY-9' })
    expect(screen.queryByTestId('out-drawer')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '产出' }))
    // the stage the run waits at
    expect(p.replace).toHaveBeenLastCalledWith({ kind: 'board', repo: 'r1', key: 'PAY-9', out: { node: 'release' } })
    expect(await screen.findByTestId('out-pane')).toBeInTheDocument()
    expect(p.go).not.toHaveBeenCalled()
  })

  test('a gate’s 看文档 opens the document it asks for', async () => {
    const p = renderBoard({ repo: 'r1', key: 'PAY-16' }, pay16)
    const graph = await screen.findByTestId('kt-graph')
    const doc = Array.from(graph.querySelectorAll('[data-testid="kt-gate-pill"]')).find((g) => g.querySelector('text')?.textContent === '看文档') as Element
    fireEvent.click(doc)
    const at = (p.replace as ReturnType<typeof vi.fn>).mock.calls.at(-1)?.[0]
    expect(at.out.file).toMatch(/requirements\.md$/)
    expect((await screen.findByTestId('out-pane')).dataset.node).toBe(at.out.node)
  })

  test('the drawer: the steps, the rounds, what the round was about, its files; a document rendered, the version asked for marked', async () => {
    renderBoard({ ...PAY9, out: { node: 'release', round: 4 } })
    const pane = await screen.findByTestId('out-pane')
    const drawer = screen.getByRole('dialog', { name: '每一步的产出' })
    expect(within(drawer).getAllByTestId('out-step').map((s) => s.textContent)).toEqual(
      ['1需求分析', '2应用设计', '3拆分单元', '4代码生成 · api', '5代码生成 · web', '6构建测试', '7审查', '8发布'])
    expect(within(pane).getByTestId('out-rounds').textContent).toBe('第几轮第 1 轮退回第 2 轮代码又改了第 3 轮代码又改了第 4 轮等验收')
    expect(within(pane).getByTestId('out-banner').textContent).toContain('✋ 这是请产品批的版本')
    expect(within(pane).getByTestId('out-scope').textContent).toContain('运行机 第 1 次（RUN-PAY-9-1）')
    expect(within(pane).getAllByTestId('out-file').map((f) => f.textContent)).toEqual(
      ['Moperations/release.md+6 −4', 'Mruns/PAY-9/MR.md+41', 'Mservices/api/notify.py+6 −4'])
    const md = await within(pane).findByTestId('out-md')
    expect(within(md).getByRole('heading', { level: 3 }).textContent).toBe('Release: 退款通知短信')
    expect(within(md).getAllByRole('checkbox').map((c) => (c as HTMLInputElement).checked)).toEqual([true, false])
    expect(md.querySelector('li li')?.textContent).toBe('D-3 在 MR 上')       // a list inside a list
    expect(within(pane).getByTestId('out-asked').textContent).toBe('✋ 就是请批的那一版 · 73b5061f')
    expect(within(pane).getByTestId('out-at').textContent).toBe('第 4 轮的版本 · 提交 ddddddd')
  })

  test('改动 compares with the round before, 和第 1 轮比 with the first', async () => {
    renderBoard({ ...PAY9, out: { node: 'release', round: 4 } })
    const pane = await screen.findByTestId('out-pane')
    fireEvent.click(await within(pane).findByRole('button', { name: '改动（和第 3 轮比）' }))
    await waitFor(() => expect(fileCalls().at(-1)?.get('base')).toBe(R3))
    expect(fileCalls().at(-1)?.get('view')).toBe('diff')
    fireEvent.click(within(within(pane).getByTestId('out-vs')).getByRole('button', { name: '和第 1 轮比' }))
    await waitFor(() => expect(fileCalls().at(-1)?.get('base')).toBe(R1))
    const code = await within(pane).findByTestId('out-code')
    await waitFor(() => expect(code.querySelector('li.is-del')?.textContent).toBe('-old a'))
    expect(code.querySelector('li.is-add')?.textContent).toBe('+new')
    expect(within(pane).getByRole('button', { name: '改动（和第 1 轮比）' })).toHaveAttribute('aria-pressed', 'true')
  })

  test('a file this machine has no commit of says why and what to do; nothing is asked for it', async () => {
    renderBoard({ ...PAY9, out: { node: 'release', round: 4, file: 'services/api/notify.py' } })
    const pane = await screen.findByTestId('out-pane')
    expect((await within(pane).findByTestId('out-why')).textContent).toContain('等运行机把它推上来')
    expect(fileCalls().some((q) => q.get('path') === 'services/api/notify.py')).toBe(false)
  })

  test('a round pill, a step and a file move the drawer in place; Esc closes it before anything else', async () => {
    const p = renderBoard({ ...PAY9, out: { node: 'release', round: 4 } })
    const pane = await screen.findByTestId('out-pane')
    fireEvent.click((await within(pane).findAllByTestId('out-file'))[1])
    expect(p.replace).toHaveBeenLastCalledWith({ kind: 'board', repo: 'r1', key: 'PAY-9', out: { node: 'release', round: 4, file: 'runs/PAY-9/MR.md' } })
    expect(await screen.findByText('runs/PAY-9/MR.md', { selector: '.kt-fview-path' })).toBeInTheDocument()
    fireEvent.click(within(within(pane).getByTestId('out-rounds')).getByRole('button', { name: /第 1 轮/ }))
    expect(p.replace).toHaveBeenLastCalledWith({ kind: 'board', repo: 'r1', key: 'PAY-9', out: { node: 'release', round: 1 } })
    await waitFor(() => expect(screen.getByTestId('out-scope').textContent).toMatch(/^第 1 轮/))
    fireEvent.click(screen.getAllByTestId('out-step')[1])
    expect(p.replace).toHaveBeenLastCalledWith({ kind: 'board', repo: 'r1', key: 'PAY-9', out: { node: 'application-design' } })
    expect((await screen.findByTestId('out-pane')).dataset.node).toBe('application-design')
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(p.replace).toHaveBeenLastCalledWith({ kind: 'board', repo: 'r1', key: 'PAY-9' })
    expect(screen.queryByTestId('out-drawer')).toBeNull()
    expect(p.go).not.toHaveBeenCalled()
  })

  test('a local round: 它的回复 is its last turn’s reply, 接到的任务 the app’s message as sent and a person’s only by its length, 做了什么 every call', async () => {
    const p = renderBoard({ ...PAY9, out: { node: 'application-design', round: 1, tab: 'reply' } })
    const pane = await screen.findByTestId('out-pane')
    const reply = await within(pane).findByTestId('out-reply')
    expect(reply.textContent).toContain('需求和设计都写好了，等你批')
    expect(within(reply).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['requirements.md：7 条验收标准'])
    expect(within(pane).getByTestId('out-reply-meta').textContent).toBe(
      '本机会话 brain-PAY-9-7f2c · 这一轮的最后一个回合（第 2 回合 · 10:21–10:52）的回复 · 这一轮里 25 次工具调用 · 这一回合也做了：需求分析、拆分单元')
    expect(within(pane).getAllByTestId('out-tab').map((x) => x.textContent)).toEqual(['产出文件3', '它的回复', '接到的任务2', '做了什么25'])
    fireEvent.click(within(pane).getByRole('button', { name: '第 1 回合' }))
    expect(within(pane).getByTestId('out-reply').textContent).toBe('先读了 AGENTS.md。')

    fireEvent.click(within(pane).getByRole('tab', { name: /接到的任务/ }))
    expect(p.replace).toHaveBeenLastCalledWith({ kind: 'board', repo: 'r1', key: 'PAY-9', out: { node: 'application-design', round: 1, tab: 'task' } })
    const tasks = await within(pane).findByTestId('out-tasks')
    expect(within(tasks).getAllByText(/^第 \d 回合/).map((h) => h.textContent)).toEqual(
      ['第 1 回合 · 10:05 · app 发的（开工消息，只有编号）', '第 2 回合 · 10:21 · 你在会话里打的'])
    expect(within(tasks).getByTestId('out-task-text').textContent).toBe(KICKOFF)
    expect(within(tasks).getByTestId('out-task-person').textContent).toBe('你打的话（64 字）不在这里显示：看板上不放有人写的话。在会话里看 ↗')

    fireEvent.click(within(pane).getByRole('tab', { name: /做了什么/ }))
    const calls = await within(pane).findByTestId('out-calls')
    const rows = Array.from(calls.querySelectorAll('li')).map((li) => [li.className, li.textContent])
    expect(rows).toEqual([
      ['kt-calls-turn', '10:05:20第 1 回合app 发的（开工消息，只有编号）'],
      ['is-out', '10:05:31读文件AGENTS.md'],
      ['', '10:06:02跑命令python3 .kiro/aidlc-lean/aidlc_lean.py stage start application-design'],
      ['kt-calls-turn', '10:21:00第 2 回合你在会话里打的'],
      ['', '10:24:05写文件aidlc-docs/261111-refund-sms/inception/application-design/application-design.md'],
      ['', '…还有 21 次没列出来'],
    ])
  })

  test('a runner round: each session tab is the card that says where its session is and what is here instead', async () => {
    renderBoard({ ...PAY9, out: { node: 'release', round: 1, tab: 'calls' } })
    const pane = await screen.findByTestId('out-pane')
    const card = await within(pane).findByTestId('out-whynot')
    expect(within(card).getByRole('heading', { level: 4 }).textContent).toBe('这一轮是运行机做的（RUN-PAY-9-1）')
    expect(within(card).getAllByRole('listitem').map((li) => li.textContent)).toEqual(
      ['它的 2 次提交（「产出文件」里都能看）', '它在 MR 上问的 D-2', 'Lean 记下的这一轮：13:25 开始、14:41 产品退回（D-2）'])
    expect(card.querySelector('.do')?.textContent).toBe('做什么要看它的回复和工具调用：请管运行机的人在运行机上的 Brain 交付里打开 PAY-9。')
    expect(within(pane).getAllByTestId('out-tab').map((x) => x.textContent)).toEqual(['产出文件3', '它的回复', '接到的任务', '做了什么'])
    fireEvent.click(within(pane).getByRole('tab', { name: '它的回复' }))
    expect(await within(pane).findByTestId('out-whynot')).toBeInTheDocument()
  })

  test('the tab stays when a round pill or a step moves the drawer', async () => {
    const p = renderBoard({ ...PAY9, out: { node: 'release', round: 4, tab: 'reply' } })
    const pane = await screen.findByTestId('out-pane')
    fireEvent.click(within(within(pane).getByTestId('out-rounds')).getByRole('button', { name: /第 1 轮/ }))
    expect(p.replace).toHaveBeenLastCalledWith({ kind: 'board', repo: 'r1', key: 'PAY-9', out: { node: 'release', round: 1, tab: 'reply' } })
    fireEvent.click(screen.getAllByTestId('out-step')[1])
    expect(p.replace).toHaveBeenLastCalledWith({ kind: 'board', repo: 'r1', key: 'PAY-9', out: { node: 'application-design', tab: 'reply' } })
  })
})
