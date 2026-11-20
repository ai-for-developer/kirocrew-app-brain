import { describe, expect, test } from 'vitest'
import { FLOW_FORM, can, flowNow } from './flows'
import { fx } from './test/data'
import { FAST, MR, WAITS, fastRow } from './test/fast'
import type { FlowAction, Requirement } from './types'
import { leanNow } from './vocab'

const ACTIONS: FlowAction[] = ['handoff', 'review', 'mr', 'full']
const allowed = (r: Pick<Requirement, 'flow'>) => ACTIONS.filter((a) => can(r, a))

describe('can: what a person may do with a run, as brain-kit’s table says', () => {
  test('a Lean run is handed over and reviewed; a fast one opens its MR and switches to the full flow', () => {
    expect(allowed(fx.overview().rows[0])).toEqual(['handoff', 'review'])
    expect(allowed(fastRow())).toEqual(['mr', 'full'])
  })

  test('a row without a flow (an older backend) is a Lean one', () => {
    const { flow: _, ...old } = fx.overview().rows[0]
    const row: Requirement = old
    expect(allowed(row)).toEqual(['handoff', 'review'])
  })

  test('the buttons follow the table the backend sent, not the flow’s name', () => {
    expect(allowed(fastRow({ flow: { ...FAST, actions: ['full'] } }))).toEqual(['full'])
  })
})

describe('flowNow: where a run is on its way, in words', () => {
  test('a Lean run says Lean’s progress, as before', () => {
    const r = fx.overview().rows[0]
    const { flow: _, ...old } = r
    expect(flowNow(r)).toBe(leanNow(r))
    expect(flowNow(r, [])).toBe(leanNow(r, []))
    expect(flowNow(old)).toBe(leanNow(r))
  })

  test.each([
    ['the AI writing', { waits: WAITS.writing }, '在写代码'],
    ['its session stopped', { waits: WAITS.stopped }, '在写代码'],
    ['its code committed and checked', { waits: WAITS.ready }, '代码写好了，等开 MR'],
    ['a risk path changed', { waits: WAITS.risk }, '改到风险目录'],
    ['a risk path changed after its MR opened', { waits: WAITS.risk, mr: MR }, '改到风险目录'],
    ['its MR open, CI running', { waits: WAITS.ciRunning, mr: MR }, 'MR !12：CI 在跑'],
    ['CI red, MR 巡检 fixing it', { waits: WAITS.ciPatrol, mr: MR }, 'MR !12：CI 没过'],
    ['CI red, the person tells the session', { waits: WAITS.ciYou, mr: MR }, 'MR !12：CI 没过'],
    ['CI green, people to merge', { waits: WAITS.merge, mr: MR }, 'MR !12 等合并'],
    ['merged', { state: 'merged', waits: WAITS.writing, mr: { ...MR, state: 'merged' } }, '已合并'],
    ['its MR closed', { state: 'closed', mr: { ...MR, state: 'closed' } }, 'MR !12 关了，没合并'],
  ] as [string, Partial<Requirement>, string][])('a fast run, %s: %s', (_, more, said) => {
    expect(flowNow(fastRow(more))).toBe(said)
  })

  test('a fast run never says a Lean stage, even with Lean’s summary still on it', () => {
    const lean = fx.overview().rows[0].lean
    expect(flowNow(fastRow({ lean, waits: WAITS.writing }))).toBe('在写代码')
  })
})

describe('FLOW_FORM: 开需求 per flow', () => {
  test('the Lean entry is today’s page, word for word: its steps after 开工 and what happens in the session', () => {
    const lean = FLOW_FORM.lean
    expect(lean.way({ big: false, localOnly: false })).toEqual([
      { icon: 'req', title: 'Lean 推进', text: '需求和设计由你批准' },
      { icon: 'runner', title: '交给运行机', text: '团队仓库：交接以后它接着做' },
    ])
    expect(lean.way({ big: true, localOnly: false })).toEqual([
      { icon: 'req', title: 'Lean 推进', text: '需求和设计由你批准，设计里拆单元' },
      { icon: 'scope', title: '拆成小需求', text: '批了设计再拆，大家认领、并行做' },
      { icon: 'runner', title: '交给运行机', text: '小需求都合并以后' },
    ])
    expect(lean.way({ big: false, localOnly: true })).toEqual([
      { icon: 'req', title: 'Lean 推进', text: '需求和设计由你批准' },
      { icon: 'runner', title: '交给运行机', text: '这次不需要：这个仓库只在本机做', state: 'off' },
    ])
    expect(lean.startedNext).toBe('打开会话：AI 先写需求文档；写好会请你批准（待办和运行过程会提醒，批准那一行要你在会话里自己打）。')
  })

  test('the fast one ends with 开 MR and 合并, never 交给运行机', () => {
    const titles = FLOW_FORM.fast.way({ big: false, localOnly: false }).map((s) => s.title)
    expect(titles).toEqual(['写代码', '开 MR', '合并'])
    expect(FLOW_FORM.fast.startedNext).toContain('点「开 MR」')
    expect(FLOW_FORM.fast.startedNext).not.toMatch(/批准|运行机/)
  })
})
