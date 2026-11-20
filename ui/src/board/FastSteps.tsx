/**
 * A fast run's way on 运行过程, where the stage graph goes (a board with steps): 写代码 → 自动检查 → 开 MR → CI → 合并, a box
 * each in the stage graph's own look (StageGraph.tsx's box: its icon, title, line and state; at work glows, waiting for
 * the person looking blinks magenta, for anyone else calm blue, failed solid red with ✕, done a green outline, merged
 * solid green with ✓, not reached dashed), with the arrows between them. 写代码 leads to the commit conveyor (its
 * commits and their diffs), 自动检查 to the check wall, 开 MR, CI and 合并 to their page on GitLab.
 */
import type { KeyboardEvent } from 'react'
import { STEP_ICON, STEP_PART, stepHref, stepLook, toPart, type StepLook } from '../flows'
import type { FastStep, Waits } from '../types'
import { brief, clip, fmtWhen, textWidth } from './format'
import { GraphDefs, Halo, Scroller } from './GraphFrame'
import { NODE_H, NODE_W, SCALE } from './layout'
import { RoleGlyph } from './RoleIcon'

const GAP = 44
const PAD = 28

export function FastSteps({ steps, waits, reducedMotion }: { steps: FastStep[]; waits: Pick<Waits, 'who' | 'do'>; reducedMotion: boolean }) {
  const width = PAD * 2 + steps.length * NODE_W + (steps.length - 1) * GAP
  const height = PAD * 2 + NODE_H
  const xOf = (i: number) => PAD + NODE_W / 2 + i * (NODE_W + GAP)
  const y = PAD + NODE_H / 2
  const looks = steps.map((s) => stepLook(s, waits))
  const count = (status: FastStep['status']) => steps.filter((s) => s.status === status).length
  const asked = looks.find((l) => l.node === 'awaiting_approval')
  const label = [`快速需求的路：${steps.length} 步，${count('done')} 步做完`, count('running') ? `${count('running')} 步在做` : '',
    count('failed') ? `${count('failed')} 步没过` : '', asked ? `1 步${asked.word}` : ''].filter(Boolean).join('，')
  return (
    <div className="kt-graph is-stages br-graph" data-testid="br-fast-steps">
      <Scroller minWidth={Math.floor(width * SCALE)}>
        <svg className="kt-graph-svg" viewBox={`0 0 ${width} ${height}`} role="group" aria-label={`${label}。`}>
          <GraphDefs />
          <rect className="kt-g-bg" x={0} y={0} width={width} height={height} fill="url(#kt-dots)" />
          <g className="kt-edges" aria-hidden="true">
            {steps.slice(1).map((s, i) => {
              const state = s.status !== 'waiting' ? 'used' : steps[i].status === 'done' ? 'ready' : 'pending'
              return <path key={s.id} data-edge={`${steps[i].id}>${s.id}`} className={`kt-edge is-${state}`} markerEnd={`url(#kt-arrow-${state})`}
                d={`M${xOf(i) + NODE_W / 2 + 4},${y} L${xOf(i + 1) - NODE_W / 2 - 6},${y}`} />
            })}
          </g>
          <g className="kt-nodes">
            {steps.map((s, i) => <StepBox key={s.id} s={s} look={looks[i]} x={xOf(i)} y={y} reducedMotion={reducedMotion} />)}
          </g>
        </svg>
      </Scroller>
    </div>
  )
}

function StepBox({ s, look, x, y, reducedMotion }: { s: FastStep; look: StepLook; x: number; y: number; reducedMotion: boolean }) {
  const w = NODE_W
  const h = NODE_H
  const merged = look.node === 'merged'
  const failed = s.status === 'failed'
  const sub = `${s.status === 'done' ? '✓ ' : failed ? '✕ ' : ''}${look.word}`
  const name = [`${s.label}：${look.word}`, s.text, s.at ? fmtWhen(s.at) : ''].filter(Boolean).join('，')
  const href = stepHref(s)
  const onKey = (e: KeyboardEvent<SVGGElement>) => {
    if (e.key !== 'Enter' && e.key !== ' ') return
    e.preventDefault()
    toPart(e.currentTarget, s.id)
  }
  const acts = href ? {} : STEP_PART[s.id] ? { role: 'button', tabIndex: 0, 'aria-label': name, onClick: (e: { currentTarget: Element }) => toPart(e.currentTarget, s.id), onKeyDown: onKey }
    : { role: 'img', 'aria-label': name }
  const box = (
    <g className={`kt-node is-${look.node}${look.other ? ' is-other' : ''}${merged ? ' is-final' : ''}`} transform={`translate(${x},${y})`} data-node={s.id} data-status={s.status} {...acts}>
      <title>{name}</title>
      {(s.status === 'running' || (look.node === 'awaiting_approval' && !look.other)) && <Halo w={w} h={h} reducedMotion={reducedMotion} />}
      <rect className="kt-node-body" x={-w / 2} y={-h / 2} width={w} height={h} rx={16} />
      <circle className="kt-node-avatar" cx={-w / 2 + 33} cy={0} r={22.5} />
      {s.status === 'running' && <circle className="kt-node-spin" cx={-w / 2 + 33} cy={0} r={27} />}
      <RoleGlyph role={STEP_ICON[s.id]} cx={-w / 2 + 33} cy={0} size={24} className="kt-node-emoji" />
      <text className={`kt-node-title${textWidth(s.label) > 4.6 ? ' is-long' : ''}`} x={-w / 2 + 66} y={-15}>{clip(s.label, 6.4)}</text>
      {s.text && <text className="kt-node-task" x={-w / 2 + 66} y={4}>{brief(s.text, 8.6)}</text>}
      <text className="kt-node-sub" x={-w / 2 + 66} y={23}>{clip(sub, merged || failed ? 7.4 : 8.6)}</text>
      {merged && (
        <g className="kt-node-check" transform={`translate(${w / 2 - 14},${h / 2 - 14})`}>
          <circle r={9} />
          <path d="M-4,0 L-1.2,3 L4.2,-3.2" />
        </g>
      )}
      {failed && (
        <g className="kt-node-x" transform={`translate(${w / 2 - 14},${h / 2 - 14})`}>
          <circle r={9} />
          <path d="M-3.5,-3.5 L3.5,3.5 M3.5,-3.5 L-3.5,3.5" />
        </g>
      )}
      <rect className="kt-node-focus" x={-w / 2 - 5} y={-h / 2 - 5} width={w + 10} height={h + 10} rx={20} />
    </g>
  )
  return href ? <a href={href} target="_blank" rel="noopener noreferrer" aria-label={`${name} ↗`}>{box}</a> : box
}
