import { describe, expect, it } from 'vitest'
import { BELT_EDGE, newestEnd } from './Conveyor'

// PAY-9's belt at 1280 with KiroCrew's sidebar: 15 chips 160 px wide, 10 px apart, in a belt 968 px wide
const starts = Array.from({ length: 15 }, (_, i) => 2 + i * 170)
const scrollWidth = starts[14] + 160 + 2

describe('the conveyor opens at its newest end', () => {
  it('moved on to a chip’s start, so the first chip in view is whole and starts past the fade', () => {
    const { left, room } = newestEnd(starts, 968, scrollWidth)
    const max = scrollWidth - 968
    expect(left).toBeGreaterThanOrEqual(max)
    expect(starts).toContain(left + BELT_EDGE)
    const firstSeen = starts.find((x) => x + 160 > left)!
    expect(firstSeen - left).toBe(BELT_EDGE)
    // the newest chip stays whole: the room that takes is added after it
    expect(left + 968).toBeLessThanOrEqual(scrollWidth + room)
    expect(room).toBe(left - max)
    expect(room).toBeLessThan(170)
  })

  it('a belt that fits is not moved', () => {
    expect(newestEnd(starts.slice(0, 5), 968, starts[4] + 162)).toEqual({ left: 0, room: 0 })
  })

  it('ending right after a whole chip it adds no room', () => {
    const w = 170 * 6 + 2 + BELT_EDGE - 10
    expect(newestEnd(starts, w, scrollWidth).room).toBe(0)
  })
})
