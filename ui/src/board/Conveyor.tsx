/**
 * The conveyor (the sample team board's 合并传送带): every commit of the run branch in order, each named by the run that
 * made it (本机, 运行机, a small requirement), the hand-over commit, and the merge into the default branch at the head.
 * Its head line counts them by run. A commit that is new since the previous poll slides in; a click looks at the stage
 * it was made in. A person's commit after the hand-over is marked: it is a step outside the judgment points.
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

export function Conveyor({ board, fresh, focus, onNode }: { board: ReqBoard; fresh: ReadonlySet<string>; focus: string | null; onNode: (id: string) => void }) {
  const belt = useRef<HTMLOListElement | null>(null)
  const [older, setOlder] = useState(false)
  useEffect(() => {
    const el = belt.current
    if (!el) return
    el.scrollLeft = el.scrollWidth
    setOlder(el.scrollLeft > 4)
  }, [board.belt.length])
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
      </header>
      {board.belt.length === 0 ? (
        <p className="kt-dim kt-wall-empty">
          {beltEmpty(board)}
          {board.source !== 'trace' && board.row.mr && <> <ExtLink href={board.row.mr.url}>{ACT.viewMr}</ExtLink></>}
        </p>
      ) : (
        <ol className={`kt-belt${older ? ' has-older' : ''}`} ref={belt} onScroll={(e) => setOlder(e.currentTarget.scrollLeft > 4)}>
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
