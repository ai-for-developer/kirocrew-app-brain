/**
 * The requirement's stage graph (the hero; the reference team board's TeamGraph.tsx, for a requirement's runs): a box
 * per stage a run did, in the bands of the AI-DLC phases (layout.ts stageLayout). A box says its stage and its AI-DLC
 * number, what it hands in, and its state with who did it and how long it took (✓ 本机 · 0:28); 第 N 轮 when it took
 * more than one round; its gate as a badge once decided (✓ 你批了), and while it waits a row of chips under it: a round
 * sent back before, then the one asked now, which takes the person to the step. The owner sits in the orchestrator's
 * slot with this time's runs and hand-overs under them, and a dashed line runs from a run at work or waiting to the box
 * it is on. The run branch is a rail with a dot per run and each hand-over's pill, the check ring at its end. React
 * draws the static layer; the particle layer on top (GraphFrame useParticles) blips on the box an event names.
 *
 * State is colour + shape + words, never colour alone: at work glows and its avatar ring spins; waiting for the person
 * looking blinks magenta, for anyone else a calm blue that says whom; failed is solid red with ✕; done (not merged
 * yet) is a green outline; approved or merged solid green with ✓; not reached yet dashed.
 */
import { useMemo, type KeyboardEvent, type MutableRefObject } from 'react'
import type { Gate, Relay, RelayStep, ReqBoard, Round, Run, StageNode, WaitDo, WaitWho } from '../types'
import { ExtLink } from '../ui'
import { ACT, CHILD_STATE_NAME, DO_NAME, approvalName } from '../vocab'
import type { Item } from './engine'
import {
  LIVE, ROUND_TONE, RUN_ICON, WORKING, actorMeta, brief, clip, clockAt, decider, fmtCredits, fmtHm, fmtSpan, gateVerb, gateWhat, localOnly, nodeWait,
  openGate, otherWaits, roundWords, runShort, runnerBlind, sayWho, sessionOf, textWidth, waitsOnMe, whoOf, whoThen, wrap,
} from './format'
import { GraphDefs, Halo, PersonCard, Scroller, Station, useHoverCard, useParticles, type FlashMap } from './GraphFrame'
import { useServerNow } from './hooks'
import {
  NODE_H, NODE_W, ORCH, RELAY_W, SCALE, ST_FOLD, ST_ORCH, pathD, stageAnchorBox, stageCommandPath, stageHeadX, stageParticlePath,
  type RosterKind, type StageBand, type StageBox, type StageInput, type StageLayout,
} from './layout'
import { doOf, reviewNext } from './next'
import { CHOICE_TONE, type Held } from './Replay'
import { RoleGlyph, RoleIcon } from './RoleIcon'
import type { Choice } from './timeline'

export interface StageGraphProps {
  board: ReqBoard
  lay: StageLayout
  /** the node looked at (a click on a box), or null */
  focus: string | null
  onNode: (id: string) => void
  flashes: FlashMap
  reducedMotion: boolean
  feed: MutableRefObject<((items: Item[]) => void) | null>
  /** projection or replay: no wait clocks, no tickers, nothing to click but the boxes */
  quiet?: boolean
  /** 打开会话 from a box's hover card */
  onSession?: (url: string) => void
  /** a gate's open chip: takes the person to where they act (the session, the step), as the board's other chips do */
  onAct?: (node: string, d: WaitDo) => void
  /** 看产出 from a box's hover card (a round of it: that round), 看文档 from a gate: the 产出 drawer there */
  onOutputs?: (node: string, round?: number, file?: string) => void
  /** 回放: the decision it holds on, ringed on its box; what the person chose lit under it */
  held?: Held | null
}

/** A row of the list under the orchestrator box (layout.ts RosterKind), as it is drawn. */
interface RosterRow {
  kind: RosterKind
  run: string | null
  text: string
  /** done, work, mine (waits for the person looking), other (waits for someone else), idle, bad, relay; a Brain
   * entry's: know (used), quiet (not used), stale */
  tone: 'done' | 'work' | 'mine' | 'other' | 'idle' | 'bad' | 'relay' | 'know' | 'quiet' | 'stale' | null
  tip: string
}

/** Brain entries listed by id under the orchestrator box; the rest are said as a count. */
const BRAIN_IDS = 4
/** At 12 px: a row of the list under the owner, past its dot; the owner's own line; what a box hands in (from its
 * avatar to its right edge). */
const ROW_EM = (ST_ORCH.w - 26) / 12
const ORCH_EM = (ST_ORCH.w - 16) / 12
const TASK_EM = 9.6

const RELAY_SHORT: Record<RelayStep['key'], string> = { handover: '交接', bridge: '桥', signer: '签名', workspace: '工作区' }

const isDone = (s: string) => s === 'done' || s === 'merged'
const mark = (s: RelayStep['status']) => (isDone(s) ? '✓' : actorMeta(s).icon)
/** Who decided, as a badge on a box's top edge says it: shorter, 大需求 for 大需求的负责人. */
const badgeWho = (by: string | null) => clip(decider(by).replace(/的负责人$/, ''), 5)
/** What the person chose, on the chip under the box a replay holds on. */
const CHOSE: Record<Choice, string> = { approve: '✓ 批了', accept: '✓ 验收通过', answer: '✓ 回答了', reject: '✗ 退回了' }

/** An open gate's chip: 等你批 · 需求和设计, 等产品验收 · D-3. */
const askWords = (g: Gate, b: ReqBoard) => `${gateVerb(g, b)} · ${g.mr?.decision ?? gateWhat(g.kind)}`
/** The step an open gate's chip stands for. */
const gateDo = (g: Gate): WaitDo => (g.line ? 'approve' : g.kind === 'RELEASE' ? 'accept' : 'answer')

/** The list under the orchestrator box: this time's runs (each hand-over before the run it hands to, a runner run's
 * id under its row), then what the Brain brought in. */
function rosterOf(b: ReqBoard, quiet = false): RosterRow[] {
  const rows: RosterRow[] = [{ kind: 'head', run: null, text: b.runs.length ? `这次谁在做 · ${b.runs.length} 次运行` : '这次谁在做', tone: null, tip: '' }]
  const known = new Set<string>(b.runs.map((r) => r.key))
  for (const run of b.runs) {
    for (const r of b.relays) if (r.to === run.key) rows.push(relayRow(r))
    rows.push(runRow(run, b))
    if (run.run_id) rows.push({ kind: 'sub', run: run.key, text: run.run_id, tone: null, tip: '' })
  }
  for (const r of b.relays) if (r.to === null || !known.has(r.to)) rows.push(relayRow(r))
  if (!b.runs.length) rows.push({ kind: 'run', run: null, text: '还没开工', tone: 'idle', tip: '这个需求还没有一次运行' })
  const br = b.brain
  const brain = (text: string, tone: RosterRow['tone'], tip: string): RosterRow => ({ kind: 'brain', run: null, text, tone, tip })
  rows.push(
    { kind: 'brain-head', run: null, text: 'Brain 带来的知识', tone: null, tip: '' },
    brain(br.loaded !== null ? `载入 ${br.loaded} 条` : '载入 —', br.loaded ? 'know' : 'quiet', '开工时从 Brain 带进来的条目'),
    quiet ? brain('引用 —', 'quiet', '回放时不算：引用记在决定里，决定没有自己的时间') : brain(`引用 ${br.cited.length} 条`, br.cited.length ? 'know' : 'quiet', '决定里引用的 Brain 条目'),
    ...br.cited.slice(0, BRAIN_IDS).map((id) => brain(id, 'know', `引用了 ${id}`)),
  )
  if (br.cited.length > BRAIN_IDS) rows.push(brain(`+${br.cited.length - BRAIN_IDS} 条`, 'quiet', br.cited.slice(BRAIN_IDS).join('、')))
  if (br.stale.length) rows.push(brain(`过时 ${br.stale.length} 条`, 'stale', `引用了过时的 ${br.stale.join('、')}`))
  return rows
}

function runRow(run: Run, b: ReqBoard): RosterRow {
  const s = run.now.status
  const waits = s === 'awaiting_approval'
  const tone = waits ? (run.now.who === 'you' ? 'mine' : 'other') : WORKING.has(s) ? 'work' : s === 'failed' || s === 'stalled' ? 'bad' : isDone(s) ? 'done' : 'idle'
  const tip = [run.label, run.now.doing, run.credits !== null ? `${fmtCredits(run.credits)} credits` : '', run.readable === 'elsewhere' ? '它的会话在别的机器上' : '']
  const head = `${run.label} · `
  return { kind: 'run', run: run.key, text: `${head}${brief(waits ? whoOf(run.now.who, b) : run.now.doing, ROW_EM - textWidth(head), actorMeta(s).label)}`, tone, tip: tip.filter(Boolean).join(' · ') }
}

/** The steps a hand-over shows: the workspace only until it is built, and none after the first one not through (*more*:
 * there are; its card has every step). */
function relaySteps(r: Relay): { steps: RelayStep[]; more: boolean } {
  const shown = r.steps.filter((s) => s.key !== 'workspace' || !isDone(s.status))
  const at = shown.findIndex((s) => !isDone(s.status))
  return at < 0 ? { steps: shown, more: false } : { steps: shown.slice(0, at + 1), more: at + 1 < shown.length }
}

function relayRow(r: Relay): RosterRow {
  const { steps, more } = relaySteps(r)
  const bad = r.steps.some((s) => s.status === 'failed' || s.status === 'stalled')
  const through = !more && steps.every((s) => isDone(s.status))
  const text = through ? `${steps.map((s) => RELAY_SHORT[s.key]).join(' → ')} ✓` : steps.map((s) => `${RELAY_SHORT[s.key]} ${mark(s.status)}`).join(' → ')
  return { kind: 'relay', run: null, text, tone: bad ? 'bad' : 'relay', tip: r.steps.map((s) => `${s.title}：${s.text}${s.todo ? `（做什么：${s.todo}）` : ''}`).join('\n') }
}

/** What stageLayout reads of a board. */
export function stageInput(b: ReqBoard): StageInput {
  return { phases: b.phases, nodes: b.nodes, runs: b.runs, relays: b.relays, roster: rosterOf(b).map(({ kind, run }) => ({ kind, run })) }
}

export function StageGraph({ board, lay, focus, onNode, flashes, reducedMotion, feed, quiet = false, onSession, onAct, onOutputs, held = null }: StageGraphProps) {
  const [card, hover] = useHoverCard()
  const head = stageHeadX(lay, board.belt)
  const runs = useMemo(() => new Map<string, Run>(board.runs.map((r) => [r.key, r])), [board.runs])
  const byId = useMemo(() => new Map<string, StageNode>(board.nodes.map((n) => [n.id, n])), [board.nodes])
  const particlesRef = useParticles(reducedMotion, feed, (a, b) => stageParticlePath(lay, a, b, head), (a) => stageAnchorBox(lay, a, head))
  const rows = rosterOf(board, quiet)
  const drawn = board.nodes.filter((n) => lay.boxes.has(n.id))
  // an open gate's chips stand under the last box it covers (one INCEPTION ask: under 应用设计, not also 需求分析)
  const chips = new Map<string, Gate>()
  for (const n of drawn) {
    const g = openGate(n)
    if (g) {
      for (const [id, x] of chips) if (x.kind === g.kind) chips.delete(id)
      chips.set(n.id, g)
    }
  }
  const mine = waitsOnMe(board)
  // a run waiting for the person looking where no gate is open (a tool call to approve, a reply): the step's chip
  const step = doOf(board)
  const stepWaits = !quiet && mine && step !== 'approve' && step !== 'none' && step !== 'watch'
    ? board.runs.flatMap((r) => (r.now.status === 'awaiting_approval' && r.now.who === 'you' && r.now.node && lay.boxes.has(r.now.node) && !openGate(byId.get(r.now.node)!)
      ? [byId.get(r.now.node)!] : []))
    : []
  const ask = [...chips.values()].find((g) => g.state === 'pending' && g.who === 'you')
  const asking = ask ? askWords(ask, board) : null
  // a decision held on where no gate is open (an answer): its own chip under the box
  const heldAlone = held && lay.boxes.has(held.node) && !chips.has(held.node) ? held : null
  const under = new Set<string>([...chips.keys(), ...stepWaits.map((n) => n.id), ...drawn.filter((n) => tickerOf(n, runs.get(n.run ?? ''), quiet)).map((n) => n.id),
    ...(heldAlone ? [heldAlone.node] : [])])
  const working = drawn.filter((n) => WORKING.has(n.status)).length
  const waits = drawn.map((n) => nodeWait(n, runs.get(n.run ?? ''))).filter((w): w is WaitWho => w !== null)
  const finished = drawn.filter((n) => isDone(n.status)).length
  const waitWords = [...new Set(waits.map((w) => whoOf(w, board)))].join('、')
  const label = `需求的路：${drawn.length} 步，${working} 步在做${waits.length ? `，${waits.length} 步${waitWords}` : ''}，${finished} 步做完；检查 ${board.checks.passed}/${board.checks.total}。点一步只看它。`
  // the blue in the legend is said as the boxes and the lanes say it (等产品, 等 carol)
  const others = [...new Set([...waits.filter((w) => sayWho(w, board) !== 'you').map((w) => whoOf(w, board)), ...otherWaits(board)])]
  const cardNode = card ? byId.get(card) : undefined
  const cardBox = card ? lay.boxes.get(card) : undefined

  return (
    <div className="kt-graph is-stages br-graph" data-testid="kt-graph">
      <Scroller minWidth={Math.floor(lay.width * SCALE)}>
        <svg className="kt-graph-svg" viewBox={`0 0 ${lay.width} ${lay.height}`} role="group" aria-label={label}>
          <GraphDefs />
          <rect className="kt-g-bg" x={0} y={0} width={lay.width} height={lay.height} fill="url(#kt-dots)" />
          <Bands board={board} lay={lay} runs={runs} under={under} />
          <Orchestrator board={board} lay={lay} flash={flashes[ORCH]} reducedMotion={reducedMotion} mine={mine} asking={asking}
            onHover={(on) => hover(on ? PERSON_CARD : null, on ? 300 : 250)} />
          <Roster rows={rows} lay={lay} />
          <g className="kt-edges" aria-hidden="true">
            {lay.edges.map((e) => {
              const state = edgeState(e.from === ORCH ? (board.started_at !== null ? 'done' : 'waiting') : byId.get(e.from)?.status, byId.get(e.to)?.status)
              const hot = !!focus && (e.from === focus || e.to === focus)
              return <path key={`${e.from}>${e.to}`} className={`kt-edge is-${state}${focus ? (hot ? ' is-hot' : ' is-dim') : ''}`} d={e.d} markerEnd={`url(#kt-arrow-${state})`} />
            })}
          </g>
          <Commands board={board} lay={lay} byId={byId} />
          <Rail board={board} lay={lay} head={head} focusRun={focus ? byId.get(focus)?.run ?? null : null} flash={flashes.run} />
          <Station board={board} at={lay.station} flash={flashes.tests} />
          <g className="kt-nodes">
            {drawn.map((n) => (
              <StageBoxView key={n.id} n={n} box={lay.boxes.get(n.id)!} board={board} run={runs.get(n.run ?? '')} focus={focus} flash={flashes[n.id]}
                onNode={onNode} reducedMotion={reducedMotion} quiet={quiet} onHover={(on) => hover(on ? n.id : null, on ? 300 : 250)}
                held={held?.node === n.id ? held : undefined} />
            ))}
            {[...chips].map(([id, g]) => (
              <GateChips key={`gate-${id}`} n={byId.get(id)!} g={g} box={lay.boxes.get(id)!} board={board} onAct={quiet ? undefined : onAct}
                onDoc={quiet || !onOutputs ? undefined : onOutputs} held={held?.node === id ? held : undefined} />
            ))}
            {heldAlone && <HeldChip h={heldAlone} box={lay.boxes.get(heldAlone.node)!} />}
            {stepWaits.map((n) => (
              <StepChip key={`step-${n.id}`} n={n} box={lay.boxes.get(n.id)!} d={step} review={reviewNext(board.row.waits)} onAct={onAct} />
            ))}
          </g>
          <g className="kt-particles" aria-hidden="true" ref={particlesRef} />
        </svg>
        {card === PERSON_CARD && (
          <div className="kt-ncard" style={{ top: `${((lay.orch.y - 40) / lay.height) * 100}%`, left: `${((lay.orch.x + ST_ORCH.w / 2 + 8) / lay.width) * 100}%` }}
            data-testid="kt-ncard" role="dialog" aria-label="负责人" onMouseEnter={() => hover(PERSON_CARD, 0)} onMouseLeave={() => hover(null, 200)}>
            <PersonCard board={board} mine={mine} who={whoOf(board.row.waits.who, board)} />
          </div>
        )}
        {cardNode && cardBox && (
          <div key={card} className="kt-ncard" style={cardPlace(cardBox, lay)} data-testid="kt-ncard" role="dialog" aria-label={`${cardNode.title}做了什么`}
            onMouseEnter={() => hover(card, 0)} onMouseLeave={() => hover(null, 200)}>
            <StageCard n={cardNode} board={board} runs={runs} onSession={quiet ? undefined : onSession} onOutputs={quiet ? undefined : onOutputs} />
          </div>
        )}
      </Scroller>
      <Legend others={others} relay={board.relays.length > 0} />
    </div>
  )
}

const PERSON_CARD = '__person'

/** The hover card beside its box: right of it, or left near the graph's right end; from its top, or up from its
 * bottom low in the graph. */
function cardPlace(box: StageBox, lay: StageLayout) {
  const right = box.x > lay.width * 0.62
  const low = box.y > lay.height * 0.55
  return {
    ...(low ? { bottom: `${Math.max(1, ((lay.height - (box.y + NODE_H / 2)) / lay.height) * 100)}%` } : { top: `${Math.max(1, ((box.y - NODE_H / 2) / lay.height) * 100)}%` }),
    ...(right ? { right: `${((lay.width - (box.x - NODE_W / 2 - 10)) / lay.width) * 100}%` } : { left: `${((box.x + NODE_W / 2 + 10) / lay.width) * 100}%` }),
  }
}

/** An edge's state: used (both ends got going), ready (the first is through, the next has not started), pending. */
function edgeState(a: string | undefined, b: string | undefined): 'used' | 'ready' | 'pending' {
  if (!a || !b || !(isDone(a) || a === 'awaiting_approval')) return 'pending'
  return b !== 'waiting' && b !== 'skipped' ? 'used' : 'ready'
}

/** How many lines a band's foot note may take: the room between its lowest box (and what is drawn under it) and the
 * bands' foot, up to three. */
function noteLines(band: StageBand, lay: StageLayout, under: ReadonlySet<string>): number {
  const ids = lay.columns.filter((c) => c.phase === band.key).flatMap((c) => c.ids)
  const lowest = Math.max(0, ...ids.map((id) => lay.boxes.get(id)!.y + NODE_H / 2 + (under.has(id) ? 38 : 6)))
  return Math.max(1, Math.min(3, 1 + Math.floor((lay.bottom - 22 - lowest) / 15)))
}

function Bands({ board, lay, runs, under }: { board: ReqBoard; lay: StageLayout; runs: Map<string, Run>; under: ReadonlySet<string> }) {
  const phases = new Map(board.phases.map((p) => [p.key, p]))
  return (
    <g className="kt-phases" aria-hidden="true">
      {lay.bands.map((band) => {
        const p = phases.get(band.key)
        if (!p) return null
        const cx = (band.x1 + band.x2) / 2
        if (band.folded) return <FoldedBand key={band.key} band={band} n={p.n} title={p.title.split(' · ')[0]} note={p.note ?? '这次不需要'} bottom={lay.bottom} />
        const nodes = board.nodes.filter((n) => n.phase === band.key && lay.boxes.has(n.id))
        const person = band.key === 'init'
        const mine = nodes.some((n) => nodeWait(n, runs.get(n.run ?? '')) === 'you') || (person && board.person.status === 'awaiting_approval' && waitsOnMe(board))
        const live = nodes.some((n) => LIVE.has(n.status) && n.status !== 'awaiting_approval')
        const done = nodes.length > 0 && nodes.every((n) => isDone(n.status))
        // a repo done only here has no MR to accept on: its release is Lean's approval in the session
        const sub = band.key === 'release' && localOnly(board) ? approvalName('RELEASE') : p.sub
        const notes = p.note ? wrap(p.note, (band.x2 - band.x1 - 16) / 13, noteLines(band, lay, under)) : []
        return (
          <g key={band.key} className={`kt-phase${mine ? ' is-wait' : live ? ' is-live' : ''}${done ? ' is-done' : ''}`} data-phase={band.key}>
            <rect className="kt-phase-band" x={band.x1} y={8} width={band.x2 - band.x1} height={lay.bottom - 8} rx={18} />
            <text className="kt-phase-title" x={cx} y={30} textAnchor="middle">{p.n} {p.title}</text>
            {sub && wrap(sub, (band.x2 - band.x1 - 12) / 12, 2).map((l, i) => <text key={i} className="kt-phase-stages" x={cx} y={48 + i * 15} textAnchor="middle">{l}</text>)}
            {notes.map((l, i) => <text key={i} className="kt-phase-note" x={cx} y={lay.bottom - 12 - (notes.length - 1 - i) * 15} textAnchor="middle">{l}</text>)}
          </g>
        )
      })}
      {lay.unitPill && (
        <g className="kt-units-pill" transform={`translate(${lay.unitPill.x},${lay.unitPill.y})`}>
          <rect x={-50} y={-11} width={100} height={22} rx={11} />
          <text y={4.5} textAnchor="middle">{lay.unitPill.n} 个单元</text>
        </g>
      )}
      {lay.units.map((u) => <text key={u.unit} className="kt-unit-label" x={u.x} y={u.y}>{u.unit}</text>)}
    </g>
  )
}

/** A phase not on this way, folded (the reference's 「1 构思 · 这次不需要」): its words under its title, clear of the
 * edges and the command bus that cross it. */
function FoldedBand({ band, n, title, note, bottom }: { band: StageBand; n: string; title: string; note: string; bottom: number }) {
  const cx = (band.x1 + band.x2) / 2
  return (
    <g className="kt-phase is-unused is-folded" data-phase={band.key} data-testid="kt-phase-folded">
      <rect className="kt-phase-band" x={band.x1} y={8} width={band.x2 - band.x1} height={bottom - 8} rx={14} />
      <text className="kt-phase-title" x={cx} y={30} textAnchor="middle">{n} {title}</text>
      {wrap(note, (ST_FOLD - 14) / 13, 5).map((l, i) => <text key={i} className="kt-phase-note" x={cx} y={56 + i * 15} textAnchor="middle">{l}</text>)}
    </g>
  )
}

/** The owner, in the orchestrator's slot: 你 · alice / 负责人 / what they did or wait for. Only a wait for the person
 * looking blinks; a wait for someone else says whom, calmly. Its card has the whole sentence. */
function Orchestrator({ board, lay, flash, reducedMotion, mine, asking, onHover }: {
  board: ReqBoard; lay: StageLayout; flash?: { n: number; tone: string }; reducedMotion: boolean; mine: boolean
  /** the chip of the gate that asks the person looking, the shorter way to say what they wait for */
  asking: string | null; onHover: (on: boolean) => void
}) {
  const { w, h } = ST_ORCH
  const p = board.person
  const waiting = p.status === 'awaiting_approval' && mine
  const other = p.status === 'awaiting_approval' && !mine
  const name = p.me ? `你${p.owner ? ` · ${p.owner}` : ''}` : p.owner ?? '负责人'
  const line = brief(p.doing, waiting ? ORCH_EM - 1.6 : ORCH_EM, waiting ? asking : null)
  return (
    <g className={`kt-orch br-person is-${other ? 'waiting' : p.status}${waiting ? ' is-active' : ''}${other ? ' is-other' : ''}`} transform={`translate(${lay.orch.x},${lay.orch.y})`}
      data-testid="kt-orch" tabIndex={0} aria-label={`负责人 ${p.owner ?? ''}：${p.doing}`} onMouseEnter={() => onHover(true)} onMouseLeave={() => onHover(false)}
      onFocus={() => onHover(true)} onBlur={() => onHover(false)}>
      {waiting && <Halo w={w} h={h} reducedMotion={reducedMotion} />}
      <rect className="kt-orch-body" x={-w / 2} y={-h / 2} width={w} height={h} rx={16} />
      <RoleGlyph role="person" cx={-w / 2 + 24} cy={-15} size={18} className="kt-node-emoji" />
      <text className="kt-orch-title" x={-w / 2 + 40} y={-9}>{clip(name, 8.6)}</text>
      <text className="kt-orch-cn" y={13} textAnchor="middle">{p.me || p.owner ? '负责人' : '还不知道是谁'}</text>
      <text className={`kt-orch-sub${waiting ? ' is-wait' : ''}${other ? ' is-other' : ''}`} y={30} textAnchor="middle">{waiting ? `✋ ${line}` : line}</text>
      {flash && <rect key={flash.n} className={`kt-node-flash is-${flash.tone}`} x={-w / 2 - 5} y={-h / 2 - 5} width={w + 10} height={h + 10} rx={20} />}
    </g>
  )
}

const BRAIN_CLASS: Record<string, string> = { know: 'is-team is-used', quiet: 'is-team', stale: 'is-team is-busy' }

/** Under the owner: this time's runs and hand-overs (这次谁在做), then what the Brain brought in. */
function Roster({ rows, lay }: { rows: RosterRow[]; lay: StageLayout }) {
  const w = ST_ORCH.w
  const x = lay.orch.x
  return (
    <g className="kt-roster" data-testid="kt-roster">
      {rows.map((r, i) => {
        const y = lay.roster[i].y
        if (r.kind === 'head' || r.kind === 'brain-head') return <text key={i} className="kt-roster-title" x={x} y={y + 4} textAnchor="middle">{r.text}</text>
        if (r.kind === 'sub') return <text key={i} className="kt-roster-sub" x={x - w / 2 + 16} y={y + 4}>{clip(r.text, (w - 20) / 12)}</text>
        const brain = r.kind === 'brain'
        const h = brain ? 15 : 16
        return (
          <g key={i} className={brain ? `kt-roster-item ${BRAIN_CLASS[r.tone ?? 'quiet']}` : `kt-run-row is-${r.tone}${r.kind === 'relay' ? ' is-relay' : ''}`} transform={`translate(${x},${y})`}
            data-testid={brain ? undefined : 'kt-roster-row'} data-run={r.run ?? undefined}>
            <title>{r.tip || r.text}</title>
            <rect x={-w / 2} y={-h / 2} width={w} height={h} rx={h / 2} />
            <circle cx={-w / 2 + 10} cy={0} r={3.5} />
            <text x={-w / 2 + 19} y={4}>{clip(r.text, (w - 26) / 12)}</text>
          </g>
        )
      })}
    </g>
  )
}

/** The dashed line from a run at work or waiting to the box it is on (the reference's orchestrator lines), with what
 * it is: 运行机 → 发布：等产品. Magenta only when it waits for the person looking. */
function Commands({ board, lay, byId }: { board: ReqBoard; lay: StageLayout; byId: Map<string, StageNode> }) {
  const active = board.runs.filter((r) => r.now.node && (LIVE.has(r.now.status) || r.now.status === 'queued'))
  return (
    <g className="kt-cmds" aria-hidden="true">
      {active.map((r, i) => {
        const c = stageCommandPath(lay, r.key, r.now.node!, i)
        if (!c) return null
        const waits = r.now.status === 'awaiting_approval'
        const tone = waits ? (r.now.who === 'you' ? 'awaiting_approval' : 'other') : r.now.status
        const text = `${runShort(r, board)} → ${byId.get(r.now.node!)?.title ?? ''}：${waits ? whoOf(r.now.who, board) : actorMeta(r.now.status).label}`
        const w = textWidth(text) * 12 + 22
        const x = Math.max(c.bus.x1 + w / 2 + 6, c.bus.x2 - w / 2 - 16)
        return (
          <g key={r.key} data-testid="kt-cmd" data-run={r.key}>
            <path className={`kt-cmd is-${tone}`} d={pathD(c.path)} />
            <g className={`kt-cmd-label is-${tone}`} transform={`translate(${x},${c.bus.y})`}>
              <rect x={-w / 2} y={-11} width={w} height={22} rx={11} />
              <text y={4.5} textAnchor="middle">{text}</text>
            </g>
          </g>
        )
      })}
    </g>
  )
}

/** The run branch: a dot per run where its commits drop in (×n; the hand-over's commit is its pill), each hand-over's
 * pill, the merge at the end. */
function Rail({ board, lay, head, focusRun, flash }: { board: ReqBoard; lay: StageLayout; head: number; focusRun: string | null; flash?: { n: number; tone: string } }) {
  const { x1, x2, y } = lay.rail
  const commits = new Map<string, { n: number; last: string }>()
  for (const x of board.belt) if (x.kind === 'commit' && x.run) commits.set(x.run, { n: (commits.get(x.run)?.n ?? 0) + 1, last: x.sha.slice(0, 8) })
  const merged = board.ended_at !== null || board.belt.some((x) => x.kind === 'merge')
  return (
    <g className="kt-rail" data-testid="kt-rail">
      <line className="kt-rail-base" x1={x1} y1={y} x2={x2} y2={y} />
      {board.belt.length > 0 && <line className="kt-rail-fill" x1={x1} y1={y} x2={merged ? x2 : head} y2={y} />}
      <text className="kt-rail-label" x={x1} y={y - 18}>
        运行分支 <tspan className="kt-mono">{board.branch ?? (board.row.state === 'claimed' ? '（还没开工）' : runnerBlind(board) ? '（运行机还读不到）' : '（还不知道）')}</tspan>
      </text>
      <text className="kt-rail-sub" x={x1} y={y + 24}>
        从 <tspan className="kt-mono">{board.base}</tspan> 出发{merged ? <>，已合并回 <tspan className="kt-mono">{board.base}</tspan></> : ''}
      </text>
      {board.runs.map((r) => {
        const at = lay.runX.get(r.key)
        const c = commits.get(r.key)
        if (at === undefined || !c) return null
        return (
          <g key={r.key} className={`kt-rail-dot${focusRun && focusRun !== r.key ? ' is-dim' : ''}`} transform={`translate(${at},${y})`} data-testid="kt-rail-dot" data-run={r.key}>
            <title>{`${r.label}：${c.n} 次提交，最后一次 ${c.last}`}</title>
            <circle r={10} />
            <RoleGlyph role={RUN_ICON[r.kind]} cx={0} cy={0} size={13} />
            {c.n > 1 && <text className="kt-rail-n" x={12} y={-10}>×{c.n}</text>}
          </g>
        )
      })}
      {board.relays.map((r, i) => <RelayPill key={i} r={r} x={lay.relayX[i]} y={y} today={board.at} />)}
      {merged && (
        <g className="kt-rail-dot is-merge" transform={`translate(${x2 - 16},${y})`}>
          <title>{`合并进 ${board.base}`}</title>
          <circle r={11} />
          <RoleGlyph role="merge" cx={0} cy={0} size={14} />
        </g>
      )}
      {flash && <circle key={flash.n} className={`kt-rail-flash is-${flash.tone}`} cx={head} cy={y} r={14} />}
    </g>
  )
}

/** A hand-over on the rail: 交接 ✓ · 桥 ✓ · 签名 ✓ · 11:07 (a step that went wrong red, its 做什么 on hover). */
function RelayPill({ r, x, y, today }: { r: Relay; x: number; y: number; today: number }) {
  const { steps: shown, more } = relaySteps(r)
  const bad = r.steps.some((s) => s.status === 'failed' || s.status === 'stalled')
  const through = !more && shown.every((s) => isDone(s.status))
  const last = Math.max(r.at, ...shown.map((s) => s.at ?? 0))
  const words = [...shown.map((s) => `${RELAY_SHORT[s.key]} ${mark(s.status)}`), ...(more ? ['…'] : [])].join(' · ')
  return (
    <g className={`kt-relay${bad ? ' is-bad' : ''}`} transform={`translate(${x},${y})`} data-testid="kt-relay" role="img" aria-label={`交接：${words}`}>
      <title>{r.steps.map((s) => `${s.title}：${s.text}${s.todo ? `（做什么：${s.todo}）` : ''}`).join('\n')}</title>
      <rect x={-RELAY_W / 2} y={-13} width={RELAY_W} height={26} rx={13} />
      <text y={4.5} textAnchor="middle">
        {shown.map((s, i) => (
          <tspan key={s.key}>{i ? ' · ' : ''}{RELAY_SHORT[s.key]} <tspan className={isDone(s.status) ? 'ok' : s.status === 'failed' || s.status === 'stalled' ? 'bad' : ''}>{mark(s.status)}</tspan></tspan>
        ))}
        {more && ' · …'}
        {through && ` · ${clockAt(last, today).slice(-5)}`}
      </text>
    </g>
  )
}

/** A box's badges on its top edge, the most telling two: 第 N 轮, whom an open gate waits on, a decided gate. */
function badgesOf(n: StageNode, board: ReqBoard, wait: WaitWho | null): { text: string; tone: string }[] {
  const out: { text: string; tone: string }[] = []
  if (n.rounds.length > 1) out.push({ text: `第 ${n.rounds.length} 轮`, tone: 'fix' })
  const g = openGate(n)
  if (g?.state === 'queued') out.push({ text: '排队', tone: 'queue' })
  else if (wait) out.push({ text: whoOf(wait, board), tone: wait === 'you' ? 'gate' : 'other' })
  else {
    const ok = [...n.gates].reverse().find((x) => x.state === 'approved')
    if (ok) out.push({ text: `✓ ${whoThen(badgeWho(ok.by), '批了')}`, tone: 'okline' })
    if (n.gates.some((x) => x.state === 'stale')) out.push({ text: '⚠ 文件改过了', tone: 'warn' })
  }
  if (n.status === 'stalled') out.push({ text: '⚠ 卡住', tone: 'warn' })
  return out.slice(0, 2)
}

/** What a box hands in, on its second line: a unit's stage that does not fit says just its unit (its title says the
 * stage); a long file name takes two lines. */
function taskLines(n: StageNode): string[] {
  if (textWidth(n.sub) <= TASK_EM) return [n.sub]
  if (n.unit && textWidth(n.unit) <= TASK_EM) return [n.unit]
  return wrap(n.sub, TASK_EM, 2)
}

/** Under a box at work whose run's session is busy here: ▸ AI 在做 · 4.9 credits (none in a replay). */
function tickerOf(n: StageNode, run: Run | undefined, quiet: boolean): string | null {
  if (quiet || !WORKING.has(n.status) || !run?.sessions.some((s) => s.busy)) return null
  return `▸ AI 在做${run.credits !== null ? ` · ${fmtCredits(run.credits)} credits` : ''}`
}

/** A box's third line: its state's mark, who did it and how long it took (✓ 本机 · 0:28), or what it waits for. */
function stateLine(n: StageNode, run: Run | undefined, board: ReqBoard, wait: WaitWho | null): string {
  if (n.child && n.child.state !== 'merged') return `${n.child.req} · ${CHILD_STATE_NAME[n.child.state]}`
  const who = runShort(run, board)
  if (n.status === 'waiting' || !who) return n.doing || actorMeta(n.status).label
  if (wait) return `${wait === 'you' ? '✋ ' : ''}${who} · ${whoOf(wait, board)}`
  const last = n.rounds[n.rounds.length - 1]
  const checks = last?.outcome.checks ?? []
  if (isDone(n.status)) {
    const took = n.rounds.reduce((t, r) => t + (r.ended !== null ? Math.max(0, r.ended - r.started) : 0), 0)
    return `✓ ${who} · ${checks.length ? `${checks.filter((c) => c.ok).length}/${checks.length}` : fmtHm(took)}`
  }
  if (n.status === 'failed') return `✕ ${who} · ${last ? roundWords(last, n, board) : n.doing}`
  return `${actorMeta(n.status).icon} ${who} · ${n.doing || actorMeta(n.status).label}`
}

function StageBoxView({ n, box, board, run, focus, flash, onNode, reducedMotion, quiet, onHover, held }: {
  n: StageNode; box: StageBox; board: ReqBoard; run: Run | undefined; focus: string | null; flash?: { n: number; tone: string }
  onNode: (id: string) => void; reducedMotion: boolean; quiet: boolean; onHover: (on: boolean) => void; held?: Held
}) {
  const w = NODE_W
  const h = NODE_H
  const working = WORKING.has(n.status)
  const wait = nodeWait(n, run)
  const other = wait !== null && wait !== 'you'
  const g = openGate(n)
  const isFocus = focus === n.id
  const state = wait ? whoOf(wait, board) : actorMeta(n.status).label
  const name = [`${n.title}（${n.sub}）：${state}`, run?.label, n.rounds.length > 1 ? `第 ${n.rounds.length} 轮` : '', n.doing].filter(Boolean).join('，')
  const onKey = (e: KeyboardEvent<SVGGElement>) => {
    if (e.key !== 'Enter' && e.key !== ' ') return
    e.preventDefault()
    onNode(n.id)
  }
  const corner = n.status === 'merged' || n.status === 'failed'
  let bx = w / 2 - 8
  const ticker = tickerOf(n, run, quiet)
  const asked = !!g && g.state === 'pending' && g.at !== null && !quiet
  const task = asked ? [] : taskLines(n)
  // a second line for what it hands in pushes its state down
  const stateY = task.length > 1 ? 30 : 23
  const room = corner ? 7.4 : 8.6
  const full = stateLine(n, run, board, wait)
  // with no room, a ✓ or ✕ the corner shows already is the first thing to go
  const said = textWidth(full) > room && corner && /^[✓✕] /.test(full) ? full.slice(2) : full
  return (
    <g className={`kt-node is-${n.status}${other ? ' is-other' : ''}${n.status === 'merged' ? ' is-final' : ''}${focus ? (isFocus ? ' is-focus' : ' is-dim') : ''}`}
      transform={`translate(${box.x},${box.y})`} data-node={n.id} data-status={n.status} role="button" tabIndex={0} aria-label={name} aria-pressed={isFocus}
      onClick={() => onNode(n.id)} onKeyDown={onKey} onMouseEnter={() => onHover(true)} onMouseLeave={() => onHover(false)}>
      <title>{name}</title>
      {(working || wait === 'you') && <Halo w={w} h={h} reducedMotion={reducedMotion} />}
      <rect className="kt-node-body" x={-w / 2} y={-h / 2} width={w} height={h} rx={16} />
      {n.status === 'stalled' && <rect className="kt-node-hatch" x={-w / 2} y={-h / 2} width={w} height={h} rx={16} fill="url(#kt-hatch)" />}
      {n.status === 'queued' && <rect className="kt-node-ants" x={-w / 2 - 4} y={-h / 2 - 4} width={w + 8} height={h + 8} rx={19} />}
      <circle className="kt-node-avatar" cx={-w / 2 + 33} cy={0} r={22.5} />
      {working && <circle className="kt-node-spin" cx={-w / 2 + 33} cy={0} r={27} />}
      <RoleGlyph role={n.icon} cx={-w / 2 + 33} cy={0} size={24} className="kt-node-emoji" />
      <text className={`kt-node-title${textWidth(n.title) > 4.6 ? ' is-long' : ''}`} x={-w / 2 + 66} y={-15}>
        {clip(n.title, 6.4)}{n.n !== '✓' && <tspan className="kt-node-tag" dx={5}>{n.n}</tspan>}
      </text>
      {asked ? <WaitLine g={g} /> : task.map((l, i) => <text key={i} className="kt-node-task" x={-w / 2 + 66} y={task.length > 1 ? 1 + i * 13 : 4}>{l}</text>)}
      <text className="kt-node-sub" x={-w / 2 + 66} y={stateY}>{clip(said, room)}</text>
      {n.status === 'merged' && (
        <g className="kt-node-check" transform={`translate(${w / 2 - 14},${h / 2 - 14})`}>
          <circle r={9} />
          <path d="M-4,0 L-1.2,3 L4.2,-3.2" />
        </g>
      )}
      {n.status === 'failed' && (
        <g className="kt-node-x" transform={`translate(${w / 2 - 14},${h / 2 - 14})`}>
          <circle r={9} />
          <path d="M-3.5,-3.5 L3.5,3.5 M3.5,-3.5 L-3.5,3.5" />
        </g>
      )}
      {badgesOf(n, board, wait).map((b) => {
        const bw = Math.max(34, Math.round(textWidth(b.text) * 12.4) + 16)
        bx -= bw
        const x = bx
        bx -= 4
        return (
          <g key={b.text} className={`kt-node-badge is-${b.tone}`} transform={`translate(${x + bw / 2},${-h / 2})`}>
            <rect x={-bw / 2} y={-10} width={bw} height={20} rx={10} />
            <text y={4.5} textAnchor="middle">{b.text}</text>
          </g>
        )
      })}
      {ticker && <text className="kt-node-ticker" x={0} y={h / 2 + 18} textAnchor="middle">{ticker}</text>}
      {held && <rect className={`kt-node-held${held.picked ? ` is-${CHOICE_TONE[held.choice]}` : ''}`} x={-w / 2 - 6} y={-h / 2 - 6} width={w + 12} height={h + 12} rx={21}
        data-testid="kt-node-held" />}
      {flash && <rect key={flash.n} className={`kt-node-flash is-${flash.tone}`} x={-w / 2 - 5} y={-h / 2 - 5} width={w + 10} height={h + 10} rx={20} />}
      <rect className="kt-node-focus" x={-w / 2 - 5} y={-h / 2 - 5} width={w + 10} height={h + 10} rx={20} />
    </g>
  )
}

/** An asked gate's line in its box: how long it has waited, ticking, after the MR question that asks it (D-3 · 已等 1
 * 分钟; whom it waits on is the badge's and the line under it). */
function WaitLine({ g }: { g: Gate }) {
  const now = useServerNow()
  const waited = `已等 ${fmtSpan(now - (g.at ?? now))}`
  const full = g.mr?.decision ? `${g.mr.decision} · ${waited}` : waited
  // set in the mono font: a Latin character or a space takes 0.6 em
  const mono = [...full].reduce((em, c) => em + (textWidth(c) === 1 ? 1 : 0.6), 0)
  return (
    <text className={`kt-node-task is-wait${g.who === 'you' ? '' : ' is-other'}`} x={-NODE_W / 2 + 66} y={4}>
      {clip(mono <= 9.5 ? full : waited, TASK_EM)}
    </text>
  )
}

/** Under the box an open gate covers last: a round sent back before (✗ 退回 · D-2), then the one asked now; the open
 * one takes the person to the step (the session, the step strip), as the board's other chips do. */
function GateChips({ n, g, box, board, onAct, onDoc, held }: {
  n: StageNode; g: Gate; box: StageBox; board: ReqBoard; onAct?: (node: string, d: WaitDo) => void
  onDoc?: (node: string, round?: number, file?: string) => void; held?: Held
}) {
  const back = n.rounds.filter((r) => r.outcome.kind === 'sent_back' && r.outcome.gate === g.kind).slice(-2)
  // 看文档: the drawer on the first document the gate approves, at the round it was asked (an INCEPTION ask covers two
  // nodes: the one of that document)
  const doc = g.docs[0]
  const docNode = board.nodes.find((x) => x.rounds.some((r) => r.outputs.some((o) => o.path === doc))) ?? n
  const chips: { text: string; cls: string; tip: string; open: boolean; doc?: boolean }[] = [
    ...back.map((r) => ({ text: `✗ 退回 · ${r.outcome.decision ?? `第 ${r.n} 轮`}`, cls: 'is-back', tip: `第 ${r.n} 轮：${roundWords(r, n, board)}`, open: false })),
    held?.picked
      ? { text: CHOSE[held.choice], cls: `is-picked is-${CHOICE_TONE[held.choice]}`, tip: held.text, open: false }
      : g.state === 'queued'
      ? { text: `排队 · ${g.text ?? '等流水线过了才问'}`, cls: 'is-queued', tip: g.text ?? '', open: false }
      : { text: askWords(g, board), cls: g.who === 'you' ? 'is-mine' : 'is-other', tip: g.line ?? '', open: true },
    ...(onDoc && doc ? [{ text: '看文档', cls: 'is-doc', tip: `看要批的 ${doc.slice(doc.lastIndexOf('/') + 1)}`, open: false, doc: true }] : []),
  ]
  const widths = chips.map((c) => Math.max(84, Math.round(textWidth(c.text) * 12.6) + 20))
  let x = box.x - (widths.reduce((s, v) => s + v, 0) + 6 * (chips.length - 1)) / 2
  const y = box.y + NODE_H / 2 + 22
  return (
    <g className="kt-gate-row" data-testid="kt-gate-row" data-node={n.id}>
      {chips.map((c, i) => {
        const at = x + widths[i] / 2
        x += widths[i] + 6
        const act = c.doc && onDoc ? () => onDoc(docNode.id, docNode === n ? g.round : undefined, doc)
          : c.open && g.state === 'pending' && onAct ? () => onAct(n.id, gateDo(g)) : undefined
        return (
          <g key={c.text} className={`kt-gate-pill ${c.cls}${act ? '' : ' is-static'}`} transform={`translate(${at},${y})`} role={act ? 'button' : undefined}
            tabIndex={act ? 0 : undefined} aria-label={act ? `${n.title}：${c.text}` : undefined} onClick={act}
            onKeyDown={act ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); act() } } : undefined} data-testid="kt-gate-pill">
            {c.tip && <title>{c.tip}</title>}
            <rect x={-widths[i] / 2} y={-13} width={widths[i]} height={26} rx={13} />
            <text y={5} textAnchor="middle">{c.text}</text>
          </g>
        )
      })}
    </g>
  )
}

/** Under the box a replay holds on where no gate is open (a question): 等人回答, then what the person chose. */
function HeldChip({ h, box }: { h: Held; box: StageBox }) {
  const text = h.picked ? CHOSE[h.choice] : h.choice === 'answer' ? '等人回答' : '等人拍板'
  const w = Math.max(84, Math.round(textWidth(text) * 12.6) + 20)
  return (
    <g className={`kt-gate-pill is-static ${h.picked ? `is-picked is-${CHOICE_TONE[h.choice]}` : 'is-other'}`} transform={`translate(${box.x},${box.y + NODE_H / 2 + 22})`}
      data-testid="kt-held-pill">
      <title>{h.text}</title>
      <rect x={-w / 2} y={-13} width={w} height={26} rx={13} />
      <text y={5} textAnchor="middle">{text}</text>
    </g>
  )
}

/** Under the box a run waits on for the person looking, with no gate open there: the step, named as everywhere (vocab
 * DO_NAME; a reply that is Lean's review in a new session is 开审查会话, as the step strip says); it takes them there. */
function StepChip({ n, box, d, review, onAct }: { n: StageNode; box: StageBox; d: WaitDo; review: boolean; onAct?: (node: string, d: WaitDo) => void }) {
  const text = `✋ ${review && d === 'reply' ? ACT.review : DO_NAME[d] || DO_NAME.answer}`
  const go = onAct ? () => onAct(n.id, d) : undefined
  const w = Math.max(96, Math.round(textWidth(text) * 12.6) + 20)
  return (
    <g className={`kt-gate-pill is-mine${go ? '' : ' is-static'}`} transform={`translate(${box.x},${box.y + NODE_H / 2 + 22})`} role={go ? 'button' : undefined}
      tabIndex={go ? 0 : undefined} aria-label={`${n.title}：${text.slice(2)}`} onClick={go}
      onKeyDown={go ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go() } } : undefined} data-testid="kt-step-pill">
      <rect x={-w / 2} y={-13} width={w} height={26} rx={13} />
      <text y={5} textAnchor="middle">{text}</text>
    </g>
  )
}

/** A box's hover card: what it is, its rounds (第 1 轮 · 运行机 · 13:25 · 产品退回（D-2）), the line to type for a gate
 * asked here; 打开会话 when its run's session is on this machine, 看 MR for a gate asked on the MR (neither in a replay). */
function StageCard({ n, board, runs, onSession, onOutputs }: {
  n: StageNode; board: ReqBoard; runs: Map<string, Run>; onSession?: (url: string) => void
  onOutputs?: (node: string, round?: number, file?: string) => void
}) {
  const run = runs.get(n.run ?? '')
  const wait = nodeWait(n, run)
  const g = openGate(n)
  const meta = actorMeta(n.status)
  const session = run && run.readable === 'here' ? sessionOf(run) : undefined
  return (
    <div className="kt-ncard-body">
      <p className="kt-ncard-head">
        <RoleIcon role={n.icon} size={16} /> <b>{n.title}{n.unit ? ` · ${n.unit}` : ''}{n.rounds.length > 1 ? ` · ${n.rounds.length} 轮` : ''}</b>
        <span className={`kt-pill tone-${wait ? (wait === 'you' ? 'approve' : 'info') : meta.tone}`}>{wait ? whoOf(wait, board) : meta.label}</span>
      </p>
      <p className="kt-ncard-task"><span className="kt-dim">交什么</span> {n.sub}</p>
      {n.doing && <p className="kt-ncard-did">{n.doing}</p>}
      {n.rounds.length > 0 && (
        <ol className="kt-ncard-rounds">
          {n.rounds.map((r) => {
            const words = [runShort(runs.get(r.run), board), clockAt(r.started, board.at), roundWords(r, n, board)].filter(Boolean).join(' · ')
            return (
              <li key={r.n} className={`tone-${ROUND_TONE[r.outcome.kind]}`}>
                {onOutputs ? (
                  <button type="button" className="kt-ncard-round" onClick={() => onOutputs(n.id, r.n)} title={`看第 ${r.n} 轮交了什么`}>
                    <b>第 {r.n} 轮</b><span>{words}</span>
                  </button>
                ) : <><b>第 {r.n} 轮</b><span>{words}</span></>}
              </li>
            )
          })}
        </ol>
      )}
      {g?.line && <p className="kt-ncard-facts">要你自己在会话里打：<code>{g.line}</code></p>}
      {((onSession && (session || g?.mr?.url)) || (onOutputs && n.rounds.length > 0)) && (
        <div className="kt-ncard-actions">
          {onOutputs && n.rounds.length > 0 && <button type="button" className="kt-btn" onClick={() => onOutputs(n.id)} data-testid="kt-ncard-out">看产出 ›</button>}
          {onSession && session && <button type="button" className="kt-btn" onClick={() => onSession(session.url)}>{ACT.openSession}</button>}
          {onSession && g?.mr?.url && <ExtLink href={g.mr.url} className="kt-btn">{ACT.viewMr} ↗</ExtLink>}
        </div>
      )}
    </div>
  )
}

const LEGEND: { status: string; text: string }[] = [
  { status: 'running', text: '在做' },
  { status: 'fixing', text: '返工' },
  { status: 'queued', text: '排队' },
  { status: 'awaiting_approval', text: '等你' },
  { status: 'other', text: '等别人' },
  { status: 'done', text: '做完这步' },
  { status: 'merged', text: '完成' },
  { status: 'failed', text: '没过' },
  { status: 'waiting', text: '还没轮到' },
]

/** *others*: the waits on someone else this board shows, in their words (等产品); *relay*: it has a hand-over. */
function Legend({ others, relay }: { others: string[]; relay: boolean }) {
  return (
    <div className="kt-legend" role="note" aria-label="图例">
      {LEGEND.map((l) => (
        <span key={l.status} className={`kt-legend-item is-${l.status}`} data-testid={l.status === 'other' ? 'kt-legend-other' : undefined}>
          <i aria-hidden="true" />
          {l.status === 'other' && others.length ? others.join(' / ') : l.text}
        </span>
      ))}
      <span className="kt-legend-sep" aria-hidden="true" />
      <span className="kt-legend-item"><span className="kt-legend-round" aria-hidden="true">第 2 轮</span>又做了一轮</span>
      {relay && <span className="kt-legend-item is-p-handoff"><b aria-hidden="true" />交接 · 签名</span>}
      <span className="kt-legend-item is-p-merge"><b aria-hidden="true" />批准 · 检查全过</span>
      <span className="kt-legend-item is-p-fail"><b aria-hidden="true" />退回 · 没过</span>
      <span className="kt-legend-sep" aria-hidden="true" />
      <span className="kt-legend-item kt-dim">阶段框里跳过的不画，写在每段最下面</span>
    </div>
  )
}
