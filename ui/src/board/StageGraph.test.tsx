import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { fx } from '../test/data'
import { pay16, pay9 } from '../test/stageBoards'
import type { BoardEvent, NodeId, ReqBoard, RunKey } from '../types'
import { diffBoards, effects } from './diff'
import { ClockContext } from './hooks'
import { ORCH, RUN, TESTS, stageLayout, stageShape } from './layout'
import { StageGraph, stageInput, type StageGraphProps } from './StageGraph'

/** The graph of *b* as the 1600 px app draws it (*card*: its graph card's width). */
function draw(b: ReqBoard, over: Partial<StageGraphProps> = {}, card = 1558) {
  const input = stageInput(b)
  const props: StageGraphProps = {
    board: b, lay: stageLayout(input, stageShape(card, input)), focus: null, onNode: vi.fn(), flashes: {}, reducedMotion: true,
    feed: { current: null }, onSession: vi.fn(), onAct: vi.fn(), ...over,
  }
  render(<ClockContext.Provider value={{ at: b.at, receivedAt: Date.now() }}><StageGraph {...props} /></ClockContext.Provider>)
  return { ...props, graph: screen.getByTestId('kt-graph') }
}
const box = (graph: HTMLElement, id: string) => graph.querySelector<SVGGElement>(`g.kt-node[data-node="${id}"]`)!
const band = (graph: HTMLElement, key: string) => graph.querySelector(`[data-phase="${key}"]`)!
const shown = (els: Iterable<Element>) => Array.from(els).map((e) => e.querySelector('text')?.textContent ?? '')
const badges = (graph: HTMLElement, id: string) => Array.from(box(graph, id).querySelectorAll('.kt-node-badge')).map((b) => b.textContent)
const line = (graph: HTMLElement, id: string) => box(graph, id).querySelector('.kt-node-sub')!.textContent

describe('阶段图 · PAY-9 (交给了运行机，产品退回过一次发布)', () => {
  test('the AI-DLC phases are bands left to right; the one with nothing on the way folds and says why; a band’s foot says what it skipped', () => {
    const { graph } = draw(pay9())
    expect(Array.from(graph.querySelectorAll('[data-phase]')).map((e) => e.getAttribute('data-phase')))
      .toEqual(['init', 'ideation', 'inception', 'construction', 'release', 'operations'])
    const folded = within(graph).getAllByTestId('kt-phase-folded')
    expect(folded.map((f) => f.getAttribute('data-phase'))).toEqual(['ideation'])
    expect(folded[0]).toHaveTextContent('1 构思这次不需要')
    expect(band(graph, 'inception')).toHaveTextContent('2 启动')
    expect(band(graph, 'inception')).toHaveTextContent('跳过：逆向工程、用户故事')
    expect(band(graph, 'construction')).toHaveTextContent('每个单元都跳过了功能、非功能、基础设施设计')
    expect(band(graph, 'operations')).toHaveTextContent('运维 7 个阶段：这次不需要')
  })

  test('a box per stage a run did, with its AI-DLC number and what it hands in; skipped stages have none; 3 构建 has a row per unit', () => {
    const { graph } = draw(pay9())
    expect(Array.from(graph.querySelectorAll('g.kt-node')).map((n) => n.getAttribute('data-node'))).toEqual([
      'requirements', 'application-design', 'units', 'code-generation/api', 'code-generation/web', 'build-and-test', 'review', 'release', 'reflect',
    ])
    expect(box(graph, 'requirements')).toHaveTextContent('需求分析2.3requirements.md')
    expect(box(graph, 'code-generation/web')).toHaveTextContent('代码生成3.5web 的代码生成计划')
    expect(within(graph).getByText('2 个单元')).toBeInTheDocument()
    expect(Array.from(graph.querySelectorAll('.kt-unit-label')).map((u) => u.textContent)).toEqual(['api', 'web'])
  })

  test('each box says who did it and how long it took, or whom it waits on; 第 N 轮 after more than one round; a decided gate is a badge', () => {
    const { graph } = draw(pay9())
    expect(line(graph, 'requirements')).toBe('✓ 本机 · 0:28')
    expect(line(graph, 'application-design')).toBe('✓ 本机 · 0:32')
    expect(line(graph, 'code-generation/api')).toBe('✓ 运行机 · 1:09')
    expect(line(graph, 'build-and-test')).toBe('✓ 运行机 · 3/3')
    expect(line(graph, 'release')).toBe('运行机 · 等产品')
    expect(line(graph, 'reflect')).toBe('等合并以后')
    expect(badges(graph, 'requirements')).toEqual(['✓ 你批了'])
    expect(badges(graph, 'application-design')).toEqual(['✓ 你批了'])
    expect(badges(graph, 'build-and-test')).toEqual(['第 5 轮'])
    expect(badges(graph, 'release')).toEqual(['第 4 轮', '等产品'])
    expect(badges(graph, 'units')).toEqual([])
    // the product's wait is calm: no beacon, the box says how long it has waited on the MR question
    expect(box(graph, 'release')).toHaveClass('is-other')
    expect(box(graph, 'release').querySelector('.kt-node-halo')).toBeNull()
    expect(box(graph, 'release').querySelector('.kt-node-task')).toHaveTextContent('D-3 · 已等 1 分钟')
  })

  test('under 发布: the round the product sent back, then the acceptance asked now, calm since it is not your turn; it takes you to the step', () => {
    const { graph, onAct } = draw(pay9())
    const rows = within(graph).getAllByTestId('kt-gate-row')
    expect(rows.map((r) => r.getAttribute('data-node'))).toEqual(['release'])
    const pills = within(rows[0]).getAllByTestId('kt-gate-pill')
    expect(shown(pills)).toEqual(['✗ 退回 · D-2', '等产品验收 · D-3'])
    expect(pills[0]).toHaveClass('is-back', 'is-static')
    expect(pills[1]).toHaveClass('is-other')
    fireEvent.click(within(graph).getByRole('button', { name: '发布：等产品验收 · D-3' }))
    expect(onAct).toHaveBeenCalledWith('release', 'accept')
  })

  test('under the owner, this time’s runs: 本机会话 done, the hand-over’s steps, 运行机 第 1 次 waiting on the product; then the Brain', () => {
    const { graph } = draw(pay9())
    expect(within(graph).getByTestId('kt-orch')).toHaveTextContent('你 · alice负责人')
    const roster = within(graph).getByTestId('kt-roster')
    expect(roster).toHaveTextContent('这次谁在做 · 2 次运行')
    const rows = within(roster).getAllByTestId('kt-roster-row')
    expect(rows.map((r) => r.getAttribute('class'))).toEqual(['kt-run-row is-done', 'kt-run-row is-relay is-relay', 'kt-run-row is-other'])
    expect(rows[0]).toHaveTextContent('本机会话 · 做完 · 11:05 交出去')
    expect(shown(rows.slice(1))).toEqual(['交接 → 桥 → 签名 ✓', '运行机 第 1 次 · 等产品'])
    expect(roster).toHaveTextContent('RUN-PAY-9-1')
    expect(Array.from(roster.querySelectorAll('.kt-roster-item text')).map((t) => t.textContent)).toEqual(['载入 6 条', '引用 2 条', 'P-7', 'T-12'])
  })

  test('the rail: a dot per run with its commits, the hand-over’s pill between them, the check ring at its end', () => {
    const { graph } = draw(pay9())
    const dots = within(graph).getAllByTestId('kt-rail-dot')
    expect(dots.map((d) => [d.getAttribute('data-run'), d.querySelector('.kt-rail-n')?.textContent])).toEqual([['local', '×3'], ['runner-1', '×9']])
    const relay = within(graph).getByTestId('kt-relay')
    expect(relay).toHaveAccessibleName('交接：交接 ✓ · 桥 ✓ · 签名 ✓')
    expect(relay.querySelector('text')).toHaveTextContent('交接 ✓ · 桥 ✓ · 签名 ✓ · 11:07')
    expect(within(graph).getByTestId('kt-station')).toHaveAccessibleName('检查 3/3')
    expect(within(graph).getByTestId('kt-rail')).toHaveTextContent('运行分支 ai/alice/PAY-9-refund-sms')
  })

  test('a dashed line runs from the run that waits to the box it is on, calm because it waits on the product', () => {
    const { graph } = draw(pay9())
    const cmds = within(graph).getAllByTestId('kt-cmd')
    expect(cmds.map((c) => c.getAttribute('data-run'))).toEqual(['runner-1'])
    expect(cmds[0].querySelector('path')).toHaveClass('kt-cmd', 'is-other')
    expect(cmds[0]).toHaveTextContent('运行机 → 发布：等产品')
  })

  test('a box’s card lists its rounds, who did each and how it ended; 看 MR for the question on the MR; 打开会话 only for a session on this machine', async () => {
    const { graph, onSession } = draw(pay9())
    fireEvent.mouseEnter(box(graph, 'release'))
    const card = await screen.findByRole('dialog', { name: '发布做了什么' })
    expect(within(card).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      '第 1 轮运行机 · 13:25 · 产品退回（D-2）',
      '第 2 轮运行机 · 15:06 · 请批以后代码又改了（2e8d1f6「修好 CI：api 的 lint」）',
      '第 3 轮运行机 · 15:34 · 请批以后代码又改了（9f3c2a8「修好 CI：web 的类型检查」）',
      '第 4 轮运行机 · 16:11 · 等产品验收（D-3）',
    ])
    expect(within(card).getByRole('link', { name: '看 MR ↗' })).toHaveAttribute('href', 'https://gitlab.example.com/shop/shop-pay/-/merge_requests/41')
    expect(within(card).queryByRole('button', { name: '打开会话' })).toBeNull()
    fireEvent.mouseLeave(box(graph, 'release'))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '发布做了什么' })).toBeNull())
    fireEvent.mouseEnter(box(graph, 'requirements'))
    const local = await screen.findByRole('dialog', { name: '需求分析做了什么' })
    expect(local).toHaveTextContent('第 1 轮本机 · 10:05 · 你批了')
    fireEvent.click(within(local).getByRole('button', { name: '打开会话' }))
    expect(onSession).toHaveBeenCalledWith('/chat?sid=brain-PAY-9-7f2c')
  })

  test('a click on a box looks at it, Enter too; each box says its state, who did it and its rounds in words', () => {
    const b = pay9()
    const { graph, onNode } = draw(b)
    expect(box(graph, 'release')).toHaveAttribute('role', 'button')
    expect(box(graph, 'release')).toHaveAccessibleName('发布（release.md）：等产品，运行机 第 1 次，第 4 轮，等产品验收（D-3）')
    expect(box(graph, 'units')).toHaveAccessibleName('拆分单元（unit-of-work.md）：完成，本机会话，2 个单元：api、web')
    fireEvent.click(box(graph, 'release'))
    expect(onNode).toHaveBeenLastCalledWith('release')
    fireEvent.keyDown(box(graph, 'review'), { key: 'Enter' })
    expect(onNode).toHaveBeenLastCalledWith('review')
    cleanup()
    const focused = draw(b, { focus: 'release' }).graph
    expect(box(focused, 'release')).toHaveAttribute('aria-pressed', 'true')
    expect(box(focused, 'review')).toHaveClass('is-dim')
    expect(Array.from(focused.querySelectorAll('path.kt-edge.is-hot')).length).toBe(2)
  })
})

describe('阶段图 · PAY-16 (本机，等你批需求和设计)', () => {
  test('both boxes the INCEPTION ask covers blink 等你; one chip row, under 应用设计, takes you to the line; the wait counts up', async () => {
    const { graph, onAct } = draw(pay16())
    for (const id of ['requirements', 'application-design']) {
      expect(box(graph, id)).toHaveAttribute('data-status', 'awaiting_approval')
      expect(box(graph, id)).not.toHaveClass('is-other')
      expect(box(graph, id).querySelector('.kt-node-halo')).not.toBeNull()
      expect(badges(graph, id)).toEqual(['等你'])
      expect(box(graph, id).querySelector('.kt-node-task')).toHaveTextContent('已等 12 分钟')
      expect(line(graph, id)).toBe('✋ 本机 · 等你')
    }
    expect(within(graph).getAllByTestId('kt-gate-row').map((r) => r.getAttribute('data-node'))).toEqual(['application-design'])
    fireEvent.click(within(graph).getByRole('button', { name: '应用设计：等你批 · 需求和设计' }))
    expect(onAct).toHaveBeenCalledWith('application-design', 'approve')
    expect(within(graph).getByTestId('kt-cmd').querySelector('path')).toHaveClass('is-awaiting_approval')
    expect(within(graph).getAllByTestId('kt-roster-row')[0]).toHaveClass('is-mine')
    for (const [id, words] of [['code-generation/app', '等批了设计'], ['build-and-test', '等代码'], ['release', '等审查和自动检查'], ['reflect', '等合并以后']]) {
      expect(box(graph, id)).toHaveAttribute('data-status', 'waiting')
      expect(line(graph, id)).toBe(words)
    }
    expect(within(graph).getByTestId('kt-station')).toHaveAccessibleName('还没有检查结果')
    fireEvent.mouseEnter(box(graph, 'application-design'))
    const card = await screen.findByRole('dialog', { name: '应用设计做了什么' })
    expect(within(card).getByText('approve INCEPTION 3fa2c1d0').tagName).toBe('CODE')
  })

  test('a wait for you with no gate open (a tool call to approve): the box it is on says 等你 and its chip names the step, as today’s chips do', () => {
    const b = pay16()
    b.nodes = b.nodes.map((n) => {
      if (n.stage === 'requirements' || n.stage === 'application-design') return { ...n, status: 'merged', gates: [{ ...n.gates[0], state: 'approved', by: '你', who: null, line: null }] }
      if (n.id === 'code-generation/app') return { ...n, status: 'awaiting_approval', rounds: [{ ...b.nodes[1].rounds[0], n: 1 }], run: b.runs[0].key, doing: '会话里有一个工具调用等你批准' }
      return n
    })
    b.runs = [{ ...b.runs[0], now: { ...b.runs[0].now, node: 'code-generation/app' as NodeId, doing: '会话里有一个工具调用等你批准' } }]
    b.row.waits = { ...b.row.waits, do: 'tool', line: null, text: '会话里有一个工具调用等你批准' }
    const { graph, onAct } = draw(b)
    expect(within(graph).queryAllByTestId('kt-gate-row')).toEqual([])
    expect(badges(graph, 'code-generation/app')).toEqual(['等你'])
    fireEvent.click(within(graph).getByRole('button', { name: '代码生成：去批准工具调用' }))
    expect(onAct).toHaveBeenCalledWith('code-generation/app', 'tool')
    cleanup()
    expect(within(draw(b, { quiet: true }).graph).queryAllByTestId('kt-step-pill')).toEqual([])
  })

  test('in a replay nothing on the graph can be done: the chip is no button, no wait clock, no 打开会话', async () => {
    const { graph } = draw(pay16(), { quiet: true })
    expect(within(graph).getByTestId('kt-gate-pill')).toHaveClass('is-static')
    expect(within(graph).queryByRole('button', { name: '应用设计：等你批 · 需求和设计' })).toBeNull()
    // what it hands in, whole, on two lines; no wait clock
    expect(Array.from(box(graph, 'application-design').querySelectorAll('.kt-node-task')).map((t) => t.textContent)).toEqual(['application-', 'design.md'])
    fireEvent.mouseEnter(box(graph, 'application-design'))
    const card = await screen.findByRole('dialog', { name: '应用设计做了什么' })
    expect(within(card).queryByRole('button', { name: '打开会话' })).toBeNull()
  })
})

test('every word on the graph is whole at the 1280 and 1600 px apps (BOARD-31: 12 px and up, so it wraps or says it shorter, never cuts)', () => {
  for (const make of [fx.board, fx.boardMerged]) {
    for (const card of [1238, 1558]) {
      const { graph } = draw(make(), {}, card)
      const cut = Array.from(graph.querySelectorAll('svg text')).map((t) => t.textContent ?? '').filter((t) => t.includes('…'))
      expect(cut, `${make().row.key} at ${card}`).toEqual([])
      cleanup()
    }
  }
})

test('a poll’s new events: a blip on the box each names, a red flash on a sent-back one, verify from the rail to the check ring; a late one stays still', () => {
  const was = pay9()
  const b = pay9()
  const ev = (seq: number, kind: BoardEvent['kind'], from: string, to: string, text = '', at = b.at): BoardEvent =>
    ({ seq, at, kind, from, to, text, detail: false, src: null, node: null, run: 'runner-1' as RunKey, round: null })
  b.events = [ev(1, 'stage', 'review', 'review'), ev(2, 'reject', 'person', 'release', '产品退回了发布'), ev(3, 'verify', 'branch', 'checks', '自动检查通过'),
    ev(4, 'accepted', 'relay', 'relay'), ev(5, 'stage', 'ci-pipeline', 'ci-pipeline'), ev(6, 'stage', 'units', 'units', '', b.at - 600)]
  const input = stageInput(b)
  const ids = new Set(stageLayout(input, stageShape(1558, input)).boxes.keys())
  const { items, flashes } = effects(diffBoards(was, b, { at: b.at }), b, ids)
  expect(items.map((i) => [i.kind, i.from, i.to, i.shape])).toEqual([['stage', 'review', 'review', 'spark'], ['reject', ORCH, 'release', 'comet'], ['verify', RUN, TESTS, 'comet'],
    ['accepted', 'relay:0', 'relay:0', 'ring']])
  expect(items[2].label).toBe('3/3')
  expect(flashes.map((f) => [f.target, f.tone])).toEqual([['release', 'bad']])
  // the merge: the rail and every merged box flash green
  const merged = { ...b, ended_at: b.at }
  const at = effects(diffBoards(b, merged, { at: b.at }), merged, ids).flashes
  expect(at.map((f) => [f.target, f.tone])).toEqual([RUN, 'requirements', 'application-design', 'units', 'code-generation/api', 'code-generation/web', 'build-and-test', 'review']
    .map((t) => [t, 'ok']))
})
