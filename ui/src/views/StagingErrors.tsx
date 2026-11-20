/**
 * 测试环境报错 on 运行机 (GET /team/staging): the errors the runner found in the test environment's log, a short list
 * with the ones at work first: each one's signature, how many times it was seen, its state in words with why it waits
 * or was left to people, its Issue, the MR that brought it in and the fix's MR, and its session.
 */
import { ago } from '../format'
import type { StagingLink, StagingState, TeamStaging } from '../types'
import { ExtLink, Icon, RoleIcon } from '../ui'
import type { Tone } from '../styles'
import { ACT } from '../vocab'

/** staging.py's states, in words, in the order the list shows them; another one as it is. */
export const STAGING_STATE: Record<StagingState, { label: string; tone: Tone }> = {
  fixing: { label: '在修', tone: 'run' },
  fix_mr: { label: '修好了，等人审 MR', tone: 'wait' },
  issued: { label: '开了 Issue，等修', tone: 'hand' },
  new: { label: '刚看到', tone: 'warn' },
  stopped: { label: '交给人了', tone: 'bad' },
  closed: { label: '已关', tone: 'ok' },
}
export const stagingMeta = (state: string) => STAGING_STATE[state as StagingState] ?? { label: state, tone: 'idle' as Tone }
const rank = (state: string) => { const i = Object.keys(STAGING_STATE).indexOf(state); return i < 0 ? 99 : i }
export const STAGING_OFF = '还没有项目打开测试环境报错：项目的 .brain/kit.json 里 work_sources 写上 staging-errors，桥的 bridge.json 里这个项目写上 staging（测试环境的日志和 VERSION 文件在哪）就开始。'
/** the most rows the list shows */
export const STAGING_ROWS = 8

function MrOf({ what, link }: { what: string; link: StagingLink }) {
  return <span title={link.title || undefined}>{what} <ExtLink href={link.url} className="tm-mr">!{link.iid}</ExtLink></span>
}

export function StagingErrors({ view, openSession }: { view: TeamStaging; openSession: (slot: string) => void }) {
  const rows = [...view.items].sort((a, b) => rank(a.state) - rank(b.state) || b.last_seen - a.last_seen)
  const shown = rows.slice(0, STAGING_ROWS)
  const on = view.projects.filter((p) => p.on).map((p) => p.project)
  return (
    <section className="br-card" aria-label="测试环境报错" data-testid="staging">
      <style>{ST_CSS}</style>
      <header>
        <RoleIcon role="alarm" size={18} />
        <h3>测试环境报错</h3>
        <span className="br-dim br-small">
          {view.on ? `${on.join('、')}：测试环境日志里的报错，运行机开 Issue、开分支修、开 MR，人来审、来合并，同时最多修 ${view.cap} 个` : '没打开'}
        </span>
        <span className="br-grow" />
        {view.on && <span className="br-small">在修 <b>{view.working}</b>/{view.cap}</span>}
      </header>
      {!view.on ? <p className="br-empty">{STAGING_OFF}</p> : rows.length === 0 ? (
        <p className="br-empty">{view.last_at ? `上一轮（${ago(view.last_at)}）测试环境的日志里没有报错。` : '还没看过测试环境：下一轮就看。'}</p>
      ) : (
        <div className="br-scroll">
          <table className="cto-table tm-runs">
            <thead><tr><th>报错</th><th>次数</th><th>状态</th><th>Issue</th><th>MR</th><th>会话</th></tr></thead>
            <tbody>
              {shown.map((i) => {
                const m = stagingMeta(i.state)
                return (
                  <tr key={`${i.project} ${i.signature}`} data-state={i.state}>
                    <td>
                      <span className="tm-pt-mr">
                        <code className="tm-st-sig">{i.signature}</code>
                        <span className="tm-pt-title">{i.project} · 最近一次 {ago(i.last_seen)}</span>
                      </span>
                    </td>
                    <td className="tm-st-count">{i.count}</td>
                    <td>
                      <span className="tm-state">
                        <span className={`br-chip kt-pill tone-${m.tone}${i.state === 'fixing' ? ' is-live' : ''}`}>{m.label}</span>
                        {i.note && <span className="tm-state-hint">{i.note}</span>}
                      </span>
                    </td>
                    <td>{i.issue ? <ExtLink href={i.issue.url} className="tm-mr">#{i.issue.iid}</ExtLink> : <span className="br-dim">—</span>}</td>
                    <td>
                      {i.origin || i.fix_mr ? (
                        <span className="tm-st-mrs">
                          {i.origin && <MrOf what="带进来的" link={i.origin} />}
                          {i.fix_mr && <MrOf what="修复" link={i.fix_mr} />}
                        </span>
                      ) : <span className="br-dim">—</span>}
                    </td>
                    <td>
                      {i.slot ? (
                        <button type="button" className="br-btn is-small" onClick={() => openSession(i.slot as string)}>
                          <Icon name="MessageSquare" size={13} />{ACT.openSession}
                        </button>
                      ) : <span className="br-dim">—</span>}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      {rows.length > shown.length && <p className="br-dim br-small">还有 {rows.length - shown.length} 个没列出：已关的和早先的。</p>}
      {view.projects.filter((p) => p.error).map((p) => (
        <p key={p.project} className="br-bad-text br-small" role="alert">{p.project} 这一轮没看成测试环境：{p.error}</p>
      ))}
    </section>
  )
}

const ST_CSS = `
.tm-st-sig { font-size: .88em; color: var(--text-strong); max-width: 30em; white-space: normal; overflow-wrap: anywhere; }
.tm-st-count { font-variant-numeric: tabular-nums; font-weight: 650; color: var(--text-strong); }
.tm-st-mrs { display: inline-grid; gap: .15em; font-size: .9em; white-space: nowrap; }
`
