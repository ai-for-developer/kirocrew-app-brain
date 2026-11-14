/**
 * 回放 on the board (the sample team board's Replay.tsx): a requirement played again from its own record, with a
 * scrubber and three speeds. The board shows replayAt(board, t) (timeline.ts) instead of the latest snapshot, and every
 * tick goes through the same differ as a live poll, so particles, flips and red flashes come in the order they
 * happened. At each decision a person made (timeline.ts decisionStops) the replay's time stands still for 4 s: 2 s with
 * the ask still open, then 2 s with what they chose lit. Nothing can be done while it plays (no line, no step): it
 * costs nothing and changes nothing.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReqBoard } from '../types'
import { diffBoards, type BoardDiff } from './diff'
import { fmtSpan, fmtWhen, gateVerb, openGate } from './format'
import type { SnapshotClock } from './hooks'
import { decisionStops, replayAt, replayClock, replaySpan, type Choice, type DecisionStop, type ReplaySpan } from './timeline'

/** The whole replay plays in about this many seconds at 1×. */
export const PLAY_S = 60
export const SPEEDS = [1, 2, 4] as const
export const TICK_MS = 250
/** At a decision the replay holds 16 ticks (4 s); in the last 8 (2 s) what the person chose is lit. */
export const HOLD_TICKS = 16
export const PICK_TICKS = 8

/** The decision the replay holds on, and whether what the person chose is lit yet. */
export type Held = DecisionStop & { picked: boolean }

/** What a person chose, lit: the 退回 colour for a send-back. */
export const CHOICE_TONE: Record<Choice, 'ok' | 'fix'> = { approve: 'ok', accept: 'ok', answer: 'ok', reject: 'fix' }

/** 应用设计：等你批, then 应用设计：你退回了设计 (the board's own words for it). */
export function heldNote(b: ReqBoard, h: Held): string {
  const n = b.nodes.find((x) => x.id === h.node)
  const title = n ? (n.unit ? `${n.title} · ${n.unit}` : n.title) : '这一步'
  if (h.picked) return `${title}：${h.text}`
  const g = n ? openGate(n) : null
  return `${title}：${g ? gateVerb(g, b) : h.choice === 'answer' ? '等人回答' : '等人拍板'}`
}

export interface ReplayHandle {
  on: boolean
  board: ReqBoard | null
  clock: SnapshotClock | null
  span: ReplaySpan | null
  /** play time, 0 … total */
  p: number
  total: number
  /** the real time the replay stands at */
  t: number
  held: Held | null
  /** the requirement had ended (merged or closed) when the replay started */
  finished: boolean
  speed: number
  playing: boolean
  /** from the start, playing; or stopped at the moment *at* (epoch s: a decision, a part of 价值流) */
  start: (at?: number) => void
  exit: () => void
  toggle: () => void
  pause: () => void
  seek: (p: number) => void
  setSpeed: (s: number) => void
}

interface State {
  on: boolean
  /** play time */
  p: number
  speed: number
  playing: boolean
  /** holding on a decision (at its play time): the ticks left */
  hold: { stop: DecisionStop & { p: number }; left: number } | null
}

const OFF: State = { on: false, p: 0, speed: 1, playing: false, hold: null }

export function useReplay(board: ReqBoard | null, onDiff: (d: BoardDiff, b: ReqBoard) => void): ReplayHandle {
  const [st, setSt] = useState<State>(OFF)
  const frozen = useRef<ReqBoard | null>(null)
  const prev = useRef<ReqBoard | null>(null)
  const quiet = useRef(false)
  if (!st.on) frozen.current = board
  const src = st.on ? frozen.current : null
  const clk = useMemo(() => (src ? replayClock(src) : null), [src])
  const span = useMemo(() => (src ? replaySpan(src) : null), [src])
  const total = clk?.total ?? 0
  const t = clk ? clk.toReal(st.p) : 0
  const stops = useMemo(() => (src && clk ? decisionStops(src).map((x) => ({ ...x, p: clk.toPlay(x.at) })) : []), [src, clk])

  const start = useCallback((at?: number) => {
    frozen.current = board
    prev.current = null
    const p = at !== undefined && board ? replayClock(board).toPlay(at) : 0
    setSt({ on: true, p, speed: 1, playing: at === undefined, hold: null })
  }, [board])
  const exit = useCallback(() => {
    prev.current = null
    setSt(OFF)
  }, [])
  const toggle = useCallback(() => setSt((s) => {
    if (s.p >= total) {
      quiet.current = true
      prev.current = null
      return { ...s, p: 0, playing: true, hold: null }
    }
    return { ...s, playing: !s.playing }
  }), [total])
  const pause = useCallback(() => setSt((s) => ({ ...s, playing: false })), [])
  const seek = useCallback((p: number) => {
    quiet.current = true
    setSt((s) => ({ ...s, p, hold: null }))
  }, [])
  const setSpeed = useCallback((speed: number) => setSt((s) => ({ ...s, speed })), [])

  useEffect(() => {
    if (!st.on || !st.playing || !total) return
    const step = (total / PLAY_S) * (TICK_MS / 1000)
    const timer = setInterval(() => setSt((s) => {
      if (s.hold && s.hold.left > 1) return { ...s, hold: { ...s.hold, left: s.hold.left - 1 } }
      const p = Math.min(total, s.p + step * s.speed)
      const stop = stops.find((x) => x.p > s.p && x.p <= p)
      if (stop) return { ...s, p: stop.p, hold: { stop, left: HOLD_TICKS } }
      return { ...s, p, playing: p < total, hold: null }
    }), TICK_MS)
    return () => clearInterval(timer)
  }, [st.on, st.playing, total, stops])

  const shown = useMemo(() => (src && span ? replayAt(src, t) : null), [src, span, t])
  useEffect(() => {
    if (!shown) return
    // events up to two ticks of the requirement's time old still fly
    const late = Math.max(30, (total / PLAY_S) * st.speed * (TICK_MS / 1000) * 2)
    const d = diffBoards(prev.current, shown, { at: t, resync: quiet.current, lateS: late })
    quiet.current = false
    prev.current = shown
    onDiff(d, shown)
  }, [shown]) // eslint-disable-line react-hooks/exhaustive-deps

  const clock = useMemo(() => (shown ? { at: t, receivedAt: Number.POSITIVE_INFINITY } : null), [shown, t])
  const finished = !!src && (src.ended_at !== null || src.row.state === 'merged' || src.row.state === 'closed')
  const held = st.hold ? { ...st.hold.stop, picked: st.hold.left <= PICK_TICKS } : null
  return { on: st.on, board: shown, clock, span, p: st.p, total, t, held, finished, speed: st.speed, playing: st.playing, start, exit, toggle, pause, seek, setSpeed }
}

/** The replay's controls, under the top bar: what is playing, how far, how fast. */
export function ReplayBar({ rp }: { rp: ReplayHandle }) {
  if (!rp.on || !rp.span) return null
  const ended = rp.p >= rp.total
  return (
    <div className="kt-replay" role="group" aria-label="回放" data-testid="br-replay">
      <span className="kt-pill tone-info">回放</span>
      <button type="button" className="kt-btn kt-btn-icon" onClick={rp.toggle} aria-label={rp.playing ? '暂停' : ended ? '从头再放' : '播放'} title={rp.playing ? '暂停' : ended ? '从头再放' : '播放'}>
        {rp.playing ? '⏸' : ended ? '↺' : '▶'}
      </button>
      <input type="range" className="kt-replay-scrub" aria-label="回放进度" min={0} max={Math.max(1, Math.round(rp.total))} step={1}
        value={Math.min(rp.total, Math.round(rp.p))} onChange={(e) => rp.seek(Number(e.target.value))} />
      <span className="kt-mono kt-replay-time" title="这一刻的真实时间">
        {fmtWhen(rp.t)} · 第 {fmtSpan(rp.t - rp.span.start)}
      </span>
      <span className="kt-replay-speeds" role="radiogroup" aria-label="倍速">
        {SPEEDS.map((s) => (
          <button key={s} type="button" role="radio" aria-checked={rp.speed === s} className={`kt-btn${rp.speed === s ? ' is-on' : ''}`} onClick={() => rp.setSpeed(s)}>
            {s}×
          </button>
        ))}
      </span>
      {rp.held && rp.board ? (
        <span className={`kt-replay-held${rp.held.picked ? ` is-${CHOICE_TONE[rp.held.choice]}` : ''}`} role="status" data-testid="br-replay-held">{heldNote(rp.board, rp.held)}</span>
      ) : (
        <span className="kt-dim kt-replay-note">{ended ? (rp.finished ? '放完了：下面是这个需求最后的样子' : '放到现在了：它还在进行') : !rp.playing && rp.p > 0 ? '停在这一刻：按 ▶ 接着放' : '按记录重放：安静的时段缩短了，不调用 agent'}</span>
      )}
      <button type="button" className="kt-btn" onClick={rp.exit}>退出回放</button>
    </div>
  )
}
