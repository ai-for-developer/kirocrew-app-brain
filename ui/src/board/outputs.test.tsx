import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { fx } from '../test/data'
import { API, installFixtureRoutes } from '../test/routes'
import { pay16, pay9 } from '../test/stageBoards'
import { apiCalls } from '../test/stubs/app-sdk'
import type { BoardEntry } from '../nav'
import type { FileView, NodeOutput, OutFile, ReqBoard } from '../types'
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

function routes(board: () => ReqBoard) {
  installFixtureRoutes({
    [`GET ${API}/board/*`]: () => board(),
    [`GET ${API}/outputs/*`]: (_b, path) => {
      const q = new URLSearchParams(path.split('?')[1])
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
})
