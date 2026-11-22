/**
 * 团队运行机 on a personal install (GET /team/status); nothing here changes anything. Where a repo's GitLab project has
 * brain/status, the team server writes that project's state there (状态中转) and this page reads it with the person's
 * own GitLab token, the projects of one GitLab put together: GitLab decides what a person sees, every project they
 * can read and no other. A strip with the server's version, when it last wrote, and each project (what it does, what
 * went wrong, how old its state is), 「团队服务器可能停了」 once the newest is older than stale_after_s; the runner at
 * work (its hand-overs with what each waits on, MR 巡检, 测试环境报错, as the team server's 运行机 shows them, without
 * its sessions; 从 issue 接活's labelled issues not started yet, with why, where a project takes issues); 全队交付,
 * every requirement of those projects (who, which project, its stage in 交付总览's words, MR and CI, deployed, what it
 * waits on), filtered by person and project, what waits on the person first; and the questions waiting on the person,
 * read from GitLab by this app. A repo whose project has no brain/status, or no snapshot read from it yet (its
 * project's line in the strip says why), is a card of the runner as GitLab shows it, as before an older team server:
 * the MRs it works on, the questions waiting on the person, when it last acted; one whose kit.json names no
 * bridge_user is one line, 这个仓库还没有团队运行机.
 */
import { useMemo, useState } from 'react'
import { paths, usePoll } from '../api'
import { ago, fmtWaited } from '../format'
import type { Question, RelayProject, RelayRead, RunnerMr, RunnerQuestion, RunnerRepo, RunnerStatus, RelayView, SnapshotDelivery, SnapshotWaits, TeamIssues, TeamPatrol, TeamRunner, TeamStaging } from '../types'
import { ExtLink, LoadState, RoleIcon } from '../ui'
import type { View } from '../nav'
import type { Tone } from '../styles'
import type { ViewProps } from './props'
import { QuestionCard } from './Question'
import { StagingErrors } from './StagingErrors'
import { Issues, Patrol, TM_CSS, runStateMeta } from './Team'

export const RUNNER_POLL_MS = 30_000
export const NO_RUNNER = '这个仓库还没有团队运行机'
export const NO_RUNNER_YET = '团队运行机建好、它开的 kit.json MR 合并以后，这里就能看到它在做什么、在等你回答什么。'
export const WHO_UNKNOWN = '不知道你在这个仓库里是谁，看不出哪些问题在等你：在设置的「仓库」那一行填上你的 GitLab 用户名'
export const STALE = (since: string) => `团队服务器可能停了：上次写状态是 ${since}前`
export const STALE_DO = '请管团队服务器的人看一下：它开着没有，团队模式还在不在跑。下面是它上次写的。'
export const PROJECT_STALE = (since: string) => `这个项目的状态是 ${since}前写的：团队服务器可能没在写它，看它的「运行机」页`
export const READ_FROM = '每个项目的状态写在它自己的 brain/status 分支上：你能看哪个项目，就读到哪个。团队服务器每 5 分钟最多写一次，没变化的 30 分钟写一次'
/** a project's 全队交付 read this long before its state was written is said (a round ran out of time) */
const LAG_S = 5 * 60

/** Why an MR counts as at work, in words. */
export const WHY: Record<RunnerMr['why'], { label: string; tone: Tone; hint: (h: number) => string }> = {
  running: { label: '在做', tone: 'run', hint: () => '交接过来的需求，运行机接着做' },
  patrol: { label: '巡检', tone: 'info', hint: (h) => `MR 巡检最近 ${h} 小时在上面回复过` },
}
/** the work sources a project's kit.json switches on, in words */
export const SOURCE_NAME: Record<string, string> = { 'mr-handoff': '交接', 'mr-patrol': '巡检', 'staging-errors': '测试环境报错', 'issue-label': '从 issue 接活' }
/** 交付总览's stages (deliveries.FLOW_STAGES), each with its tone; a closed one and a small one merged into its big one
 * are in no column there, their state's words stand for the stage */
export const STAGE_TONE: Record<string, Tone> = { 需求: 'idle', 设计: 'info', 构建: 'run', 交给运行机: 'hand', '发布 · 验收': 'wait', 已合并: 'ok', 已合进大需求: 'ok', 已关闭: 'idle' }
/** done: listed after the ones in progress */
const DONE: ReadonlySet<string> = new Set(['merged', 'merged_into_parent', 'closed'])
/** GitLab's pipeline statuses, in words */
export const CI_WORD: Record<string, { label: string; tone: Tone }> = {
  success: { label: '通过', tone: 'ok' }, failed: { label: '没过', tone: 'bad' }, running: { label: '在跑', tone: 'run' },
  pending: { label: '等着跑', tone: 'wait' }, created: { label: '等着跑', tone: 'wait' }, preparing: { label: '等着跑', tone: 'wait' },
  waiting_for_resource: { label: '等着跑', tone: 'wait' }, scheduled: { label: '等着跑', tone: 'wait' },
  manual: { label: '等人点', tone: 'warn' }, canceled: { label: '取消了', tone: 'idle' }, skipped: { label: '跳过了', tone: 'idle' },
}
const ciWord = (s: string) => CI_WORD[s] ?? { label: s, tone: 'idle' as Tone }
const NOBODY = '\u0000nobody'

/** A waiting question as 需求现状 and 待办 show one: on the MR, open, answered there (never here). */
const asQuestion = (q: RunnerQuestion): Question => ({
  source: 'mr', id: q.id, kind: q.kind, kind_label: q.kind_label, question: q.question, options: q.options,
  recommend: null, lean_request: null, refs: [], state: 'open', answer: null, answerer: null, voucher_id: null,
  mr_url: q.url, at: q.at, answerable_here: false, answer_via: null,
})

export const waitsOn = (w: SnapshotWaits, me: string | null): boolean => !!me && w.on.includes(me)

export function RunnerStatusPage({ pollMs, go }: ViewProps) {
  const got = usePoll<RunnerStatus>(paths.teamStatus(), pollMs ?? RUNNER_POLL_MS)
  const data = got.data
  const alone = data?.repos.filter((r) => !r.relay) ?? []
  return (
    <main className="kt-root br-view tm-root" data-testid="team-runner">
      <style>{TM_CSS}</style>
      <style>{RV_CSS}</style>
      <header className="tm-top">
        <RoleIcon role="runner" size={26} className="tm-top-icon" />
        <div className="br-view-head">
          <h2>团队运行机</h2>
          <p>团队服务器每几分钟把每个项目的状态写进它的 brain/status 分支，这里用你的 GitLab 令牌读你能看的项目，只看</p>
        </div>
      </header>
      <LoadState loading={got.loading} error={got.error} has={!!data} />
      {data && data.repos.length === 0 && <p className="br-empty">还没有仓库：先在「导入仓库」里导入一个。</p>}
      {data && data.repos.length > 0 && data.relays.length === 0 && data.repos.every((r) => !r.runner) && <p className="br-note">{NO_RUNNER_YET}</p>}
      {data?.relays.map((r) => (
        <RelayGroup key={r.repos.join(' ')} relay={r} at={data.at} repos={data.repos.filter((x) => x.relay && r.repos.includes(x.repo))} go={go} />
      ))}
      {alone.map((r) => (r.runner
        ? <RunnerCard key={r.repo} repo={r.repo} name={r.name} runner={r.runner} recentH={data?.recent_h ?? 24} />
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

/** The projects of one GitLab on the relay: the strip, the runner at work, 全队交付, and the questions their repos' MRs
 * wait on the person for. */
function RelayGroup({ relay, at, repos, go }: { relay: RelayRead; at: number; repos: RunnerRepo[]; go: (v: View) => void }) {
  const view = relay.view
  return (
    <>
      <RelayStrip relay={relay} at={at} />
      {view && <AtWork view={view} projects={relay.projects} me={relay.me} at={at} />}
      {view && <TeamDelivery view={view} me={relay.me} repoOf={relay.repo_of} at={at} go={go} />}
      <Waiting repos={repos} />
    </>
  )
}

function RelayStrip({ relay, at }: { relay: RelayRead; at: number }) {
  const view = relay.view
  const versions = Array.from(new Set(relay.projects.map((p) => p.version).filter((v): v is string => !!v)))
  const bad = relay.stale || relay.projects.some((p) => p.problem || p.stale || p.state?.error)
  return (
    <section className={`br-card rv-strip${bad ? ' is-bad' : ''}`} aria-label="团队服务器" data-testid="relay-strip">
      <header>
        <RoleIcon role="bridge" size={18} />
        <h3>团队服务器</h3>
        {versions.length > 0 && <span className="br-dim br-small" data-testid="relay-version">版本 {versions.join(' · ')}</span>}
        <span className="br-grow" />
        <span className="br-small" data-testid="relay-written">{view ? <>状态写于 <b className="rv-strong">{ago(view.generated_at, at)}</b></> : '还没读到状态'}</span>
      </header>
      {relay.stale && view && (
        <div className="tm-loop-error" role="alert" data-testid="relay-stale">
          <p className="br-bad-text"><b>{STALE(fmtWaited(at - view.generated_at))}</b></p>
          <span className="tm-do"><b>做什么</b>{STALE_DO}</span>
        </div>
      )}
      <ul className="rv-projects" aria-label="每个项目">
        {relay.projects.map((p) => <ProjectLine key={p.project} p={p} at={at} />)}
      </ul>
      <p className="br-dim br-small rv-read">{READ_FROM}</p>
    </section>
  )
}

/** One project of the strip: what the runner does there, what went wrong, and how old its state is when that says
 * something (stale, or its 全队交付 read rounds before). */
function ProjectLine({ p, at }: { p: RelayProject; at: number }) {
  const st = p.state
  const tone: Tone = p.problem || st?.error ? 'bad' : st?.on ? 'ok' : 'idle'
  const word = !st ? '还没读到' : st.error ? '有问题' : st.on ? '在接交接' : '不接交接'
  const lagging = p.delivery_at !== null && p.written_at !== null && p.written_at - p.delivery_at > LAG_S
  return (
    <li data-project={p.project} className={tone === 'bad' || p.stale ? 'is-bad' : undefined}>
      <b>{p.project}</b>
      <span className={`br-chip kt-pill tone-${tone}`}>{word}</span>
      {st && st.sources.length > 0 && <span className="br-dim br-small">{st.sources.map((s) => SOURCE_NAME[s] ?? s).join(' · ')}</span>}
      {p.stale && p.written_at !== null && <span className="br-bad-text br-small rv-project-error" data-testid="project-stale">{PROJECT_STALE(fmtWaited(at - p.written_at))}</span>}
      {st?.error && <span className="br-bad-text br-small rv-project-error">{st.error}</span>}
      {lagging && p.delivery_at !== null && <span className="br-dim br-small rv-project-error" data-testid="delivery-lag">全队交付是 {ago(p.delivery_at, at)}读完的</span>}
      {p.problem && (
        <span className="rv-project-error rv-problem" role="alert" data-testid="relay-problem">
          <span className="br-bad-text br-small"><b>{p.problem.text}</b>{st && p.read_at ? `：下面是 ${ago(p.read_at, at)}读到的` : ''}</span>
          <span className="tm-do br-small"><b>做什么</b>{p.problem.todo}</span>
          {p.problem.raw && <details className="br-raw"><summary>细节</summary><pre>{p.problem.raw}</pre></details>}
        </span>
      )}
    </li>
  )
}

/** The runner at work, as the team server's 运行机 shows it: its hand-overs (each with what it waits on), then MR 巡检 and
 * 测试环境报错 (their views as they are, without the sessions, which are the team server's), then the labelled issues
 * not started yet where a project takes issues. */
function AtWork({ view, projects, me, at }: { view: RelayView; projects: RelayProject[]; me: string | null; at: number }) {
  const { handovers, patrol, staging, issues } = view.runner
  const { cap, working, last_round_at } = view.server
  const on = (kind: string) => projects.some((p) => !!p.state?.sources.includes(kind))
  const patrolView: TeamPatrol = {
    on: on('mr-patrol'), cap, working: patrol.filter((i) => i.state === 'working').length, last_at: last_round_at,
    projects: projects.map((p) => ({ project: p.project, on: !!p.state?.sources.includes('mr-patrol'), error: '' })), items: patrol,
  }
  const stagingView: TeamStaging = {
    on: on('staging-errors'), cap, working: staging.filter((i) => i.state === 'fixing').length, last_at: last_round_at,
    projects: projects.map((p) => ({ project: p.project, on: !!p.state?.sources.includes('staging-errors'), error: '', deployed_sha: '' })), items: staging,
  }
  const issuesView: TeamIssues = {
    on: on('issue-label'), last_at: last_round_at,
    projects: projects.map((p) => ({ project: p.project, on: !!p.state?.sources.includes('issue-label'), label: p.state?.issue_label ?? '', error: '' })), items: issues,
  }
  return (
    <>
      <section className="br-card" aria-label="运行机在做" data-testid="relay-runner">
        <header>
          <RoleIcon role="runner" size={18} />
          <h3>运行机在做</h3>
          <span className="br-dim br-small">交接过来的需求 {handovers.length} 个</span>
          <span className="br-grow" />
          <span className="br-small" title="MR 巡检和测试环境报错一起用的 brain-patrol 会话">brain-patrol 会话在做 <b>{working}</b>/{cap}</span>
        </header>
        {handovers.length === 0 ? <p className="br-empty">运行机现在没有交接过来的需求。</p> : (
          <div className="br-scroll">
            <table className="cto-table tm-runs" data-testid="relay-handovers">
              <thead><tr><th>需求</th><th>状态</th><th>在等</th></tr></thead>
              <tbody>
                {handovers.map((h) => {
                  const m = runStateMeta(h.state)
                  const mine = waitsOn(h.waits, me)
                  return (
                    <tr key={h.run_id} data-key={h.key} data-mine={mine || undefined}>
                      <td>
                        <span className="tm-pt-mr">
                          <span><b className="br-key">{h.key}</b> <ExtLink href={h.mr_url} className="tm-mr">!{h.iid}</ExtLink></span>
                          <span className="tm-pt-title">{h.title} · {h.owner ?? '没人认领'} · {h.project}</span>
                        </span>
                      </td>
                      <td><span className={`br-chip kt-pill tone-${m.tone}${h.state === 'dispatched' ? ' is-live' : ''}`} title={m.hint}>{m.label}</span></td>
                      <td><WaitsCell w={h.waits} mine={mine} at={at} /></td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <Patrol view={patrolView} />
      <StagingErrors view={stagingView} />
      {issuesView.on && <Issues view={issuesView} me={me} waiting />}
    </>
  )
}

function WaitsCell({ w, mine, at }: { w: SnapshotWaits; mine: boolean; at: number }) {
  return (
    <span className="rv-waits">
      {mine && <span className="br-chip kt-pill tone-wait" data-testid="waits-me">等你</span>}
      <span>{w.text || '—'}</span>
      {w.since !== null && w.who !== 'nobody' && <span className="br-dim br-small">已等 {fmtWaited(at - w.since)}</span>}
    </span>
  )
}

/** 全队交付: every requirement of the team's projects, what waits on the person first, by person and project. A key of a
 * project this machine lists opens its 需求现状 here. */
function TeamDelivery({ view, me, repoOf, at, go }: { view: RelayView; me: string | null; repoOf: Record<string, string>; at: number; go: (v: View) => void }) {
  const [who, setWho] = useState('')
  const [project, setProject] = useState('')
  const people = useMemo(() => Array.from(new Set(view.delivery.map((r) => r.owner ?? NOBODY))).sort((a, b) => (a === me ? -1 : b === me ? 1 : a.localeCompare(b))), [view, me])
  const projects = useMemo(() => Array.from(new Set(view.delivery.map((r) => r.project))).sort(), [view])
  const rows = useMemo(() => view.delivery
    .filter((r) => (!who || (r.owner ?? NOBODY) === who) && (!project || r.project === project))
    .sort((a, b) => Number(waitsOn(b.waits, me)) - Number(waitsOn(a.waits, me))
      || Number(DONE.has(a.state)) - Number(DONE.has(b.state)) || (b.updated_at ?? 0) - (a.updated_at ?? 0)), [view, who, project, me])
  const mine = view.delivery.filter((r) => waitsOn(r.waits, me)).length
  return (
    <section className="br-card" aria-label="全队交付" data-testid="team-delivery">
      <header>
        <RoleIcon role="team" size={18} />
        <h3>全队交付</h3>
        <span className="br-dim br-small" data-testid="delivery-count">{view.delivery.length} 个需求{me ? ` · 等你的 ${mine} 个` : ''} · 在做的和近 14 天合并、关掉的</span>
        <span className="br-grow" />
        <label className="rv-filter">人
          <select value={who} onChange={(e) => setWho(e.target.value)} data-testid="filter-person">
            <option value="">全部</option>
            {people.map((p) => <option key={p} value={p}>{p === NOBODY ? '没人认领' : p === me ? `你（${p}）` : p}</option>)}
          </select>
        </label>
        <label className="rv-filter">项目
          <select value={project} onChange={(e) => setProject(e.target.value)} data-testid="filter-project">
            <option value="">全部</option>
            {projects.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
      </header>
      {rows.length === 0 ? <p className="br-empty">{view.delivery.length ? '这个筛选下没有需求。' : '这些项目里现在没有在做的需求，近 14 天也没有合并、关掉的。'}</p> : (
        <div className="br-scroll">
          <table className="cto-table tm-runs rv-delivery" data-testid="delivery-table">
            <thead><tr><th>需求</th><th>阶段</th><th>MR · 部署</th><th>在等</th></tr></thead>
            <tbody>
              {rows.map((r) => <DeliveryRow key={`${r.project} ${r.key}`} r={r} me={me} repo={repoOf[r.project]} at={at} go={go} />)}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function DeliveryRow({ r, me, repo, at, go }: { r: SnapshotDelivery; me: string | null; repo?: string; at: number; go: (v: View) => void }) {
  const mine = waitsOn(r.waits, me)
  const ci = r.mr?.ci ? ciWord(r.mr.ci) : null
  return (
    <tr data-key={r.key} data-project={r.project} data-mine={mine || undefined}>
      <td>
        <span className="tm-pt-mr">
          <span>
            {repo ? <button type="button" className="br-link br-key" title="打开它的需求现状" onClick={() => go({ kind: 'item', repo, key: r.key, from: 'team' })}>{r.key}</button>
              : <b className="br-key">{r.key}</b>}
            {' '}<span className="rv-title">{r.title}</span>
          </span>
          <span className="tm-pt-title">{r.owner === null ? '没人认领' : r.owner === me ? `你（${r.owner}）` : r.owner} · {r.project}</span>
        </span>
      </td>
      <td><span className={`br-chip kt-pill tone-${STAGE_TONE[r.stage] ?? 'idle'}`} data-testid="stage">{r.stage}</span></td>
      <td>
        <span className="rv-mr">
          {r.mr ? <span><ExtLink href={r.mr.url} className="tm-mr">!{r.mr.iid}</ExtLink>{ci && <span className={`br-chip kt-pill tone-${ci.tone}`} data-testid="ci">CI {ci.label}</span>}</span> : <span className="br-dim">没开 MR</span>}
          {r.state === 'merged' && (
            <span className="br-small" data-testid="deployed">
              {r.deployed === true ? <span className="rv-ok">已部署到测试环境</span> : r.deployed === false ? <span className="rv-wait">还没部署到测试环境</span> : <span className="br-dim">没看测试环境</span>}
            </span>
          )}
        </span>
      </td>
      <td><WaitsCell w={r.waits} mine={mine} at={at} /></td>
    </tr>
  )
}

/** 等你回答: the bridge's open questions on these repos' MRs that name the person, read from GitLab with their token. */
function Waiting({ repos }: { repos: RunnerRepo[] }) {
  const runners = repos.filter((r): r is RunnerRepo & { runner: TeamRunner } => !!r.runner)
  if (runners.length === 0) return null
  const unknown = runners.some((r) => !r.runner.me)
  const items = runners.flatMap((r) => (r.runner.seen?.waiting ?? []).map((q) => ({ q, repo: r })))
  return (
    <section className="br-card" aria-label="等你回答" data-testid="relay-waiting">
      <header>
        <RoleIcon role="question" size={18} />
        <h3>等你回答</h3>
        <span className="br-dim br-small">运行机在 MR 上问你的，从 GitLab 读</span>
      </header>
      {unknown && <p className="br-warn-text br-small">{WHO_UNKNOWN}</p>}
      {items.length === 0 ? (!unknown && <p className="br-empty">没有等你回答的问题。</p>) : items.map(({ q, repo }) => (
        <div key={`${repo.repo} ${q.id}`} className="rv-q">
          <span className="br-dim br-small">{repo.name} !{q.iid} {q.title}</span>
          <QuestionCard q={asQuestion(q)} repo={repo.repo} reqKey={q.id} />
        </div>
      ))}
      {runners.filter((r) => r.runner.problem).map((r) => (
        <p key={r.repo} className="br-bad-text br-small" role="alert">{r.name}：{r.runner.problem?.text}（问题没读到）</p>
      ))}
    </section>
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
.rv-repo.is-bad, .rv-strip.is-bad { border-color: var(--kit-bad); }
.rv-strong { color: var(--text-strong); font-weight: 650; }
.rv-cols { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr); gap: var(--kit-gap-row, 12px) 1.4em; align-items: start; }
@container br-app (max-width: 1050px) { .rv-cols { grid-template-columns: minmax(0, 1fr); } }
.rv-col { display: grid; gap: .5em; min-width: 0; align-content: start; }
.rv-col h4 { margin: 0; font-size: .93em; color: var(--text-strong); }
.rv-q { display: grid; gap: .3em; }
.rv-read { margin: 0; }
.rv-projects { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: .4em 1.2em; }
.rv-projects li { display: inline-flex; flex-wrap: wrap; align-items: baseline; gap: .45em; min-width: 0; }
.rv-projects li b { color: var(--text-strong); font-weight: 650; }
.rv-project-error { flex-basis: 100%; }
.rv-problem { display: grid; gap: .2em; }
.rv-filter { display: inline-flex; align-items: center; gap: .35em; font-size: .88em; color: var(--muted); }
.rv-filter select { font: inherit; color: var(--text); background: var(--card); border: 1px solid var(--border); border-radius: 6px; padding: 2px 6px; max-width: 12em; }
.rv-delivery td { vertical-align: top; }
.rv-delivery td:first-child { min-width: 9.5em; }
.rv-delivery tr[data-mine] td:first-child { box-shadow: inset 3px 0 0 var(--kt-approve, var(--kit-warn)); }
.rv-title { color: var(--text-strong); }
.rv-mr { display: inline-grid; gap: .2em; }
.rv-mr .br-chip { margin-left: .4em; }
.rv-ok { color: var(--kt-ok); }
.rv-wait { color: var(--kt-approve, var(--kit-warn)); }
.rv-waits { display: inline-flex; flex-wrap: wrap; align-items: baseline; gap: .2em .45em; }
`
