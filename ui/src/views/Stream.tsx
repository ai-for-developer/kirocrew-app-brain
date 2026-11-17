/**
 * Where a requirement's time went (时间花在哪), from the board's stages: each span of a stage, and the run that was at
 * it (the AI on this machine, the runner, waiting for a person, done again), from its start to the merge or now.
 */
import type { ReqBoard, Run, StageNode } from '../types'

/** Where the time went: AI on this machine, the runner, waiting for a person, done again. */
export type Part = 'agent' | 'runner' | 'person' | 'rework'
export const PART_LABEL: Record<Part, string> = { agent: 'AI 在本机做', runner: '运行机在做', person: '等人', rework: '返工' }
export const PART_VAR: Record<Part, string> = { agent: 'var(--vs-agent)', runner: 'var(--s1)', person: 'var(--vs-person)', rework: 'var(--vs-rework)' }
export const PARTS: Part[] = ['agent', 'runner', 'person', 'rework']
const RANK: Record<Part, number> = { person: 4, rework: 3, runner: 2, agent: 1 }

/** One stretch of the requirement's time: what was most telling then (a wait for a person over work going on meanwhile). */
export interface StreamSeg {
  from: number
  to: number
  part: Part
  /** the stage it is on (a board node id); `run:<key>` for a run's wait on no stage */
  node: string
}

function partOf(kind: 'work' | 'wait' | 'fix', run: Run | undefined): Part {
  return kind === 'wait' ? 'person' : kind === 'fix' ? 'rework' : run?.kind === 'runner' ? 'runner' : 'agent'
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
    else out.push({ from: t0, to: t1, part: top.part, node })
  }
  return out
}

export function splitOf(segs: StreamSeg[]) {
  return PARTS.map((p) => ({ p, s: segs.filter((g) => g.part === p).reduce((a, g) => a + g.to - g.from, 0) }))
}
