import { render, screen, within } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { worldBoard } from '../test/world'
import type { ReqBoard } from '../types'
import { clockAt } from './format'
import { Kpis } from './Header'
import { ClockContext } from './hooks'
import { stageLayout, stageShape } from './layout'
import { StageGraph, stageInput } from './StageGraph'

function page(b: ReqBoard) {
  const input = stageInput(b)
  render(
    <ClockContext.Provider value={{ at: b.at, receivedAt: Date.now() }}>
      <Kpis board={b} reducedMotion />
      <StageGraph board={b} lay={stageLayout(input, stageShape(1558, input))} focus={null} onNode={vi.fn()} flashes={{}} reducedMotion
        feed={{ current: null }} />
    </ClockContext.Provider>,
  )
  const graph = screen.getByTestId('kt-graph')
  return {
    graph,
    lean: screen.getByText('AI-DLC Lean').parentElement as HTMLElement,
    status: (id: string) => graph.querySelector(`g.kt-node[data-node="${id}"]`)?.getAttribute('data-status'),
    ready: () => Array.from(graph.querySelectorAll('path.kt-edge.is-ready')).length,
  }
}

describe('the AI-DLC Lean card and the graph say the same next step', () => {
  test('PAY-12 merged into its big one: every box is solid, so the card has no 下一步 left (Lean never closed 构建测试)', () => {
    const b = worldBoard('r1/PAY-12')
    expect(b.row.lean.next?.label).toBe('构建测试')            // Lean's own record still has it open
    const p = page(b)
    expect(p.status('build-and-test')).toBe('merged')
    expect(p.lean.querySelector('.br-lean-next')).toHaveTextContent(/^全部做完/)
    expect(p.lean).not.toHaveTextContent('构建测试')
    expect(p.lean.querySelectorAll('.kt-segs .is-next, .kt-segs .is-run')).toHaveLength(0)
  })

  test('PAY-16 waits for its INCEPTION approval: 下一步 is 代码生成, the dashed box the graph leads to next, not Lean’s 功能设计', () => {
    const b = worldBoard('r1/PAY-16')
    expect(b.row.lean.next?.label).toBe('功能设计')
    const p = page(b)
    expect(p.status('code-generation/app')).toBe('waiting')
    expect(p.lean.querySelector('.br-lean-next')).toHaveTextContent(/^下一步：代码生成/)
    expect(p.lean).toHaveAccessibleName('AI-DLC Lean：做完 3 · 跳过 2 · 下一步：代码生成 · 构思整段不需要')
    const into = Array.from(p.graph.querySelectorAll('path.kt-edge.is-ready'))
    expect(into).toHaveLength(1)
    expect(p.graph.querySelector('g.kt-node[data-node="functional-design/app"]')).toBeNull()
  })

  test('PAY-9 asked the product for its release: the card says 在做：发布, the box it is on', () => {
    const p = page(worldBoard('r1/PAY-9'))
    expect(p.status('release')).toBe('awaiting_approval')
    expect(p.lean.querySelector('.br-lean-next')).toHaveTextContent(/^在做：发布/)
    // its cell is in its box's colour: it waits on the product, not on you
    expect(p.lean.querySelectorAll('.kt-segs .is-other')).toHaveLength(1)
  })
})

describe('检查通过 counts the latest verify, not the whole wall', () => {
  test('PAY-9: 3/3 of its fifth verify, the ring the same; the wall still counts its gates and the hand-over', () => {
    const b = worldBoard('r1/PAY-9')
    expect(b.checks.total).toBeGreaterThan(3)
    page(b)
    expect(screen.getByTestId('br-checks-counter')).toHaveAccessibleName('检查通过 3/3')
    expect(screen.getByTestId('br-checks-say')).toHaveTextContent('第 5 轮 · 5 轮都过')
    expect(screen.getByTestId('kt-station')).toHaveAccessibleName('检查 3/3')
  })

  test('PAY-2: its second verify passed after the first failed', () => {
    page(worldBoard('r1/PAY-2'))
    expect(screen.getByTestId('br-checks-counter')).toHaveAccessibleName('检查通过 3/3')
    expect(screen.getByTestId('br-checks-say')).toHaveTextContent('第 2 轮 · 第 1 轮没过')
  })

  test('PAY-16 has not run a verify yet', () => {
    page(worldBoard('r1/PAY-16'))
    expect(screen.getByTestId('br-checks-counter')).toHaveAccessibleName('检查通过：还没跑过检查')
    expect(screen.getByTestId('br-checks-counter')).toHaveTextContent(/^—$/)
    expect(screen.getByTestId('br-checks-say')).toHaveTextContent('还没跑过检查')
    expect(screen.getByTestId('kt-station')).toHaveAccessibleName('还没有检查结果')
  })
})

describe('the roster says when a run asked and what', () => {
  test('PAY-9: under 运行机 第 1 次, its run id, then the time it asked and what (the mockup’s RUN-PAY-9-1 · 16:12 请批发布, in two lines at 12 px)', () => {
    const b = worldBoard('r1/PAY-9')
    const asked = b.nodes.find((n) => n.id === 'release')!.gates.find((g) => g.state === 'pending')!
    const { graph } = page(b)
    const sub = Array.from(within(graph).getByTestId('kt-roster').querySelectorAll('.kt-roster-sub')).map((x) => x.textContent)
    expect(sub).toEqual(['RUN-PAY-9-1', `${clockAt(asked.at!, b.at)} 请批发布`])
  })
})
