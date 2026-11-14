/**
 * The event feed (the sample team board's EventFeed.tsx): the requirement's trace, newest first, an icon per kind, where
 * it went (a stage's chip: click it to look at that stage only, everywhere on the board; 你, the run branch, the checks,
 * the hand-over in words), the run that did it, the time. Events that are new since the previous poll slide in. Lean's
 * stage steps and blocked tool calls are many and quiet: they can be hidden.
 */
import { useState } from 'react'
import type { BoardEvent, ReqBoard, Run, StageNode } from '../types'
import { EVENT_META, PLACE, RUN_ICON, fmtAgo, fmtWhen, personPlace, runShort } from './format'
import { useServerNow } from './hooks'
import { RoleIcon } from './RoleIcon'

const nodeTitle = (n: StageNode) => (n.unit ? `${n.title} · ${n.unit}` : n.title)

export function EventFeed({ board, focus, onNode, fresh }: { board: ReqBoard; focus: string | null; onNode: (id: string) => void; fresh: ReadonlySet<number> }) {
  const now = useServerNow()
  const [detail, setDetail] = useState(true)
  const nodes = new Map<string, StageNode>(board.nodes.map((n) => [n.id, n]))
  const runs = new Map<string, Run>(board.runs.map((r) => [r.key, r]))
  const shown = board.events.slice().reverse().filter((e) => (detail || !e.detail) && (!focus || e.node === focus))
  const focused = focus ? nodes.get(focus) : undefined
  return (
    <section className="kt-card kt-feed" data-testid="kt-feed" aria-label="事件">
      <header className="kt-card-head">
        <h2>事件</h2>
        <span className="kt-dim">{board.source === 'trace' ? '来自 brain-kit 的记录' : '来自 Lean 的记录'}</span>
        {focused && (
          <button type="button" className="kt-filter" onClick={() => onNode(focused.id)} aria-label={`不再只看${nodeTitle(focused)}`}>
            只看 <RoleIcon role={focused.icon} size={14} /> {nodeTitle(focused)} ✕
          </button>
        )}
        <label className="kt-check">
          <input type="checkbox" checked={detail} onChange={(e) => setDetail(e.target.checked)} />
          Lean 细节
        </label>
      </header>
      <ol className="kt-feed-list">
        {shown.length === 0 && <li className="kt-dim kt-feed-empty">{focus ? '这一步还没有事件' : '还没有事件'}</li>}
        {shown.map((e) => {
          const meta = EVENT_META[e.kind] ?? { icon: '•', label: e.kind, tone: 'idle' }
          const run = runs.get(e.run ?? '')
          return (
            <li key={e.seq} className={`kt-ev is-${e.kind} tone-${meta.tone}${fresh.has(e.seq) ? ' is-new' : ''}${e.detail ? ' is-tool' : ''}`} data-seq={e.seq} title={e.src ?? undefined}>
              <span className="kt-ev-icon" aria-hidden="true" title={meta.label}>{meta.icon}</span>
              <span className="kt-ev-text">{e.text}</span>
              <Ends e={e} board={board} nodes={nodes} onNode={onNode} />
              {run && (
                <span className="kt-ev-run" data-testid="kt-ev-run" title={run.label}><RoleIcon role={RUN_ICON[run.kind]} size={12} /> {runShort(run, board)}</span>
              )}
              <time className="kt-ev-time" dateTime={new Date(e.at * 1000).toISOString()} title={fmtWhen(e.at)}>{fmtAgo(now - e.at)}</time>
            </li>
          )
        })}
      </ol>
    </section>
  )
}

/** Where it went: from → to, a stage as its chip, the other places in words; one place when it stayed there. */
function Ends({ e, board, nodes, onNode }: { e: BoardEvent; board: ReqBoard; nodes: Map<string, StageNode>; onNode: (id: string) => void }) {
  const end = (k: string | null) => {
    if (!k) return null
    const n = nodes.get(k)
    if (!n) {
      const place = k === 'person' ? personPlace(e, board) : PLACE[k]
      return place ? <span key={k} className="kt-ev-place" data-testid="kt-ev-place">{place}</span> : null
    }
    // a stage Lean skipped has no box and no lane: nothing to look at
    if (n.status === 'skipped') return <span key={n.id} className="kt-ev-role is-off" title={`${nodeTitle(n)}：这次跳过了`}><RoleIcon role={n.icon} size={15} /></span>
    return (
      <button key={n.id} type="button" className="kt-ev-role" onClick={() => onNode(n.id)} title={`只看${nodeTitle(n)}`} aria-label={`只看${nodeTitle(n)}`}>
        <RoleIcon role={n.icon} size={15} />
      </button>
    )
  }
  if (e.from && e.to && e.from !== e.to) {
    return (
      <span className="kt-ev-roles">
        {end(e.from)}
        <span aria-hidden="true">→</span>
        {end(e.to)}
      </span>
    )
  }
  return <span className="kt-ev-roles">{end(e.from ?? e.to)}</span>
}
