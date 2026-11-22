import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'
import { fx } from '../test/data'
import { API, installFixtureRoutes } from '../test/routes'
import { navigations } from '../test/stubs/app-sdk'
import type { StagingError } from '../types'
import type { ViewProps } from './props'
import { STAGING_OFF, STAGING_ROWS, STAGING_STATE, stagingMeta } from './StagingErrors'
import { TeamPage } from './Team'

function teamStatus() {
  const s = fx.status()
  s.app.team = true
  s.app.role = 'team_server'
  s.team = fx.team().status
  return s
}
const props = (over: Partial<ViewProps> = {}): ViewProps => ({
  status: teamStatus(), go: vi.fn(), replace: vi.fn(), reloadStatus: vi.fn(), setStatus: vi.fn(), pollMs: 60_000, ...over,
})
const GITLAB = 'https://gitlab.example.com/shop/shop-pay/-'

describe('测试环境报错', () => {
  test('each error of the test environment: signature, count, state in words, its Issue, the MR that brought it in and the fix, the ones at work first', async () => {
    const user = userEvent.setup()
    installFixtureRoutes()
    render(<TeamPage {...props()} />)
    const st = await screen.findByRole('region', { name: '测试环境报错' })
    const row = (signature: string) => within(st).getByText(signature).closest('tr') as HTMLElement
    expect(st).toHaveTextContent('shop/shop-pay：测试环境日志里的报错，运行机开 Issue、开分支修、开 MR，人来审、来合并，同时最多修 2 个')
    expect(st).toHaveTextContent('在修 1/2')
    expect(Array.from(st.querySelectorAll('tbody tr')).map((tr) => tr.getAttribute('data-state'))).toEqual(
      ['fixing', 'fix_mr', 'issued', 'new', 'stopped', 'dismissed', 'closed'])
    const zero = row('ZeroDivisionError at orders/discount.py in apply')
    expect(within(zero).getByText('26')).toHaveClass('tm-st-count')
    expect(within(zero).getByText('开了修复 MR，等人审')).toHaveClass('tone-wait')
    expect(within(zero).getByRole('link', { name: '#12' })).toHaveAttribute('href', `${GITLAB}/issues/12`)
    expect(within(zero).getByRole('link', { name: '!41' })).toHaveAttribute('href', `${GITLAB}/merge_requests/41`)
    expect(within(zero).getByRole('link', { name: '!41' }).closest('span')).toHaveAttribute('title', '折扣按百分比算')
    expect(within(zero).getByRole('link', { name: '!53' })).toHaveAttribute('href', `${GITLAB}/merge_requests/53`)
    expect(within(zero).getByRole('link', { name: '!53' }).closest('span')).toHaveTextContent(/^修复 !53$/)
    const gateway = row('app.pay: gateway timeout after <n>s (order <n>)')
    expect(within(gateway).getByText('在修')).toHaveClass('tone-run', 'is-live')
    expect(within(gateway).queryByRole('link', { name: /修复/ })).toBeNull()
    expect(row('HTTP 502 GET /api/orders/<n>/receipt')).toHaveTextContent('开了 Issue，等修排队：同时最多 2 个会话在修（MR 巡检的也算）')
    expect(row('KeyError at cart/views.py in add')).toHaveTextContent('交给人了试了 2 次没修好（最后一次：30 分钟没做完，停掉了），在 Issue 上说了：交给人')
    const fresh = row('app.stock: warehouse <n> is gone')
    expect(fresh).toHaveTextContent('刚看到这一轮没做成：POST /projects/shop%2Fshop-pay/issues -> HTTP 502: Bad Gateway')
    expect(within(fresh).queryAllByRole('link')).toHaveLength(0)
    expect(within(row('shop.errors.RefundError at shop/refunds.py in refund')).getByText('已关')).toHaveClass('tone-ok')
    const left = row('app.mail: SMTP relay refused <email>')
    expect(within(left).getByText('人关了，不修')).toHaveClass('tone-idle')
    expect(left).toHaveTextContent('不会再打开，也不修；新的部署里又出现，在 Issue 上说一次')
    expect(within(left).getByRole('link', { name: '#5' })).toHaveAttribute('href', `${GITLAB}/issues/5`)
    await user.click(within(gateway).getByRole('button', { name: '打开会话' }))
    expect(navigations).toEqual(['/chat?sid=staging-12-13'])
    expect(within(fresh).queryByRole('button')).toBeNull()
    // every state staging.py answers has words; another one is shown as it is
    expect(Object.keys(STAGING_STATE).sort()).toEqual(['closed', 'dismissed', 'fix_mr', 'fixing', 'issued', 'new', 'stopped'])
    expect(stagingMeta('later')).toEqual({ label: 'later', tone: 'idle' })
  })

  test('counts every session it holds: a fix whose MR is open while its turn runs is said under the count', async () => {
    const sv = fx.teamStaging()
    sv.working = 2
    sv.hidden = ['shop/shop-pay#12 的修复 MR !53 开了，会话还在收尾']
    installFixtureRoutes({ [`GET ${API}/team/staging`]: () => sv })
    render(<TeamPage {...props()} />)
    const st = await screen.findByRole('region', { name: '测试环境报错' })
    expect(st).toHaveTextContent('在修 2/2')
    expect(within(st).getByTestId('held-hint')).toHaveTextContent('在修的里有 1 个下面没列出：shop/shop-pay#12 的修复 MR !53 开了，会话还在收尾')
  })

  test('off: says how to switch it on; a project it could not read says so; a long list stays short', async () => {
    const off = fx.teamStaging()
    off.on = false
    off.items = []
    off.projects = [{ project: 'shop/shop-pay', on: false, error: '', deployed_sha: '' }]
    installFixtureRoutes({ [`GET ${API}/team/staging`]: () => off })
    const { unmount } = render(<TeamPage {...props()} />)
    const st = await screen.findByRole('region', { name: '测试环境报错' })
    expect(st).toHaveTextContent(STAGING_OFF)
    expect(within(st).queryByRole('table')).toBeNull()
    unmount()
    const long = fx.teamStaging()
    const closed = long.items.find((i) => i.state === 'closed') as StagingError
    long.items = [...long.items, ...[1, 2, 3, 4].map((n) => ({ ...closed, signature: `OldError${n} at old.py in f`, last_seen: closed.last_seen - n }))]
    long.projects[0].error = 'staging.log：[Errno 2] No such file or directory'
    installFixtureRoutes({ [`GET ${API}/team/staging`]: () => long })
    render(<TeamPage {...props()} />)
    const again = await screen.findByRole('region', { name: '测试环境报错' })
    expect(again.querySelectorAll('tbody tr')).toHaveLength(STAGING_ROWS)
    expect(again).toHaveTextContent('还有 3 个没列出：已关的和早先的。')
    expect(within(again).queryByText('OldError4 at old.py in f')).toBeNull()
    expect(within(again).getByRole('alert')).toHaveTextContent('shop/shop-pay 这一轮没看成测试环境：staging.log：[Errno 2] No such file or directory')
  })

  test('现在跑一轮 says in words that it looked at the test environment', async () => {
    const user = userEvent.setup()
    installFixtureRoutes({ [`POST ${API}/team/loop`]: () => ({ ok: true, summary: { poll: 0, patrol: 1, staging: 1 }, error: '' }) })
    render(<TeamPage {...props()} />)
    await screen.findByRole('region', { name: '测试环境报错' })
    await user.click(screen.getByRole('button', { name: '现在跑一轮' }))
    const result = await screen.findByTestId('loop-result')
    expect(within(result).getAllByText(/ 个$/).map((t) => t.textContent)).toEqual(['看了 MR 0 个', '巡检了 MR 1 个', '看了测试环境 1 个'])
    expect(result).not.toHaveTextContent('staging')
  })
})
