# Brain 交付: brain-kit on Kiro Crew

**安装（中文）：见 [INSTALL.md](INSTALL.md)。** KiroCrew → Apps → Discover → 齿轮 Sources，添加 `https://github.com/ai-for-developer/kirocrew-app-brain`，分支填 `release`（`scripts/release.sh --push` 生成）。

A Kiro Crew app for [brain-kit](../brain-kit): claim a requirement, take its documents through the five gates,
develop it with AI-DLC Lean in a `brain-dev` session, hand it over to the team runner, follow its questions (D-n on
the GitLab MR), and keep the team's in-repo Brain. On the team runner the same app hosts brain-kit's bridge loop and
runnerd, and dispatches the runs into `brain-run` sessions.

**Started from the aws-samples app.** This app was started from the sample app
`samples/agent-team-on-kiro-crew/app` of
[sample-ai-for-software-engineering-on-aws](https://github.com/aws-samples/sample-ai-for-software-engineering-on-aws)
(commit `8674f15`), MIT No Attribution, Copyright Amazon.com, Inc. or its affiliates (see `LICENSE`). The first
commit of this repo is that copy, unchanged.

| Kept from the sample | Dropped (it stays in the sample) |
| --- | --- |
| `backend/core/gateway.py` (Kiro Crew HTTP API with the app secret, loopback only), `sessions.py` (chat slots in sidebar folders, project dir, `trust` per slot, idle detection, resend on start errors, credits), `httpkit.py` (owner-only routes), `store.py` (append-only log, now the app's own events), the bootstrap in `backend/routes.py` / `hooks.py`, `scripts/dev-install.sh`, `scripts/kcapi.sh`, the test fakes (`tests/http_fakes.py`, the fake gateway), and the UI shell: AppBar, URL navigation, SDK stubs, polling rules, preview build. The UI's look: the team board's styles, particle engine and overlay, role icons and hooks (`ui/src/board/styles.ts`, `engine.ts`, `overlay.ts`, `hooks.ts`, `RoleIcon.tsx`: copied), its graph, lanes, conveyor, test wall, event feed, sign-off card and replay (redrawn for one requirement's way), the CTO view's styles and charts (`ui/src/cto/`), the card frame (`ui/src/kit.ts`) | The agent-team engine: team files, roles, packs, starters, the taoyuan demo, the CTO history (its numbers are brain-kit's eval here), checks/deploy/site |

Added to the session runner: `strict_agent` (a session boots exactly the template `brain-dev` / `brain-run`, or is
not opened: Crew's default agent would run without Lean's hooks), exact slot names (the bridge names runner slots
after the run id), deliver-once sends (no resend, no nudge), per-slot credits over the whole transcript, and the
bridge's KiroCrew surface (`adapter_create / set_project / send / slot / stop`). A turn is read back a page (500 rows,
`before`) at a time until the app's own message (its `sendId`), however long the turn; a message the app sent is sent
again only when the whole transcript has no row of it, so a session that took it never gets it twice. A slot's
credits are read the same way, every page to its first row (KiroCrew answers at most 500 rows a call; up to 40 pages,
past which the stats say they are not whole). A slot's 轮 are its turns as 产出 cuts them: a message and the agent's
work up to the next message, not its replies (one turn often has several).

## Views (UI text is Chinese)

| View | What it shows | Where it comes from |
| --- | --- | --- |
| **交付总览** | The sample's CTO view. The hero: one sentence, 「这个仓库只在本机做」 for a repo without a remote, and three numbers from brain-kit's eval (how many handed-over requirements ran unattended end to end, how often a person stepped in at the judgment points against outside them, credits per requirement, which says how many of those requirements leave something out: 「1 个还有没算进来的，实际更多」), each with its small chart, and who settled the decisions (the funnel); a number with nothing to count says 还没有 and when it will have one, with no chart. 轮到你: every requirement that waits on the person, the longest wait first, with how long it has waited (`waits.since`) and its one step as a button (`waits.do`). 全队卡点, beside it in the same look: what is stuck across the team, the worst kind first, with the counts by kind (「超时 1 · 很久没动 1 · 目录撞车 1 · verify 失败 1」): 超时, a human gate (a Lean approval, a question to answer, the acceptance) that has waited `wait_timeout_h` working hours or more, the same on every machine (a person's own next steps are 轮到你's); 很久没动, someone else's run on their own machine with nothing new pushed for `stale_after_h` working hours (not a big requirement waiting for its small ones, nor a small one's MR waiting to be merged); 目录撞车, two open requirements of one repo whose expected paths could hold the same file, a glob read as brain-kit reads it (either key opens its board); verify 失败, one check failing `verify_fail_limit` `lean verify` runs in a row, when the AI should stop and ask. Each key opens its 运行过程, 「还有 N 件」 filters 交付清单 to all of them, and with nothing stuck the card says what it watches. Working hours are Monday to Friday, 09:00 to 18:00, this machine's time. 每周合并 (12 weeks, by how they were delivered, 图 / 表格). 需求流动, the kanban of what is in progress: columns 需求 → 设计 → 构建 → 交给运行机 → 发布 · 验收 → 已合并, lanes by `waits.who` (轮到你 / 等别人 / AI 在本机做 / 运行机在跑; AI 在本机做 only while the session works, not while it asks for a tool approval), the counters above it by the same lanes, 轮到你 split by `waits.do` (「轮到你 5（等回话 2 · 等开工 3）」), and the longest wait; in a repo done only on this machine the runner's column and lane fold to a narrow 「这次不需要」; a card opens the requirement's 运行过程. Its switch 按阶段 / 按人 (按阶段 first): 按人 makes each person a lane (you first, then the most open) with 「在做 n/上限」, red at or over `wip_limit`, and a lane for what nobody has (with nobody holding anything, one lane says so); a person's name filters 交付清单 to what they have open. 在做 leaves out 待认领 and a big requirement while its small ones are built (they count), and counts a row on the team runner under its owner; in one repo of several the lanes say 「在这个仓库做 n」, with no limit, since a person's whole load is 全部's. A card past a timeout or a limit has a red mark in either view (超时, 很久没动, verify 失败 3 次). The team view ranks nobody and shows no conversation. A big number, a legend entry, a week, a column, a lane or 「+N 个」 filters 交付清单, the ledger with the eval batch's summary rows. Every requirement: claimed, local, handed over, running on the runner, parked with D-n, waiting acceptance, merged; owner; Lean's progress in the one form the whole app uses (「做完 4 · 跳过 6 · 下一步：发布 · 构思整段不需要」, from `lean.done_n`, `skipped_n`, `next` and `skipped_phases`: it counts the stages on the way, and a phase skipped as a whole leaves the count and is said once; where it is, 在做 once Lean started its next stage, is one rule on 交付总览, 需求现状 and 运行过程 (`vocab.leanNow`): 还没开工 before Lean recorded anything, nothing next once the way is over, and the board draws Lean's next stage as a box even off the default way, and the graph's lit edges lead to it, through the 审查 still to do before 发布); what it waits on (the exact approval line, with 打开会话); the first line of its 变更说明 (`summary`, made in the background), credits, overlap warnings, its red marks and 「verify 失败 n 次」 (`verify_fails`, red at the limit). Eval numbers, the runs Eval skipped and its notes (also the runs known wrong that wait for their merge: autonomy's `wrong_open`), and the low-score queue (复看队列); the decision funnel, with the decisions.md rows whose Who is not in the vocabulary (没归类) 大需求拆成小需求 (brain-kit 0d52e97): 需求流动's 待认领 lane (a small requirement nobody has claimed, in 构建); a big requirement's card with its 已合并 n/m as segments and its small ones in short; a small one's card with its big one, its unit, its dependencies and what holds it; 交付清单 with the small ones under their big one (a fold on its row: 「小需求 1/3 ▾」) and eval's families as summary rows (已合并 n/m with the ones not started or closed, its state, a person's acts at and outside the judgment points, questions, credits); the 交付方式 column with eval's state of the run when it says more (有了回答、运行机没接着做, 已合进大需求, 需求关了 …), 停住了, and 没交付就结束 (运行机没能开始 / MR 关了没合并, left out of 无人值守's count) | git refs (`claim/*`, run branches, the default branch), worktree files, run.json, park.json, the Lean ledger; `backend/core/roster.py` over the rows (the team view); `brainkit.evaluate` when present; `brainkit.mrdesc`; the bridge ledger in team mode; the MR via GitLab (person's token); `brain intent list --all --json` for families |
| **运行过程** | One requirement as the sample's team board (切换需求, the sample's 选择运行: a select of every requirement 交付总览 lists, by repo, 「等你」 marked, opens another one here in place of this one, so ← 返回 still goes where the view came from). 你要做的一步, a narrow strip above the graph: what the person does now and where, with the means beside it (the line to type with 复制那一行 and 打开会话, a question they may answer here with its form in place, the MR, a command for the terminal); when it is not their turn, one dim line says whom it waits for. 打开会话 there and in the top bar is the live dev session the backend names (`live_slot`, `service._live`, as on 需求现状), never a review session beside it. A hand-over committed here whose push failed (`push_handoff`) is not handed over on the board either: its event and the conveyor say 「交接提交了，没推上去」, the relay's 交接 step failed with what to do (push, then 交接给运行机 again on 需求现状), and the check wall says 推上交接, never 桥 在核对. The hero graph is the run's flow as the reference team board draws it: AI-DLC's phase bands (0 初始化, 1 构思, 2 启动, 3 构建, ✓ 发布, 4 运维 · 回写) with a box for each stage on this requirement's way (the one stage table, `backend/core/stages.py`; 代码生成 one row per unit, 构建测试 over 审查, 回写 Brain at the end): the stages Lean recorded, and dashed the default way's stages not reached yet, with what each waits for. Each box is in its state's colour, shape and words, and a finished stage turns solid once the way has moved past it. A box that took more than one round has 第 N 轮 (a design sent back, a verify that failed, a release asked again after new code; on a person's machine, CI 没过 #1234 when the MR's pipeline failed, read from GitLab), cut from Lean's ledger every time the board is built. An approval it waits for is a chip under the last box it covers (等你批 · 需求和设计, 等产品验收 · D-3; a click brings 你要做的一步 with the line to type), a round sent back before it a chip too (✗ 退回 · D-2), and a decided approval a badge on the box (✓ 你批了). The box's last line says who did it and for how long (✓ 本机 · 0:28; 运行机, the big requirement, or the owner's name for a session on another machine), told by the ledger file each clone writes. A stage Lean skipped gets no box: the band's foot names it, and with the design skipped (Size: small) 代码生成 waits 「等批了需求」, never on a design approval, and with 发布 Lean's next stage the 审查 not made yet says 「等开审查会话」 (回放 says it from the moment Lean turned to 发布); a phase with nothing on this way folds to 「这次不需要」 (a small requirement's 发布 and 回写 fold to 「跟大需求一起发布」 and 「跟大需求一起回写 Brain」, and it draws no hand-over). The owner's box lists 这次谁在做 (each run and its state, each hand-over's 交接 → 桥 → 签名) above what the Brain brought in, with a dashed line to the box a run waits at or works on; the run branch is the rail along the bottom (each run's commits, the hand-over pill, the merge) with the check ring at its end. A click on a box, a lane, a commit, a check or an event looks at that stage everywhere on the page. 运行：全部 ▾ in the top bar (the reference's 选择运行) lists 全部 (the whole way by phase: 本机做启动，运行机接着做构建、发布), each run with when it worked, the stages it did with their rounds and where it is now (运行机 第 1 次 · RUN-PAY-9-1 · 4 个阶段 · 18:50 起 · 代码生成、构建测试 5 轮、审查、发布 4 轮 · 等产品验收), and the runs on another branch, greyed (not read into this board). Picking one dims the boxes it did no round of and shows only its flow in the cards, the conveyor, the lanes, the check wall, the events and 产出; nothing is renumbered (第 N 轮 and the events' order stay the whole way's). The run is in the URL (`run=local|runner-<n>|child-<KEY>|parent-<KEY>`); a pick is a history entry, so 后退 returns to the run before without opening the board again; a run the board does not have shows 全部; with one run the button names it and has no menu. Under a box at work, its ticker says what it does right now, as the reference's does: for a run whose session is on this machine, its last call (▸ 写文件 application-design.md), with the end of the last line its agent wrote as the box's second line (「…再一次写进价格表。」); for a run elsewhere, its last recorded step and how long ago (▸ 运行机提交 2949c0b… · 4 分钟前); the hover card says both whole. Under a runner run's row in the owner's box: its run id, then when it asked and what (16:12 请批发布). KPIs (检查通过 is the latest verify's checks, the check ring too: 3/3 · 第 5 轮 · 5 轮都过, — before any; AI-DLC Lean counts the stages on the way, as 交付总览 does, and says a phase skipped as a whole once; its 在做 / 下一步 is the box the graph is at or leads to next, `ui/src/board/way.ts`, the one rule its edges follow too, so a stage Lean never closed but the graph shows through is not named next; under a picked run every card counts that run only); 验收 (the 变更说明, the decisions and who made them, the checks, 证据, the one step a person takes: the line to type, the MR to answer on; 证据 is what `lean verify` sealed, judged by brain-kit at the commit under review, in brain-kit's words: 「截图 3 张（1 张没看到） · 测试输出 1 份 · 是这份代码的」, a chip per plan row and test output with the MR's mark for what is wrong with it (没看到, 旧代码, 还没拍 …) that opens 产出 at the round and file that sealed it, and brain-kit's sentence for each thing missing or old; a brain-kit older than its proof module is said so); 提交传送带 (each commit named by the run that made it, with the files it changed; the hand-over; the merge); a lane per stage that did a round, on one time axis that ends at the merge: its state and the runs that did it, its rounds (第 n 轮 and how each ended), what its latest round delivered (the agent's own note) and changed (改动; 构建测试: the checks of its latest round), how long it took; an idle session's hours, and a run's with no session on this machine, are a wait for the person, not work; 检查墙 (Lean approvals, verify, the hand-over, the Brain and the repo's kit, after the merge; 「目前 3 项都过 · 还剩 发布」 while a check is still to come; the 14 days after the merge as 观察中, not as a wait for a person); open questions; the event feed. A requirement claimed but not started has 去开工, which opens 开需求 with its key and title. Polled every 2 s. 产出 (the reference's 每一步的产出 drawer): what each stage handed in, round by round, opened by 看产出 › on a lane or a box's hover card (a round there opens that round), a lane's file, a gate's 看文档 and 产出 in the top bar; a step pill per stage with a lane, its rounds (第 1 轮 退回 · 第 2 轮 CI 没过 #1234 · 第 4 轮 等验收), what the round was about (done because a pipeline failed, with its page; sent back by whom and the D-n, never their words; the version asked for and the line to type; the commit after a request), its own note, then its files: 约定它交的 (what Lean recorded for it; a 构建测试 round also hands in the evidence its VERIFY sealed, found by the output_sha256 Lean recorded for each evidence check, each file by its plan row, 「P-2 · AC-2 · 看什么」, and how it went) and 其他改动 (its commits' other files, never an evidence set's; a unit's code generation keeps its Paths). A code generation lists 代码改动 first, its files outside `aidlc-docs/` and `runs/` with the biggest change first, and opens the first of them; then 约定它交的 (its plan) and 其他改动 (the records). A round's commits are its run's commits inside it, and its run's later commits, until the stage's next round begins, that hold a version Lean recorded for it. A run that commits its work after it recorded the stage done (the plan with the code) still has that commit in the round. A document opens rendered (内容), a PNG or a JPEG as 图片 (by its first bytes), any file as 源码 or 改动, from the round before's version (和第 N 轮比, every earlier round to pick) or the commit before; 「就是请批的那一版 · sha」 marks the very version an approval was asked for. For a round done on this machine, 它的回复 is the agent's last reply in each session turn the round was done in (rendered, the turn's time and what else it worked on), 接到的任务 each turn's message (the app's own as it was sent; what a person typed only as 你打的话（64 字）不在这里显示 with 在会话里看 ↗, the owner included), 做了什么 every call with its time, lit inside the round's time; a round done elsewhere (the team runner, someone else's machine, a small requirement, brain reflect) shows a card that says where its session is, what is here instead and what to do. The drawer's place is in the URL (`out`, `round`, `file`, `tab`), replaced in place, never a history entry; Esc closes what is on top first: a box's card or the 运行 menu, then the drawer, then the box looked at. 停止, red in the top bar while this requirement's own session on this machine works, asks first (Esc is 取消), then `POST /requirements/{repo}/{key}/stop` stops the turn of that requirement's 本机会话 and 审查会话 and nothing else: the backend finds the sessions itself, the page names none, and the session stays in KiroCrew for 打开会话 to carry on. 回放 plays a finished requirement's trace over its stages (1× / 2× / 4×, scrubber; the URL says `replay=1`; Lean's progress at a moment is the backend's own count at its records, `lean_steps`; a band's foot line names only the stages skipped by then, and a band Lean skipped whole says 还没轮到 until it was). Like the reference's replay, it holds 4 s on each decision a person made (an approval, a send-back, an answer, the product's acceptance of 发布; once per ask, so D-2's answer and the approval that follows it are one stop): 2 s with the ask still open (「应用设计：等你批」 in the bar), then 2 s with what they chose lit on the box, its chip (✗ 退回了, ✓ 批了, ✓ 回答了, ✓ 验收通过) and the bar (「应用设计：你退回了设计」). A 退回 is red wherever it is said (the held choice, the flash when it lands, a round sent back on its lane, in 产出's pill and banner, the box's ✗ 退回 · D-2 chip, as the legend's 退回 · 没过); orange is 返工, the stage being done again. A jump on the scrubber never holds. 产出 stays open in a replay, with the replay's controls in it, and shows the replayed moment: only the rounds started by then (a later one is not there, and a step not started then says 这一步那时还没开始); a round over by then at its own version, except a file read at a commit its run made later (这一轮那时已经结束：那以后才提交的 n 个文件这里没列); one still going with only the files committed by then, what it hands in and 「就是请批的那一版」 once it asked for approval (a round whose code changed before anyone decided is asked while its stage waited, as Lean recorded), and its session turns up to then (a turn still going has no reply yet). 看产出 opens it during a replay too; 退出回放 brings back the live drawer. A big requirement: 「大需求 · 已合并 n/m」 in the top bar (a jump to its family card), the card 拆成的小需求 under the conveyor: 已合并 n/m as segments, the counts by state (待认领 / 认领了 / 在做 / 已关闭), each small one as a lane (its unit, title, dependencies with ✓ or 等, what holds it, who has it, its own numbers from the trace), the roll-up 整个大需求加起来 (commits, verify, a person's acts, credits; what brain-kit could not count is named, not 0), and 拆成小需求: brain-kit's dry run first (per unit: 新登记 / 没变 / 改了 / 关掉 …, its warnings), 确定拆 applies it. A small one: its big one in the crumb and the top bar (a link), the card 「<大需求> 的小需求 · 单元 web」 (dependencies, paths, contracts, the big one's branch, what holds it) with 认领 (the first claim wins; the one who was late is told who has it), 放弃认领, 开工 (`brain start --key`, then a `brain-dev` session in its own worktree; held with the dependency named; 「在 <DEP> 的分支上先做」 = `--on` only when brain-kit would allow it), 同步 (`brain sync`, conflicts by file); before it starts, the way of a small one (认领 → 开工 → 做 · 同步 → 交接 to the big one's branch → 合并) | `GET /board/{repo}/{key}`: `backend/core/board.py` folds `brainkit.trace.build`, the Lean audit events, the run branch's commits, park.json, the MR and the app's sessions into one snapshot; 产出: `GET /outputs/{repo}/{key}?node&round` and `…/file?ref&base&path&view&pin` (`backend/core/outputs.py`) over that very fold, files from commits only; 证据: `brainkit.proof.bind()` at the commit under review (`backend/core/evidence.py`); `…/turns?node&round` (`backend/core/turns.py`) joins Lean's HUMAN_TURN records to the run's session transcripts here; the family from `backend/core/family.py` (`brain intent list --all --json` in the background, else the claims here); `POST …/split`, `/claim`, `/start`, `/sync` run `bin/brain` |
| **需求现状** | The sample's 交付详情 layout, with 切换需求 as on 运行过程. 此刻 first: who it waits on, since when, and the step right there (打开会话, 复制那一行, 开审查会话, 开工, 去 MR 上回答, 回写 Brain); while it waits for its acceptance (or the release approval), its 证据 as the 验收 card says it, a chip opening 运行过程's 产出 there. A requirement only claimed is one sentence (「认领了 3 小时，还没开工」, or 「等 bob 开工」 when someone else claimed it) and 开工; no trace is read for it. Then 周期, 时间花在哪, 成本, 检查; 价值流 on a time axis (AI on this machine, the runner, waits for people, rework; the key moments named under it; 图 / 表格; a part or a moment opens the board there); Lean's stages (Lean 0.3.1's 26, in AI-DLC's phases like the board's bands: 1 构思 · 2 启动 · 3 构建 · ✓ 发布 · 4 运维; a phase skipped as a whole, or one Lean never asks for with nothing recorded, is one cell; a stage Lean never asks for is 可选 until it is recorded; 构建测试 is 做完 only as Lean's own page says it: every unit's code done and the last verify passed on that code, so a pass while the code is still being written leaves it not reached, and a failed verify or a later record naming other code leaves it 在做) and approvals, Session notes, decisions by stage name with their Brain citations and who settled each (brain-kit's Who vocabulary: `agent` or a bot user, `brain <entry id>`, `check`, `<P-xxxx> <V-xxxxxxxxxx>`, `owner`; anything else is 没归类 and not shown, it may be a real name), each opening the board at its stage; parked questions and answers (MR link), the MR's 变更说明 (formatted, 复制 copies the Markdown for the MR description), the run trace timeline (a commit's author date, and its commit date when it differs; how it was merged, in words; a stage note the AI wrote in English folded as 「AI 写的原话（英文）」 so the line reads Chinese; code in monospace only for a file name), the sessions this app opened for it on this machine with their credits and 轮 (its brain-dev and review sessions, and its runs' when the team runner is here too; on the runner's page, the sessions before the hand-over when they are here), Brain usage. 成本 adds them up, and where they leave something out it says 至少 and lists what (a subagent: KiroCrew reports its credits nowhere this app reads; a turn that reported none, most often one cut short; a session too long to read whole; the runner's sessions on another machine once that runner has the run, not before it takes the hand-over or when it refuses it; on the runner's page, the sessions before the hand-over when they are not here); 会话 says 合计至少 with the list on hover, 运行过程's credits says 至少 before the number, as 成本 does, and 「N 个会话合计，另有 k 处没算进来」 (the list on hover), 验收 says 至少, and 交付清单 and 需求流动's cards say 至少 before the number: never a smaller number shown as whole. In a repo done only on this machine the runner's parts are not shown The run trace timeline also says a bridge decision in the words of its state, with the commit it was asked at, and this requirement's runs on another branch (brain-kit 0d52e97's `other_runs`, not counted in its trace); a big or small requirement has its family card (the same as on its 运行过程, with the same buttons) | `brainkit.trace.build` (else the Lean ledger, its audit files shown as `L-<6 hex>.jsonl` like brain-kit's), decisions.md, `sessions.slot_stats`; the 变更说明: `runs/<KEY>/MR.md` at the commit when the branch has it, else `brainkit.mrdesc.build` / `render` at that commit |
| **开需求** | The way in five steps (交给运行机 is 「这次不需要」 for a repo done only on this machine), then three cards. 认领: the repo (by its name), title, 英文短名 for the branch (suggested from the title), key (the next free one), expected paths (separated by commas, spaces or new lines), 认领人 (the name brain-kit will claim it with; 开工 waits for 「你的 GitLab 用户名」 and its 保存 when brain-kit would not know). 需求内容（选填）: 「用一段话说需求」, a paragraph typed or pasted (up to 20000 characters), which brain-kit takes in as one more document, 需求描述.md, first (the backend writes it to a folder only this user can read for the dry run and the start, and removes it after; the session reads it in the material package, never in its first message); the documents (选文件… / 选文件夹… open this machine's own file dialog and add the paths chosen to the box, one per line, each once; or pasted, ⌥⌘C in Finder) and the files left out (by name, `*` allowed); the five gates (consent, classification incl. 不确定, business keep/mask, personal data and secrets that brain-kit masks), for the paragraph as for the documents, gate 2 saying up front when the repo takes no confidential material; with neither a paragraph nor documents the gates are one line. 先检查 (a dry run) says what stops it as what to do on this page (the field it names is marked), the counts, one card per file in words, and what may be a name or a password but may as well be a word (请你确认: not masked, stopping nothing); brain-kit's own words are under 细节. 开工 runs `brain start` and opens a `brain-dev` session in the run's own worktree; the button is off, with why next to it, while KiroCrew cannot open the session (naming the fix button above), while the default branch is not on the remote yet (推送 right there), or until the form is complete and checked. When the person already has `wip_limit` or more requirements open (交付总览's roster), one line above 开工 says how many and suggests handing one over first; starting a claim that is already counted does not add one, and the line never turns 开工 off. Opened as `?view=new&repo=<id>&key=<KEY>&title=<title>` (运行过程's and 需求现状's 开工 of a requirement claimed already), the key is fixed, the title filled, and 开工 starts that claim. Without a usable repo there is no form: 导入仓库, or the next step of a repo on its way 「大需求：先拆成小需求」 (one line: requirements and design as usual, the units in the design, then 拆成小需求 on its 运行过程; off, with why, when the repo uses Jira or brain-kit cannot split yet): the session's first message says so | `brainkit.intake.package.build(dry_run=True)`, `bin/brain start`; `GET /overview` for the roster |
| **Brain** | One repo's Brain on its default branch (the repo by its name). Counters: 条目 per file, 知识有用率 (the entries there that a run cited), 重复纠正率, 撤回候选 with what to do, Brain 检查; the numbers count only the runs merged into the default branch. The four files as tabs (产品, 技术, 改进, 项目), each entry a card (type, side, how it relates to another entry, repeats, 撤回候选, sources, how often runs load and cite it, `code` in monospace), filtered by text and type; beside them 检查 (brain lint) and 用处 (the corrections that keep coming back, the entries never cited). 每次运行: each merged run's loads and citations, and the runs not merged yet apart, with what 需求现状 says for them. 回写 in four steps (选一个需求 → 预览 → 确认写入 → 写在新分支上): the preview's changes, or why there is nothing to write; apply on a new branch from the default branch (the reply names the branch and the worktree brain-kit wrote it in, `~/.brain/worktrees/<repo>/…`), or apply and open the MR (its link; the write buttons are off once written). An empty Brain says what fills it, with 去回写 or 去开需求; without brain-kit it says that one cause and leads to 设置 | `brainkit.entries`, `usage`, `brainlint`, `reflect`; `bin/brain reflect --apply [--push]` |
| **待办** | What waits on the person, by the judgment every view reads (`waits.who` and `waits.do`), so the list, the tab's count and the sidebar badge agree: the local session idle, waiting for them to talk (会话在等你回话, with 打开会话); Lean approvals pending (the line the person types in that session, 复制那一行, 打开会话); the person's own clarify questions (answer here); D-n questions on the runner (去 MR 上回答); acceptance (去 MR 上验收); claimed and not started (认领了还没开工: 去开工, 开需求 with its key); merged with new knowledge not written back (等回写 Brain: Brain's 回写 on that requirement); refused hand-overs (why, in words, and what to say to the AI); hand-overs pushed without their label (没交出去的交接); runs on the runner stuck for good (卡住了的运行: the signer refused the answer, the question is void or could not be asked); Kiro Crew's failing checks with their fixes; a requirement of yours closed with `brain intent close` whose run branch or MR is still there (delete the branch, close the MR); small requirements you claimed whose dependencies all merged (可以开工的小需求, 开工 here), and your big requirements whose small ones all merged (等评审、发布的大需求, to its 运行过程); a push of your progress at a Lean checkpoint that origin refused (进度没推上去), with what happened, what to do and git's words under 细节, until origin has the branch's tip | as above; `pushes.json` in the app's data dir (`progress.py`) |
| **运行机** | On a personal install (the default), the team runner as GitLab shows it, read only, with the person's own token: for each repo whose kit.json names `bridge_user`, the open MRs it works on and why (在做: the hand-over's running label; 巡检: MR 巡检 replied or summed up on it within a day, not its own 「运行机先不管这个 MR」), the bridge's questions that name the person and that nobody answered after (as 需求现状 shows a question, 去 MR 上回答 at its note), when it last acted (its account's newest note on the MRs read); a repo without `bridge_user` is one line, 这个仓库还没有团队运行机; no token or a GitLab that refuses is said in words with 做什么, the last read kept with when it was. On the team server in team mode: the bridge ledger (questions in brain-kit's states, `asking`: a bridge posting it right now; 0d52e97: the messages into the runs' sessions by outbox state, 送进会话的消息), what the bridge said about each labelled MR last round (接手了, 拒绝接手, 看过了, 标签刚被拿掉 = `unlabelled`, 正在别处签名 = `waiting`, 这一轮没查成), runs in brain-kit's states (`signing`: the run id reserved while the signer signs the contract; `failed` 没能开始) with what the bridge holds about each under it (the acceptance question held until the pipeline is green, runnerd silent, sends that keep failing, queued, a message not known to have landed), signature requests waiting for the signer, the ones it refused, and vouchers it has not answered yet (asked for again, the same voucher, next round); 配置: the bridge.json and runnerd.json keys brain-kit reads, each with its value or default and one line; the runner's Lean hooks as brain-runnerd checks them (`cli.runner_hook_problems`); MR 巡检: every open MR of the projects whose kit.json `work_sources` names `mr-patrol`, as its last round saw it, the ones at work first, with what there is to fix (a failed pipeline and its jobs, or the checks Jenkins reported into it, an open review thread with whose it is and where, a person's plain comment, a conflict), its state in words (在修, 等会话, 排队, 这一轮没成, 没修好: two failed attempts, or three dispatches with no person pushing or commenting in between; 修过一轮, 没问题, 不管) with why it waits or is left alone, and its brain-patrol session; 测试环境报错: a short list of the errors found in the test environment's log of the projects whose kit.json `work_sources` names `staging-errors`, the ones at work first, each with its signature, how many times it was seen, its state in words (在修, 开了修复 MR，等人审, 开了 Issue，等修, 刚看到, 交给人了, 已关) with why it waits, its Issue, the MR that brought it in, the fix's MR and its session | personal: `GET /team/status` (`runner_view.py`: the MRs with the running label and those changed in the last day, their notes, each repo at most every minute); the team server: the hosted bridge (its ledger's outbox and notices), bridge.json and runnerd.json, the signer spool; MR 巡检's last round (`GET /team/patrol`); 测试环境报错's (`GET /team/staging`) |
| **导入仓库** | How a repo gets into the app (the bar's button, and every place that has no repo: 交付总览's empty state, 开需求, 设置's 仓库 card). 从哪来: 本机目录 (a path, or one click on a checkout found under `~/workplace` and `~/workspace`) or 克隆. 检查: ✓ / ! / ✗ with why and what to do, 你是谁 among them; the rows that are fine are folded, and 「还没装 brain-kit」 is the next step, not a fault. 安装, for a repo not installed: the kit.json form with a live preview; by default in place on the checkout's branch (a requirement can be opened today, in a team repo after 推送), or on a new branch for an MR (a requirement can be opened after the merge), each way with one line that says what it costs and the recommended one marked. The install puts the repo in the list by itself; its card then says where it is and has the one next step as a button (开需求, 推送 main, 推送并开 MR or 合并到 main). The self-check, the files and brain-kit's own output are folded. 推送 and 推送并开 MR come only after a confirm (for a team repo's main: it changes the team's main without an MR). An install branch that can no longer go in offers 改成直接装. 加入列表 for a repo installed before | `/import/*` (backend/core/importer.py, installer.py, inplace.py, landing.py), `imports.json` / `upgrades.json` in the app's data dir |
| **设置** | brain-kit first when it cannot be used: why, by its cause, the line to type (its clone, or a pull), its place and 保存, which reads brain-kit again. Then brain-kit (its version and what its optional parts do), 你是谁 (a token is only said to be there) and the team runner; the repos, each with its state by its reason (能用, 没装, 找不到目录, 等 brain-kit …), path and settings, 移出列表 (the app's list only), 导入仓库 and, per repo, 你是谁 (the name brain-kit goes by there and where it came from: GitLab 令牌, BRAIN_USER, brain.user or 登录名; or 「你的 GitLab 用户名」 / 「你的名字」 when it would not know); Kiro Crew checks with their fixes, each fix named as its text says (and, per repo, Lean's lean.json leaving `runs/**` out of its code fingerprint; an old hook adapter, which matters only for a repo that hands over to the team runner: ! there, not 有问题, with 重装 kit 文件; a requirement folder the app could not move into its repo's folder: !, with that folder's place in the sidebar to drag it into, or that the app tries again, checked again whenever the page asks so it goes once it is in; a 「Brain 需求」 that is a person's: 有问题, new requirements cannot open a session in it, rename it); the form: work root; 和队友同步; 全队视图's four limits, one plain line each with its number in it (每人同时在做的需求最多 3 个, 等人批准、回答或验收超过 4 个工作小时算超时, 别人的需求 8 个工作小时没有推上新进度算很久没动, 同一项检查 verify 连续失败 3 次该停下来问人: `wip_limit`, `wait_timeout_h`, `stale_after_h`, `verify_fail_limit`, whole numbers within bounds); and, on the team server only, team mode as one checkbox until it is on (then the bridge's and the runner's config files). Below the form, on the team server only, 一键建运行机 builds a same-user team runner itself. 账号 is 项目令牌 (one project: GitLab 地址, 项目, and a one-time token, used once and kept nowhere, that makes the two project tokens) or 机器人账号 (several projects, 项目 one per line, every repo on GitLab to start with, served as two GitLab users of the runner's own: the bridge's personal token with `api`, its user a Developer in every project, and 运行机怎么推送, this machine's SSH or 机器人账号 with its personal token; both tokens kept on this machine, and left empty on a later run they are the ones kept; the one-time token optional, without it the labels and kit.json MRs are made as the bridge's account and 产品 and 开发 must be typed). Then 产品 and 开发 (blank: the token's owner), 巡检所有开着的 MR (ticked unless unticked: it writes brain-patrol, and the kit.json MRs add `mr-handoff` and `mr-patrol` to `work_sources`); Jenkins 地址, Jenkins 只读用户 and Jenkins token, when the MRs' pipelines run on Jenkins (blank: none; MR 巡检 reads a failed build's log as that read-only user, at the address this machine reaches; bridge.json gets `jenkins`, the token goes into `jenkins-token` under the root, 0600; the last run's address and user come back, the token does not, and a rerun with it blank keeps the saved one); 开始建, then each step with ✓, 等人 (a kit.json MR to merge, its link) or ✕, its words and 细节, and under a step about several projects a line for each with its own mark, words and link, read every second while it runs; 再跑一次 makes only what is missing (and gives a project token in its last 14 days a new value, the same account), and a project added or taken out changes nothing in the others; a run cut off part-way says the step it stopped at. 和队友同步 has 每隔几分钟从远端取最新 (5 by default, 0 for the button only), with each repo whose last fetch did not work and when the others last got the latest, and 过关卡时把进度推到远端 for each repo done with the team (on by default), with the backend's reason when it cannot push there and the line to type. A repo done only on this machine has no push setting. At the bottom, 这台机器 says what this install is: a personal computer (the default) or the team server, switched with 这台机器是团队运行机（服务器） after a confirm that says what changes (red when it would stop this machine's bridge: back to personal turns team mode off); settings of 0.4.2 and before with team mode on are the team server, so a running runner keeps running. A personal install's 团队运行机 card leads to 运行机. Each setting has one line that says what it does | `settings.json` in the app's data dir (`role`: `personal` or `team_server`); the last fetches (`progress.py`) |

Every view uses the sample's components: cards, state chips, role icons, the progress as segments and charts, and
empty states that say what to do. A view's breakpoints follow its own width (CSS container queries), not the window's:
inside Kiro Crew the dashboard's sidebar takes about 230 px. Above every view there can be four banners. The first
shows when `/status` cannot be read: 登录过期了 for the host's 401 or 403, 连不上 app 的后端 when nothing answers, with
再试一次. The second shows when brain-kit cannot be used, said by its cause, with the line to type (its clone, or a
pull) and 去填位置 or 去设置; 设置 has no such banner, because its top card asks for brain-kit's place. The third lists
KiroCrew's failing checks with their fix buttons, except on 开需求, 设置 and 待办, which list those checks themselves.
The fourth names the repos where the app cannot tell who you are, with 你是谁's input and 保存, except on 设置, 导入仓库
and 开需求, which ask for the name themselves. Each banner says what it is and 做什么, with the original words folded under
细节. The preview (`npm run
preview:build`) shows a world made by `scripts/preview_world.py` with the backend's own `board.py` and `cto_numbers`:
four repos, seventeen requirements in every state (one waiting for an approval, one parked on a question, one waiting
for its acceptance, a refused hand-over, one running on the runner, merged ones with whole traces for 回放, a
local-only repo with an old adapter whose session is at work, its ticker read off the end of its transcript). `?pv=` takes flags for other states (`team`, `nokit`, `crewfail`, `noeval`,
`norepos`, `light`, comma-separated). On 运行过程, `?simulate=1` plays the requirement forward by itself, one moment
every 3.2 s (`&stepMs=` sets another pace, `&step=<n>` holds it at the n-th moment), so the live board's animation can
be seen without a running session.

The app never sends a Lean approval line: it shows it, copies it, and opens the session; the person types it. On the
team runner's dashboard no question is answered (answers are made on GitLab, where the person is identified). On a
person's machine, a clarify question of their own run may be answered in the app: into the local session (a local
run), or as their own MR comment `answer D-n X` with their own token (a handed-over run; the bridge checks it as any
other). X is one of the question's options, or `approve` / `reject` for a question without options (brain-kit counts
nothing else). A question that carries a Lean request is never answerable in the app. After the hand-over a Lean
approval is answered on the MR, never typed in a session: the runner's park.json is shown as the runner's question
(named by its approval, 代码生成计划审批, not by the clarify it was asked as), and once the bridge posted it as D-n, 此刻,
the Lean card and 运行过程's step give the comment to write there (`answer D-n approve`, 复制那一行; the app never
posts it).

The routes' JSON is `ui/src/types.ts`. Every requirement (a row of `GET /overview`, the board's `row`, `GET
/requirements/{repo}/{key}`) carries `waits`, the one judgment of whose turn it is that every view reads: `who` (`you`,
`agent`, `runner`, `bridge`, `product`, `builder`, `people`, `nobody`), `text`, `line` (the exact Lean approval line when
that is what it waits for), `do` (the step: `approve`, `reply`, `tool`, `start`, `answer`, `accept`, `redo_handoff`,
`label_handoff`, `write_back`, `watch`, `none`), `since` (since when it has waited like this, in epoch seconds: the
moment the session went idle, the approval was asked, the claim was made, each by its record's own time and never by
when the app looked; null when unknown) and `where` (`session`,
`mr`, `app`, or null: where the step is taken). `local_only` says the repo has no remote, and `lean` counts `done_n` and
`skipped_n` and names `next`; `verify_fails` counts the most `lean verify` runs in a row one check has failed since it
last passed. `progress_pushed_at` says when this machine last pushed the run branch at a Lean checkpoint (null when it
never did). `GET /overview` adds `cto` (the sentence, the three numbers, the runs and the weeks:
`deliveries.cto_numbers`) and `roster` (`people` with what each has `open` against its `limit`, `stuck`, `counts` by
kind, `limits`: `roster.roster`); `GET /board/{repo}/{key}` answers one requirement's `ReqBoard`. 产出 has three routes,
siblings of `/board/` (a stub matching the longest prefix would answer anything under it with the board): `GET
/outputs/{repo}/{key}?node=<stage id, or stage/unit>&round=<n>` answers a round's `NodeOutput` (its files, declared
first, each with the commit to read it at, `base` and `vs` for 和第 N 轮比, `pin` for a document waiting in the worktree,
`why` when this machine has no commit of it), and `GET /outputs/{repo}/{key}/file?ref&base&path&view&pin` one `FileView`
(`view` `source` or `content`: its text; `diff`: its change from `base`, or all of it added), and `GET
/outputs/{repo}/{key}/turns?node&round` the round's `NodeTurns` (`turns`: each with `task`, `reply`, `calls`, `also`;
`lit`, the time its calls are lit in; or `why_not`, the card for a round whose session is not on this machine). `POST
/intake/start` takes `paths` as a list (one path per item) or as one string with the paths separated by commas, spaces
or new lines
(`"calc.py,tests"`); each must be a relative path in the repo. `POST /pick {kind}` (`file` or `folder`) opens this machine's own
file dialog (osascript on macOS; zenity, else kdialog, on Linux with a display; `$BRAIN_APP_PICKER` names a command used
instead) and answers `{paths}`, absolute, `[]` for a cancel; one dialog at a time (409), one open 5 minutes is closed
(408), none on this machine says to paste the paths (501). `POST /brain/reflect/apply` answers `{ok, output, branch,
worktree, mr_url, pushed}`. `POST /settings` takes any part of the settings: a key left out keeps its value (the 设置
form sends no `repos`; the list is changed by `POST /import/add` and `POST /repos/remove` only; nor `role`, which only
这台机器 sends: `personal` or `team_server`; from `team_server` back to `personal` team mode goes off, team mode on a
personal install is refused, and so is `POST /team/setup`, 409 `not_team_server`). `GET /status`'s `app.role` says
which one this install is. `GET /team/status` answers `RunnerStatus`: per listed repo its `runner` (null without
`bridge_user`), with `seen` (the last read: `at_work`, `waiting`, `last_at`) and `problem` (why the last read did not
work, in words). `fetch_every_min` is
a whole number of minutes from 0 to 120. `auto_push` maps a listed repo's path to `false` to stop its pushes. Each repo
of `GET /status` carries `sync` (`RepoSync`): its push setting (null for a repo done only on this machine), why it
cannot push and the line to type, and its last fetch. The import routes:
`GET /import/defaults` (with `found`, the checkouts under `~/workplace` and `~/workspace`), `POST /import/inspect
{path}` (read only), `POST /import/choices {path, purpose?}` (where an install or a kit update goes: `inplace` or `branch`, each with one
line, the recommended one, and why a way cannot be taken), `POST /import/clone {url, parent, name}`, `POST
/import/install {path, mode?, branch?, kit}` and `POST /import/upgrade {path, mode?, branch?}` (the 'kit' fix below;
all three answer `{job}`; `GET /import/jobs/{id}` until it is not running; no `mode`: the recommended one), `POST
/import/commit {path, purpose?}`, `POST /import/publish {path, branch, target, remote_url, confirm: true, purpose?}`,
`POST /import/push {path, branch, remote_url, confirm: true}` (the checkout's branch after an install in place or a
local merge), `POST /import/merge {path, confirm?, purpose?}` (「合并到 main」: a fast-forward; `confirm: true` for a
repo with a remote), `POST /import/pull {path}` (「拉取」: `git pull --ff-only`), `POST /import/add {path}`, `POST
/repos/remove {path}` (`purpose`: `install`, the default, or `upgrade`); and `POST /me/name {path, name}` (`git
config brain.user <name>` in that repo, brain-kit's USER_RE). 大需求拆成小需求 (brain-kit 0d52e97): `POST
/requirements/{repo}/{key}/split {apply}` (`apply` false: the preview, `brain split --dry-run --json`, nothing written;
true: the split), `POST /requirements/{repo}/{key}/claim {release?}` (认领, first one wins; 放弃认领), `POST
/requirements/{repo}/{key}/start {on?, open_session?, trust?}` (开工 on a small one: `brain start --key`, `--on` one
unmerged dependency's branch, then its brain-dev session) and `POST /requirements/{repo}/{key}/sync` (`brain sync`);
every row of `GET /overview` (and 需求现状 / 运行过程's) carries `family` (`ReqFamily`: its role, state, owner, unit,
dependencies and what blocks it, a big one's small ones and roll-up, and what this person can do on it now). 交接给运行机:
`POST /requirements/{repo}/{key}/handoff` runs `brain handoff` in the requirement's worktree and answers `HandoffResult`
(how far it got, why, 做什么, the MR, its warnings, brain-kit's own words: `backend/core/handover.py`); it refuses in
words before anything runs for a repo done only on this machine, someone else's run, a busy session, or a hand-over
already taken, and two clicks at once get one answer. Every
check row (`CheckRow`) is said in plain short Chinese with a 做什么 line, its fix and brain-kit's own words (`raw`,
shown folded): `backend/core/problems.py`.

## How to import a repo (导入仓库)

1. Click **导入仓库** (top right; with no repo yet, 交付总览 and 开需求 show it big).
2. **从哪来**: 本机目录, the path of a checkout (a subfolder or one of its worktrees is fine: the app uses the repo's
   top level, or the main checkout of a worktree), or one click on a checkout the page found under `~/workplace` and
   `~/workspace`. Or 克隆: the git URL (SSH or https, never with a password or token
   in it), the folder it goes under (default `~/workplace`, never inside the work root), its name. The clone runs in
   the background with no prompt (an SSH key or certificate that has expired fails at once: refresh your SSH
   credentials, e.g. `ssh-add` your key or renew your SSH certificate, and retry); the new folder is checked next.
3. **检查** (read only): repo top level, git, current and default branch (origin/HEAD, else main / master), origin and
   the GitLab project it suggests, uncommitted changes (an install in place commits only the files it writes and
   leaves yours as they are),
   brain-kit (`.brain/kit.json`, committed, on the default branch), AI-DLC Lean, the Brain files, AGENTS.md, the Kiro
   Crew checks (with their fix buttons), already in the list. Each row says why and what to do.
4. **Already installed**: **加入列表**. That is all.
5. **Not installed**: check the kit.json values `brain install` asks you to check (key prefix, tracker, GitLab base and
   project, prefilled from origin, default branch, team), then **安装**. Where it goes:
   - **直接装在 <当前分支>** (the default, the way brain-kit's own CLI is used): `brain install` in your checkout, the
     values into `.brain/kit.json`, `brain doctor --offline`, and one commit on the branch you are on, with your own
     git identity and hooks, of exactly the files the install wrote: your own uncommitted changes are neither
     committed nor touched (`git commit --only` of those paths). Usable at once: nothing waits. It is refused, with
     one line why, when the checkout is on no branch, in the middle of a merge, rebase or cherry-pick, or has
     uncommitted changes in a file `brain install` writes (`.gitignore`, `AGENTS.md`, `.kiro/…`, `.brain/…`,
     `aidlc-docs/…`, the Brain): then only the new branch is offered. It never pushes by itself: a repo with a remote
     gets a **推送** button that first says which branch goes where.
   - **装到新分支，开 MR 给团队看** (offered for a repo with a remote or a checkout with uncommitted changes, and
     recommended only when the install in place cannot be done; a requirement can be opened after the MR is merged):
     branch `brain/install` (you can rename it) from `origin/<default branch>` in its own worktree,
     `<work root>/<repo>/_brain-install`; your checkout is not touched. The same install, doctor and commit there.
   Your git hooks run on the commit (nothing is bypassed): if one says no, the page shows what it said; fix it and
   click **再提交一次**. An install that went in puts the repo in the list by itself, and its card names the one next
   step with its button (开需求, 推送 main, 推送并开 MR or 合并到 main).
6. **推送并开 MR** (a new branch, optional): the page first says exactly which branch goes where (`brain/install` →
   origin's URL, the MR `brain/install` → `main` on the GitLab project); after **确定推送** the branch is pushed (no
   force) and the MR opened with your GitLab token (`$GITLAB_TOKEN` or `~/.brain/gitlab-token`, as the app reads MRs),
   its description a list of what the install adds. Without a token or a GitLab project: the exact `git push` / `glab
   mr create` commands and GitLab's new-MR link.
7. **A branch install's way forward** (in 导入仓库, and on the repo's row in 设置 and 交付总览): **合并到 main**, a
   fast-forward (`git merge --ff-only`) when your checkout is clean, on the default branch, and the default branch is
   still where the install branch started (else one line why: e.g. it moved on, so install in place instead); then the
   install worktree (`git worktree remove`, never forced) and the merged branch go. A repo without a remote is never
   shown as waiting for a merge: its row says 「还没合进 main」 with that button. With an open MR its link comes first
   (merging there is the team's way); a local merge in a repo with a remote asks first that the remote does not have it
   until you push, and is offered in 导入仓库 only (its 推送 stays on the page). After the MR was merged on GitLab:
   **拉取** (`git pull --no-rebase --ff-only`, no prompt, a time limit) when the checkout is clean and on the default
   branch. **加入列表** works before all that: the repo shows what it waits for, not an error. The background check
   (every 30 s) clears an install record nothing waits for any more (kit.json on the default branch in the checkout,
   the install branch or worktree gone); it deletes nothing on disk.
8. **移出列表** (设置 → 仓库) takes a repo off the app's list only: no file, branch or worktree changes.

## How it works

- **brain-kit is a library**, imported from the configured checkout under a private package name
  (`_brainapp_brainkit`, nothing on `sys.path`); it is never edited or patched. It must be commit `72b278e` or newer
  (the spool signer, `check_answer` bound to the recorded question note, the question's commit and park hash, the
  hand-over commit rule, run reservations and pending vouchers, the agent-spec hook functions; 72b278e: the edit
  signal, `GitLab.note_edits`, `decisions.edit_state`, `DecisionLoop.reply_states`): an older checkout is refused with
  a message saying what it lacks. Tested on `0d52e97` (QA rounds 3 and 4: the bridge's outbox and notices, a reply
  whose edits cannot be read held as `pending`, the hook adapter version 3, eval's batch and families; the split of a
  big requirement) and on `48b6b97`. Optional modules (`trace`, `evaluate`, `golden`, `intents`, `mrdesc`, `split`) are
  guarded: a missing one turns its feature off and the page says so. Wishes for brain-kit: `BRAINKIT_REQUESTS.md`.
- **The 变更说明** (brain-kit's `brain mr-desc`) is always read at a commit: the row's (a worktree's or a branch's
  tip, the default branch once merged, the runner workspace's HEAD). `runs/<KEY>/MR.md` when that commit has it (a
  regular file, at most 4 MB; a symlink is not read), made inert with `mrdesc.inert` and cut to 60000 characters as
  the bridge does; else `mrdesc.build(ref=<commit>, credits=…)` / `render`. Never the working tree: `build` there
  would run the project's own Lean (agent-writable) for its code fingerprint. Results are kept by (folder, commit,
  default branch commit, credits); the overview's first lines are made in one background thread. 复制 copies the
  inert text, for the MR description; nothing is posted by the app.
- **Each requirement gets its own worktree** under `~/workplace/brain-app/<repo>/<slug>` (`git worktree add
  --detach`, then `brain start` in it), so several requirements can be open on one machine and each `brain-dev`
  session has its own project directory. Kiro Crew lets agents work under `~/workplace` / `~/workspace` only.
- **Lean is read, never run**: stages and pending approvals come from `aidlc-docs/audit/*.jsonl` and the files
  (`lean status` would rewrite aidlc-state.md, and the project's `.kiro/aidlc-lean/` is agent-writable). Lean's stage
  table is copied once, in `backend/core/stages.py`; `tests/test_stages_contract.py` parses brain-kit's vendored
  `aidlc_lean.py` with `ast` and fails when an id, phase, per-unit flag, document or approval kind differs, or a name
  differs from brain-kit's own (`mrdesc.py`).
- **Startup checks** (and fixes on click), with brain-kit's own functions: the agent's spec (`cli.crew_agent_path`)
  carries Lean's three hooks (SessionStart, UserPromptSubmit, PreToolUse via `.kiro/brain/lean-hook.py`) under the right
  triggers (`cli.missing_lean_hooks`, which reads both the original `{"hooks": {"agentSpawn": [...]}}` and the list Kiro
  rewrote every spec into on 2026-11-08, `[{"name", "trigger", "action": {"type": "command", "command"}, ...}]` with a
  top-level `kiroUpgrade`); the engine `agent.acp_backend` is `kas`; every repo has Lean and the hook adapter committed
  (`cli.runner_kit_problems`: worktrees are made from main). The agent fix never rewrites a spec: no file, brain-kit's
  `cli.CREW_AGENT_SRC` is installed; a file that lacks some of Lean's hooks gets only those (`cli.add_lean_hooks`, in
  the file's own format), and everything else stays (other tools' hooks such as Orca's, `kiroUpgrade`, every other
  key), with the old file kept as `<agent>.json.bak-<time>`; a file that is not valid JSON is left for the person to
  fix or remove. A spec that differs from brain-kit's in other ways is fine (72b278e: an OK row names the fields you set
  yourself; `brain crew-agent --reset` takes brain-kit's, `--force` only adds what is missing). The engine fix runs
  `kirocrew config set agent.acp_backend kas`. The agent is read in one place, `crewcheck.crew_agent`: brain-kit's
  `cli.CREW_AGENT` (`brain-dev-crew`, so that no project's `.kiro/agents/brain-dev.md` runs in its place on KiroCrew's
  kiro-cli engine); with a brain-kit from before that rename, `brain-dev-crew` all the same (`constants.LOCAL_AGENT`). Every
  new brain-dev session boots that agent, and the checks, their fix and their words name it. A session opened on
  `brain-dev` before the rename stays the requirement's session, and the board names the agent each session boots. Per repo, the check also FAILs when Lean counts `runs/**` as code
  (`mrdesc.write_problem`: lean.json `fingerprint_exclude`) and (d46765f) when the committed adapter is too old for the
  team runner (`crew_hook_problem`). Those two matter to the runner only: FAIL for a repo handed to it (kit.json
  `bridge_user`, or a requirement with a hand-over), a reminder (WARN, no 待办 item) for a local-only one; not
  committed is FAIL everywhere. Their fix ('kit', 重装 kit 文件) is `POST /import/upgrade`, where 安装 goes: in place
  by default (`brain install` in the checkout, kit.json kept, the kit files refreshed, runs/** added to lean.json,
  `brain doctor`, the person's commit of only those paths on their branch; 推送 when asked), or a new branch
  (`brain/upgrade-kit`, -2 … when taken) from the default branch in its own worktree, the same there, then 推送并开 MR
  with `purpose: upgrade` (or 合并到 main without a remote); nothing to change takes back what it wrote. Its records
  are `upgrades.json`, apart from the install's.
- **Team mode** hosts brain-kit's own `bridge.handoff.Bridge` (poll → dispatch → watch → collect → reconcile) and
  `bridge.runnerd.once` as a background task (`on_startup` / `on_shutdown`): bridge.json is read by brain-kit's
  `load_bridge_config`, and the bridge runs with `signer=` brain-kit's `sign.SpoolSigner` (bridge.json's `signer`
  block) and no key. Rounds run one at a time on the runtime's own worker thread; the page reads the same brain-kit
  `Ledger` from other threads (one shared connection, `Ledger.lock` around a multi-table read), and stopping closes it
  with `Ledger.close()`. Sessions go through `team/crewadapter.py` (brain-kit's KiroCrew surface: `create_slot`,
  `set_project`, `send`, `slot`, `stop`) into the app's session runner, not brain-kit's HTTP client.
- **The bridge's notes on a person's machine** are read back with brain-kit's `decisions.Q_HEAD_RE` (the question's
  first line names its commit and park.json hash); when the repo's kit.json names `bridge_user`, only that account's
  notes count, so a look-alike posted by a person is not a question. The bridge's 「变更说明」 comment (first line
  `decisions.DESC_TITLE`, 48b6b97) is skipped whole, whatever the MR.md in it says. States shown: open, answered (the
  bridge has the answer but could not sign it this round: it sends it later, nobody answers again), delivered, stale
  (a new push), void (not bound to its push and park.json, or its question note was deleted), refused (the signer
  would not sign), invalid; 0d52e97: held (GitLab cannot say yet whether a reply was edited: it waits, nobody replies
  again), delivery_unknown. The run's own notes (they start with its run id) say why a taken hand-over or a running run
  waits, as long as nothing was pushed after them: runnerd silent for `runnerd_wait_minutes`, runnerd retrying, 没能开始
  (labelled rejected too: its reason is that note's), KiroCrew not opening the session, queued, the hand-over send not
  known, sends that keep failing, the acceptance question held until the head pipeline is green (running, red,
  canceled / manual), a question nobody may answer. A parked run whose question the bridge already answered says so
  (brain-kit's `answered`: the run, or the bridge, has not gone on).
- **The MR's pipelines on a person's machine** are read in the same MR refresh, with the same token and at the same
  cadence (each handed-over run's MR at most every 60 s, from the background loop): one more call,
  `GET /projects/:id/merge_requests/:iid/pipelines?per_page=20`, the MR's 20 newest. Nothing else calls it: building
  the board, 需求现状 and the overview read only the cached copy, so a slow GitLab never holds a page up. Kept per
  pipeline: its number, status, commit, when it finished (GitLab's `finished_at`, else `updated_at` once it has
  finished: the MR's list gives no finish time) and its page (GitLab's `web_url` when it is on the repo's GitLab, else
  made from it); a row without a number or a full sha is dropped. A round its run did because one failed says so: a
  release request followed by a fix commit is 「CI 没过 #1234，代码又改了」 (the newest pipeline that failed on one of
  that run's commits inside the round, before the fix), and the next round, and 构建测试's next check, say
  「CI 没过 #1234 以后」 (that check's round starts at the failure, so it holds the fix commit). The lanes, a box's card,
  产出's round pills and its 「这一轮是因为 CI 没过 #1234」 banner (with 看流水线 ↗), the card of a runner round and 回放
  all say it in these words. When the pipelines are not read (no token, GitLab down, 403 or 404 because CI is off or
  the token may not read them, a repo without GitLab, team mode), the rounds say what they said before
  (「请批以后代码又改了」) and the board answers as before; a failure on the pipelines call alone keeps the last copy and
  the MR's notes still count. Once read, 「CI 流水线：在 GitLab 上，这里读不到」 is no longer among what the board cannot
  read.
- **Team mode, brain-kit 0d52e97**: the ledger's `outbox` (each message into a run's session: sending, interrupted,
  sent, unknown, dropped) and `notices` (what the bridge told an MR once, or counts: the acceptance question waiting
  for a green pipeline, runnerd silent, sends that keep failing, queued …) say why a run waits, on its row and under it
  on 运行机; `failed` is also a hand-over KiroCrew never took (after `send_give_up_minutes`). 运行机 lists the
  bridge.json and runnerd.json settings brain-kit reads (the new `send_give_up_minutes`, `runnerd_wait_minutes`,
  `acceptance_ci_wait_minutes`, `edit_check_rounds`, `edit_check_wait_minutes`; runnerd's `kit_dir`, `agent_spec`,
  `hook_path` …, each with its default when unset) and the runner's Lean hooks as brain-runnerd checks them
  (`cli.runner_hook_problems`: the hook adapter of the brain-kit installed there must be version 3, brain-run's three
  hooks must run it in runner mode). The app's KiroCrew adapter shows the bridge a slot's messages only, so the bridge
  sees a send that landed and never decides one did not: `send_unknown` and an outbox row `unknown` say 要人看一下
  (BRAINKIT_REQUESTS 44).
- **Team mode states**, brain-kit's: a run reserved while its contract is signed (`signing`) shows as handed over and
  waiting for the signer; a question being posted (`posting`, or 72b278e's `asking`: one bridge process posting it
  now) shows as parked, waiting for the bridge; a run whose latest question the signer refused, that is void or could
  not be asked (for the head the bridge last saw) shows as parked, waiting for a person, and is in 待办. The app runs
  `Bridge.poll`'s loop itself (each project, each MR with the hand-over label, `Bridge.process`) to keep each MR's
  result for the page; brain-kit's own `poll` returns nothing.
- **导入仓库** (`importer.py`, `installer.py`): every path from the page must be absolute, without `..` or control
  characters, a real directory, and with no symlink on the way that a user other than root owns (an agent with this OS
  user could have planted it; root's aliases such as macOS's `/tmp` are fine); from then on its realpath is used. A
  repo whose tree has a committed symlink where `brain install` writes (`.kiro`, `.brain`, `aidlc-docs`, `brain`,
  `AGENTS.md`, `.gitignore`) is not installed. Git runs without a shell and with a time limit on every call; over the
  network with `GIT_TERMINAL_PROMPT=0`, an askpass that answers nothing, `SSH_ASKPASS_REQUIRE=never`, ssh in batch mode
  (unless you set your own `core.sshCommand`) and in a session of its own, so nothing can wait for a password; ssh-agent
  and git's credential helpers still work. A clone URL with a user name or password in it is refused. brain-kit's
  `brain install` / `brain doctor` run as commands from the configured checkout. A failed install takes back its
  worktree and branch (in place: the files it wrote that were clean before, nothing else); the commit uses your identity
  and hooks. Clones and installs are background jobs (never on the event loop): the page polls their steps.
  `imports.json` keeps what was installed on a branch (branch, worktree, commit, pushed, MR), so a listed repo without
  kit.json in its checkout is pending when its install branch has one (or the default branch has it and the checkout
  was not pulled yet); an install in place is recorded only while a hook keeps it from being committed. The background
  loop looks at pending repos every 30 s and clears the records nothing waits for (`landing.sweep`).
- **你是谁** (`identity.py`): the name brain-kit goes by in a repo, by brain-kit's own rule (`cli._me`). With a GitLab
  token and kit.json's `gitlab.base`, it is GitLab's username for the token. The app asks GitLab's current user
  (`GET /api/v4/user`, with brain-kit's own client) in a background thread, never while a page waits, and keeps the
  answer per GitLab and token for 10 minutes (1 minute after GitLab did not answer). Without a token, the name is
  `$BRAIN_USER`, else `git config brain.user`. With a token, those two stand in only while GitLab has not said who the
  token is (slow, down, or the token refused), and the page says so. In a repo without GitLab, the name is the login
  name when the loaded brain-kit falls back to it (91e2fee and later; 48b6b97 does not, and the page asks for 「你的名字」
  there). With GitLab and none of these, brain-kit refuses ("who are you?") and the page asks for 「你的 GitLab 用户名」,
  prefilled with the login name. Saving runs `git config brain.user <name>` in that repo (lowercased, brain-kit's
  USER_RE, no shell). 设置 says, per repo, where the name came from. The same name decides which requirements are
  yours in 交付总览, 需求现状, 运行过程 and 待办, who 你 is in 按人 and in 开需求's reminder (on 全部, the first repo's
  name, the one 设置's 你是谁 shows), and which run branches the app pushes. When the app cannot tell who you are in
  a repo, no requirement with an owner there is yours, and one banner above the views asks for the name.
  brain-kit's "who are you?" from `brain start` is said in Chinese, with the input as its fix.
- **大需求拆成小需求** (`family.py`, brain-kit 0d52e97's `brain split`, phase 1: its `docs/安装和使用.md` §4.7). A big
  requirement goes through Lean's requirements and design as usual, the design with its unit split (unit-of-work.md);
  the person's DESIGN approval approves the split. 开需求's 「大需求：先拆成小需求」 tells the brain-dev session so
  (brain-kit's skill brain-split). Then, on the big one's 运行过程 or 需求现状, 拆成小需求 first shows `brain split
  --dry-run --json` (each unit, its paths, dependencies and contracts, brain-kit's warnings) and on 确定拆 runs `brain
  split --json`, in the big one's worktree on this machine and only for its owner; brain-kit pushes the big one's
  branch and files one small requirement per unit, 待认领. What each row is in its family comes from brain-kit's own
  listing, `brain intent list --all --json`, run in the background (at most every 60 s per repo that has split
  requirements, and right after an action here or 「从远端取最新」): it fetches the claims and the big one's branch from
  origin as brain-kit does. Until it has answered, a row says what this machine's refs say (the small one's claim.json
  as last fetched). A small one is 待认领 (`unclaimed`), claimed, in progress (its owner's run branch), 已合进大需求
  (`merged_into_parent`: its MR merged into the big one's branch; main comes with the big one) or closed. 认领 runs
  `brain claim <KEY>` (the first push wins; the loser is told who has it), 放弃认领 `brain claim --release <KEY>`, 开工
  `brain start --key <KEY> --worktree <work root>/<repo>/<key>-<unit>` (a worktree where KiroCrew lets agents work; it
  claims on the way) and opens its brain-dev session there, told the unit and runs/<KEY>/intent.md. 开工 is off while a
  dependency has not merged into the big one's branch, naming it and who has it; 「在 <DEP> 的分支上先做」 (`--on`) is
  offered only when brain-kit allows it (its only unmerged dependency, whose owner has a run branch). 同步 runs `brain
  sync` in the small one's worktree (conflicts listed with what to do). The app claims or starts only a small one
  `brain split` filed (brain-kit files a new claim for any other key), releases only the person's own, and never
  passes AIDLC_LEAN_TEAM on. Each command runs from the configured brain-kit as `bin/brain` with no shell, stdin closed,
  in a session of its own, git over the network without a prompt (as 导入仓库's), and a time limit that sends SIGINT
  first (so brain-kit gives back a claim it took, a half-made worktree), then kills; its output is shown folded, its
  words said in plain Chinese (`family.RULES`, problems.py), credentials in URLs hidden and agent text scrubbed. 待办
  lists a small one the person claimed whose dependencies have all merged (开工 now), and a big one of theirs whose
  small ones have all merged (review, `lean release`, RELEASE; or `brain handoff`). Without brain-kit's split (older
  than 0d52e97) the features are off and say why.
- **After the hand-over the runner's work is read from origin** (`repo.py`). A worktree here shows its own files, until
  origin's run branch has every commit of the local one and more (the runner, or a teammate, pushed): then the page
  reads origin's branch, so a run that went on to ask for its acceptance is shown at that ask, not frozen at
  「等桥核对交接」. The files the person changed in the worktree and has not committed are still read from there. The first
  look at a hand-over commit the MR has not been read at reads the MR at once, so a label that did not go on is said
  right after `brain handoff`, not a poll later.
- **A hand-over without its label** (person mode): run.json has the hand-over, the branch tip is the hand-over commit
  (one commit on top of `handoff.local_head`), and the MR at that tip carries none of the hand-over, running or
  rejected labels. Nothing was handed over (`brain handoff` exits 1 then, 72b278e): the row says so and 待办 lists it.
- **和队友同步** (`progress.py`, the org-level design 2.3 items 1 and 2). Teammates see a run on 交付总览 before its
  hand-over, because this machine pushes the person's own run branch when Lean records a checkpoint on it, and every
  app fetches by itself. A checkpoint is a ledger record of a stage done or skipped, an approval asked for, given or
  rejected, or a verify result (`CHECKPOINT_KINDS`). A release is a RELEASE approval and the release stage's record.
  An approval line Lean refused is not one, because the request stays pending. `push_due` is one table of reasons not
  to push, read in order. A branch goes when it is the person's own, checked out in a worktree here, in a repo with a
  remote and GitLab whose `auto_push` is on, not merged, closed or handed over, already on origin and ahead of it, and
  when the newest checkpoint committed at its tip is not one this machine pushed or tried. Only what is committed
  goes, as `git push origin <sha>:refs/heads/<branch>`. The push is never forced and runs the person's hooks, with no
  prompt and a time limit. It adds no label, opens no MR and makes no GitLab call, so the bridge (it takes only open
  MRs with the hand-over label) takes nothing. A run.json with a hand-over is left to `brain handoff`, also after
  `--no-push`, and the team runner pushes nothing. When git refuses a push, `pushes.json` (one writer, the loop) keeps
  git's words and the checkpoint, so the next try comes at the next checkpoint, and 待办 lists it. The loop runs
  every 15 s. It also fetches each repo with a remote every `fetch_every_min` minutes with `repo.fetch()`
  (「从远端取最新」's own). A repo has one git over the network at a time, its fetches and its pushes: the loop skips a
  repo still fetching (a push due then goes the next round), and the button waits for that fetch. A failure is logged
  once until a fetch works again. git's words from a push or a fetch keep no `user:token@` from a URL. In a repo where
  the app cannot tell the person's name (see 你是谁), it pushes nothing, and 设置 says so with the line to type.

## Security

The app runs as the same OS user as the agent sessions, and agents have a shell. So:

- **Signing is not the app's.** The bridge's private key stays with `signer/brain_signer.py`, a small separate
  process (another OS user in production, `brainbridge`), the other end of brain-kit's `sign.SpoolSigner`: the bridge
  writes `<spool>/requests/<req>.json`, the signer answers in `signed/` or `refused/`, and the SpoolSigner accepts a
  signature only if the signed voucher is exactly the one it asked for and verifies against `allowed_signers`. The
  signer decides with brain-kit's own rules (72b278e), run with its own identities, ledger, GitLab token and the kit
  config from the default branch: a hand-over must pass `Bridge.verify` (the owner's label newer than GitLab's MR
  version of the head, and the head the hand-over commit itself: one parent, run.json changed, as `brain handoff`
  commits it, and neither authored nor committed by a bot (kit `bot_emails` / `bot_users`); a commit pushed on top of
  it is not handed over, whatever the label times); an answer must pass `decisions.check_answer` bound to the question
  note the voucher names (`question_note`, the note id brain-kit's ledger recorded; a voucher without one is refused; a
  deleted question voids the decision): the question the bridge posts for park.json at that commit (ticking its review
  checklist is fine), by kit.json `bridge_user`, posted after the push, the D-n bound to nothing else; the first valid
  reply by an allowed person, never a bot or the bridge account, saying what its author wrote; approve / reject for a
  question without options; a batch review's item list whole; no conflict; the head unchanged). "What its author
  wrote" is check_answer's `edits`, made as brain-kit's own `collect` makes them: `DecisionLoop.reply_states` on the
  signer's ledger (each reply recorded at the signer's first sight with its text's hash) and GitLab GraphQL with the
  signer's own token (`GitLab.note_edits`: lastEditedBy, which an award emoji leaves empty and a text edit sets, as
  measured on a real GitLab on 2026-11-09). A reply someone else edited never counts and, from an allowed person,
  holds the round (a conflict: nothing signed); one its author fixed before the signer read it counts as it reads;
  one whose text changed after that never counts. When GraphQL does not answer, the signer answers nothing (the bridge
  asks again) and records nothing for that reply: fail closed, but not for good. brain-kit's own bridge does the same
  since 0d52e97 (QA round 3: the reply is `pending`, read again each round, and counts once GraphQL answers; the MR is
  told once after `edit_check_rounds` rounds), and a check_answer verdict naming pending replies is no refusal for the
  signer either; 48b6b97's bridge recorded such a reply as "cannot tell" for good. What the signer adds: it rebuilds
  each voucher from those checks and compares canonical bytes (brain-kit has no voucher builder, so a field brain-kit
  adds is refused until the signer learns it); it serves only its projects, checks
  `issued_at` and the contract's TTL, reserves a contract's run id in brain-kit's `signing` state before checking it
  (one open run per requirement; the same hand-over asked again is signed again), signs an answer only for a run whose
  contract it signed; and its own ledger keeps one answer per question and never binds a D-n to a second question or
  question note, and it refuses a question note that an allowed person's reply to its D-n comes before (a copy posted
  later, with or without the first one deleted, would drop that reply), because the bridge account's token is
  readable by the agents' OS user, so the bridge's notes on the MR can be deleted, edited or posted again. A request must be written as
  brain-kit's SpoolSigner writes it, as a small regular file (no symlink or FIFO). Nothing in the app's private
  brain-kit copy is patched. A signer that does not answer in time leaves the hand-over to the next round (its run id
  given back) and the answer open; the next round asks for that very voucher, so a late signature is used and nothing
  is signed twice. A refusal rejects the hand-over on the MR (or marks the answer `refused`) with the reasons, as
  brain-kit does. Team mode does not start when bridge.json names a `signing_key`, when its signer is not a spool signer, or
  (unless `dev_same_user`, a local trial shown as a warning) when this OS user can write the signer's `signed/` or
  `refused/`.
- **No listening sockets**: routes only under `/api/apps/brain` on the loopback gateway; every route but `/health`
  is the dashboard owner's only (an app token, this app's included, is refused). The family routes (split, claim,
  start, sync) check the repo id and key as every route does, take `on` only as a key among the small one's own
  dependencies, act only on a small one brain split filed (or the person's own big one, for the split), and answer
  brain-kit's output scrubbed, never a token. 选文件… (`POST /pick`) runs a fixed command: only `file` or `folder`
  comes from the page, never a word of a script. Outbound only: the loopback gateway,
  GitLab (`brainkit.rest`), git. The git that runs by itself is `git fetch` of each repo with a remote every few
  minutes, and a fast-forward `git push` of the person's own run branch at a Lean checkpoint (`progress.py`). Both use
  git's own credentials and a time limit. The push runs without a prompt, as 导入仓库's git does; the fetch is
  「从远端取最新」's own, with git's terminal prompt off. 推送并开 MR pushes and opens the MR only after the person confirmed the branch, the
  remote and the target on the page (the backend refuses a request whose three do not match what it sees now).
- **No secrets out**: the GitLab token is read like `brainkit.rest.read_token` (`$GITLAB_TOKEN`, else
  `~/.brain/gitlab-token`, refused when others can read it) and never logged, stored or returned. It goes only to
  GitLab: the MR calls (the MR, its notes, its pipelines), and 你是谁's `GET /api/v4/user`, whose answer the app keeps under a hash of the token. Agent-written text
  on its way to the page goes through Kiro Crew's scrubber; materials stay in the intake store (never in a repo). The
  变更说明 is read from commits only (an MR.md committed as a symlink is not followed): a planted link cannot bring a
  file of this machine into the text the person copies to GitLab. `GET /board/{repo}/{key}` only reads (the repo and
  key are checked as every route's are) and its agent-written text (commit subjects, event lines, a round's own note)
  goes through the same scrubber; 回放 runs in the page on the snapshot it has and changes nothing (with 产出 open it reads the same `/outputs/` routes, only for the rounds started by the replayed moment).
- **产出 reads only what the fold names.** Both `/outputs/` routes are the owner's only (`kit.guarded`: 401 without a
  login, 403 for an app token or someone else) and read the board's own fold, the last one `GET /board` made (folded
  again only when older than 10 s), never a fold of their own. The query is checked before anything is read: a path
  must be repo-relative (absolute, `..`, `.`, an empty part, a backslash or a control character is 400), a sha exactly
  40 hex (a trailing new line is 400 too), a pin exactly 64 hex. A `path` must be one of the requirement's files, a
  path its commits changed or one Lean recorded for its rounds: any other is 404, whatever commit holds it. A `ref` or
  `base` must be one of the run branch's commits or a first parent of one: any other is 404, never fetched. A file is
  read from a commit only (`mrdesc_view.tree_entry` with literal pathspecs, then `committed_file`: a regular blob of
  at most 4 MB, else 413; a folder, a symlink or a submodule is refused, on either side of a diff too). A diff is `git
  diff --no-ext-diff --no-textconv`, so an agent-written `.gitattributes` cannot run a diff driver or a textconv, and
  with `GIT_NO_REPLACE_OBJECTS=1` a replace ref cannot swap what a commit holds. The one read outside a commit is a
  local round's document waiting for approval: only at the stage table's document path, only for a sha256 a local
  round of that stage recorded, each part of the path opened with `O_NOFOLLOW` under the worktree's real path, and
  only when the bytes hash to that sha256 (else 409: 到会话里看). Lean's ledgers (`aidlc-docs/audit` and anything under it,
  wherever `aidlc-docs` is) and the pages Lean writes from them (`aidlc-state.md`, `audit.md`, at any depth under
  `aidlc-docs`) are never listed or read: they hold what people typed, a rejection's words among them. Emails (release.md's approval table
  has git emails) become brain-kit's pseudonyms (G-<6 hex>) before anything is sent, and the text goes through the
  scrubber; a text over 300 000 characters is cut at a line end first (a longer first line shows nothing), so the
  scrubber never gets part of a secret. Each refusal says why and what to do, in plain Chinese, and carries none of
  the file's content. `tests/test_outputs.py` and `tests/test_outputs_guard.py` prove each of these on real git
  repositories; `tests/test_typed_words_sweep.py` types two rejections in a real Lean worktree and finds their words
  in no read route's answer (交付总览, 需求现状, the board, 待办, 产出 with every file it lists).
- **产出's session tabs never show what a person typed.** `GET /outputs/{repo}/{key}/turns` is the owner's only too and
  reads the same fold. A round's turns are found by the sha256 Lean recorded of each message (HUMAN_TURN), matched to
  a user row of the run's session transcripts on this machine (KiroCrew lets an app read only the slots it opened; a
  transcript is read whole, 500 rows a page with `before`). A row a person typed (`meta.human`) becomes its length and
  time only, the owner's included. The app's own messages are shown as the code that sent them wrote them down
  (`SessionInfo.shown`: the kickoff and the review kickoff, ids only; a clarify answer with the person's note
  replaced); a bridge message by its kind (`sessions.TASK_KINDS`: the hand-over whole, an answer or a notice by its
  first line, since later lines carry a person's note or a CI log); anything else, and any message sent before
  `shown` existed, is hidden. Replies and calls are the agent's: a reply is cut at a line end within 20 000
  characters, emails become pseudonyms, and a call's detail is scrubbed before it is cut to 120 characters. A round
  done elsewhere is never fetched from there: the card says where to look. `tests/test_outputs_turns.py` runs a real
  Lean worktree through the service with a transcript of more than 500 rows and searches the JSON for the typed words.
- **The board's ticker never shows what a person typed either.** It rides on `GET /board` (owner only, no new route).
  The session manager keeps, per slot, its newest tool call and the last line of the agent's newest text
  (`sessions.tail`): tool and assistant rows only, never a user row, kept in memory and never written to its state
  file. The board sends it only for a run whose session is on this machine: the call's path relative to the session's
  project, scrubbed and cut to 120 characters; the line cut at a line end within 200 characters (a longer line is left
  out, not cut through) with its emails pseudonyms (`outputs.shown_text`), then scrubbed with the rest of the board.
  `tests/test_ticker.py` ends a transcript with a typed line and checks it is nowhere.
- The brain-kit checkout and the app directory run inside the gateway: in production they must not be writable by
  the agents' OS user. Team mode refuses to start unless bridge.json, runnerd.json, allowed_signers and the
  identities file are owned by another user and read-only to the app (`dev_same_user` skips this for a local trial).
- **What is a boundary and what is a guardrail.** On the runner the agents can do whatever the app's OS user can:
  rewrite the app's ledger, point a session at another slot, write signature requests. The app's own checks are
  guardrails. The boundaries are the signer (it trusts nothing it is sent), runnerd and CI verifying signatures with
  keys and configs the agents cannot change, the pre-receive hook (72b278e: bots may not change
  `runs/<KEY>/run.json` either; its config's `runs_dir` must be kit.json's) and GitLab's approvals. Give the app's GitLab
  account Reporter rights only (comment, label; no push, no merge).
- On a person's machine the `brain-dev` agent runs as that person and can read their GitLab token file anyway: the
  app's answer route adds no power it lacks, and it only answers clarify questions (never a Lean approval). The real
  gates stay where brain-kit puts them: the bridge's checks, the signer, CI and GitLab approvals.

## Install (a person's machine)

1. **brain-kit** checkout (`72b278e` or newer). In each project brain-kit is installed by the app's 导入仓库 (in place
   on the checkout's branch by default, or on a new branch: see above), or by hand: `brain install <repo> --prefix
   PAY`, then commit `.kiro/`, `.brain/`, `aidlc-docs/`, `brain/`, `AGENTS.md` (since 48b6b97 it also adds `runs/**`
   to `aidlc-docs/lean.json` `fingerprint_exclude`: review and commit that too, or `brain handoff` refuses and the
   Kiro Crew check lists the repo). On this machine: the GitLab token in `~/.brain/gitlab-token` (`chmod 600`), `git
   config brain.intake <brain-intake clone>` (see brain-kit's `docs/安装和使用.md`). Your name per repo (`git config
   brain.user`, only where brain-kit cannot tell) is asked for in the app.
2. **Trust the app yourself**: Kiro Crew settings → trusted apps → add `brain` (`agent.apps_trusted`). Kiro Crew
   refuses to enable an untrusted third-party app; the install script does not trust it for you.
3. **Install**: `scripts/dev-install.sh --consent-session-approval` (builds the UI with node 22+, installs or updates
   the app, enables it; the flag gives your consent to `permissions.sessionApproval`, which lets the app set the
   sessions it opens to trust; without it, click Enable in the dashboard and grant it there). Re-run after changes:
   it disables, updates and enables (backend hooks are re-imported on enable only).
4. **In the app → 设置**: the brain-kit checkout (`$BRAINKIT_HOME` or a usual place is found by itself) and the work
   root; the project repos through **导入仓库**. Then fix what the Kiro Crew check lists (brain-dev with Lean's hooks:
   the fix adds the missing ones to your spec and keeps the rest; engine `kas`; a new session is needed for a changed
   brain-dev spec, and a Kiro Crew restart may be needed for the engine).

## Install (the team runner)

1. OS users as in brain-kit: `brainbot` (Kiro Crew with this app, runnerd), `brainbridge` (the signer), group
   `brainspool`. `~/.kiro/agents/brain-run.json` for `brainbot` (brain-kit `remote/agents/brain-run.json`).
2. **The app's bridge.json** (brain-kit's format, `examples/bridge.json`): `projects`, `gitlab` (base, token for an
   account with Reporter rights: it comments and labels, it must not be able to merge or push), `identities`
   (readable by the app, writable only by `brainbridge`), `ledger`, `spool_dir` (runnerd's spool), `vouchers_dir`
   (an app-local copy; brain-intake is written by the signer only), `runner_runs_root`, `kirocrew.agent: brain-run`,
   `pseudonym_salt`, and the signer instead of a key:
   `"signer": {"type": "spool", "spool_dir": "/srv/brain-signer", "allowed_signers": "/etc/brain-kit/allowed_signers"}`
   (optional `timeout`, default 30 s, and `poll`; the signer's spool must not be runnerd's `spool_dir`). No
   `signing_key`: with one, team mode does not start. `publish_vouchers` is forced off (so brain-kit's
   `publish_timeout` does not matter here; the signer has its own). The token is also used on GitLab GraphQL (the edit
   signal, 72b278e): it needs the `read_api` or `api` scope.
3. **kit.json `bridge_user`** on the default branch of each project: the GitLab account of that token (not one of
   `bot_users`). brain-kit's bridge checks that its token is that account before it asks; the signer signs no answer
   without it (it is how a question by the bridge is told from a look-alike by a person).
4. **runnerd.json** (brain-kit's format) with the same `spool_dir`; `allowed_signers` = the bridge's public key.
5. **The signer**: `signer/signer.example.json` → `/etc/brain-kit/signer.json` (same `pseudonym_salt`; its own
   identities copy, GitLab token (it reads GitLab GraphQL too: `read_api` or `api` scope), ledger, the signing key,
   `publish_timeout` for its pushes to brain-intake; `bridge_users` is optional, a pin: kit.json's `bridge_user`
   must be one of them), `signer/brain-signer.service` as `brainbridge`, started before team mode is turned on.
   Spool permissions: `requests/` writable by `brainbot`, `signed/` and `refused/` by `brainbridge` only (team mode
   checks the last two). A run that will never finish is closed on the signer with `brain_signer.py --config … close
   RUN-…`. The signer's brain-intake clone (`intake_dir`, it runs `git pull` there), `/opt/brain-kit` and the signer's
   own folder must not be writable by `brainbot`: give runnerd its own intake clone. After a change of kit.json on main
   or of the identities, restart the signer and the app's bridge (brain-kit reads them once per process).
6. **In the app → 设置 → 团队模式** (这台机器是团队运行机): bridge.json and runnerd.json (the signer's spool and public key are in
   bridge.json); turn it on. Settings of an older version of the app (a separate signer spool and allowed_signers) are
   dropped: move them into bridge.json's `signer` block.

## Code map

| Path | What it is |
| --- | --- |
| `app.json` | The manifest: `brain`, page `/apps/brain`, `permissions.api` `/api/apps/brain` and `/api/chat` only, `sessionApproval` |
| `backend/core/service.py`, `handlers.py` | The service object (settings, repos, sessions, team mode, every view) and the routes |
| `backend/core/bk.py` | brain-kit, loaded from the configured checkout under a private name; guarded optional modules |
| `backend/core/stages.py` | The one stage table: Lean 0.3.1's 26 stages, 审查 and 回写 Brain, each with its AI-DLC number, name, phase, document and approvals; the phases; the stage graph's rules: node ids, the round machine (`DOC_MACHINE`), gates, node status, the bands |
| `backend/core/runs.py` | Who did each round: the ledger file each clone writes, hand-over boundaries, the runner's runs numbered, what each run does now, the hand-over relay |
| `backend/core/repo.py`, `lean_view.py` | Requirements found in a repo (claims, run branches, worktrees, main); Lean's stages and progress (read through `stages.py`), approvals, Session notes |
| `backend/core/deliveries.py`, `detail.py`, `todo.py`, `mrinfo.py` | 交付总览 (and its eval numbers, `cto_numbers`), 需求现状, 待办, the MR and its pipelines on a person's machine |
| `backend/core/runner_view.py` | 运行机 on a personal install: each repo's team runner as GitLab shows it (MRs at work, the questions waiting on the person, its last act), with the same GitLab client as the MR reads (`mrinfo.client`) |
| `backend/core/roster.py` | 交付总览's team view: what each person has open against the limit, and what is stuck (`STUCK_RULES`, one table, the worst kind first), in working hours |
| `backend/core/board.py` | 运行过程: the stage graph's nodes, runs and hand-overs, the events placed on it, commits, check wall and acceptance of one requirement, folded from the trace and the repo; a family's roll-up |
| `backend/core/turns.py` | 产出's session tabs: a round's turns (Lean's HUMAN_TURN sha256 joined to the session transcripts here), each turn's message by default deny, its reply and calls; the card for a round done elsewhere |
| `backend/core/outputs.py` | 产出: a round's files from the board's fold (declared first, at the commit whose blob hashes to Lean's sha256; the version to compare with), one file's text or change, the pinned worktree read, the path and ref checks |
| `backend/core/family.py` | 大需求拆成小需求: the families (`brain intent list --all --json` in the background, else the claims here), each requirement's family state and what it may do, `brain split` / `claim` / `start --key [--on]` / `sync` through `bin/brain` (no shell, bounded time) with their refusals in Chinese |
| `backend/core/problems.py` | Every check row in plain Chinese: brain-kit's crewcheck, doctor, lint and refusals mapped to a sentence, 做什么 and a fix |
| `backend/core/mrdesc_view.py` | The 变更说明 at a commit (MR.md, else brain-kit's mrdesc), its first line, the cache and its background maker |
| `backend/core/intake.py`, `crewcheck.py`, `brainview.py` | 开需求, the Kiro Crew checks and fixes, Brain and reflect |
| `backend/core/picker.py` | 开需求's 选文件… / 选文件夹…: this machine's own file dialog from a fixed command, one at a time |
| `backend/core/importer.py`, `installer.py` | 导入仓库: path checks, 检查, 克隆, where an install goes (`choices`), the install records and background jobs; 安装到新分支 (worktree, `brain install`, kit.json, `brain doctor`, commit), 推送并开 MR |
| `backend/core/inplace.py`, `landing.py` | 直接装 (in the checkout, on its branch, only the install's paths committed; 推送 when asked); a branch install's 合并到 main, 拉取 and the background sweep of stale records |
| `backend/core/identity.py` | 你是谁: the name brain-kit goes by in a repo, and `git config brain.user` from 「你的 GitLab 用户名」 |
| `backend/core/progress.py` | 和队友同步: the person's own run branches pushed at Lean's checkpoints (`push_due`'s table of reasons, the marks in `pushes.json`), and every repo with a remote fetched every few minutes |
| `backend/core/team/` | Team mode: the hosted bridge (`bridgehost.py`: brain-kit's Bridge and SpoolSigner, its poll loop with each MR's result, the spool view for the page), the KiroCrew adapter; 一键建运行机 (`setup.py`: the same-user team runner, for one project from a one-time token that makes project tokens, or for several from the personal tokens of two GitLab users of the runner's own, as one table of steps that each check before they make anything, the steps about the projects done for each with its own row, `POST`/`GET /team/setup`), and the signer as the app's child process for that runner (`signerproc.py`: started and stopped with team mode, started again by a round of it when its process is gone and whenever what it read at its start has changed, a pid only ever signalled when its command line is that signer); the work sources (`sources.py`: the kinds a project's kit.json `work_sources` switches on, default `mr-handoff` alone, and the one claim record each makes, so work is claimed once); MR 巡检 (`patrol.py`, work source `mr-patrol`: every open MR's failed pipeline (GitLab's jobs, and the checks external CI posted into it as commit statuses, with their Jenkins logs: `jenkins.py`, bridge.json's optional `jenkins`), open review threads, people's plain comments and conflict, given to one brain-patrol session per MR in a clone of its own (deleted once the MR is merged or closed), two at a time counting 测试环境报错's, a message KiroCrew did not answer never sent again, its state machine in `patrol.sqlite` beside bridge.json; `patrol_reply.py`, the command a session replies on the MR or Issue with, and opens a fix's MR with, as the bridge's account); 测试环境报错 (`staging.py`, work source `staging-errors`: each round reads on in the staging log of every project whose bridge.json entry names it (`stagelog.py`: the log and VERSION file readers, the errors in plain text or JSON lines, a signature each), opens one GitLab Issue per signature with the deployed commit and the MR that merged it, comments when its count jumps, gives the fix to a brain-patrol session that opens a branch, a fix MR with `Closes #`, two at a time counting MR 巡检's, stops after two failed attempts with one comment, opens the Issue again when the error comes back on another deploy; its rows and the log's cursor in `staging.sqlite`) |
| `backend/core/gateway.py`, `sessions.py`, `httpkit.py`, `store.py` | Kept from the sample (see above) |
| `signer/` | The separate signer (brain-kit's rules, its own ledger; the service half of brain-kit's spool protocol), its example config and systemd unit |
| `ui/src/` | The page: `main.tsx`, `AppBar.tsx`, `nav.ts`, `views/*`, `board/*` (运行过程), `cto/*` (交付总览's charts and kanban), the contract `types.ts`; `ui/preview/` on `world.json` |
| `scripts/runner_setup.py` | 一键建运行机 from a terminal (the steps of `team/setup.py`, one line each, and a line per project under a step about several; `--mode bot-account` with `--project` once for each), for a live check against a real GitLab outside KiroCrew |
| `scripts/preview_world.py`, `preview_docs.py`, `preview_session.py` | Writes `ui/preview/world.json` and the board and overview fixtures with the backend's own code; PAY-9's and PAY-2's commits are real ones in a scratch repository (what their runs wrote: `preview_docs.py`), and PAY-9's local session is a KiroCrew transcript (`preview_session.py`), so the world's 产出 is `outputs.py`'s and `turns.py`'s own answers |
| `tests/` | Backend tests: real brain-kit projects in temp dirs, the fake Kiro Crew gateway, a fake GitLab with GraphQL's edit marks (`teamfix.py`), the real signer behind brain-kit's own Bridge and SpoolSigner (`test_signer.py`: the attacks it refuses, the edit signal) |

## Host APIs relied on (Kiro Crew 0.11.2)

`POST /api/apps/brain/token` (app secret → 5-minute token); `GET/POST /api/chat/folders`, `PATCH
/api/chat/folders/<id>` (`hidden`, `parent_id`: an app nests, moves and hides only its own folders, never one holding
someone else's; the sidebar draws 「Brain 需求」 › repo › requirement); `GET/POST /api/chat/slots` (`agent`, `agent_kind: template`, `model`, `memory_mode`,
`folder_id`), `GET /api/chat/slots/<key>?limit=&before=` (at most 500 rows, `has_more`, `next_before`), `POST /api/chat/slots/<key>/project`, `POST
/api/chat/slots/<key>/stop`, `DELETE /api/chat/slots/<key>`; `POST /api/chat/mode` (`trust` for one slot,
`permissions.sessionApproval`); `POST /api/chat?ws=1` (`meta.sendId`); assistant rows' `meta.turn_stats.credits`;
the backend hooks (`routes`, `on_startup`, `on_shutdown`), `ctx.name / logger / data_dir / scrub`, KiroCrew's owner
predicate; the UI host modules `@kirocrew/app-sdk` (`useAppApi`, `useNavigate`, `useNavBadge`) and its import map.

## Test

```bash
python3 -m pytest -q                     # backend (Python 3.9+, pytest, git, ssh-keygen, the brain-kit checkout)
(cd ui && npm ci && npm run check)       # UI: type check, component tests, build
(cd ui && npm run preview:build)         # ui/preview-dist/index.html: the page on the fixtures, no Kiro Crew
(cd ui && npm run fit)                   # after preview:build: every view at 1280, 1050, 860 wide (KiroCrew's sidebar
                                         # taken off) in headless Chrome; fails when something is cut off
python3 scripts/preview_world.py         # after changing board.py, detail.py or deliveries.py: the preview's world
```

`BRAIN_TEST_BRAINKIT` points the backend tests at a brain-kit checkout (default `~/warren_ws/brain-kit`; a clean clone
of a commit needs its `vendor/aidlc-lean` too: `git submodule update --init`, or with the URL set to a local copy of
it). The suite passes against clean clones of 0d52e97 and 48b6b97: what a version has not got is skipped (the split's
tests, `tests/test_family.py`: a bare origin, alice's checkout in the app, bob's clone, brain-kit's own `brain split`,
`claim`, `start --key`, `sync`; the version-3 adapter; brain-kit's pending replies), and the tests that depend on a
version ask the checkout (the adapter version brain-kit needs, the login-name rule). Every repo the tests make is a
throwaway one in a temp dir, and git hooks are off in them (`core.hooksPath` at an empty folder, per repo and for
every git the tests start, the app's clones too: a machine-wide secret scanner costs about 0.5 s a commit);
`BRAIN_TEST_KEEP_HOOKS=1` keeps the hooks. The import tests use a bare repo as the remote, a fake ssh for the failures
and a fake GitLab for the MR: no network. The 和队友同步 tests (`tests/test_progress.py`) use a bare origin, alice's
checkout in her app and bob's clone in his, Lean's records from the real AI-DLC Lean, and a GitLab transport that
records every call. The fake GitLab (`tests/teamfix.py`) serves commits with their `parent_ids`,
authors and committers from a bare repo, and GraphQL's edit marks as measured on a real GitLab (lastEditedBy for a
text edit only); every hand-over in the tests is a real `brain handoff` commit, and the runner's commits are the bot
account's. The 你是谁 tests run against a brain-kit without brain-kit 91e2fee's login-name rule (48b6b97: no name
without GitLab) and one with it: the checkout is whichever it is, the other one a copy of it with the rule taken out
or applied (`tests/test_identity.py`).

## Not verified on a live Kiro Crew yet

Opening a `brain-dev` slot with `agent_kind: template` and a worktree under `~/workplace` as its project; that the
template's hooks run in such a slot (also after the hook fix added them to a Kiro-upgraded spec); `trust` for it;
credits in `turn_stats`; the bridge's runner sessions; the `kirocrew config set` fix while the gateway runs; the page in
the dashboard; the signer against a real GitLab (MR versions, note ids, GraphQL's lastEditedBy through its own
token) and as another OS user behind the spool; the 变更说明 of a real run (brain-kit 48b6b97's MR.md in the hand-over
commit) on the page; 导入仓库 against a real GitLab (a clone over SSH and its failure texts, the push, the MR opened
with a real token, a protected default branch refusing 推送 after an install in place, 拉取 from it) and its long jobs
behind the gateway; 大需求拆成小需求 against a real GitLab (claims pushed to it, two people racing for one, the small
one's MR into the big one's branch, brain-kit's pre-receive rules on `claim/*`) and its background listing over SSH;
KiroCrew's slot read for brain-kit's resend (`running`, `queue`); 和队友同步 against a real GitLab (a push over SSH at a
checkpoint, a protected run branch, whether a pipeline runs on every push, the fetch's failure texts); MR 巡检 against a
real GitLab and KiroCrew (a brain-patrol slot booting its template, the discussions API's resolvable and position fields
as the fake has them, how soon `has_conflicts` follows a push, `patrol_reply.py` through a GitLab behind single sign-on); MR 巡检's Jenkins read against a real Jenkins (a build's
consoleText as the read-only user at bridge.json's `jenkins.base`; a GitLab that ignores the statuses API's
`pipeline_id`, each status's own `pipeline_id` then telling them apart).
