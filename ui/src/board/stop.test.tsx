import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { API, installFixtureRoutes } from '../test/routes'
import { apiError } from '../test/stubs/app-sdk'
import { worldBoard } from '../test/world'
import type { BoardEntry } from '../nav'
import { fx } from '../test/data'
import type { ReqBoard } from '../types'
import type { ViewProps } from '../views/props'
import { BoardPage } from './BoardPage'

const TOOL1: BoardEntry = { repo: 'r4', key: 'TOOL-1' }
const props = (): ViewProps => ({ status: fx.status(), go: vi.fn(), replace: vi.fn(), reloadStatus: vi.fn(), setStatus: vi.fn(), pollMs: 60_000 })
const top = () => document.querySelector<HTMLElement>('header.kt-top')!

function serve(b: () => ReqBoard, stop: () => unknown) {
  const stops: unknown[] = []
  installFixtureRoutes({
    [`GET ${API}/board/*`]: () => b(),
    [`POST ${API}/requirements/r4/TOOL-1/stop`]: (body) => (stops.push(body), stop()),
  })
  return stops
}

describe('停止 (UI-24): this requirement’s own session on this machine, after the person says so', () => {
  test('TOOL-1’s session works: 停止 asks first, 取消 and Esc stop nothing, 确定停止 stops it and reads the board again', async () => {
    let reads = 0
    const stops = serve(() => (reads++, worldBoard('r4/TOOL-1')), () => ({ ok: true, stopped: ['brain-TOOL-1-5a2c'] }))
    render(<BoardPage entry={TOOL1} {...props()} />)
    await screen.findByTestId('kt-graph')
    fireEvent.click(within(top()).getByRole('button', { name: '停止' }))
    const ask = within(top()).getByRole('group', { name: '停止 TOOL-1 的本机会话' })
    expect(ask).toHaveTextContent('停下 TOOL-1 的本机会话正在做的这一轮？别的需求、别的会话都不动')
    fireEvent.click(within(ask).getByRole('button', { name: '取消' }))
    fireEvent.click(within(top()).getByRole('button', { name: '停止' }))
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(within(top()).queryByRole('group', { name: '停止 TOOL-1 的本机会话' })).toBeNull()
    expect(stops).toEqual([])
    const before = reads
    fireEvent.click(within(top()).getByRole('button', { name: '停止' }))
    fireEvent.click(within(top()).getByRole('button', { name: '确定停止' }))
    await waitFor(() => expect(stops).toEqual([{}]))
    await waitFor(() => expect(reads).toBeGreaterThan(before))
    expect(within(top()).queryByRole('group', { name: '停止 TOOL-1 的本机会话' })).toBeNull()
  })

  test('确定停止 done: the top bar says 停下了 for a few seconds (F6)', async () => {
    serve(() => worldBoard('r4/TOOL-1'), () => ({ ok: true, stopped: ['brain-TOOL-1-5a2c'] }))
    render(<BoardPage entry={TOOL1} {...props()} />)
    await screen.findByTestId('kt-graph')
    expect(within(top()).queryByText(/停下了/)).toBeNull()
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      fireEvent.click(within(top()).getByRole('button', { name: '停止' }))
      fireEvent.click(within(top()).getByRole('button', { name: '确定停止' }))
      expect(await within(top()).findByRole('status')).toHaveTextContent('停下了：会话还在，打开会话可以接着做')
      act(() => vi.advanceTimersByTime(8000))
      expect(within(top()).queryByText(/停下了/)).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  test('nothing working any more: the 409 says so in the ask, and nothing else changes', async () => {
    serve(() => worldBoard('r4/TOOL-1'), () => { throw apiError(409, 'nothing_to_stop', 'TOOL-1 在这台机器上没有在做的会话，没有要停的：刷新页面看看') })
    render(<BoardPage entry={TOOL1} {...props()} />)
    await screen.findByTestId('kt-graph')
    fireEvent.click(within(top()).getByRole('button', { name: '停止' }))
    fireEvent.click(within(top()).getByRole('button', { name: '确定停止' }))
    expect(await within(top()).findByRole('alert')).toHaveTextContent('没有在做的会话')
  })

  test('no session working here (PAY-9 waits on the product): no 停止', async () => {
    installFixtureRoutes({ [`GET ${API}/board/*`]: () => worldBoard('r1/PAY-9') })
    render(<BoardPage entry={{ repo: 'r1', key: 'PAY-9' }} {...props()} />)
    await screen.findByTestId('kt-graph')
    expect(within(top()).queryByRole('button', { name: '停止' })).toBeNull()
  })
})
