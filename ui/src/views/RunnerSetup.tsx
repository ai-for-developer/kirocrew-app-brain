/**
 * 一键建运行机 (设置, with 团队模式): the team runner in same-user mode, built by the app from GitLab tokens typed once
 * (backend/core/team/setup.py). 账号 picks where the runner's GitLab accounts come from: 项目令牌 (one project; the
 * one-time token makes project access tokens) or 机器人账号 (the projects one per line, served as two GitLab users of the
 * runner's own: the bridge's personal token, and the bot's when the runner pushes as it rather than over this machine's
 * SSH; a personal token left empty is the one this machine kept, and the one-time token is optional).
 * POST /team/setup starts its steps in the background; GET /team/setup is read every second while they run. Each step
 * says ✓ when done, 等人 when it waits for a person (a kit.json MR, with its link), ✕ when it stopped the run, then its
 * words, and what GitLab or a command said under 细节; a step about two projects or more lists each project under it
 * with its own mark, words and link. A run cut off part-way (the app stopped) says where. The tokens go in that one
 * request: this page keeps them in their fields while it is open (for 再跑一次); the backend keeps the two accounts'
 * personal tokens and Jenkins' only. 巡检所有开着的 MR (on unless unticked) adds brain-patrol and switches work source
 * mr-patrol on in the kit.json MRs; 从打了标签的 issue 接活 (off unless ticked) switches work source issue-label on there
 * and makes its label. With several projects each of the two is ticked project by project, and a project left unticked
 * gets no MR for it. Jenkins (address, read-only user, its token; one for the runner, whatever the projects) is for
 * MR 巡检 to read failed builds' logs: sent only with an address; the backend keeps the token, so the last run's address
 * and user come back, not it.
 */
import { useEffect, useRef, useState, type MutableRefObject, type ReactNode } from 'react'
import { useAppApi } from '@kirocrew/app-sdk'
import { paths, useAction, usePoll } from '../api'
import { ago } from '../format'
import { useLatest } from '../hooks'
import type { Tone } from '../styles'
import type { RepoInfo, SetupMode, SetupProjectRow, SetupStep, SetupStepStatus, SetupToken, TeamSetup, TeamSetupBody } from '../types'
import { ExtLink, LoadState, RoleIcon } from '../ui'

export const SETUP_POLL_MS = 1000
export const TOKEN_HINT = '只用这一次：建令牌、标签和 MR，用完就丢，不存到任何地方。要有 api 权限，你在项目里要是 Maintainer 或 Owner。'
export const ROLES_HINT = '填 GitLab 用户名，几个人用逗号隔开；空着就是 token 的主人。安全和数据两个角色也是 token 的主人。'
export const SAME_USER_NOTE = '签名服务、app 和 AI 的会话都用这台机器的同一个系统用户：签名挡不住 app 自己，只适合试用。'
export const PATROL_HINT = '运行机每一轮看这个项目所有开着的 MR，以前开的也算：流水线没过、评审意见没回、和目标分支有冲突的，派 brain-patrol 去修，同时最多 2 个。交接过来的 MR 还是交接那边管。'
export const JENKINS_HINT = 'MR 的流水线在 Jenkins 上跑的话填：巡检用这个只读用户（要有 Overall/Read 和 Job/Read）读没过的构建的日志。地址写这台机器连得上的。token 存在这台机器上，再跑一次时空着就用上次存的。不用 Jenkins 就空着。'
export const ISSUES_HINT = '项目里 Developer 以上的人给 issue 打上标签 brain，运行机就把它认领成需求、开分支和 MR，在 MR 上问问题；MR 合并时 issue 跟着关。issue 的内容要过 Brain 的内容检查：不能有密钥、个人信息和金额。'
export const TOKEN_FIRST = '先填一次性 token：它只用这一次，不存'
export const cutOffSaid = (title: string) => `上次跑到「${title}」就停了，没跑完（app 关了或者重启了）：再跑一次，接着把没做的建完`
export const MODE_HINT: Record<SetupMode, string> = {
  'project-token': 'app 用你的一次性 token 给这个项目建两个项目令牌，桥和推送各用一个。项目令牌只看得到自己的项目，这台运行机就只管这一个。',
  'bot-account': '先在 GitLab 上建一个专门给桥用的用户（比如 brain-bridge），把它加进每个项目，角色 Developer。运行机用它的个人令牌管下面所有项目，不建项目令牌。',
}
export const BRIDGE_HINT = '桥的账号的个人令牌，要有 api 权限。它存在这台机器上（只有你能读），桥和签名服务都用它。'
export const BOT_HINT = '推代码的另一个专门用户（比如 brain-bot），每个项目里是 Developer；它的个人令牌要有 write_repository 和 read_user 权限，也存在这台机器上。'
export const SSH_HINT = '这台机器的 SSH key 要能拉取、推送每个项目：在这台机器上对每个项目的 SSH 地址跑 git ls-remote 能通。'
export const OPTIONAL_TOKEN_HINT = '可以不填。只用这一次：建 MR 标签、开 kit.json 的 MR，用完就丢，不存。不填就用桥的账号来建（它是 Developer 就够），下面的产品和开发就都要填。'
export const ROLES_HINT_NO_TOKEN = '填 GitLab 用户名，几个人用逗号隔开。没填一次性 token：产品和开发都要填，安全和数据两个角色跟开发一样。'
export const BRIDGE_FIRST = '先填桥的账号的个人令牌：这台机器上还没有存'
export const BOT_FIRST = '先填机器人账号的个人令牌，或者推送选「这台机器的 SSH」'
export const keptSaid = (name: string) => `空着就用这台机器上存的 ${name} 的令牌。`
/** The runner's GitLab tokens (TeamSetup.tokens) in words: whose, which kind, when it ends, and what to do once it ends
 * within two weeks: the next run gives a project token a new value, a personal token is its owner's to replace. */
const TOKEN_WHAT: Record<SetupToken['key'], string> = { bridge: '桥的令牌', bot: '推送的令牌' }
const tokenKind = (t: SetupToken) => (t.kind === 'project' ? `项目令牌 ${t.name}` : '个人令牌')
const tokenEnd = (t: SetupToken) => (t.expires_at === null || t.days_left === null ? 'GitLab 没说它哪天到期'
  : t.days_left < 0 ? `${t.expires_at} 已经过期了` : `${t.expires_at} 到期，还有 ${t.days_left} 天`)
const tokenDo = (t: SetupToken) => (t.kind === 'project' ? '再跑一次：它给这个项目令牌换上新的值，账号不变'
  : `在 GitLab 上给 ${t.user} 换一个新的个人令牌，再跑一次填上`)
const LEAD: Record<SetupMode, string> = {
  'project-token': '填一次 GitLab 地址、项目和一个一次性 token，app 自己建好运行机要的一切：两个项目令牌、MR 标签、身份表、签名密钥、三份配置、brain-run 和 brain-patrol、签名服务，最后打开团队模式。你只要在 GitLab 上合并它开的 kit.json MR。',
  'bot-account': '填 GitLab 地址、几个项目和桥的账号的个人令牌，app 给每个项目建好 MR 标签、开 kit.json 的 MR，再建好身份表、签名密钥、三份配置、brain-run 和 brain-patrol、签名服务，最后打开团队模式。你只要在 GitLab 上合并每个项目的 kit.json MR。',
}

/** A step's state as the list shows it: its glyph, its word, its colour, and the check row's look it borrows. */
export const STEP_LOOK: Record<SetupStepStatus, { glyph: string; word: string; tone: Tone; look: string }> = {
  done: { glyph: '✓', word: '好了', tone: 'ok', look: 'is-OK' },
  waiting: { glyph: '!', word: '等人', tone: 'warn', look: 'is-WARN' },
  failed: { glyph: '✕', word: '没成', tone: 'bad', look: 'is-FAIL' },
  skipped: { glyph: '–', word: '跳过', tone: 'idle', look: 'is-quiet' },
  running: { glyph: '●', word: '正在做', tone: 'run', look: 'is-running' },
  todo: { glyph: '○', word: '还没做', tone: 'idle', look: 'is-quiet' },
}

type Push = 'ssh' | 'token'
/** A switch ticked project by project: lower-case path -> on; '' stands for a project not typed yet. */
type Ticks = Record<string, boolean>
interface Form { mode: SetupMode; base: string; project: string; projects: string; product: string; builder: string; patrol: Ticks; issues: Ticks; push: Push; jenkinsBase: string; jenkinsUser: string }

const names = (s: string) => s.split(/[,，、\s]+/).map((x) => x.trim()).filter(Boolean)
const tickKey = (project: string) => project.trim().toLowerCase()
/** Whether a switch is on in *project*: as ticked, else as ticked before a project was typed, else its default. */
const ticked = (t: Ticks, project: string, dflt: boolean) => t[tickKey(project)] ?? t[''] ?? dflt
/** The projects the form names now, each once. */
const projectsOf = (f: Form) => [...new Map((f.mode === 'bot-account' ? names(f.projects) : names(f.project)).map((p) => [tickKey(p), p])).values()]
/** A switch of the last run's input (the projects it was on in) as ticks over the projects it ran on. */
const ticksOf = (ran: string[], on: string[]): Ticks => {
  const yes = new Set(on.map(tickKey))
  return Object.fromEntries(ran.map((p) => [tickKey(p), yes.has(tickKey(p))]))
}

/** The last run's input, else the repos on GitLab (their kit.json's): the first one for 项目令牌, all of them for 机器人账号. */
function formOf(s: TeamSetup, repos: RepoInfo[]): Form {
  const kits = repos.flatMap((r) => (r.kit?.gitlab_project ? [r.kit] : []))
  const listed = [...new Set(kits.map((k) => k.gitlab_project))]
  const ran = s.input.projects
  return {
    mode: s.input.mode,
    base: s.input.gitlab_base || kits[0]?.gitlab_base || '',
    project: ran[0] || listed[0] || '',
    projects: (s.input.mode === 'bot-account' || !listed.length ? ran : listed).join('\n'),
    product: s.input.roles.product.join(', '),
    builder: s.input.roles.builder.join(', '),
    patrol: ticksOf(ran, s.input.patrol),
    issues: ticksOf(ran, s.input.issues),
    push: s.input.push === 'token' ? 'token' : 'ssh',
    jenkinsBase: s.input.jenkins.base,
    jenkinsUser: s.input.jenkins.user,
  }
}

/** The last run in a word: still going, waiting for a person, stopped at a step or cut off part-way, or done. */
function runLook(s: TeamSetup): { label: string; tone: Tone } | null {
  if (s.running) return { label: '正在建', tone: 'run' }
  if (s.cut_off || s.steps.some((x) => x.status === 'failed')) return { label: '没建完', tone: 'bad' }
  if (s.steps.every((x) => x.status === 'todo')) return null
  if (s.steps.some((x) => x.status === 'waiting')) return { label: '等人', tone: 'warn' }
  return { label: '建好了', tone: 'ok' }
}

/** *onDone*: a run this page started or saw going has ended (it may have switched team mode on). */
export function RunnerSetupCard({ repos, onDone, pollMs = SETUP_POLL_MS }: { repos: RepoInfo[]; onDone?: () => void; pollMs?: number }) {
  const api = useAppApi()
  const [running, setRunning] = useState(false)
  const got = usePoll<TeamSetup>(paths.teamSetup(), running ? pollMs : 0)
  const [form, setForm] = useState<Form | null>(null)
  const [token, setToken] = useState('')
  const [bridge, setBridge] = useState('')
  const [bot, setBot] = useState('')
  const [jenkinsToken, setJenkinsToken] = useState('')
  const [ask, setAsk] = useState('')
  const tokenRef = useRef<HTMLInputElement | null>(null)
  const bridgeRef = useRef<HTMLInputElement | null>(null)
  const botRef = useRef<HTMLInputElement | null>(null)
  const following = useRef(false)
  const start = useAction((body: TeamSetupBody) => api.post<TeamSetup>(paths.teamSetup(), body))
  const doneRef = useLatest(onDone)
  const data = got.data
  useEffect(() => {
    if (data && !form) setForm(formOf(data, repos))
  }, [data]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!data) return
    setRunning(data.running)
    if (data.running) following.current = true
    else if (following.current) {
      following.current = false
      doneRef.current?.()
    }
  }, [data, doneRef])
  const edit = (f: Partial<Form>) => setForm((x) => (x ? { ...x, ...f } : x))
  const kept = data?.accounts ?? { bridge: '', bot: '' }
  /** What must be typed before a run can start, and where to type it. */
  const missing = (f: Form): [string, HTMLInputElement | null] | null => {
    if (f.mode === 'project-token') return token.trim() ? null : [TOKEN_FIRST, tokenRef.current]
    if (!bridge.trim() && !kept.bridge) return [BRIDGE_FIRST, bridgeRef.current]
    if (f.push === 'token' && !bot.trim() && !kept.bot) return [BOT_FIRST, botRef.current]
    return null
  }
  const bodyOf = (f: Form): TeamSetupBody => {
    const jenkins = f.jenkinsBase.trim()
      ? { jenkins: { base: f.jenkinsBase.trim(), user: f.jenkinsUser.trim(), token: jenkinsToken.trim() } }
      : {}
    const listed = projectsOf(f)
    const common = {
      gitlab_base: f.base.trim(), roles: { product: names(f.product), builder: names(f.builder) },
      patrol: listed.filter((p) => ticked(f.patrol, p, true)), issues: listed.filter((p) => ticked(f.issues, p, false)), ...jenkins,
    }
    if (f.mode === 'project-token') return { ...common, project: f.project.trim(), admin_token: token.trim() }
    return {
      ...common, mode: 'bot-account', projects: names(f.projects), push: f.push,
      ...(token.trim() ? { admin_token: token.trim() } : {}),
      ...(bridge.trim() ? { bridge_token: bridge.trim() } : {}),
      ...(f.push === 'token' && bot.trim() ? { bot_token: bot.trim() } : {}),
    }
  }
  const go = async () => {
    if (!form) return
    const need = missing(form)
    if (need) {
      setAsk(need[0])
      need[1]?.focus()
      return
    }
    setAsk('')
    const r = await start.run(bodyOf(form))
    if (r) {
      following.current = true
      got.set(r)
    }
  }
  const look = data ? runLook(data) : null
  const ran = !!data && (!!data.cut_off || data.steps.some((s) => s.status !== 'todo'))
  const cutAt = data?.cut_off ? data.steps.find((s) => s.id === data.cut_off)?.title ?? data.cut_off : null
  const busy = running || start.busy
  const bots = form?.mode === 'bot-account'
  const said = (
    <>
      {ask && <span className="br-error" role="alert">{ask}</span>}
      {start.error && <span className="br-error" role="alert">{start.error}</span>}
    </>
  )
  const secret = (id: string, label: string, value: string, set: (v: string) => void, hint: ReactNode, ref: MutableRefObject<HTMLInputElement | null>) => (
    <div className="br-field">
      <label htmlFor={id}>{label}</label>
      <input id={id} ref={ref} type="password" className="br-mono" autoComplete="off" spellCheck={false} value={value}
        aria-describedby={`${id}-hint`} onChange={(e) => { set(e.target.value); setAsk('') }} />
      <p className="br-hint" id={`${id}-hint`}>{hint}</p>
    </div>
  )
  return (
    <section className="br-card rs-card" aria-label="一键建运行机" data-testid="runner-setup">
      <style>{RS_CSS}</style>
      <header>
        <RoleIcon role="runner" size={18} />
        <h3>一键建运行机</h3>
        <span className="br-dim br-small">同一个系统用户 · 只适合试用</span>
        <span className="br-grow" />
        {look && <span className={`br-chip kt-pill tone-${look.tone}${running ? ' is-live' : ''}`} data-testid="setup-state">{look.label}</span>}
      </header>
      <p className="rs-lead">{LEAD[form?.mode ?? 'project-token']}</p>
      <p className="br-banner br-small rs-note" role="note">{SAME_USER_NOTE}</p>
      <LoadState loading={got.loading} error={got.error} has={!!data} />
      {form && (
        <form className="br-form" noValidate onSubmit={(e) => { e.preventDefault(); void go() }}>
          <fieldset className="rs-fieldset" disabled={busy}>
            <Choice name="mode" label="账号" value={form.mode} onPick={(mode) => { edit({ mode }); setAsk('') }} hint={MODE_HINT[form.mode]}
              options={[['project-token', '项目令牌：一个项目'], ['bot-account', '机器人账号：几个项目']]} />
            <div className="rs-grid">
              <div className="br-field">
                <label htmlFor="rs-base">GitLab 地址</label>
                <input id="rs-base" type="text" className="br-mono" value={form.base} placeholder="https://gitlab.example.com" onChange={(e) => edit({ base: e.target.value })} />
              </div>
              {bots ? (
                <div className="br-field">
                  <label htmlFor="rs-projects">项目（一行一个）</label>
                  <textarea id="rs-projects" rows={Math.min(8, Math.max(3, form.projects.split('\n').length + 1))} value={form.projects}
                    placeholder={'组/仓库\n组/另一个仓库'} spellCheck={false} onChange={(e) => edit({ projects: e.target.value })} />
                </div>
              ) : (
                <div className="br-field">
                  <label htmlFor="rs-project">项目</label>
                  <input id="rs-project" type="text" className="br-mono" value={form.project} placeholder="组/仓库" onChange={(e) => edit({ project: e.target.value })} />
                </div>
              )}
            </div>
            {bots && secret('rs-bridge', '桥的账号的个人令牌', bridge, setBridge,
              <>{BRIDGE_HINT}{kept.bridge && <b className="rs-kept">{keptSaid(kept.bridge)}</b>}</>, bridgeRef)}
            {bots && (
              <>
                <Choice name="push" label="运行机怎么推送" value={form.push} onPick={(push) => { edit({ push }); setAsk('') }}
                  hint={form.push === 'ssh' ? SSH_HINT : undefined} options={[['ssh', '这台机器的 SSH'], ['token', '机器人账号']]} />
                {form.push === 'token' && secret('rs-bot', '机器人账号的个人令牌', bot, setBot,
                  <>{BOT_HINT}{kept.bot && <b className="rs-kept">{keptSaid(kept.bot)}</b>}</>, botRef)}
              </>
            )}
            {secret('rs-token', bots ? '一次性 token（可以不填）' : '一次性 token', token, setToken, bots ? OPTIONAL_TOKEN_HINT : TOKEN_HINT, tokenRef)}
            <div className="rs-grid">
              <div className="br-field">
                <label htmlFor="rs-product">产品（回答需求的问题、验收）</label>
                <input id="rs-product" type="text" value={form.product} placeholder={bots && !token.trim() ? 'GitLab 用户名' : 'token 的主人'} aria-describedby="rs-roles-hint" onChange={(e) => edit({ product: e.target.value })} />
              </div>
              <div className="br-field">
                <label htmlFor="rs-builder">开发（回答设计和计划的问题）</label>
                <input id="rs-builder" type="text" value={form.builder} placeholder={bots && !token.trim() ? 'GitLab 用户名' : 'token 的主人'} aria-describedby="rs-roles-hint" onChange={(e) => edit({ builder: e.target.value })} />
              </div>
            </div>
            <p className="br-hint" id="rs-roles-hint">{bots && !token.trim() ? ROLES_HINT_NO_TOKEN : ROLES_HINT}</p>
            <Switch id="rs-patrol" label="巡检所有开着的 MR" hint={PATROL_HINT} projects={projectsOf(form)} ticks={form.patrol} dflt
              onTick={(patrol) => edit({ patrol })} />
            <Switch id="rs-issues" label="从打了标签的 issue 接活" hint={ISSUES_HINT} projects={projectsOf(form)} ticks={form.issues}
              dflt={false} onTick={(issues) => edit({ issues })} />
            <div className="rs-grid">
              <div className="br-field">
                <label htmlFor="rs-jenkins">Jenkins 地址</label>
                <input id="rs-jenkins" type="text" className="br-mono" value={form.jenkinsBase} placeholder="不用 Jenkins 就空着" aria-describedby="rs-jenkins-hint" onChange={(e) => edit({ jenkinsBase: e.target.value })} />
              </div>
              <div className="br-field">
                <label htmlFor="rs-jenkins-user">Jenkins 只读用户</label>
                <input id="rs-jenkins-user" type="text" className="br-mono" value={form.jenkinsUser} placeholder="brain-reader" aria-describedby="rs-jenkins-hint" onChange={(e) => edit({ jenkinsUser: e.target.value })} />
              </div>
            </div>
            <div className="br-field">
              <label htmlFor="rs-jenkins-token">Jenkins token</label>
              <input id="rs-jenkins-token" type="password" className="br-mono" autoComplete="off" spellCheck={false} value={jenkinsToken}
                aria-describedby="rs-jenkins-hint" onChange={(e) => setJenkinsToken(e.target.value)} />
              <p className="br-hint" id="rs-jenkins-hint">{JENKINS_HINT}</p>
            </div>
          </fieldset>
          {!ran && (
            <div className="br-row">
              <button type="submit" className="br-btn is-primary" disabled={busy}>{busy ? '正在建…' : '开始建'}</button>
              {said}
            </div>
          )}
        </form>
      )}
      {data && ran && (
        <>
          {!running && cutAt && <p className="br-banner br-small rs-note" role="status" data-testid="setup-cut-off">{cutOffSaid(cutAt)}</p>}
          <ol className="br-probs rs-steps" aria-label="建运行机的每一步">
            {data.steps.map((s) => <StepRow key={s.id} s={s} />)}
          </ol>
          {data.tokens.length > 0 && <Tokens tokens={data.tokens} />}
          <div className="br-row">
            <button type="button" className="br-btn is-primary" disabled={busy} onClick={() => void go()}>{busy ? '正在建…' : '再跑一次'}</button>
            {!running && data.ended_at && <span className="br-dim br-small">上次 {ago(data.ended_at)}跑完；再跑一次只补没做成的，已经有的不会再建一份</span>}
            {said}
          </div>
        </>
      )}
    </section>
  )
}

/** A work source the runner takes in a project: one checkbox for one project, one per project for several. */
function Switch({ id, label, hint, projects, ticks, dflt, onTick }: { id: string; label: string; hint: string; projects: string[]; ticks: Ticks; dflt: boolean; onTick: (t: Ticks) => void }) {
  const tick = (project: string, on: boolean) => onTick({ ...ticks, [tickKey(project)]: on })
  if (projects.length < 2) {
    const one = projects[0] ?? ''
    return (
      <div className="br-field">
        <label className="br-check">
          <input type="checkbox" checked={ticked(ticks, one, dflt)} aria-describedby={`${id}-hint`} onChange={(e) => tick(one, e.target.checked)} />
          <b>{label}</b>
        </label>
        <p className="br-hint" id={`${id}-hint`}>{hint}</p>
      </div>
    )
  }
  return (
    <div className="br-field" role="group" aria-labelledby={`${id}-l`} aria-describedby={`${id}-hint`}>
      <span className="br-label" id={`${id}-l`}>{label}</span>
      <div className="rs-each">
        {projects.map((p) => (
          <label key={tickKey(p)} className="br-check">
            <input type="checkbox" checked={ticked(ticks, p, dflt)} onChange={(e) => tick(p, e.target.checked)} />
            <span className="br-mono rs-each-name">{p}</span>
          </label>
        ))}
      </div>
      <p className="br-hint" id={`${id}-hint`}>{hint}</p>
    </div>
  )
}

/** Two or three ways of one thing, as pills (导入仓库's 跟踪方式), and a line about the one picked. */
function Choice<T extends string>({ name, label, value, options, onPick, hint }: { name: string; label: string; value: T; options: [T, string][]; onPick: (v: T) => void; hint?: string }) {
  return (
    <div className="br-field">
      <span className="br-label" id={`rs-${name}-l`}>{label}</span>
      <div className="rs-seg" role="radiogroup" aria-labelledby={`rs-${name}-l`} aria-describedby={hint ? `rs-${name}-hint` : undefined}>
        {options.map(([v, text]) => (
          <label key={v} className={`rs-opt${value === v ? ' is-on' : ''}`}>
            <input type="radio" name={`rs-${name}`} value={v} checked={value === v} onChange={() => onPick(v)} />
            <span>{text}</span>
          </label>
        ))}
      </div>
      {hint && <p className="br-hint" id={`rs-${name}-hint`}>{hint}</p>}
    </div>
  )
}

function StepRow({ s }: { s: SetupStep }) {
  const m = STEP_LOOK[s.status] ?? STEP_LOOK.todo
  return (
    <li className={`br-prob rs-step ${m.look}`} data-step={s.id} data-status={s.status}>
      <span className={`br-prob-glyph tone-${m.tone}`} role="img" aria-label={m.word}>{m.glyph}</span>
      <span className="br-prob-text">
        {s.title}
        {s.status !== 'done' && s.status !== 'todo' && <span className={`br-chip tone-${m.tone} rs-word`}>{m.word}</span>}
      </span>
      {s.message && <span className="br-prob-todo">{s.message}</span>}
      <Acts status={s.status} link={s.link} detail={s.detail} />
      {s.projects.length > 0 && (
        <ul className="rs-projects" aria-label={`${s.title}：每个项目`}>
          {s.projects.map((p) => <ProjectRow key={p.project} p={p} />)}
        </ul>
      )}
    </li>
  )
}

/** The runner's GitLab tokens, each with its account and when it ends; one ending within two weeks warns, with 做什么. */
function Tokens({ tokens }: { tokens: SetupToken[] }) {
  return (
    <ul className="br-probs rs-tokens" aria-label="运行机的令牌">
      {tokens.map((t) => (
        <li key={t.key} className={`br-prob ${t.soon ? 'is-WARN' : 'is-OK'}`} data-token={t.key} data-soon={t.soon}>
          <span className={`br-prob-glyph tone-${t.soon ? 'warn' : 'ok'}`} role="img" aria-label={t.soon ? '快到期了' : '没到期'}>{t.soon ? '!' : '✓'}</span>
          <span className="br-prob-text">{TOKEN_WHAT[t.key]}：{tokenKind(t)}，账号 {t.user}</span>
          <span className="br-prob-todo">{tokenEnd(t)}</span>
          {t.soon && <span className="br-prob-todo"><b>做什么</b>{tokenDo(t)}</span>}
        </li>
      ))}
    </ul>
  )
}

/** One project's part of a step: its mark, its name, its words, its page or MR on GitLab, 细节. */
function ProjectRow({ p }: { p: SetupProjectRow }) {
  const m = STEP_LOOK[p.status] ?? STEP_LOOK.todo
  return (
    <li className="rs-project" data-project={p.project} data-status={p.status}>
      <span className={`rs-project-glyph tone-${m.tone}`} role="img" aria-label={m.word}>{m.glyph}</span>
      <span className="br-mono rs-project-name">{p.project}</span>
      <span className="rs-project-text">{p.message}</span>
      <Acts status={p.status} link={p.link} detail={p.detail} />
    </li>
  )
}

function Acts({ status, link, detail }: { status: SetupStepStatus; link: string | null; detail: string }) {
  if (!link && !detail) return null
  return (
    <span className="br-prob-acts">
      {link && <ExtLink href={link}>{status === 'waiting' ? '去 GitLab 合并' : '在 GitLab 上看'}</ExtLink>}
      {detail && <details className="br-raw"><summary>细节</summary><pre>{detail}</pre></details>}
    </span>
  )
}

const RS_CSS = `
.rs-card { gap: .8em; }
.rs-lead { margin: 0; color: var(--text); max-width: 60em; }
.rs-note { margin: 0; }
.rs-fieldset { border: 0; padding: 0; margin: 0; display: grid; gap: .8em; min-width: 0; }
.rs-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(16em, 1fr)); gap: .8em 1.2em; align-items: start; }
.rs-kept { margin-left: .4em; font-weight: 650; color: var(--text); }
.rs-each { display: flex; flex-wrap: wrap; gap: 4px 18px; min-width: 0; }
.rs-each-name { word-break: break-all; }
.rs-seg { display: flex; flex-wrap: wrap; gap: 6px; }
.rs-opt { display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px 6px 9px; border-radius: 999px; cursor: pointer; font-size: .92em; font-weight: 650;
  border: 1px solid var(--border-strong, var(--border)); background: var(--card); color: var(--text); }
.rs-opt input { margin: 0; accent-color: var(--accent); }
.rs-opt:hover { border-color: var(--accent); }
.rs-opt.is-on { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 17%, var(--card)); color: var(--text-strong); }
.rs-opt:has(input:focus-visible) { outline: 2px solid var(--accent); outline-offset: 2px; }
.rs-fieldset:disabled .rs-opt { cursor: default; opacity: .7; }
.rs-word { margin-left: .6em; font-size: .78em; vertical-align: 1px; }
.rs-step.is-quiet { border-color: transparent; background: transparent; padding-top: 3px; padding-bottom: 3px; }
.rs-step.is-quiet .br-prob-glyph { width: 20px; height: 20px; border-radius: 6px; font-size: 11px; border-width: 1.5px;
  color: var(--muted); border-color: color-mix(in srgb, var(--muted) 50%, transparent); background: transparent; }
.rs-step.is-quiet .br-prob-text { font-weight: 550; color: var(--muted); }
.rs-step.is-running { border-color: var(--kit-run); background: color-mix(in srgb, var(--kt-run) 7%, transparent); }
.rs-step.is-running .br-prob-glyph { animation: kt-blink 1.4s ease-in-out infinite; }
.rs-projects { grid-column: 2; list-style: none; margin: 4px 0 0; padding: 6px 0 0; display: grid; gap: 5px; border-top: 1px dashed var(--border); }
.rs-project { display: grid; grid-template-columns: 18px minmax(9em, max-content) minmax(0, 1fr); gap: 2px 10px; align-items: baseline; font-size: .93em; }
.rs-project-glyph { align-self: center; width: 16px; height: 16px; display: grid; place-items: center; border-radius: 5px; font-size: 10px; font-weight: 900;
  color: var(--kt-tone); border: 1.5px solid var(--kt-tone); background: color-mix(in srgb, var(--kt-tone) 14%, transparent); }
.rs-project-name { color: var(--text-strong); font-weight: 650; word-break: break-all; }
.rs-project-text { color: var(--text); min-width: 0; }
.rs-project .br-prob-acts { grid-column: 3; margin-top: 0; }
`
