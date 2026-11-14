/**
 * The preview's boards (ui/preview/world.json): every one made by the backend's own fold (scripts/preview_world.py), so
 * a test of one reads exactly what the page gets for that requirement.
 */
import world from '../../preview/world.json'
import type { ReqBoard } from '../types'
import { clone } from './data'

const boards = world.boards as unknown as Record<string, ReqBoard>

/** A fresh copy of the preview's board of *key* (`r1/PAY-9`). */
export function worldBoard(key: string): ReqBoard {
  const b = boards[key]
  if (!b) throw new Error(`the preview has no board ${key}`)
  return clone(b)
}
