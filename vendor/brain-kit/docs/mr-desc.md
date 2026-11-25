# MR 变更说明（`brain mr-desc`）

2026-11-09 · 代码：`brainkit/mrdesc.py`、`brainkit/commands/mrdesc_cmd.py`；接进流程：`brainkit/cli.py`（install、doctor、handoff）、`brainkit/bridge/decisions.py`（watch） · 测试：`tests/test_mrdesc.py`、`tests/test_mrdesc_flow.py`

每个 MR 都要写明白改了什么、有什么效果，产品、Builder、别的组不读整个 diff 也能看懂。这份说明由 `brain mr-desc` 从 run 的记录生成，agent 不手写。

```bash
brain mr-desc [KEY]            # 打出 Markdown（KEY 默认取当前 run 分支）
brain mr-desc --write          # 同时写 runs/<KEY>/MR.md（同样的记录，生成同样的内容；随时可以重新生成）
brain mr-desc KEY --ref origin/main   # 合并后，从 main 上读记录
brain mr-desc KEY --json       # 结构化数据（给 KiroCrew app 用）
```

其他参数：`--base REF`（换 diff 基线）、`--credits FILE`（KiroCrew 给的 credits，和 `brain trace` 一样）、`--vouchers DIR` 和 `--allowed-signers F`（核对设计批准的签名，默认取 `<intake>/vouchers` 和 `$BRAIN_ALLOWED_SIGNERS`）。

## 说明里有什么，从哪来

| 段 | 内容 | 来源 |
|---|---|---|
| 一句话 | Goal 的第一句、Size、run 在哪（Lean 阶段、本地还是运行机、在等什么）：交接过的写「已交给团队运行机」，运行机自己开工的（比如从 issue 接活）写「从开工起就在团队运行机上」，都不是才写「在本地开发（还没交接）」 | requirements.md；Lean audit 的阶段事件；run.json 的 `handoff` 和 `origin`（`remote`：运行机开的工）；park.json；没答的审批请求 |
| 做了什么 / 没做什么 | Goal、Not doing、每个单元的计划做完几步 | requirements.md；代码生成计划的勾选框 |
| 验收标准和证据 | AC 表：要求、测试、结果（通过/没过 · 日期 · 代码指纹；代码在 verify 之后又改了就写出来） | requirements.md 的 AC 表；计划的 Tests 表；Lean audit 里最近一次 VERIFY；Lean 自己算的当前代码指纹 |
| 改了什么 | 和默认分支分叉点的 diff，按顶层目录计数（合并以后用 `--ref origin/main` 读：那次合并带进 main 的改动，合并提交、快进、变基、压缩合并都认，和 `brain trace` 找合并的办法一样；小需求合进大需求的分支以后同理）；代码文件；改到的 risk path，以及有没有设计批准覆盖 | `git diff --no-renames -z --numstat`（不含 MR.md 本身）；main 上的 `gates/risk-paths`；`ci.check_route`（有签名凭证时），否则 Lean 记下的 DESIGN 批准（只在本地会话批的，写明 CI 不认：CI 只认桥签过的） |
| 关键决定 | ID、决定、为什么、谁定的（固定词：agent / brain 条目号 / check / P-xxxx V-xxxx / owner）、引用的 Brain 条目；agent 自己定的标出来 | decisions.md |
| 假设和待确认 | A-n、release.md 的 Open items、park.json 里在等的问题 | requirements.md、release.md、park.json |
| 审批 | Lean 记下的每次审批（类型、哈希、时间、声称的渠道），还缺哪些 | Lean audit |
| 评审 | 发现数（按严重度）、修了几条、高严重度的几条、结论行。修没修看 Status 列，没有就看 Fix 列开头：`fixed`、`已修` 等算修了，空的、`not fixed`、`未修` 等算没修，只写了修法的算没写修没修 | review.md |
| Brain | 用到的条目；过时的、不存在的条目号；等 `brain reflect` 的提案和缺口 | Lean 记录；`brain trace`（usage）；brain-proposal / brain-gap 块 |
| 怎么审 | 2–4 条具体的先看哪里（risk path、verify 过期、agent 定的边界、假设、没测试的 AC、没修或没写修没修的高严重度发现） | 从上面的数据推出来 |
| 花费 | credits | `brain trace`（KiroCrew app 传进来时才有） |
| 还没有 | 缺的记录，一条一行（"还没跑 verify"） | — |

规则：
- 每句都来自记录。记录没有就说没有，不猜。
- 人只出现 GitLab 用户名和记录里已有的化名。邮箱换成化名，家目录换成 `~`（`trace.scrub`）。decisions.md 里认不出的 Who 不显示（可能是真名），只写"没按约定写"。
- 记录里的文字压成一行，`@`、`<`、图片都失效：不会 @ 人，不会用 HTML 注释藏内容，不会触发 GitLab 快捷命令，不会加载图片；像审批或回答的行（`approve …`、`answer Q-n …`、旧的 `answer D-n …`）前面加 `> `，Lean 和桥都不会当真。
- 图片不只是 `![`。GitLab 读 Markdown 前先删掉 CR，再把转义（`!\[`）和字符实体（`&#33;`、`&#91;`、`&lt;`）读成字符本身，读出来是 `![…](…)` 的文字也显示成图片。所以 `inert()` 先删 CR，再把 `![`、后面跟反斜杠的 `!`、开始一个实体的 `&` 都换成全角的样子（`mrdesc._defang`，`_one()` 用的也是它）。
- 只有一行图片例外：「验收标准和证据」最后那行截图，是 `render()` 自己从证据拼出来的，验收的产品在 GitLab 上直接看到。记录里的文字写成同样的样子，照旧失效。桥贴 MR.md 时不信这一行，逐张核对是这次 run 封好的证据。放过什么、为什么，见 `docs/proof.md` 的「MR 说明里怎么看」。
- 不超过约 120 行，长表折叠成"另有 N 条，见 …"。
- MR.md 不算在它自己描述的改动里，所以写入、提交、再生成，内容不变。
- 拆出来的小需求（`brain split`）：decisions.md 和 requirements.md 是整个 intent 共用的。小需求的说明只列它自己的提交写的那些行（git blame，和 `brain trace` 一样）：决定、假设、提案和缺口；「用到的条目」只算它这个单元的页面（和 `brain usage` 一样）。大需求和兄弟需求写的不算它的。

## 快速需求（run.json 的 `mode: fast`）

快速需求没有 Lean intent，说明换一种写法（`mrdesc._build_fast`、`_render_fast`）。数据还是 `schema: 1`，Lean 才有的部分是空的，另有 `"flow": "fast"` 和 `commits`。

- 开头：快速模式，没走 Lean 的需求、设计和审批，改动由提交前的 verify、CI 和审 MR 的人把关。
- 改了什么：和完整流程一样。改到风险目录时写明快速需求不能合并它，要转完整流程（`brain full`）。
- 提交：这个需求自己的提交（不含合并提交），最多 20 条。
- 证据：这个分支最近一次 `lean verify`（VERIFY 记录不带 intent，按 `data.branch` 认，`flows.belongs`）；CI 看 MR 的流水线。
- 关键决定、Brain：读 `runs/<KEY>/decisions.md`（`flows.decisions_path`；`brain reflect` 也从这里读）。
- 怎么审、还没有：和完整流程一样；只会缺「还没跑 verify」和「还没有提交的改动」。

`brain mr` 每次推送都用它设 MR 的描述，不写 `runs/<KEY>/MR.md`。`brain mr-desc` 照样能打出来。

## 前提：`runs/` 不能算进 Lean 的代码指纹

Lean 把 `fingerprint_exclude`（默认只有 `aidlc-docs/**`、`.kiro/**`）以外的文件都当代码。`runs/<KEY>/MR.md` 和 `park.json` 默认都算。verify 之后写它们，Lean 就认为代码改了：提交被拦，`lean release` 要重来。

已实测（`tests/test_mrdesc_flow.py` 的 `ReleaseApprovalAfterPark`，真的 Lean）：默认配置下，`lean release` 之后写 park.json，桥送回的 `approve RELEASE` 会被 Lean 拒掉（APPROVAL_REFUSED，check `release`）；加上 `runs/**` 以后同样的流程能记下（APPROVED）。所以 `aidlc-docs/lean.json` 要有：

```json
"fingerprint_exclude": ["aidlc-docs/**", ".kiro/**", "runs/**"]
```

- `brain install` 会自动并进去：缺什么加什么（`runs/**`；自定义的列表漏了 Lean 的默认项也补上），不删、不改顺序，其它字段原样。它会说加了什么，并提醒：这个文件归人管（AI 改不了），看过再提交。
- `brain doctor`：没排除 `runs/` 时报 FAIL，并给出修法。
- `brain handoff`：没排除时不交接（退出码 2），说明原因和修法。
- `brain mr-desc --write`：没排除时拒绝写（退出码 1），只打印照常。
- 改了 `fingerprint_exclude` 以后 Lean 的代码指纹会变，之前的 `lean verify` 要重跑。

## 接进流程的地方（已接好）

1. **`brain install` / `brain doctor`**：见上一节（`lean.merge_fingerprint_exclude`、`mrdesc.write_problem`）。
2. **`brain handoff`**：提交交接（写 run.json 的 `handoff`）以后、推送以前，用 `mrdesc.build` / `render` 生成 `runs/<KEY>/MR.md`；内容变了就 `git commit --amend` 进同一个交接提交。交接提交仍然只有一个父提交、仍然改了 run.json，桥的核对照常通过。推送以后用主人的 token 设 MR 描述：新 MR 走 `create_mr(..., description=...)`（GitLab 不收这段描述就不带描述开 MR），已有的 MR 走 `rest.GitLab.edit_mr`（`PUT /projects/:id/merge_requests/:iid`）。生成失败或设不上描述只警告，交接照常。
3. **桥**（`bridge/decisions.py` 的 `watch`）：桥不改 MR 描述（描述是主人的 `brain handoff` 设的），所以发评论。验收问题（park.json 的 `kind` 是 `acceptance`，或 `lean_request` 是 `RELEASE …`）贴出之前，用 `gl.file_raw` 读 MR 最新提交上的 `runs/<KEY>/MR.md`，过 `mrdesc.inert()`（MR.md 是机器人提交的：@ 提及、HTML、图片、GitLab 快捷命令、像审批或回答的行，比如 `approve RELEASE …`、`answer Q-3 approve`，都失效；只放过第一行单独成段的截图行，而且每张都要是这次 run 在 MR 最新提交上封好、结果是 `ok` 的证据，Lean 记录的哈希链也没断（`mrdesc.sealed_evidence`，也用 `gl.file_raw` 读；读不懂的记录不算，这行照样失效，评论照常发；这是护栏，挡不住能推送分支、手写 Lean 记录的人），项目按 main 上 kit.json 的 `gitlab.base`、`gitlab.project` 认，见 `docs/proof.md` 的「MR 说明里怎么看」），单独发一条评论，第一行是"**变更说明**（`runs/<KEY>/MR.md`，提交 <sha12>：…）"。它不进问题评论：问题评论的文字是绑定的，`check_answer` 逐字比对。以后每次推送，MR.md 的哈希变了，桥就改自己那条评论（`rest.GitLab.edit_note`；评论被删了就重发一条）。评论 id 和哈希记在桥的 ledger 表 `mr_desc_notes` 里（旧的 ledger 打开时自动建表）。读不到 MR.md 时发一行"run 没有写 runs/<KEY>/MR.md"，以后写了就把这行改成内容。超过 6 万字只贴前面一段。这里出的任何错只记日志、下一轮再试，问题和回答照常。
4. **KiroCrew app 的需求详情**：调 `brain mr-desc KEY --json --credits <app 的 credits 文件>`，或者直接读分支上的 `runs/<KEY>/MR.md`，渲染 Markdown。数据格式就是 `mrdesc.build()` 的输出（`schema: 1`）。
