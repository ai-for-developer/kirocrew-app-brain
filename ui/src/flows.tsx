/**
 * How a requirement's run goes (Requirement.flow: brain-kit's flow, as the backend sends it), the one place the views
 * ask. A run a Lean intent carries shows the Lean widgets as they are; a fast run (快速: no intent, no stages, no
 * approvals; it ends with an MR a person merges) shows its own in their place. Buttons ask can(row, action), brain-kit's
 * table.
 */
import type { FlowAction, FlowId, FlowView, Requirement } from './types'
import type { FlowStep } from './ui'
import { ACT, FAMILY_ACT, leanNow } from './vocab'

/** what a row goes by when it carries no flow (an older backend): the default one */
const DEFAULT_FLOW: FlowView = { id: 'lean', label: '完整流程', intent: true, actions: ['handoff', 'review'] }

export const flowOf = (r: { flow?: FlowView }): FlowView => r.flow ?? DEFAULT_FLOW

/** The buttons' one rule: what brain-kit lets a person do with this run's flow. */
export const can = (r: { flow?: FlowView }, a: FlowAction): boolean => flowOf(r).actions.includes(a)

/** Where a requirement is on its way, in words: Lean's progress (vocab.leanNow) for a run a Lean intent carries; a fast
 * run's from its waits, its MR and its state. */
export function flowNow(r: Pick<Requirement, 'flow' | 'state' | 'lean' | 'waits' | 'mr'>, skipped_phases?: string[]): string {
  if (flowOf(r).intent) return leanNow(r, skipped_phases)
  const mr = r.mr ? `MR !${r.mr.iid}` : ''
  if (r.state === 'merged') return '已合并'
  if (r.state === 'closed') return mr ? `${mr} 关了，没合并` : '已关闭'
  const { who, do: step } = r.waits
  if (step === 'full') return '改到风险目录'
  if (step === 'open_mr') return '代码写好了，等开 MR'
  if (mr && step === 'merge') return `${mr} 等合并`
  if (mr && who === 'nobody') return `${mr}：CI 在跑`
  if (mr && (who === 'runner' || step === 'reply')) return `${mr}：CI 没过`
  return '在写代码'
}

// ---------------------------------------------------------------- 开需求

export const FAST_TOGGLE = '快速：直接写代码'
export const FAST_HINT = '不开 Lean intent、不走阶段和审批，AI 直接按你说的写代码；提交前的自动检查、CI 和风险目录照旧，做完开 MR，由人合并。'

/** 开需求 for each flow: the hero's steps after 开工 (the way the run goes; NewReq lights the first once it started), and
 * what happens in the session, as the start's answer says it. */
export const FLOW_FORM: Record<FlowId, { way: (o: { big: boolean; localOnly: boolean }) => FlowStep[]; startedNext: string }> = {
  lean: {
    way: ({ big, localOnly }) => [
      { icon: 'req', title: 'Lean 推进', text: big ? '需求和设计由你批准，设计里拆单元' : '需求和设计由你批准' },
      ...(big ? [{ icon: 'scope', title: FAMILY_ACT.split, text: '批了设计再拆，大家认领、并行做' }] : []),
      localOnly ? { icon: 'runner', title: '交给运行机', text: '这次不需要：这个仓库只在本机做', state: 'off' as const }
        : { icon: 'runner', title: '交给运行机', text: big ? '小需求都合并以后' : '团队仓库：交接以后它接着做' },
    ],
    startedNext: '打开会话：AI 先写需求文档；写好会请你批准（待办和运行过程会提醒，批准那一行要你在会话里自己打）。',
  },
  fast: {
    way: () => [
      { icon: 'code', title: '写代码', text: '提交前自动检查要绿' },
      { icon: 'pr', title: ACT.openMr, text: '做完了你来开，CI 照跑' },
      { icon: 'merge', title: '合并', text: '人看 MR、合并' },
    ],
    startedNext: `打开会话：AI 直接按你说的写代码和测试，自动检查绿了才提交。做完了在${ACT.openDetail}里点「${ACT.openMr}」。`,
  },
}
