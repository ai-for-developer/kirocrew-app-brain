/**
 * Where a requirement's way is, read off its boxes: the one rule the AI-DLC Lean card, the graph's edges and the check
 * ring follow. A box's status has one writer (backend stages.node_status); where the way goes next is the card's
 * 下一步 (vocab.leanNow).
 */
import type { GateKind, ReqBoard, Round, StageNode } from '../types'
import { leanNow } from '../vocab'
import { openGate } from './format'

/** Approvals of a document its stage has finished: Lean marks the stage done, then asks. A box waiting on one of these
 * is through; one waiting on PLAN (the code comes after its plan) or RELEASE (that acceptance is the release) is not. */
const DOC_GATES: ReadonlySet<GateKind> = new Set<GateKind>(['IDEATION', 'REQUIREMENTS', 'DESIGN', 'INCEPTION'])

/** Its work is through: done, merged, or its document handed in and waiting to be approved. */
export function through(n: StageNode): boolean {
  if (n.status === 'done' || n.status === 'merged') return true
  const g = openGate(n)
  return n.status === 'awaiting_approval' && !!g && DOC_GATES.has(g.kind)
}

/** *at*: the last box on the way that is reached and not through (at work, waiting on its own acceptance, failed); *next*:
 * the first box not reached yet (dashed). A skipped stage has no box. */
export function wayAt(nodes: readonly StageNode[]): { at: StageNode | null; next: StageNode | null } {
  const drawn = nodes.filter((n) => n.status !== 'skipped')
  const on = drawn.filter((n) => n.status !== 'waiting' && !through(n))
  return { at: on[on.length - 1] ?? null, next: drawn.find((n) => n.status === 'waiting') ?? null }
}

/** The boxes the graph's edges lead on to: Lean's next stage once the AI-DLC Lean card says 下一步 and it has a box, with
 * the 审查 still to do before it (Lean releases only after a review in a new session); else the first box not reached. */
export function wayAhead(board: Pick<ReqBoard, 'nodes' | 'row'>): ReadonlySet<string> {
  const drawn = board.nodes.filter((n) => n.status !== 'skipped')
  const said = leanNow(board.row, []).split(' · ').pop()!.startsWith('下一步：')
  const lean = said ? drawn.findIndex((n) => n.stage === board.row.lean.next?.id && n.status === 'waiting') : -1
  if (lean < 0) {
    const { next } = wayAt(drawn)
    return new Set(next ? [next.id] : [])
  }
  return new Set([...drawn.slice(0, lean).filter((n) => n.stage === 'review' && n.status === 'waiting'), drawn[lean]].map((n) => n.id))
}

export interface VerifyCount {
  passed: number
  failed: number
  total: number
  /** 第 5 轮 · 5 轮都过 · 第 2 轮 · 第 1 轮没过 · 还没跑过检查 */
  say: string
  tone: 'ok' | 'bad' | 'idle'
}

const ran = (r: Round) => r.outcome.kind === 'passed' || r.outcome.kind === 'failed'

/** 检查通过 and the check ring: the checks of the latest verify on these boxes (构建测试's latest round that ran them),
 * and how the rounds before it went. A verify that recorded no checks counts as one. */
export function verifyCount(nodes: readonly StageNode[]): VerifyCount {
  const rounds = nodes.find((n) => n.stage === 'build-and-test')?.rounds.filter(ran) ?? []
  const last = rounds[rounds.length - 1]
  if (!last) return { passed: 0, failed: 0, total: 0, say: '还没跑过检查', tone: 'idle' }
  const ok = last.outcome.kind === 'passed'
  const checks = last.outcome.checks
  const passed = checks.length ? checks.filter((c) => c.ok).length : ok ? 1 : 0
  const total = checks.length || 1
  const failed = total - passed
  if (!ok) return { passed, failed, total, say: `第 ${last.n} 轮 · ${failed || 1} 项没过`, tone: 'bad' }
  const before = rounds.slice(0, -1).filter((r) => r.outcome.kind === 'failed').map((r) => r.n)
  const say = before.length ? `第 ${last.n} 轮 · 第 ${before.join('、')} 轮没过` : rounds.length > 1 ? `第 ${last.n} 轮 · ${rounds.length} 轮都过` : `第 ${last.n} 轮 · 全过`
  return { passed, failed, total, say, tone: 'ok' }
}
