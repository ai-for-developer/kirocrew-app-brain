/**
 * 运行机 on a personal install: the team runner of each listed repo as GitLab shows it (GET /team/status), read with
 * the person's own GitLab token; nothing here changes anything. A repo with a team runner is a card in 运行机's look:
 * the MRs it works on now (交接过来的, by their hand-over label; MR 巡检's, by its replies within recent_h hours), the
 * questions waiting on the person (each as 需求现状 shows a question, with the way to its note on the MR), when the
 * runner last acted, and why GitLab could not be read this time (the last read stays, with when it was). A repo whose
 * kit.json names no bridge_user is one line: 这个仓库还没有团队运行机.
 */
import { paths, usePoll } from '../api'
import { ago } from '../format'
import type { Question, RunnerMr, RunnerQuestion, RunnerStatus, TeamRunner } from '../types'
import { ExtLink, LoadState, RoleIcon } from '../ui'
import type { Tone } from '../styles'
import type { ViewProps } from './props'
import { QuestionCard } from './Question'
import { TM_CSS } from './Team'

export const RUNNER_POLL_MS = 30_000
export const NO_RUNNER = '这个仓库还没有团队运行机'
export const NO_RUNNER_YET = '团队运行机建好、它开的 kit.json MR 合并以后，这里就能看到它在做什么、在等你回答什么。'
export const WHO_UNKNOWN = '不知道你在这个仓库里是谁，看不出哪些问题在等你：在设置的「仓库」那一行填上你的 GitLab 用户名'

/** Why an MR counts as at work, in words. */
export const WHY: Record<RunnerMr['why'], { label: string; tone: Tone; hint: (h: number) => string }> = {
  running: { label: '在做', tone: 'run', hint: () => '交接过来的需求，运行机接着做' },
  patrol: { label: '巡检', tone: 'info', hint: (h) => `MR 巡检最近 ${h} 小时在上面回复过` },
}

/** A waiting question as 需求现状 and 待办 show one: on the MR, open, answered there (never here). */
const asQuestion = (q: RunnerQuestion): Question => ({
  source: 'mr', id: q.id, kind: q.kind, kind_label: q.kind_label, question: q.question, options: q.options,
  recommend: null, lean_request: null, refs: [], state: 'open', answer: null, answerer: null, voucher_id: null,
  mr_url: q.url, at: q.at, answerable_here: false, answer_via: null,
})

export function RunnerStatusPage({ pollMs }: ViewProps) {
  const got = usePoll<RunnerStatus>(paths.teamStatus(), pollMs ?? RUNNER_POLL_MS)
  const data = got.data
  return (
    <main className="kt-root br-view tm-root" data-testid="team-runner">
      <style>{TM_CSS}</style>
      <style>{RV_CSS}</style>
      <header className="tm-top">
        <RoleIcon role="runner" size={26} className="tm-top-icon" />
        <div className="br-view-head">
          <h2>团队运行机</h2>
          <p>团队服务器上的运行机在做什么、在等你回答什么：用你自己的 GitLab 令牌从 GitLab 读，这里只看</p>
        </div>
      </header>
      <LoadState loading={got.loading} error={got.error} has={!!data} />
      {data && data.repos.length === 0 && <p className="br-empty">还没有仓库：先在「导入仓库」里导入一个。</p>}
      {data && data.repos.length > 0 && data.repos.every((r) => !r.runner) && <p className="br-note">{NO_RUNNER_YET}</p>}
      {data?.repos.map((r) => (r.runner
        ? <RunnerCard key={r.repo} repo={r.repo} name={r.name} runner={r.runner} recentH={data.recent_h} />
        : (
          <section key={r.repo} className="br-card" aria-label={r.name} data-repo={r.repo}>
            <header>
              <RoleIcon role="file" size={18} />
              <h3>{r.name}</h3>
              <span className="br-dim">{NO_RUNNER}</span>
            </header>
          </section>
        )))}
    </main>
  )
}

function RunnerCard({ repo, name, runner, recentH }: { repo: string; name: string; runner: TeamRunner; recentH: number }) {
  const { seen, problem } = runner
  return (
    <section className={`br-card rv-repo${problem ? ' is-bad' : ''}`} aria-label={name} data-repo={repo}>
      <header>
        <RoleIcon role="runner" size={18} />
        <h3>{name}</h3>
        <span className="br-dim br-small" title={`运行机在 GitLab 上的账号：@${runner.bridge_user}`}>{runner.project}</span>
        <span className="br-grow" />
        {seen && (
          <span className="br-row br-small">
            <span className={`br-chip kt-pill tone-run${seen.at_work.length ? ' is-live' : ''}`}>在做 {seen.at_work.length}</span>
            <span className={`br-chip kt-pill tone-${seen.waiting.length ? 'wait' : 'idle'}`}>等你回答 {seen.waiting.length}</span>
            <span className="br-dim">上次动作 <b className="rv-strong">{seen.last_at ? ago(seen.last_at) : `${recentH} 小时里没有`}</b></span>
          </span>
        )}
      </header>
      {problem && (
        <div className="tm-loop-error" role="alert" data-testid="runner-problem">
          <p className="br-bad-text"><b>{problem.text}</b>{seen && `：下面是 ${ago(seen.at)}读到的`}</p>
          <span className="tm-do"><b>做什么</b>{problem.todo}</span>
          {problem.raw && <details className="br-raw"><summary>细节</summary><pre>{problem.raw}</pre></details>}
        </div>
      )}
      {seen && (
        <div className="rv-cols">
          <section className="rv-col" aria-label="在做的 MR">
            <h4>在做的 MR</h4>
            {seen.at_work.length === 0 ? <p className="br-empty">现在没有在做的 MR。</p> : (
              <div className="br-scroll">
                <table className="cto-table tm-runs">
                  <thead><tr><th>MR</th><th>为什么算在做</th><th>运行机上次留言</th></tr></thead>
                  <tbody>
                    {seen.at_work.map((m) => (
                      <tr key={m.iid} data-why={m.why}>
                        <td>
                          <span className="tm-pt-mr">
                            <ExtLink href={m.url} className="tm-mr">!{m.iid}</ExtLink>
                            <span className="tm-pt-title">{m.title}</span>
                          </span>
                        </td>
                        <td>
                          <span className="tm-state">
                            <span className={`br-chip kt-pill tone-${WHY[m.why].tone}${m.why === 'running' ? ' is-live' : ''}`}>{WHY[m.why].label}</span>
                            <span className="tm-state-hint">{WHY[m.why].hint(recentH)}</span>
                          </span>
                        </td>
                        <td className="br-dim">{ago(m.at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
          <section className="rv-col" aria-label="等你回答">
            <h4>等你回答</h4>
            {!runner.me ? <p className="br-warn-text br-small">{WHO_UNKNOWN}</p>
              : seen.waiting.length === 0 ? <p className="br-empty">没有等你回答的问题。</p> : seen.waiting.map((q) => (
                <div key={q.id} className="rv-q">
                  <span className="br-dim br-small">!{q.iid} {q.title}</span>
                  <QuestionCard q={asQuestion(q)} repo={repo} reqKey={q.id} />
                </div>
              ))}
          </section>
        </div>
      )}
      {seen && <p className="br-dim br-small rv-read">{ago(seen.at)}从 GitLab 读的</p>}
    </section>
  )
}

const RV_CSS = `
.rv-repo { gap: .8em; }
.rv-repo.is-bad { border-color: var(--kit-bad); }
.rv-strong { color: var(--text-strong); font-weight: 650; }
.rv-cols { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr); gap: var(--kit-gap-row, 12px) 1.4em; align-items: start; }
@container br-app (max-width: 1050px) { .rv-cols { grid-template-columns: minmax(0, 1fr); } }
.rv-col { display: grid; gap: .5em; min-width: 0; align-content: start; }
.rv-col h4 { margin: 0; font-size: .93em; color: var(--text-strong); }
.rv-q { display: grid; gap: .3em; }
.rv-read { margin: 0; }
`
