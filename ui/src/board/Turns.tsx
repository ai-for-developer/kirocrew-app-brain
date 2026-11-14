/**
 * 产出's session tabs (它的回复 · 接到的任务 · 做了什么): for a round its run did on this machine, the session turns it was
 * done in (GET /outputs/{repo}/{key}/turns, backend turns.py): the agent's reply, the message it got, every call it made.
 * What a person typed is never here, the owner's included: its length, its time and 在会话里看. A round done where this
 * machine cannot read its session gets the card that says where it is and what is here instead (WhyNotCard).
 */
import { useState } from 'react'
import { useNavigate } from '@kirocrew/app-sdk'
import type { CallView, NodeTurns, ReqBoard, Round, Run, StageNode, TaskView, TurnView, WhyNot } from '../types'
import { Markdown } from '../views/Markdown'
import { clockAt, roundWords } from './format'

export type SessionTab = 'reply' | 'task' | 'calls'

const pad2 = (v: number) => String(v).padStart(2, '0')
/** 10:05:31: a call's own second, the way the reference lists them */
const clockS = (t: number) => {
  const d = new Date(t * 1000)
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`
}

/** Lean's record of the round, from the board: 13:25 开始、14:41 产品退回（D-2）; a request still open says when it was
 * asked (a decided gate keeps only when it was decided). */
function leanLine(r: Round, n: StageNode, board: ReqBoard): string {
  const asked = n.gates.find((g) => g.round === r.n && (g.state === 'pending' || g.state === 'queued'))
  return [`${clockAt(r.started, board.at)} 开始`, asked?.at ? `${clockAt(asked.at, board.at)} 请批` : '',
    r.ended !== null ? `${clockAt(r.ended, board.at)} ${roundWords(r, n, board)}` : ''].filter(Boolean).join('、')
}

export function WhyNotCard({ w, r, n, board }: { w: WhyNot; r: Round; n: StageNode; board: ReqBoard }) {
  return (
    <div className="kt-whynot" data-testid="out-whynot">
      <h4>{w.title}</h4>
      {w.missing && <p>{w.missing}</p>}
      <p>这台机器上有：</p>
      <ul>
        {w.here.map((x) => <li key={x}>{x}</li>)}
        {n.stage !== 'reflect' && <li>Lean 记下的这一轮：{leanLine(r, n, board)}</li>}
      </ul>
      {w.do && <p className="do"><b>做什么</b>{w.do}</p>}
    </div>
  )
}

/** Who sent a turn's message, in its header: app 发的（开工消息，只有编号） · 你在会话里打的. */
function sender(t: TaskView, run: Run | undefined): string {
  switch (t.kind) {
    case 'app': return `app 发的（${t.cause}）`
    case 'bridge': return `桥发的（${t.cause}）`
    case 'person': return run?.kind === 'local' ? '你在会话里打的' : '有人在会话里打的'
    case 'hidden': return '说不清是谁发的'
  }
}

const turnName = (t: TurnView) => (t.n ? `第 ${t.n} 回合` : '一个回合')

function TaskBody({ t, run }: { t: TaskView; run: Run | undefined }) {
  const navigate = useNavigate()
  if (t.kind === 'app' || t.kind === 'bridge') return <pre className="kt-task" data-testid="out-task-text">{t.text}</pre>
  if (t.kind === 'hidden') return <div className="kt-hidden" data-testid="out-task-hidden"><span>{t.why}</span></div>
  return (
    <div className="kt-hidden" data-testid="out-task-person">
      <span>{run?.kind === 'local' ? '你' : '有人'}打的话（{t.chars} 字）不在这里显示：看板上不放有人写的话。</span>
      <button type="button" className="br-link" onClick={() => navigate(t.url)}>在会话里看 ↗</button>
    </div>
  )
}

function TaskTab({ turns, run, board }: { turns: TurnView[]; run: Run | undefined; board: ReqBoard }) {
  return (
    <div data-testid="out-tasks">
      {turns.map((t, i) => (
        <section key={i}>
          <p className="kt-task-h">{turnName(t)} · {clockAt(t.at, board.at)} · {sender(t.task, run)}</p>
          <TaskBody t={t.task} run={run} />
        </section>
      ))}
    </div>
  )
}

function ReplyTab({ turns, data, board }: { turns: TurnView[]; data: NodeTurns; board: ReqBoard }) {
  const [pick, setPick] = useState(turns.length - 1)
  const t = turns[Math.min(pick, turns.length - 1)]
  const last = t === turns[turns.length - 1]
  const span = `${clockAt(t.at, board.at)}${t.until ? `–${clockAt(t.until, board.at)}` : ''}`
  return (
    <div data-testid="out-reply-tab">
      {turns.length > 1 && (
        <div className="kt-turn-pick" role="group" aria-label="哪一回合的回复">
          {turns.map((x, i) => (
            <button key={i} type="button" className={`kt-btn is-small${x === t ? ' is-on' : ''}`} aria-pressed={x === t} onClick={() => setPick(i)}>
              {turnName(x)}
            </button>
          ))}
        </div>
      )}
      <p className="kt-attempt-meta" data-testid="out-reply-meta">
        {[t.slot ? `本机会话 ${t.slot}` : '',
          `${last ? '这一轮的最后一个回合' : '这一轮的'}（${turnName(t)} · ${span}）的回复`,
          data.meta.calls !== null ? `这一轮里 ${data.meta.calls} 次工具调用` : '',
          t.also.length ? `这一回合也做了：${t.also.join('、')}` : ''].filter(Boolean).join(' · ')}
      </p>
      {t.reply ? <div className="kt-reply"><Markdown text={t.reply} doc testid="out-reply" /></div>
        : <p className="kt-pad kt-dim" data-testid="out-reply-none">
          {t.live ? '它还在做这一回合：做完了，这里就是它的回复' : t.task.kind === 'hidden' && !t.slot ? t.task.why : '这一回合它没有回复'}
        </p>}
    </div>
  )
}

function CallRow({ c }: { c: CallView }) {
  return (
    <li className={c.in_round ? undefined : 'is-out'}>
      <span className="t">{c.at ? clockS(c.at) : ''}</span>
      <span className="k">{c.tool}</span>
      <span className="d" title={c.detail ?? undefined}>{c.detail ?? ''}</span>
    </li>
  )
}

function CallsTab({ turns, r, run, board }: { turns: TurnView[]; r: Round; run: Run | undefined; board: ReqBoard }) {
  const found = turns.filter((t) => t.slot)
  const named = turns.map((t) => (t.n ? String(t.n) : '?')).join('、')
  const end = r.ended ?? null
  return (
    <div data-testid="out-calls-tab">
      <p className="kt-attempt-meta">
        这一轮做在第 {named} 回合里（Lean 的回合记录对上了会话里的 {found.length} 条消息）。亮着的是这一轮
        （{clockAt(r.started, board.at)}–{end ? clockAt(end, board.at) : '现在'}）里的调用，暗的在这一轮以外
      </p>
      <ul className="kt-calls" data-testid="out-calls">
        {turns.map((t, i) => [
          turns.length > 1 && <li key={`h${i}`} className="kt-calls-turn"><span className="t">{clockS(t.at)}</span><span className="k">{turnName(t)}</span><span className="d kt-dim">{sender(t.task, run)}</span></li>,
          ...t.calls.map((c, k) => <CallRow key={`${i}:${k}`} c={c} />),
          t.more_calls > 0 && <li key={`m${i}`}><span className="t">…</span><span className="k" /><span className="d kt-dim">还有 {t.more_calls} 次没列出来</span></li>,
          !t.calls.length && <li key={`e${i}`}><span className="t" /><span className="k" /><span className="d kt-dim">{t.slot ? '这一回合没调用工具' : '找不到这一回合的消息'}</span></li>,
        ])}
      </ul>
    </div>
  )
}

/** The body of a session tab: the card, why there is nothing, or the tab. */
export function SessionPane({ tab, data, error, r, n, run, board }: {
  tab: SessionTab; data: NodeTurns | null; error: string; r: Round; n: StageNode; run: Run | undefined; board: ReqBoard
}) {
  if (error) return <p className="kt-pad" role="alert">{error}</p>
  if (!data) return <p className="kt-pad kt-dim">读取中…</p>
  if (data.why_not) return <WhyNotCard w={data.why_not} r={r} n={n} board={board} />
  if (!data.turns.length) return <p className="kt-pad kt-dim" data-testid="out-turns-none">{data.why}</p>
  if (tab === 'reply') return <ReplyTab key={`${r.n}`} turns={data.turns} data={data} board={board} />
  if (tab === 'task') return <TaskTab turns={data.turns} run={run} board={board} />
  return <CallsTab turns={data.turns} r={r} run={run} board={board} />
}
