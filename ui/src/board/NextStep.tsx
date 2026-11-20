/**
 * 你要做的一步: one line at the top of the board, above the graph. What the person does now, how many things wait on
 * them and since when, and 去需求现状 →, where each of them is done (the line to type, the session, the answer form,
 * the MR). When it is not their turn, one dim line says whom it waits for. The app takes no step for them, but one: a
 * fast run's 开 MR, or 转完整流程 once a risk path changed, is right there when that is the step (views/FastActs.tsx's
 * parts, as on 需求现状), 去需求现状 → after it; what it answered stays under the line until 收起, the board moving on.
 */
import type { ReqBoard } from '../types'
import { ExtLink } from '../ui'
import { ActButtons, ActFollow, aiWriting, fastActs, stepAct, useFastAct } from '../views/FastActs'
import { HANDOVER_CSS } from '../views/Handover'
import { ACT } from '../vocab'
import { fmtSpan, localOnly } from './format'
import { useServerNow } from './hooks'
import { nextOf, type Step } from './next'
import { RoleIcon } from './RoleIcon'

export function NextStep({ board, repo, reqKey, onDetail, onActed }: {
  board: ReqBoard
  repo: string
  reqKey: string
  onDetail?: () => void
  /** after a fast run's act answered (or failed): the board read again */
  onActed: () => void
}) {
  const n = nextOf(board)
  const now = useServerNow()
  const f = useFastAct(repo, reqKey, onActed)
  const st = board.row.state
  const a = n.mine ? stepAct(fastActs(board.row, st !== 'claimed', localOnly(board)), n.steps[0]?.do) : undefined
  const offers = a ? [a] : []
  const busy = aiWriting(board.sessions)
  const follow = <ActFollow f={f} offers={offers} lead={a ?? f.sent} busy={busy} className="br-hand"><style>{HANDOVER_CSS}</style></ActFollow>
  if (!n.mine) {
    if (!n.text || st === 'claimed' || st === 'merged' || st === 'closed') return follow
    return (
      <>
        <p className="br-next is-other" role="status" data-testid="br-next">
          <span className="br-next-who">此刻不用你做什么</span>
          <span>{n.text}</span>
          {n.mr && <ExtLink href={n.mr} className="br-link">{ACT.viewMr} ↗</ExtLink>}
        </p>
        {follow}
      </>
    )
  }
  if (!n.steps.length) return follow
  return (
    <>
      <section id="br-next" className="br-next is-mine" aria-label="你要做的一步" data-testid="br-next">
        <span className="br-next-who"><RoleIcon role="person" size={16} />你要做的一步</span>
        <span className="br-next-text br-strong" data-testid="br-next-text" title={stepText(n.steps[0])}>{stepText(n.steps[0])}</span>
        {n.steps.length > 1 && <span className="kt-pill tone-approve">共 {n.steps.length} 件事等你</span>}
        {n.since !== null && <span className="kt-dim">已经等了 {fmtSpan(now - n.since)}</span>}
        {a && <ActButtons f={f} offers={offers} lead={a} busy={busy} />}
        {onDetail && <button type="button" className={`kt-btn${a ? '' : ' is-primary'}`} onClick={onDetail}>去需求现状 →</button>}
      </section>
      {follow}
    </>
  )
}

/** A step in one line: a question as asked, a refused hand-over by why, else the first line of what to do. */
function stepText(s: Step): string {
  if (s.question) return `回答：${s.question.question}`
  if (s.why) return `接手被拒：${s.why}`
  return s.text.split('\n')[0]
}
