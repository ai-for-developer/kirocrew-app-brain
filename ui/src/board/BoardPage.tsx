/**
 * 需求看板: one requirement as the sample's 团队看板 shows a run (its main.tsx TeamPage, for a requirement's way).
 *
 * Polls GET /board/{repo}/{key} every 2 s (useBoard.ts) and renders only that contract (types.ts ReqBoard): the
 * header, 你要做的一步 (a narrow strip: what the person does now, and where), the live counters, the stage graph
 * (StageGraph.tsx, the hero: a box per stage a run did, in the AI-DLC phase bands, with the particle layer), the
 * commit conveyor, one lane per stage a run did on a shared time axis, the check wall, the events, and 验收 (on top once it is
 * time to accept, else under the lanes). Each poll's
 * snapshot is diffed against the previous one (diff.ts): new events become particles and box flashes, changed check
 * cells flip, changed lanes and new commits get a one-time highlight. The first snapshot is history and never animates.
 *
 * Click a box, a lane, a commit, a cell, a stage's dot or an event's chip to look at that stage only, everywhere on the
 * page (what has no stage looks at nothing); 跟随 scrolls to the stage that moved last; Esc lets go. 看产出 (a box's
 * card, a lane, a gate's 看文档, the top bar's 产出) opens the 产出 drawer (Outputs.tsx) on that stage: its place is in
 * the address (?out=), opened, moved and closed in place of the current history entry; Esc closes it first. 运行：全部 ▾
 * (RunPicker.tsx) picks one run: the boxes it did no round of dim, and the counters, the conveyor, the lanes, the wall,
 * the events and the drawer show its flow only (picker.ts inRun); the pick is in the address (?run=) and is a history
 * entry, so 后退 returns to the run before. 回放
 * (Replay.tsx) plays the requirement again from the same snapshot (timeline.ts), holding on each decision a person
 * made; while it plays nothing can be done, and the drawer shows what each step had at the replayed moment.
 * Reduced motion: nothing flies or pulses, states still change.
 *
 * A big requirement split into small ones (row.family, views/Family.tsx): a small one names its big one in the crumb
 * and the top bar; a big one's small ones are lanes with the roll-up and the trace's family numbers. Either family card
 * (a small one's: its dependencies, paths, 认领 / 开工 / 同步) sits under the conveyor, before the requirement's own
 * lanes, so the graph stays the hero; a big one not split yet says what comes next under the counters. Before a small one is started the board
 * introduces its way. The family's answers live here (useFamily), so a card that moves keeps them.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useAppApi, useNavigate } from '@kirocrew/app-sdk'
import { VIEW_NAME, parseView, type BoardEntry, type OutAt, type View } from '../nav'
import type { ReqBoard, WaitDo } from '../types'
import { LoadState } from '../ui'
import { FamilyPanel, useFamily } from '../views/Family'
import { QuestionCard } from '../views/Question'
import type { ViewProps } from '../views/props'
import { NotFound, OnRunner, ReqSwitch, useOnRunner } from '../views/ReqSwitch'
import { Accept } from './Accept'
import type { FlashMap } from './GraphFrame'
import { CheckWall } from './CheckWall'
import { Conveyor } from './Conveyor'
import { effects, type BoardDiff, type Flash } from './diff'
import type { Item } from './engine'
import { EventFeed } from './EventFeed'
import { BOARD_EXTRA_CSS } from './extra'
import { PERSON, WORKING, boardWho, sessionOf } from './format'
import { NextStep } from './NextStep'
import { OutputsDrawer } from './Outputs'
import { openHere, reviewNext } from './next'
import { Intro, Kpis, TopBar } from './Header'
import { ClockContext, useReducedMotion, type SnapshotClock } from './hooks'
import { Lanes, laneNodes } from './Lanes'
import { stageKey, stageLayout, stageShape } from './layout'
import { inRun, pickedRun, type RunFilter } from './picker'
import { ReplayBar, useReplay } from './Replay'
import { RoleIcon } from './RoleIcon'
import { StageGraph, stageInput } from './StageGraph'
import { useBoard } from './useBoard'

interface Effects {
  req: string | null
  flips: Record<string, number>
  flashes: FlashMap
  bumps: Record<string, number>
  belt: ReadonlySet<string>
  fresh: ReadonlySet<number>
  announce: string
}
const NO_EFFECTS: Effects = { req: null, flips: {}, flashes: {}, bumps: {}, belt: new Set(), fresh: new Set(), announce: '' }

/** Fold one poll's diff and its flashes on the graph into the one-shot effects (each counter is a key that replays an
 * animation; a lane's bump is its node's). */
export function applyDiff(f: Effects, d: BoardDiff, flashed: Flash[]): Effects {
  if (d.first || f.req !== d.req) return { ...NO_EFFECTS, req: d.req }
  const flips = { ...f.flips }
  for (const x of d.flips) flips[x.id] = (flips[x.id] ?? 0) + 1
  const flashes = { ...f.flashes }
  for (const x of flashed) flashes[x.target] = { n: (flashes[x.target]?.n ?? 0) + 1, tone: x.tone }
  const bumps = { ...f.bumps }
  for (const x of d.nodes) bumps[x.id] = (bumps[x.id] ?? 0) + 1
  return { req: d.req, flips, flashes, bumps, belt: new Set(d.belt), fresh: new Set(d.fresh.map((e) => e.seq)), announce: d.announce ?? f.announce }
}

/** The stage 跟随 looks at: the one at work or waiting whose state began last. */
export function mostActive(b: ReqBoard): string | null {
  const live = b.nodes.filter((n) => WORKING.has(n.status) || n.status === 'awaiting_approval')
  live.sort((x, y) => (y.since ?? 0) - (x.since ?? 0))
  return live[0]?.id ?? null
}

/** The stage the top bar's 产出 opens on: the one at work or waiting, else the last one a run did. */
export function firstOutputs(b: ReqBoard): string {
  const lanes = laneNodes(b)
  const live = mostActive(b)
  return lanes.find((n) => n.id === live)?.id ?? lanes[lanes.length - 1]?.id ?? b.nodes[0].id
}

/** The stage looked at, for the crumb and the focus bar. */
function focusOf(b: ReqBoard, focus: string | null): { icon: string; title: string } | null {
  const n = b.nodes.find((x) => x.id === focus)
  return n ? { icon: n.icon, title: n.unit ? `${n.title} · ${n.unit}` : n.title } : null
}

/** An error the board came with, said so a person can act on it (brain-kit's own words folded under 细节). A claimed
 * requirement has no run yet: nothing to read is no error. */
export function boardProblem(b: ReqBoard): { text: string; todo: string; raw: string } | null {
  if (!b.error || b.row.state === 'claimed') return null
  if (!/[A-Za-z]{4,}/.test(b.error.replace(/brain-kit|Lean|KiroCrew/g, ''))) return { text: b.error, todo: '', raw: '' }
  return { text: '这个需求的运行记录没读全：图上只画了 Lean 的记录和仓库里看得到的', todo: '到需求详情看细节；一直这样的话，到设置里看 brain-kit 能不能用', raw: b.error }
}

export function BoardPage({ entry, onBack, onSwitch, onMissing, go, replace, push, status, pollMs, reloadStatus }: {
  entry: BoardEntry
  onBack?: () => void
  /** a step inside the board a person goes back from (a run picked): a history entry, the board not opened again
   * (default: in place of the current one) */
  push?: (v: View) => void
  /** 切换需求: open another requirement's board here (default: a move to it) */
  onSwitch?: (repo: string, key: string) => void
  /** told whether the repo has this requirement (the app bar's other tab cannot open one it does not have) */
  onMissing?: (missing: boolean) => void
} & ViewProps) {
  const api = useAppApi()
  const navigate = useNavigate()
  const reducedMotion = useReducedMotion()
  const [focus, setFocus] = useState<string | null>(null)
  const [follow, setFollow] = useState(false)
  const [fx, setFx] = useState<Effects>(NO_EFFECTS)
  const feed = useRef<((items: Item[]) => void) | null>(null)
  const rootRef = useRef<HTMLElement | null>(null)
  const replaying = useRef(false)
  const boxes = useRef<ReadonlySet<string>>(new Set())
  const animate = useCallback((d: BoardDiff, b: ReqBoard) => {
    const on = effects(d, b, boxes.current)
    if (on.items.length) feed.current?.(on.items)
    setFx((f) => applyDiff(f, d, on.flashes))
  }, [])
  const onDiff = useCallback((d: BoardDiff, b: ReqBoard) => void (replaying.current || animate(d, b)), [animate])
  const live = useBoard(api, { repo: entry.repo, reqKey: entry.key, pollMs, onDiff })
  const gone = live.missing && !live.board
  const onRunner = useOnRunner(entry.repo, entry.key, status, pollMs)
  const pick = onSwitch ?? ((repo: string, key: string) => go({ kind: 'board', repo, key }))
  useEffect(() => {
    if (gone || live.board) onMissing?.(gone)
  }, [gone, !!live.board]) // eslint-disable-line react-hooks/exhaustive-deps
  const family = useFamily(entry.repo, entry.key, () => {
    live.reload()
    reloadStatus()
  })
  const rp = useReplay(live.board, animate)
  replaying.current = rp.on
  const board = rp.board ?? live.board
  useEffect(() => setFx(NO_EFFECTS), [rp.on])
  // the address says whether it replays (a reload, a copied link open the same): rewritten, not a new entry; the
  // moment it was opened at goes with the replay
  const said = useRef(!!entry.replay || !!entry.at)
  const entered = useRef(false)
  // the 产出 drawer's place and the run picked: the view's own, written to the address (replace only rewrites it, it
  // opens nothing)
  const [out, setOut] = useState<OutAt | null>(entry.out ?? null)
  const outRef = useRef(out)
  outRef.current = out
  const [run, setRun] = useState<RunFilter>(entry.run ?? 'all')
  const runRef = useRef(run)
  runRef.current = run
  const here = useCallback((): View => {
    const { replay: _r, at: _a, out: _o, run: _u, ...rest } = entry
    return { kind: 'board', ...rest, ...(said.current ? { replay: true } : {}), ...(runRef.current !== 'all' ? { run: runRef.current } : {}),
      ...(outRef.current ? { out: outRef.current } : {}) }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const address = useCallback(() => replace(here()), [here]) // eslint-disable-line react-hooks/exhaustive-deps
  const onRun = useCallback((r: RunFilter) => {
    runRef.current = r
    setRun(r)
    ;(push ?? replace)(here())
  }, [here]) // eslint-disable-line react-hooks/exhaustive-deps
  // 后退 / 前进 between this board's entries (a run picked): the entry says the run and the drawer
  useEffect(() => {
    const onPop = () => {
      const v = parseView(window.location.search)
      if (v.kind !== 'board' || v.repo !== entry.repo || v.key !== entry.key) return
      runRef.current = v.run ?? 'all'
      outRef.current = v.out ?? null
      setRun(runRef.current)
      setOut(outRef.current)
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  // a link to a run this board does not have shows the whole way
  const picked: RunFilter = board && pickedRun(board, run) ? run : 'all'
  const view = useMemo(() => (board ? inRun(board, picked) : null), [board, picked])
  useEffect(() => {
    if (!entered.current || rp.on === said.current) return
    said.current = rp.on
    address()
  }, [rp.on]) // eslint-disable-line react-hooks/exhaustive-deps

  // the way in: ?replay=1 starts the replay once the board is here, ?at= stops it at that moment; ?focus= scrolls to
  // that part
  useEffect(() => {
    if (entered.current || !live.board) return
    entered.current = true
    if (entry.at) rp.start(entry.at)
    else if (entry.replay) rp.start()
    const where = entry.focus === 'accept' ? '#br-accept' : entry.focus === 'checks' ? '[data-testid="kt-wall"]' : entry.focus === 'lanes' ? '[data-testid="kt-lanes"]'
      : entry.focus === 'feed' ? '[data-testid="kt-feed"]' : entry.replay || entry.at ? '[data-testid="kt-graph"]' : ''
    if (where) setTimeout(() => rootRef.current?.querySelector(where)?.scrollIntoView?.({ behavior: 'smooth', block: 'start' }), 80)
  }, [live.board]) // eslint-disable-line react-hooks/exhaustive-deps

  // the live board's shape: a replay moves through the same picture, and a poll moves nothing unless what the layout
  // reads changed; the graph's width is the app's (its text keeps its size: layout.ts stageShape, the compact layout
  // when the full one does not fit)
  const input = useMemo(() => (live.board ? stageInput(live.board) : null), [live.board])
  const structure = input ? stageKey(input) : ''
  const [graphW, setGraphW] = useState<number | null>(null)
  const watch = useRef<ResizeObserver | null>(null)
  const graphBox = useCallback((el: HTMLDivElement | null) => {
    watch.current?.disconnect()
    if (!el || typeof ResizeObserver === 'undefined') return
    const read = () => {
      // the graph card's own inner width (its border and a scrollbar left out)
      const w = (el.firstElementChild as HTMLElement | null)?.clientWidth || el.clientWidth
      if (w > 0) setGraphW((old) => (old !== null && Math.abs(old - w) < 8 ? old : w))
    }
    read()
    watch.current = new ResizeObserver(read)
    watch.current.observe(el)
  }, [])
  useEffect(() => () => watch.current?.disconnect(), [])
  const lay = useMemo(() => (input ? stageLayout(input, stageShape(graphW, input)) : null), [structure, graphW]) // eslint-disable-line react-hooks/exhaustive-deps
  boxes.current = useMemo(() => new Set(lay?.boxes.keys() ?? []), [lay])
  const liveClock: SnapshotClock = useMemo(() => ({ at: live.board?.at ?? Date.now() / 1000, receivedAt: live.receivedAt || Date.now() }), [live.board?.at, live.receivedAt])
  const clock = rp.clock ?? liveClock

  const onNode = useCallback((id: string) => setFocus((f) => (f === id ? null : id)), [])
  // opened, moved and closed in place of the current history entry (后退 leaves the board, not the drawer)
  const toOutputs = useCallback((at: OutAt | null) => {
    outRef.current = at
    setOut(at)
    address()
  }, [address])
  const onOutputs = useCallback((node: string, round?: number, file?: string) =>
    toOutputs({ node, ...(round ? { round } : {}), ...(file ? { file } : {}) }), [toOutputs])
  const followKey = follow && board ? mostActive(board) : null
  useEffect(() => {
    if (!followKey) return
    rootRef.current?.querySelector(`[data-testid="kt-lane"][data-node="${followKey}"]`)?.scrollIntoView?.({ block: 'nearest', behavior: reducedMotion ? 'auto' : 'smooth' })
  }, [followKey, reducedMotion])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      if (outRef.current) toOutputs(null)
      else setFocus(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [toOutputs])
  const toDetail = () => go({ kind: 'item', repo: entry.repo, key: entry.key, from: 'board' })
  // the crumb's first step and ← 返回 name the view it was opened from (the way back)
  const fromName = VIEW_NAME[entry.from ?? 'overview']
  const toBrain = () => go({ kind: 'brain', repo: entry.repo, reflect: entry.key })
  // 开工 the requirement claimed already: 开需求 with its key (fixed there) and title; only its owner starts it: the one
  // the board calls 你 (an older backend did not say: the owner, or anyone while it is not known who is looking)
  const me = status?.repos.find((r) => r.id === entry.repo)?.me?.name ?? null
  const startable = !!board && entry.repo !== 'runner' && (board.person.you ?? (board.person.me || !board.person.owner || !me))
  const toStart = () => go({ kind: 'new', repo: entry.repo, key: entry.key, ...(board?.row.title ? { title: board.row.title } : {}) })
  const toBoard = (key: string) => go({ kind: 'board', repo: entry.repo, key })
  // a box that waits for the person: its run's session for what is said or approved there, the step strip for the rest
  // (a review is not said in the session that wrote the code: the strip has 开审查会话)
  const onAct = useCallback((id: string, d: WaitDo) => {
    const runs = board?.runs ?? []
    const run = runs.find((r) => r.key === board?.nodes.find((n) => n.id === id)?.run)
    const url = (sessionOf(run) ?? sessionOf(runs.find((r) => r.kind === 'local')))?.url
    const review = !!board && reviewNext(board.row.waits)
    if (((d === 'reply' && !review) || d === 'tool') && url) return navigate(url)
    if (d === 'write_back') return toBrain()
    rootRef.current?.querySelector(d === 'accept' && !rootRef.current?.querySelector('#br-next') ? '#br-accept' : '#br-next')?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
    setFocus(id)
  }, [board, navigate]) // eslint-disable-line react-hooks/exhaustive-deps
  const quiet = rp.on
  const looked = board ? focusOf(board, focus) : null
  const side = board ? board.questions.filter((q) => (q.state === 'waiting' || q.state === 'open') && !(board.row.waits.who === 'you' && openHere(q))) : []
  // whom those wait on when it is not the person looking (the one rule, as the strip says it)
  const waiter = !board || board.row.waits.who === 'you' ? null : PERSON.has(board.row.waits.who) ? boardWho(board) : '等人' 
  const acceptTop = !!board && board.accept.state !== 'not_yet'
  const problem = board ? boardProblem(board) : null
  const fam = board?.row.family ?? null
  // a small one is started from its family card (brain start --key), not from 开需求
  const onStart = startable && fam?.role !== 'child' ? toStart : undefined
  const notStarted = !!board && (board.row.state === 'claimed' || board.row.state === 'unclaimed') && !rp.on
  const famCard = board && fam && (
    <FamilyPanel f={fam} repo={entry.repo} reqKey={entry.key} ctl={family} look="board" go={go} rollup={board.rollup} quiet={quiet} />
  )
  const toFamily = () => rootRef.current?.querySelector('[data-testid="fam-panel"]')?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })

  return (
    <main ref={rootRef} className={`kt-root br-view br-board${board?.live && !quiet ? ' is-live' : ''}`} data-testid="br-board" data-motion={reducedMotion ? 'reduce' : undefined}>
      <style>{BOARD_EXTRA_CSS}</style>
      <ClockContext.Provider value={clock}>
        <div className="cto-top br-where-row">
          <nav className="kit-crumb kt-where" aria-label="位置" data-testid="kt-where">
            <button type="button" onClick={onBack ?? (() => go({ kind: 'overview' }))}>{fromName}</button>
            <span aria-hidden="true">›</span>
            {fam?.role === 'child' && (
              <>
                <button type="button" onClick={() => toBoard(fam.parent)} title={`大需求 ${fam.parent} 的需求看板`}>{fam.parent}</button>
                <span aria-hidden="true">›</span>
              </>
            )}
            {/* a key the repo does not have has no 需求详情 to go to: said, not linked */}
            {gone ? <span data-testid="kt-where-key">{entry.key}</span> : <button type="button" onClick={toDetail} title="需求详情">{entry.key}</button>}
            <span aria-hidden="true">›</span>
            <b>需求看板{looked ? ` · ${looked.title}` : ''}{rp.on ? ' · 回放' : ''}</b>
          </nav>
          <span className="cto-grow" />
          {!rp.on && <ReqSwitch repo={entry.repo} reqKey={entry.key} status={status} pollMs={pollMs} onPick={pick} />}
          {onBack && <button type="button" className="kt-btn" onClick={onBack}>← 返回{fromName}</button>}
        </div>
        {onRunner && !rp.on && <OnRunner reqKey={entry.key} what="需求看板" onOpen={() => pick('runner', entry.key)} />}
        {live.missing && !board
          ? !onRunner && <NotFound repoName={status?.repos.find((r) => r.id === entry.repo)?.name ?? (entry.repo === 'runner' ? '运行机' : null)} reqKey={entry.key}
              backName={onBack ? fromName : VIEW_NAME.overview} onBack={onBack ?? (() => go({ kind: 'overview', ...(entry.repo !== 'runner' ? { repo: entry.repo } : {}) }))}
              testId="br-board-missing" />
          : <LoadState loading={live.loading} error={live.error} has={!!board} />}
        {board && view && lay && (
          <>
            <TopBar board={board} run={picked} onRun={onRun} replaying={rp.on} follow={follow} onReplay={() => rp.start()} onFollow={() => setFollow((v) => !v)} onDetail={toDetail}
              onSession={(url) => navigate(url)} onStart={onStart} onParent={toBoard} onFamily={toFamily}
              onOutputs={!laneNodes(view).length ? undefined : () => onOutputs(firstOutputs(view))} />
            <ReplayBar rp={rp} />
            {problem && (
              <div className="kt-banner br-problem" role="status" data-testid="br-board-problem">
                <b>{problem.text}</b>
                {problem.todo && <span>做什么：{problem.todo}</span>}
                {problem.raw && <details className="br-raw"><summary>细节</summary><pre>{problem.raw}</pre></details>}
              </div>
            )}
            {notStarted ? (
              <>
                {famCard}
                <Intro board={board} onStart={onStart} />
              </>
            ) : (
              <>
                {!quiet && <NextStep board={board} repo={entry.repo} reqKey={entry.key} onAnswered={live.reload} onBrain={toBrain} onDetail={toDetail} />}
                <Kpis key={`${rp.on ? 'replay' : 'live'}`} board={view} run={picked} reducedMotion={reducedMotion} onNode={onNode} replaying={rp.on} />
                {acceptTop && <Accept board={board} onDetail={toDetail} quiet={quiet} />}
                {fam?.role === 'big' && famCard}
                <div className="kt-body">
                  <div className="kt-col">
                    <div ref={graphBox}>
                      <StageGraph key={`${entry.repo}/${entry.key}${rp.on ? ':replay' : ''}`} board={board} run={picked} lay={lay} focus={focus} onNode={onNode} flashes={fx.flashes}
                        reducedMotion={reducedMotion} feed={feed} quiet={quiet} onSession={(url) => navigate(url)} onAct={quiet ? undefined : onAct}
                        onOutputs={onOutputs} held={rp.held} />
                    </div>
                    {focus && <FocusBar board={board} focus={focus} onClear={() => setFocus(null)} />}
                    <Conveyor board={view} fresh={fx.belt} focus={focus} onNode={onNode} />
                    {(fam?.role === 'parent' || fam?.role === 'child') && famCard}
                    <Lanes board={view} focus={focus} bumps={fx.bumps} onNode={onNode} quiet={quiet} onOutputs={onOutputs} />
                    {!acceptTop && <Accept board={board} onDetail={toDetail} quiet={quiet} />}
                  </div>
                  <aside className="kt-side" aria-label="检查、问题和事件">
                    <CheckWall board={view} focus={focus} onNode={onNode} flips={fx.flips} onSettings={() => go({ kind: 'settings' })} />
                    {!quiet && side.length > 0 && (
                      <section className="kt-card br-board-qs" aria-label="等回答的问题" data-testid="br-board-questions">
                        <header className="kt-card-head"><h2>等回答的问题</h2><span className={`kt-badge tone-${board.row.waits.who === 'you' ? 'approve' : 'info'}`}>{side.length}</span></header>
                        {side.map((q, i) => <QuestionCard key={`${q.id ?? 'q'}-${i}`} q={q} repo={entry.repo} reqKey={entry.key} onAnswered={live.reload} waiter={waiter} />)}
                      </section>
                    )}
                    <EventFeed board={view} focus={focus} onNode={onNode} fresh={fx.fresh} />
                  </aside>
                </div>
              </>
            )}
          </>
        )}
        {/* a way in that replays opens the drawer at its moment, never first at the live board */}
        {view && out && (entered.current || !(entry.at || entry.replay)) && (
          <OutputsDrawer board={view} repo={entry.repo} reqKey={entry.key} at={out} onAt={toOutputs} onClose={() => toOutputs(null)}
            moment={rp.moment} bar={rp.on && <ReplayBar rp={rp} />} />
        )}
        <div className="kt-sr" aria-live="polite" data-testid="kt-announce">{fx.announce}</div>
      </ClockContext.Provider>
    </main>
  )
}

function FocusBar({ board, focus, onClear }: { board: ReqBoard; focus: string; onClear: () => void }) {
  const a = focusOf(board, focus)
  if (!a) return null
  return (
    <div className="kt-focuschip" role="status" data-testid="kt-focusbar">
      <RoleIcon role={a.icon} size={14} /> {a.title}
      <button type="button" onClick={onClear} aria-label="看全部" title="看全部（Esc）">✕</button>
    </div>
  )
}
