/**
 * 运行：全部 ▾ in the board's top bar (the reference team board's 选择运行): 全部, then each run with when it worked,
 * the stages it did and where it is now, then the runs on another branch, greyed (they are not read into this board).
 * A pick shows that run's flow everywhere on the page (picker.ts inRun) and is a history entry: 后退 goes back.
 */
import { useEffect, useRef, useState } from 'react'
import type { ReqBoard } from '../types'
import { pickRows, pickedRun, type RunFilter } from './picker'
import { RoleIcon } from './RoleIcon'

export function RunPicker({ board, value, onPick }: { board: ReqBoard; value: RunFilter; onPick: (run: RunFilter) => void }) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLSpanElement | null>(null)
  useEffect(() => {
    if (!open) return
    const away = (e: Event) => {
      if (!(e instanceof KeyboardEvent)) return void (!root.current?.contains(e.target as Node) && setOpen(false))
      if (e.key !== 'Escape') return
      e.stopPropagation()   // the board's own Esc (the box looked at) waits for the next one
      setOpen(false)
    }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', away)
    return () => {
      document.removeEventListener('mousedown', away)
      document.removeEventListener('keydown', away)
    }
  }, [open])
  if (!board.runs.length) return null
  const one = board.runs.length === 1
  const label = one ? board.runs[0].label : pickedRun(board, value)?.label ?? '全部'
  const rows = pickRows(board)
  return (
    <span className="kt-runpick" ref={root}>
      <button type="button" className="kt-btn" aria-haspopup="menu" aria-expanded={open} disabled={one} data-testid="kt-runpick"
        title={one ? '这个需求只有这一次运行' : '只看其中一次运行的流程'} onClick={() => setOpen((v) => !v)}>
        运行：<b>{label}</b>{one ? '' : ' ▾'}
      </button>
      {open && (
        <div className="kt-runmenu" role="menu" aria-label="选择运行">
          {rows.map((r, i) => {
            const k = r.key
            return (
              <div key={k ?? `off-${i}`} role="none">
                {k === null && rows[i - 1]?.key !== null && <hr />}
                <button type="button" role="menuitemradio" aria-checked={k === value} aria-disabled={k === null || undefined}
                  className={`${k === value ? 'is-on' : ''}${k === null ? ' is-off' : ''}`}
                  onClick={k === null ? undefined : () => {
                    setOpen(false)
                    if (k !== value) onPick(k)
                  }}>
                  <span className="ico" aria-hidden="true"><RoleIcon role={r.icon} size={15} /></span>
                  <b>{r.title}</b>
                  <span className="kt-dim">{r.count}</span>
                  <small>{r.sub}</small>
                </button>
              </div>
            )
          })}
        </div>
      )}
    </span>
  )
}
