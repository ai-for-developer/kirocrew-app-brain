// npm run fit: every view of the preview build (preview-dist/index.html) in headless Chrome at three window widths,
// with KiroCrew's sidebar taken off the left the way KiroCrew does (the app gets the window minus 230 px, media queries
// still see the window), and PAY-9 again with a long title. It fails when the page scrolls sideways, anything sits
// past the app's right edge outside a box that scrolls, or a box cuts off what is in it. Chrome talks over
// --remote-debugging-pipe: no port is opened.
// CHROME=<path> picks the browser; FIT_SHOTS=<dir> also writes a first-screen shot of each view there.
import { spawn } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const CHROME = process.env.CHROME ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const DIST = fileURLToPath(new URL('../preview-dist/', import.meta.url))
const WIDTHS = [1280, 1050, 860]
const SIDEBAR = 230
const LONG = '退款成功以后给用户发短信通知，旧订单没有手机号的改发站内信，并且在订单详情页显示退款进度和预计到账时间'
const VIEWS = [
  ['交付总览', 'view=overview'],
  ['交付总览 r1', 'view=overview&repo=r1'],
  ['需求现状 PAY-9', 'view=item&repo=r1&key=PAY-9'],
  ['需求现状 PAY-11', 'view=item&repo=r1&key=PAY-11'],
  ['需求现状 PAY-17', 'view=item&repo=r1&key=PAY-17'],
  ['运行过程 PAY-9', 'view=board&repo=r1&key=PAY-9'],
  ['运行过程 PAY-2', 'view=board&repo=r1&key=PAY-2'],
  ['运行过程 PAY-11', 'view=board&repo=r1&key=PAY-11'],
  ['运行过程 PAY-18', 'view=board&repo=r1&key=PAY-18'],
  ['运行过程 PAY-9 产出', 'view=board&repo=r1&key=PAY-9&out=release&round=3'],
  ['开需求', 'view=new&repo=r1'],
  ['开需求 r3', 'view=new&repo=r3'],
  ['Brain', 'view=brain&repo=r1'],
  ['待办', 'view=todo'],
  ['设置', 'view=settings'],
  ['导入仓库', 'view=import'],
  ['团队', 'view=team', 'light,team'],
]
const LONG_VIEWS = [['运行过程 PAY-9 长标题', 'view=board&repo=r1&key=PAY-9'], ['需求现状 PAY-9 长标题', 'view=item&repo=r1&key=PAY-9']]

// boxes that scroll sideways by design, as on the reference board: the commit belt, the stage graph, 产出's steps;
// and text kept for screen readers only
const SCROLLS = '.kt-belt, .br-graph-scroll, .kt-steps, .br-sr'
// The preview moves the fixture's times to now, so what fits depends on the hour fit runs at. The page's clock starts
// at 00:05 today: most rounds then started the day before and their chips carry a date, the widest they get.
const CLOCK = `(() => { const D = Date, S = D.now(), T = ${new Date().setHours(0, 5, 0, 0)}
  class F extends D { constructor(...a) { super(...(a.length ? a : [T + D.now() - S])) } static now() { return T + D.now() - S } }
  window.Date = F })()`
const FIT = `(() => {
  document.body.style.cssText += ';margin-left:${SIDEBAR}px;width:calc(100vw - ${SIDEBAR}px);overflow-x:hidden'
  const settled = (since) => new Promise((go) => {
    const look = () => (!document.querySelector('.br-loading') && document.querySelector('.br-app')) || Date.now() - since > 15000 ? setTimeout(go, 600) : setTimeout(look, 200)
    look()
  })
  return settled(Date.now()).then(() => new Promise((done) => {
    if (document.querySelector('.br-loading')) return done({ sideways: 0, past: ['still 正在读取… after 15 s'] })
    const app = document.querySelector('.br-app') || document.body
    const edge = Math.min(innerWidth, app.getBoundingClientRect().right)
    const scrolls = (e) => { for (let p = e.parentElement; p && p !== document.body; p = p.parentElement) if (/auto|scroll/.test(getComputedStyle(p).overflowX)) return true; return false }
    const shown = (e) => { for (let p = e; p; p = p.parentElement) { const s = getComputedStyle(p); if (s.display === 'none' || s.visibility === 'hidden') return false } return !e.closest('details:not([open]) > :not(summary)') }
    const name = (e) => e.tagName.toLowerCase() + [...e.classList].slice(0, 2).map((c) => '.' + c).join('')
    const past = [...app.querySelectorAll('*')].filter((e) => { const r = e.getBoundingClientRect(); return r.width && r.height && r.right > edge + 1 && !scrolls(e) && shown(e) })
      .slice(0, 5).map((e) => name(e) + ' right=' + Math.round(e.getBoundingClientRect().right) + ' «' + e.textContent.trim().replace(/\\s+/g, ' ').slice(0, 24) + '»')
    const SCROLLS = ${JSON.stringify(SCROLLS)}
    const cut = [...app.querySelectorAll('*')].filter((e) => { const s = getComputedStyle(e); return /hidden|clip|auto|scroll/.test(s.overflowX) && s.textOverflow !== 'ellipsis' && s.webkitLineClamp === 'none' && e.scrollWidth > e.clientWidth + 1 && e.clientWidth > 1 && !e.matches(SCROLLS) && shown(e) })
      .slice(0, 5).map((e) => name(e) + ' shows ' + e.clientWidth + ' of ' + e.scrollWidth + ' px «' + e.textContent.trim().replace(/\\s+/g, ' ').slice(0, 24) + '»')
    done({ sideways: document.documentElement.scrollWidth - innerWidth, past, cut })
  }))
})()`

if (!existsSync(CHROME)) {
  console.error(`no Chrome at ${CHROME}: set CHROME to its path`)
  process.exit(2)
}
const index = readFileSync(join(DIST, 'index.html'), 'utf8')
const scratch = mkdtempSync(join(tmpdir(), 'fit-'))
writeFileSync(join(scratch, 'long.html'), index.replaceAll('"title":"退款通知短信"', `"title":"${LONG}"`))

const chrome = spawn(CHROME, ['--headless=new', '--remote-debugging-pipe', '--no-first-run', '--no-default-browser-check',
  `--user-data-dir=${join(scratch, 'profile')}`, '--hide-scrollbars', '--force-device-scale-factor=1', 'about:blank'],
{ stdio: ['ignore', 'ignore', 'ignore', 'pipe', 'pipe'] })
const [, , , toChrome, fromChrome] = chrome.stdio
const pending = new Map()
let id = 0
let buf = ''
fromChrome.on('data', (d) => {
  buf += d
  for (let i = buf.indexOf('\0'); i >= 0; i = buf.indexOf('\0')) {
    const msg = JSON.parse(buf.slice(0, i))
    buf = buf.slice(i + 1)
    pending.get(msg.id)?.(msg)
    pending.delete(msg.id)
  }
})
const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
  const m = { id: ++id, method, params, ...(sessionId ? { sessionId } : {}) }
  pending.set(m.id, (x) => (x.error ? reject(new Error(`${method}: ${JSON.stringify(x.error)}`)) : resolve(x.result)))
  toChrome.write(JSON.stringify(m) + '\0')
})
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const runs = [...VIEWS.map(([n, q, pv]) => [n, join(DIST, 'index.html'), q, pv]), ...LONG_VIEWS.map(([n, q]) => [n, join(scratch, 'long.html'), q])]
async function atWidth(width) {
  const { targetId } = await send('Target.createTarget', { url: 'about:blank' })
  const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true })
  const page = (m, p) => send(m, p, sessionId)
  await page('Page.enable')
  await page('Page.addScriptToEvaluateOnNewDocument', { source: CLOCK })
  await page('Emulation.setDeviceMetricsOverride', { width, height: 1000, deviceScaleFactor: 1, mobile: false })
  const lines = []
  for (const [label, file, q, pv = 'light'] of runs) {
    await page('Page.navigate', { url: `file://${file}?pv=${pv}&${q}` })
    await sleep(1500)
    const r = await page('Runtime.evaluate', { expression: FIT, returnByValue: true, awaitPromise: true })
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description ?? r.exceptionDetails.text)
    const { sideways, past, cut = [] } = r.result.value
    const ok = sideways <= 0 && !past.length && !cut.length
    lines.push({ ok, text: `${ok ? 'ok  ' : 'FAIL'} ${width} ${label}${sideways > 0 ? ` · the page scrolls sideways by ${sideways} px` : ''}` })
    for (const p of past) lines.push({ text: `       past the edge: ${p}` })
    for (const c of cut) lines.push({ text: `       cut off inside its box: ${c}` })
    if (process.env.FIT_SHOTS) {
      const shot = await page('Page.captureScreenshot', { format: 'png' })
      writeFileSync(join(process.env.FIT_SHOTS, `${label.replaceAll(' ', '-')}-${width}.png`), Buffer.from(shot.data, 'base64'))
    }
  }
  await send('Target.closeTarget', { targetId })
  return lines
}

let bad = 0
try {
  // one width after another: a tab behind another one never finishes loading
  for (const width of WIDTHS) {
    for (const { ok, text } of await atWidth(width)) {
      if (ok === false) bad++
      console.log(text)
    }
  }
} finally {
  const gone = new Promise((r) => chrome.once('exit', r))
  chrome.kill()
  await gone
  rmSync(scratch, { recursive: true, force: true })
}
console.log(bad ? `${bad} view(s) do not fit` : 'every view fits')
process.exit(bad ? 1 : 0)
