/**
 * How a requirement's run goes (Requirement.flow: brain-kit's flow, as the backend sends it), the one place the views
 * ask. A run a Lean intent carries shows the Lean widgets as they are; a fast run (快速: no intent, no stages, no
 * approvals; it ends with an MR a person merges) shows its own in their place. Buttons ask can(row, action), brain-kit's
 * table. Each wrapper below is given the Lean widget as its child and renders it for a Lean run, the fast one otherwise.
 */
import type { CSSProperties, ReactNode } from 'react'
import { RoleIcon } from './board/RoleIcon'
import type { FastStep, FastStepId, FlowAction, FlowId, FlowView, ReqBoard, Requirement, WaitDo, Waits } from './types'
import type { FlowStep } from './ui'
import { ACT, FAMILY_ACT, leanNow, waitsOn } from './vocab'

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

// ---------------------------------------------------------------- a fast run's five steps

export const STEP_ICON: Record<FastStepId, string> = { code: 'code', verify: 'lint', mr: 'pr', ci: 'ci', merge: 'merge' }

/** the part of 运行过程 a step leads to (写代码: its commits and their diffs; 自动检查: the check wall); 开 MR, CI and 合并
 * lead to their page on GitLab instead (stepHref) */
export const STEP_PART: Partial<Record<FastStepId, string>> = { code: 'kt-conveyor', verify: 'kt-wall' }

/** Scrolls the board *from* is on to the part step *id* leads to. */
export function toPart(from: Element, id: FastStepId): void {
  const part = STEP_PART[id]
  if (part) from.closest('[data-testid="br-board"]')?.querySelector(`[data-testid="${part}"]`)?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
}

export const stepHref = (s: FastStep): string | null => (s.url && /^https?:\/\//i.test(s.url) ? s.url : null)

/** the step a person's turn is at: 开 MR waits for the owner to say it is done, 合并 for people to merge it */
const ASKED: Partial<Record<WaitDo, FastStepId>> = { open_mr: 'mr', merge: 'merge' }

export interface StepLook {
  /** the stage graph's box it is drawn as (kt-node is-…) */
  node: string
  /** 需求现状's cell (br-stagecell is-…) */
  cell: string
  /** a counter's cell (kt-segs is-…); '' not reached */
  seg: string
  tone: 'ok' | 'run' | 'idle' | 'bad' | 'approve' | 'info'
  word: string
}

const LOOK: Record<FastStep['status'], StepLook> = {
  done: { node: 'done', cell: 'is-done', seg: 'is-pass', tone: 'ok', word: '做完' },
  running: { node: 'running', cell: 'is-started', seg: 'is-run', tone: 'run', word: '在做' },
  waiting: { node: 'waiting', cell: 'is-todo', seg: '', tone: 'idle', word: '还没轮到' },
  failed: { node: 'failed', cell: 'is-failed', seg: 'is-fail', tone: 'bad', word: '没过' },
}

/** How a step is drawn: by its status, as a stage's box is; one that waits for a person in a waiting box's colours
 * (magenta for the person looking, calm blue for anyone else); 合并 done is the run merged. */
export function stepLook(s: FastStep, w: Pick<Waits, 'who' | 'do'>): StepLook {
  if (s.status === 'waiting' && ASKED[w.do ?? 'none'] === s.id) {
    const you = w.who === 'you'
    return { node: you ? 'awaiting_approval' : 'awaiting_approval is-other', cell: '', seg: you ? 'is-wait' : 'is-other', tone: you ? 'approve' : 'info', word: waitsOn(w.who) || '等人' }
  }
  if (s.status === 'done' && s.id === 'merge') return { ...LOOK.done, node: 'merged', word: '已合并' }
  return LOOK[s.status]
}

// ---------------------------------------------------------------- the wrappers

/** 交付总览's progress cell: LeanProgress, or the fast run's way in words. */
export function FlowProgress({ r, children }: { r: Requirement; children: ReactNode }) {
  if (flowOf(r).intent) return <>{children}</>
  const label = flowOf(r).label
  const now = flowNow(r)
  return (
    <span className="br-lean" data-testid="lean-cell" title={`${label} · ${now}`}>
      <span><span className="br-nw">{label}</span> · <span className="br-nw">{now}</span></span>
    </span>
  )
}

/** 需求现状's Lean card: LeanCard, or the fast run's five steps (from its board, *steps*) in the same cells. */
export function FlowCard({ r, steps, children }: { r: Requirement; steps: FastStep[] | null; children: ReactNode }) {
  if (flowOf(r).intent) return <>{children}</>
  const label = flowOf(r).label
  return (
    <section className="cto-card" aria-label={`${label}进度`} data-testid="fast-card">
      <div className="cto-chart-head">
        <h3>{label}</h3>
        <span className="cto-dim">{flowNow(r)} · 不走 Lean 的阶段和审批：自动检查绿了才提交，做完开 MR，由人合并</span>
      </div>
      {!steps ? <p className="br-empty">运行过程的数据还没来。</p> : (
        <div className="br-lean-phases">
          <div className="br-lean-phase" style={{ '--cells': steps.length, '--cell': '104px' } as CSSProperties}>
            <ol className="br-stagecells" aria-label={`${label}的步骤`}>
              {steps.map((s, i) => {
                const look = stepLook(s, r.waits)
                return (
                  <li key={s.id} className={['br-stagecell', look.cell, `tone-${look.tone}`].filter(Boolean).join(' ')} data-testid="fast-step" data-step={s.id} data-state={s.status} title={s.text || look.word}>
                    <span className="br-stage-n">{i + 1} · {look.word}</span>
                    <span className="br-stage-label">{s.label}<span className="br-sr">（{look.word}）</span></span>
                    {s.text && <span className="br-stage-n">{s.text}</span>}
                  </li>
                )
              })}
            </ol>
          </div>
        </div>
      )}
    </section>
  )
}

/** 运行过程's AI-DLC Lean counter: LeanKpi, or for a board with steps the fast run's: its commits, its steps, where it is. */
export function FlowKpi({ board, children }: { board: ReqBoard; children: ReactNode }) {
  const steps = board.steps
  if (!steps) return <>{children}</>
  const label = flowOf(board.row).label
  const now = flowNow(board.row)
  return (
    <div className="kt-kpi kt-kpi-gates br-kpi-lean" aria-label={`${label}：${now}`} data-testid="br-fast-kpi">
      <div className="kt-kpi-label">{label}</div>
      <div className="kt-kpi-value">
        <span className="kt-kpi-big">{board.belt.filter((b) => b.kind === 'commit').length}</span>
        <span className="kt-kpi-of">次提交</span>
      </div>
      <div className="kt-segs" aria-hidden="true">
        {steps.map((s) => <i key={s.id} className={stepLook(s, board.row.waits).seg} />)}
      </div>
      <div className="kt-kpi-note br-lean-next">{now}</div>
    </div>
  )
}

/** 运行过程's 这条路走到哪了: StepsKpi (the stages on the way), or for a board with steps the five steps, one icon each. */
export function FlowWayKpi({ board, children }: { board: ReqBoard; children: ReactNode }) {
  const steps = board.steps
  if (!steps) return <>{children}</>
  const looks = steps.map((s) => ({ s, look: stepLook(s, board.row.waits) }))
  const working = steps.filter((s) => s.status === 'running').length
  const asked = looks.find(({ look }) => look.node.startsWith('awaiting'))?.look
  return (
    <div className="kt-kpi kt-kpi-roles">
      <div className="kt-kpi-label">这条路走到哪了</div>
      <div className="kt-kpi-value" data-testid="br-steps-counter">
        <span className="kt-kpi-big">{steps.filter((s) => s.status === 'done').length}</span>
        <span className="kt-kpi-of">/{steps.length}</span>
      </div>
      <div className="kt-role-dots">
        {looks.map(({ s, look }) => {
          const cls = `kt-role-dot tone-${look.tone}${s.status === 'running' ? ' is-working' : ''}`
          const name = `${s.label}：${look.word}`
          const icon = <RoleIcon role={STEP_ICON[s.id]} size={15} />
          const href = stepHref(s)
          if (href) return <a key={s.id} className={cls} href={href} target="_blank" rel="noopener noreferrer" title={`${name}（在 GitLab 上看）`} aria-label={`${name} ↗`}>{icon}</a>
          return (
            <button key={s.id} type="button" className={cls} title={name} aria-label={name} disabled={!STEP_PART[s.id]} onClick={(e) => toPart(e.currentTarget, s.id)}>
              {icon}
            </button>
          )
        })}
      </div>
      <div className="kt-kpi-note">
        {working ? <b className="tone-run">在做 {working}</b> : '没有在做的'}
        {asked ? <b className={`tone-${asked.tone}`}> · {asked.word}</b> : null}
      </div>
    </div>
  )
}
