/**
 * What the person looking at the board does now, read from one judgment (UI问题清单 #1, #4): the requirement's
 * `waits` (who, do, since, where), 验收's step, the runs and gates that wait for them, and the questions they can
 * answer right here. The strip above the graph, the chips under the nodes and the counters all read this. Pure.
 */
import type { Question, ReqBoard, WaitDo, Waits, WaitWho } from '../types'
import { isOpenQuestion } from '../ui'
import { ACT } from '../vocab'
import { leanOf, openGate, replySession, waitsOnMe } from './format'

export interface Step {
  id: string
  do: WaitDo
  /** what to do, one plain line */
  text: string
  /** why it came to this (a refused hand-over's reasons, in short Chinese) */
  why?: string
  /** a line under it: what to say, where */
  hint?: string
  /** the line the person types themselves in the session */
  line?: string | null
  /** what they write on the MR after the hand-over (`answer D-3 approve`) */
  comment?: string | null
  /** Lean's next step is a review in a new session (the session that wrote the code may not review it): 开审查会话 */
  review?: boolean
  /** the line 开审查会话 says to the new session (Lean's own), to copy for a session opened by hand */
  say?: string | null
  /** a command typed in a terminal, in this directory */
  command?: { text: string; cwd: string | null } | null
  session?: string | null
  mr?: string | null
  question?: Question
  /** the original words (folded under 细节) */
  raw?: string
}

export interface Next {
  who: WaitWho
  /** the person looking takes the step */
  mine: boolean
  /** since when it waits like this */
  since: number | null
  /** the steps, the ones answerable right here first; empty when it is not the person's turn */
  steps: Step[]
  /** whom or what it waits for, said (when it is not the person's turn) */
  text: string
  mr: string | null
}

export const openHere = (q: Question) => isOpenQuestion(q) && q.answerable_here && !q.lean_request

/** What a requirement waits for is Lean's review, in a new session (Lean's next note, the rows' waits.text). */
export const isReview = (text: string) => /审查/.test(text) && /新会话/.test(text)

export const REVIEW_SENDS = `${ACT.review}会把 Lean 的审查要求发给新会话`

/** Its next step is Lean's review in a new session: the backend says so (waits.review); an older one said it in words. */
export const reviewNext = (w: Waits): boolean => w.review ?? isReview(w.text)

/** The step an older backend did not name: from the requirement's state, else what its runs wait on the person for. */
function guessDo(b: ReqBoard): WaitDo {
  const st = b.row.state
  if (st === 'claimed') return 'start'
  if (st === 'rejected') return 'redo_handoff'
  if (st === 'acceptance') return 'accept'
  if (st === 'parked') return 'answer'
  const asked = b.runs.find((r) => r.now.who === 'you' && r.now.do)?.now.do
  if (asked) return asked
  if (b.accept.step.line || b.nodes.some((n) => openGate(n)?.line)) return 'approve'
  if (st === 'merged' && b.nodes.find((n) => n.id === 'reflect')?.status === 'awaiting_approval') return 'write_back'
  if (st === 'local' && b.row.waits.who === 'you') return 'reply'
  return 'none'
}

export const doOf = (b: ReqBoard): WaitDo => b.row.waits.do ?? guessDo(b)

/** This machine's session: 验收's, else the local run's. */
const localSession = (b: ReqBoard) => b.accept.step.session_url ?? replySession(b)?.url ?? null

export function nextOf(b: ReqBoard): Next {
  const w = b.row.waits
  const mine = w.who === 'you'
  const mr = b.accept.step.mr_url ?? b.row.mr?.url ?? null
  const waiting = b.runs.filter((r) => r.now.status === 'awaiting_approval')
  // the backend's since (null: it does not know); an older backend's, from the runs that wait
  const since = w.since !== undefined ? w.since : waiting.map((r) => r.now.since).filter((x): x is number => x !== null).sort((x, y) => x - y)[0] ?? null
  if (!mine) return { who: w.who, mine, since, steps: [], text: w.text, mr }
  const steps: Step[] = []
  b.questions.forEach((q, i) => {
    if (openHere(q)) steps.push({ id: `q-${q.id ?? i}`, do: 'answer', text: q.question, question: q })
  })
  const st = b.accept.step
  const d = doOf(b)
  const session = localSession(b)
  const text = st.text || w.text
  if (text && d !== 'none' && d !== 'watch') {
    const main: Step = { id: 'main', do: d, text, line: st.line, comment: st.comment ?? null, mr: st.mr_url, session: st.session_url }
    if (d === 'reply') {
      const next = leanOf(b.row.lean).next
      main.session = st.session_url ?? session
      if (reviewNext(w)) Object.assign(main, { text: w.text, line: null, review: true, hint: REVIEW_SENDS, say: w.review !== undefined ? st.line : null })
      else if (next && !text.includes(next.label)) main.hint = `Lean 的下一步是「${next.label}」：在会话里让它接着做`
    }
    if (d === 'tool') main.session = st.session_url ?? session
    if (d === 'redo_handoff') {
      main.why = w.text.replace(/^接手被拒[：:]\s*/, '')
      // the step's own first line says the refusal again: the why above says it
      main.text = text.split('\n').filter((l) => !l.startsWith('接手被拒')).join('\n') || text
      main.command = { text: 'brain handoff', cwd: b.row.worktree }
      main.raw = w.raw || undefined
      main.session = st.session_url ?? session
      main.mr = st.mr_url ?? mr
    }
    if (d === 'answer' && steps.some((s) => s.question)) main.text = ''
    if (main.text) steps.push(main)
  }
  // another approval the person types, besides the one asked now; a tool call a run waits on, besides the step's
  for (const n of b.nodes) {
    const g = openGate(n)
    if (g?.line && g.who === 'you' && g.line !== st.line && !steps.some((x) => x.line === g.line)) {
      const run = b.runs.find((r) => r.key === n.run)
      steps.push({ id: `g-${n.id}`, do: 'approve', text: n.doing, line: g.line, session: replySession(b, run)?.url ?? session })
    }
  }
  for (const r of waiting) {
    if (r.now.who === 'you' && r.now.do === 'tool' && d !== 'tool') steps.push({ id: `r-${r.key}`, do: 'tool', text: r.now.doing, session: replySession(b, r)?.url ?? session })
  }
  return { who: w.who, mine, since, steps, text: w.text, mr }
}

/** How many things wait, the one count the page says (这条路走到哪了): on the person looking, what 你要做的一步 lists (a
 * hand-over to label is one, though no gate asks); else, and in a replay, the runs that wait. */
export function waitCount(b: ReqBoard, replaying = false): number {
  return waitsOnMe(b) && !replaying ? nextOf(b).steps.length : b.runs.filter((r) => r.now.status === 'awaiting_approval').length
}
