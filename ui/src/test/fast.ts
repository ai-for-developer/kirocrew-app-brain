/**
 * A fast run (快速) as the backend sends one, built from the Lean fixtures (a copy, then the fast fields): brain-kit's
 * fast flow on the row, no intent, Lean's empty summary; its board has the five steps in place of the stage graph (no
 * phases, nodes, runs, relays or Lean steps), its own commits on the conveyor (on no node and no run) and a wall of
 * 自动检查, CI, 风险目录 and the Brain's cells. PAY-17 is ready for 开 MR; PAY-18 has MR !12 open with CI green.
 */
import type { ActResult, BeltItem, CheckCell, CommitOut, CommitsOutput, FastStep, FlowView, LeanSummary, OutFile, ReqBoard, Requirement, RequirementDetail, Waits } from '../types'
import { fx } from './data'

export const FAST: FlowView = { id: 'fast', label: '快速', intent: false, actions: ['full', 'mr'] }
export const LEAN: FlowView = { id: 'lean', label: '完整流程', intent: true, actions: ['handoff', 'review'] }
export const MR_URL = 'https://gitlab.example.com/shop/shop-pay/-/merge_requests/12'
export const PIPELINE_URL = 'https://gitlab.example.com/shop/shop-pay/-/pipelines/803'
const AT = 1794052000

const EMPTY_LEAN: LeanSummary = {
  stage: null, stage_label: null, stage_n: null, stage_state: null, done: 0, total: 0, done_n: 0, skipped_n: 0, next: null, skipped_phases: [],
}

const waits = (who: Waits['who'], step: Waits['do'], text: string): Waits =>
  ({ who, do: step, text, line: null, raw: '', todo: '', since: AT, where: who === 'you' ? 'app' : null, review: false })

export const WAITS = {
  writing: waits('agent', 'none', 'AI 在写代码'),
  stopped: waits('you', 'reply', '会话停下了：看它最后说了什么，回它'),
  ready: waits('you', 'open_mr', '代码提交了，检查是绿的：做完了就点「开 MR」，没做完就回会话'),
  risk: waits('you', 'full', '改到了风险目录（src/pay/refund.py）：快速需求不能合并这个改动。点「转完整流程」'),
  ciRunning: waits('nobody', 'none', 'MR !12：CI 在跑'),
  ciPatrol: waits('runner', 'none', 'MR !12：CI 没过，运行机的 MR 巡检会去修'),
  ciYou: waits('you', 'reply', 'MR !12：CI 没过：回会话让 AI 修'),
  merge: waits('people', 'merge', 'MR !12：CI 过了，等人审、合并'),
} as const

export const MR = { iid: 12, url: MR_URL, state: 'opened' }

/** PAY-17 (ready for 开 MR) made of a Lean fixture's row (*base*: 交付总览's first), or that row with *more*. */
export function fastRow(more: Partial<Requirement> = {}, base: Requirement = fx.overview().rows[0]): Requirement {
  return {
    ...base, key: 'PAY-17', title: '订单优惠码', branch: 'ai/alice/PAY-17-coupon', worktree: '/Users/alice/workplace/brain-app/PAY-17-coupon',
    flow: FAST, intent: null, state: 'local', state_label: '本地开发中', mr: null, lean: EMPTY_LEAN, waits: WAITS.ready, ...more,
  }
}

const step = (id: FastStep['id'], label: string, status: FastStep['status'], text = '', url: string | null = null): FastStep =>
  ({ id, label, status, text, at: status === 'waiting' ? null : AT, url })

/** PAY-17's: its code committed and checked, the MR not opened yet. */
export const READY_STEPS: FastStep[] = [
  step('code', '写代码', 'done', '提交了 3 次'), step('verify', '自动检查', 'done', '3 项都过'), step('mr', '开 MR', 'waiting'),
  step('ci', 'CI', 'waiting'), step('merge', '合并', 'waiting'),
]
/** PAY-18's: MR !12 open, its CI green, people to merge it. */
export const MERGE_STEPS: FastStep[] = [
  step('code', '写代码', 'done', '提交了 4 次'), step('verify', '自动检查', 'done', '3 项都过'), step('mr', '开 MR', 'done', 'MR !12', MR_URL),
  step('ci', 'CI', 'done', '过了', PIPELINE_URL), step('merge', '合并', 'waiting', '等人审、合并', MR_URL),
]

export function fastDetail(row: Partial<Requirement> = {}): RequirementDetail {
  const d = fx.detail()
  return {
    ...d, row: fastRow(row, d.row), run: d.run && { ...d.run, branch: 'ai/alice/PAY-17-coupon' },
    lean: { ...d.lean, stages: [], phases: [], approvals: [], next: [], open_questions: [] }, decisions: [], questions: [],
  }
}

/** PAY-17's commits (40 hex each), oldest first, and the commit its branch left main at. */
export const [C1, C2, C3, C4, FORK] = ['c1', 'c2', 'c3', 'c4', 'b0'].map((x) => x.repeat(20))
const changed = (path: string, status: OutFile['status'], added: number, removed: number, ref: string, base: string | null): OutFile =>
  ({ path, output: false, status, added, removed, ref, pin: null, base, base_round: null, vs: [], why: null, proof: null })
const commit = (sha: string, at: number, subject: string, files: OutFile[], records = false): CommitOut =>
  ({ sha, at, subject, by: 'person', records_only: records, files, more: 0 })

/** PAY-17's three code commits, then MR 说明 (only the run's records), as GET /outputs/{repo}/{key}/commits lists them. */
export const COMMITS: CommitOut[] = [
  commit(C1, AT - 3000, '优惠码：校验和折扣', [changed('src/pay/coupon.py', 'A', 48, 0, C1, null), changed('tests/test_coupon.py', 'A', 60, 0, C1, null)]),
  commit(C2, AT - 2000, '优惠码：下单时用上', [changed('src/pay/coupon.py', 'M', 6, 2, C2, C1), changed('src/pay/order.py', 'M', 12, 3, C2, C1)]),
  commit(C3, AT - 1000, '优惠码：补下单的测试', [changed('tests/test_order.py', 'M', 25, 1, C3, C2)]),
  commit(C4, AT - 500, 'MR 说明', [changed('runs/PAY-17/MR.md', 'A', 18, 0, C4, null)], true),
]

/** All of them added up: each file at the last commit that changed it, compared with where the branch left main. */
export function fastCommits(): CommitsOutput {
  return {
    branch: 'ai/alice/PAY-17-coupon', head: C4, commits: structuredClone(COMMITS), more: 0, why: null,
    files: [changed('src/pay/coupon.py', 'A', 52, 0, C2, FORK), changed('tests/test_coupon.py', 'A', 60, 0, C1, FORK),
      changed('src/pay/order.py', 'M', 12, 3, C2, FORK), changed('tests/test_order.py', 'M', 25, 1, C3, FORK), changed('runs/PAY-17/MR.md', 'A', 18, 0, C4, FORK)],
  }
}

/** The conveyor of those commits, as the board has them (12 hex, on no node and no run). */
export const beltOf = (commits: CommitOut[]): BeltItem[] => commits.map((c) => ({
  kind: 'commit', at: c.at, sha: c.sha.slice(0, 12), by: c.by, text: c.subject, files: c.files.length, records_only: c.records_only, run: null, node: null,
  changes: c.files.map((f) => ({ path: f.path, added: f.added ?? 0, removed: f.removed ?? 0, status: f.status })),
}))

const cell = (id: string, group: CheckCell['group'], label: string, name: string, status: CheckCell['status'], detail: string): CheckCell =>
  ({ id, group, label, name, status, at: AT, detail, node: null, run: null })

export function fastBoard(row: Partial<Requirement> = {}, steps: FastStep[] = READY_STEPS): ReqBoard {
  const b = fx.board()
  const cells = [
    cell('verify-pytest', 'verify', 'pytest', 'pytest', 'pass', '通过'), cell('verify-ruff', 'verify', 'ruff', 'ruff', 'pass', '通过'),
    cell('verify-mypy', 'verify', 'mypy', 'mypy', 'pass', '通过'), cell('ci', 'ci', 'CI', 'MR 的流水线', 'skip', '还没开 MR'),
    cell('risk', 'risk', '风险目录', '风险目录（gates/risk-paths）', 'pass', '没改到'), ...b.checks.cells.filter((c) => c.group === 'brain'),
  ]
  return {
    ...b, row: fastRow(row, b.row), steps, phases: [], nodes: [], runs: [], relays: [], other_runs: [], lean_steps: [], belt: beltOf(COMMITS), decisions: [], questions: [],
    checks: { ...b.checks, cells, todo: [], passed: cells.filter((c) => c.status === 'pass').length, failed: cells.filter((c) => c.status === 'fail').length,
      total: cells.filter((c) => c.status === 'pass' || c.status === 'fail').length },
    accept: { ...b.accept, state: 'not_yet' },
  }
}

/** 开 MR's or 转完整流程's answer (POST /requirements/{repo}/{key}/act/{action}): 开 MR that did nothing yet, with *over*. */
export const actAnswer = (over: Partial<ActResult>): ActResult => ({
  ok: true, action: 'mr', text: '', pushed: false, mr: null, commands: [], new_mr_url: null, intent: null, told: false, warnings: [], output: '', ...over,
})
