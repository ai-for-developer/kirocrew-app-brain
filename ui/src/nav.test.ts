import { describe, expect, test } from 'vitest'
import { fileOfEntry, parseView, searchOf, type View } from './nav'

describe('nav: the URL of every view', () => {
  test('each view survives a round trip through its search string', () => {
    const views: View[] = [
      { kind: 'overview' },
      { kind: 'overview', repo: 'r1', state: 'parked' },
      { kind: 'item', repo: 'r1', key: 'PAY-3' },
      { kind: 'item', repo: 'runner', key: 'PAY-4', from: 'todo' },
      { kind: 'item', repo: 'r1', key: 'PAY-3', from: 'board' },
      { kind: 'board', repo: 'r1', key: 'PAY-3' },
      { kind: 'board', repo: 'r2', key: 'PAY-2', replay: true, focus: 'accept', from: 'overview' },
      { kind: 'board', repo: 'runner', key: 'PAY-4', focus: 'feed', from: 'todo' },
      { kind: 'board', repo: 'r1', key: 'PAY-5', replay: true, from: 'item' },
      { kind: 'new', repo: 'r2' },
      { kind: 'new' },
      { kind: 'brain', repo: 'r1', file: 'TECH', id: 'T-PAY-2-1', reflect: 'PAY-5' },
      { kind: 'brain', id: 'P-7' },
      { kind: 'todo' },
      { kind: 'team' },
      { kind: 'settings' },
      { kind: 'import' },
      { kind: 'import', path: '/Users/alice/workplace/shop pay' },
      { kind: 'import', path: '~/workplace/refund' },
      { kind: 'item', repo: 'runner', key: 'PAY-8', from: 'team' },
      { kind: 'item', repo: 'r1', key: 'PAY-4', from: 'overview', decision: 'D-3' },
      { kind: 'board', repo: 'r1', key: 'PAY-2', from: 'item', at: 1793631120 },
    ]
    for (const v of views) expect(parseView(searchOf(v))).toEqual(v)
  })

  test('导入仓库 takes an absolute path only (it fills the box, nothing runs)', () => {
    expect(parseView('?view=import&path=relative/x')).toEqual({ kind: 'import' })
    expect(parseView('?view=import&path=%2Fx%0Aapprove')).toEqual({ kind: 'import' })
    expect(searchOf({ kind: 'import', path: 'relative' })).toBe('?view=import')
  })

  test('the default view has a clean URL; filters and entries are spelled out', () => {
    expect(searchOf({ kind: 'overview' })).toBe('')
    expect(parseView('')).toEqual({ kind: 'overview' })
    expect(searchOf({ kind: 'overview', state: 'local' })).toBe('?view=overview&state=local')
    expect(searchOf({ kind: 'item', repo: 'r1', key: 'PAY-3', from: 'brain' })).toBe('?view=item&repo=r1&key=PAY-3&from=brain')
    expect(searchOf({ kind: 'brain', repo: 'r1', id: 'T-12' })).toBe('?view=brain&repo=r1&id=T-12')
  })

  test('anything that does not match its pattern is left out', () => {
    expect(parseView('?view=item&repo=r1&key=../etc')).toEqual({ kind: 'overview' })
    expect(parseView('?view=item&repo=x1&key=PAY-3')).toEqual({ kind: 'overview' })
    expect(parseView('?view=item&repo=r1&key=PAY-3&from=evil')).toEqual({ kind: 'item', repo: 'r1', key: 'PAY-3' })
    expect(parseView('?view=overview&repo=all&state=nope')).toEqual({ kind: 'overview' })
    expect(parseView('?view=brain&repo=r1&file=SECRETS&id=<b>&reflect=pay-3')).toEqual({ kind: 'brain', repo: 'r1' })
    expect(parseView('?view=nothing')).toEqual({ kind: 'overview' })
    expect(parseView('?view=item&repo=r1&key=PAY-4&decision=D-3%22%3E')).toEqual({ kind: 'item', repo: 'r1', key: 'PAY-4' })
    expect(searchOf({ kind: 'item', repo: 'r1', key: 'PAY-4', decision: 'x' })).toBe('?view=item&repo=r1&key=PAY-4')
  })

  test('运行过程: repo and key or nothing; replay only as 1; a focus and a from of its own, else left out', () => {
    expect(searchOf({ kind: 'board', repo: 'r1', key: 'PAY-2', replay: true, focus: 'checks', from: 'overview' }))
      .toBe('?view=board&repo=r1&key=PAY-2&replay=1&focus=checks&from=overview')
    expect(searchOf({ kind: 'board', repo: 'r1', key: 'PAY-2', replay: false })).toBe('?view=board&repo=r1&key=PAY-2')
    expect(parseView('?view=board&repo=r1&key=PAY-2&replay=1&focus=lanes&from=item'))
      .toEqual({ kind: 'board', repo: 'r1', key: 'PAY-2', replay: true, focus: 'lanes', from: 'item' })
    // without a repo or a key there is no board to show
    expect(parseView('?view=board&repo=r1')).toEqual({ kind: 'overview' })
    expect(parseView('?view=board&key=PAY-2')).toEqual({ kind: 'overview' })
    expect(parseView('?view=board&repo=x1&key=PAY-2')).toEqual({ kind: 'overview' })
    expect(parseView('?view=board&repo=r1&key=pay-2')).toEqual({ kind: 'overview' })
    // replay is 1 or absent; a focus or a from it does not know is dropped (brain and board are 需求现状's, not its)
    const bare = { kind: 'board', repo: 'r1', key: 'PAY-2' }
    expect(parseView('?view=board&repo=r1&key=PAY-2')).toEqual(bare)
    expect(parseView('?view=board&repo=r1&key=PAY-2&replay=true')).toEqual(bare)
    expect(parseView('?view=board&repo=r1&key=PAY-2&replay=0')).toEqual(bare)
    expect(parseView('?view=board&repo=r1&key=PAY-2&focus=graph')).toEqual(bare)
    expect(parseView('?view=board&repo=r1&key=PAY-2&from=brain')).toEqual(bare)
    expect(parseView('?view=board&repo=r1&key=PAY-2&from=board')).toEqual(bare)
    expect(parseView('?view=board&repo=r1&key=PAY-2&from=evil&focus=%3Cb%3E&replay=yes')).toEqual(bare)
  })

  test('运行过程’s 产出 drawer: a stage, a round and a file of its own, else left out', () => {
    const v: View = { kind: 'board', repo: 'r1', key: 'PAY-9', out: { node: 'code-generation/api', round: 2, file: 'services/api/notify.py' } }
    expect(parseView(searchOf(v))).toEqual(v)
    const bad = parseView('?view=board&repo=r1&key=PAY-9&out=release&round=0&file=../etc/passwd')
    expect(bad).toEqual({ kind: 'board', repo: 'r1', key: 'PAY-9', out: { node: 'release' } })
    for (const file of ['/etc/passwd', 'a//b', 'a\\b', './x']) expect(parseView(`?view=board&repo=r1&key=PAY-9&out=release&file=${encodeURIComponent(file)}`)).toEqual(bad)
    expect(parseView('?view=board&repo=r1&key=PAY-9&out=Release%3Cx%3E&round=2')).toEqual({ kind: 'board', repo: 'r1', key: 'PAY-9' })
  })

  test('the drawer’s tab: 它的回复, 接到的任务 or 做了什么, else 产出文件', () => {
    for (const tab of ['reply', 'task', 'calls'] as const) {
      const v: View = { kind: 'board', repo: 'r1', key: 'PAY-9', out: { node: 'application-design', round: 1, tab } }
      expect(parseView(searchOf(v))).toEqual(v)
    }
    expect(parseView('?view=board&repo=r1&key=PAY-9&out=release&tab=files')).toEqual({ kind: 'board', repo: 'r1', key: 'PAY-9', out: { node: 'release' } })
    expect(parseView('?view=board&repo=r1&key=PAY-9&tab=reply')).toEqual({ kind: 'board', repo: 'r1', key: 'PAY-9' })
  })

  test('a fast run’s 产出 by commit: 7 to 40 lowercase hex with out, its file kept; else left out', () => {
    const v: View = { kind: 'board', repo: 'r1', key: 'PAY-17', out: { node: 'code', commit: 'c2c2c2c2c2c2', file: 'src/pay/order.py' } }
    expect(searchOf(v)).toBe('?view=board&repo=r1&key=PAY-17&out=code&file=src%2Fpay%2Forder.py&commit=c2c2c2c2c2c2')
    expect(parseView(searchOf(v))).toEqual(v)
    expect(parseView(`?view=board&repo=r1&key=PAY-17&out=code&commit=${'f'.repeat(40)}`)).toEqual({ kind: 'board', repo: 'r1', key: 'PAY-17', out: { node: 'code', commit: 'f'.repeat(40) } })
    const all = { kind: 'board', repo: 'r1', key: 'PAY-17', out: { node: 'code' } }
    for (const commit of ['c2c2c2', 'C2C2C2C2', 'c2c2c2g', 'a'.repeat(41), 'c2c2c2c%0A']) expect(parseView(`?view=board&repo=r1&key=PAY-17&out=code&commit=${commit}`)).toEqual(all)
    expect(parseView('?view=board&repo=r1&key=PAY-17&commit=c2c2c2c2')).toEqual({ kind: 'board', repo: 'r1', key: 'PAY-17' })
    expect(searchOf({ kind: 'board', repo: 'r1', key: 'PAY-17', out: { node: 'code', commit: 'HEAD~1' } })).toBe('?view=board&repo=r1&key=PAY-17&out=code')
  })

  test('an entry id says its Brain file by its side letter', () => {
    expect(fileOfEntry('P-PAY-2-1')).toBe('PRODUCT')
    expect(fileOfEntry('T-12')).toBe('TECH')
    expect(fileOfEntry('I-PAY-2-1')).toBe('IMPROVEMENT')
    expect(fileOfEntry('J-3')).toBe('PROJECT')
    expect(fileOfEntry('X-1')).toBeUndefined()
  })
})
