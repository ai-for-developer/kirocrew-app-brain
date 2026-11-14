/**
 * One lane per stage a run did, in the order of the way (the sample team board's Lanes.tsx, a lane per node): the
 * stage, its state and who did it (本机, 运行机 第 1 次), what it hands in and in how many rounds, how long it took; what
 * it waits for (the line a person types) or what it handed in (✔ 交付: its own note, else how its round ended); its
 * rounds (第 1 轮 ✕ 产品退回 · D-2 → 第 2 轮 ↻ 请批以后代码又改了 …); what it changed (the sample's 改动 block: its
 * commits and the files they touched) or, for 构建测试, the checks of its last round; and its time track.
 *
 * Every lane ends in a time track on one shared axis, the requirement's start → its last activity (or its merge): its
 * work spans, the spans it waited for a person (magenta when it is you, blue for someone else), the ones it did again
 * (orange), with commits, approvals, verify rounds and questions marked where they happened. A long quiet time after the
 * last activity (a night, hours of waiting) is drawn short at the end, with how long it was, so the activity keeps the
 * width. Read down the lanes and the way lines up like a Gantt chart.
 */
import { useNavigate } from '@kirocrew/app-sdk'
import type { ActorSpan, BeltItem, ReqBoard, Round, Run, StageNode } from '../types'
import { TypeLine } from '../ui'
import { ACT } from '../vocab'
import {
  ROUND_TONE, RUN_ICON, WORKING, actorMeta, boardWho, clip, clockAt, fmtAgo, fmtHm, fmtSpan, fmtWhen, nodeLabel, nodeWait, openGate, roundWords, runShort,
  sessionOf, spanWho,
} from './format'
import { useServerNow } from './hooks'
import { RoleIcon } from './RoleIcon'

export interface LanesProps {
  board: ReqBoard
  /** the node looked at, or null */
  focus: string | null
  bumps: Record<string, number>
  onNode: (id: string) => void
  /** in a replay: nothing to type, nothing to open */
  quiet?: boolean
}

const isDone = (s: string) => s === 'done' || s === 'merged'

/** The stages that have a lane: on this way, with a round. */
export const laneNodes = (b: Pick<ReqBoard, 'nodes'>): StageNode[] => b.nodes.filter((n) => n.status !== 'skipped' && n.rounds.length > 0)

/** Which lane holds an open gate's line to type: the last one that gate covers, as its chip stands under the last box
 * (one INCEPTION ask: under 应用设计, not also 需求分析). */
function askers(nodes: StageNode[]): Set<string> {
  const by = new Map<string, string>()
  for (const n of nodes) {
    const g = openGate(n)
    if (g?.line) by.set(g.kind, n.id)
  }
  return new Set(by.values())
}

export function Lanes({ board, focus, bumps, onNode, quiet = false }: LanesProps) {
  const ax = axisOf(board, useServerNow())
  const runs = new Map<string, Run>(board.runs.map((r) => [r.key, r]))
  const lanes = laneNodes(board)
  const asks = askers(lanes)
  return (
    <section className="kt-lanes" aria-label="每一步的泳道" data-testid="kt-lanes">
      <LanesAxis board={board} ax={ax} />
      {lanes.map((n) => (
        <LaneRow key={n.id} n={n} run={runs.get(n.run ?? '')} runs={runs} board={board} ax={ax} focused={focus === n.id} dim={!!focus && focus !== n.id}
          bump={bumps[n.id] ?? 0} onNode={onNode} quiet={quiet} asks={asks.has(n.id)} />
      ))}
      {!lanes.length && <p className="kt-dim kt-lanes-out" data-testid="kt-lanes-empty">还没有哪一步开始：开工以后，每做一步，这里多一条泳道</p>}
    </section>
  )
}

/** The axis length in seconds and its ticks (from the start), at most 6 round steps. */
export function axisTicks(dur: number): number[] {
  const steps = [60, 300, 900, 1800, 3600, 3 * 3600, 6 * 3600, 12 * 3600, 86400, 2 * 86400, 7 * 86400]
  const step = steps.find((x) => dur / x <= 6) ?? Math.ceil(dur / 6 / 86400) * 86400
  const out: number[] = []
  for (let t = 0; t <= dur + 1e-6; t += step) out.push(t)
  return out
}

/** 0, 15 分, 3 小时, 1 小时 30 分, 1 天, 1 天 6 时 */
export function tickLabel(s: number): string {
  if (s <= 0) return '0'
  if (s < 3600) return `${Math.round(s / 60)} 分`
  if (s < 86400) {
    const m = Math.round(s / 60)
    return m % 60 ? `${Math.floor(m / 60)} 小时 ${m % 60} 分` : `${m / 60} 小时`
  }
  const d = Math.floor(s / 86400)
  const h = Math.round((s % 86400) / 3600)
  return h ? `${d} 天 ${h} 时` : `${d} 天`
}

/** The share of the track a long quiet tail is drawn in. */
export const TAIL_PCT = 16

export interface Axis {
  t0: number
  /** what happened at t0, in the words 需求详情 uses for it: 开工 (the run's start), else 认領 (the claim); '' when
   * neither is recorded */
  from: '开工' | '认领' | ''
  /** the last activity (the end of the even part) */
  last: number
  /** the axis' end: the merge, else now */
  end: number
  /** the quiet time after the last activity, drawn short; null: none worth folding */
  tail: number | null
  pct: (t: number) => number
}

/** The last thing that happened on the board: an event, a commit, a mark, a span's start or end. */
export function lastActivity(b: ReqBoard): number | null {
  const ts: number[] = [...b.events.map((e) => e.at), ...b.belt.map((x) => x.at)]
  for (const n of b.nodes) {
    for (const m of n.marks) ts.push(m.at)
    for (const s of n.spans) ts.push(s.b ?? s.a)
  }
  return ts.length ? Math.max(...ts) : null
}

/** The axis starts where 需求详情's cycle does, at 开工 (the run's start event); before it nothing works or waits on a
 * lane (the claim is the event before it). A board with no 开工 recorded starts at its first event, said as what that
 * was. */
export function axisStart(b: Pick<ReqBoard, 'events' | 'started_at' | 'at'>): { t0: number; from: Axis['from'] } {
  const start = b.events.find((e) => e.kind === 'start')
  if (start) return { t0: start.at, from: '开工' }
  const t0 = b.started_at ?? b.events[0]?.at ?? b.at
  return { t0, from: b.events.some((e) => e.kind === 'claim' && e.at === t0) ? '认领' : '' }
}

export function axisOf(b: ReqBoard, now: number): Axis {
  const { t0, from } = axisStart(b)
  const end = Math.max(t0 + 60, b.ended_at ?? now)
  const last = Math.min(end, Math.max(t0 + 60, lastActivity(b) ?? end))
  const quiet = end - last
  const tail = b.ended_at === null && quiet > Math.max(900, 0.3 * (last - t0)) ? quiet : null
  const even = tail === null ? 100 : 100 - TAIL_PCT
  const top = tail === null ? end : last
  const pct = (t: number) => {
    if (t <= top) return Math.max(0, ((t - t0) / (top - t0)) * even)
    return Math.min(100, even + ((t - last) / (end - last)) * TAIL_PCT)
  }
  return { t0, from, last, end, tail, pct }
}

function LanesAxis({ board, ax }: { board: ReqBoard; ax: Axis }) {
  const dur = (ax.tail === null ? ax.end : ax.last) - ax.t0
  const ticks = axisTicks(dur)
  const waiting = board.nodes.some((n) => n.status === 'awaiting_approval')
  return (
    <div className="kt-lane kt-lane-axis" aria-hidden="true">
      <div className="kt-axis-title" data-testid="kt-axis-title">时间线（从 {fmtWhen(ax.t0)} {ax.from}起）</div>
      <div className="kt-axis">
        {ticks.map((t, i) => {
          const left = ax.pct(ax.t0 + t)
          const edge = i === 0 ? ' is-first' : left > (ax.tail === null ? 92 : 100 - TAIL_PCT - 6) ? ' is-last' : ''
          return <span key={t} className={`kt-axis-tick${edge}`} style={{ left: `${left}%` }}>{tickLabel(t)}</span>
        })}
        {ax.tail !== null && (
          <span className="kt-axis-tail" style={{ left: `${100 - TAIL_PCT}%`, width: `${TAIL_PCT}%` }} data-testid="kt-axis-tail"
            title={`最后一次动静以后又过了 ${fmtSpan(ax.tail)}${waiting ? '，一直在等' : board.live ? '' : '，没有动静'}：这一段画短了`}>
            之后 {fmtSpan(ax.tail)}
          </span>
        )}
      </div>
    </div>
  )
}

const MARK_TITLE: Record<string, string> = {
  commit: '提交', approved: '批准', rejected: '退回', request: '请求批准', refused: '批准的那一行没被接受', blocked: 'Lean 拦下',
  handover: '交接', accepted: '桥接手', signed: '签名', question: '问人', answer: '回答', 'verify-ok': '自动检查通过',
  'verify-fail': '自动检查没过', reflect: '回写', session: '会话',
}

function LaneTrack({ n, title, board, ax }: { n: StageNode; title: string; board: ReqBoard; ax: Axis }) {
  const now = useServerNow()
  const word = (s: ActorSpan) => (s.kind === 'wait' ? boardWho(board, spanWho(n, board, s)) : s.kind === 'fix' ? '返工' : '在做')
  // an open span glows only while it really goes on: work while the stage is at work, a wait while it waits
  const going = (kind: string) => (kind === 'wait' ? n.status === 'awaiting_approval' : WORKING.has(n.status))
  return (
    <div className="kt-track" role="img" aria-label={`${title}的时间线：${n.spans.length ? n.spans.map((s) => `${word(s)} ${fmtSpan((s.b ?? now) - s.a)}`).join('，') : '没有记下时间段'}`}>
      <div className="kt-track-line" />
      {ax.tail !== null && <div className="kt-track-tail" style={{ left: `${100 - TAIL_PCT}%`, width: `${TAIL_PCT}%` }} />}
      {n.spans.map((s, i) => (
        <span key={i} className={`kt-track-span is-${s.kind}${s.b === null && going(s.kind) ? ' is-live' : ''}${s.kind === 'wait' && spanWho(n, board, s) !== 'you' ? ' is-other' : ''}`}
          style={{ left: `${ax.pct(s.a)}%`, width: `${Math.max(0.6, ax.pct(s.b ?? now) - ax.pct(s.a))}%` }}
          title={`${word(s)}：${fmtWhen(s.a)} 起，${s.b === null ? `到现在（${fmtSpan(now - s.a)}）` : `用了 ${fmtSpan(s.b - s.a)}`}${ax.from ? `（${ax.from}后 ${fmtSpan(s.a - ax.t0)}）` : ''}`} />
      ))}
      {n.marks.map((m, i) => (
        <span key={`m${i}`} className={`kt-track-mark is-${m.kind}`} style={{ left: `${ax.pct(m.at)}%` }} title={`${fmtWhen(m.at)} · ${MARK_TITLE[m.kind] ?? m.kind}：${m.title}`} />
      ))}
    </div>
  )
}

interface LaneProps {
  n: StageNode
  /** who has it now (its latest round's run) */
  run: Run | undefined
  runs: Map<string, Run>
  board: ReqBoard
  quiet: boolean
  /** this lane is the one that holds its open gate's line (askers) */
  asks?: boolean
}

function LaneRow({ n, run, runs, board, ax, focused, dim, bump, onNode, quiet, asks }: LaneProps & { ax: Axis; focused: boolean; dim: boolean; bump: number; onNode: (id: string) => void }) {
  const meta = actorMeta(n.status)
  const wait = nodeWait(n, run)
  const title = n.unit ? `${n.title} · ${n.unit}` : n.title
  return (
    <article className={`kt-lane is-${n.status} tone-${wait && wait !== 'you' ? 'info' : meta.tone}${focused ? ' is-focus' : ''}${dim ? ' is-dim' : ''}`}
      data-testid="kt-lane" data-node={n.id} data-status={n.status} aria-label={`${title}：${nodeLabel(n, run, board)}`}>
      {bump > 0 && <span key={bump} className="kt-lane-bump" aria-hidden="true" />}
      <LaneId n={n} run={run} runs={runs} board={board} title={title} focused={focused} onNode={onNode} quiet={quiet} />
      <div className="kt-lane-live">
        <Situation n={n} run={run} runs={runs} board={board} quiet={quiet} asks={asks} />
        {n.rounds.length > 1 && <RoundsStrip n={n} runs={runs} board={board} />}
      </div>
      <div className="kt-lane-work"><Work n={n} board={board} /></div>
      <LaneTrack n={n} title={title} board={board} ax={ax} />
    </article>
  )
}

/** How long its rounds took, summed (a round still open: until now). */
function usedOf(n: StageNode, now: number): number {
  return n.rounds.reduce((t, r) => t + Math.max(0, (r.ended ?? now) - r.started), 0)
}

function LaneId({ n, run, board, title, focused, onNode, quiet }: LaneProps & { title: string; focused: boolean; onNode: (id: string) => void }) {
  const meta = actorMeta(n.status)
  const navigate = useNavigate()
  const now = useServerNow()
  const wait = nodeWait(n, run)
  const label = nodeLabel(n, run, board)
  const used = usedOf(n, now)
  const commits = board.belt.filter((x) => x.node === n.id && x.kind === 'commit').length
  const session = run?.readable === 'here' ? sessionOf(run) : undefined
  return (
    <div className="kt-lane-id">
      <button type="button" className="kt-avatar" onClick={() => onNode(n.id)} aria-pressed={focused} aria-label={focused ? `不再只看${title}` : `只看${title}`} title={focused ? '再点一次看全部' : '只看这一步'}>
        <RoleIcon role={n.icon} size={22} />
      </button>
      <div className="kt-lane-who">
        {/* 发布's number is its phase's ✓, which beside the name reads as 做完了; its box leaves it out too */}
        <div className="kt-lane-name">
          {n.title}{n.unit && <> · <span className="kt-lane-unit">{n.unit}</span></>}{n.n !== '✓' && <span className="br-lane-n">{n.n}</span>}
        </div>
        <div className="kt-lane-tag">
          <span className={`kt-pill tone-${wait && wait !== 'you' ? 'info' : meta.tone}${n.status === 'running' || wait === 'you' ? ' is-live' : ''}`} title={meta.hint}>
            <i aria-hidden="true">{meta.icon}</i>
            {label}
          </span>
          {run && <span className="kt-pill tone-handoff br-run-pill" title={run.readable === 'elsewhere' ? `${run.label}：它的会话在别的机器上` : run.label}><RoleIcon role={RUN_ICON[run.kind]} size={12} /> {run.label}</span>}
        </div>
        <div className="kt-lane-doing">{n.sub}{n.rounds.length > 1 ? ` · 第 ${n.rounds.length} 轮` : ''}</div>
      </div>
      <p className="kt-lane-stats">
        {wait && n.since !== null
          ? <span title={`从 ${fmtWhen(n.since)} 起`}>⏱ 等了 <b><Wait since={n.since} /></b></span>
          : used >= 30 && <span title={n.rounds.length > 1 ? '每一轮的用时加起来' : undefined}>⏱ <b>{fmtHm(used)}</b> 用时{n.rounds.length > 1 ? `（${n.rounds.length} 轮）` : ''}</span>}
        {commits > 0 && <span>提交 <b>{commits}</b> 次</span>}
        {n.since !== null && !wait && <span title={fmtWhen(n.since)}><Ago at={n.since} /></span>}
      </p>
      {!quiet && session && (
        <button type="button" className="kt-out-btn" onClick={() => navigate(session.url)} title="在 Kiro Crew 里打开做这一步的会话">{ACT.openSession} ›</button>
      )}
    </div>
  )
}

function Ago({ at }: { at: number }) {
  const now = useServerNow()
  return <>{fmtAgo(now - at)}</>
}

function Wait({ since }: { since: number }) {
  const now = useServerNow()
  return <>{fmtSpan(now - since)}</>
}

/** What the stage does or waits for, or what it handed in: one loud line, and what the person can do about it here. */
function Situation({ n, run, board, quiet, asks }: LaneProps) {
  const meta = actorMeta(n.status)
  const wait = nodeWait(n, run)
  const mine = wait === 'you'
  const g = openGate(n)
  const last = n.rounds[n.rounds.length - 1] as Round | undefined
  // a wait says whom it is on once: the line itself when it starts so (「等产品验收（D-2）」), else the lead
  const says = !!wait && n.doing.startsWith('等')
  const strong: Record<string, string> = {
    awaiting_approval: says ? (mine ? '✋' : '') : `${mine ? '✋ ' : ''}${nodeLabel(n, run, board)}：`, failed: '✕ 没过：', stalled: '⚠ 卡住：',
    fixing: '↻ 返工：', queued: '⏸ 排队：', merged: '✔ 交付', done: '✓ 交付', running: '● 在做', waiting: '还没轮到：',
  }
  const lead = strong[n.status] ?? meta.label
  // what it handed in: its own note of the last round, else the fold's line for the step (已合进 Brain), else how that
  // round ended (the rounds strip says who decided each)
  const handed = n.status === 'done' || n.status === 'merged' || n.status === 'failed'
  const text = handed && last ? last.note ?? (n.doing || roundWords(last, n, board)) : n.doing
  const bare = lead.replace(/[：:]$/, '')
  const same = !text || text === meta.label || text === bare
  return (
    <>
      <div className={`kt-situation tone-${wait && !mine ? 'info' : meta.tone}`} data-testid="kt-situation">
        <strong>{same ? bare : lead}</strong>
        {!same && <span className="kt-summary">{text}</span>}
      </div>
      {!quiet && mine && asks && g?.line && <TypeLine line={g.line} sessionUrl={sessionOf(run)?.url ?? null} />}
    </>
  )
}

const ROUND_GLYPH: Record<Round['outcome']['kind'], string> = {
  open: '⏳', done: '✓', approved: '✓', passed: '✓', sent_back: '✕', refused: '⚠', failed: '✕', superseded: '↻', skipped: '–',
}

/** Its rounds, oldest first: 第 1 轮 ✕ 产品退回（D-2）· 14:41 (who did each when more than one run did). */
function RoundsStrip({ n, runs, board }: Omit<LaneProps, 'run' | 'quiet'>) {
  const many = new Set(n.rounds.map((r) => r.run)).size > 1
  return (
    <ol className="br-rounds" aria-label={`${n.title}的 ${n.rounds.length} 轮`} data-testid="kt-rounds">
      {n.rounds.map((r, i) => {
        const words = roundWords(r, n, board)
        const who = runShort(runs.get(r.run), board)
        const at = clockAt(r.started, board.at)
        return (
          <li key={r.n} className={`br-round tone-${ROUND_TONE[r.outcome.kind]}${i === n.rounds.length - 1 ? ' is-now' : ''}`} title={`第 ${r.n} 轮 · ${who} · ${at} 起 · ${words}`}>
            <b>第 {r.n} 轮</b> {ROUND_GLYPH[r.outcome.kind]} {clip(words, 14)}{many ? ` · ${who}` : ''} · {at}
          </li>
        )
      })}
    </ol>
  )
}

/** What it changed: 构建测试 the checks of its last round; the others the files its commits touched, else the files it
 * declared, else that nothing is committed yet. */
function Work({ n, board }: { n: StageNode; board: ReqBoard }) {
  const commits = board.belt.filter((x) => x.node === n.id && x.kind !== 'merge')
  const checked = [...n.rounds].reverse().find((r) => r.outcome.checks.length)?.outcome.checks ?? []
  const outputs = [...n.rounds].reverse().find((r) => r.outputs.length)?.outputs ?? []
  // a step that is through and committed nothing did not have anything to commit (回写 Brain goes to the Brain)
  if (!commits.length && !checked.length && !outputs.length) return <p className="kt-dim kt-nothing">{isDone(n.status) ? '没有提交' : '还没提交'}</p>
  return (
    <>
      {checked.length > 0 && (
        <div className="kt-owned" aria-label="这一轮的检查" data-testid="kt-lane-checks">
          <span className="kt-dim">这一轮的检查</span>
          {checked.map((c) => <span key={c.name} className={`kt-owned-case is-${c.ok ? 'pass' : 'fail'}`}>{c.name} {c.ok ? '✓' : '✕'}{c.secs !== null ? ` ${Math.round(c.secs)}s` : ''}</span>)}
        </div>
      )}
      {commits.length > 0 ? <Changes commits={commits} /> : outputs.length > 0 && (
        <div className="br-changes" data-testid="kt-outputs">
          <div className="kt-files-head"><span>交的文件</span><span className="kt-dim">{outputs.length} 个 · 还没提交</span></div>
          <ul className="kt-commits">
            {outputs.slice(0, 4).map((o) => (
              <li key={o.path}>
                <code>{o.sha8}</code>
                <span className="kt-file-path kt-mono is-split" title={o.path}>
                  <span className="kt-file-dir">{o.path.slice(0, o.path.lastIndexOf('/') + 1)}</span>
                  <span className="kt-file-base">{o.path.slice(o.path.lastIndexOf('/') + 1)}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  )
}

type FileChange = NonNullable<BeltItem['changes']>[number]

/** The files a node's commits changed, one row each, biggest first (lines added / removed summed over its commits). */
export function filesOf(commits: BeltItem[]): FileChange[] {
  const by = new Map<string, FileChange>()
  for (const c of commits) {
    for (const f of c.changes ?? []) {
      const had = by.get(f.path)
      by.set(f.path, had ? { ...had, added: had.added + f.added, removed: had.removed + f.removed, status: had.status === 'A' ? 'A' : f.status } : { ...f })
    }
  }
  return [...by.values()].sort((x, y) => y.added + y.removed - (x.added + x.removed))
}

/** 改动 (the sample's change block): the files and their +/− bars when the backend reads them, and the commits. */
function Changes({ commits }: { commits: BeltItem[] }) {
  const files = filesOf(commits)
  const added = files.reduce((n, f) => n + f.added, 0)
  const removed = files.reduce((n, f) => n + f.removed, 0)
  const scale = Math.max(1, ...files.map((f) => f.added + f.removed))
  const most = Math.max(1, ...commits.map((c) => c.files))
  const show = files.slice(0, 5)
  return (
    <div className="br-changes" data-testid="kt-changes">
      <div className="kt-files-head">
        <span>改动</span>
        {files.length > 0 ? (
          <>
            <span className="kt-add">+{added}</span>
            <span className="kt-del">−{removed}</span>
            <span className="kt-dim">{files.length} 个文件 · 提交 {commits.length} 次</span>
          </>
        ) : (
          <span className="kt-dim">提交 {commits.length} 次</span>
        )}
      </div>
      {show.length > 0 && (
        <ul className="kt-files">
          {show.map((f) => (
            <li key={f.path} title={`${f.path}  +${f.added} −${f.removed}`}>
              <span className={`kt-file-st is-${f.status}`}>{f.status}</span>
              {/* a narrow lane cuts the directory, never the file's own name */}
              <span className="kt-file-path kt-mono is-split">
                <span className="kt-file-dir">{f.path.slice(0, f.path.lastIndexOf('/') + 1)}</span>
                <span className="kt-file-base">{f.path.slice(f.path.lastIndexOf('/') + 1)}</span>
              </span>
              <span className="kt-file-bar" aria-hidden="true">
                <i className="kt-bar-add" style={{ width: `${(f.added / scale) * 100}%` }} />
                <i className="kt-bar-del" style={{ width: `${(f.removed / scale) * 100}%` }} />
              </span>
              <span className="kt-file-num kt-mono">
                <span className="kt-add">+{f.added}</span>
                {f.removed > 0 && <span className="kt-del"> −{f.removed}</span>}
              </span>
            </li>
          ))}
          {files.length > show.length && <li className="kt-more kt-dim">还有 {files.length - show.length} 个文件</li>}
        </ul>
      )}
      <ul className="kt-commits br-commit-rows" aria-label="提交">
        {commits.slice().reverse().slice(0, 3).map((c) => (
          <li key={`${c.kind}-${c.sha}`} className={c.records_only ? 'is-records' : ''}>
            <code>{c.sha.slice(0, 8)}</code>
            <span className="br-commit-subj" title={c.text}>{c.kind === 'handover' ? `交接：${c.text}` : c.text}</span>
            {!files.length && (
              <span className="br-commit-files" title={`${c.files} 个文件`}>
                <span className="kt-file-bar" aria-hidden="true"><i className="kt-bar-add" style={{ width: `${(c.files / most) * 100}%` }} /></span>
                <span className="kt-mono">{c.files} 个文件{c.records_only ? ' · 只改记录' : ''}</span>
              </span>
            )}
          </li>
        ))}
        {commits.length > 3 && <li className="kt-more kt-dim">还有 {commits.length - 3} 次提交：看提交传送带</li>}
      </ul>
    </div>
  )
}
