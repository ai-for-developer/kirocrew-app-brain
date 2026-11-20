/**
 * 产出: what each stage handed in, round by round (the sample's 每一步的产出 drawer, for a requirement's stages).
 *
 * A step pill per stage a run did (in band order), its rounds (第 1 轮 退回 · 第 2 轮 CI 没过 #1234 …), what the round picked
 * was about (done because a pipeline failed, sent back by whom, asked for, changed after the request), then its files:
 * what Lean recorded it hands in (约定它交的) and what else its commits changed (其他改动). A file shows as it was in that
 * round: a document rendered (内容), the source (源码), or its change (改动), from the round before's version (和第 N 轮比) or
 * the commit before. 「就是请批的那一版」 marks the very version an approval was asked for. A document waiting for approval and
 * not committed yet is read from this machine's worktree, only as the bytes Lean recorded (the backend checks).
 *
 * GET /outputs/{repo}/{key}?node&round lists a round's files, GET /outputs/{repo}/{key}/file reads one; the rest
 * (rounds, gates, runs) is the board's own snapshot. Where it stands is in the URL (nav.ts out): the parent replaces
 * the history entry, so 后退 never steps through the drawer.
 *
 * In a replay it reads the replayed board at its moment (timeline.ts): only the rounds started by then; nothing is
 * read for a step not started then. The replay's controls come along, so it can be played and scrubbed with the
 * drawer open.
 *
 * A fast run (快速) has no stage rounds: its drawer is 写代码's (?out=code), by commit. GET /outputs/{repo}/{key}/commits
 * lists them, read once and again when the board has another: 全部改动 first (each file at the last commit that changed
 * it, compared with where the branch left), then each commit oldest first (its files compared with the commit before);
 * the code it wrote leads (代码改动, biggest change first), the run's records after (其他改动), and the biggest code
 * change is open. In a replay only the commits made by then, and 全部改动 of the files whose last change was made then.
 */
import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { paths, usePoll } from '../api'
import type { OutAt } from '../nav'
import type { CommitOut, CommitsOutput, FileView, Gate, NodeOutput, NodeTurns, OutFile, ReqBoard, Round, Run, StageNode } from '../types'
import { ExtLink, TYPE_IT_YOURSELF } from '../ui'
import { Markdown } from '../views/Markdown'
import { biggestFirst, laneNodes } from './Lanes'
import { ROUND_TONE, actorMeta, ciCause, ciWords, clip, clockAt, decider, fmtWhen, gateVerb, gateWhat, nodeLabel, roundWords, whoOf } from './format'
import { RoleIcon } from './RoleIcon'
import { filesAt, turnsAt, type Moment } from './timeline'
import { SessionPane } from './Turns'

type Mode = 'content' | 'source' | 'diff'

const TURNS_LIVE_MS = 4000

/** A round in two words, for its pill: 退回 · CI 没过 #1234 · 代码又改了 · 等验收 · 批了. */
function roundShort(r: Round, n: StageNode, b: ReqBoard): string {
  const o = r.outcome
  switch (o.kind) {
    case 'open': {
      const g = openAsk(n, r)
      return g ? (g.kind === 'RELEASE' && g.who === 'product' ? '等验收' : gateVerb(g, b)) : '在做'
    }
    case 'sent_back': return '退回'
    case 'superseded': return o.pipeline ? ciWords(o.pipeline) : '代码又改了'
    case 'approved': return '批了'
    case 'passed': return '检查过了'
    case 'failed': return '检查没过'
    case 'refused': return '没接受'
    case 'skipped': return '跳过'
    default: return '做完'
  }
}

/** The approval asked on this round and not decided yet. */
const openAsk = (n: StageNode, r: Round): Gate | undefined => n.gates.find((g) => g.round === r.n && (g.state === 'pending' || g.state === 'queued'))

const runName = (run: Run | undefined): string => (run ? `${run.label}${run.run_id ? `（${run.run_id}）` : ''}` : '')

/** A path as the list says it: a Lean document from its intent's folder on (operations/release.md). */
const shortPath = (p: string): string => p.replace(/^aidlc-docs\/[^/]+\//, '')

const isDoc = (p: string): boolean => /\.(md|markdown)$/i.test(p)

/** Kept for the run by Lean (aidlc-docs/) or brain-kit (runs/: the MR's description, park.json), not code it wrote. */
const isRecord = (p: string): boolean => /^(aidlc-docs|runs)\//.test(p)

/** *codeFirst*: the code it wrote leads (代码改动, biggest change first), the records last. */
function fileGroups(codeFirst: boolean, files: OutFile[]): [string, OutFile[]][] {
  const declared = files.filter((f) => f.output)
  const others = files.filter((f) => !f.output)
  if (!codeFirst) return [['约定它交的', declared], ['其他改动', others]]
  return [['代码改动', others.filter((f) => !isRecord(f.path)).sort(biggestFirst)], ['约定它交的', declared],
    ['其他改动', others.filter((f) => isRecord(f.path))]]
}

/** What the round was about, above its files: sent back (who, the D-n, never their words), asked for, changed after
 * the request, its checks red, approved. */
function RoundBanner({ n, r, board, replay }: { n: StageNode; r: Round; board: ReqBoard; replay: boolean }) {
  const o = r.outcome
  const when = (t: number | null) => (t ? clockAt(t, board.at) : '')
  const g = openAsk(n, r)
  if (g) {
    const who = whoOf(g.who, board).replace(/^等\s?/, '')
    return (
      <div className="kt-round-banner tone-approve" data-testid="out-banner">
        <strong>✋ 这是请{who}批的版本</strong>
        <span>{gateWhat(g.kind)}{g.at ? ` · ${when(g.at)} 请批` : ''}{g.mr?.decision ? ` · ${g.mr.decision} 在 MR 上` : ''}</span>
        {g.line ? <span>做什么：看完回会话打 <code>{g.line}</code>（{TYPE_IT_YOURSELF}）</span>
          : (g.mr || !replay) && <span className="kt-dim">{who}在 MR 上回答；你不用在会话里打什么</span>}
        {g.mr?.url && <ExtLink href={g.mr.url} className="kt-btn is-small">看 MR ↗</ExtLink>}
      </div>
    )
  }
  if (o.kind === 'sent_back') {
    return (
      <div className="kt-round-banner tone-bad" data-testid="out-banner">
        <strong>✗ {o.by === '你' ? '你退回了这一轮' : o.by?.startsWith('产品') ? '产品在 MR 上退回了这一轮' : `${decider(o.by)}退回了这一轮`}</strong>
        <span>{[o.decision, when(o.at)].filter(Boolean).join(' · ')}</span>
        <span className="kt-dim">退回的原话没在这里显示（有人写的话不上看板）</span>
        {o.decision && board.row.mr?.url && <ExtLink href={board.row.mr.url} className="kt-btn is-small">在 MR 上看 ↗</ExtLink>}
      </div>
    )
  }
  if (o.kind === 'superseded') {
    return (
      <div className="kt-round-banner tone-idle" data-testid="out-banner">
        <strong>↻ {o.pipeline ? `${ciWords(o.pipeline)}：请批以后代码又改了` : '请批以后代码又改了'}</strong>
        <span>{o.sha ? `提交 ${o.sha.slice(0, 7)}${o.subject ? `「${o.subject}」` : ''}` : ''}{o.at ? ` · ${when(o.at)}` : ''}</span>
        <span className="kt-dim">这一版没人批过：下一轮又请批了</span>
        {o.pipeline?.url && <ExtLink href={o.pipeline.url} className="kt-btn is-small">看流水线 ↗</ExtLink>}
      </div>
    )
  }
  if (o.kind === 'failed' || o.kind === 'refused') {
    return <div className={`kt-round-banner tone-${o.kind === 'failed' ? 'bad' : 'warn'}`} data-testid="out-banner"><strong>✗ {roundWords(r, n, board)}</strong></div>
  }
  if (o.kind === 'approved') {
    return <div className="kt-round-banner tone-ok" data-testid="out-banner"><strong>✓ {roundWords(r, n, board)}</strong>{o.at && <span>{when(o.at)}</span>}</div>
  }
  return null
}

/** Why the round was done, when it was for a pipeline that failed: CI 没过 #1234 · 15:16 · 看流水线. */
function CauseBanner({ r, board }: { r: Round; board: ReqBoard }) {
  const ci = ciCause(r)
  if (!ci) return null
  return (
    <div className="kt-round-banner tone-bad" data-testid="out-cause">
      <strong>↻ 这一轮是因为 {ciWords(ci)}</strong>
      <span>MR 的流水线 #{ci.id}{ci.at ? ` · ${clockAt(ci.at, board.at)} 没过` : ''}</span>
      {ci.url && <ExtLink href={ci.url} className="kt-btn is-small">看流水线 ↗</ExtLink>}
    </div>
  )
}

function FileRow({ f, on, onPick }: { f: OutFile; on: boolean; onPick: () => void }) {
  const p = shortPath(f.path)
  const cut = p.lastIndexOf('/') + 1
  return (
    <li>
      <button type="button" className={`kt-frow${on ? ' is-on' : ''}${f.ref ? '' : ' is-missing'}`} onClick={onPick} title={f.path} aria-pressed={on} data-testid="out-file">
        <span className={`kt-file-st is-${f.status}`}>{f.status}</span>
        {/* a long path cuts its folders, never the file's own name */}
        <span className="kt-frow-path kt-file-path is-split"><span className="kt-file-dir">{p.slice(0, cut)}</span><span className="kt-file-base">{p.slice(cut)}</span></span>
        <span className="kt-frow-num">
          {f.added !== null && <span className="kt-add">+{f.added}</span>}
          {!!f.removed && <span className="kt-del"> −{f.removed}</span>}
        </span>
      </button>
    </li>
  )
}

/** The files by group, each under its heading (an empty group says nothing), *pick* the one open; how many were left
 * out past the backend's 200, then the caller's own notes (*children*). */
function FileList({ groups, pick, onPick, more, children }: {
  groups: [string, OutFile[]][]; pick: OutFile | undefined; onPick: (f: OutFile) => void; more: number; children?: ReactNode
}) {
  return (
    <div className="kt-flist">
      {groups.map(([label, fs]) => fs.length > 0 && (
        <Fragment key={label}><p className="kt-flist-h">{label}</p><ul>{fs.map((f) => <FileRow key={f.path} f={f} on={f === pick} onPick={() => onPick(f)} />)}</ul></Fragment>
      ))}
      {more > 0 && <p className="kt-dim kt-flist-foot">还有 {more} 个文件没列出来</p>}
      {children}
    </div>
  )
}

/** One file: its bar (path, lines, which version it is, 就是请批的那一版 once the round had asked with it, the modes) and
 * its body (keyed by the file and its version: another one starts in its own default mode). */
function FilePane({ repo, reqKey, f, version, asked }: { repo: string; reqKey: string; f: OutFile; version: string; asked: boolean }) {
  const doc = isDoc(f.path)
  const [mode, setMode] = useState<Mode>(doc ? 'content' : 'diff')
  const [vs, setVs] = useState<string | null>(f.base)
  const diff = mode === 'diff'
  const fv = usePoll<FileView>(f.ref ? paths.outputsFile(repo, reqKey, { ref: f.ref, base: diff ? vs : null, path: f.path, view: diff ? 'diff' : 'source', pin: f.pin }) : null, 0)
  const vsRound = f.vs.find((v) => v.ref === vs)?.round ?? null
  const v = fv.data
  return (
    <div className="kt-fview" data-testid="out-view">
      <div className="kt-fview-bar">
        <span className="kt-fview-path">{f.path}</span>
        {f.added !== null && <span className="kt-add">+{f.added}</span>}
        {f.removed !== null && <span className="kt-del">−{f.removed}</span>}
        {f.ref && <span className="kt-fview-at" data-testid="out-at">{version}</span>}
        {asked && v?.asked && <span className="kt-asked" data-testid="out-asked" title={`请批${gateWhat(v.asked.kind)}时 Lean 记下的指纹 ${v.asked.sha8}`}>✋ 就是请批的那一版 · {v.asked.sha8}</span>}
        {f.ref && (
          <span className="kt-seg" role="group" aria-label="怎么看">
            <button type="button" className={`kt-btn is-small${mode === 'source' ? ' is-on' : ''}`} aria-pressed={mode === 'source'} onClick={() => setMode('source')}>源码</button>
            {doc && <button type="button" className={`kt-btn is-small${mode === 'content' ? ' is-on' : ''}`} aria-pressed={mode === 'content'} onClick={() => setMode('content')}>内容</button>}
            <button type="button" className={`kt-btn is-small${diff ? ' is-on' : ''}`} aria-pressed={diff} onClick={() => setMode('diff')}>
              改动{vsRound ? `（和第 ${vsRound} 轮比）` : ''}
            </button>
          </span>
        )}
      </div>
      {diff && f.vs.length > 1 && (
        <div className="kt-vs" role="group" aria-label="和哪一轮比" data-testid="out-vs">
          <span className="kt-dim">和哪一轮比</span>
          {f.vs.map((x) => (
            <button key={x.ref} type="button" className={`kt-btn is-small${vs === x.ref ? ' is-on' : ''}`} aria-pressed={vs === x.ref} onClick={() => setVs(x.ref)}>和第 {x.round} 轮比</button>
          ))}
        </div>
      )}
      <div className="kt-fview-body">
        {!f.ref ? <p className="kt-pad kt-dim" data-testid="out-why">{f.why}</p>
          : fv.error ? <p className="kt-pad" role="alert" data-testid="out-error">{fv.error}</p>
          : !v ? <p className="kt-pad kt-dim">读取中…</p>
          : v.binary ? <p className="kt-pad kt-dim">二进制文件：这里不显示</p>
          : <FileBody v={v} mode={mode} />}
      </div>
    </div>
  )
}

function FileBody({ v, mode }: { v: FileView; mode: Mode }) {
  const lines = useMemo(() => v.text.replace(/\n$/, '').split('\n'), [v.text])
  return (
    <>
      {v.truncated && <p className="kt-pad kt-dim">太长了：只显示了前 30 万个字</p>}
      {mode === 'content' && v.lang === 'markdown' ? <Markdown text={v.text} doc testid="out-md" />
        : mode === 'diff' && !v.text ? <p className="kt-pad kt-dim">这两版一样，没有改动</p>
        : (
          <ol className={`kt-code${mode === 'diff' ? ' is-diff' : ''}`} data-testid="out-code">
            {lines.map((l, i) => (
              <li key={i} className={mode !== 'diff' ? undefined : /^(\+\+\+|---|diff |index |new file|deleted file|similarity|rename )/.test(l) ? 'is-meta'
                : l.startsWith('@@') ? 'is-hunk' : l.startsWith('+') ? 'is-add' : l.startsWith('-') ? 'is-del' : undefined}><code>{l || ' '}</code></li>
            ))}
          </ol>
        )}
    </>
  )
}

function NodePane({ board, repo, reqKey, n, r, at, onAt, moment }: {
  board: ReqBoard; repo: string; reqKey: string; n: StageNode; r: Round | null; at: OutAt; onAt: (at: OutAt) => void; moment: Moment | null
}) {
  const runs = useMemo(() => new Map(board.runs.map((x) => [x.key, x])), [board.runs])
  // no round then: nothing to read (the latest round is not that moment's)
  const out = usePoll<NodeOutput>(moment && !r ? null : paths.outputs(repo, reqKey, n.id, r?.n ?? null), 0)
  const [live, setLive] = useState(false)
  // a turn still going: its reply and calls as they come
  const turns = usePoll<NodeTurns>(r ? paths.outputsTurns(repo, reqKey, n.id, r.n) : null, live && !moment ? TURNS_LIVE_MS : 0)
  useEffect(() => setLive(!!turns.data?.turns.some((t) => t.live)), [turns.data])
  // the round moved on (a commit, its end): its files and turns again; a replayed round is cut from what was read already
  const sig = r && !moment ? `${r.n}:${r.commits.length}:${r.outcome.kind}:${r.outputs.map((o) => o.sha8).join(',')}` : ''
  const seen = useRef(sig)
  useEffect(() => {
    if (seen.current !== sig) {
      out.reload()
      turns.reload()
    }
    seen.current = sig
  }, [sig]) // eslint-disable-line react-hooks/exhaustive-deps
  const run = r ? runs.get(r.run) : undefined
  const listed = out.data?.files ?? []
  // a round still going then had asked with its version only if its gate was open then (replayAt's gates)
  const asked = !moment || !r || r.ended !== null || !!openAsk(n, r)
  const { files, later } = moment && r ? filesAt(listed, r, asked, moment) : { files: listed, later: 0 }
  const groups = fileGroups(n.stage === 'code-generation', files)
  const shown = groups.flatMap(([, fs]) => fs)
  const pick = shown.find((f) => f.path === at.file) ?? shown.find((f) => f.ref) ?? shown[0]
  const meta = actorMeta(n.status)
  const head = moment ? r?.commits[r.commits.length - 1] : out.data?.head
  const title = n.unit ? `${n.title} · ${n.unit}` : n.title
  const tab = at.tab ?? 'files'
  const said = turns.data && moment ? turnsAt(turns.data, moment.t) : turns.data
  const t = said && !said.why_not ? said : null
  const tabs: [OutAt['tab'], string, number | null][] = [[undefined, '产出文件', out.data ? files.length : null], ['reply', '它的回复', null],
    ['task', '接到的任务', t?.turns.length || null], ['calls', '做了什么', t?.meta.calls || null]]
  return (
    <section className="kt-pane" aria-label={`${title}的产出`} data-testid="out-pane" data-node={n.id}>
      <div className="kt-pane-head">
        <div className="kt-pane-who">
          <RoleIcon role={n.icon} size={22} />
          <h3>{title}的产出</h3>
          <span className={`kt-pill tone-${meta.tone}`}>{nodeLabel(n, run, board)}</span>
          {board.branch && <span className="kt-run-meta kt-mono">{board.branch}{head ? ` @ ${head.slice(0, 7)}` : ''}</span>}
        </div>
        {r && (
          <p className="kt-pane-scope" data-testid="out-scope">
            第 {r.n} 轮 · <b className="kt-pane-run">{runName(run)}</b> · {clockAt(r.started, board.at)} 开始
            {r.ended !== null && ` · ${clockAt(r.ended, board.at)} ${roundWords(r, n, board)}`}
          </p>
        )}
        {n.rounds.length > 0 && (
          <div className="kt-rounds" role="group" aria-label="第几轮" data-testid="out-rounds">
            <span className="lbl">第几轮</span>
            {n.rounds.map((x) => (
              <button key={x.n} type="button" className={`kt-round tone-${openAsk(n, x) ? 'approve' : ROUND_TONE[x.outcome.kind]}${x.n === r?.n ? ' is-on' : ''}`} aria-pressed={x.n === r?.n}
                onClick={() => onAt({ node: n.id, round: x.n, tab: at.tab })} title={roundWords(x, n, board)}>
                <b>第 {x.n} 轮</b>{roundShort(x, n, board)}
              </button>
            ))}
          </div>
        )}
        {r && <CauseBanner r={r} board={board} />}
        {r && <RoundBanner n={n} r={r} board={board} replay={!!moment} />}
        {r?.note && <p className="kt-note-box"><b>它自己写的说明</b>{r.note}</p>}
        <div className="kt-tabs" role="tablist">
          {tabs.map(([k, label, count]) => (
            <button key={label} type="button" role="tab" className="kt-tab" aria-selected={(k ?? 'files') === tab} onClick={() => onAt({ ...at, tab: k })} data-testid="out-tab">
              {label}{count !== null && <sub>{count}</sub>}
            </button>
          ))}
        </div>
      </div>
      <div className="kt-pane-body">
        {moment && !r ? <p className="kt-pad kt-dim" data-testid="out-not-yet">这一步那时还没开始</p>
          : tab !== 'files' ? (r ? <SessionPane key={moment ? said?.turns.length : undefined} tab={tab} data={said} error={turns.error} r={r} n={n} run={run} board={board} />
          : <p className="kt-pad kt-dim">这一步还没开始：开始以后，这里有它每一回合的回复、接到的消息和工具调用</p>)
          : out.error ? <p className="kt-pad" role="alert">{out.error}</p>
          : !out.data ? <p className="kt-pad kt-dim">读取中…</p>
          : !files.length ? <p className="kt-pad kt-dim" data-testid="out-none">{later ? '这一轮那时还没提交文件' : out.data.why ?? '这一轮没有提交文件'}</p>
          : (
            <div className="kt-files-pane">
              <FileList groups={groups} pick={pick} onPick={(f) => onAt({ ...at, file: f.path })} more={out.data.more}>
                {later > 0 && <p className="kt-dim kt-flist-foot" data-testid="out-later">这一轮那时{r?.ended !== null ? '已经结束' : '还在做'}：那以后才提交的 {later} 个文件这里没列</p>}
                {run?.kind === 'runner' && <p className="kt-dim kt-flist-foot">都在运行机推上来的提交里：这台机器取回来以后能看全</p>}
              </FileList>
              {pick && (
                <FilePane key={`${pick.path}@${pick.ref}`} repo={repo} reqKey={reqKey} f={pick} asked={asked}
                  version={`第 ${r?.n ?? 0} 轮的版本 · ${pick.ref === 'worktree' ? '还没提交，和 Lean 记下的指纹一样' : `提交 ${pick.ref?.slice(0, 7)}`}`} />
              )}
            </div>
          )}
      </div>
    </section>
  )
}

/** The drawer over the board, focused as it opens: its name, ‹ › through its steps, ✕ (Esc is the board's); in a
 * replay the replay's controls and what that moment shows (*then*); the step bar keeps the step on (*picked*) in view. */
function Frame({ title, board, reqKey, moment, bar, then, nav, picked, prev, next, onClose, children }: {
  title: string
  board: ReqBoard
  reqKey: string
  moment: Moment | null
  bar: ReactNode
  then: string
  nav: { label: string; steps: ReactNode }
  picked: string
  /** none: there is no step that way */
  prev?: () => void
  next?: () => void
  onClose: () => void
  children: ReactNode
}) {
  const box = useRef<HTMLElement | null>(null)
  const stepBar = useRef<HTMLElement | null>(null)
  useEffect(() => box.current?.focus(), [])
  useEffect(() => {
    stepBar.current?.querySelector('[aria-current="step"]')?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' })
  }, [picked])
  return (
    <div className="kt-drawer-backdrop" onClick={(e) => e.target === e.currentTarget && onClose()} data-testid="out-drawer">
      <aside ref={box} className="kt-drawer" role="dialog" aria-modal="true" aria-label={title} tabIndex={-1}>
        <div className="kt-drawer-top">
          <h2>{title}</h2>
          <span className="kt-dim">{board.row.title && board.row.title !== reqKey ? `${board.row.title} · ${reqKey}` : reqKey}</span>
          <span className="kt-grow" />
          <button type="button" className="kt-btn is-small" onClick={prev} disabled={!prev}>‹ 上一步</button>
          <button type="button" className="kt-btn is-small" onClick={next} disabled={!next}>下一步 ›</button>
          <button type="button" className="kt-btn is-small" onClick={onClose} aria-label="关掉产出" title="关掉（Esc）">✕</button>
        </div>
        {moment && (
          <div className="kt-drawer-replay" data-testid="out-replay">
            {bar}
            <p className="kt-dim">回放：这是 {fmtWhen(moment.t)} 那一刻的产出，{then}；退出回放看现在的</p>
          </div>
        )}
        <nav ref={stepBar} className="kt-steps" aria-label={nav.label}>{nav.steps}</nav>
        {children}
      </aside>
    </div>
  )
}

type DrawerProps = {
  board: ReqBoard
  repo: string
  reqKey: string
  at: OutAt
  /** move the drawer (another step, round or file) */
  onAt: (at: OutAt) => void
  onClose: () => void
  /** 回放: the moment *board* is replayed at, and the replay's controls */
  moment?: Moment | null
  bar?: ReactNode
}

/** A fast run's 写代码 (out=code): by commit; else the stages, by round. */
export function OutputsDrawer(p: DrawerProps) {
  return p.board.steps && p.at.node === 'code' ? <CommitsDrawer {...p} /> : <StagesDrawer {...p} />
}

function StagesDrawer({ board, repo, reqKey, at, onAt, onClose, moment = null, bar }: DrawerProps) {
  const lanes = laneNodes(board)
  const steps = board.nodes.filter((x) => lanes.includes(x) || x.id === at.node)
  const i = steps.findIndex((x) => x.id === at.node)
  const n = steps[i]
  const r = n ? (n.rounds.find((x) => x.n === at.round) ?? n.rounds[n.rounds.length - 1] ?? null) : null
  return (
    <Frame title="每一步的产出" board={board} reqKey={reqKey} moment={moment} bar={bar} then="只有那时已经开始的轮次" picked={at.node} onClose={onClose}
      prev={i > 0 ? () => onAt({ node: steps[i - 1].id }) : undefined} next={i >= 0 && i < steps.length - 1 ? () => onAt({ node: steps[i + 1].id }) : undefined}
      nav={{ label: '每一步', steps: steps.map((x, k) => (
        <button key={x.id} type="button" className={`kt-step tone-${actorMeta(x.status).tone}${x.id === at.node ? ' is-on' : ''}`} aria-current={x.id === at.node ? 'step' : undefined}
          onClick={() => onAt({ node: x.id, tab: at.tab })} data-testid="out-step">
          <span className="kt-step-n">{k + 1}</span>
          <RoleIcon role={x.icon} size={14} />
          {x.unit ? `${x.title} · ${x.unit}` : x.title}
        </button>
      )) }}>
      {n ? <NodePane key={`${n.id}#${r?.n ?? 0}`} board={board} repo={repo} reqKey={reqKey} n={n} r={r} at={at} onAt={onAt} moment={moment} />
        : <p className="kt-pad kt-dim">运行过程里没有这一步了：点上面的一步看它的产出</p>}
    </Frame>
  )
}

function CommitsDrawer({ board, repo, reqKey, at, onAt, onClose, moment = null, bar }: DrawerProps) {
  const list = usePoll<CommitsOutput>(paths.outputsCommits(repo, reqKey), 0)
  // another commit on the live board: the list again (a replay cuts the one read already)
  const sig = moment ? '' : board.belt.filter((x) => x.kind === 'commit').map((x) => x.sha).join(',')
  const seen = useRef(sig)
  useEffect(() => {
    if (sig && seen.current !== sig) list.reload()
    seen.current = sig
  }, [sig]) // eslint-disable-line react-hooks/exhaustive-deps
  const data = list.data
  const commits = (data?.commits ?? []).filter((x) => !moment || moment.made(x.sha))
  const want = at.commit
  const c = want ? commits.find((x) => x.sha.startsWith(want)) : undefined
  const i = !want ? 0 : c ? commits.indexOf(c) + 1 : -1
  const to = (k: number): OutAt => (k === 0 ? { node: at.node } : { node: at.node, commit: commits[k - 1].sha.slice(0, 12) })
  return (
    <Frame title="写代码的产出" board={board} reqKey={reqKey} moment={moment} bar={bar} then="只有那时已经做了的提交" picked={want ?? ''} onClose={onClose}
      prev={i > 0 ? () => onAt(to(i - 1)) : undefined} next={i >= 0 && i < commits.length ? () => onAt(to(i + 1)) : undefined}
      nav={{ label: '每个提交', steps: [null, ...commits].map((x, k) => (
        <button key={x?.sha ?? 'all'} type="button" className={`kt-step tone-${!x ? 'info' : x.records_only ? 'idle' : 'ok'}${i === k ? ' is-on' : ''}`}
          aria-current={i === k ? 'step' : undefined} onClick={() => onAt(to(k))} title={x?.subject} data-testid="out-step">
          {x ? <><span className="kt-step-n">{k}</span><code>{x.sha.slice(0, 7)}</code>{clip(x.subject, 14)}{x.records_only ? '（只改记录）' : ''}</>
            : <><RoleIcon role="code" size={14} />全部改动</>}
        </button>
      )) }}>
      {list.error ? <p className="kt-pad" role="alert">{list.error}</p>
        : !data ? <p className="kt-pad kt-dim">读取中…</p>
        : !commits.length ? <p className="kt-pad kt-dim" data-testid="out-none">{moment && data.commits.length ? '那时运行分支上还没有提交' : data.why ?? '运行分支上还没有提交'}</p>
        : i < 0 ? <p className="kt-pad kt-dim" data-testid="out-none">{moment ? `那时还没有提交 ${want}` : `运行分支上没有提交 ${want}：点上面的一个提交看它改了什么`}</p>
        : <CommitPane key={c?.sha ?? 'all'} board={board} repo={repo} reqKey={reqKey} data={data} commits={commits} c={c ?? null} file={at.file}
            onFile={(path) => onAt({ ...to(i), file: path })} moment={moment} />}
    </Frame>
  )
}

/** One commit's files, each compared with the commit before it; or 全部改动 (*c* null): each file at the last commit
 * that changed it, compared with where the branch left; in a replay only the files whose last change was made by then. */
function CommitPane({ board, repo, reqKey, data, commits, c, file, onFile, moment }: {
  board: ReqBoard; repo: string; reqKey: string; data: CommitsOutput; commits: CommitOut[]; c: CommitOut | null; file?: string
  onFile: (path: string) => void; moment: Moment | null
}) {
  const last = commits[commits.length - 1]
  const all = c ? c.files : data.files
  const files = c || !moment ? all : all.filter((f) => f.ref !== null && moment.made(f.ref))
  const groups = fileGroups(true, files)
  const shown = groups.flatMap(([, fs]) => fs)
  const pick = shown.find((f) => f.path === file) ?? shown.find((f) => f.ref) ?? shown[0]
  const sha7 = (c ?? last).sha.slice(0, 7)
  const branch = data.branch ?? board.branch
  return (
    <section className="kt-pane" aria-label={c ? `提交 ${sha7}的改动` : '全部改动'} data-testid="out-pane">
      <div className="kt-pane-head">
        <div className="kt-pane-who">
          <RoleIcon role="code" size={22} />
          <h3>{c ? `提交 ${sha7}` : '全部改动'}</h3>
          {c?.records_only && <span className="kt-pill tone-idle">只改记录</span>}
          {branch && <span className="kt-run-meta kt-mono">{branch} @ {last.sha.slice(0, 7)}</span>}
        </div>
        <p className="kt-pane-scope" data-testid="out-scope">
          {c ? `${c.subject} · ${c.by === 'bot' ? '运行机' : '本机'} · ${clockAt(c.at, board.at)}` : `${commits.length} 次提交加起来，和运行分支出发的地方比`}
        </p>
      </div>
      <div className="kt-pane-body">
        {!files.length ? <p className="kt-pad kt-dim" data-testid="out-none">{c ? '这个提交没有改文件' : '这些提交没有改文件'}</p> : (
          <div className="kt-files-pane">
            <FileList groups={groups} pick={pick} onPick={(f) => onFile(f.path)} more={c ? c.more : data.more}>
              {files.length < all.length && <p className="kt-dim kt-flist-foot" data-testid="out-later">那以后才改（或又改了）的 {all.length - files.length} 个文件这里没列</p>}
            </FileList>
            {pick && <FilePane key={`${pick.path}@${pick.ref}`} repo={repo} reqKey={reqKey} f={pick} asked={false} version={c ? `提交 ${sha7}` : `全部改动 · 到最后一个提交 ${sha7}`} />}
          </div>
        )}
      </div>
    </section>
  )
}
