/**
 * The backend contract of the Brain app: every route under /api/apps/brain and the JSON it answers.
 * The backend (backend/core/*.py) produces exactly these shapes; tests/test_contract.py checks the
 * fixtures in src/test/fixtures/*.json against the backend's own output keys.
 *
 * Words: a requirement (需求) has a key like PAY-3. It is claimed (claim/<KEY> branch), developed
 * locally with AI-DLC Lean in a brain-dev session (run branch ai/<owner>/<KEY>-<slug>), handed over
 * to the team runner (bridge + runnerd + a brain-run session), may park on a question (D-<n> on the
 * GitLab MR), waits for acceptance, and is merged. The Brain is brain/PRODUCT.md, TECH.md,
 * IMPROVEMENT.md, PROJECT.md on the default branch.
 *
 * Times are epoch seconds (number) unless the field says ISO. Missing facts are null, never guessed.
 * No route ever returns a token, a secret or a signing key path's content.
 */

export const BASE = '/api/apps/brain'

// ---------------------------------------------------------------- shared

export type Level = 'OK' | 'WARN' | 'FAIL'
/**
 * One check, said for a person (backend/core/problems.py): OK ✓, WARN ! (a reminder, stops nothing), FAIL ✗. The
 * backend always sends every field; the optional ones are optional here only so older literals still compile.
 */
export interface CheckRow {
  level: Level
  /** what is wrong (or right): plain short Chinese (code names such as `lean.json` or runs/** may be in it) */
  text: string
  /** 做什么: one plain Chinese line ('' for an OK row, or when there is nothing to do) */
  todo?: string
  /** the one-click fix: agent (add Lean's hooks to ~/.kiro/agents/brain-dev.json) and engine (kirocrew engine kas),
   * both POST /crew/fix {what}; kit (reinstall brain-kit in `repo`: POST /import/upgrade {path: repo}); me (the name
   * brain-kit goes by in `repo`: the page's input, POST /me/name); null: none */
  fix?: 'agent' | 'engine' | 'kit' | 'me' | null
  /** the repo (its path) the row is about; null for a machine-wide row */
  repo?: string | null
  /** what brain-kit (or the app's older code) said, verbatim, for debugging (shown folded); '' when there is nothing
   * more than text and todo */
  raw?: string
}

/** Where a requirement is now. */
export type ReqState =
  | 'claimed' // 已认领：only claim/<KEY>, no run yet
  | 'local' // 本地开发中：run branch on this machine, not handed over
  | 'handed_over' // 已交接：run.json has a hand-over, the bridge has not taken it yet
  | 'rejected' // 接手被拒：the bridge refused the hand-over (reason in waits.text)
  | 'running' // 运行机在跑
  | 'parked' // 停下等回答：park.json waiting / an open D-<n>
  | 'acceptance' // 等验收：parked on an acceptance question (Lean RELEASE)
  | 'merged' // 已合并：run.json is on the default branch
  | 'closed' // 已关闭：the claim was closed (duplicate, dropped)
  | 'unclaimed' // 待认领：a small requirement `brain split` filed for others: nobody has it yet
  | 'merged_into_parent' // 已合进大需求：a small requirement merged into its big requirement's branch (main comes with it)

export const STATE_LABEL: Record<ReqState, string> = {
  claimed: '已认领',
  local: '本地开发中',
  handed_over: '已交接',
  rejected: '接手被拒',
  running: '运行机在跑',
  parked: '停下等回答',
  acceptance: '等验收',
  merged: '已合并',
  closed: '已关闭',
  unclaimed: '待认领',
  merged_into_parent: '已合进大需求',
}

/** Who the requirement waits on right now. */
export type WaitWho = 'you' | 'agent' | 'runner' | 'bridge' | 'product' | 'builder' | 'people' | 'nobody'

/**
 * The step whoever `who` is takes now (UI问题清单 #1: one judgment, every view reads it: 待办, the badges, 交付总览's
 * lanes, the board): approve (a Lean approval line, in the session), reply (the local session is idle: your turn to
 * talk), tool (a tool call in the session waits for approval), start (claimed, no run yet: 开工), answer (a question:
 * in the session for a local run, on the MR for a handed-over one), accept (acceptance), redo_handoff (the hand-over
 * was refused), label_handoff (pushed without its label), push_handoff (the hand-over commit is on this machine
 * only: its push failed, nothing went out; 交接给运行机 again), write_back (merged with new knowledge to write back into
 * the Brain), watch (merged, in its 14 days: nobody's turn), none (nothing to do: AI or the runner at work, or done).
 */
export type WaitDo = 'approve' | 'reply' | 'tool' | 'start' | 'answer' | 'accept' | 'redo_handoff' | 'label_handoff'
  | 'push_handoff' | 'write_back' | 'watch' | 'none'

export interface Waits {
  who: WaitWho
  /** one short Chinese sentence, e.g. 「等你批准需求（在会话里自己打一行）」 */
  text: string
  /** the exact Lean approval line the person must type themselves in the session, when that is what it waits on */
  line: string | null
  /** what text was made from, verbatim, for debugging (shown folded): a refused hand-over's reasons as the bridge wrote
   * them on the MR (text has them in short Chinese, backend/core/problems.py); '' otherwise. The backend always sends it */
  raw?: string
  /** the step (see WaitDo). The backend always sends it; optional only so older literals compile */
  do?: WaitDo
  /** since when it waits like this (epoch s): the moment the session went idle, the approval was asked, the claim was
   * made, the question was asked; null when unknown. The backend always sends it */
  since?: number | null
  /** where the step is taken: the local session, the MR, this app (开需求 / 待办), nowhere. The backend always sends it */
  where?: 'session' | 'mr' | 'app' | null
  /** the reply is Lean's review in a NEW session (开审查会话: the session that wrote the code may not review it), not a
   * turn in the open one. The backend always sends it */
  review?: boolean
  /** what to do, in words, when the step alone does not say it: a refused hand-over's reasons' own 做什么
   * (backend/core/problems.py REASON_RULES); '' otherwise. The backend always sends it */
  todo?: string
}

export interface LeanSummary {
  /** the current Lean stage id (see stage_state); null while Lean recorded no stage on the way yet */
  stage: string | null
  /** its Chinese label, e.g. 需求分析 */
  stage_label: string | null
  /** its AI-DLC number, e.g. 2.3 (发布: ✓) */
  stage_n: string | null
  /** the state of `stage`: started (being worked on); todo (nothing open: the next stage to do after the last one done);
   * done or skipped (every stage up to the last is done: the requirement's Lean work is finished); null when Lean
   * recorded no stage yet */
  stage_state: StageState | null
  /** the stages on the way done or skipped, of how many: Lean's own, and an optional one (Ideation's, ci-pipeline,
   * Operations') once recorded; a phase skipped as a whole is not among them (backend/core/lean_view.py summary) */
  done: number
  total: number
  /** UI问题清单 #8, the one way to say the progress (vocab.leanProgress): stages done, stages skipped, the next one to do
   * (null: every stage on the way done or skipped), and the phases skipped as a whole, said once: 构思、运维 */
  done_n: number
  skipped_n: number
  next: { id: string; label: string; n: string } | null
  skipped_phases: string[]
}

export interface MrRef { iid: number; url: string | null; state: string | null }

/** One row of 交付总览; also the header of 需求现状. */
export interface Requirement {
  /** repo id from /status (r1, r2 …); team-mode rows use 'runner' */
  repo: string
  /** a team-mode row on the runner: the listed repo (r1 …) of its GitLab project, whose view and pill hold it; null
   * when this machine does not list that project, and for every other row. The backend always sends it */
  home?: string | null
  key: string
  title: string
  owner: string | null
  /** where the requirement came from (brainkit.intents): product, debt, alert, schedule, eval-gap, gap */
  source: string
  state: ReqState
  state_label: string
  /** where the facts were read: this machine's worktree, a branch ref, the default branch, the runner workspace */
  where: 'worktree' | 'branch' | 'main' | 'runner' | 'claim'
  branch: string | null
  intent: string | null
  lean: LeanSummary
  /** the current Lean stage as text, as the backend writes it: 「9/11 代码生成」 (started), 「3/11 用户故事（下一步）」
   * (todo), 「11/11 发布（做完）」 (done or skipped); null when Lean recorded no stage */
  stage: string | null
  /** Lean's verify runs that failed in a row since the last one that passed (0: the last passed, or none ran) */
  verify_fails: number
  waits: Waits
  /** the first line of its 变更说明 (brain mr-desc's 一句话, from runs/<KEY>/MR.md or the run's records at the commit
   * the row is read at); null before it has a run, while it is being made, or when brain-kit cannot make one */
  summary: string | null
  /** credits of its sessions this machine reads (null: none known). KiroCrew reports credits only. */
  credits: number | null
  /** false: the credits leave something out (its detail's credits.missing says what) */
  credits_whole: boolean
  /** latest activity we know of (commit, Lean event, session) */
  updated_at: number | null
  mr: MrRef | null
  /** this machine's working directory for it (the run's worktree), if any */
  worktree: string | null
  /** the team runner's run ids (RUN-PAY-3-1 …) */
  run_ids: string[]
  sessions: number
  /** other open requirements whose expected paths overlap this one's */
  overlap: string[]
  /** open D-<n> questions */
  open_questions: string[]
  /** expected paths from run.json (overlap warnings come from these) */
  expected_paths: string[]
  /** its repo has no remote: it is done only on this machine, never handed over (no runner, no MR). The backend
   * always sends it */
  local_only?: boolean
  /** its brain-dev session on this machine (KiroCrew's chat URL: 打开会话), null when there is none. The backend always
   * sends it */
  session_url?: string | null
  /** a big requirement split into small ones (brain split), one of those, or one opened as a big one: null otherwise */
  family: ReqFamily | null
  /** when this machine last pushed its run branch at a Lean checkpoint (epoch seconds); null: not from here. The
   * backend always sends it */
  progress_pushed_at?: number | null
}

// ---------------------------------------------------------------- 大需求拆成小需求 (brain-kit's split, phase 1)

/** A small requirement's state, brain-kit's (intents.STATES): unclaimed 待认领, claimed, in_progress (its owner's run
 * branch is there), merged (into the big requirement's branch, or main), closed. */
export type ChildState = 'unclaimed' | 'claimed' | 'in_progress' | 'merged' | 'closed'
/** A big requirement's family, brain-kit's (split.families): in_progress; ready_to_release (every small one merged:
 * the owner reviews and releases); done (released, every small one merged); closed. */
export type FamilyState = 'in_progress' | 'ready_to_release' | 'done' | 'closed'

export interface FamilyProgress {
  merged: number
  /** the small requirements that are not closed */
  total: number
  unclaimed: number
  claimed: number
  in_progress: number
  closed: number
  /** brain-kit's own words, e.g. 「1/3 merged」 */
  text: string
}

/** A dependency of a small requirement, and how far it is. */
export interface FamilyDep {
  key: string
  unit: string | null
  state: ChildState | null
  owner: string | null
  /** merged into the big requirement's branch: it blocks nothing any more */
  merged: boolean
  /** one plain line: 「PAY-2（api，alice 在做）」 */
  text: string
}

/** A big requirement's small one, in short (its parent's card, board and 交付清单). */
export interface FamilyChild {
  key: string
  unit: string | null
  title: string
  state: ChildState
  /** its words: 待认领, alice 认领了, alice 在做, 已合并, 已关闭 */
  state_label: string
  owner: string | null
  depends_on: string[]
  /** its dependencies not merged into the big requirement's branch yet */
  blocked_by: string[]
}

/** What this person can do on it here and now: null when it is not offered, else whether it can be done and why not. */
export interface FamilyAct {
  ok: boolean
  /** why not, one plain line ('' when ok) */
  why: string
}
export interface FamilyActs {
  /** 拆成小需求 (a big or split requirement of yours, its worktree on this machine, DESIGN approved): preview first */
  split: FamilyAct | null
  /** 认领 (a small requirement nobody has) */
  claim: FamilyAct | null
  /** 放弃认领 (yours, not started) */
  release: FamilyAct | null
  /** 开工 (nobody's or yours, not started): not ok while a dependency has not merged, naming it */
  start: FamilyAct | null
  /** 「在 <DEP> 的分支上先做」 (`brain start --on DEP`): the one dependency it may be built on, only when brain-kit
   * allows it (its only unmerged dependency, and that one's owner has a run branch); null otherwise */
  start_on: string | null
  /** 同步 (`brain sync`: yours, started, its worktree on this machine) */
  sync: FamilyAct | null
}

/**
 * A big requirement split into small ones others claim and build in parallel (brain-kit's `brain split`, docs/安装和使用.md
 * §4.7), one of those, or a requirement opened as a big one that is not split yet. From brain-kit's own listing (`brain
 * intent list --all --json`, docs/intents.md §2.5, run in the background), else from this machine's refs (source
 * 'local': the claims as last fetched, before brain-kit answered).
 */
export interface ReqFamily {
  /** big: opened as a big one (or its unit-of-work.md lists units), not split yet; parent: split; child: a small one */
  role: 'big' | 'parent' | 'child'
  /** the big requirement's key (the parent's own) */
  parent: string
  /** the big requirement's title */
  parent_title: string
  /** the big requirement's run branch: where its small ones start from and where their MRs go */
  branch: string | null
  /** child: its unit in unit-of-work.md */
  unit: string | null
  /** child: brain-kit's state of it */
  state: ChildState | null
  /** child: 待认领, alice 认领了, alice 在做, 已合并, 已关闭 */
  state_label: string
  /** child: who has it (null: nobody, 待认领) */
  owner: string | null
  /** child: the small requirements it depends on, each with how far it is */
  depends_on: FamilyDep[]
  /** child: those not merged into the big requirement's branch yet (keys): it cannot start before (or --on one) */
  blocked_by: string[]
  /** child: its unit's paths and contracts (unit-of-work.md) */
  paths: string[]
  contracts: string[]
  /** parent: its small requirements, in filing order */
  children: FamilyChild[]
  /** parent: the roll-up; null for a child, or before brain-kit answered */
  progress: FamilyProgress | null
  /** parent: the family's state (null for a child) */
  family_state: FamilyState | null
  /** its words: 小需求在做, 都合并了：等大需求评审和发布, 发布了、小需求都合并了, 已关闭 */
  family_label: string
  /** parent: Lean's RELEASE is approved for it */
  released: boolean
  acts: FamilyActs
  source: 'brainkit' | 'local'
  /** when brain-kit's listing was read (epoch s); null for local */
  at: number | null
}

// POST /requirements/{repo}/{key}/split {apply}: `brain split <KEY> --dry-run --json` (apply false: the preview, nothing
// written), then `brain split <KEY> --json` (apply true) in the big requirement's worktree on this machine
export interface SplitBody { apply: boolean }
export interface SplitAction {
  unit: string
  /** brain-kit's kind: new | same | update | changed | close | removed | close_duplicate | duplicate | raced */
  kind: string
  /** its words: 新登记（待认领）, 没变, 拆分变了，但已经有人认领或开工：没改 … */
  kind_label: string
  /** the small requirement's key (null: a new one in a preview, its key comes with the split) */
  key: string | null
  title: string
  paths: string[]
  /** what it depends on: keys, or 「api（新）」 for a new one in a preview */
  depends_on: string[]
  contracts: string[]
  /** why, in brain-kit's words (the kinds that need a word with whoever has it) */
  why: string[]
  /** say it to whoever has it: the split changed under a claimed or started one */
  attention: boolean
}
export interface SplitResult {
  ok: boolean
  dry_run: boolean
  parent: string
  /** the big requirement's run branch (pushed by the split) */
  branch: string | null
  /** unit-of-work.md and the DESIGN approval, 8 hex each */
  units_sha: string | null
  design_sha: string | null
  actions: SplitAction[]
  /** brain-kit's warnings, as check rows (short Chinese, its words in raw) */
  warnings: CheckRow[]
  /** one plain sentence: what it would do, did, or why not */
  text: string
  /** why it was refused, each as a check row with 做什么 */
  problems: CheckRow[]
  /** brain split's output, verbatim (shown folded) */
  output: string
}

// POST /requirements/{repo}/{key}/claim {release}: `brain claim <KEY>` (first one wins) / `brain claim --release <KEY>`
export interface ClaimBody { release?: boolean }
export interface ClaimResult {
  ok: boolean
  /** 「认领了 PAY-3」, 「PAY-3 已经是你的了」, 「晚了一步：PAY-3 已经被 alice 认领了」, 「放回去了：PAY-3 又是待认领」 */
  text: string
  /** who has it now (the one who was first, when it was not you) */
  owner: string | null
  problem: CheckRow | null
  output: string
}

// POST /requirements/{repo}/{key}/start {on?, open_session?, trust?}: `brain start --key <KEY> [--on <DEP>] --worktree
// <work_root>/<repo>/<key>-<unit>` (it claims it on the way), then its brain-dev session in that worktree
export interface ChildStartBody { on?: string; open_session?: boolean; trust?: boolean }
export interface ChildStartResult {
  ok: boolean
  key: string
  branch: string | null
  worktree: string | null
  /** --on: the dependency it was started on */
  on: string | null
  text: string
  /** refused for its dependencies: each, with who has it */
  blocked: FamilyDep[]
  /** it may be started on this dependency's branch instead (brain-kit allows it) */
  can_on: string | null
  problem: CheckRow | null
  output: string
  slot: string | null
  url: string | null
  session_error: string
}

// POST /requirements/{repo}/{key}/sync: `brain sync` in the small requirement's worktree (the big one's branch merged in;
// Lean's own files resolved)
export interface SyncResult {
  ok: boolean
  result: 'merged' | 'up_to_date' | 'conflicts' | 'refused'
  text: string
  /** the files left with conflicts (resolve, git add, git commit) */
  conflicts: string[]
  problem: CheckRow | null
  output: string
}

// ---------------------------------------------------------------- GET /status

export interface KitView { tracker: string; req_prefix: string; default_branch: string; gitlab_base: string; gitlab_project: string }

export interface RepoInfo {
  id: string
  path: string
  name: string
  /** its kit.json loaded: the views use it */
  ok: boolean
  /** why it cannot be used ('' when ok, and when it is pending) */
  error: string
  kit: KitView | null
  /** not usable yet, but brain-kit is on its way (导入仓库): the install branch, or merged and not pulled */
  pending: PendingInstall | null
  /** who brain-kit says the person is there (a loaded repo; null otherwise) */
  me: RepoMe | null
  /** it has an origin: requirements may be handed over, 从远端取最新 works. The backend always sends it */
  remote?: boolean
  /** how it keeps up with the team (a loaded repo; null otherwise). The backend always sends it */
  sync?: RepoSync | null
}

/** A repo's last fetch (every few minutes by itself, or 「从远端取最新」), in repo.fetch()'s words. */
export interface FetchState {
  at: number
  ok: boolean
  /** what happened and what to do, in plain Chinese */
  text: string
  /** git's own words and the command for the terminal (细节) */
  raw: string
}

/** progress.py: the person's own run branches pushed at Lean's checkpoints, origin fetched every few minutes. */
export interface RepoSync {
  /** settings.auto_push for it (on unless turned off); null: done only on this machine (no remote, or no GitLab), so
   * it has no push setting */
  auto_push: boolean | null
  /** why its pushes cannot happen now ('' when they can): this machine is the team runner, or the app cannot tell the
   * person's name in it */
  push_why: string
  /** the line to type for push_why ('' when there is none) */
  push_cmd: string
  /** null before the first fetch */
  fetch: FetchState | null
}

/**
 * Who brain-kit says the person is in a repo, by its own rule: a GitLab token (GitLab's username, asked by the backend),
 * $BRAIN_USER or git config brain.user, else the login name in a repo without GitLab (a brain-kit that falls back to
 * it). Never a token.
 */
export interface RepoMe {
  /** the name brain-kit goes by there; null: the app cannot tell (no requirement with an owner is the person's there):
   * GitLab has not said yet (source token, nothing asked), or brain-kit would not know */
  name: string | null
  source: 'token' | 'env' | 'git' | 'login' | 'none'
  /** kit.json gitlab.base ('' without GitLab) */
  gitlab: string
  /** one line for the page, e.g. 「alice（这台机器的登录名：这个仓库不用 GitLab）」 */
  text: string
  /** brain-kit would not know: the page shows a small input (label 「你的 GitLab 用户名」 or 「你的名字」, prefilled with
   * suggest, the login name; why: one line) */
  ask: boolean
  label: string
  why: string
  suggest: string
  /** what is wrong with the name that is set ('' when nothing) */
  error: string
}

/** POST /me/name: git config brain.user <name> in that repo (brain-kit's USER_RE, lowercased; no token, ever). */
export interface MeNameBody { path: string; name: string }
export interface MeNameResult {
  ok: true; text: string; me: RepoMe; status: Status
  /** the command it ran (git config brain.user <name>), for 细节 */
  raw?: string
}

/** What this install is (settings.role): personal (the default, a person's own machine: 运行机 shows the team runner
 * as GitLab shows it), or team_server (the team's one runner: 设置 has 团队模式 and 一键建运行机, 运行机 is its own
 * bridge). Settings of 0.4.2 and before name none: a machine in team mode then is the team server. */
export type InstallRole = 'personal' | 'team_server'

export interface Status {
  /** team: team mode runs here (only ever on a team server) */
  app: { name: string; version: string; team: boolean; role: InstallRole }
  brainkit: {
    path: string
    ok: boolean
    version: string | null
    error: string
    /** optional brain-kit modules: true when importable (split: brain split, claim, start --key, sync; 0d52e97; proof:
     * 证据, what `lean verify` sealed) */
    modules: { trace: boolean; evaluate: boolean; golden: boolean; intents: boolean; mrdesc: boolean; split: boolean; proof: boolean }
  }
  repos: RepoInfo[]
  crew: { rows: CheckRow[]; fix: { agent: boolean; engine: boolean }; checked_at: number | null }
  /** who this machine says the person is: git config brain.user, or that a GitLab token is present (never the token) */
  me: { user: string | null; token: boolean; how: string }
  team: TeamStatus
  counts: { todo: number }
  /** repo_ids.json cannot be read (UI-19): what is wrong and what to do, in words; '' when it reads fine */
  repo_ids_problem: string
}

export interface TeamStatus {
  enabled: boolean
  ok: boolean
  /** why the bridge did not start, in words ('' when it did) */
  error: string
  /** what to do about it, in words (problems.team_start). The backend always sends it */
  error_todo?: string
  /** the error as it was (file names, keys), for 细节. The backend always sends it */
  error_raw?: string
  last_loop_at: number | null
  loop_error: string
  /** the signer runs as this app's OS user (the local trial, or its signed/ and refused/ are writable here): it would
   * sign whatever the app asks, so it is no boundary (never on a real team runner) */
  same_user: boolean
  signer: { pending: number; refused: number }
}

// ---------------------------------------------------------------- 导入仓库: /import/*, POST /repos/remove

/**
 * A listed (or inspected) repo whose checkout has no .brain/kit.json yet, but brain-kit is on its way: on the install
 * branch the app made (branch), installed in that branch's worktree and not committed yet (uncommitted: a hook said
 * no), or already on the default branch and not pulled into this checkout (merged).
 */
export interface PendingInstall {
  /** branch: on the install branch (「合并到 <default>」 when it fast-forwards); uncommitted: a hook said no; merged:
   * the default branch has it (「拉取」, or switch to it) */
  state: 'branch' | 'uncommitted' | 'merged'
  /** the install branch (brain/install); for merged, where it is (origin/main) */
  branch: string
  /** the install branch's worktree under work_root (null when it is gone) */
  worktree: string | null
  /** the install branch's commit (12 hex) */
  commit: string | null
  pushed: boolean
  mr_url: string | null
  /** e.g. 「安装在 brain/install 分支上，合并后生效」 */
  text: string
  /** what to do next, one sentence */
  next: string
  /** the short state the list shows (「还没合进 main」, 「还没推送」, 「MR 等合并」, 「要拉取」 …); a repo without a remote
   * never says it waits for a merge */
  word: string
  /** 推送并开 MR: the MR's target (the default branch), origin's URL (never a password), the GitLab project */
  target: string
  remote_url: string
  gitlab_base: string
  gitlab_project: string
  /** a GitLab token is on this machine and the project is known: the app opens the MR itself */
  can_open_mr: boolean
  /** state branch: 「合并到 <target>」 (POST /import/merge): can it fast-forward now, why not, and whether it asks first
   * (a repo with a remote: the remote does not have it until pushed) */
  merge: { ok: boolean; why: string; confirm: boolean } | null
  /** state merged, on origin only: 「拉取」 (POST /import/pull) can run now, why not */
  pull: { ok: boolean; why: string } | null
}

/** Where an install (or a kit update) goes: inplace (the default: in the checkout, on its branch, committed there, as
 * brain-kit's CLI does) or branch (a new branch in its own worktree, for an MR). */
export type InstallMode = 'inplace' | 'branch'
export interface InstallChoice {
  mode: InstallMode
  /** 「直接装在 main」, 「装到新分支，开 MR 给团队看」 */
  title: string
  /** one plain line: what it means */
  line: string
  ok: boolean
  /** why it cannot be done now ('' when ok) */
  why: string
}
/** POST /import/choices {path, purpose} (and ImportInspect.install): the options in order (in place first; one only when
 * there is nothing to choose: no remote, nothing uncommitted), the recommended one, and why. */
export interface InstallChoices {
  options: InstallChoice[]
  recommend: InstallMode
  /** the checkout's branch (null: detached) */
  current: string | null
  /** the default branch */
  target: string
  /** origin's URL ('' without a remote) */
  remote_url: string
  /** the person's uncommitted changes (up to 10), and the ones that touch what brain install writes */
  dirty: string[]
  overlap: string[]
}
export interface ChoicesBody { path: string; purpose?: ImportPurpose }

/** GET /import/defaults */
export interface ImportDefaults {
  /** where a clone goes by default (the folder that holds work_root, else ~/workplace) */
  clone_parent: string
  work_root: string
  /** the install branch's default name */
  branch: string
  /** a GitLab token is on this machine (never the token) */
  token: boolean
  brainkit_ok: boolean
  /** the checkouts found on this machine to pick from (importer.found_repos) */
  found: FoundRepo[]
}
/** A git checkout under ~/workplace or ~/workspace that 导入仓库 offers to pick (importer.found_repos), sent as
 * ImportDefaults' `found` once the backend's GET /import/defaults has it */
export interface FoundRepo { path: string; name: string; installed: boolean; listed: boolean }

/** One row of 检查: ✓ OK, ! WARN (worth knowing, stops nothing), ✗ FAIL; why, and what to do. */
export interface ImportCheck { id: string; level: Level; label: string; why: string; todo: string }

/** The kit.json values the install form asks for (what `brain install` asks you to check). */
export interface KitForm {
  req_prefix: string
  tracker: string
  /** tracker jira only */
  jira_base: string
  gitlab_base: string
  gitlab_project: string
  default_branch: string
  team: string
}

/** What the wizard offers after 检查: fix (something is wrong), listed (already in the list), add (installed),
 * pending (on the install branch, or merged), install (not installed: the install form). */
export type ImportNext = 'fix' | 'listed' | 'add' | 'pending' | 'install'
export type KitState = 'none' | 'checkout' | 'invalid' | 'blocked' | 'branch' | 'uncommitted' | 'merged'

/** POST /import/inspect {path}: read only. */
export interface ImportInspect {
  /** what was asked */
  input: string
  /** the repo's top level the app uses (a subfolder → its top level, a linked worktree → its main checkout) */
  path: string | null
  /** why path differs from what was asked ('' when it does not) */
  note: string
  checks: ImportCheck[]
  /** KiroCrew's checks for it (brain-dev's Lean hooks, the engine, this repo's committed kit), with their fixes */
  crew: { rows: CheckRow[]; fix: { agent: boolean; engine: boolean } }
  next: ImportNext
  /** its id when it is already in the list */
  listed: string | null
  kit: { state: KitState; values: KitView | null }
  /** the install form, prefilled (key prefix from the folder name, GitLab from origin, the default branch) */
  form: KitForm | null
  /** the install branch's default name */
  branch: string
  pending: PendingInstall | null
  /** next 'install': where it can go */
  install: InstallChoices | null
  /** who brain-kit will say the person is here (kit.json's GitLab, or the form's before an install) */
  me: RepoMe | null
  origin: { url: string; base: string; project: string } | null
  default_branch: string | null
  current_branch: string | null
  /** the path itself was refused ('' otherwise; the rows say the rest) */
  error: string
}

export type JobStepState = 'todo' | 'running' | 'ok' | 'warn' | 'fail'
export interface JobStep { id: string; label: string; state: JobStepState; detail: string }

/** A clone, an install or an upgrade, run in the background (POST /import/clone, /import/install, /import/upgrade
 * answer {job}); GET /import/jobs/{id} until it is not running. */
export interface ImportJob<R = CloneResult | InstallResult | UpgradeResult> {
  id: string
  kind: 'clone' | 'install' | 'upgrade'
  /** the clone's target folder, the install's (or upgrade's) repo */
  target: string
  state: 'running' | 'done' | 'failed'
  steps: JobStep[]
  started_at: number
  ended_at: number | null
  /** failed: why, in plain Chinese */
  error: string
  /** done: the result; failed: what was done before (or null) */
  result: R | null
}
export interface JobStart { job: ImportJob }

export interface CloneBody { url: string; parent: string; name: string }
export interface CloneResult { path: string | null; output: string; kind: string }

/** POST /import/install; mode left out: the recommended one. branch: the new branch's name (mode branch only). */
export interface InstallBody { path: string; branch: string; kit: KitForm; mode?: InstallMode }
export interface FileChange { change: 'added' | 'modified' | 'deleted' | 'other'; path: string }
/** The install's (or upgrade's) commit (git add -A, git commit with the person's identity; their hooks run). blocked:
 * a hook said no (text has what it said); POST /import/commit {path, purpose} tries again. */
export interface CommitResult { ok: boolean; sha: string | null; blocked: boolean; text: string; output: string }
/** Which records /import/commit and /import/publish act on: the install's (imports.json, the default) or the kit
 * upgrade's (upgrades.json: kept apart, so a listed repo is never taken for a pending install). */
export type ImportPurpose = 'install' | 'upgrade'
/** POST /import/commit: 再提交一次 after a hook said no */
export interface CommitBody { path: string; purpose?: ImportPurpose }
export interface InstallResult {
  mode: InstallMode
  path: string
  /** the new branch (branch), or the checkout's branch the commit is on (inplace) */
  branch: string
  /** what the branch was made from (origin/main) and its commit */
  base: string | null
  base_sha: string | null
  worktree: string | null
  /** `brain install`'s output */
  output: string
  kit: KitForm
  doctor: { ran: boolean; rows: CheckRow[]; output: string }
  files: FileChange[]
  commit: CommitResult
  /** origin's URL ('' without a remote): in place, 推送 is offered (POST /import/push), never done by itself */
  remote_url: string
  /** the default branch */
  target: string
}

/**
 * POST /import/upgrade {path, branch?}: the 'kit' fix of a CheckRow (an older hook adapter, Lean or the adapter not
 * committed, Lean counting runs/** as code). Like the install, for a repo that has brain-kit: a new branch (default
 * brain/upgrade-kit; -2, -3 … when that name is taken here or on origin) from the default branch (origin's, else the
 * local one) in its own worktree under work_root, `brain install` there (kit.json kept; the kit files refreshed, runs/**
 * added to lean.json), `brain doctor --offline`, a commit with the person's identity and hooks. The person's checkout is
 * never touched. Answers {job} (kind 'upgrade'); then /import/commit and /import/publish with purpose 'upgrade'.
 */
export interface UpgradeBody { path: string; branch?: string; mode?: InstallMode }
export interface UpgradeResult extends InstallResult {
  /** false: brain install changed nothing (text 「已经是最新的：没有要提交的改动」; what it wrote was taken back) */
  changed: boolean
  gitlab_project: string
  /** a GitLab token is on this machine and the project and origin are known: 推送并开 MR opens the MR itself */
  can_open_mr: boolean
  /** one plain sentence: where the commit is and what to do next, e.g. 「提交在 main 上了，现在就生效」 (in place) or
   * 「提交在本机分支 brain/upgrade-kit 上（这个仓库没有远端）：点「合并到 main」就生效」 */
  text: string
}

/** POST /import/publish: only with confirm true and what the person saw (the branch, the remote, the target). */
export interface PublishBody { path: string; branch: string; target: string; remote_url: string; confirm: true; purpose?: ImportPurpose }
export interface PublishResult {
  /** pushed (and the MR opened, or the way to open it) */
  ok: boolean
  pushed: boolean
  branch: string
  target: string
  remote_url: string
  mr: { iid: number; url: string; existing: boolean } | null
  text: string
  /** git push's output */
  output: string
  /** the exact commands, when the app could not do it all (push, glab mr create) */
  commands: string[]
  /** GitLab's new-MR page for the branch */
  new_mr_url: string | null
}

/** POST /import/merge: 「合并到 <default>」, a fast-forward in the checkout; confirm true for a repo with a remote. */
export interface MergeBody { path: string; confirm?: boolean; purpose?: ImportPurpose }
export interface MergeResult {
  ok: boolean
  text: string
  /** the default branch it went into */
  branch: string
  sha: string | null
  /** the install worktree and branch, taken away or kept (and why) */
  removed: string[]
  kept: string[]
  /** origin's URL: the remote does not have it yet ('' without a remote) */
  remote_url: string
  output: string
}
/** POST /import/pull: 「拉取」 (git pull --ff-only of the default branch) after the MR was merged. */
export interface PullResult { ok: boolean; text: string; output: string; removed: string[]; kept: string[] }
/** POST /import/push: the checkout's branch to origin, only with confirm true and what the person saw. */
export interface PushBody { path: string; branch: string; remote_url: string; confirm: true }
export interface PushResult { ok: boolean; pushed: boolean; branch: string; remote_url: string; text: string; output: string }

export interface AddResult { ok: boolean; id: string; text: string; status: Status }
/** 移出列表 and 按记录重建编号: what was done, in words, and the status after it */
export interface RemoveResult { ok: boolean; text: string; status: Status }

// ---------------------------------------------------------------- GET/POST /settings

/** POST /settings takes any part of it: a key left out keeps its value (the 设置 form sends no repos: 导入仓库 adds
 * them, 移出列表 removes them). */
export interface Settings extends TeamLimits {
  /** brain-kit checkout this app imports (read-only to the app) */
  brainkit: string
  /** project repos (brain-kit installed, or on its way) shown on this machine */
  repos: string[]
  /** where the app puts each requirement's worktree (under ~/workplace or ~/workspace) */
  work_root: string
  /** every repo with a remote is fetched this often by itself, in minutes, 0 to 120 (0: only on 「从远端取最新」) */
  fetch_every_min: number
  /** {repo path: false} for a repo whose run branches are not pushed at Lean's checkpoints (on when left out) */
  auto_push: Record<string, boolean>
  /** set by 设置's own switch only (the form never sends it); back to personal turns team mode off */
  role: InstallRole
  /** team mode, only on a team server (a personal install never has it on) */
  team: {
    enabled: boolean
    /** brain-kit's bridge.json, with "signer": {"type": "spool", "spool_dir", "allowed_signers"} and no signing_key */
    bridge_config: string
    runnerd_config: string
    /** local trial only: the signer runs as the same OS user (shown as a warning) */
    dev_same_user: boolean
  }
}

// ---------------------------------------------------------------- GET /overview?repo=<id>|all

export interface Overlap { keys: string[]; paths: string[] }

/** 交付总览's team view limits, kept in 设置 (backend/core/settings.py LIMITS). Working hours are Monday to Friday, 09:00 to
 * 18:00, this machine's time. */
export interface TeamLimits {
  /** requirements a person has open at once: 按人看 marks a person at it red, 开需求 reminds them (it never stops them) */
  wip_limit: number
  /** working hours a wait on a person may last before it is 超时 */
  wait_timeout_h: number
  /** working hours someone else's run on their own machine may go without new progress before it is 很久没动 */
  stale_after_h: number
  /** failed verify runs in a row after which the AI should stop and ask a person */
  verify_fail_limit: number
}

/** What 全队卡点 says is stuck (backend/core/roster.py STUCK_RULES, the worst kind first): overdue 超时 (a wait on a
 * person for wait_timeout_h), stale 很久没动 (someone else's run on their own machine, stale_after_h without new
 * progress), overlap 目录撞车 (two open requirements change one directory), verify_fails verify 失败 (verify_fail_limit
 * failed runs in a row). */
export type StuckKind = 'overdue' | 'stale' | 'overlap' | 'verify_fails'

/** A stuck requirement and the line said after its key, e.g. 「等 carol 批准设计，已等 6 个工作小时（超时）」. */
export interface Stuck {
  repo: string
  key: string
  kind: StuckKind
  /** one plain line; how long is said in working hours */
  text: string
  /** since when (epoch s): the wait's since, or the last progress pushed; null for an overlap and a verify streak */
  since: number | null
  /** an overlap's other requirement (the line names it); null for the other kinds */
  other_key: string | null
}

/** One person with requirements open (按人看): open counts the states claimed to rejected (not 待认领), a row on the
 * team runner under its owner. */
export interface Person {
  owner: string
  /** this machine's person */
  you: boolean
  open: number
  /** at it or over it, the person is marked red */
  limit: number
  /** the requirements open counts */
  keys: string[]
}

/** 交付总览's team view: who has how much open, and what is stuck. It ranks nobody: people come you first, then by how
 * much they have open. */
export interface Roster {
  people: Person[]
  /** the worst kind first, the worst first within a kind */
  stuck: Stuck[]
  /** every kind, zeros too, in the order the stuck lines come */
  counts: Record<StuckKind, number>
  limits: TeamLimits
}

export interface EvalMetric {
  id: string
  label: string
  /** [numerator, denominator] for a rate, a number, or null when not measured yet */
  value: [number, number] | number | null
  /** e.g. 「交接之后除了必经审批没人介入，而且交付正确」 */
  rule: string
}

export interface EvalView {
  /** brainkit.evaluate is importable and ran */
  available: boolean
  error: string
  ref: string | null
  metrics: EvalMetric[]
  /** 复看队列 (low-score queue): runs that need a human look */
  /** todo: what to do about it, in words (deliveries.QUEUE_TODO by brain-kit's rule). The backend always sends it */
  queue: { key: string; intent: string | null; reasons: string[]; todo?: string }[]
  /** runs brain-kit could not read: listed with the reason in words (brain-kit's own in raw), not counted in any metric */
  skipped: { key: string; why: string; raw?: string }[]
  /** brain-kit's notes on the numbers, verbatim (credits it could not attribute to a run, deliveries already known
   * wrong whose 14 days are not over …) */
  notes: string[]
  /** the same notes, each in words with what to do (做什么) and brain-kit's own words for 细节 */
  note_rows?: EvalNote[]
  /** true while brainkit.evaluate is still computing a repo's numbers: say 正在算, not 还没有 */
  computing?: boolean
  /** brain-kit's own words behind error (for 细节); '' when none. The backend always sends it */
  raw?: string
  /** brain-kit's families (0d52e97): one row per big requirement of the batch split into small ones; [] without */
  families: EvalFamily[]
}

/** One big requirement in brain-kit's eval (`families`): its small ones, n/m merged into its branch, its state, a
 * person's acts at the judgment points and outside them, questions and credits over the family. */
export interface EvalFamily {
  key: string
  repo: string
  children: string[]
  merged: number
  /** the small ones not closed, those not started included (brain-kit 1238919, as `brain intent list` counts) */
  total: number
  /** of them, those nobody started yet; [] before brain-kit 1238919 */
  not_started?: string[]
  /** the small ones whose claim was closed: not counted in total */
  closed?: string[]
  /** brain-kit's: in_progress | ready_to_release, or the big requirement's own (merged, closed, failed …) */
  state: string
  state_label: string
  at_points: number
  outside: number
  questions: number
  credits: number | null
}

/**
 * 决策漏斗: who settled each decision, from the Who cell of decisions.md (brain-kit's vocabulary: agent, brain <entry
 * id>, check, <pseudonym> <voucher id>, owner). Each decision counts once.
 */
export interface Funnel {
  source: 'evaluate' | 'app'
  total: number
  /** id, the backend's label, count, in the backend's order: person 人, check 确定性检查, brain Brain 条目（引用）,
   * agent agent 自己定（权限内）, unknown 看不出（Who 空着）, unclassified 没归类（Who 不是规定的写法） */
  rows: { id: string; label: string; count: number }[]
  /** the decisions counted as unclassified: their Who cell is outside the vocabulary. Listed where they are, never
   * guessed (the cell may hold a real name, so it is not repeated here) */
  unclassified: { key: string; id: string; src: string }[]
  /** questions asked of people / answered / delivered back to the run */
  questions: { asked: number; answered: number; delivered: number }
}

export interface Overview {
  at: number
  rows: Requirement[]
  overlaps: Overlap[]
  funnel: Funnel
  eval: EvalView
  /** the 交付总览's three numbers, their charts and the weekly deliveries (CtoNumbers, below) */
  cto: CtoNumbers
  /** the team view: 全队卡点 and 需求流动's 按人看 */
  roster: Roster
  /** state → count */
  counts: Partial<Record<ReqState, number>>
  /** a repo whose requirements could not be read: what happened and what to do, in words; raw: the original (细节) */
  /** a repo that could not be read: its name and path as /status says them (the banner names it and opens 导入仓库 at
   * that path even when /status cannot be read). The backend always sends name and path */
  errors: { repo: string; name?: string; path?: string; error: string; raw?: string }[]
}

// ---------------------------------------------------------------- GET /requirements/{repo}/{key}

/** optional: Lean never asks for the stage (Ideation's, ci-pipeline, Operations') and nothing recorded it: not on the way */
export type StageState = 'done' | 'skipped' | 'started' | 'todo' | 'optional'
/** an AI-DLC phase (backend/core/stages.py PHASES) */
export type PhaseKey = 'init' | 'ideation' | 'inception' | 'construction' | 'release' | 'operations'
/** One of Lean's 26 stages (backend/core/stages.py STAGES), in Lean's order. */
export interface Stage {
  id: string
  label: string
  /** its AI-DLC number: 2.3, 3.5, ✓ (发布), 4.1 */
  n: string
  phase: PhaseKey
  state: StageState
  /** per-unit stages: each unit's state */
  units: { unit: string; state: StageState }[]
  note: string | null
  at: number | null
}

/** One of Lean's phases, in order: used (its stages are listed one by one); skipped (every stage of it skipped by a
 * record: said once, 整段不需要; *note*: the reason, when one covers them all); optional (nothing recorded in a phase Lean
 * never asks for: said once, 可选，还没记). */
export interface LeanPhase {
  key: PhaseKey
  /** its AI-DLC number, as the board's band says it: 1 构思 · 2 启动 · 3 构建 · ✓ 发布 · 4 运维 */
  n: string
  /** 构思 */
  name: string
  state: 'used' | 'skipped' | 'optional'
  note: string | null
}

export type ApprovalState = 'approved' | 'pending' | 'rejected' | 'stale' | 'refused'
export interface Approval {
  kind: string // REQUIREMENTS | DESIGN | INCEPTION | PLAN | RELEASE
  state: ApprovalState
  sha8: string | null
  at: number | null
  /** pending only: the exact line the person types themselves in the session's chat */
  line: string | null
  path: string | null
}

/**
 * One row of decisions.md. Its Who cell, in brain-kit's vocabulary, gives *by* and *who*: agent or a kit bot user →
 * agent; `brain <entry id> …` → brain (who: the entry ids); check → check; `<P-xxxx> <V-xxxxxxxxxx>` (a pseudonym and
 * its voucher id) → person (who: the pseudonym); owner → person (who: 'owner'); bridge → the bridge's own record;
 * anything else → unclassified (who null: the cell may hold a real name); an empty cell → null.
 */
export interface Decision {
  id: string
  date: string | null
  stage: string | null
  text: string
  why: string | null
  /** the pseudonym, 'owner', or the Brain entry ids; null when the Who cell says nothing that may be shown */
  who: string | null
  by: 'person' | 'agent' | 'check' | 'brain' | 'bridge' | 'unclassified' | null
  /** Brain entry ids it cites (open them in the Brain view) */
  cites: string[]
  vouchers: string[]
  src: string
  /** the questions the AI asked straight in the session (Q1 …) whose answer this row records, from its text and why */
  asked?: string[]
}

export interface QuestionOption { id: string; text: string }
export interface Question {
  /** park: this machine's run's park.json (answered in its session); runner: the runner's, read off the run branch after
   * the hand-over (answered on the MR once the bridge posts it); bridge: brain-kit's ledger (team mode); mr: the MR's */
  source: 'park' | 'runner' | 'bridge' | 'mr'
  /** D-<n> once the bridge posted it */
  id: string | null
  kind: string
  kind_label: string
  question: string
  options: QuestionOption[]
  recommend: string | null
  lean_request: string | null
  refs: string[]
  /** park.json: waiting | answered; on the MR / in brain-kit's ledger: posting | asking (a bridge is posting it right
   * now) | open | held (0d52e97: a reply waits until GitLab can say whether it was edited; nobody answers again) |
   * answered | delivered | delivery_unknown | refused (the signer would not sign it) | stale (a new push) | void (not
   * bound to its push and park.json) | invalid | closed */
  state: string
  answer: string | null
  answerer: string | null
  voucher_id: string | null
  mr_url: string | null
  at: number | null
  /** the person may answer it right here (their own local run, a clarify question, not a Lean approval) */
  answerable_here: boolean
  /** how an answer here travels: into the local session, or as the person's own comment on the MR */
  answer_via: 'session' | 'mr' | null
}

export type TimelineKind =
  | 'stage' | 'approval' | 'human' | 'commit' | 'handover' | 'question' | 'decision' | 'blocked' | 'verify'
  | 'merge' | 'session' | 'app'
export interface TimelineItem {
  /** ISO time or null when the source has none */
  at: string | null
  kind: TimelineKind
  label: string
  detail: string | null
  who: string | null
  src: string | null
}

/** A requirement's credits: its sessions this machine reads, added up. *missing*: what the total leaves out (a subagent,
 * a turn that reported none, a session too long to read whole, sessions on another machine), a line each; *whole*: none. */
export interface Credits {
  total: number | null
  note: string
  whole: boolean
  missing: string[]
}

export interface SessionRow {
  slot: string
  title: string
  agent: string
  kind: 'local' | 'runner'
  credits: number | null
  turns: number
  busy: boolean
  awaiting: boolean
  last_activity: number | null
  opened_at: number | null
  project: string | null
  /** open it in Kiro Crew: navigate to this path */
  url: string
  error: string | null
  /** KiroCrew no longer has it (closed or deleted there) */
  missing: boolean
}

/**
 * The MR's 变更说明 (brain-kit's `brain mr-desc`): runs/<KEY>/MR.md as committed when the branch has it, else made from
 * the run's records at that commit. Always from a commit (`ref`), never from uncommitted files. The text is made inert
 * by brain-kit (no @mention, HTML, image, quick action, approval or answer line acts when it is pasted into GitLab).
 */
export interface MrDesc {
  /** brain-kit's mrdesc module is there */
  available: boolean
  /** Markdown; '' when there is none (error says why) */
  text: string
  /** its first line (the 一句话) */
  first: string | null
  /** file: runs/<KEY>/MR.md as committed; records: made from the run's records */
  source: 'file' | 'records' | null
  /** runs/<KEY>/MR.md */
  path: string | null
  /** the commit it was read at (12 hex) */
  ref: string | null
  error: string
}

export interface RequirementDetail {
  row: Requirement
  where_label: string
  run: {
    owner: string | null
    branch: string | null
    intent: string | null
    brain_commit: string | null
    origin: string | null
    created_at: string | null
    materials: string[]
    handoff: { requested_at: string | null; by: string | null } | null
  } | null
  claim: { owner: string | null; title: string; created_at: string | null; source: string; closed: string | null } | null
  lean: {
    stages: Stage[]
    phases: LeanPhase[]
    approvals: Approval[]
    /** aidlc-state.md Session notes */
    status: string | null
    next: string[]
    open_questions: string[]
  }
  decisions: Decision[]
  questions: Question[]
  /** other_runs (brain-kit 0d52e97, rule H0): this requirement's runs on another branch, not read into this trace, in words */
  timeline: { source: 'trace' | 'app'; items: TimelineItem[]; missing: string[]; error: string; other_runs: string[] }
  sessions: SessionRow[]
  /** the live brain-dev session on this machine, as the backend decides it (service._live): the newest one KiroCrew
   * still has whose first message went in, never a review session. 打开会话 opens it, 重开一个会话 replaces it */
  live_slot: string | null
  credits: Credits
  brain: { loaded: number | null; cited: string[]; stale: string[]; unknown: string[]; proposals: number | null }
  mr_desc: MrDesc
}

// ---------------------------------------------------------------- POST /requirements/{repo}/{key}/session, /stop, /answer

export interface OpenSessionBody { trust?: boolean; kickoff?: boolean
  /** 开审查会话: a NEW brain-dev session in the worktree to review the code (the session that wrote it may not review it,
   * Lean's next step says so), never the live one; its first message the review line Lean wrote */
  review?: boolean
  /** 重开一个会话: a NEW session even when one is live (the old one stopped when busy, kept); *replace*: the slot the page
   * shows as live (when another is live by now, that one is the answer and none is opened) */
  fresh?: boolean
  replace?: string }
/** reopened: 重开一个会话 opened this one now (false: it was opened by an earlier click); said: what happened when the
 * requirement's session was handed back instead (its first message did not go in, or is still being sent), '' otherwise */
export interface OpenSessionResult { ok: boolean; slot: string; url: string; reopened?: boolean; said?: string }
/** 停止: the slots of this requirement's own sessions on this machine whose turn was stopped (409 nothing_to_stop: none
 * was working) */
export interface StopResult { ok: boolean; stopped: string[] }
/**
 * choice: an option id (A–H), or 'approve' / 'reject' for a question without options. id: the question's D-<n> when it
 * has one (an MR question); without it the open clarify question is meant.
 */
export interface AnswerBody { choice: string; note?: string; id?: string }
export interface AnswerResult { ok: boolean; via: 'session' | 'mr'; text: string }

// ---------------------------------------------------------------- 开需求: /intake/next, /intake/check, /intake/start, /pick

export type Classification = 'public' | 'internal' | 'confidential' | 'secret' | 'unsure'
export const CLASS_LABEL: Record<Classification, string> = {
  public: '公开',
  internal: '内部',
  confidential: '机密',
  secret: '绝密',
  unsure: '不确定（按机密处理）',
}

export interface IntakeNext {
  repo: string; key: string | null; tracker: string; prefix: string; error: string
  /** kit.json intake.allow_confidential: gate 2 takes confidential material here (brain-kit's default: no) */
  allow_confidential?: boolean
  /** brain-kit is on the default branch in this checkout only, not on origin, where `brain start` branches from (an
   * in-place install not pushed yet): 推送 first. null: nothing waits */
  push?: { branch: string; remote_url: string } | null
  /** 「大需求：先拆成小需求」 can be chosen here (tracker none, a brain-kit with brain split), and why not */
  big: { ok: boolean; why: string }
}

/** POST /pick {kind}: this machine's own dialog (选文件… / 选文件夹…) answers the absolute paths chosen, [] for a cancel */
export type PickKind = 'file' | 'folder'
export interface PickResult { paths: string[] }

export interface IntakeBody {
  repo: string
  /** tracker none: empty = the next free key; tracker jira: required */
  key?: string
  slug: string
  title: string
  /** paths on this machine: files or folders (a Feishu / Lark export folder works) */
  docs: string[]
  /** 「用一段话说需求」, trimmed ('' when none): brain-kit takes it in as one more document (需求描述.md), so the gates
   * are the same as for documents */
  text: string
  /** gate 1: I am the author or have the author's consent */
  consent: boolean
  /** gate 2 */
  classification: Classification | ''
  /** gate 3: amounts / company names / contract numbers: keep as they are or mask */
  business: 'keep' | 'mask' | ''
  /** leave out files matching these globs */
  exclude: string[]
}

export interface IntakeCheck {
  ok: boolean
  key: string | null
  package: string | null
  /** effective classification after the file markers */
  classification: string | null
  segments: number
  sources: number
  /** brain-kit's findings, verbatim, one line each */
  findings: string[]
  /** brain-kit's 请你确认 parts of the findings, one per file (「file：请你确认 N 处（…）：「quote」…」): what may be a name or
   * a password but may as well be a word. Not masked and not stopping anything: if it is one, change the document
   * and check again */
  review: string[]
  /** gates that would stop the start, said as what to do on this page (「第 2 关：这个仓库不收机密材料…」) */
  blocking: string[]
  /** the same, as brain-kit said them (shown folded) */
  blocking_raw?: string[]
  /** the findings per file, said plainly: what was found and what happened to it, 请你确认, why a file was not taken */
  files?: { id: string; name: string; items: string[]; review: string; refused: string }[]
  /** files not taken: why, in words (raw: brain-kit's own words) */
  refused: { id: string; name: string; reason: string; raw?: string }[]
  /** a hard stop before any finding (no consent, nothing to take in …), verbatim */
  error: string
}

export interface StartBody extends IntakeBody {
  /** modules expected to change (run.json expected_paths; overlap warnings): a list, one path per item, or one string
   * with the paths separated by commas, spaces or new lines (the API accepts both; this page sends a list) */
  paths: string[] | string
  open_session: boolean
  trust: boolean
  /** 「大需求：先拆成小需求」: Lean's requirements and design as usual, the design with the unit split (unit-of-work.md,
   * brain-kit's skill brain-split); the DESIGN approval approves the split; then 拆成小需求 */
  big?: boolean
}

export interface StartResult {
  ok: boolean
  key: string | null
  branch: string | null
  intent: string | null
  worktree: string | null
  /** `brain start`'s output, verbatim */
  output: string
  slot: string | null
  url: string | null
  session_error: string
  /** brain-kit did not know who the person is (fix 'me'), and who it says they are there now (the input) */
  problem: CheckRow | null
  me: RepoMe | null
}

// ---------------------------------------------------------------- GET /crew, POST /crew/fix

export interface CrewChecks { rows: CheckRow[]; fix: { agent: boolean; engine: boolean }; checked_at: number | null }
export interface CrewFixBody { what: 'agent' | 'engine' }
export interface CrewFixResult { ok: boolean; text: string; rows: CheckRow[] }

// ---------------------------------------------------------------- GET /brain?repo=<id>

export interface BrainEntry {
  id: string
  type: string
  side: string
  rel: string
  repeats: number
  src: string[]
  text: string
  /** how often runs cite it (brainkit.usage) */
  cites: number
  runs: number
  loaded_by: number
  /** loaded by enough runs and never cited: a withdraw candidate */
  candidate: boolean
}

export interface BrainSection { title: string; entries: BrainEntry[] }
export interface BrainFileView { key: string; letter: string; side: string; types: string[]; count: number; sections: BrainSection[] }

export interface BrainView {
  repo: string
  ref: string
  files: BrainFileView[]
  /** brain lint (static rules) on the default branch: errors as FAIL rows, warnings as WARN rows (text 「brain/TECH.md 第
   * 12 行：条目号 T-PAY-1-1 重复了」, todo, raw: brain lint's own line) */
  lint: { errors: CheckRow[]; warnings: CheckRow[] }
  usage: {
    useful: [number, number]
    repeat_rate: [number, number]
    archived: number
    purged: number
    runs: { intent: string; key: string; loaded: number | null; cited: string[]; stale: string[]; unknown: string[] }[]
    never_cited: string[]
    repeats_any: [string, number][]
    /** the numbers above count the runs merged into the default branch only; these runs are not merged yet, read where
     * 需求现状 reads them (their worktree, else their branch), with its numbers. The backend always sends it */
    unmerged?: { key: string; loaded: number | null; cited: string[]; where: 'worktree' | 'branch' }[]
  }
  /** requirements whose run can be reflected (they have a decisions.md) */
  reflect: { key: string; title: string; state: ReqState }[]
  error: string
  /** brain-kit's own words when it could not read the Brain or its use (`error` says it in Chinese): for 细节 */
  raw?: string
  /** null: the Brain on the default branch as it is now. Else the branch has moved since this Brain was read (epoch s):
   * the new one is read behind this answer, and the page asks again until it is null */
  stale: number | null
}

export interface ReflectOp { kind: string; target: string | null; file: string | null; type: string | null; section: string | null; src: string[]; text: string }
export interface ReflectPreview {
  repo: string
  key: string
  source: string
  ops: ReflectOp[]
  gaps: { title: string; kind: string; what: string }[]
  notes: string[]
  warnings: string[]
  /** what blocks --apply, each a FAIL row in the app's words (a brain lint line as the Brain 检查 card says it, with
   * its 做什么); brain-kit's lines are in raw */
  problems: CheckRow[]
  /** at least one op that is more than sources added: --apply would open a branch */
  substantive: boolean
  error: string
  /** what brain-kit said, verbatim (notes, warnings, problems and error are its words in Chinese): for 细节. The backend
   * always sends it */
  raw?: string[]
}
export interface ReflectApplyBody { repo: string; key: string; push: boolean }
export interface ReflectApplyResult {
  ok: boolean
  output: string
  branch: string | null
  /** the worktree brain-kit wrote the new brain/<KEY>-reflect-<n> branch in (the person's own checkout is not touched) */
  worktree: string | null
  /** with push: the MR brain-kit opened (null: none, e.g. no GitLab token); pushed: the branch is on the remote. The
   * backend always sends them */
  mr_url?: string | null
  pushed?: boolean
}

// ---------------------------------------------------------------- GET /todo

/** handoff_unlabelled: pushed, but its MR carries no hand-over label (nothing was handed over); handoff_unpushed: the
 * hand-over commit is on this machine only, its push failed (再交接一次, on 需求现状) */
/** reply: a local session idle, your turn to talk (opens it); start: claimed by you, no run yet (开工) */
/** child_start: a small requirement you claimed whose dependencies have all merged (开工 now); parent_release: a big
 * requirement whose small ones have all merged (review and release it); closed_leftover: your requirement closed with
 * `brain intent close` whose run branch or MR is still there (delete it, close it) */
export type TodoKind = 'lean_approval' | 'question' | 'clarify' | 'acceptance' | 'handoff_rejected' | 'handoff_unlabelled'
  | 'handoff_unpushed' | 'crew'
  | 'reply' | 'start'
  /** write_back: merged, its Brain proposals not written back yet (回写 Brain) */
  | 'write_back'
  | 'child_start' | 'parent_release' | 'closed_leftover'
  /** push_failed: the person's progress at a Lean checkpoint that origin refused (todo and raw say what and why) */
  | 'push_failed'
export interface TodoItem {
  kind: TodoKind
  repo: string
  key: string
  title: string
  /** what to do, one sentence */
  text: string
  /** reply: the row's waits.review, Lean's review in a NEW session (开审查会话), not a turn in this one. The backend
   * always sends it */
  review?: boolean
  /** lean_approval: the exact line the person types themselves in that session (the app never sends it) */
  line: string | null
  approval: string | null
  /** question / acceptance on the team runner: answered on GitLab (link) */
  mr_url: string | null
  question: Question | null
  /** the brain-dev session to open, for lean_approval / clarify */
  session: { slot: string; title: string; url: string } | null
  since: number | null
  /** lean_approval: the document to read before approving (the approval's path), shown as 要读的 */
  path?: string | null
  /** handoff_rejected / push_failed: what to do, in words ('' otherwise). The backend always sends it */
  todo?: string
  /** handoff_rejected / handoff_unlabelled: the bridge's words as it wrote them; push_failed: git's; and how to do it
   * by hand (做法), for 细节 ('' otherwise). The backend always sends it */
  raw?: string
}
export interface Todo { items: TodoItem[]; team: boolean }

// ---------------------------------------------------------------- team mode: GET /team, POST /team/loop

/** A run's state in brain-kit's bridge ledger (not a ReqState). */
export type RunState =
  | 'signing' // run id reserved while the separate signer signs the contract
  | 'spooled' // waiting for its workspace
  | 'ready' // workspace made, not sent to a session yet
  | 'dispatched' // sent to its brain-run session
  | 'send_unknown' // sending it to the session failed in a way that does not say whether it arrived (the bridge looks
  //                  at the session each round; through this app's KiroCrew adapter it sees a send that landed, never
  //                  one that did not: a person looks)
  | 'failed' // it could not start: the workspace could not be made, or (0d52e97) KiroCrew never took the hand-over
  | 'merged'
  | 'closed'

export interface TeamRun {
  run_id: string
  req: string
  project: string
  iid: number
  /** brain-kit's run state; a state this page does not know is shown as it is */
  state: RunState
  slot: string | null
  mr_url: string | null
  updated: string | null
  /** what brain-kit's bridge holds about it (0d52e97): the acceptance question held until the pipeline is green,
   * runnerd silent, sends that keep failing, queued; messages into its session not known to have landed */
  notes: TeamNote[]
}
/** One thing the bridge's ledger says about a run (its notices and outbox, brain-kit 0d52e97), in plain Chinese. */
export interface TeamNote { kind: string; text: string; at: number | null }
/** A bridge.json or runnerd.json setting brain-kit reads (0d52e97's new ones among them): its value, or its default
 * (default true), and one plain line. Never a token or a key. */
export interface TeamConfigRow { file: 'bridge.json' | 'runnerd.json'; key: string; value: string; default: boolean; text: string }
/**
 * What brain-kit's bridge said about one MR with the hand-over label in the last round (Bridge.process): accepted,
 * rejected, seen (that head and label event were checked before), unlabelled (the newest label event takes the label
 * off, as `brain handoff` does while it labels again), waiting (this very hand-over is being signed elsewhere), error
 * (it could not be checked this round: error says why). Another value is shown as it is.
 */
export type HandoverResult = 'accepted' | 'rejected' | 'seen' | 'unlabelled' | 'waiting' | 'skipped' | 'error'
export interface TeamHandover {
  project: string
  iid: number
  sha: string
  result: HandoverResult
  /** why it could not be checked this round, in short Chinese ('' unless result is error) */
  error: string
  /** error as brain-kit said it, for debugging ('' when there is nothing more) */
  raw: string
  /** epoch seconds */
  at: number
  mr_url: string | null
}

export interface TeamView {
  status: TeamStatus
  /** state → count: runs by RunState, decisions by question state (open, answered, delivered, refused, void …),
   * hand-overs by checking / accepted / rejected, and (0d52e97) the messages into the runs' sessions by outbox state
   * (sending, interrupted, sent, unknown, dropped) */
  ledger: { runs: Record<string, number>; decisions: Record<string, number>; handoffs: Record<string, number>; outbox: Record<string, number> }
  runs: TeamRun[]
  /** the labelled MRs of the last round (none before the first round) */
  handovers: TeamHandover[]
  signer: {
    /** signature requests waiting for the signer */
    pending: { req_id: string; kind: string; run_id: string; since: string | null }[]
    /** requests it refused, with its reasons in short Chinese (problems.reason); raw: the same reasons as the signer
     * wrote them (brain-kit's words for a hand-over check), one per reason, for debugging */
    refused: { req_id: string; kind: string; reasons: string[]; raw: string[] }[]
    /** vouchers asked for in an earlier round that the signer has not answered yet (brain-kit's pending_vouchers): the
     * next round asks for the very same voucher, so a late signature is still used. ref: what it is for, as brain-kit
     * names it: 「answer D-2」, or 「handoff shop/shop-pay!41 <head sha>#<label event id>」 (only those within brain-kit's
     * one-hour reuse window) */
    waiting: { ref: string; kind: string; run_id: string; since: string | null }[]
  }
  /** bridge.json and runnerd.json, the keys brain-kit reads ([] before team mode started) */
  config: TeamConfigRow[]
  /** the runner's Lean hooks as brain-runnerd checks them before each workspace (cli.runner_hook_problems, 0d52e97:
   * the hook adapter version 3, brain-run's three hooks); [] with an older brain-kit or without runnerd.json */
  hooks: CheckRow[]
}
export interface TeamLoopResult { ok: boolean; summary: Record<string, number>; error: string }

// ---------------------------------------------------------------- MR 巡检: GET /team/patrol

/** What one open MR is to MR 巡检 (backend/core/team/patrol.py) as its last round left it: working (a brain-patrol
 * session is on it), queued (waiting for one of the sessions, at most cap at once), waiting (its session is still on an
 * earlier turn, or the MR just moved), done (these problems were given to a session that ended its turn: waiting for
 * the MR to change), blocked (two attempts failed, or three dispatches in a row with no person pushing or commenting in
 * between: it said so on the MR, note says which), ok (nothing to fix), skipped (skip says why), error (this round
 * could not look at it or dispatch it: note says why). Another value is shown as it is. */
export type PatrolState = 'working' | 'queued' | 'waiting' | 'done' | 'blocked' | 'ok' | 'skipped' | 'error'
export type PatrolSkip = '' | 'handoff' | 'opt_out' | 'fork' | 'claimed' | 'bots_only'
export interface PatrolMr {
  project: string
  iid: number
  title: string
  url: string | null
  author: string
  state: PatrolState
  /** what there is to fix, in words: a failed pipeline, an open review thread, a person's plain comment, a conflict */
  problems: { kind: 'ci_failed' | 'review' | 'comment' | 'conflict'; text: string }[]
  /** why it is skipped, in words ('' unless state is skipped) */
  skip: string
  skip_code: PatrolSkip
  /** one more line in words (why it waits, why it was not dispatched; '' when nothing) */
  note: string
  /** sessions given its current problems */
  attempts: number
  slot: string | null
  updated_at: string | null
}
export interface TeamPatrol {
  /** some project's kit.json switches work source mr-patrol on */
  on: boolean
  cap: number
  working: number
  /** epoch seconds of the last round; null before the first */
  last_at: number | null
  projects: { project: string; on: boolean; error: string }[]
  /** every open MR of the projects patrol is on, as the last round saw it */
  items: PatrolMr[]
}

// ---------------------------------------------------------------- 测试环境报错: GET /team/staging

/** Where an error of the test environment is (backend/core/team/staging.py): new (seen, its Issue not open yet, or
 * seen again on a newer deploy and to be opened again), issued (its Issue open, waiting for a fix session: note says
 * why), fixing (a brain-patrol session is on it), fix_mr (its fix's MR is open, waiting for people), stopped (two fix
 * attempts failed, or a person closed the fix's MR: the Issue says so), closed (its Issue closed). Another value is
 * shown as it is. */
export type StagingState = 'new' | 'issued' | 'fixing' | 'fix_mr' | 'stopped' | 'closed'
export interface StagingLink {
  iid: number
  url: string | null
  title: string
}
export interface StagingError {
  project: string
  /** what keys the error: a traceback's exception type and innermost project frame, else its message without its
   * numbers and ids, or the request a 5xx answered */
  signature: string
  /** times the runner saw it in the log */
  count: number
  state: StagingState
  issue: StagingLink | null
  /** the MR that merged the commit deployed when its Issue was opened; null: GitLab knows none */
  origin: StagingLink | null
  fix_mr: StagingLink | null
  /** epoch seconds */
  first_seen: number
  last_seen: number
  /** the commit deployed when it was last seen */
  deployed_sha: string
  /** failed fix attempts since its Issue was opened */
  attempts: number
  slot: string | null
  /** why it waits, how its last attempt went, or what went wrong this round, in words ('' when nothing) */
  note: string
}
export interface TeamStaging {
  /** some project's kit.json switches work source staging-errors on */
  on: boolean
  cap: number
  working: number
  /** epoch seconds of the last round; null before the first */
  last_at: number | null
  projects: { project: string; on: boolean; error: string; deployed_sha: string }[]
  items: StagingError[]
}

// ---------------------------------------------------------------- 团队运行机 as GitLab shows it: GET /team/status

/** An open MR the team runner works on: running, its hand-over label says so (kit.json running_label); patrol, MR 巡检
 * replied on it or summed it up within the last recent_h hours. */
export interface RunnerMr {
  iid: number
  title: string
  url: string | null
  why: 'running' | 'patrol'
  /** the newest note the bridge's account wrote on it (epoch s); null: none */
  at: number | null
}
/** A question the bridge asked on an MR that names the person among who may answer, with no answer after it yet. */
export interface RunnerQuestion {
  /** D-n */
  id: string
  iid: number
  /** its MR's title */
  title: string
  /** the question's note on the MR */
  url: string | null
  kind: string
  kind_label: string
  question: string
  options: { id: string; text: string }[]
  /** when it was asked (epoch s) */
  at: number | null
}
export interface RunnerSeen {
  /** when GitLab was read (epoch s) */
  at: number
  /** the runner's newest act on the MRs read: a note of its account, a label it changed (null: none in recent_h) */
  last_at: number | null
  at_work: RunnerMr[]
  /** oldest first */
  waiting: RunnerQuestion[]
}
/** A repo's team runner, read with the person's own GitLab token: the last read that worked (seen), and why the last
 * one did not (problem, in words; raw: GitLab's own, for 细节). */
export interface TeamRunner {
  /** the bridge's GitLab account (kit.json bridge_user) */
  bridge_user: string
  project: string
  /** whom the questions are read for: the person's name in this repo; null: the app cannot tell */
  me: string | null
  seen: RunnerSeen | null
  problem: { text: string; todo: string; raw: string } | null
}
export interface RunnerRepo {
  repo: string
  name: string
  /** null: its kit.json names no bridge_user, so it has no team runner */
  runner: TeamRunner | null
}
/** GET /team/status: every listed repo, in the app's order. */
export interface RunnerStatus {
  /** what 「最近」 means for MR 巡检's notes, in hours */
  recent_h: number
  repos: RunnerRepo[]
}

// ---------------------------------------------------------------- 一键建运行机: GET/POST /team/setup

/** A step's state: done, waiting (for a person: the kit.json MR to merge), failed (the run stopped there), skipped;
 * running (the one in progress), todo (not reached by the last run). */
export type SetupStepStatus = 'done' | 'waiting' | 'failed' | 'skipped' | 'running' | 'todo'
export type SetupRole = 'product' | 'builder' | 'security' | 'data'
export interface SetupStep {
  id: string
  title: string
  status: SetupStepStatus
  /** short plain Chinese ('' when not reached) */
  message: string
  /** a page on GitLab: the project, the MR to merge */
  link: string | null
  /** what GitLab or a command said, for 细节 */
  detail: string
}
/** The same-user team runner the app builds itself (backend/core/team/setup.py), as the last run left it. */
export interface TeamSetup {
  /** a run holds the setup now (poll until it is false) */
  running: boolean
  started_at: number | null
  ended_at: number | null
  /** the step a run cut off part-way had not finished (the app or the command stopped while it ran); null otherwise */
  cut_off: string | null
  /** that run's input; never the token. patrol: 巡检所有开着的 MR */
  input: { gitlab_base: string; project: string; roles: Record<SetupRole, string[]>; push: 'auto' | 'ssh' | 'token'; patrol: boolean }
  /** every step of the table, in order */
  steps: SetupStep[]
  /** where its configs, tokens and keys are */
  root: string
}
/** POST /team/setup (answers TeamSetup, running). admin_token: a one-time personal token with api, used for this run
 * only; roles: GitLab usernames, a role left out is the token's owner; patrol: 巡检所有开着的 MR (left out: on). */
export interface TeamSetupBody {
  gitlab_base: string
  project: string
  admin_token: string
  roles: Partial<Record<SetupRole, string[]>>
  push?: 'auto' | 'ssh' | 'token'
  patrol?: boolean
}

// ---------------------------------------------------------------- POST /refresh {repo}

export interface RefreshResult {
  ok: boolean
  /** what happened and what to do, plain Chinese */
  text: string
  /** git's own words when the fetch failed (shown folded under 细节); '' otherwise */
  raw?: string
}

// ---------------------------------------------------------------- GET /board/{repo}/{key}: 运行过程

/**
 * The state of a node, a run or a hand-over step on a requirement's board, in the sample team board's words and looks
 * (the same CSS): waiting (not reached yet), queued (handed to it, not picked up yet), running (at work now), stalled
 * (stuck: needs a look), awaiting_approval (waits for a person), done (did its part), merged (finished: green), fixing
 * (at it again after a no), failed (refused or failed), skipped (not on this requirement's way).
 */
export type ActorStatus =
  | 'waiting' | 'queued' | 'running' | 'stalled' | 'awaiting_approval' | 'done' | 'merged' | 'fixing' | 'failed' | 'skipped'

export interface ActorSpan {
  /** epoch seconds; b null: still going */
  a: number
  b: number | null
  /** work (at it), wait (it waits for a person), fix (at it again after a no) */
  kind: 'work' | 'wait' | 'fix'
}
export interface ActorMark { at: number; kind: string; title: string }

/** A unit of work on the board: a Lean stage id, `stage/unit` for a per-unit stage (code-generation/api), or one of
 * the two steps brain records by other means: `review` (Lean's release review, review.md) and `reflect` (brain
 * reflect's branch). Made by the fold; the page never builds one. */
export type NodeId = string & { readonly __brand: 'NodeId' }
/** Who did a round: `local` (this requirement's session on the owner's machine), `runner-<n>` (the team runner's n-th
 * run), `child-<KEY>` (a small requirement of this split), `parent-<KEY>` (the big one this was split from). */
export type RunKey = string & { readonly __brand: 'RunKey' }
/** Lean's approval kinds the board draws (CR-<team>-<n> is left out, as today). */
export type GateKind = 'IDEATION' | 'REQUIREMENTS' | 'DESIGN' | 'INCEPTION' | 'PLAN' | 'RELEASE'

/** One AI-DLC phase band, left to right: 0 初始化 · 1 构思 · 2 启动 · 3 构建 · ✓ 发布 · 4 运维 · 回写 (stages.PHASES). */
export interface BoardPhase {
  key: PhaseKey
  /** 0 1 2 3 ✓ 4 */
  n: string
  title: string
  /** its subtitle: its stage numbers and names */
  sub: string
  /** used: it has a node on this way; unused: nothing of it is on this way (Lean skipped it all, or it is not this
   * requirement's: a small requirement's 发布 and 回写): a folded band saying `note`; unrecorded: nothing recorded and
   * the run is past it (a run older than Lean 0.3): folded, Lean 没记这一段 */
  state: 'used' | 'unused' | 'unrecorded'
  /** a used band's foot line (跳过：逆向工程、用户故事 · 每个单元都跳过了功能、非功能、基础设施设计), a folded band's words
   * (这次不需要 · 跟大需求一起发布 · Lean 没记这一段); null: none */
  note: string | null
}

/** What this machine can read of a run's session: here, elsewhere (another machine has it), none (a program step). */
export type Readable = 'here' | 'elsewhere' | 'none'

/** A run's state now, for the orchestrator roster and the command path. */
export interface RunNow {
  status: ActorStatus
  /** 等你批 · 在做：应用设计 · 最后一次推送 2 分钟前 · 做完 · 11:05 交出去 */
  doing: string
  since: number | null
  /** whom a wait is on (the page says 等你 only for you) */
  who: WaitWho | null
  /** the node it is on now (the command path goes there); null: none */
  node: NodeId | null
  /** the step its person takes while it waits on them (approve, reply, tool, answer, accept, redo_handoff,
   * label_handoff), named under each waiting run; null when it waits on no one */
  do: WaitDo | null
}

/** Who does the work: the roster under the orchestrator box, and each round's executor. */
export interface Run {
  key: RunKey
  kind: 'local' | 'runner' | 'child' | 'parent'
  /** 本机会话 · 运行机 第 1 次 · 小需求 PAY-12 · 大需求 PAY-11 */
  label: string
  readable: Readable
  /** runner: its number (运行机 第 n 次); null otherwise */
  n: number | null
  /** runner: RUN-<KEY>-<n> when the bridge ledger, a voucher or the row's run ids say it; null otherwise */
  run_id: string | null
  /** child / parent: that requirement's key; null otherwise */
  req: string | null
  /** child: its unit; null otherwise */
  unit: string | null
  now: RunNow
  /** summed over its sessions on this machine; null when none here (never per stage) */
  credits: number | null
  /** its KiroCrew sessions on this machine, to open; [] when none here. *agent*: the one it boots ('' when not known) */
  sessions: { slot: string; url: string; busy: boolean; title: string; agent: string }[]
  /** what its session here did last (readable here only; null otherwise or before it did anything): its last call
   * (读文件 · 写文件 · 跑命令 · 搜索 · 查知识库 · 其他, and the path or command), and the last line the agent wrote, scrubbed,
   * its emails pseudonyms; never a person's typed words */
  ticker: { at: number | null; tool: string | null; detail: string | null; text: string | null } | null
}

/** One hand-over: 交接 → 桥 → 签名 → 运行机建工作区. A row in the roster and a pill on the rail at the hand-over commit. */
export interface Relay {
  /** the runner run it handed to; null: refused, or not taken yet */
  to: RunKey | null
  /** the hand-over request */
  at: number
  /** the hand-over commit, 12 hex; null when not known */
  sha: string | null
  steps: RelayStep[]
}
export interface RelayStep {
  key: 'handover' | 'bridge' | 'signer' | 'workspace'
  /** 交接 · 桥核对 · 签名 · 运行机建工作区 */
  title: string
  status: ActorStatus
  at: number | null
  /** 桥接手了（MR !41） · 推上去了，但 MR 上没有交接标签 */
  text: string
  /** the one thing to do when the step is stuck, plain Chinese; null otherwise */
  todo: string | null
}

export interface CheckResult {
  /** pytest, ruff, npm test (board._cmd_name) */
  name: string
  ok: boolean
  secs: number | null
}

/** How a round ended (Python: stages.RoundEnd). Only the fields of its kind are set; the others are null / []. */
export interface RoundOutcome {
  kind: 'open' | 'done' | 'approved' | 'sent_back' | 'refused' | 'superseded' | 'passed' | 'failed' | 'skipped'
  /** approved / sent_back / refused / superseded: when */
  at: number | null
  /** approved / sent_back / refused: which approval */
  gate: GateKind | null
  /** approved / sent_back: 你 · a user name · 产品在 MR 上 (never an email, never a pseudonym's real name) */
  by: string | null
  /** approved / sent_back on the MR: D-n; null when typed in the session */
  decision: string | null
  /** refused: why, in words (lean_view's refused words); skipped: Lean's reason (agent text, scrubbed) */
  text: string | null
  /** superseded: the commit after the request (12 hex) and its subject */
  sha: string | null
  subject: string | null
  /** passed / failed: the checks of that VERIFY */
  checks: CheckResult[]
  /** superseded: the MR pipeline that failed before that commit (person mode, read from GitLab); null when none */
  pipeline: PipelineRef | null
}

/** A pipeline of the MR that failed, as this machine read it from GitLab with the person's token (Python:
 * stages.Pipeline): its number, its page, when it finished. */
export interface PipelineRef {
  id: number
  url: string | null
  at: number | null
}

/** Why the round happened (Python: stages.Why). */
export interface RoundWhy {
  kind: 'first' | 'sent_back' | 'verify_failed' | 'ci_failed' | 'code_changed' | 'reopened'
  /** sent_back / verify_failed: the round it follows */
  round: number | null
  /** ci_failed: the pipeline that failed (read from GitLab on a person's machine) */
  pipeline: PipelineRef | null
  /** code_changed: the commit (12 hex) and its subject */
  sha: string | null
  subject: string | null
  at: number | null
}

export interface Round {
  /** 第 n 轮 of this node, from 1, over all runs (a run filter never renumbers) */
  n: number
  run: RunKey
  started: number
  /** null: still open */
  ended: number | null
  outcome: RoundOutcome
  why: RoundWhy
  /** the agent's own note of this round (Lean STAGE_COMPLETED note), scrubbed; null when none */
  note: string | null
  /** what the round declared: Lean's outputs, an approval's files, VERIFY's summary, `lean release`'s release.md:
   * path relative to the repo, sha256's first 8 */
  outputs: { path: string; sha8: string }[]
  /** this run's commits inside the round, and those after it ended (before the node's next round) that hold a version
   * Lean recorded for it: 12 hex, oldest first, at most 20 */
  commits: string[]
}

/** A gate on the node whose document it approves. Pending gates never approve from the page: the person types the line. */
export interface Gate {
  kind: GateKind
  state: 'pending' | 'queued' | 'approved' | 'sent_back' | 'refused' | 'stale'
  /** the round of the node it belongs to */
  round: number
  /** pending / queued: since when; decided: when */
  at: number | null
  /** pending / queued: whom it waits on; null when decided */
  who: WaitWho | null
  /** decided: who decided (你 · a user name · 产品在 MR 上); null otherwise */
  by: string | null
  /** pending on a local run: the exact line to type in the session; null after the hand-over */
  line: string | null
  /** pending on the runner: the MR question that asks it (D-3) and the MR link; null on a local run */
  mr: { decision: string | null; url: string | null } | null
  /** queued: why it is held (等流水线过了才问); stale: 文件改过了：要重新请求批准; null otherwise */
  text: string | null
  /** the documents it approves, repo-relative (INCEPTION: two) */
  docs: string[]
}

/** A node's lane time: a span with the run that was at it (需求现状's 时间花在哪 tells this machine's AI from the runner by it). */
export interface NodeSpan extends ActorSpan {
  run: RunKey | null
}

/** A unit of work on the board. A skipped stage keeps its node (status skipped: the band's foot note and the replay
 * name it); the graph draws no box for it. */
export interface StageNode {
  id: NodeId
  /** Lean's stage id, or review / reflect */
  stage: string
  /** 2.3 · 3.5 · 3.6 · ✓ · 4 (stages.STAGES n) */
  n: string
  unit: string | null
  phase: PhaseKey
  /** 需求分析 (stages.STAGES label) */
  title: string
  /** what it hands in: requirements.md · api 的代码生成计划 · lean verify · review.md · release.md */
  sub: string
  /** the RoleIcon it is drawn with (stages.STAGES icon) */
  icon: string
  /** the one writer is stages.node_status. A finished stage is done (做完这步) until a later stage's round begins,
   * then merged (完成), as it is once the requirement merged */
  status: ActorStatus
  /** one short line: what it does, did, or waits for (等批了设计 · 等你批 · 需求和设计 · 第 3 轮 · 退回以后) */
  doing: string
  since: number | null
  /** status over time, oldest first: what 回放 shows at a moment */
  history: { at: number; status: ActorStatus; doing: string; who: WaitWho | null }[]
  /** its lane, oldest first: its rounds' work and fix, and the waits for a person inside them (a request out, the
   * session quiet before the person's reply or now); nothing after the requirement's way is over. A quiet run between
   * two stages is on no node's lane: Run.now says it (awaiting_approval since then) */
  spans: NodeSpan[]
  marks: ActorMark[]
  rounds: Round[]
  gates: Gate[]
  /** who has it now: its latest round's run; null before any round */
  run: RunKey | null
  /** a split's unit done by a small requirement: that requirement and its state until it merges in; null otherwise */
  child: { req: string; state: ChildState } | null
}

export type BoardEventKind =
  | 'claim' | 'start' | 'stage' | 'request' | 'approve' | 'reject' | 'refused' | 'blocked' | 'commit' | 'handover'
  | 'accepted' | 'rejected' | 'signed' | 'question' | 'answer' | 'verify' | 'merge' | 'reflect' | 'session' | 'app'

/** One thing that happened, from the trace (brainkit.trace), the Lean ledger, the MR, the bridge, or this app. */
export interface BoardEvent {
  /** its place in the requirement's history, from 1 (the order the board was built in: by time) */
  seq: number
  at: number
  kind: BoardEventKind
  /** where it travels on the graph: a NodeId, or an anchor: 'person' (the owner box, with the runs' roster), 'branch'
   * (the run branch rail), 'checks' (the check ring), 'relay' (the hand-over pill on the rail); from === to: a blip
   * there. By kind: claim, app person → person; start person → the first node; stage, refused, blocked its node;
   * request its gated node → person; approve, reject person → the gated node; commit its node → branch; handover the
   * node this machine's run was at → relay; accepted, signed relay; rejected relay → person; question its node →
   * person; answer person (the bridge's delivery: relay) → its node; verify branch → checks; merge branch; reflect,
   * session person → its node. An event with no node for an end blips at its other end; with no node at all, null */
  from: string | null
  to: string | null
  /** plain Chinese, one line */
  text: string
  /** a step a reader may hide (Lean's stage events and blocked tool calls: 「Lean 细节」) */
  detail: boolean
  src: string | null
  /** the node it is about, the run that did it, and its round there: the feed filters and the replay lights by them */
  node: NodeId | null
  run: RunKey | null
  round: number | null
}

/** One commit of the run branch, the hand-over commit, or the merge into the default branch (the conveyor). */
export interface BeltItem {
  kind: 'commit' | 'handover' | 'merge'
  at: number
  /** 12 hex */
  sha: string
  by: 'person' | 'bot'
  /** its subject, or what it is */
  text: string
  files: number
  /** it changed only Lean's and the run's records */
  records_only: boolean
  /** the run that committed it, and the node whose round the commit falls in (null: outside any round) */
  run: RunKey | null
  node: NodeId | null
  /** the files it changed, with lines added and removed (git show --numstat; A added, M modified, D deleted, R renamed):
   * the lanes' 改动 block (UI问题清单 BOARD-21). Absent from a backend that does not read them: the lane shows the
   * commits and their file counts */
  changes?: { path: string; added: number; removed: number; status: 'A' | 'M' | 'D' | 'R' }[]
}

/** One cell of the check wall: a Lean approval, a verify check, the bridge's and the signer's checks, Brain lint, the
 * 14 days after the merge. */
export interface CheckCell {
  id: string
  group: 'gate' | 'verify' | 'handover' | 'brain' | 'after'
  /** in the cell: 需求, pytest, 桥 … */
  label: string
  /** in the inspector line: 需求审批（REQUIREMENTS） */
  name: string
  /** pass, fail, wait (waits for a person), skip (not run, not needed yet), watch (the 14 days after the merge, still
   * running: nobody's turn), not_needed (not on this requirement's way: a stage Lean skipped, the runner of a repo done
   * only here) */
  status: 'pass' | 'fail' | 'wait' | 'skip' | 'watch' | 'not_needed'
  /** when it was decided (null: not yet) */
  at: number | null
  /** one plain line */
  detail: string
  /** gate cells: the gated node; verify cells: build-and-test */
  node: NodeId | null
  run: RunKey | null
}
export interface CheckWall {
  cells: CheckCell[]
  passed: number
  failed: number
  total: number
  /** the checks not reached yet, by name (「发布审批」): 「目前 3 项都过 · 还剩 发布」. The backend always sends it */
  todo?: string[]
  at: number | null
  /** verify rounds: passed / failed checks over time (the sparkline) */
  history: { at: number; passed: number; failed: number }[]
}

/** 验收 (the sample's 签字单): what a person needs to accept the requirement, and the one step they take now. */
export interface Acceptance {
  /** not_yet (not at the release stage), waiting (RELEASE / acceptance waits for a person), accepted, rejected
   * (sent back), merged */
  state: 'not_yet' | 'waiting' | 'accepted' | 'rejected' | 'merged'
  /** the one step a person takes now; text '' when there is none. *line*: what they type in the session; *comment*:
   * what they write on the MR after the hand-over (`answer D-3 approve`) */
  step: { text: string; who: string; line: string | null; comment?: string | null; mr_url: string | null; session_url: string | null }
  /** who settled the decisions (decisions.md's Who): person, check, brain, agent, unknown, unclassified */
  by: { id: string; label: string; count: number }[]
  /** what the checks say, one line */
  checks: string
  /** when it was accepted or merged, and by whom (a username or pseudonym, never an email) */
  done: { at: number; who: string | null; how: string } | null
}

/**
 * 证据: what `lean verify` sealed for the requirement, judged by brain-kit (brainkit.proof.bind) at the commit under
 * review (backend evidence.py). Its words are brain-kit's (the MR description says them the same way); the page keeps
 * tones only.
 */
export interface Evidence {
  /** brain-kit has its proof module (an older one: false, and *error* says so) */
  available: boolean
  /** off: the project's verify keeps no evidence · none: none counts yet · stale: some of it is of other code ·
   * unknown: whether it is this code cannot be told · failed: a screenshot did not show what it waits for, or a file
   * changed after · current; null when it was not read (*error* says why) */
  state: 'off' | 'none' | 'stale' | 'unknown' | 'failed' | 'current' | null
  /** 截图 3 张（1 张没看到） · 测试输出 1 份 · 是这份代码的; '' when not read */
  line: string
  /** the newest verify took no screenshot, so the ones shown are an earlier verify's: said so, else '' */
  late: string
  /** the commit it was judged at (12 hex) */
  at: string | null
  /** one per plan row, then one per test output */
  chips: EvidenceChip[]
  /** what is missing or wrong, one sentence each (brain-kit's proof.TEXT, as it fills them in) */
  gaps: { kind: string; text: string }[]
  error: string
}

export interface EvidenceChip {
  kind: 'shot' | 'log'
  /** P-2 (web/P-2 when the plan has several units) · 测试输出 tests */
  label: string
  ac: string[]
  /** the plan row's 看什么; a test output's command */
  what: string
  /** current, or what is wrong with it */
  state: 'current' | 'missed' | 'old' | 'unknown' | 'planned' | 'not_taken' | 'void'
  /** the MR's mark for that state, '' when current: 没看到 · 旧代码 · 核对不了 · 还没拍 · 没拍成 · 不算 */
  mark: string
  /** the build-and-test round whose VERIFY sealed what it shows, and its file: where the 产出 drawer opens it; null
   * when nothing of it counts */
  round: number | null
  path: string | null
}

export interface ReqBoard {
  at: number
  row: Requirement
  /** the run branch and the default branch it leaves from and lands on */
  branch: string | null
  base: string
  /** the first thing that happened, and the end (merged or closed) */
  started_at: number | null
  ended_at: number | null
  /** something is at work right now (a session busy, the runner running): the board glows */
  live: boolean
  /** the owner (the sample's orchestrator slot) */
  /** *me*: the one reading is the owner; *you*: the board calls the owner 你 (they are, or nobody has it; a reader the
   * app cannot tell is not someone's 你): the page says 你 and offers 开工 only then (an older backend did not say) */
  person: { status: ActorStatus; doing: string; owner: string | null; me: boolean; you?: boolean }
  /** all six, in order, `init` first (always used) */
  phases: BoardPhase[]
  /** in band order, then stage order, then unit order; at most 64; at most 40 rounds each */
  nodes: StageNode[]
  /** in order of first round (local first when it has one) */
  runs: Run[]
  /** one per hand-over request, oldest first */
  relays: Relay[]
  /** this key's runs on another branch (trace other_runs) */
  other_runs: { branch: string; state: string | null }[]
  events: BoardEvent[]
  /** Lean's progress after each stage record that changed it, oldest first (the first, at 0, before any): what 回放
   * shows at a moment; the last one is row.lean */
  lean_steps: { at: number; lean: LeanSummary }[]
  belt: BeltItem[]
  checks: CheckWall
  accept: Acceptance
  /** what the run took from the Brain (the list under the person) */
  brain: RequirementDetail['brain']
  decisions: Decision[]
  questions: Question[]
  mr_desc: MrDesc
  /** 证据 on the 验收 card (and 需求现状): what verify sealed, at the commit under review */
  proof: Evidence
  sessions: SessionRow[]
  /** the live brain-dev session on this machine (service._live, as on 需求现状): where the person replies, never a review
   * session */
  live_slot: string | null
  credits: Credits
  /** trace: built from brainkit.trace; app: from the Lean ledger and the repo only */
  source: 'trace' | 'app'
  missing: string[]
  error: string
  /** a big requirement split into small ones: the family's numbers from its trace (brain-kit 0d52e97 trace family
   * rollup: itself and every small one added up), and each small one's; null otherwise */
  rollup: FamilyRollup | null
}

/** One file a round handed in or changed, as GET /outputs/{repo}/{key}?node&round lists it (backend outputs.py). */
export interface OutFile {
  /** repo-relative */
  path: string
  /** a declared output (约定它交的: Lean recorded it for the round), else a file its commits changed (其他改动) */
  output: boolean
  status: 'A' | 'M' | 'D' | 'R'
  /** lines, summed over the round's commits; null when not known (a version read from the worktree) */
  added: number | null
  removed: number | null
  /** the commit to read it at (40 hex), 'worktree' for a local document not committed yet (then pin is set), null when
   * this machine has no commit of that version (why says what to do) */
  ref: string | null
  /** the sha256 Lean recorded: the worktree read is refused unless the bytes hash to it */
  pin: string | null
  /** the version 改动 compares with: an earlier round's (base_round) or the commit before; null: a new file */
  base: string | null
  base_round: number | null
  /** the versions the node's earlier rounds handed in, newest first: 和第 N 轮比 */
  vs: { round: number; ref: string }[]
  why: string | null
  /** a build-and-test round's evidence (what its VERIFY sealed): its set; its words, P-2 · AC-2 · 看什么 (a set's
   * output.txt: 截图, 命令输出 tests：pytest -q); how it went (failed: it did not show what it waits for; null for an
   * output.txt), the MR's mark for that ('' when ok: 没看到, 没拍成) and why not. null for any other file */
  proof: { set: string; label: string; result: 'ok' | 'failed' | 'skipped' | null; mark: string; note: string } | null
}

export interface NodeOutput {
  node: NodeId
  /** null: the node has no round yet */
  round: number | null
  run: RunKey | null
  branch: string | null
  /** the round's last commit (40 hex); null when it committed nothing */
  head: string | null
  /** declared outputs first, then the other files; at most 200 */
  files: OutFile[]
  /** files left out past the 200 */
  more: number
  /** nothing to list: why, in plain words */
  why: string | null
}

/** One file as GET /outputs/{repo}/{key}/file?ref&base&path&view&pin reads it: emails already pseudonyms. */
export interface FileView {
  path: string
  view: 'content' | 'source' | 'diff' | 'image'
  /** the commit it was read at, or 'worktree' (pinned) */
  at: string
  base: string | null
  /** read from this machine's worktree because its bytes hash to the sha256 Lean recorded */
  pinned: boolean
  /** markdown · python · typescript · … · text */
  lang: string
  /** this very version is the one an approval was asked for: 就是请批的那一版 */
  asked: { kind: GateKind; sha8: string } | null
  binary: boolean
  truncated: boolean
  size: number
  text: string
  /** view image: the file as a data: URL, a PNG or a JPEG by its first bytes (never SVG) */
  image?: string
}

/** What a turn's message was (backend turns.py, default deny). A person's words never travel, the owner's included:
 * only how long they were. The app's and the bridge's messages as they wrote them down when they sent them. */
export type TaskView =
  /** cause: 开工消息，只有编号 · 澄清问题的回答 (the person's note replaced) · 审查会话的开工消息 */
  | { kind: 'app'; cause: string; text: string }
  /** cause: 合同 · CI 没过 #n · D-n 已答 / 退回 · 问题没发出 · 问题作废; text: whole or its first line, by its kind */
  | { kind: 'bridge'; cause: string; text: string }
  | { kind: 'person'; chars: number; url: string }
  | { kind: 'hidden'; why: string }

export interface CallView {
  at: number | null
  /** 读文件 · 写文件 · 跑命令 · 搜索 · 查知识库 · 其他 */
  tool: string
  detail: string | null
  /** inside the round's time (from its start to its agent's last work on it) */
  in_round: boolean
}

/** One session turn a round was done in: its message, the agent's last reply in it and its calls. */
export interface TurnView {
  /** 第 n 回合 of its session; null when its message is not in a transcript here, or the transcript was cut */
  n: number | null
  at: number
  until: number | null
  slot: string | null
  url: string | null
  task: TaskView
  /** the turn's last reply, at most 20 000 characters; null while it is going or when it had none */
  reply: string | null
  live: boolean
  /** at most 400; more_calls left out */
  calls: CallView[]
  more_calls: number
  /** the other nodes this turn also worked on: 也做了 */
  also: string[]
}

/** A round whose session this machine cannot read: the team runner's, someone else's, a small requirement's own, brain
 * reflect's. The page adds Lean's record of the round (its times) to `here`. */
export interface WhyNot {
  title: string
  missing: string | null
  here: string[]
  do: string | null
  /** when the round asked for approval; null when it did not */
  asked: number | null
}

/** GET /outputs/{repo}/{key}/turns?node&round */
export interface NodeTurns {
  node: NodeId
  round: number | null
  run: RunKey | null
  turns: TurnView[]
  why_not: WhyNot | null
  /** no turn to show and no card: why, in plain words */
  why: string | null
  /** the time a call is lit in: from the round's start to its agent's last work on it (its request, or its end) */
  lit: { from: number; to: number } | null
  /** 用时 (the round's span) always; 回合 and 工具调用 only when its turns are readable here */
  meta: { took: number | null; turns: number | null; calls: number | null }
}

/** A trace's numbers of one run (brain-kit trace family numbers): null when not known. */
export interface FamilyNumbers {
  stages_done: number | null
  verify_rounds: number | null
  verify_failed: number | null
  commits_bot: number | null
  commits_person: number | null
  blocked: number | null
  human_at_judgment: number | null
  human_outside_judgment: number | null
  decisions: number | null
  questions: number | null
  credits: number | null
}
export interface FamilyRollup {
  /** the big requirement's own, and with every small one added */
  own: FamilyNumbers | null
  total: FamilyNumbers | null
  /** the numbers nobody knows (「PAY-1 credits」) */
  unknown: string[]
  children: { key: string; unit: string | null; merged: boolean; numbers: FamilyNumbers | null }[]
}

// ---------------------------------------------------------------- GET /overview: the 交付总览 numbers (the CTO 视图's)

/** One requirement in the eval batch (brainkit.evaluate's runs), for the cards' charts and the ledger's Eval column. */
export interface CtoRun {
  key: string
  repo: string
  /** unattended: handed over, nobody acted after the hand-over beyond the required approvals, delivered correctly;
   * people: handed over, a person stepped in beyond them (or it was not delivered correctly); pending: not judged yet
   * (its 14 days after the merge are not over, or it is not merged); local: never handed over (the person did it with
   * the AI on their machine); ended (brain-kit 0d52e97): handed over and ended without a delivery (the runner could not
   * start it, or its MR was closed unmerged): never unattended, and left out of autonomy's count */
  how: 'unattended' | 'people' | 'pending' | 'local' | 'ended'
  /** what the person did beyond the required approvals (brain-kit's words for the kinds, in Chinese) */
  beyond: string[]
  /** a person's acts at the judgment points, and outside them */
  at_points: number
  outside: number
  credits: number | null
  /** merged into the default branch (epoch s) */
  merged_at: number | null
  /** brain-kit's eval state of it (0d52e97's batch: merged, merged_into_parent, waiting, answered, failed, closed …)
   * and its words; null before 0d52e97 */
  state: string | null
  state_label: string
  /** it has had nothing new for brain-kit's stuck_hours */
  stuck: boolean | null
  /** brain-kit's delivery (correct, wrong, pending, wrong_open, unknown, open, closed, merged_not_here) and its words */
  delivery: string | null
  delivery_label: string
  /** a small requirement: its big one's key */
  parent: string | null
}

export interface CtoNumbers {
  /** brainkit.evaluate ran (else the cards say why, and the numbers that need it are null) */
  available: boolean
  /** the one sentence over the three numbers */
  sentence: string
  /** what the numbers are read at (main @ sha) */
  ref: string | null
  /** 无人值守: [unattended, judged hand-over runs] */
  unattended: [number, number] | null
  /** 人介入: a person's acts at the judgment points and outside them; by kind */
  stepped_in: { at_points: number; outside: number; kinds: { label: string; n: number; point: boolean }[] } | null
  /** credits per requirement (the requirements with credits) and in all */
  /** cut: how many of those requirements' credits leave something out (Requirement.credits_whole false) */
  credits: { per_req: number | null; total: number | null; reqs: number; cut: number }
  runs: CtoRun[]
  /** merged per week (Monday, YYYY-MM-DD), by how: the last 12 weeks */
  weekly: { week: string; unattended: number; people: number; local: number; pending: number }[]
}

/** One of brain-kit's notes on the 交付评估 numbers: what it says, what to do, its own words (细节). */
export interface EvalNote { text: string; todo: string; raw: string }

// ---------------------------------------------------------------- POST /requirements/{repo}/{key}/handoff: 交接给运行机

/** How far `brain handoff` got (backend/core/handover.py): handed (the MR has the hand-over label: the bridge checks it,
 * then the team runner goes on); committed (the hand-over commit is made, the branch is not pushed: no remote, or the
 * push failed); not_labelled (pushed, but the MR has no hand-over label: nothing was handed over); refused (brain-kit
 * said why before anything changed); timeout (stopped at the time limit); failed (brain-kit could not run, or said
 * what the app does not know). */
export type HandoffState = 'handed' | 'committed' | 'not_labelled' | 'refused' | 'timeout' | 'failed'

/** What 交接给运行机 did: `brain handoff` in the requirement's worktree on this machine. The app's own refusals (a repo
 * done only on this machine, someone else's run, a busy session, handed over already) are error answers, in words. */
export interface HandoffResult {
  /** handed over: the team runner takes it from here */
  ok: boolean
  state: HandoffState
  /** one short line, what happened and why: 「没交出去：交接记录还没写好（没写下一步）」 */
  text: string
  /** 做什么 next ('' when nothing) */
  todo: string
  /** the MR brain-kit named, its link from kit.json's GitLab; null when it named none */
  mr: { iid: number; url: string | null } | null
  /** brain-kit's WARN lines, each a row (read only: no fix) */
  warnings: CheckRow[]
  /** brain-kit's own output, credentials hidden (shown folded under 细节) */
  raw: string
  /** its exit status: 0, 1 (pushed, not labelled), 2 (refused), 124 (stopped at the time limit) */
  code: number
}
