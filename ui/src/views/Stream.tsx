/**
 * 价值流: a requirement's time, from the board's stages (each span of a stage, and the run that was at it: the AI on this
 * machine, the runner, waiting for a person, done again), from its start to the merge or now, as the sample's
 * StreamChart shows it: one band on a time axis, each part named (the stage, the gate it waited at) with how it ended,
 * who was at it above, the moments under it; 图 or 表格.
 */
import { useState } from 'react'
import { clip, textWidth } from '../board/format'
import { RoleGlyph } from '../board/RoleIcon'
import { Hit, Tip, useWidth, type TipState } from '../cto/charts'
import { fmtDay, fmtSpan, isoDay } from '../format'
import type { BoardEvent, ReqBoard, Run, StageNode } from '../types'
import { approvalName } from '../vocab'

/** Where the time went: AI on this machine, the runner, waiting for a person, done again. */
export type Part = 'agent' | 'runner' | 'person' | 'rework'
export const PART_LABEL: Record<Part, string> = { agent: 'AI 在本机做', runner: '运行机在做', person: '等人', rework: '返工' }
export const PART_VAR: Record<Part, string> = { agent: 'var(--vs-agent)', runner: 'var(--s1)', person: 'var(--vs-person)', rework: 'var(--vs-rework)' }
export const PARTS: Part[] = ['agent', 'runner', 'person', 'rework']
const RANK: Record<Part, number> = { person: 4, rework: 3, runner: 2, agent: 1 }
const DECISION_WORD: Partial<Record<BoardEvent['kind'], string>> = { approve: '批准', reject: '退回', answer: '回答了', accepted: '接手', rejected: '拒收' }

/** One stretch of the 价值流: what was most telling then (a wait for a person over work going on meanwhile), whose. */
export interface StreamSeg {
  from: number
  to: number
  part: Part
  /** the stage it is on (a board node id); `run:<key>` for a run's wait on no stage */
  node: string
  /** 需求分析, 需求和设计审批, 返工：应用设计 … */
  label: string
  /** who was at it: 人, AI（本机会话）, 运行机 第 1 次 … */
  who: string
  /** its RoleIcon above the band */
  icon: string
  /** how a wait ended: 批准, 退回, 回答了 … */
  decision: string | null
}

const titleOf = (n: StageNode) => (n.unit ? `${n.title} · ${n.unit}` : n.title)

function partOf(kind: 'work' | 'wait' | 'fix', run: Run | undefined): Part {
  return kind === 'wait' ? 'person' : kind === 'fix' ? 'rework' : run?.kind === 'runner' ? 'runner' : 'agent'
}

/** A wait is what it waited for: the gate of the round it is in (需求和设计审批), else its stage waiting on someone. */
function labelOf(n: StageNode, part: Part, at: number): string {
  if (part === 'rework') return `返工：${titleOf(n)}`
  if (part !== 'person') return titleOf(n)
  const round = [...n.rounds].reverse().find((r) => r.started <= at + 1)
  const gate = n.gates.find((g) => g.round === round?.n) ?? n.gates[0]
  return gate ? approvalName(gate.kind) : `${titleOf(n)}等人`
}

/** A run's own wait, on no stage: 会话等人 (this machine's session), 运行机 第 1 次等人. */
const runWaits = (run: Run | undefined) => (!run || run.kind === 'local' ? '会话等人' : `${run.label}等人`)

function whoOf(part: Part, run: Run | undefined): string {
  if (part === 'person') return '人'
  if (run && run.kind !== 'local') return run.label
  return part === 'rework' ? 'AI' : 'AI（本机会话）'
}

/** The requirement's time from its start to the merge (or now) as segments in order; quiet stretches with nothing
 * recorded are left out, and nothing after the merge counts (the write-back that follows is said on its own). */
export function valueStream(b: ReqBoard, now: number): StreamSeg[] {
  const stop = b.ended_at ?? now
  const runs = new Map<string, Run>(b.runs.map((r) => [r.key, r]))
  const ivs: { a: number; b: number; part: Part; node: StageNode | null; run: Run | undefined }[] = []
  for (const n of b.nodes) {
    for (const s of n.spans) {
      const end = Math.min(s.b ?? stop, stop)
      const run = runs.get(s.run ?? n.run ?? '')
      if (end > s.a) ivs.push({ a: s.a, b: end, part: partOf(s.kind, run), node: n, run })
    }
  }
  // a run that waits on its person between two stages is on no stage's lane: only its now says it (since then)
  for (const r of b.runs) {
    const since = r.now.since
    if (r.now.status !== 'awaiting_approval' || since === null || since >= stop) continue
    const onLane = ivs.some((x) => x.run?.key === r.key && x.part === 'person' && x.a <= since + 60 && x.b >= stop - 1)
    if (!onLane) ivs.push({ a: since, b: stop, part: 'person', node: b.nodes.find((n) => n.id === r.now.node) ?? null, run: r })
  }
  const pts = [...new Set(ivs.flatMap((x) => [x.a, x.b]))].sort((x, y) => x - y)
  const out: StreamSeg[] = []
  for (let i = 0; i < pts.length - 1; i++) {
    const t0 = pts[i], t1 = pts[i + 1]
    const on = ivs.filter((x) => x.a <= t0 && x.b >= t1)
    if (!on.length) continue
    // a wait for a person says more than work going on meanwhile; at a tie the stage first on the way
    const top = on.sort((x, y) => RANK[y.part] - RANK[x.part])[0]
    const last = out[out.length - 1]
    const node = top.node?.id ?? `run:${top.run?.key ?? ''}`
    if (last && last.part === top.part && last.node === node && Math.abs(last.to - t0) < 1) last.to = t1
    else out.push({ from: t0, to: t1, part: top.part, node, label: top.node ? labelOf(top.node, top.part, t0) : runWaits(top.run), who: whoOf(top.part, top.run),
      icon: top.part === 'person' || !top.node ? 'wait' : top.node.icon, decision: null })
  }
  for (const s of out) {
    if (s.part !== 'person') continue
    const e = b.events.find((ev) => DECISION_WORD[ev.kind] && ev.node === s.node && Math.abs(ev.at - s.to) <= 5)
    s.decision = e ? DECISION_WORD[e.kind] ?? null : null
  }
  return out
}

export function splitOf(segs: StreamSeg[]) {
  return PARTS.map((p) => ({ p, s: segs.filter((g) => g.part === p).reduce((a, g) => a + g.to - g.from, 0) }))
}

const pad = (n: number) => String(n).padStart(2, '0')
/** 14:05 (local) */
const hm = (t: number) => {
  const d = new Date(t * 1000)
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`
}
/** 11-09 14:05 when the day matters, else 14:05 */
const when = (t: number, withDay: boolean) => (withDay ? `${fmtDay(t).slice(5)} ${hm(t)}` : hm(t))

const STEPS = [60, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800, 604800]

/** Ticks on local clock marks (every 10 minutes, every hour, every day …), at most about 7 of them. */
function ticksOf(t0: number, t1: number): number[] {
  const step = STEPS.find((s) => (t1 - t0) / s <= 7) ?? STEPS[STEPS.length - 1]
  const off = new Date(t0 * 1000).getTimezoneOffset() * 60
  const out: number[] = []
  for (let t = Math.ceil((t0 - off) / step) * step + off; t < t1; t += step) out.push(t)
  return out
}

const MOMENT_ICON: Partial<Record<BoardEvent['kind'], string>> = {
  start: 'new', approve: 'approved', reject: 'bounce', handover: 'handover', accepted: 'bridge', rejected: 'bounce', question: 'question', answer: 'person', merge: 'merge', reflect: 'brain',
}
const momentTone = (k: BoardEvent['kind']) => (k === 'reject' || k === 'rejected' ? 'is-back' : k === 'question' ? 'is-wait' : 'is-ok')

/** 价值流: the requirement's time on one band over a time axis — AI on this machine, the runner, waiting for a person,
 * done again — who was at it above, its moments under it; 图 or 表格; every part and moment opens the board there. */
export function StreamCard({ board, at, onAt }: { board: ReqBoard | null; at: number; onAt: (t: number | null) => void }) {
  const [asTable, setAsTable] = useState(false)
  const segs = board ? valueStream(board, at) : []
  if (!board || !segs.length) return null
  const t0 = segs[0].from, t1 = segs[segs.length - 1].to
  // the whole time is 周期's (brain start → the merge or now); a quiet end with nothing recorded is said, not drawn
  const begin = board.events.find((e) => e.kind === 'start')?.at ?? t0
  const end = Math.max(t1, board.ended_at ?? at)
  const parts = PARTS.filter((p) => segs.some((s) => s.part === p))
  const days = isoDay(new Date(t0 * 1000).toISOString()) !== isoDay(new Date(t1 * 1000).toISOString())
  return (
    <section className="cto-card" aria-label="价值流" data-testid="item-stream">
      <div className="cto-chart-head">
        <h3>价值流</h3>
        <span className="cto-dim">从开工到{board.ended_at ? '合并' : '此刻'}，共 {fmtSpan(end - begin)}{end - t1 >= 600 ? `（最后 ${fmtSpan(end - t1)} 没有动静）` : ''} · 点任一段或任一时刻，到需求看板的那一刻</span>
        <span className="cto-grow" />
        <div className="cto-seg" role="group" aria-label="图或表格">
          <button type="button" aria-pressed={!asTable} onClick={() => setAsTable(false)}>图</button>
          <button type="button" aria-pressed={asTable} onClick={() => setAsTable(true)}>表格</button>
        </div>
      </div>
      <div className="cto-legend">
        {parts.map((p) => <span key={p}><i className="cto-sw" style={{ background: PART_VAR[p] }} />{PART_LABEL[p]}</span>)}
        <span className="cto-dim">· 色块上方是谁在做，下方是关键时刻 · 安静的时段没画</span>
      </div>
      {asTable ? (
        <div className="br-scroll">
          <table className="cto-table" aria-label="价值流的每一段">
            <thead><tr><th>时间</th><th>环节</th><th>谁</th><th className="is-num">用时</th><th>结论</th><th /></tr></thead>
            <tbody>
              {segs.map((s) => (
                <tr key={`${s.from}-${s.node}`}>
                  <td className="kt-mono">{when(s.from, days)}–{when(s.to, days)}</td>
                  <td><span className="cto-kind"><i className="cto-sw" style={{ background: PART_VAR[s.part] }} />{s.label}</span></td>
                  <td>{s.who}</td>
                  <td className="is-num">{fmtSpan(s.to - s.from)}</td>
                  <td>{s.decision ?? ''}</td>
                  <td><button type="button" className="cto-link" onClick={() => onAt(s.from)}>看板 ↗</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <StreamChart board={board} segs={segs} days={days} onAt={onAt} />
      )}
    </section>
  )
}

/** The chart itself: mounted with its box, so it is measured at the width it has. */
function StreamChart({ board, segs, days, onAt }: { board: ReqBoard; segs: StreamSeg[]; days: boolean; onAt: (t: number | null) => void }) {
  const [ref, W] = useWidth(1100)
  const [tip, setTip] = useState<TipState>(null)
  // the axis starts at 开工, as 周期 does, even when the first stage's own record starts later (nothing is drawn between)
  const t0 = Math.min(segs[0].from, board.events.find((e) => e.kind === 'start')?.at ?? Infinity), t1 = segs[segs.length - 1].to
  const total = Math.max(60, t1 - t0)
  const L = 6, R = 6, pw = Math.max(10, W - L - R)
  const x = (t: number) => L + ((Math.min(Math.max(t, t0), t0 + total) - t0) / total) * pw
  const BAND = 36, BH = 24, AXIS = 112, MOM = 148
  const edge = days ? 112 : 60   // the start and end labels: 11-04 08:34 is about 75 px, a tick's label half of 36
  const ticks = ticksOf(t0, t1).filter((t) => x(t) - x(t0) > edge && x(t1) - x(t) > edge)
  // the moments, those at the same spot as one icon with a count, in rows: one that would sit on top of the one before
  // it goes a row down
  const groups: { mx: number; evs: BoardEvent[] }[] = []
  for (const e of board.events.filter((ev) => MOMENT_ICON[ev.kind] && ev.at >= t0 - 1 && ev.at <= t1 + 1).sort((a, b) => a.at - b.at)) {
    const mx = Math.min(Math.max(x(e.at), L + 12), W - R - 12)
    const g = groups[groups.length - 1]
    if (g && mx - g.mx < 6) g.evs.push(e)
    else groups.push({ mx, evs: [e] })
  }
  const rows: number[] = []
  const placed = groups.map((g) => {
    let r = rows.findIndex((last) => g.mx - last >= 26)
    if (r < 0) r = rows.length < 3 ? rows.length : 2
    rows[r] = g.mx
    return { ...g, my: MOM + r * 26 }
  })
  const H = MOM + Math.max(1, rows.length) * 26 + 4
  return (
    <div className="br-vs" ref={ref}>
      <svg viewBox={`0 0 ${W} ${H}`} height={H} role="group" aria-label={'价值流：' + segs.map((s) => `${s.label} ${fmtSpan(s.to - s.from)}`).join('，')}>
        {[t0, ...ticks, t1].map((t, i, all) => (
          <g key={`${t}-${i}`}>
            <line x1={x(t)} x2={x(t)} y1={BAND - 6} y2={AXIS} stroke="var(--border)" strokeWidth={1} />
            <text x={x(t)} y={AXIS + 16} textAnchor={i === 0 ? 'start' : i === all.length - 1 ? 'end' : 'middle'}>{when(t, days && (i === 0 || i === all.length - 1 || hm(t) === '00:00'))}</text>
          </g>
        ))}
        <line x1={L} x2={W - R} y1={AXIS} y2={AXIS} stroke="var(--border-strong)" strokeWidth={1} />
        {segs.map((s, i) => {
          const a = x(s.from), bb = x(s.to), w = Math.max(2, bb - a - (i < segs.length - 1 ? 2 : 0))
          // both lines fit the part's own width (about 12.5 px an em): the next part's words start right after it
          const room = (w - 4) / 12.5
          const label = clip(s.label, room)
          const long = `${fmtSpan(s.to - s.from)}${s.decision ? ` · ${s.decision}` : ''}`
          const sub = clip(textWidth(long) <= room ? long : fmtSpan(s.to - s.from), room)
          return (
            <g key={`${s.from}-${s.node}`} className="cto-vs-seg">
              <rect x={a} y={BAND} width={w} height={BH} rx={i === 0 || i === segs.length - 1 ? 4 : 1} fill={PART_VAR[s.part]} />
              {w >= 22 && (
                <g className={`cto-vs-chip ${s.part === 'person' ? 'is-wait' : 'is-ok'}`} transform={`translate(${a},${BAND - 28})`}>
                  <rect width={20} height={20} rx={6} />
                  <RoleGlyph role={s.icon} cx={10} cy={10} size={14} />
                </g>
              )}
              {w >= 58 && (
                <>
                  <text x={a} y={BAND + BH + 18} className="is-strong">{label}</text>
                  <text x={a} y={BAND + BH + 34}>{sub}</text>
                </>
              )}
              <Hit x={a} y={BAND - 30} w={Math.max(8, w)} h={BH + 72} onTip={setTip} tipY={BAND + BH + 40} onPick={() => onAt(s.from)}
                label={`${s.label} ${fmtSpan(s.to - s.from)}`} when={`${when(s.from, days)} – ${when(s.to, days)} · ${PART_LABEL[s.part]}`}
                rows={[[fmtSpan(s.to - s.from), s.label, PART_VAR[s.part]], [s.who, '谁'] as [string, string], ...(s.decision ? [[s.decision, '结论'] as [string, string]] : []), ['点击', '到需求看板的这一刻']]} />
            </g>
          )
        })}
        {placed.map(({ mx, my, evs }) => {
          const e = evs[0]
          return (
            <g key={`${e.seq}`} className={`cto-vs-moment ${momentTone(e.kind)}`} data-testid="vs-moment">
              <line x1={mx} x2={mx} y1={BAND + BH} y2={my - 11} className="cto-vs-tick" />
              <circle cx={mx} cy={my} r={11} />
              <RoleGlyph role={MOMENT_ICON[e.kind]} cx={mx} cy={my} size={14} />
              {evs.length > 1 && (
                <g className="br-vs-n"><circle cx={mx + 10} cy={my - 9} r={7} /><text x={mx + 10} y={my - 5.5} textAnchor="middle">{evs.length}</text></g>
              )}
              <title>{evs.map((v) => `${hm(v.at)} ${v.text}`).join('\n')}</title>
              <Hit x={mx - 13} y={my - 13} w={26} h={26} onTip={setTip} tipY={my + 16} when={hm(e.at)} onPick={() => onAt(e.at)}
                label={`${hm(e.at)} ${evs.map((v) => v.text).join('；')}：看板回放到这一刻`}
                rows={[...evs.slice(0, 5).map((v) => [hm(v.at), v.text] as [string, string]), ['点击', '看板回放到这一刻']]} />
            </g>
          )
        })}
      </svg>
      <Tip tip={tip} width={W} />
    </div>
  )
}

