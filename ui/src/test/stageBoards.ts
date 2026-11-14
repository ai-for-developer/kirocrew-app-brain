/**
 * Boards for the stage graph in the shape of design.md's scenarios, built from board.json with hand-made phases, nodes,
 * runs and relays (the backend's fold makes the real ones): Scenario A (PAY-16, waiting for alice's INCEPTION approval
 * on her machine) and Scenario B (PAY-9, handed over; the runner did both units, verify 5 rounds, the product sent the
 * release back once, two CI fixes later round 4 waits for the product's acceptance). PAY-9's times are the clock times
 * of design.md on board.json's day, PAY-16's count back from board.json's `at`.
 */
import type {
  ActorStatus, BeltItem, BoardPhase, CheckCell, Gate, NodeId, PhaseKey, Relay, RelayStep, ReqBoard, Round, RoundOutcome, Run, RunKey, StageNode,
} from '../types'
import { fx } from './data'

const PHASE_WORDS: Record<PhaseKey, [string, string, string]> = {
  init: ['0', '初始化', '认领 · 开工 · 带上 Brain 的知识'],
  ideation: ['1', '构思', '1.1 意图捕获 · 1.4 范围定义 · 1.7 审批交接'],
  inception: ['2', '启动', '2.3 需求分析 · 2.6 应用设计 · 2.7 拆分单元'],
  construction: ['3', '构建', '3.5 代码生成 · 3.6 构建测试 · 审查'],
  release: ['✓', '发布', '发布审批 · 产品验收'],
  operations: ['4', '运维 · 回写', '4.1 部署流水线 · 回写 Brain'],
}

export function phase(key: PhaseKey, state: BoardPhase['state'] = 'used', note: string | null = null): BoardPhase {
  const [n, title, sub] = PHASE_WORDS[key]
  return { key, n, title, sub, state, note }
}

/** stage id → n, label, phase, icon, what it hands in ({unit}: the unit's) */
const STAGE: Record<string, [string, string, PhaseKey, string, string]> = {
  'reverse-engineering': ['2.1', '逆向工程', 'inception', 'search', 'architecture.md'],
  requirements: ['2.3', '需求分析', 'inception', 'req', 'requirements.md'],
  'user-stories': ['2.4', '用户故事', 'inception', 'stories', 'stories.md'],
  'application-design': ['2.6', '应用设计', 'inception', 'design', 'application-design.md'],
  units: ['2.7', '拆分单元', 'inception', 'units', 'unit-of-work.md'],
  'functional-design': ['3.1', '功能设计', 'construction', 'design', '{unit} 的功能设计'],
  'nfr-design': ['3.2', '非功能设计', 'construction', 'shield', '{unit} 的非功能设计'],
  'infrastructure-design': ['3.4', '基础设施设计', 'construction', 'server', '{unit} 的基础设施设计'],
  'code-generation': ['3.5', '代码生成', 'construction', 'code', '{unit} 的代码生成计划'],
  'build-and-test': ['3.6', '构建测试', 'construction', 'ci', 'lean verify · 3 项'],
  review: ['3.6', '审查', 'construction', 'review', 'review.md'],
  'ci-pipeline': ['3.7', 'CI 流水线', 'construction', 'pipeline', 'ci-config.md'],
  release: ['✓', '发布', 'release', 'release', 'release.md'],
  'deployment-pipeline': ['4.1', '部署流水线', 'operations', 'pipeline', 'deployment-strategy.md'],
  'environment-provisioning': ['4.2', '环境准备', 'operations', 'server', 'environment-inventory.md'],
  'deployment-execution': ['4.3', '部署执行', 'operations', 'rocket', 'deployment-log.md'],
  'observability-setup': ['4.4', '可观测性', 'operations', 'eye', 'observability.md'],
  'incident-response': ['4.5', '故障响应', 'operations', 'alarm', 'runbooks.md'],
  'performance-validation': ['4.6', '性能验证', 'operations', 'gauge', 'load-test-plan.md'],
  'feedback-optimization': ['4.7', '反馈优化', 'operations', 'loop', 'feedback-loop.md'],
  reflect: ['4', '回写 Brain', 'operations', 'brain', '合并以后写回新知识'],
}
export const OPERATIONS = Object.keys(STAGE).filter((s) => STAGE[s][2] === 'operations' && s !== 'reflect')

const OUTCOME: RoundOutcome = { kind: 'open', at: null, gate: null, by: null, decision: null, text: null, sha: null, subject: null, checks: [] }

export function round(n: number, run: string, started: number, ended: number | null, outcome: Partial<RoundOutcome> = {}, why: Partial<Round['why']> = {}): Round {
  return {
    n, run: run as RunKey, started, ended, outcome: { ...OUTCOME, kind: ended === null ? 'open' : 'done', ...outcome },
    why: { kind: n === 1 ? 'first' : 'sent_back', round: n === 1 ? null : n - 1, pipeline: null, sha: null, subject: null, at: null, ...why },
    note: null, outputs: [], commits: [],
  }
}

export function gate(kind: Gate['kind'], state: Gate['state'], r: number, over: Partial<Gate> = {}): Gate {
  return { kind, state, round: r, at: null, who: null, by: null, line: null, mr: null, text: null, docs: [], ...over }
}

/** A node: its stage's words from the table above, its run its last round's. */
export function stage(id: string, status: ActorStatus, rounds: Round[] = [], over: Partial<StageNode> = {}): StageNode {
  const [sid, unit] = id.split('/')
  const [n, title, ph, icon, sub] = STAGE[sid]
  return {
    id: id as NodeId, stage: sid, n, unit: unit ?? null, phase: ph, title, sub: sub.replace('{unit}', unit ?? ''), icon, status, doing: '',
    since: null, history: [], spans: [], marks: [], rounds, gates: [], run: rounds.length ? rounds[rounds.length - 1].run : null, child: null, ...over,
  }
}

const skipped = (id: string, run: string, at: number) => stage(id, 'skipped', [round(1, run, at, at, { kind: 'skipped' })])

export function run(key: string, kind: Run['kind'], label: string, now: Partial<Run['now']>, over: Partial<Run> = {}): Run {
  return {
    key: key as RunKey, kind, label, readable: kind === 'local' ? 'here' : 'elsewhere', n: null, run_id: null, req: null, unit: null,
    now: { status: 'done', doing: '', since: null, who: null, node: null, do: null, ...now }, credits: null, sessions: [], ticker: null, ...over,
  }
}

function step(key: RelayStep['key'], title: string, at: number, text: string): RelayStep {
  return { key, title, status: 'done', at, text, todo: null }
}

function commit(sha: string, at: number, runKey: string, text: string, over: Partial<BeltItem> = {}): BeltItem {
  const local = runKey === 'local'
  return { kind: 'commit', at, sha: sha.padEnd(12, '0'), by: local ? 'person' : 'bot', text, files: 2, records_only: false,
    run: runKey as RunKey, node: null, ...over }
}

function verifyCell(name: string, at: number, secs: string): CheckCell {
  return { id: `verify-${name}`, group: 'verify', label: name, name, status: 'pass', at, detail: secs, node: 'build-and-test' as NodeId, run: 'runner-1' as RunKey }
}

/** design.md's Scenario B: PAY-9 退款通知短信, at 16:15. */
export function pay9(): ReqBoard {
  const b = fx.board()
  const t = (hm: string) => {
    const d = new Date(b.at * 1000)
    const [h, m] = hm.split(':').map(Number)
    d.setHours(h, m, 0, 0)
    return d.getTime() / 1000
  }
  const MR = 'https://gitlab.example.com/shop/shop-pay/-/merge_requests/41'
  const inception = gate('INCEPTION', 'approved', 1, { at: t('10:52'), by: '你', docs: ['inception/requirements/requirements.md', 'inception/application-design/application-design.md'] })
  const approved = (from: string, to: string) => [round(1, 'local', t(from), t(to), { kind: 'approved', at: t('10:52'), gate: 'INCEPTION', by: '你' })]
  const checks = ['pytest', 'ruff', 'mypy'].map((name) => ({ name, ok: true, secs: 9 }))
  const verify = (n: number, at: string) => round(n, 'runner-1', t(at), t(at) + 60, { kind: 'passed', checks }, n === 1 ? {} : { kind: 'code_changed', round: null })
  const back = round(1, 'runner-1', t('13:25'), t('14:41'), { kind: 'sent_back', at: t('14:41'), gate: 'RELEASE', by: '产品在 MR 上', decision: 'D-2' })
  const ci = (n: number, from: string, to: string, sha: string, subject: string) =>
    round(n, 'runner-1', t(from), t(to), { kind: 'superseded', at: t(to), sha, subject }, n === 2 ? {} : { kind: 'code_changed', round: null, sha, subject })
  const perUnit = ['functional-design', 'nfr-design', 'infrastructure-design'].flatMap((s) => ['api', 'web'].map((u) => skipped(`${s}/${u}`, 'runner-1', t('11:10'))))
  const nodes: StageNode[] = [
    skipped('reverse-engineering', 'local', t('10:05')),
    stage('requirements', 'merged', approved('10:05', '10:33'), { gates: [inception], doing: '你批了' }),
    skipped('user-stories', 'local', t('10:33')),
    stage('application-design', 'merged', approved('10:06', '10:38'), { gates: [inception], doing: '你批了' }),
    stage('units', 'merged', [round(1, 'local', t('10:53'), t('10:57'))], { doing: '2 个单元：api、web' }),
    ...perUnit,
    stage('code-generation/api', 'merged', [round(1, 'runner-1', t('11:12'), t('12:21'))]),
    stage('code-generation/web', 'merged', [round(1, 'runner-1', t('12:21'), t('13:05'))]),
    stage('build-and-test', 'merged', [verify(1, '11:10'), verify(2, '13:08'), verify(3, '15:05'), verify(4, '15:33'), verify(5, '16:10')], { doing: '第 5 轮 · 5 轮都过' }),
    stage('review', 'merged', [round(1, 'runner-1', t('13:12'), t('13:24'))]),
    skipped('ci-pipeline', 'runner-1', t('13:24')),
    stage('release', 'awaiting_approval', [back, ci(2, '15:06', '15:30', '2e8d1f6a0b1c', '修好 CI：api 的 lint'), ci(3, '15:34', '16:08', '9f3c2a8d4e5f', '修好 CI：web 的类型检查'),
      round(4, 'runner-1', t('16:11'), null, {}, { kind: 'code_changed', round: null, sha: '9f3c2a8d4e5f', subject: '修好 CI：web 的类型检查' })], {
      doing: '等产品验收（D-3）', since: t('16:12'),
      gates: [gate('RELEASE', 'sent_back', 1, { at: t('14:41'), by: '产品在 MR 上' }), gate('RELEASE', 'pending', 4, { at: t('16:14'), who: 'product', mr: { decision: 'D-3', url: MR }, docs: ['operations/release.md'] })],
    }),
    ...OPERATIONS.map((s) => skipped(s, 'runner-1', t('16:11'))),
    stage('reflect', 'waiting', [], { doing: '等合并以后' }),
  ]
  const runs = [
    run('local', 'local', '本机会话', { status: 'done', doing: '做完 · 11:05 交出去', since: t('11:05') }, {
      credits: 12.4, sessions: [{ slot: 'brain-PAY-9-7f2c', url: '/chat?sid=brain-PAY-9-7f2c', busy: false, title: 'PAY-9 退款通知短信' }],
    }),
    run('runner-1', 'runner', '运行机 第 1 次', { status: 'awaiting_approval', doing: '等产品验收（D-3）', since: t('16:14'), who: 'product', node: 'release' as NodeId },
      { n: 1, run_id: 'RUN-PAY-9-1' }),
  ]
  const relays: Relay[] = [{
    to: 'runner-1' as RunKey, at: t('11:05'), sha: 'e7d2b11c4a90',
    steps: [step('handover', '交接', t('11:05'), '交出去了（提交 e7d2b11）'), step('bridge', '桥核对', t('11:06'), '桥接手了（MR !41）'),
      step('signer', '签名', t('11:07'), '合同签好了'), step('workspace', '运行机建工作区', t('11:09'), '运行机建好了工作区')],
  }]
  const belt: BeltItem[] = [
    commit('a1c3e09', t('10:41'), 'local', 'PAY-9：需求和设计'), commit('5b7d210', t('10:52'), 'local', 'PAY-9：批准记录', { records_only: true }),
    commit('6d20f4b', t('10:57'), 'local', 'PAY-9：拆成 api 和 web'), commit('e7d2b11', t('11:05'), 'local', '交给运行机', { kind: 'handover' }),
    ...[['5f0a3c2', '12:18', 'api 的短信通知'], ['c41b7d9', '13:03', 'web 的退款短信入口'], ['77aa0f1', '13:10', '构建测试记录'], ['88bb2c3', '13:24', '审查：review.md'],
      ['3b8e01f', '13:26', '请批发布'], ['4a7b9c1', '15:02', '按 D-2 改：旧订单不发短信'], ['2e8d1f6', '15:30', '修好 CI：api 的 lint'],
      ['9f3c2a8', '16:08', '修好 CI：web 的类型检查'], ['d5e4a90', '16:12', '请批发布（第 4 轮）']].map(([sha, at, text]) => commit(sha, t(at), 'runner-1', text)),
  ]
  const cells = [verifyCell('pytest', t('16:10'), '41 秒'), verifyCell('ruff', t('16:10'), '2 秒'), verifyCell('mypy', t('16:10'), '9 秒')]
  return {
    ...b,
    at: t('16:15'),
    row: { ...b.row, key: 'PAY-9', title: '退款通知短信', state: 'acceptance', state_label: '等验收', branch: 'ai/alice/PAY-9-refund-sms', worktree: null,
      waits: { who: 'product', text: '等产品在 MR 上验收（D-3）', line: null, raw: '', do: 'accept', todo: '', since: t('16:14'), where: 'mr', review: false } },
    branch: 'ai/alice/PAY-9-refund-sms', started_at: t('10:05'), ended_at: null, live: false,
    person: { status: 'done', doing: '认领 · 开工 · 交接', owner: 'alice', me: true, you: true },
    phases: [phase('init'), phase('ideation', 'unused', '这次不需要'), phase('inception', 'used', '跳过：逆向工程、用户故事'),
      phase('construction', 'used', '每个单元都跳过了功能、非功能、基础设施设计 · 3.7 CI 流水线'), phase('release'), phase('operations', 'used', '运维 7 个阶段：这次不需要')],
    nodes, runs, relays, other_runs: [], events: [], belt,
    checks: { cells, passed: 3, failed: 0, total: 3, todo: [], at: t('16:10'), history: [] },
    brain: { ...b.brain, loaded: 6, cited: ['P-7', 'T-12'], stale: [] },
  }
}

/** design.md's Scenario A: PAY-16 退款进度查询, waiting 12 minutes for alice's INCEPTION approval on her machine. */
export function pay16(): ReqBoard {
  const b = fx.board()
  const ago = (min: number) => b.at - min * 60
  const line = 'approve INCEPTION 3fa2c1d0'
  const asked = gate('INCEPTION', 'pending', 1, { at: ago(12), who: 'you', line, docs: ['inception/requirements/requirements.md', 'inception/application-design/application-design.md'] })
  const open = (from: number) => [round(1, 'local', ago(from), null)]
  const nodes: StageNode[] = [
    skipped('reverse-engineering', 'local', ago(80)),
    stage('requirements', 'awaiting_approval', open(80), { gates: [asked], doing: '等你批', since: ago(12) }),
    skipped('user-stories', 'local', ago(54)),
    stage('application-design', 'awaiting_approval', open(54), { gates: [asked], doing: '等你批', since: ago(12) }),
    stage('units', 'done', [round(1, 'local', ago(20), ago(16))], { doing: '1 个单元：app' }),
    stage('code-generation/app', 'waiting', [], { doing: '等批了设计' }),
    stage('build-and-test', 'waiting', [], { doing: '等代码' }),
    stage('review', 'waiting', [], { doing: '等审查' }),
    stage('release', 'waiting', [], { doing: '等审查和自动检查' }),
    ...OPERATIONS.map((s) => skipped(s, 'local', ago(80))),
    stage('reflect', 'waiting', [], { doing: '等合并以后' }),
  ]
  return {
    ...b,
    row: { ...b.row, key: 'PAY-16', title: '退款进度查询', state: 'local', state_label: '本地开发中', branch: 'ai/alice/PAY-16-refund-progress',
      waits: { who: 'you', text: '等你批准需求和设计（在会话里自己打一行）', line, raw: '', do: 'approve', todo: '', since: ago(12), where: 'session', review: false } },
    branch: 'ai/alice/PAY-16-refund-progress', started_at: ago(82), ended_at: null, live: false,
    person: { status: 'awaiting_approval', doing: '等你批准需求和设计', owner: 'alice', me: true, you: true },
    phases: [phase('init'), phase('ideation', 'unused', '这次不需要'), phase('inception', 'used', '跳过：逆向工程、用户故事'), phase('construction'), phase('release'),
      phase('operations', 'used', '运维 7 个阶段：这次不需要')],
    nodes,
    runs: [run('local', 'local', '本机会话', { status: 'awaiting_approval', doing: '等你批', since: ago(12), who: 'you', node: 'application-design' as NodeId }, {
      credits: 3.1, sessions: [{ slot: 'brain-PAY-16-2b9d', url: '/chat?sid=brain-PAY-16-2b9d', busy: false, title: 'PAY-16 退款进度查询' }],
    })],
    relays: [], other_runs: [], events: [],
    belt: [commit('8c1f0aa', ago(14), 'local', 'PAY-16：需求和设计', { records_only: true })],
    checks: { cells: [], passed: 0, failed: 0, total: 0, todo: [], at: null, history: [] },
    brain: { ...b.brain, loaded: 4, cited: ['P-7'], stale: [] },
  }
}
