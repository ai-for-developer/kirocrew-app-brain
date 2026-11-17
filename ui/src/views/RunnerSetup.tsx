/**
 * 一键建运行机 (设置, with 团队模式): the team runner in same-user mode, built by the app from one GitLab token typed once
 * (backend/core/team/setup.py). POST /team/setup starts its steps in the background; GET /team/setup is read every
 * second while they run. Each step says ✓ when done, 等人 when it waits for a person (the kit.json MR, with its link),
 * ✕ when it stopped the run, then its words, and what GitLab or a command said under 细节; a run cut off part-way (the
 * app stopped) says where. The token goes in that one request: this page keeps it in its field while it is open (for
 * 再跑一次), the backend nowhere.
 */
import { useEffect, useRef, useState } from 'react'
import { useAppApi } from '@kirocrew/app-sdk'
import { paths, useAction, usePoll } from '../api'
import { ago } from '../format'
import { useLatest } from '../hooks'
import type { Tone } from '../styles'
import type { RepoInfo, SetupStep, SetupStepStatus, TeamSetup, TeamSetupBody } from '../types'
import { ExtLink, LoadState, RoleIcon } from '../ui'

export const SETUP_POLL_MS = 1000
export const TOKEN_HINT = '只用这一次：建令牌、标签和 MR，用完就丢，不存到任何地方。要有 api 权限，你在项目里要是 Maintainer 或 Owner。'
export const ROLES_HINT = '填 GitLab 用户名，几个人用逗号隔开；空着就是 token 的主人。安全和数据两个角色也是 token 的主人。'
export const SAME_USER_NOTE = '签名服务、app 和 AI 的会话都用这台机器的同一个系统用户：签名挡不住 app 自己，只适合试用。'
export const TOKEN_FIRST = '先填一次性 token：它只用这一次，不存'
export const cutOffSaid = (title: string) => `上次跑到「${title}」就停了，没跑完（app 关了或者重启了）：再跑一次，接着把没做的建完`

/** A step's state as the list shows it: its glyph, its word, its colour, and the check row's look it borrows. */
export const STEP_LOOK: Record<SetupStepStatus, { glyph: string; word: string; tone: Tone; look: string }> = {
  done: { glyph: '✓', word: '好了', tone: 'ok', look: 'is-OK' },
  waiting: { glyph: '!', word: '等人', tone: 'warn', look: 'is-WARN' },
  failed: { glyph: '✕', word: '没成', tone: 'bad', look: 'is-FAIL' },
  skipped: { glyph: '–', word: '跳过', tone: 'idle', look: 'is-quiet' },
  running: { glyph: '●', word: '正在做', tone: 'run', look: 'is-running' },
  todo: { glyph: '○', word: '还没做', tone: 'idle', look: 'is-quiet' },
}

interface Form { base: string; project: string; product: string; builder: string }

const names = (s: string) => s.split(/[,，、\s]+/).map((x) => x.trim()).filter(Boolean)

/** The last run's input, else the first listed repo on GitLab (its kit.json's). */
function formOf(s: TeamSetup, repos: RepoInfo[]): Form {
  const kit = repos.find((r) => r.kit?.gitlab_project)?.kit
  return {
    base: s.input.gitlab_base || kit?.gitlab_base || '',
    project: s.input.project || kit?.gitlab_project || '',
    product: s.input.roles.product.join(', '),
    builder: s.input.roles.builder.join(', '),
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
  const [ask, setAsk] = useState('')
  const tokenRef = useRef<HTMLInputElement | null>(null)
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
  const go = async () => {
    if (!form) return
    if (!token.trim()) {
      setAsk(TOKEN_FIRST)
      tokenRef.current?.focus()
      return
    }
    setAsk('')
    const r = await start.run({
      gitlab_base: form.base.trim(), project: form.project.trim(), admin_token: token.trim(),
      roles: { product: names(form.product), builder: names(form.builder) },
    })
    if (r) {
      following.current = true
      got.set(r)
    }
  }
  const look = data ? runLook(data) : null
  const ran = !!data && (!!data.cut_off || data.steps.some((s) => s.status !== 'todo'))
  const cutAt = data?.cut_off ? data.steps.find((s) => s.id === data.cut_off)?.title ?? data.cut_off : null
  const busy = running || start.busy
  const said = (
    <>
      {ask && <span className="br-error" role="alert">{ask}</span>}
      {start.error && <span className="br-error" role="alert">{start.error}</span>}
    </>
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
      <p className="rs-lead">
        填一次 GitLab 地址、项目和一个一次性 token，app 自己建好运行机要的一切：两个项目令牌、MR 标签、身份表、签名密钥、三份配置、brain-run、签名服务，最后打开团队模式。你只要在 GitLab 上合并它开的 kit.json MR。
      </p>
      <p className="br-banner br-small rs-note" role="note">{SAME_USER_NOTE}</p>
      <LoadState loading={got.loading} error={got.error} has={!!data} />
      {form && (
        <form className="br-form" noValidate onSubmit={(e) => { e.preventDefault(); void go() }}>
          <fieldset className="rs-fieldset" disabled={busy}>
            <div className="rs-grid">
              <div className="br-field">
                <label htmlFor="rs-base">GitLab 地址</label>
                <input id="rs-base" type="text" className="br-mono" value={form.base} placeholder="https://gitlab.example.com" onChange={(e) => edit({ base: e.target.value })} />
              </div>
              <div className="br-field">
                <label htmlFor="rs-project">项目</label>
                <input id="rs-project" type="text" className="br-mono" value={form.project} placeholder="组/仓库" onChange={(e) => edit({ project: e.target.value })} />
              </div>
            </div>
            <div className="br-field">
              <label htmlFor="rs-token">一次性 token</label>
              <input id="rs-token" ref={tokenRef} type="password" className="br-mono" autoComplete="off" spellCheck={false} value={token}
                aria-describedby="rs-token-hint" onChange={(e) => { setToken(e.target.value); setAsk('') }} />
              <p className="br-hint" id="rs-token-hint">{TOKEN_HINT}</p>
            </div>
            <div className="rs-grid">
              <div className="br-field">
                <label htmlFor="rs-product">产品（回答需求的问题、验收）</label>
                <input id="rs-product" type="text" value={form.product} placeholder="token 的主人" aria-describedby="rs-roles-hint" onChange={(e) => edit({ product: e.target.value })} />
              </div>
              <div className="br-field">
                <label htmlFor="rs-builder">开发（回答设计和计划的问题）</label>
                <input id="rs-builder" type="text" value={form.builder} placeholder="token 的主人" aria-describedby="rs-roles-hint" onChange={(e) => edit({ builder: e.target.value })} />
              </div>
            </div>
            <p className="br-hint" id="rs-roles-hint">{ROLES_HINT}</p>
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
      {(s.link || s.detail) && (
        <span className="br-prob-acts">
          {s.link && <ExtLink href={s.link}>{s.status === 'waiting' ? '去 GitLab 合并' : '在 GitLab 上看'}</ExtLink>}
          {s.detail && <details className="br-raw"><summary>细节</summary><pre>{s.detail}</pre></details>}
        </span>
      )}
    </li>
  )
}

const RS_CSS = `
.rs-card { gap: .8em; }
.rs-lead { margin: 0; color: var(--text); max-width: 60em; }
.rs-note { margin: 0; }
.rs-fieldset { border: 0; padding: 0; margin: 0; display: grid; gap: .8em; min-width: 0; }
.rs-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(16em, 1fr)); gap: .8em 1.2em; align-items: start; }
.rs-word { margin-left: .6em; font-size: .78em; vertical-align: 1px; }
.rs-step.is-quiet { border-color: transparent; background: transparent; padding-top: 3px; padding-bottom: 3px; }
.rs-step.is-quiet .br-prob-glyph { width: 20px; height: 20px; border-radius: 6px; font-size: 11px; border-width: 1.5px;
  color: var(--muted); border-color: color-mix(in srgb, var(--muted) 50%, transparent); background: transparent; }
.rs-step.is-quiet .br-prob-text { font-weight: 550; color: var(--muted); }
.rs-step.is-running { border-color: var(--kit-run); background: color-mix(in srgb, var(--kt-run) 7%, transparent); }
.rs-step.is-running .br-prob-glyph { animation: kt-blink 1.4s ease-in-out infinite; }
`
