/**
 * 交接给运行机 on 需求现状 (E2E-10): `brain handoff` for this requirement in its worktree on this machine (POST
 * /requirements/{repo}/{key}/handoff), so nobody opens a terminal for it. It says first what will happen, then runs it,
 * then what came of it as the backend says it (backend/core/handover.py): what happened, 做什么, the MR, brain-kit's
 * warnings as rows (read only), its own words folded under 细节. Offered for a run on this machine not handed over yet,
 * or whose hand-over is to be done again (pushed without its label, refused), in a repo with a remote; the backend
 * refuses the rest in words. The answer stays (收起) when the page moves on after it.
 */
import { useState } from 'react'
import { useAppApi } from '@kirocrew/app-sdk'
import { paths, useAction } from '../api'
import { RoleIcon } from '../board/RoleIcon'
import type { HandoffResult, HandoffState, RequirementDetail, WaitDo } from '../types'
import { CheckRows, Icon } from '../ui'
import { ACT } from '../vocab'

/** the button's name: the backend's 做什么 lines point at it by this name (handover.BUTTON) */
export const HANDOVER = '交接给运行机'
const AGAIN: readonly WaitDo[] = ['label_handoff', 'redo_handoff']
const LOOK: Record<HandoffState, { cls: string; glyph: string }> = {
  handed: { cls: 'is-ok', glyph: '✓' },
  committed: { cls: 'is-warn', glyph: '!' },
  not_labelled: { cls: 'is-warn', glyph: '!' },
  refused: { cls: 'is-bad', glyph: '✕' },
  timeout: { cls: 'is-warn', glyph: '!' },
  failed: { cls: 'is-bad', glyph: '✕' },
}

/** 需求现状 offers it: a run with its worktree on this machine, in a repo with a remote, not a small requirement (that
 * one's MR goes into its big one's branch), not handed over yet or to be handed over again. */
export function canHandOver(x: RequirementDetail, localOnly: boolean): boolean {
  const r = x.row
  if (localOnly || !r.worktree || !x.run || r.family?.role === 'child') return false
  return r.state === 'local' || AGAIN.includes(r.waits.do ?? 'none')
}

export function Handover({ x, repo, reqKey, localOnly, onDone }: {
  x: RequirementDetail
  repo: string
  reqKey: string
  localOnly: boolean
  /** after an answer (or an error): the page reads the requirement again */
  onDone: () => void
}) {
  const api = useAppApi()
  const act = useAction(() => api.post<HandoffResult>(paths.handoff(repo, reqKey), {}))
  const [asking, setAsking] = useState(false)
  const offer = canHandOver(x, localOnly)
  const busy = x.sessions.some((s) => s.kind === 'local' && s.busy)
  if (!offer && !act.result && !act.error && !act.busy) return null
  const go = async () => {
    setAsking(false)
    await act.run()
    onDone()
  }
  return (
    <section className="cto-card br-hand" aria-label="交接" data-testid="handover">
      <style>{HANDOVER_CSS}</style>
      <div className="br-row">
        <div className="cto-chart-head">
          <h3>交接</h3>
          <span className="cto-dim">{offer ? '本地这一段做完了，就交给团队的运行机接着做。' : '交接的结果'}</span>
        </div>
        <span className="br-grow" />
        {offer && !asking && !act.busy && (
          <button type="button" className="kt-btn is-primary" disabled={busy} onClick={() => { act.reset(); setAsking(true) }}>
            <RoleIcon role="handover" size={15} />{HANDOVER}
          </button>
        )}
      </div>
      {offer && busy && !act.busy && <p className="br-hint" role="status">AI 正在会话里做事：等它停下再交接。</p>}
      {asking && (
        <div className="br-confirm" role="group" aria-label="确定交接">
          <p>
            会做三件事：提交交接记录、把分支 <code>{x.row.branch ?? x.run?.branch}</code> 推到远端、在 MR 上加交接标签。{''}
            之后运行机接着做，你在 MR 上回答它的问题。
          </p>
          <div className="br-row">
            <button type="button" className="kt-btn is-primary" onClick={() => void go()}>确定交接</button>
            <button type="button" className="kt-btn" onClick={() => setAsking(false)}>取消</button>
          </div>
        </div>
      )}
      {act.busy && <p className="br-loading" role="status">正在交接：提交、推送，等 GitLab 记下这次推送，再加交接标签。一般一两分钟。</p>}
      {act.result && !act.busy && <Outcome r={act.result} onClose={act.reset} />}
      {act.error && !act.busy && <p className="br-error" role="alert">{act.error}</p>}
    </section>
  )
}

function Outcome({ r, onClose }: { r: HandoffResult; onClose: () => void }) {
  const look = LOOK[r.state] ?? LOOK.failed
  const mrUrl = r.mr?.url && /^https?:\/\//i.test(r.mr.url) ? r.mr.url : null
  return (
    <div className={`br-hand-out ${look.cls}`} data-testid="handover-result" data-state={r.state}>
      <p className="br-hand-verdict" role="status"><i aria-hidden="true">{look.glyph}</i><b>{r.text}</b></p>
      {r.todo && <p className="br-prob-todo"><b>做什么</b>{r.todo}</p>}
      {r.warnings.length > 0 && (
        <div className="br-stack" style={{ gap: 4 }} aria-label="提醒">
          <b className="br-small">提醒</b>
          <CheckRows rows={r.warnings.map((w) => ({ ...w, fix: null }))} />
        </div>
      )}
      <div className="br-row">
        {mrUrl && (
          <a className="kt-btn" href={mrUrl} target="_blank" rel="noopener noreferrer">
            {ACT.viewMr} !{r.mr?.iid} <Icon name="ExternalLink" size={12} />
          </a>
        )}
        <span className="br-grow" />
        <button type="button" className="kt-btn" onClick={onClose}>收起</button>
      </div>
      {r.raw && (
        <details className="br-raw">
          <summary>细节：brain-kit 的原话</summary>
          <pre>{r.raw}</pre>
        </details>
      )}
    </div>
  )
}

const HANDOVER_CSS = `
.br-hand { display: grid; gap: 10px; }
.br-hand .kt-btn { display: inline-flex; align-items: center; gap: 6px; }
.br-hand .kt-btn:disabled { cursor: not-allowed; opacity: .55; }
.br-hand a.kt-btn { text-decoration: none; }
.br-hand .br-confirm p { margin: 0; }
.br-hand-out { display: grid; gap: 8px; padding: 12px 14px; border-radius: 12px; border: 1px solid var(--border); background: var(--kt-glass); min-width: 0; }
.br-hand-out.is-ok { border-color: var(--kit-ok); background: color-mix(in srgb, var(--kt-ok) 7%, var(--card)); }
.br-hand-out.is-bad { border-color: var(--kit-bad); background: color-mix(in srgb, var(--kt-bad) 7%, var(--card)); }
.br-hand-out.is-warn { border-color: color-mix(in srgb, var(--kt-warn) 55%, var(--border)); background: color-mix(in srgb, var(--kt-warn) 7%, var(--card)); }
.br-hand-out p { margin: 0; }
.br-hand-verdict { display: flex; align-items: center; gap: 8px; color: var(--text-strong); }
.br-hand-verdict b { font-weight: 800; }
.br-hand-verdict i { font-style: normal; flex: none; display: inline-grid; place-items: center; width: 22px; height: 22px; border-radius: 7px; font-size: .8em; font-weight: 900;
  color: #fff; background: var(--kt-ok-solid); }
.br-hand-out.is-bad .br-hand-verdict i { background: var(--kt-bad-solid); }
.br-hand-out.is-warn .br-hand-verdict i { background: var(--kt-warn); color: var(--kt-on); }
.br-hand-out .br-raw pre { max-height: 16em; overflow: auto; }
`
