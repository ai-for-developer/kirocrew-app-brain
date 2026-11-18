/**
 * 证据 (backend evidence.py): what `lean verify` sealed for the requirement, judged by brain-kit at the commit under
 * review, on the 验收 card and on 需求现状. Every word is the backend's, in brain-kit's words (the MR description says
 * them the same way); this file keeps tones only. The state line in its state's tone; a chip per plan row and per test
 * output, with the MR's mark for what is wrong with it, opening the 产出 drawer at the round whose VERIFY sealed it and
 * at its file; then what is missing, one sentence each.
 */
import type { OutAt } from '../nav'
import type { Evidence, EvidenceChip } from '../types'

/** where a chip opens: the build-and-test node, one round per VERIFY */
export const EVIDENCE_NODE = 'build-and-test'

/** brain-kit's tones for its states (docs/proof.md): current ok; old, unknown warn; missed, void bad; planned, not taken idle */
const STATE_TONE: Record<NonNullable<Evidence['state']>, string> = {
  current: 'ok', stale: 'warn', unknown: 'warn', failed: 'bad', none: 'idle', off: 'idle',
}
const CHIP_TONE: Record<EvidenceChip['state'], string> = {
  current: 'ok', old: 'warn', unknown: 'warn', missed: 'bad', void: 'bad', planned: 'idle', not_taken: 'idle',
}
/** a gap's tone by its kind (a row kind as its chip; the project's own setup idle; anything else, a newer kind too, warn) */
const GAP_TONE: Record<string, string> = { ...CHIP_TONE, no_proof_check: 'idle', no_verify: 'idle', uncommitted: 'idle' }

export const EVIDENCE_CSS = `
.kt-proof { display: grid; gap: 6px; min-width: 0; }
.kt-proof-late { margin: 0; font-size: .88em; }
.kt-proof-chips { display: flex; flex-wrap: wrap; gap: 6px; list-style: none; margin: 0; padding: 0 !important; }
.kt-proof-chip { appearance: none; font: inherit; font-size: .86em; font-weight: 700; display: inline-flex; gap: 4px; align-items: baseline;
  padding: 2px 10px; border-radius: 999px; color: var(--text); background: color-mix(in srgb, var(--kt-tone) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--kt-tone) 42%, transparent); max-width: 100%; }
.kt-root span.kt-proof-chip { color: var(--text); border-style: dashed; }
button.kt-proof-chip { cursor: pointer; }
button.kt-proof-chip:hover { background: color-mix(in srgb, var(--kt-tone) 24%, transparent); }
.kt-proof-chip .kt-proof-ac { color: var(--muted); font-weight: 600; }
.kt-proof-chip .kt-proof-mark { color: var(--kt-tone); }
.kt-proof-gaps { margin: 0; padding-left: 1.1em !important; display: grid; gap: 2px; font-size: .9em; overflow-wrap: anywhere; }
.kt-proof-gaps li::marker { color: var(--kt-tone); }
`

function Chip({ c, onOpen }: { c: EvidenceChip; onOpen?: (at: OutAt) => void }) {
  const tone = CHIP_TONE[c.state] ?? 'warn'
  const body = (
    <>
      {c.label}
      {c.ac.length > 0 && <span className="kt-proof-ac">{c.ac.join('、')}</span>}
      <span className="kt-proof-mark">{c.state === 'current' ? '✓' : c.mark}</span>
    </>
  )
  const where = c.round !== null && c.path ? c.round : null
  const title = [c.what, c.mark, where !== null ? `点开看第 ${where} 轮拍的` : ''].filter(Boolean).join(' · ')
  if (where === null || !c.path || !onOpen) {
    return <span className={`kt-proof-chip tone-${tone}`} title={title} data-testid="evidence-chip">{body}</span>
  }
  const file = c.path
  return (
    <button type="button" className={`kt-proof-chip tone-${tone}`} title={title} data-testid="evidence-chip"
      onClick={() => onOpen({ node: EVIDENCE_NODE, round: where, file })}>{body}</button>
  )
}

/** The 证据 row: brain-kit's state line, the chips (each opens the drawer when *onOpen* is given), the gap sentences. */
export function EvidenceRow({ proof, onOpen }: { proof: Evidence; onOpen?: (at: OutAt) => void }) {
  if (!proof.state) return <span className="kt-dim" data-testid="evidence-none">{proof.error || '还没有'}</span>
  return (
    <div className="kt-proof" data-testid="evidence">
      <style>{EVIDENCE_CSS}</style>
      <b className={`tone-${STATE_TONE[proof.state] ?? 'warn'}`} data-testid="evidence-line">{proof.line}</b>
      {proof.late && <p className="kt-proof-late kt-dim">{proof.late}</p>}
      {proof.chips.length > 0 && (
        <ul className="kt-proof-chips" aria-label="每一项证据">
          {proof.chips.map((c, i) => <li key={`${c.kind}:${c.label}:${i}`}><Chip c={c} onOpen={onOpen} /></li>)}
        </ul>
      )}
      {proof.gaps.length > 0 && (
        <ul className="kt-proof-gaps" aria-label="还缺什么">
          {proof.gaps.map((g, i) => <li key={i} className={`tone-${GAP_TONE[g.kind] ?? 'warn'}`}>{g.text}</li>)}
        </ul>
      )}
    </div>
  )
}
