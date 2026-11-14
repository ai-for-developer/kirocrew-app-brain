/**
 * The Markdown brain-kit writes (a 变更说明, Lean's notes, a decision) shown as formatted text: headings, paragraphs,
 * lists, tables, code. Built as React elements from the text, never as HTML, so nothing in it can act on the page; a
 * link stays its words. Document mode (a whole document a stage handed in, in the 产出 drawer) keeps what a document
 * has besides: each heading level, lists inside lists, task boxes ([ ] / [x]) and quotes.
 */
import type { ReactNode } from 'react'

const INLINE = /`([^`]+)`|\*\*([^*]+)\*\*|\[([^\]]+)\]\([^)\s]*\)/g
/** a path or a branch (ai/alice/PAY-3-refund-export, /Users/alice/…, aidlc-docs/…/): names joined by 「/」 */
const PATH = /\/?[\w.~@+-]+(?:\/[\w.~@+-]*)+/g

/** A path's pieces as it may break: only next to a 「/」 (a folder's trailing 「/」 stays with its name). */
export function pathPieces(path: string): string[] {
  const parts = path.split('/').map((p, i) => (i ? `/${p}` : p)).filter((p, i) => p || i)
  if (parts.length > 1 && parts[parts.length - 1] === '/') parts.splice(-2, 2, `${parts[parts.length - 2]}/`)
  return parts
}

/** A path drawn so that it breaks only at a 「/」, never at a 「-」 inside a name (.br-path > span is nowrap). */
export function PathPieces({ path }: { path: string }) {
  return <span className="br-path">{pathPieces(path).map((p, i) => <span key={i}><span>{p}</span><wbr /></span>)}</span>
}

/** Plain text with its paths and branches drawn by PathPieces. */
export function Paths({ text }: { text: string | null | undefined }) {
  if (!text) return null
  const out: ReactNode[] = []
  let last = 0
  for (const m of text.matchAll(PATH)) {
    if (m.index > last) out.push(text.slice(last, m.index))
    out.push(<PathPieces key={m.index} path={m[0]} />)
    last = m.index + m[0].length
  }
  if (last < text.length) out.push(text.slice(last))
  return <>{out}</>
}

/** A text cut short (「…」) inside its `code` keeps the code: its open backtick is closed before the 「…」, so no lone
 * backtick shows (brain-kit cuts long table cells at a length, wherever that falls). */
const closeCut = (text: string) => ((text.match(/`/g)?.length ?? 0) % 2 && /…$/.test(text) ? text.replace(/…$/, '`…') : text)

/** One line's `code`, **bold** and [links](…) (their words only); a path in it breaks only at a 「/」. */
export function Inline({ text: raw }: { text: string | null | undefined }) {
  if (!raw) return null
  const text = closeCut(raw)
  const out: ReactNode[] = []
  let last = 0
  for (const m of text.matchAll(INLINE)) {
    if (m.index > last) out.push(<Paths key={`t${m.index}`} text={text.slice(last, m.index)} />)
    out.push(m[1] !== undefined ? <code key={m.index}><Paths text={m[1]} /></code> : m[2] !== undefined ? <b key={m.index}><Paths text={m[2]} /></b> : m[3])
    last = m.index + m[0].length
  }
  if (last < text.length) out.push(<Paths key="end" text={text.slice(last)} />)
  return <>{out}</>
}

const REF = /\s*（(D\d{2}#S\d{3}[^）]*)）/g

/** A line's material refs (「（D01#S002 · 097f2e9e）」: a document's section and its hash) taken out: the line for a
 * person, and the refs for 细节. */
export function takeRefs(text: string): { text: string; refs: string[] } {
  return { text: text.replace(REF, ''), refs: [...text.matchAll(REF)].map((m) => m[1]) }
}

/** A list as a document has it: each item with its task box (null: none) and the list inside it. */
export type ListTree = { ordered: boolean; indent: number; items: { text: string; check: boolean | null; kids: ListTree | null }[] }

type Block =
  | { kind: 'h'; level: number; text: string }
  | { kind: 'p'; lines: string[] }
  | { kind: 'ul' | 'ol'; items: string[] }
  | { kind: 'list'; tree: ListTree }
  | { kind: 'quote'; lines: string[] }
  | { kind: 'table'; head: string[]; rows: string[][] }
  | { kind: 'code'; text: string }
  | { kind: 'hr' }

const cells = (line: string) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, '|'))
const isRow = (l: string) => /^\s*\|.*\|\s*$/.test(l)
const isRule = (l: string) => /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/.test(l)
const UL = /^\s*[-*+]\s+(.*)$/
const OL = /^\s*\d+[.)]\s+(.*)$/
const H = /^(#{1,6})\s+(.*)$/
const ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/
const TASK = /^\[([ xX])\]\s+(.*)$/
const QUOTE = /^\s*>\s?(.*)$/

/** Consecutive list lines (and their indented continuations) as a tree: an item indented past the one before it
 * opens a list inside that one. */
export function listTree(lines: string[]): ListTree {
  const first = ITEM.exec(lines[0]) as RegExpExecArray
  const root: ListTree = { ordered: /\d/.test(first[2]), indent: first[1].length, items: [] }
  const stack = [root]
  for (const l of lines) {
    const m = ITEM.exec(l)
    let top = stack[stack.length - 1]
    if (!m) {
      const last = top.items[top.items.length - 1]
      if (last) last.text += ` ${l.trim()}`
      continue
    }
    const indent = m[1].length
    while (stack.length > 1 && indent < top.indent) top = (stack.pop(), stack[stack.length - 1])
    const last = top.items[top.items.length - 1]
    if (indent > top.indent && last) {
      last.kids = { ordered: /\d/.test(m[2]), indent, items: [] }
      stack.push(last.kids)
      top = last.kids
    }
    const t = TASK.exec(m[3])
    top.items.push({ text: t ? t[2] : m[3], check: t ? t[1] !== ' ' : null, kids: null })
  }
  return root
}

export function blocks(text: string, doc = false): Block[] {
  const ls = text.replace(/\r\n?/g, '\n').split('\n')
  const out: Block[] = []
  let i = 0
  while (i < ls.length) {
    const l = ls[i]
    if (!l.trim()) { i++; continue }
    if (/^\s*```/.test(l)) {
      const body: string[] = []
      i++
      while (i < ls.length && !/^\s*```/.test(ls[i])) body.push(ls[i++])
      i++
      out.push({ kind: 'code', text: body.join('\n') })
      continue
    }
    const h = H.exec(l)
    if (h) { out.push({ kind: 'h', level: h[1].length, text: h[2] }); i++; continue }
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(l)) { out.push({ kind: 'hr' }); i++; continue }
    if (isRow(l) && i + 1 < ls.length && isRule(ls[i + 1])) {
      const head = cells(l)
      const rows: string[][] = []
      i += 2
      while (i < ls.length && isRow(ls[i])) rows.push(cells(ls[i++]))
      out.push({ kind: 'table', head, rows })
      continue
    }
    if (doc && ITEM.test(l)) {
      const lines: string[] = []
      while (i < ls.length && (ITEM.test(ls[i]) || (lines.length > 0 && /^\s{2,}\S/.test(ls[i])))) lines.push(ls[i++])
      out.push({ kind: 'list', tree: listTree(lines) })
      continue
    }
    if (doc && QUOTE.test(l)) {
      const lines: string[] = []
      while (i < ls.length && QUOTE.test(ls[i])) lines.push((QUOTE.exec(ls[i++]) as RegExpExecArray)[1])
      out.push({ kind: 'quote', lines })
      continue
    }
    for (const [re, kind] of [[UL, 'ul'], [OL, 'ol']] as const) {
      if (!re.test(l)) continue
      const items: string[] = []
      while (i < ls.length && re.test(ls[i])) items.push((re.exec(ls[i++]) as RegExpExecArray)[1])
      out.push({ kind, items })
      break
    }
    if (UL.test(l) || OL.test(l)) continue
    const lines: string[] = []
    while (i < ls.length && ls[i].trim() && !H.test(ls[i]) && !UL.test(ls[i]) && !OL.test(ls[i]) && !/^\s*```/.test(ls[i]) && !(isRow(ls[i]) && isRule(ls[i + 1] ?? '')) && !(doc && QUOTE.test(ls[i]))) lines.push(ls[i++])
    out.push({ kind: 'p', lines })
  }
  return out
}

function List({ tree }: { tree: ListTree }) {
  const items = tree.items.map((x, j) => (
    <li key={j} className={x.check === null ? undefined : 'is-task'}>
      {x.check !== null && <input type="checkbox" checked={x.check} disabled aria-label={x.check ? '做完了' : '还没做'} />}
      <Inline text={x.text} />
      {x.kids && <List tree={x.kids} />}
    </li>
  ))
  return tree.ordered ? <ol>{items}</ol> : <ul>{items}</ul>
}

/** A heading in document mode: # → h3 … ###### → h6 (the page's own h1 and h2 are above it). */
const DOC_H = ['h3', 'h3', 'h4', 'h5', 'h6', 'h6', 'h6'] as const

/** A whole Markdown text as formatted text (the 变更说明's 看全文). *foldRefs*: the material refs (「（D01#S002 · 097f2e9e）」)
 * are taken out of the lines and said once, at the end (BOARD-17). *doc*: document mode. */
export function Markdown({ text: raw, testid, foldRefs = false, doc = false }: { text: string; testid?: string; foldRefs?: boolean; doc?: boolean }) {
  const refs: string[] = []
  const text = foldRefs ? raw.split('\n').map((l) => { const t = takeRefs(l); refs.push(...t.refs); return t.text }).join('\n') : raw
  const once = [...new Set(refs)]
  return (
    <div className={doc ? 'br-md kt-md is-doc' : 'br-md'} data-testid={testid}>
      {blocks(text, doc).map((b, i) => {
        switch (b.kind) {
          case 'h': {
            if (doc) {
              const Tag = DOC_H[b.level]
              return <Tag key={i}><Inline text={b.text} /></Tag>
            }
            return b.level <= 2 ? <h4 key={i}><Inline text={b.text} /></h4> : <h5 key={i}><Inline text={b.text} /></h5>
          }
          case 'list': return <List key={i} tree={b.tree} />
          case 'quote': return <blockquote key={i}>{b.lines.map((t, j) => <span key={j}>{j > 0 && <br />}<Inline text={t} /></span>)}</blockquote>
          case 'hr': return <hr key={i} />
          case 'code': return <pre key={i}><code>{b.text}</code></pre>
          case 'ul': return <ul key={i}>{b.items.map((t, j) => <li key={j}><Inline text={t} /></li>)}</ul>
          case 'ol': return <ol key={i}>{b.items.map((t, j) => <li key={j}><Inline text={t} /></li>)}</ol>
          case 'table':
            return (
              <div key={i} className="br-scroll">
                <table className="cto-table">
                  <thead><tr>{b.head.map((c, j) => <th key={j}><Inline text={c} /></th>)}</tr></thead>
                  <tbody>{b.rows.map((r, j) => <tr key={j}>{b.head.map((_, k) => <td key={k}><Inline text={r[k] ?? ''} /></td>)}</tr>)}</tbody>
                </table>
              </div>
            )
          default: return <p key={i}>{b.lines.map((t, j) => <span key={j}>{j > 0 && <br />}<Inline text={t} /></span>)}</p>
        }
      })}
      {once.length > 0 && <p className="br-hint" data-testid="md-refs">引用的材料段落：{once.join('、')}</p>}
    </div>
  )
}
