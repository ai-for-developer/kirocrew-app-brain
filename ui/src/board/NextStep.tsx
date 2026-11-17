/**
 * 你要做的一步: one line at the top of the board, above the graph. What the person does now, how many things wait on
 * them and since when, and 去需求详情 →, where each of them is done (the line to type, the session, the answer form,
 * the MR). When it is not their turn, one dim line says whom it waits for. The app takes no step for them.
 */
import type { ReqBoard } from '../types'
import { ExtLink } from '../ui'
import { ACT } from '../vocab'
import { fmtSpan } from './format'
import { useServerNow } from './hooks'
import { nextOf, type Step } from './next'
import { RoleIcon } from './RoleIcon'

export function NextStep({ board, onDetail }: { board: ReqBoard; onDetail?: () => void }) {
  const n = nextOf(board)
  const now = useServerNow()
  const st = board.row.state
  if (!n.mine) {
    if (!n.text || st === 'claimed' || st === 'merged' || st === 'closed') return null
    return (
      <p className="br-next is-other" role="status" data-testid="br-next">
        <span className="br-next-who">此刻不用你做什么</span>
        <span>{n.text}</span>
        {n.mr && <ExtLink href={n.mr} className="br-link">{ACT.viewMr} ↗</ExtLink>}
      </p>
    )
  }
  if (!n.steps.length) return null
  return (
    <section id="br-next" className="br-next is-mine" aria-label="你要做的一步" data-testid="br-next">
      <span className="br-next-who"><RoleIcon role="person" size={16} />你要做的一步</span>
      <span className="br-next-text br-strong" data-testid="br-next-text" title={stepText(n.steps[0])}>{stepText(n.steps[0])}</span>
      {n.steps.length > 1 && <span className="kt-pill tone-approve">共 {n.steps.length} 件事等你</span>}
      {n.since !== null && <span className="kt-dim">已经等了 {fmtSpan(now - n.since)}</span>}
      {onDetail && <button type="button" className="kt-btn is-primary" onClick={onDetail}>去需求详情 →</button>}
    </section>
  )
}

/** A step in one line: a question as asked, a refused hand-over by why, else the first line of what to do. */
function stepText(s: Step): string {
  if (s.question) return `回答：${s.question.question}`
  if (s.why) return `接手被拒：${s.why}`
  return s.text.split('\n')[0]
}
