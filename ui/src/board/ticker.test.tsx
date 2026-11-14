import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { worldBoard } from '../test/world'
import type { ReqBoard } from '../types'
import { ClockContext } from './hooks'
import { stageLayout, stageShape } from './layout'
import { StageGraph, stageInput } from './StageGraph'

/** what alice typed into TOOL-1's session after its agent's last call (scripts/preview_world.py) */
const TYPED = '表头要兼容旧模板'

function draw(b: ReqBoard, quiet = false) {
  const input = stageInput(b)
  render(
    <ClockContext.Provider value={{ at: b.at, receivedAt: Date.now() }}>
      <StageGraph board={b} lay={stageLayout(input, stageShape(1238, input))} focus={null} onNode={vi.fn()} flashes={{}} reducedMotion
        feed={{ current: null }} quiet={quiet} />
    </ClockContext.Provider>,
  )
  return screen.getByTestId('kt-graph')
}
const box = (g: HTMLElement, id: string) => g.querySelector<HTMLElement>(`g.kt-node[data-node="${id}"]`)!
/** what an SVG text shows (its <title>, the hover words, left out) */
const shown = (el: Element) => Array.from(el.childNodes).filter((x) => x.nodeName !== 'title').map((x) => x.textContent).join('')

describe('the working box’s ticker', () => {
  test('TOOL-1 on this machine: its last call and the end of the last line its agent wrote; what alice typed is nowhere', () => {
    const b = worldBoard('r4/TOOL-1')
    expect(b.runs[0].ticker).toMatchObject({ tool: '写文件', text: '导入分两步：先校验整张表，再一次写进价格表。' })
    const g = draw(b)
    const t = within(box(g, 'application-design')).getByTestId('kt-node-ticker')
    expect(shown(t)).toBe('▸ 写文件 application-design.md')
    expect(t.querySelector('title')).toHaveTextContent('正在：写文件 application-design.md 它刚写：导入分两步：先校验整张表，再一次写进价格表。')
    // the end of what its agent last wrote is the box's second line, in place of what it hands in
    expect(within(box(g, 'application-design')).getByTestId('kt-node-said').textContent).toMatch(/^「…[^」]*写进价格表。」$/)
    fireEvent.mouseEnter(box(g, 'application-design'))
    expect(document.body).not.toHaveTextContent(TYPED)
    expect(g.querySelectorAll('[data-testid="kt-node-ticker"]')).toHaveLength(1)
  })

  test('ORD-10 on the runner, not readable here: its last recorded step and how long ago', () => {
    const b = worldBoard('r3/ORD-10')
    const last = [...b.events].reverse().find((e) => e.run === 'runner-1')!
    const g = draw(b)
    const t = within(box(g, 'code-generation/push-svc')).getByTestId('kt-node-ticker')
    expect(shown(t)).toMatch(/^▸ 运行机提交 2949c0b.* · \d+ 分钟前$/)
    expect(within(box(g, 'code-generation/push-svc')).queryByTestId('kt-node-said')).toBeNull()
    expect(t.querySelector('title')).toHaveTextContent(`最后一步：${last.text}`)
  })

  test('no ticker in a replay, nor on a box that waits for someone', () => {
    draw(worldBoard('r4/TOOL-1'), true)
    expect(screen.queryByTestId('kt-node-ticker')).toBeNull()
  })
})
