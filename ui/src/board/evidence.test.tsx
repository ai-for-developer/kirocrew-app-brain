import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import world from '../../preview/world.json'
import { clone, fx } from '../test/data'
import { API, installFixtureRoutes } from '../test/routes'
import { apiCalls, StubApiError } from '../test/stubs/app-sdk'
import { worldBoard } from '../test/world'
import type { Evidence, FileView, NodeOutput, OutFile, ReqBoard, RequirementDetail } from '../types'
import { DetailView } from '../views/Detail'
import type { ViewProps } from '../views/props'
import { Accept } from './Accept'
import { BoardPage } from './BoardPage'

/** The preview's 产出 answer for a GET /outputs/… path, its query sorted as preview_world keys it. */
function worldOutput(path: string): unknown {
  const [route, query = ''] = path.slice(API.length).split('?')
  const q = [...new URLSearchParams(query)].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)).filter(([, v]) => v).map(([k, v]) => `${k}=${v}`).join('&')
  const got = (world as unknown as { outputs: Record<string, unknown> }).outputs[`${route}?${q}`]
  if (got === undefined) throw new StubApiError(404, { error: 'not in the world', code: 'not_in_preview' })
  return clone(got)
}

const props = (): ViewProps => ({ status: fx.status(), go: vi.fn(), replace: vi.fn(), reloadStatus: vi.fn(), setStatus: vi.fn(), pollMs: 60_000 })
const chipOf = (b: ReqBoard, label: string) => b.proof.chips.find((c) => c.label === label)!
const TOO_OLD: Evidence = { available: false, state: null, line: '', late: '', at: null, chips: [], gaps: [],
  error: '这个 brain-kit 还不会读证据（它太旧）：在设置里换新一点的 brain-kit' }

describe('证据 on the 验收 card', () => {
  test('PAY-9 waits for its acceptance: what verify left in brain-kit’s words, a chip per plan row and test output, what is missing', () => {
    const b = worldBoard('r1/PAY-9')
    const onOutputs = vi.fn()
    render(<Accept board={b} onOutputs={onOutputs} />)
    const row = screen.getByTestId('evidence')
    expect(within(row).getByTestId('evidence-line')).toHaveTextContent('截图 3 张（1 张没看到） · 测试输出 1 份 · 是这份代码的')
    const chips = within(row).getAllByTestId('evidence-chip')
    expect(chips.map((c) => c.textContent)).toEqual(['P-1AC-4✓', 'P-2AC-2✓', 'P-3AC-4没看到', 'P-4AC-5还没拍', '测试输出 tests✓'])
    // P-4 is in the plan and not shot yet: nothing to open
    expect(chips.map((c) => c.tagName)).toEqual(['BUTTON', 'BUTTON', 'BUTTON', 'SPAN', 'BUTTON'])
    fireEvent.click(chips[2])
    expect(onOutputs).toHaveBeenCalledWith({ node: 'build-and-test', round: 5, file: chipOf(b, 'P-3').path })
    expect(chipOf(b, 'P-3').path).toMatch(/\/proof\/[^/]+\/ui\/web-P-3\.png$/)
    expect(within(screen.getByRole('list', { name: '还缺什么' })).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'P-3 没看到要的：没等到「短信没发出去」（10 秒）', 'P-4 在证据计划里，还没被 lean verify 拍过（新加的，或者改过步骤）'])
  })

  test('a project whose verify keeps no evidence: brain-kit’s sentence for it, no chips', () => {
    render(<Accept board={worldBoard('r1/PAY-2')} />)
    expect(screen.getByTestId('evidence-line')).toHaveTextContent('不留证据')
    expect(screen.queryAllByTestId('evidence-chip')).toHaveLength(0)
    expect(screen.getByRole('list', { name: '还缺什么' })).toHaveTextContent(/^这个项目的 lean verify 不留证据：/)
  })

  test('a brain-kit too old for evidence: the row says so, and the card is all there', () => {
    const b = worldBoard('r1/PAY-9')
    b.proof = TOO_OLD
    render(<Accept board={b} />)
    expect(screen.getByTestId('evidence-none')).toHaveTextContent(TOO_OLD.error)
    expect(screen.getByTestId('accept-checks')).toHaveTextContent(/\S/)
  })

  test('回放 does not list it: evidence is judged against the commit now', () => {
    render(<Accept board={worldBoard('r1/PAY-9')} quiet />)
    expect(screen.queryByTestId('evidence')).toBeNull()
    expect(screen.getByText('回放时不列：证据按现在的提交核对')).toBeInTheDocument()
  })

  test('the newest verify took no screenshot: the row says whose are shown', () => {
    const b = worldBoard('r1/PAY-9')
    b.proof.late = '最近一次 verify 没拍截图（没有 Chrome（设 CHROME））：显示的是第 2 轮拍的'
    render(<Accept board={b} />)
    expect(screen.getByTestId('evidence')).toHaveTextContent(b.proof.late)
  })
})

describe('证据 in the 产出 drawer', () => {
  test('the round that sealed it lists each file by its plan row and how it went, and draws the screenshot as a picture', async () => {
    const b = worldBoard('r1/PAY-9')
    const p3 = chipOf(b, 'P-3')
    installFixtureRoutes({ [`GET ${API}/board/*`]: () => b, [`GET ${API}/outputs/*`]: (_b, path) => worldOutput(path) })
    render(<BoardPage entry={{ repo: 'r1', key: 'PAY-9', out: { node: 'build-and-test', round: p3.round!, file: p3.path! } }} {...props()} />)
    const pane = await screen.findByTestId('out-pane')
    const rows = await within(pane).findAllByTestId('out-file')
    expect(rows.map((f) => f.textContent).slice(0, 6)).toEqual(['Mconstruction/build-and-test/build-and-test-summary.md+2 −2', 'M命令输出 tests：pytest -q',
      'M截图', 'MP-1 · AC-4 · 退款详情页显示「短信已发送」✓', 'MP-2 · AC-2 · 短信内容里有退款金额和预计到账时间✓', 'MP-3 · AC-4 · 发送失败的退款显示「短信没发出去」没看到'])
    const img = await within(pane).findByTestId('out-image')
    expect(img.getAttribute('src')).toMatch(/^data:image\/png;base64,iVBORw0KGgo/)
    expect(img).toHaveAccessibleName('P-3 · AC-4 · 发送失败的退款显示「短信没发出去」')
    expect(within(pane).getByTestId('out-proof')).toHaveTextContent('P-3 · AC-4 · 发送失败的退款显示「短信没发出去」 没看到 · 没等到「短信没发出去」（10 秒）')
    const asked = apiCalls.filter((c) => c.path.includes('/outputs/r1/PAY-9/file?')).map((c) => new URLSearchParams(c.path.split('?')[1]))
    expect(asked.map((q) => [q.get('path'), q.get('view')])).toEqual([[p3.path, 'image']])
  })

  test('a picture is drawn only from the backend’s data: URL of a PNG or a JPEG', async () => {
    const shot = 'aidlc-docs/261118-x/construction/build-and-test/proof/c-1/ui/app-P-1.png'
    const file: OutFile = { path: shot, output: true, status: 'A', added: 0, removed: 0, ref: 'a'.repeat(40), pin: null, base: null, base_round: null,
      vs: [], why: null, proof: { set: 'ui', label: 'P-1 · AC-1 · 有退款按钮', result: 'ok', mark: '', note: '' } }
    const listed: NodeOutput = { node: 'build-and-test' as NodeOutput['node'], round: 5, run: 'runner-1' as NodeOutput['run'], branch: 'b', head: null,
      files: [file], more: 0, why: null }
    const view: FileView = { path: shot, view: 'image', at: 'a'.repeat(40), base: null, pinned: false, lang: 'text', asked: null, binary: true,
      truncated: false, size: 9, text: '', image: 'data:image/svg+xml;base64,PHN2Zy8+' }
    installFixtureRoutes({
      [`GET ${API}/board/*`]: () => worldBoard('r1/PAY-9'),
      [`GET ${API}/outputs/*`]: (_b, path) => (path.includes('/file?') ? view : path.includes('/turns?') ? worldOutput(path) : listed),
    })
    render(<BoardPage entry={{ repo: 'r1', key: 'PAY-9', out: { node: 'build-and-test', round: 5, file: shot } }} {...props()} />)
    const pane = await screen.findByTestId('out-pane')
    expect(await within(pane).findByText('二进制文件：这里不显示')).toBeInTheDocument()
    expect(within(pane).queryByTestId('out-image')).toBeNull()
  })
})

describe('证据 on 需求现状', () => {
  const detailOf = (key: string) => clone((world as unknown as { details: Record<string, RequirementDetail> }).details[key])

  test('waiting for its acceptance, 此刻 says what verify left, and a chip opens 运行过程 at that file', async () => {
    const b = worldBoard('r1/PAY-9')
    installFixtureRoutes({ [`GET ${API}/requirements/*`]: () => detailOf('r1/PAY-9'), [`GET ${API}/board/*`]: () => b })
    const p = props()
    render(<DetailView entry={{ repo: 'r1', key: 'PAY-9' }} {...p} />)
    const proof = await screen.findByTestId('now-proof')
    expect(within(screen.getByRole('region', { name: '此刻' })).getByTestId('now-proof')).toBe(proof)
    expect(within(proof).getByTestId('evidence-line')).toHaveTextContent('截图 3 张（1 张没看到） · 测试输出 1 份 · 是这份代码的')
    fireEvent.click(within(proof).getAllByTestId('evidence-chip')[0])
    expect(p.go).toHaveBeenCalledWith({ kind: 'board', repo: 'r1', key: 'PAY-9', from: 'item',
      out: { node: 'build-and-test', round: 5, file: chipOf(b, 'P-1').path } })
  })

  test('not at its acceptance, 此刻 has no 证据: the board keeps it on its 验收 card', async () => {
    installFixtureRoutes()
    render(<DetailView entry={{ repo: 'r1', key: 'PAY-3' }} {...props()} />)
    await screen.findByRole('region', { name: '此刻' })
    expect(fx.board().accept.state).not.toBe('waiting')
    expect(screen.queryByTestId('now-proof')).toBeNull()
  })
})

test('a command’s whole output opens as it printed it, not as its change from the verify before', async () => {
  const b = worldBoard('r1/PAY-9')
  const log = chipOf(b, '测试输出 tests')
  installFixtureRoutes({ [`GET ${API}/board/*`]: () => b, [`GET ${API}/outputs/*`]: (_b, path) => worldOutput(path) })
  render(<BoardPage entry={{ repo: 'r1', key: 'PAY-9', out: { node: 'build-and-test', round: log.round!, file: log.path! } }} {...props()} />)
  const pane = await screen.findByTestId('out-pane')
  expect(await within(pane).findByTestId('out-code')).toHaveTextContent('11 passed')
  expect(within(pane).getByRole('button', { name: '源码' })).toHaveAttribute('aria-pressed', 'true')
  expect(within(pane).getByTestId('out-proof')).toHaveTextContent('命令输出 tests：pytest -q')
})
