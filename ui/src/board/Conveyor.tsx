/**
 * The conveyor (the sample team board's 合并传送带): every commit of the run branch in order, each named by the run that
 * made it (本机, 运行机, a small requirement), the hand-over commit, and the merge into the default branch at the head.
 * Its head line counts them by run. A commit that is new since the previous poll slides in; a click looks at the stage
 * it was made in. A person's commit after the hand-over is marked: it is a step outside the judgment points. A fast
 * run's commits are on no stage: a click opens 产出 on that commit (*onCommit*).
 */
import { useEffect, useRef, useState } from 'react'
import type { ReqBoard } from '../types'
import { beltKey } from './diff'
import { RUN_ICON, clockAt, outsidePushes, runShort } from './format'
import { RoleIcon } from './RoleIcon'
import { ExtLink } from '../ui'
import { ACT } from '../vocab'

/** An empty belt, said by why it is empty: no commit yet; a hand-over the runner has not started on (what it waits
 * for, as the board says it); a run whose working copy is on the runner, not here (its commits are on the MR); a
 * requirement brain-kit could not read the record of (the page's note says what to do). */
function beltEmpty(b: Pick<ReqBoard, 'source' | 'row' | 'runs'>): string {
  if (b.source === 'trace') return '运行分支上还没有提交'
  if (b.row.where === 'runner' && !b.row.worktree) {
    const runner = b.runs.find((r) => r.kind === 'runner')?.now.status
    return !runner || runner === 'waiting' || runner === 'queued' ? `运行机还没开始做，还没有它的提交：${b.row.waits.text}` : '运行机的工作目录不在这台机器上，这里读不到它的提交'
  }
  return '这里读不到运行分支的提交：brain-kit 没读出这个需求的记录'
}

/** The gap between two chips: the fade at the belt's left edge covers it, and the first chip in view starts after it. */
export const BELT_EDGE = 10

/** Where the belt opens: at its newest end, moved on to where a chip starts BELT_EDGE in, so the first chip in view is
 * whole; the room that takes is added after the newest chip, which stays whole too. *starts*: each chip's left, in
 * order, in the belt's own coordinates. */
export function newestEnd(starts: number[], clientWidth: number, scrollWidth: number): { left: number; room: number } {
  const max = scrollWidth - clientWidth
  if (max <= 0) return { left: 0, room: 0 }
  const left = starts.map((x) => x - BELT_EDGE).find((x) => x >= max) ?? max
  return { left, room: left - max }
}

/** How many chips are left of the view, whole or in part. */
const hiddenLeft = (el: HTMLElement) => [...el.querySelectorAll<HTMLElement>('.kt-belt-chip')].filter((c) => c.offsetLeft < el.scrollLeft).length

export function Conveyor({ board, fresh, focus, onNode, onCommit }: {
  board: ReqBoard; fresh: ReadonlySet<string>; focus: string | null; onNode: (id: string) => void; onCommit?: (sha: string) => void
}) {
  const belt = useRef<HTMLOListElement | null>(null)
  const [older, setOlder] = useState(0)
  // at the newest end as it opened: a resize opens it there again (a scroll the resize makes is not the person's); once
  // the person moves it, it stays where they put it
  const pinned = useRef(false)
  useEffect(() => {
    const el = belt.current
    if (!el) return
    const open = () => {
      el.style.paddingRight = ''
      const pad = parseFloat(getComputedStyle(el).paddingRight) || 0
      const { left, room } = newestEnd([...el.children].map((c) => (c as HTMLElement).offsetLeft), el.clientWidth, el.scrollWidth)
      if (room) el.style.paddingRight = `${pad + room}px`
      el.scrollTo?.({ left, behavior: 'instant' })
      pinned.current = true
      setOlder(hiddenLeft(el))
    }
    open()
    if (typeof ResizeObserver === 'undefined') return
    let width = el.clientWidth
    const ro = new ResizeObserver(() => {
      if (el.clientWidth === width) return
      width = el.clientWidth
      if (pinned.current) open()
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [board.belt.length])
  const moved = () => void (pinned.current = false)
  const runs = new Map(board.runs.map((r) => [r.key as string, r]))
  const commits = board.belt.filter((x) => x.kind === 'commit')
  const merged = board.belt.some((x) => x.kind === 'merge')
  const handovers = board.belt.filter((x) => x.kind === 'handover').length
  const after = outsidePushes(board).length
  // 本机 3 · 运行机 9, in the runs' order
  const byRun = board.runs.map((r) => [runShort(r, board), commits.filter((x) => x.run === r.key).length] as const).filter(([, k]) => k > 0)
  return (
    <section className="kt-card kt-conveyor" data-testid="kt-conveyor" aria-label="提交传送带">
      <header className="kt-card-head">
        <h2>提交传送带</h2>
        <span className="kt-dim">
          {commits.length} 次提交{byRun.map(([who, k]) => ` · ${who} ${k}`).join('')}{handovers ? ` · 交接 ${handovers}` : ''}{merged ? ' · 已合并' : ''}
          {after ? <> · <span className="tone-warn">交接后有人推了 {after} 次</span></> : null}
        </span>
        {older > 0 && <button type="button" className="kt-btn is-small kt-belt-older" onClick={() => {
          moved()
          belt.current?.scrollTo({ left: 0, behavior: 'smooth' })
        }}>← 左边还有 {older} 个</button>}
      </header>
      {board.belt.length === 0 ? (
        <p className="kt-dim kt-wall-empty">
          {beltEmpty(board)}
          {board.source !== 'trace' && board.row.mr && <> <ExtLink href={board.row.mr.url}>{ACT.viewMr}</ExtLink></>}
        </p>
      ) : (
        <ol className={`kt-belt${older ? ' has-older' : ''}`} ref={belt} onScroll={(e) => setOlder(hiddenLeft(e.currentTarget))}
          onWheel={moved} onPointerDown={moved} onKeyDown={moved}>
          <li className="kt-belt-end is-base" title="运行分支从这里出发">从 <code>{board.base}</code></li>
          {board.belt.map((x) => {
            const k = beltKey(x)
            const run = runs.get(x.run ?? '')
            const node = board.nodes.find((n) => n.id === x.node)
            const push = x.kind === 'commit' && x.by === 'person' && run?.kind === 'runner'
            const cls = x.kind === 'handover' ? ' is-handover' : x.kind === 'merge' ? ' is-merge' : push ? ' is-push' : run && run.kind !== 'local' ? ' is-bot' : ''
            const who = x.kind === 'merge' ? `合并进 ${board.base}` : x.kind === 'handover' ? '交接' : push ? '有人推的' : run ? runShort(run, board) : x.by === 'bot' ? '运行机' : '本机'
            const icon = x.kind === 'merge' ? 'merge' : x.kind === 'handover' ? 'handover' : run ? RUN_ICON[run.kind] : 'commit'
            const where = node?.unit ? `${node.title} · ${node.unit}` : node?.title
            return (
              <li key={k} className={`kt-belt-chip${cls}${fresh.has(k) ? ' is-new' : ''}${focus && focus !== x.node ? ' is-dim' : ''}`} data-run={x.run ?? undefined}>
                {node ? (
                  <button type="button" className="kt-belt-role" onClick={() => onNode(node.id)} title={`${x.text}（在「${where}」这一步：点一下只看它）`}>
                    <RoleIcon role={icon} size={14} /> {who}
                  </button>
                ) : x.kind === 'commit' && onCommit ? (
                  <button type="button" className="kt-belt-role" onClick={() => onCommit(x.sha)} title={`${x.text}（点一下看它改了什么）`}>
                    <RoleIcon role={icon} size={14} /> {who}
                  </button>
                ) : (
                  <span className="kt-belt-role" title={x.text}><RoleIcon role={icon} size={14} /> {who}</span>
                )}
                <span className="kt-belt-meta">
                  {x.sha && <><code>{x.sha.slice(0, 8)}</code> · </>}{x.files ? `${x.files} 个文件 · ` : ''}{clockAt(x.at, board.at)}
                </span>
                {x.kind === 'commit' && <span className="kt-belt-text">{x.text}{x.records_only ? '（只改记录）' : ''}</span>}
              </li>
            )
          })}
          <li className={`kt-belt-end is-head${merged ? ' is-merged' : ''}`} title={merged ? '已合并进默认分支' : '运行分支的最新提交'}>
            <code>{merged ? board.base : board.branch ?? '运行分支'}</code>
          </li>
        </ol>
      )}
    </section>
  )
}
