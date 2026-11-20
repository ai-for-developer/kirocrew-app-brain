/**
 * 开 MR and 转完整流程 on 需求现状, where a Lean run has 交接给运行机 (Handover.tsx): a fast run's two actions, `brain mr`
 * and `brain full` in its worktree on this machine (POST /requirements/{repo}/{key}/act/{action}), so nobody opens a
 * terminal for them. The card says what 开 MR will do and runs it on a click; 转完整流程 asks once. Then what came of it,
 * as 导入仓库 says a publish: the MR, or GitLab's new-MR link and the commands to run by hand, or brain-kit's words; its
 * own output folded under 细节. Offered as brain-kit's table says (can), for a run with its worktree here that is not
 * merged or closed; 开 MR only in a repo with a remote. The backend refuses the rest in words. The answer stays (收起)
 * when the page moves on after it.
 */
import { useState } from 'react'
import { useAppApi } from '@kirocrew/app-sdk'
import { paths, useAction } from '../api'
import { RoleIcon } from '../board/RoleIcon'
import { can } from '../flows'
import type { ActResult, RequirementDetail } from '../types'
import { ExtLink, Icon } from '../ui'
import { ACT } from '../vocab'
import { HANDOVER_CSS } from './Handover'

type Act = ActResult['action']

export const FULL_ASK = '这个需求以后按 Lean 走：先写需求，等你批准。已经写好的代码留着。'
const FULL_SAYS = '以后按 Lean 走：同一个分支，写好的代码留着。'
const BUSY: Record<Act, string> = {
  mr: '正在推送，开 MR。一般几秒到一分钟。',
  full: '正在转完整流程：建 Lean 的需求记录，提交。',
}
const NAME: Record<Act, string> = { mr: ACT.openMr, full: ACT.toFull }

/** What 需求现状 offers a run: 开 MR (in a repo with a remote) and 转完整流程, as its flow allows, while its worktree is on
 * this machine and it is not merged or closed. */
export function fastActs(x: RequirementDetail, localOnly: boolean): Act[] {
  const r = x.row
  if (!r.worktree || !x.run || r.state === 'merged' || r.state === 'closed') return []
  return (['mr', 'full'] as const).filter((a) => can(r, a) && !(a === 'mr' && localOnly))
}

export function FastActs({ x, repo, reqKey, localOnly, onDone }: {
  x: RequirementDetail
  repo: string
  reqKey: string
  localOnly: boolean
  /** after an answer (or an error): the page reads the requirement again */
  onDone: () => void
}) {
  const api = useAppApi()
  const act = useAction((a: Act) => api.post<ActResult>(paths.act(repo, reqKey, a), {}))
  const [asking, setAsking] = useState(false)
  const [sent, setSent] = useState<Act>('mr')
  const offers = fastActs(x, localOnly)
  if (!offers.length && !act.result && !act.error && !act.busy) return null
  const busy = x.sessions.some((s) => s.kind === 'local' && s.busy)
  // the step the run waits for leads: 转完整流程 once a risk path changed
  const lead: Act = x.row.waits.do === 'full' && offers.includes('full') ? 'full' : offers[0] ?? sent
  const mr = x.row.mr
  const says = lead === 'full' ? FULL_SAYS
    : mr ? `MR !${mr.iid} 开着：又提交了的话，再点「${ACT.openMr}」推上去。`
      : <>会把分支 <code>{x.row.branch ?? x.run?.branch}</code> 推到远端，在 GitLab 上开 MR：CI 照跑，人来审、合并，不交给运行机。</>
  const go = async (a: Act) => {
    setAsking(false)
    setSent(a)
    await act.run(a)
    onDone()
  }
  return (
    <section className="cto-card br-hand" aria-label={NAME[lead]} data-testid="fast-acts">
      <style>{HANDOVER_CSS}</style>
      <div className="br-row">
        <div className="cto-chart-head">
          <h3>{NAME[lead]}</h3>
          <span className="cto-dim">{offers.length ? says : `${NAME[sent]}的结果`}</span>
        </div>
        <span className="br-grow" />
        {!asking && !act.busy && offers.map((a) => (
          <button key={a} type="button" className={`kt-btn${a === lead ? ' is-primary' : ''}`} disabled={busy}
            onClick={() => { act.reset(); if (a === 'full') setAsking(true); else void go(a) }}>
            <RoleIcon role={a === 'mr' ? 'pr' : 'req'} size={15} />{NAME[a]}
          </button>
        ))}
      </div>
      {offers.length > 0 && busy && !act.busy && <p className="br-hint" role="status">AI 正在会话里做事：等它停下再{NAME[lead]}。</p>}
      {asking && (
        <div className="br-confirm" role="group" aria-label={`确定${ACT.toFull}`}>
          <p>{FULL_ASK}</p>
          <div className="br-row">
            <button type="button" className="kt-btn is-primary" onClick={() => void go('full')}>确定转</button>
            <button type="button" className="kt-btn" onClick={() => setAsking(false)}>取消</button>
          </div>
        </div>
      )}
      {act.busy && <p className="br-loading" role="status">{BUSY[sent]}</p>}
      {act.result && !act.busy && <ActOut r={act.result} onClose={act.reset} />}
      {act.error && !act.busy && <p className="br-error" role="alert">{act.error}</p>}
    </section>
  )
}

function ActOut({ r, onClose }: { r: ActResult; onClose: () => void }) {
  return (
    <div className={`br-hand-out ${r.ok ? 'is-ok' : 'is-bad'}`} data-testid="act-result" data-action={r.action}>
      <p className="br-hand-verdict" role="status"><i aria-hidden="true">{r.ok ? '✓' : '✕'}</i><b>{r.text}</b></p>
      {r.action === 'full' && r.ok && (
        <p>{r.told ? '会话正空着：已经跟它说了以后按 Lean 走。' : '会话在忙，或者没开着：在会话里跟它说一声以后按 Lean 走，或者开一个新会话。'}</p>
      )}
      <MrWay r={r} />
      <div className="br-row">
        <span className="br-grow" />
        <button type="button" className="kt-btn" onClick={onClose}>收起</button>
      </div>
      {r.output && (
        <details className="br-raw">
          <summary>细节：brain-kit 的原话</summary>
          <pre>{r.output}</pre>
        </details>
      )}
    </div>
  )
}

/** Where an MR answer leads (开 MR here, 设置's 允许快速模式), as 导入仓库 says a publish: the MR; else GitLab's new-MR page
 * and the commands to run by hand, 复制 copying them all. */
export function MrWay({ r }: { r: Pick<ActResult, 'mr' | 'new_mr_url' | 'commands'> & { pushed?: boolean } }) {
  const [copied, setCopied] = useState('')
  if (r.mr) return <p><ExtLink href={r.mr.url}>MR !{r.mr.iid}</ExtLink></p>
  if (!r.new_mr_url && !r.commands.length) return null
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(r.commands.join('\n'))
      setCopied('已复制')
    } catch {
      setCopied('没复制成：请手动选中')
    }
  }
  return (
    <div className="br-stack" style={{ gap: 4 }}>
      {r.new_mr_url && <p><ExtLink href={r.new_mr_url}>在 GitLab 上开 MR</ExtLink></p>}
      {r.commands.length > 0 && (
        <>
          <span className="br-hint">{r.pushed ? '或者在终端里开 MR：' : '在终端里自己做：'}</span>
          <pre className="br-findings" data-testid="commands">{r.commands.join('\n')}</pre>
          <div className="br-row">
            <button type="button" className="br-btn is-small" onClick={() => void copy()}><Icon name="Copy" size={13} />复制</button>
            {copied && <span className="br-small br-dim" role="status">{copied}</span>}
          </div>
        </>
      )}
    </div>
  )
}
