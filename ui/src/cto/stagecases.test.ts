import { describe, expect, test } from 'vitest'
import type { Requirement } from '../types'
import { COL_NAME, colOf } from './Flow'
import cases from '../test/stage-cases.json'

describe('需求流动 column: the cases the team server says a stage by too (backend/core/deliveries.py flow_stage)', () => {
  test.each(cases.cases.map((c) => [c.why, c] as const))('%s', (_why, c) => {
    const col = colOf(c.row as unknown as Requirement)
    expect(col ? COL_NAME[col] : null).toBe(c.stage)
  })
})
