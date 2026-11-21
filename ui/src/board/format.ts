/**
 * Words, tones and number formats every part of the 运行过程 shares (the sample team board's format.ts, in a
 * requirement's words). Pure: no React, no DOM.
 *
 * A tone is a meaning, not a colour: styles.ts maps each to a colour per host mode (kiro-dark / kiro-light), so the
 * same word always has the same colour and every colour has a word next to it.
 */
import type { ActorStatus, BeltItem, BoardEvent, BoardEventKind, CheckWall, Gate, GateKind, LeanSummary, PipelineRef, ReqBoard, Round, Run, StageNode, WaitDo, WaitWho } from '../types'
import { approvalName, waitsOn } from '../vocab'

export const POLL_MS = 2000

export type Tone = 'run' | 'fix' | 'ok' | 'done' | 'queue' | 'warn' | 'approve' | 'bad' | 'idle' | 'handoff' | 'info'

export interface StatusMeta {
  label: string
  tone: Tone
  icon: string
  /** One line for a person who does not know the word. */
  hint: string
}

/** An actor's state in words (the sample's lane states, said for a requirement's way). */
export const ACTOR_META: Record<ActorStatus, StatusMeta> = {
  waiting: { label: '还没轮到', tone: 'idle', icon: '⏳', hint: '前面的步骤还没做完' },
  queued: { label: '排队', tone: 'queue', icon: '⏸', hint: '交给它了，它还没接' },
  running: { label: '在做', tone: 'run', icon: '●', hint: '它正在做' },
  stalled: { label: '卡住', tone: 'warn', icon: '⚠', hint: '停住了：要人看一下' },
  awaiting_approval: { label: '等人', tone: 'approve', icon: '✋', hint: '等一个人：批准、回答或验收' },
  done: { label: '做完这步', tone: 'done', icon: '✓', hint: '它这一步做完了，后面还有' },
  merged: { label: '完成', tone: 'ok', icon: '✔', hint: '这一步完成了' },
  fixing: { label: '返工', tone: 'fix', icon: '↻', hint: '被退回或没过，在改' },
  failed: { label: '没过', tone: 'bad', icon: '✕', hint: '被拒或失败了' },
  skipped: { label: '这次不需要', tone: 'idle', icon: '–', hint: '这个需求的路上没有这一步' },
}

export function actorMeta(status: string): StatusMeta {
  return ACTOR_META[status as ActorStatus] ?? { label: String(status), tone: 'idle', icon: '•', hint: '' }
}

/** At work right now (these glow; stalled and awaiting get their own loud look). */
export const WORKING: ReadonlySet<ActorStatus> = new Set<ActorStatus>(['running', 'fixing'])
export const LIVE: ReadonlySet<ActorStatus> = new Set<ActorStatus>(['running', 'fixing', 'stalled', 'awaiting_approval'])

export const EVENT_META: Record<BoardEventKind, { icon: string; label: string; tone: Tone }> = {
  claim: { icon: '🏷️', label: '认领', tone: 'info' },
  start: { icon: '🏁', label: '开工', tone: 'run' },
  stage: { icon: '🧭', label: 'Lean 阶段', tone: 'idle' },
  request: { icon: '✋', label: '请求批准', tone: 'approve' },
  approve: { icon: '👍', label: '批准', tone: 'ok' },
  reject: { icon: '↩️', label: '退回', tone: 'bad' },
  refused: { icon: '🚫', label: '批准的那一行没被接受', tone: 'warn' },
  blocked: { icon: '🛑', label: 'Lean 拦下', tone: 'warn' },
  commit: { icon: '📝', label: '提交', tone: 'info' },
  handover: { icon: '📨', label: '交接', tone: 'handoff' },
  accepted: { icon: '🤝', label: '桥接手', tone: 'ok' },
  rejected: { icon: '⛔', label: '接手被拒', tone: 'bad' },
  signed: { icon: '🔏', label: '签名', tone: 'handoff' },
  question: { icon: '❓', label: '问人', tone: 'approve' },
  answer: { icon: '💬', label: '回答', tone: 'ok' },
  verify: { icon: '🧪', label: '自动检查', tone: 'info' },
  merge: { icon: '⤵️', label: '合并', tone: 'ok' },
  reflect: { icon: '📚', label: '回写 Brain', tone: 'ok' },
  session: { icon: '💻', label: '会话', tone: 'run' },
  app: { icon: '•', label: 'app', tone: 'idle' },
}

/** The words of the places an event travels between that are no node ('person' is said by personPlace). */
export const PLACE: Record<string, string> = { branch: '运行分支', checks: '检查', relay: '交接' }

/** what a person does that, after the hand-over, happens on the MR: the runner's questions, their answers, approvals */
const ON_MR: ReadonlySet<string> = new Set(['approve', 'reject', 'request', 'question', 'answer'])

/** Who an event's 'person' end is: on the MR (one the bridge delivered, a question the runner asked, its answer, an
 * approval after the hand-over) whoever answers there, 产品 for the release and its acceptance, else the MR; on this
 * machine's way the owner, 「你」 only for the person looking (UI问题清单 BOARD-11). */
export function personPlace(e: Pick<BoardEvent, 'kind' | 'from' | 'to' | 'at' | 'text' | 'node'>, b: Pick<ReqBoard, 'person' | 'events'>): string {
  const hand = b.events.find((x) => x.kind === 'handover')?.at
  const mr = e.from === 'relay' || e.to === 'relay' || (ON_MR.has(e.kind) && hand !== undefined && e.at > hand)
  if (mr) return e.node === 'release' || /验收/.test(e.text) ? '产品' : 'MR'
  return b.person.me ? '你' : b.person.owner ?? '负责人'
}

/** 0:05, 25:00, 1:02:03 */
export function fmtClock(sec: number): string {
  const s = Math.max(0, Math.floor(sec))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const r = s % 60
  return `${h ? `${h}:${String(m).padStart(2, '0')}` : m}:${String(r).padStart(2, '0')}`
}

/** 45 秒, 23 分钟, 1 小时 5 分, 2 天 3 小时 */
export function fmtSpan(sec: number): string {
  const s = Math.max(0, Math.round(sec))
  if (s < 60) return `${s} 秒`
  const m = Math.round(s / 60)
  if (m < 60) return `${m} 分钟`
  const h = Math.floor(m / 60)
  if (h < 24) return m % 60 ? `${h} 小时 ${m % 60} 分` : `${h} 小时`
  const d = Math.floor(h / 24)
  return h % 24 ? `${d} 天 ${h % 24} 小时` : `${d} 天`
}

export function fmtAgo(delta: number): string {
  const s = Math.max(0, Math.round(delta))
  if (s < 5) return '刚刚'
  if (s < 60) return `${s} 秒前`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} 分钟前`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} 小时前`
  return `${Math.floor(h / 24)} 天前`
}

export function fmtCredits(c: number | null | undefined): string {
  if (c === null || c === undefined || !Number.isFinite(c)) return '—'
  return c >= 100 ? String(Math.round(c)) : (Math.round(c * 10) / 10).toFixed(1)
}

const pad = (n: number) => String(n).padStart(2, '0')

/** 11-08 14:05 (the local day and time of an epoch-seconds time) */
export function fmtWhen(t: number | null | undefined): string {
  if (t === null || t === undefined || !Number.isFinite(t)) return '—'
  const d = new Date(t * 1000)
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** Approximate rendered width in em: CJK ≈ 1, emoji ≈ 1.2, latin ≈ 0.58, space ≈ 0.3. */
export function textWidth(s: string): number {
  let w = 0
  for (const ch of s) w += charWidth(ch)
  return w
}

/** Chinese characters, fullwidth forms and CJK punctuation: one em wide. */
const isWide = (ch: string) => {
  const c = ch.codePointAt(0) ?? 0
  return (c >= 0x2e80 && c <= 0x9fff) || (c >= 0xff00 && c <= 0xffef) || (c >= 0x3000 && c <= 0x303f)
}

function charWidth(ch: string): number {
  const c = ch.codePointAt(0) ?? 0
  if (c === 0x20) return 0.3
  if (c > 0x1f000) return 1.2
  return isWide(ch) ? 1 : 0.58
}

/** Cut *s* to about *maxEm* em with an ellipsis (SVG text does not ellipsize by itself). */
export function clip(s: string, maxEm: number): string {
  if (textWidth(s) <= maxEm) return s
  let out = ''
  let w = 0
  for (const ch of s) {
    const cw = charWidth(ch)
    if (w + cw > maxEm - 0.8) break
    out += ch
    w += cw
  }
  return `${out.trimEnd()}…`
}

/** Chinese line breaking: a line does not start with these, nor end with an opening bracket. */
const NO_START = new Set([...'、，。：；！？）」』…'])
const NO_END = new Set([...'（「『'])

/** How much better a line ending is than a break between two Chinese characters: after a hyphen or at a space, after
 * 、，；：, before a ` · `. */
const BREAK_BONUS = [0, 0.15, 0.35, 0.6]

/** *s* in lines of at most *maxEm* em, at most *maxLines* of them (the last clipped when they are not enough), for SVG
 * text, which does not wrap. A line ends before a ` · `, after 、，；：, at a space or after a hyphen, else between two
 * Chinese characters, whichever leaves it fullest for its kind; then the lines are evened out to about one length. */
export function wrap(s: string, maxEm: number, maxLines: number): string[] {
  const first = fill(s, maxEm, maxLines)
  if (first.lines.length < 2 || first.clipped) return first.lines
  let [lo, hi] = [maxEm / first.lines.length, maxEm]
  while (hi - lo > 0.2) {
    const mid = (lo + hi) / 2
    const t = fill(s, mid, first.lines.length)
    if (t.clipped) lo = mid
    else hi = mid
  }
  return fill(s, hi, first.lines.length).lines
}

/** The lines of *s* at *maxEm*, each ended at its best break; *clipped*: *maxLines* were not enough. */
function fill(s: string, maxEm: number, maxLines: number): { lines: string[]; clipped: boolean } {
  const ch = [...s]
  const w = (a: number, b: number) => textWidth(ch.slice(a, b).join(''))
  const lines: string[] = []
  let at = 0
  while (at < ch.length) {
    const rest = ch.slice(at).join('').trim()
    if (lines.length === maxLines - 1 || w(at, ch.length) <= maxEm) return { lines: [...lines, clip(rest, maxEm)], clipped: w(at, ch.length) > maxEm }
    // [where this line ends, where the next starts, what kind of break]
    const cuts: [number, number, number][] = []
    for (let i = at + 1; i < ch.length; i++) {
      const [a, b] = [ch[i - 1], ch[i]]
      if (b === ' ' && ch[i + 1] === '·' && ch[i + 2] === ' ') cuts.push([i, i + 3, 3])
      else if ('、，；：'.includes(a)) cuts.push([i, i, 2])
      else if (b === ' ') cuts.push([i, i + 1, 1])
      else if (a === '-' || a === '/') cuts.push([i, i, 1])
      else if ((isWide(a) || isWide(b)) && a !== ' ' && !NO_START.has(b) && !NO_END.has(a)) cuts.push([i, i, 0])
    }
    const fit = cuts.filter(([end]) => w(at, end) <= maxEm)
    if (!fit.length) return { lines: [...lines, clip(rest, maxEm)], clipped: true }
    const score = ([end, , kind]: [number, number, number]) => w(at, end) / maxEm + BREAK_BONUS[kind]
    const [end, next] = fit.reduce((best, c) => (score(c) > score(best) ? c : best))
    lines.push(ch.slice(at, end).join('').trim())
    at = next
  }
  return { lines, clipped: false }
}

/** *s* in at most *maxEm* em on one line: whole when it fits, else without its closing （…）, else what comes before its
 * first ：, else *or* (the same said shorter elsewhere on the page), else clipped. */
export function brief(s: string, maxEm: number, or: string | null = null): string {
  const bare = s.replace(/（[^（）]*）$/, '').trim()
  const fit = [s, bare, bare.split('：')[0], or].find((x): x is string => !!x && textWidth(x) <= maxEm)
  return fit ?? clip(bare, maxEm)
}

/** The cell groups of the check wall, in wall order. */
export const CELL_GROUPS: { key: string; label: string }[] = [
  { key: 'gate', label: 'Lean 审批' },
  { key: 'verify', label: '自动检查' },
  { key: 'ci', label: 'CI' },
  { key: 'risk', label: '风险目录' },
  { key: 'handover', label: '交接' },
  { key: 'brain', label: 'Brain 和仓库' },
  { key: 'after', label: '合并以后' },
]

export const CELL_META: Record<string, { label: string; glyph: string }> = {
  pass: { label: '通过', glyph: '✓' },
  fail: { label: '没过', glyph: '✕' },
  wait: { label: '等人', glyph: '✋' },
  skip: { label: '还没到', glyph: '–' },
  watch: { label: '观察中', glyph: '⏳' },
  not_needed: { label: '这次不需要', glyph: '–' },
}

/** Who a requirement waits on, in one word, the one rule every view reads: 「等你」 only for the person looking
 * (`you`); 「等 carol」 for the owner when that is someone else (`people`, the owner's name known and *me* false: the
 * person looking is not the owner), unless the step is a fast run's MR waiting to be merged (*step* merge: its
 * reviewers, 等人审); else vocab's word (等产品, 等别人 …). */
export function whoWord(who: WaitWho, owner: string | null, me = false, step?: WaitDo): string {
  if (step === 'merge') return '等人审'
  if (who === 'people' && owner && !me) return `等 ${owner}`
  return waitsOn(who) || '等人'
}

/** The board calls the requirement's owner 你 (the backend's person.you; an older backend: the owner looking, or no owner
 * known). */
export const ownerIsYou = (b: Pick<ReqBoard, 'person'>): boolean => b.person.you ?? (b.person.me || !b.person.owner)

/** A wait said in the board's own words (UI问题清单 BOARD-11): 等你 only while the board calls the owner 你; a board
 * read by someone else names them (等 carol), whatever the record says. */
export const sayWho = (who: WaitWho, b: Pick<ReqBoard, 'person'>): WaitWho => (who === 'you' && !ownerIsYou(b) ? 'people' : who)

/** whoWord with the board's owner and whether the person looking is the owner; *who*: the requirement's wait now. */
export const boardWho = (b: Pick<ReqBoard, 'row' | 'person'>, who: WaitWho = b.row.waits.who): string => whoWord(sayWho(who, b), b.person.owner, b.person.me)

/** Whom a node's wait that began at *t* was on, by its own history (null: its history does not say). */
export function waitedOn(n: Pick<StageNode, 'history'>, t: number): WaitWho | null {
  const h = [...n.history].reverse().find((x) => x.at <= t + 1 && x.status === 'awaiting_approval')
  return h?.who ?? null
}

/** Whom a wait span of *n* was on, by its history (else the requirement's wait now): every wait in a lane said by the
 * one rule (boardWho). */
export function spanWho(n: Pick<StageNode, 'history'>, board: Pick<ReqBoard, 'row'>, s: { a: number }): WaitWho {
  return waitedOn(n, s.a) ?? board.row.waits.who
}

/** The waits on someone other than the person looking that this board shows (a run that waits, a lane's wait span),
 * in the words they are said there: 「等产品」, 「等 carol」. */
export function otherWaits(b: Pick<ReqBoard, 'row' | 'person' | 'runs' | 'nodes'>): string[] {
  const words = new Set<string>()
  const other = (who: WaitWho) => sayWho(who, b) !== 'you'
  for (const r of b.runs) if (r.now.status === 'awaiting_approval' && other(r.now.who ?? 'people')) words.add(boardWho(b, r.now.who ?? 'people'))
  for (const n of b.nodes) {
    for (const s of n.spans) {
      if (s.kind === 'wait' && other(spanWho(n, b, s))) words.add(boardWho(b, spanWho(n, b, s)))
    }
  }
  return [...words]
}

/** A run in a word or two, for a box's line, a round's and the conveyor's count: 本机 (the owner's name when their
 * session is on another machine) · 运行机 (运行机 2 from its second run) · PAY-12. */
export function runShort(run: Run | undefined, b: Pick<ReqBoard, 'person'>): string {
  if (!run) return ''
  if (run.kind === 'local') return run.readable === 'here' ? '本机' : b.person.owner ?? '负责人'
  if (run.kind === 'runner') return run.n && run.n > 1 ? `运行机 ${run.n}` : '运行机'
  return run.req ?? run.label
}

/** The RoleIcon a run is drawn with: this machine's session, the team runner, a small or the big requirement. */
export const RUN_ICON: Record<Run['kind'], string> = { local: 'laptop', runner: 'runner', child: 'pr', parent: 'pr' }

/** The session a run opens: the one at work, else its latest on this machine. */
export const sessionOf = (r: Run | undefined) => r?.sessions.find((s) => s.busy) ?? r?.sessions[r.sessions.length - 1]

/** The session a person replies in for *r*: for the local run the live dev session the backend names (live_slot, never
 * a review session beside it), else sessionOf. */
export function replySession(b: Pick<ReqBoard, 'runs' | 'live_slot'>, r: Run | undefined = b.runs.find((x) => x.kind === 'local')) {
  if (r?.kind !== 'local') return sessionOf(r)
  return r.sessions.find((s) => s.slot === b.live_slot)
}

/** The gate that waits now on a node: asked (pending) or held back on the runner (queued). */
export const openGate = (n: Pick<StageNode, 'gates'>): Gate | null => n.gates.find((g) => g.state === 'pending' || g.state === 'queued') ?? null

/** Whom a node waits on: its open gate's, else its run's wait while the run is at it; null when it does not wait. */
export function nodeWait(n: StageNode, run: Run | undefined): WaitWho | null {
  if (n.status !== 'awaiting_approval') return null
  return openGate(n)?.who ?? (run && run.now.node === n.id ? run.now.who : null) ?? 'people'
}

/** whoWord with the board's owner, for a wait that may not say whom (then someone: 等别人). */
export const whoOf = (who: WaitWho | null, b: Pick<ReqBoard, 'person'>): string => whoWord(sayWho(who ?? 'people', b), b.person.owner, b.person.me)

/** A node's state in words: one that waits says whom (等你, 等产品). */
export function nodeLabel(n: StageNode, run: Run | undefined, b: Pick<ReqBoard, 'person'>): string {
  const wait = nodeWait(n, run)
  return wait ? whoOf(wait, b) : actorMeta(n.status).label
}

/** What a gate approves, in a few words: 需求和设计 · 代码生成计划 · 发布. */
export const gateWhat = (kind: GateKind): string => approvalName(kind).replace(/审批$/, '')

/** An open gate's verb: 等你批, 等产品验收, 等 carol 批. */
export function gateVerb(g: Gate, b: Pick<ReqBoard, 'person'>): string {
  if (g.who === 'you') return '等你批'
  return g.kind === 'RELEASE' && g.who === 'product' ? '等产品验收' : whoThen(whoOf(g.who, b), '批')
}

/** Who decided, as a round says it: 你, 产品, bob, 大需求的负责人. */
export const decider = (by: string | null): string => (!by ? '有人' : by.startsWith('产品') ? '产品' : by)

/** A round's colour by how it ended. */
export const ROUND_TONE: Record<Round['outcome']['kind'], Tone> = {
  open: 'run', done: 'ok', approved: 'ok', passed: 'ok', sent_back: 'bad', refused: 'warn', failed: 'bad', superseded: 'idle', skipped: 'idle',
}

/** A failed pipeline, as every place that names it says it: CI 没过 #1234 (backend stages.ci_words). */
export const ciWords = (p: PipelineRef): string => `CI 没过 #${p.id}`

/** The failed pipeline a round was done for (its why is ci_failed: read from GitLab on a person's machine), or null. */
export const ciCause = (r: Round): PipelineRef | null => (r.why.kind === 'ci_failed' ? r.why.pipeline : null)

/** What a list of a node's rounds says before a round done because a pipeline failed: CI 没过 #1234 以后; '' when it was
 * not, or when the round before already ends with that pipeline (发布's CI 没过 #1234，代码又改了). */
export function causeWords(r: Round, n: Pick<StageNode, 'rounds'>): string {
  const ci = ciCause(r)
  return ci && n.rounds.find((x) => x.n === r.n - 1)?.outcome.pipeline?.id !== ci.id ? `${ciWords(ci)} 以后` : ''
}

/** How a round ended, in words: 产品退回（D-2）, CI 没过 #1234，代码又改了（2e8d1f6「修好 CI：api 的 lint」）, 请批以后代码又改了
 * (when the pipelines are not read here), 检查全过（3 项）. */
export function roundWords(r: Round, n: Pick<StageNode, 'gates'>, b: Pick<ReqBoard, 'person'>): string {
  const o = r.outcome
  const said = o.decision ? `（${o.decision}）` : ''
  switch (o.kind) {
    case 'open': {
      const g = openGate(n)
      return g && g.round === r.n ? `${gateVerb(g, b)}${g.mr?.decision ? `（${g.mr.decision}）` : ''}` : '在做'
    }
    case 'done':
      return '做完'
    case 'approved':
      return whoThen(decider(o.by), '批了')
    case 'sent_back':
      return o.by === '你' ? `你退回了${said}` : `${whoThen(decider(o.by), '退回')}${said}`
    case 'refused':
      return `批准的那一行没被接受${o.text ? `：${o.text}` : ''}`
    case 'superseded':
      return `${o.pipeline ? `${ciWords(o.pipeline)}，代码又改了` : '请批以后代码又改了'}${o.sha ? `（${o.sha.slice(0, 7)}${o.subject ? `「${o.subject}」` : ''}）` : ''}`
    case 'passed':
      return `检查全过（${o.checks.length} 项）`
    case 'failed': {
      const bad = o.checks.filter((c) => !c.ok).map((c) => c.name)
      return bad.length ? `${bad.join('、')} 没过` : '检查没过'
    }
    case 'skipped':
      return `跳过${o.text ? `：${o.text}` : ''}`
  }
}

const pad2 = (v: number) => String(v).padStart(2, '0')

/** 11:07 on the board's day (*today*, epoch s), else 11-10 11:07. */
export function clockAt(t: number, today: number): string {
  const d = new Date(t * 1000)
  const hm = `${pad2(d.getHours())}:${pad2(d.getMinutes())}`
  return new Date(today * 1000).toDateString() === d.toDateString() ? hm : `${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${hm}`
}

/** How long a stage took, the reference's way: 0:28, 1:09 (hours and minutes). */
export function fmtHm(sec: number): string {
  const m = Math.max(1, Math.round(sec / 60))
  return `${Math.floor(m / 60)}:${pad2(m % 60)}`
}

/** A person's pushes onto the runner's branch after the hand-over: the steps outside the judgment points (the backend
 * puts such a commit in the runner's run, by a person). */
export const outsidePushes = (b: Pick<ReqBoard, 'belt' | 'runs'>): BeltItem[] =>
  b.belt.filter((x) => x.kind === 'commit' && x.by === 'person' && b.runs.some((r) => r.key === x.run && r.kind === 'runner'))

/** The ones a requirement may wait on that are people. */
export const PERSON: ReadonlySet<WaitWho> = new Set<WaitWho>(['you', 'product', 'people', 'builder'])

/** The board waits on the person looking at it. */
export const waitsOnMe = (b: Pick<ReqBoard, 'row'>) => b.row.waits.who === 'you'

/** Its repo has no remote: done only on this machine (a board without row.local_only: it was never handed over and no
 * runner ran it). */
export function localOnly(b: Pick<ReqBoard, 'row' | 'relays' | 'runs'>): boolean {
  return b.row.local_only ?? (!b.relays.length && !b.runs.some((r) => r.kind === 'runner'))
}

/** The team runner's page of a requirement it has no working directory for yet (the bridge took it; the signer signs, or
 * the runner prepares): nothing of its records is readable here, nor its branch (backend board.runner_blind). */
export const runnerBlind = (b: Pick<ReqBoard, 'row'>): boolean =>
  b.row.where === 'runner' && !b.row.worktree && !['claimed', 'merged', 'closed'].includes(b.row.state)

/** The requirement's title when it has one of its own (one whose claim is not read yet is called by its key only). */
export const titleOf = (r: Pick<ReqBoard['row'], 'key' | 'title'>): string => (r.title && r.title !== r.key ? r.title : '')

/** *started*: the next stage is the one being worked on (「在做」), not one still to do; *stage_state* says the same in
 * LeanSummary's word, for vocab.leanProgress */
export type LeanNow = Pick<LeanSummary, 'done_n' | 'skipped_n' | 'next' | 'total' | 'skipped_phases'> & { started: boolean; stage_state: 'started' | null }

/** Lean's progress the one way (vocab.leanProgress's parts). */
export function leanOf(l: LeanSummary): LeanNow {
  const started = l.stage_state === 'started' && !!l.next && (!l.stage || l.stage === l.next.id)
  return { done_n: l.done_n, skipped_n: l.skipped_n, next: l.next, total: l.total, skipped_phases: l.skipped_phases, started,
    stage_state: started ? 'started' : null }
}

export interface ChecksSay {
  text: string
  tone: 'ok' | 'bad' | 'approve' | 'info' | 'idle'
  /** every check passed and none is left to come */
  green: boolean
}

/** The checks not reached yet, by name: the backend's todo (without the 14 days it watches: those are 在观察, nobody's
 * turn, not something left to do), else an older backend's approvals and verify not run yet. */
export function todoLeft(w: CheckWall): string[] {
  const watched = w.cells.filter((c) => c.status === 'watch').map((c) => c.name)
  if (w.todo) return w.todo.filter((t) => !watched.some((n) => n.startsWith(t)))
  return w.cells.filter((c) => c.status === 'skip' && (c.group === 'gate' || c.group === 'verify')).map((c) => (c.group === 'gate' ? c.name.replace(/（[^）]*）$/, '') : c.label))
}

/** What the checks say, the one way (the KPI, the wall, 验收): 「目前 3 项都过 · 还剩 发布审批」. A check that waits for
 * a person names whom; the 14 days after the merge are 「在观察」, nobody's turn. */
export function checksSay(w: CheckWall, b: Pick<ReqBoard, 'row' | 'person'>): ChecksSay {
  const who = b.row.waits.who
  const n = (s: string) => w.cells.filter((c) => c.status === s).length
  const wait = n('wait')
  const watch = n('watch')
  const todo = todoLeft(w)
  const left = todo.length ? ` · 还剩 ${todo.join('、')}` : ''
  const watching = watch ? ` · ${watch} 项在观察` : ''
  if (!w.total && !watch) return { text: '还没有检查结果', tone: 'idle', green: false }
  if (w.failed) return { text: `${w.failed} 项没过${left}`, tone: 'bad', green: false }
  if (wait) return { text: `${wait} 项${PERSON.has(who) ? boardWho(b) : '等人'}${left}`, tone: 'approve', green: false }
  const all = w.passed === 1 ? '1 项过了' : `${w.passed} 项都过`
  if (todo.length) return { text: `目前 ${all}${left}${watching}`, tone: 'info', green: false }
  return { text: `${w.passed === 1 ? '1 项过了' : `${w.passed} 项全过`}${watching}`, tone: 'ok', green: !watch }
}

/** 「等 carol 在 MR 上回答」, 「等产品在 MR 上回答」: a wait word and what follows, a space after a Latin name. */
export const whoThen = (who: string, rest: string): string => `${who}${/[A-Za-z0-9]$/.test(who) && /^[^\s，。：；、）]/.test(rest) ? ' ' : ''}${rest}`
