import { describe, expect, test } from 'vitest'
import type { ActorStatus, PhaseKey } from '../types'
import {
  GW, NODE_H, NODE_W, ORCH, RELAY_W, SCALE, ST_FOLD, ST_ORCH, pointAt, measure, relayAnchor, stageAnchorBox, stageCommandPath, stageKey, stageLayout, stageShape,
  type StageInput, type StageLayout,
} from './layout'

type N = StageInput['nodes'][number]

/** A node of the way: its stage and unit from its id (code-generation/api), done by *run* unless it has not started. */
function nd(id: string, phase: PhaseKey, status: ActorStatus = 'merged', run: string | null = 'local', rounds = 1): N {
  const [stage, unit] = id.split('/')
  return { id, stage, unit: unit ?? null, phase, status, rounds: run ? Array.from({ length: rounds }, () => ({ run })) : [] }
}

const PHASES: StageInput['phases'] = [
  { key: 'init', state: 'used' }, { key: 'ideation', state: 'unused' }, { key: 'inception', state: 'used' },
  { key: 'construction', state: 'used' }, { key: 'release', state: 'used' }, { key: 'operations', state: 'used' },
]
const SKIPPED_PER_UNIT = ['functional-design', 'nfr-design', 'infrastructure-design'].flatMap((s) => ['api', 'web'].map((u) => nd(`${s}/${u}`, 'construction', 'skipped', 'runner-1')))
const OPS = ['deployment-pipeline', 'environment-provisioning', 'deployment-execution', 'observability-setup', 'incident-response', 'performance-validation', 'feedback-optimization']

/** design.md's Scenario B (PAY-9): requirements and design here, handed over, the runner did both units, verify 5
 * rounds, the release waits for the product in its 4th round; Ideation not needed, Operations skipped. */
function pay9(over: Partial<StageInput> = {}): StageInput {
  return {
    phases: PHASES,
    nodes: [
      nd('reverse-engineering', 'inception', 'skipped'), nd('requirements', 'inception'), nd('user-stories', 'inception', 'skipped'),
      nd('application-design', 'inception'), nd('units', 'inception'), ...SKIPPED_PER_UNIT,
      nd('code-generation/api', 'construction', 'merged', 'runner-1'), nd('code-generation/web', 'construction', 'merged', 'runner-1'),
      nd('build-and-test', 'construction', 'merged', 'runner-1', 5), nd('review', 'construction', 'merged', 'runner-1'),
      nd('ci-pipeline', 'construction', 'skipped', 'runner-1'), nd('release', 'release', 'awaiting_approval', 'runner-1', 4),
      ...OPS.map((s) => nd(s, 'operations', 'skipped', 'runner-1')), nd('reflect', 'operations', 'waiting', null),
    ],
    runs: [{ key: 'local' }, { key: 'runner-1' }],
    relays: [{ to: 'runner-1' }],
    roster: [
      { kind: 'head', run: null }, { kind: 'run', run: 'local' }, { kind: 'relay', run: null }, { kind: 'run', run: 'runner-1' },
      { kind: 'sub', run: 'runner-1' }, { kind: 'brain-head', run: null }, ...Array.from({ length: 4 }, () => ({ kind: 'brain' as const, run: null })),
    ],
    ...over,
  }
}

/** The 1280 and 1050 px apps' graph cards. */
const CARD_1280 = 1238
const CARD_1050 = 1008

const box = (lay: StageLayout, id: string) => {
  const b = lay.boxes.get(id)
  if (!b) throw new Error(`no box for ${id}`)
  return b
}
const bandOf = (lay: StageLayout, key: string) => lay.bands.find((b) => b.key === key)!
const edgeKeys = (lay: StageLayout) => lay.edges.map((e) => `${e.from}>${e.to}`)

function noOverlap(lay: StageLayout) {
  const boxes = [...lay.boxes.values()]
  for (const p of boxes) for (const q of boxes) if (p !== q) expect(Math.abs(p.x - q.x) >= NODE_W || Math.abs(p.y - q.y) >= NODE_H, `${p.id} on ${q.id}`).toBe(true)
  for (let i = 1; i < lay.bands.length; i++) expect(lay.bands[i].x1, `${lay.bands[i].key} after ${lay.bands[i - 1].key}`).toBeGreaterThan(lay.bands[i - 1].x2)
  expect(lay.bands[lay.bands.length - 1].x2).toBeLessThanOrEqual(lay.width)
  for (const b of boxes) {
    const band = lay.bands.find((x) => x.key === lay.columns[b.col].phase)!
    expect(b.x - NODE_W / 2, `${b.id} in its band`).toBeGreaterThan(band.x1)
    expect(b.x + NODE_W / 2, `${b.id} in its band`).toBeLessThan(band.x2)
    expect(b.y + NODE_H / 2, `${b.id} above the bands' foot`).toBeLessThan(lay.bottom - 24)
  }
  const init = bandOf(lay, 'init')
  for (const r of lay.roster) expect(r.y, 'the orchestrator list inside its band').toBeLessThan(lay.bottom)
  expect(lay.orch.x).toBeGreaterThan(init.x1)
  expect(lay.orch.x).toBeLessThan(init.x2)
  expect(lay.rail.y).toBeGreaterThan(lay.bottom)
  expect(lay.height).toBeGreaterThan(lay.rail.y)
}

describe('stageLayout', () => {
  test('bands follow the phases left to right; a phase with nothing on the way folds into a narrow band', () => {
    const lay = stageLayout(pay9(), stageShape(1558, pay9()))
    expect(lay.bands.map((b) => [b.key, b.folded])).toEqual([
      ['init', false], ['ideation', true], ['inception', false], ['construction', false], ['release', false], ['operations', false],
    ])
    const w = (k: string) => bandOf(lay, k).x2 - bandOf(lay, k).x1
    expect(w('ideation')).toBe(ST_FOLD)
    // one column each, but 构建 has the code-generation column and the shared one
    expect(w('construction')).toBeCloseTo(2 * w('inception'), 0)
    expect(w('release')).toBeCloseTo(w('inception'), 0)
    noOverlap(lay)
    // a small requirement releases with its big one: its 发布 and 回写 fold, nothing of them has a box
    const child = pay9({ phases: PHASES.map((p) => (p.key === 'release' || p.key === 'operations' ? { ...p, state: 'unused' as const } : p)) })
    const small = stageLayout(child, stageShape(1558, child))
    expect(small.bands.filter((b) => b.folded).map((b) => b.key)).toEqual(['ideation', 'release', 'operations'])
    expect(small.boxes.has('release')).toBe(false)
    expect(small.boxes.has('reflect')).toBe(false)
    expect(edgeKeys(small).at(-1)).toBe('build-and-test>review')
    noOverlap(small)
  })

  test('a band stacks its boxes in node order; 3 构建 gives each unit a row and 构建测试 over 审查 a column; skipped stages get no box', () => {
    const lay = stageLayout(pay9(), stageShape(CARD_1280, pay9()))
    const inception = ['requirements', 'application-design', 'units'].map((id) => box(lay, id))
    expect(new Set(inception.map((b) => b.x)).size).toBe(1)
    expect(inception.map((b) => b.y)).toEqual([...inception.map((b) => b.y)].sort((p, q) => p - q))
    const [api, web, bt, review] = ['code-generation/api', 'code-generation/web', 'build-and-test', 'review'].map((id) => box(lay, id))
    expect(api.x).toBe(web.x)
    expect(api.y).toBeLessThan(web.y)
    expect(bt.x).toBe(review.x)
    expect(bt.x).toBeGreaterThan(api.x)
    // the unit rows and the shared column line up; every column is centred on the tallest
    expect([bt.y, review.y]).toEqual([api.y, web.y])
    expect(box(lay, 'release').y).toBe(inception[1].y)
    expect(box(lay, 'reflect').y).toBe(inception[1].y)
    expect((api.y + web.y) / 2).toBe(inception[1].y)
    for (const id of ['reverse-engineering', 'user-stories', 'ci-pipeline', 'functional-design/api', ...OPS]) expect(lay.boxes.has(id), id).toBe(false)
    expect(lay.units.map((u) => u.unit)).toEqual(['api', 'web'])
    lay.units.forEach((u, i) => {
      const first = [api, web][i]
      expect(u.y).toBeLessThan(first.y - NODE_H / 2)
      expect(u.y).toBeGreaterThan(first.y - NODE_H / 2 - 14)
      expect(u.x).toBeLessThanOrEqual(first.x - NODE_W / 2)
    })
    expect(lay.unitPill).toMatchObject({ n: 2 })
    noOverlap(lay)
  })

  test('a column every unit skipped is not there; a stage only one unit did stands in that unit’s row', () => {
    const one = pay9()
    const nodes = one.nodes.map((n) => (n.id === 'functional-design/web' ? nd('functional-design/web', 'construction', 'merged', 'runner-1') : n))
    const lay = stageLayout({ ...one, nodes }, stageShape(1558, { ...one, nodes }))
    const fd = box(lay, 'functional-design/web')
    expect(fd.y).toBe(box(lay, 'code-generation/web').y)
    expect(fd.x).toBeLessThan(box(lay, 'code-generation/web').x)
    expect(lay.boxes.has('functional-design/api')).toBe(false)
    expect(lay.columns.filter((c) => c.phase === 'construction').map((c) => c.ids)).toEqual([
      ['functional-design/web'], ['code-generation/api', 'code-generation/web'], ['build-and-test', 'review'],
    ])
    expect(edgeKeys(lay)).toEqual(expect.arrayContaining(['units>functional-design/web', 'functional-design/web>code-generation/web', 'units>code-generation/api',
      'code-generation/web>build-and-test']))
    expect(edgeKeys(lay)).not.toContain('units>code-generation/web')
    expect(edgeKeys(lay)).not.toContain('functional-design/web>build-and-test')
    noOverlap(lay)
  })

  test('the way runs from the orchestrator through every box in order, the unit rows side by side, only between boxes', () => {
    const lay = stageLayout(pay9(), stageShape(1558, pay9()))
    expect(edgeKeys(lay)).toEqual([
      `${ORCH}>requirements`, 'requirements>application-design', 'application-design>units',
      'units>code-generation/api', 'units>code-generation/web', 'code-generation/api>build-and-test', 'code-generation/web>build-and-test',
      'build-and-test>review', 'review>release', 'release>reflect',
    ])
    // an edge between two columns leaves one box's right side and enters the other's left side
    const e = lay.edges.find((x) => x.from === 'units' && x.to === 'code-generation/api')!
    const [u, api] = [box(lay, 'units'), box(lay, 'code-generation/api')]
    expect(e.path[0][0]).toEqual({ x: u.x + NODE_W / 2, y: u.y })
    expect(e.path.at(-1)![3]).toEqual({ x: api.x - NODE_W / 2, y: api.y })
  })

  test('compact (BOARD-31): each band one column, 3 构建 unit by unit; an edge past boxes of its column goes round them', () => {
    const one = pay9()
    const nodes = one.nodes.map((n) => (n.id === 'functional-design/web' ? nd('functional-design/web', 'construction', 'merged', 'runner-1') : n))
    const g = { ...one, nodes }
    const shape = stageShape(CARD_1050, g)
    expect(shape.compact).toBe(true)
    const lay = stageLayout(g, shape)
    for (const ph of ['inception', 'construction', 'release', 'operations']) expect(lay.columns.filter((c) => c.phase === ph), ph).toHaveLength(1)
    expect(lay.columns.find((c) => c.phase === 'construction')!.ids).toEqual(['code-generation/api', 'functional-design/web', 'code-generation/web', 'build-and-test', 'review'])
    expect(lay.units).toEqual([])
    noOverlap(lay)
    // code-generation/api → build-and-test passes two boxes of the column: it runs down the column's right side
    const e = lay.edges.find((x) => x.from === 'code-generation/api' && x.to === 'build-and-test')!
    const col = lay.columns.find((c) => c.phase === 'construction')!
    const info = measure(e.path)
    for (let t = 0.05; t < 1; t += 0.05) {
      const p = pointAt(info, t)
      for (const b of lay.boxes.values()) {
        const inside = Math.abs(p.x - b.x) < NODE_W / 2 - 1 && Math.abs(p.y - b.y) < NODE_H / 2 - 1
        expect(inside, `${b.id} under the edge at ${t.toFixed(2)}`).toBe(false)
      }
    }
    expect(pointAt(info, 0.5).x).toBeGreaterThan(col.x + NODE_W / 2)
    // the edges between neighbours in the column run straight down
    const down = lay.edges.find((x) => x.from === 'build-and-test' && x.to === 'review')!
    expect(down.path).toHaveLength(1)
    expect(down.path[0][0].x).toBe(down.path[0][3].x)
  })

  test('the full layout at 1280 and 1600, the compact one at 1050; never drawn below SCALE; a way too wide even compact scrolls', () => {
    const g = pay9()
    expect(stageShape(null, g)).toEqual({ width: GW, compact: false })
    expect(stageShape(1558, g)).toEqual({ width: GW, compact: false })
    for (const [avail, compact] of [[CARD_1280, false], [CARD_1050, true]] as const) {
      const s = stageShape(avail, g)
      expect(s.compact, `${avail}`).toBe(compact)
      expect(Math.floor(s.width * SCALE)).toBeLessThanOrEqual(avail)
      noOverlap(stageLayout(g, s))
    }
    // inception with every stage, and each unit's four per-unit stages: nine columns
    const wide: StageInput = { ...g, nodes: [
      ...['reverse-engineering', 'requirements', 'user-stories', 'application-design', 'units'].map((s) => nd(s, 'inception')),
      ...['functional-design', 'nfr-design', 'infrastructure-design', 'code-generation'].flatMap((s) => ['api', 'web'].map((u) => nd(`${s}/${u}`, 'construction', 'merged', 'runner-1'))),
      nd('build-and-test', 'construction', 'merged', 'runner-1'), nd('review', 'construction', 'merged', 'runner-1'),
      nd('release', 'release', 'waiting', null), nd('reflect', 'operations', 'waiting', null),
    ] }
    expect(stageShape(1558, wide).compact).toBe(true)
    const full = stageLayout(wide, stageShape(null, wide))
    expect(full.width).toBeGreaterThan(GW)
    noOverlap(full)
    const narrow = stageShape(700, wide)
    expect(Math.floor(narrow.width * SCALE)).toBeGreaterThan(700)
    noOverlap(stageLayout(wide, narrow))
  })

  test('the rail: a dot per run under the middle of its boxes, the hand-over pill between the two runs, each clear of the next', () => {
    const lay = stageLayout(pay9(), stageShape(CARD_1280, pay9()))
    const local = lay.runX.get('local')!
    const runner = lay.runX.get('runner-1')!
    expect(local).toBe(box(lay, 'units').x)
    expect(runner).toBe(box(lay, 'build-and-test').x)
    expect(lay.relayX).toHaveLength(1)
    expect(lay.relayX[0] - 90).toBeGreaterThan(local + 11)
    expect(lay.relayX[0] + 90).toBeLessThan(runner - 11)
    expect(runner).toBeLessThan(lay.rail.x2)
    // handed over again: two runner runs, two pills, all in order and apart
    const again = pay9({ runs: [{ key: 'local' }, { key: 'runner-1' }, { key: 'runner-2' }], relays: [{ to: 'runner-1' }, { to: 'runner-2' }],
      nodes: pay9().nodes.map((n) => (n.id === 'release' ? nd('release', 'release', 'awaiting_approval', 'runner-2') : n)) })
    const two = stageLayout(again, stageShape(CARD_1280, again))
    const marks = [two.runX.get('local')!, two.relayX[0], two.runX.get('runner-1')!, two.relayX[1], two.runX.get('runner-2')!]
    expect(marks).toEqual([...marks].sort((p, q) => p - q))
    const half = [11, 90, 11, 90, 11]
    for (let i = 1; i < marks.length; i++) expect(marks[i] - half[i], `mark ${i}`).toBeGreaterThan(marks[i - 1] + half[i - 1])
    // a hand-over the bridge refused goes to nobody: its pill comes after the last dot
    const refused = stageLayout(pay9({ runs: [{ key: 'local' }], relays: [{ to: null }] }), stageShape(CARD_1280, pay9()))
    expect(refused.relayX[0]).toBeGreaterThan(refused.runX.get('local')!)
    // a hand-over's events blip on its pill; one the rail has no pill for, nowhere
    expect(stageAnchorBox(two, relayAnchor(1), 0)).toEqual({ x: two.relayX[1], y: two.rail.y, w: RELAY_W, h: 26, rx: 13 })
    expect(stageAnchorBox(two, relayAnchor(2), 0)).toBeNull()
  })

  test('the picture holds while only states change; a stage skipped, a unit, a run more, or a run’s first round on a box changes it', () => {
    const g = pay9()
    const key = stageKey(g)
    const same = { ...g, nodes: g.nodes.map((n) => (n.id === 'release' ? nd('release', 'release', 'merged', 'runner-1', 5) : n)) }
    expect(stageKey(same)).toBe(key)
    expect(stageLayout(same, stageShape(CARD_1280, same)).boxes).toEqual(stageLayout(g, stageShape(CARD_1280, g)).boxes)
    const skipped = { ...g, nodes: g.nodes.map((n) => (n.id === 'review' ? nd('review', 'construction', 'skipped', 'runner-1') : n)) }
    const unit = { ...g, nodes: [...g.nodes, nd('code-generation/jobs', 'construction', 'waiting', null)] }
    const run = { ...g, runs: [...g.runs, { key: 'runner-2' }] }
    const started = { ...g, nodes: g.nodes.map((n) => (n.id === 'reflect' ? nd('reflect', 'operations', 'running', 'local') : n)) }
    for (const other of [skipped, unit, run, started]) expect(stageKey(other)).not.toBe(key)
  })

  test('a command line starts at its run’s row in the list and ends in its box: from above into a column’s top box, else into its left side', () => {
    const lay = stageLayout(pay9(), stageShape(CARD_1280, pay9()))
    const row = lay.roster.find((r) => r.kind === 'run' && r.run === 'runner-1')!
    const rel = box(lay, 'release')
    const cmd = stageCommandPath(lay, 'runner-1', 'release')!
    expect(cmd.path[0][0]).toEqual({ x: lay.orch.x + ST_ORCH.w / 2, y: row.y })
    expect(cmd.path.at(-1)![3]).toEqual({ x: rel.x, y: rel.y - NODE_H / 2 })
    expect(cmd.bus.y).toBeLessThan(Math.min(...[...lay.boxes.values()].map((b) => b.y - NODE_H / 2)))
    const ad = box(lay, 'application-design')
    const local = stageCommandPath(lay, 'local', 'application-design')!
    expect(local.path.at(-1)![3]).toEqual({ x: ad.x - NODE_W / 2, y: ad.y })
    expect(stageCommandPath(lay, 'runner-1', 'ci-pipeline')).toBeNull()
  })
})
