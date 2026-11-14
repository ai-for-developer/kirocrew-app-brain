/**
 * 重开一个会话 (E2E-7, E2E-12): a new session for a requirement in local development, in its worktree, when the live one
 * is stuck or wrong. 打开会话 hands the live one back, and a session KiroCrew left stuck after an interrupted turn could
 * only be deleted. Asked first, in plain words; POST …/session {fresh, replace: the live slot}: the backend opens one new
 * session per click (the same click twice, or a second tab, gets that one), stops the old one when it is still busy and
 * keeps it, so its record stays readable in KiroCrew.
 */
import { useState } from 'react'
import { useAppApi, useNavigate } from '@kirocrew/app-sdk'
import { paths, useAction } from '../api'
import type { OpenSessionBody, OpenSessionResult, SessionRow } from '../types'
import { Icon } from '../ui'

export function ReopenSession({ repo, reqKey, live, onOpened }: {
  repo: string
  reqKey: string
  /** the session the page shows as the one in use (the newest local one) */
  live: SessionRow
  onOpened: () => void
}) {
  const api = useAppApi()
  const navigate = useNavigate()
  const [asking, setAsking] = useState(false)
  const act = useAction((body: OpenSessionBody) => api.post<OpenSessionResult>(paths.session(repo, reqKey), body))
  const r = act.result
  const reopen = async () => {
    const got = await act.run({ fresh: true, replace: live.slot, trust: true })
    setAsking(false)
    if (got) onOpened()
  }
  return (
    <section className="br-reopen" aria-label="重开会话" data-testid="reopen">
      {r ? (
        <div className="br-row">
          <span className="br-ok-text" role="status">{r.reopened ? '新会话开好了。旧会话留在 KiroCrew 里，记录还能看。' : '已经重开过了：这是那个新会话。'}</span>
          <button type="button" className="kt-btn is-small is-primary" onClick={() => navigate(r.url)}>
            <Icon name="MessageSquare" size={13} />打开新会话
          </button>
        </div>
      ) : asking ? (
        <div className="br-confirm" role="group" aria-label="确认重开会话">
          <p>开一个新会话接着做 {reqKey}。旧会话留着，记录还能看；它要是还在跑，会先停下。</p>
          <div className="br-row">
            <button type="button" className="kt-btn is-small is-primary" disabled={act.busy} onClick={() => void reopen()}>{act.busy ? '正在重开…' : '重开'}</button>
            <button type="button" className="kt-btn is-small" disabled={act.busy} onClick={() => setAsking(false)}>算了</button>
          </div>
        </div>
      ) : (
        <div className="br-row">
          <span className="br-dim br-small">会话卡住了，或者想换个新的接着做？</span>
          <button type="button" className="kt-btn is-small" onClick={() => setAsking(true)}><Icon name="RefreshCw" size={13} />重开一个会话</button>
        </div>
      )}
      {act.error && <p className="br-error" role="alert">{act.error}</p>}
    </section>
  )
}
