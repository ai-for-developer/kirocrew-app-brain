/**
 * The frame every graph of the 需求看板 is drawn in: the scroller that keeps it at its scale (layout.ts SCALE), the
 * SVG's dotted background, hatching and arrow heads, the halo of a node at work or waiting for the person looking (one
 * shared beat), the check ring, the particle layer, a hover card's delay, and the owner's card.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject, type ReactNode } from 'react'
import type { ReqBoard } from '../types'
import { Engine, type Item } from './engine'
import { fmtWhen } from './format'
import { useLatest } from './hooks'
import type { Path, Pt } from './layout'
import { Overlay, type Box } from './overlay'
import { RoleIcon } from './RoleIcon'

/** A one-shot flash per anchor (a node, 'orch', 'run', 'tests'): the counter replays its animation. */
export type FlashMap = Record<string, { n: number; tone: string }>

const PULSE_MS = 2400
const beat = () => `-${Math.round(performance.now() % PULSE_MS)}ms`

/** The animation delay that puts a newly drawn pulse on the shared beat (none under reduced motion). */
export function useBeat(reducedMotion: boolean) {
  const [delay] = useState(beat)
  return reducedMotion ? undefined : { animationDelay: delay }
}

export function Halo({ w, h, reducedMotion }: { w: number; h: number; reducedMotion: boolean }) {
  const style = useBeat(reducedMotion)
  return <rect className="kt-node-halo" x={-w / 2 - 8} y={-h / 2 - 8} width={w + 16} height={h + 16} rx={23} style={style} />
}

/** The graph at its size (layout.ts SCALE): when the app is narrower, it scrolls sideways, and the edge that hides
 * more of it says so (a fade, and 「右边还有」 that scrolls there): a hidden scrollbar shows nothing. */
export function Scroller({ minWidth, children }: { minWidth: number; children: ReactNode }) {
  const ref = useRef<HTMLDivElement | null>(null)
  const [more, setMore] = useState({ left: false, right: false })
  const read = useCallback(() => {
    const el = ref.current
    if (!el) return
    const next = { left: el.scrollLeft > 4, right: el.scrollLeft + el.clientWidth < el.scrollWidth - 4 }
    setMore((m) => (m.left === next.left && m.right === next.right ? m : next))
  }, [])
  useEffect(() => {
    read()
    const el = ref.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(read)
    ro.observe(el)
    return () => ro.disconnect()
  }, [read, minWidth])
  const by = (dir: number) => ref.current?.scrollBy?.({ left: dir * ref.current.clientWidth * 0.6, behavior: 'smooth' })
  return (
    <div className={`br-graph-view${more.left ? ' has-left' : ''}${more.right ? ' has-right' : ''}`}>
      <div className="br-graph-scroll" ref={ref} onScroll={read} data-testid="kt-graph-scroll">
        <div className="kt-graph-stage" style={{ minWidth }}>{children}</div>
      </div>
      {more.left && <button type="button" className="br-graph-more is-left" onClick={() => by(-1)}>← 左边还有</button>}
      {more.right && <button type="button" className="br-graph-more is-right" onClick={() => by(1)} data-testid="kt-graph-more">右边还有 →</button>}
    </div>
  )
}

export function GraphDefs() {
  return (
    <defs>
      <pattern id="kt-dots" width={22} height={22} patternUnits="userSpaceOnUse">
        <circle className="kt-g-dot" cx={2} cy={2} r={1} />
      </pattern>
      <pattern id="kt-hatch" width={9} height={9} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
        <rect className="kt-hatch-line" x={0} y={0} width={3.5} height={9} />
      </pattern>
      {(['pending', 'ready', 'used'] as const).map((s) => (
        <marker key={s} id={`kt-arrow-${s}`} viewBox="0 0 10 10" refX={8.5} refY={5} markerWidth={7} markerHeight={7} orient="auto-start-reverse">
          <path className={`kt-arrow is-${s}`} d="M0,0.8 L9,5 L0,9.2 Z" />
        </marker>
      ))}
    </defs>
  )
}

/** The person's card: the whole sentence their node can only start (UI问题清单 BOARD-10). */
export function PersonCard({ board, mine, who }: { board: ReqBoard; mine: boolean; who: string }) {
  const p = board.person
  const w = board.row.waits
  return (
    <div className="kt-ncard-body">
      <p className="kt-ncard-head">
        <RoleIcon role="person" size={16} /> <b>{p.me ? '你' : '负责人'}{p.owner ? `（${p.owner}）` : ''}</b>
        {p.status === 'awaiting_approval' && <span className={`kt-pill tone-${mine ? 'approve' : 'info'}`}>{mine ? '等你' : who}</span>}
      </p>
      <p className={mine && p.status === 'awaiting_approval' ? 'kt-ncard-back' : 'kt-ncard-did'}>{p.doing}</p>
      {w.since ? <ul className="kt-ncard-facts"><li>从 {fmtWhen(w.since)} 起</li></ul> : null}
    </div>
  )
}

/** The check ring at the rail's end (the sample's test station): the check wall's passed / total. */
export function Station({ board, at, flash }: { board: ReqBoard; at: Pt & { r: number }; flash?: { n: number; tone: string } }) {
  const { passed, failed, total } = board.checks
  const r = at.r - 5
  const c = 2 * Math.PI * r
  const pass = total ? (passed / total) * c : 0
  const fail = total ? (failed / total) * c : 0
  const tone = !total ? 'none' : failed ? 'fail' : passed === total ? 'pass' : 'part'
  return (
    <g className={`kt-station is-${tone}`} transform={`translate(${at.x},${at.y})`} data-testid="kt-station" role="img" aria-label={total ? `检查 ${passed}/${total}` : '还没有检查结果'}>
      <circle className="kt-station-bg" r={at.r} />
      <circle className="kt-station-track" r={r} />
      {pass > 0 && <circle className="kt-station-pass" r={r} strokeDasharray={`${pass} ${c}`} transform="rotate(-90)" />}
      {fail > 0 && <circle className="kt-station-fail" r={r} strokeDasharray={`${fail} ${c}`} strokeDashoffset={-pass} transform="rotate(-90)" />}
      <text className="kt-station-num" y={1} textAnchor="middle" dominantBaseline="central">{total ? `${passed}/${total}` : '—'}</text>
      <text className="kt-station-label" y={-at.r - 9} textAnchor="middle">检查</text>
      {flash && <circle key={flash.n} className={`kt-station-flash is-${flash.tone}`} r={at.r + 3} />}
    </g>
  )
}

/** The particle layer (overlay.ts) on its own engine: *feed* takes a poll's items, *path* and *box* route them (the
 * latest ones, so a new layout takes effect at once); nothing plays while the page is hidden. Returns the ref of the
 * layer's <g>. */
export function useParticles(reducedMotion: boolean, feed: MutableRefObject<((items: Item[]) => void) | null>,
  path: (from: string, to: string) => Path | null, box: (a: string) => Box | null): (el: SVGGElement | null) => void {
  const engine = useMemo(() => new Engine({ now: () => performance.now(), reducedMotion }), [reducedMotion])
  const overlay = useRef<Overlay | null>(null)
  const pathRef = useLatest(path)
  const boxRef = useLatest(box)
  const ref = useCallback(
    (el: SVGGElement | null) => {
      overlay.current?.dispose()
      overlay.current = el && !reducedMotion
        ? new Overlay(el, engine, { path: (a, b) => pathRef.current(a, b), box: (a) => boxRef.current(a), now: () => performance.now() })
        : null
    },
    [engine, reducedMotion, pathRef, boxRef],
  )
  useEffect(() => {
    feed.current = (items) => {
      engine.ingest(items, performance.now())
      overlay.current?.wake()
    }
    return () => {
      feed.current = null
    }
  }, [engine, feed])
  useEffect(() => {
    const apply = () => {
      engine.setHidden(document.hidden, performance.now())
      if (!document.hidden) overlay.current?.wake()
    }
    document.addEventListener('visibilitychange', apply)
    return () => document.removeEventListener('visibilitychange', apply)
  }, [engine])
  useEffect(() => () => overlay.current?.dispose(), [])
  return ref
}

/** Which hover card is open, and hover(key, delay): open *key* (null: close) after *delay* ms, so passing over a node
 * opens nothing and the pointer can move into the card. */
export function useHoverCard(): [string | null, (key: string | null, delay: number) => void] {
  const [card, setCard] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const hover = useCallback((key: string | null, delay: number) => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => setCard(key), delay)
  }, [])
  useEffect(() => () => void (timer.current && clearTimeout(timer.current)), [])
  return [card, hover]
}
