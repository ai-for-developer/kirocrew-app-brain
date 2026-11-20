# 证据：审查看证据（`.kiro/brain/proof.py`、`brainkit/proof.py`）

2026-11-18 · 代码：`kit/.kiro/brain/proof.py`（在 `lean verify` 里拍、记、封）、`brainkit/proof.py`（读：算不算、是不是这份代码的、缺什么） · 测试：`tests/test_proof_capture.py`、`tests/test_proof_plan.py`、`tests/test_proof_shots.py`、`tests/test_proof_bind.py`

审查的人看证据验收：每条看得见的验收标准有一张程序运行时的截图，还有整份测试输出。证据由 `lean verify` 自己拍，每次 verify 都拍。所以证据总属于某一次 verify，也属于那次 verify 时的代码。agent 不挑怎么拍，不起文件名，不写指纹，也不判断证据新不新。

## 接上（人做一次）

1. 先升级 kit（`brain install --upgrade`，合并它开的 MR）。分支上没有 `.kiro/brain/proof.py` 时，下面两行会让每次 verify 都失败。
2. 在 `aidlc-docs/lean.json` 的 `checks` 里，把现在的测试命令包一层，再加一行截图（`brain doctor` 会给出这两行）：

```json
"checks": ["python3 .kiro/brain/proof.py run tests -- ./gradlew test",
           "python3 .kiro/brain/proof.py shots"]
```

- `run`：检查的结果还是那条命令的，退出码一样，PASS 和 FAIL 也一样。命令的整份输出留下（stderr 并进来；太长时留最后 2 MB），先用 Lean 自己的 `mask()` 遮掉密码和 token。命令写进 `$BRAIN_PROOF_DIR` 的文件也留下（最多 50 个，每个不超过 4 MB）：项目自己的驱动（Playwright 之类）的截图这样也算证据，文件名用 `AC-3-` 开头就算 AC-3 的。
- `shots`：按证据计划拍截图（见下面）。拍不成不会让 verify 失败；写成 `shots --strict`，有一行不是 ok 就让 verify 失败，Lean 自己的关卡就会拦提交和发布。
- 这个文件只有人能改（Lean 不让 agent 改），所以 agent 跳不过、也改不了 verify 拍什么。

3. 在 `.brain/kit.json` 里写这台机器上怎么启动程序：

```json
"proof": {"url": "http://127.0.0.1:5173", "launch": "npm run dev -- --host 127.0.0.1 --port 5173 --strictPort"}
```

- `url`：只能是本机（127.0.0.1、localhost、[::1]）。
- `launch`：启动这份代码的程序，要先构建就写在里面。写了 0.0.0.0 或 `::` 的不用。要登录的程序，用只在开发环境有效的绕过（一个专门起名的环境变量），不要把密码写进计划。
- 可选：`ready`（等它答应的路径，默认 `/`，最多等 60 秒）、`size`（窗口，默认 `[1280, 800]`）、`ui_paths`（哪些路径算界面：改了它们却没有这份代码的截图，MR 说明会说）。
- kit.json 只有人能改，运行机上也不变。

## 证据计划（agent 写）

每个单元一份 `aidlc-docs/<intent>/construction/<单元>/proof-plan.md`（intent 没有 `unit-of-work.md` 时单元是 `app`；拆出来的小需求只写自己单元的）。里面一张表，一行是审查的人要看到的一个状态：

```markdown
| ID | AC | 看什么 | 步骤 |
|---|---|---|---|
| P-1 | AC-1 | 已付款订单有「退款」按钮 | open /orders/123 · wait 退款 |
| P-2 | AC-2 | 点退款以后状态是「已退款」 | open /orders/123 · click 退款 · wait 已退款 |
```

- `ID`：P-1、P-2……，不要改号。`AC`：这一行证明的 AC（`AC-2`、`BR-1`），没有就写 `—`。`看什么`：一句话，印在截图旁边。
- `步骤`用 ` · ` 隔开，只有四个词：`open <路径>`（程序里的路径，以 `/` 开头，不能写整个网址）、`click <字>`、`type <输入框> = <内容>`、`wait <字>`。第一步是 `open`。
- 步骤只能浏览程序，不能跑命令，也不写密码。
- 读不懂的行不会悄悄丢掉：读者会说「proof-plan.md 第 7 行：步骤看不懂（clik 退款）」。

## 截图怎么拍（`shots`）

每次 verify，`shots` 这样拍：

1. 读这个 clone 要拍的计划：拆出来的小需求只拍自己单元的。一次最多 20 行、300 秒。
2. 停掉上次被打断时留下的程序（只认它自己记下的那个进程）。端口上还有别的程序在听，就不拍：那可能是旧代码。
3. 用 `launch` 启动程序（它自己的进程组），等 `ready` 答应，再查它只在本机监听（用 `lsof`，没有就用 `ss`）。监听在所有网卡上，或者查不了，就停掉程序，不拍。
4. 开无界面的 Chrome，用管道连（不开端口）。每行从 `open` 开始走步骤，走完或者走不下去就截一张图，所以截图总是当时页面的样子。
5. 停掉 Chrome 和程序，写下这套证据。

一行走不通时，截图照样留下，MR 说明写「没看到」。没有 Chrome、程序没起来、端口被占时，每行都记成「没拍成」，写明原因。程序和 Chrome 只在 verify 的这几秒里运行，只在本机。

## 自己先看一眼（`look`）

```bash
python3 .kiro/brain/proof.py look "open /orders/123 · click 退款 · wait 已退款"
```

和 verify 用同一套启动和浏览器。它截一张图放进仓库外面的临时文件夹，打出页面停在哪、每一步怎么样、页面上的字。它不是证据，也不往仓库里写东西（`--out` 不能指到仓库里：Lean 会把多出来的文件算成代码，证据就成了旧代码的）。`--no-launch`：用你自己已经在 proof.url 启动的程序。

`python3 .kiro/brain/proof.py doctor` 只读不写：这台机器的 Python 是不是按 UTF-8 读命令输出、Chrome 在不在、kit.json `proof` 能不能用、有没有查监听地址的工具。

## 一套证据

一次 verify 里一条证据检查，留下一个文件夹：

```
aidlc-docs/<intent>/construction/build-and-test/proof/<clone id>/<名字>/
  output.txt      这条检查打印的全部内容：看到的东西，最后一行是 brain-proof: {清单}
  <文件>          清单列出的文件，每个都记了 sha256
```

- 放在 `aidlc-docs/` 下面：Lean 把它当记录，不算代码。写证据不改代码指纹，只提交记录也不用重新 verify。
- 按 clone 分文件夹：本机、运行机、拆出来的小需求各写各的，读的时候再合起来。
- 先写进 `<名字>.new/`，`output.txt` 最后写，再整个换进去。被打断留下的 `.new`、`.old` 下次会清掉，读的人也不看它们。

清单记下：格式版本 `v`、名字 `set`、种类 `kind`（`run` 或 `shots`）、`intent`、`clone`、退出码 `exit`、代码 `code`、每个文件 `items`（`file`、`sha256`、`size`、`result`、`note` 等）、读到的计划 `plans`、浏览器 `env`、拍的时候遇到的问题 `problems`。清单里没有时间，也没有 Lean 的指纹：封它的那条 VERIFY 记录里有。

## 算不算，是不是这份代码的

- **算不算（封）。** Lean 的 VERIFY 记录会存每条检查全部输出的 sha256。证据检查打印的正好是 `output.txt` 的字节，所以 `sha256(output.txt)` 就等于那个值。找得到这条 VERIFY，这套证据才算；它是谁拍的、什么时候拍的，都以这条记录为准。手工跑的、拍完又改过的、被超时打断的，都找不到，所以不算。清单里每个文件还要和记下的 sha256 对得上。
- **是不是这份代码的。** 清单的 `code` 是检查开始时的代码：HEAD 的提交，加上每个没提交的代码文件的 blob（和 `git add` 存的一样；符号链接按链接文字算）。它在 `output.txt` 里，所以也被封住了。拿它和要审的代码比（某个提交，或者工作区）：两边不一样的代码文件，内容都一样（或者两边都没有），证据就是这份代码的。
  - 记录（`aidlc-docs/**`、`.kiro/**`、`runs/**` 这类 `fingerprint_exclude`）不算代码。只提交记录，证据不会变旧。
  - 检查开始和结束时代码不一样，清单会写「拍的过程中代码变了」，这套证据里一项都不算通过。
  - 拍的时候的提交不在这个仓库里（变基丢了，或者还没取回来），就「核对不了」，不会说成是这份代码的。
- **verify 那一行不变。** MR 说明里「最近一次 verify」那行还按 Lean 的代码指纹判断。

## 缺什么，怎么说

一种情况一个名字，一句话。MR 说明、app 和以后的门禁用的都是这些（`brainkit.proof.GapKind`、`TEXT`）。

| kind | 什么时候 | 原话（%s 换成具体的） |
|---|---|---|
| `no_proof_check` | lean.json 没有证据检查，最近一次 verify 也没跑 | 这个项目的 lean verify 不留证据：aidlc-docs/lean.json 的 checks 里没有 .kiro/brain/proof.py（`brain doctor` 给出要加的行） |
| `no_verify` | lean.json 有证据检查，最近一次 verify 还没跑它（或者还没 verify 过） | aidlc-docs/lean.json 有证据检查，要等下一次 lean verify |
| `lost` | 最近一次 verify 跑了证据检查，却没有一套证据对得上 | 最近一次 verify 的证据检查（%s，exit %s）没留下证据：被打断了，或者它写的文件没提交 |
| `void` | 一套证据找不到封它的 verify，或者一个文件拍完改过 | %s 不是 lean verify 留下的样子（拍完以后改过、手工跑的，或者 verify 时编码不是 UTF-8），不算证据：重跑 lean verify |
| `newer_format` | 新版 brain-kit 写的证据 | %s 是新版 brain-kit 写的（证据格式 v%s），这个 brain-kit 读不了：升级 brain-kit |
| `problem` | 拍的时候遇到的问题（没有 Chrome、端口被占、计划某行读不懂） | 拍的时候：%s |
| `planned` | 计划里的行还没被拍过（新加的，或者改了步骤） | %s 在证据计划里，还没被 lean verify 拍过（新加的，或者改过步骤） |
| `not_taken` | 拍了，什么也没拍到 | %s 没拍成：%s |
| `missed` | 截图没看到这一行等的字 | %s 没看到要的：%s |
| `old` | 证据是别的代码的：拍完以后代码改了 | %s 是旧代码的：拍完以后改了 %s。合并前要重跑 lean verify |
| `unknown` | 比不出是不是这份代码的 | %s 核对不了是不是这份代码的：%s |
| `uncommitted` | 只在工作区：证据还没提交 | %s 还没提交，MR 里看不到 |
| `ac_without` | 接上证据检查以后：一条 AC 没有测试，也没有证据 | %s 没有证据（没有测试，也没有截图） |
| `ui_without_shot` | 接上证据检查以后：改了界面（kit.json `proof.ui_paths`），却没有这份代码的截图 | 改了界面（%s），没有这份代码的截图 |

一行计划的状态只取一个：`current`（是这份代码的，看到了要的），或者 `planned`、`not_taken`、`missed`、`old`、`unknown`、`void` 之一。一行可能有好几次拍：显示的是最新的、有文件的那张，按它自己的代码判新旧。最新一次什么都没拍到（比如运行机没有 Chrome），就还显示上一张，`last` 里写最新这次为什么没拍。

## MR 说明里怎么看（`brain mr-desc`）

「验收标准和证据」在 verify 那行下面多一行证据：

- 「证据：verify 留下了 3 张截图（2 张看到了要的，1 张没看到）和 1 份测试输出，是这份代码的。」
- 拍完以后代码又改了：「……，**是旧代码的**：拍完以后改了 `src/refund.py`。合并前要重跑 lean verify。」
- 运行机没有 Chrome 时，再加一句：「最近一次 verify 没拍截图（没有 Chrome……）：用的是 2026-11-18 04:19 UTC 那次 verify 拍的。」
- 这个项目的 verify 不留证据时，就是 `no_proof_check` 那一句。

接上证据检查以后，AC 表多一列「证据」：每张截图一个链接，钉在写下这个文件的那次提交上（kit.json 要有 `gitlab.base` 和 `gitlab.project`），所以之后再提交记录，链接也不变。没看到要的写「没看到」，旧代码的写「旧代码」，没有测试也没有证据的 AC 写「没有证据」。表下面是截图用的计划和浏览器，还有每份测试输出的链接。「还没有」列出缺的证据（上表除了已经说过的 `no_proof_check`、`missed`、`old`、`unknown`），「怎么审」先指没看到要的那张截图。

这一节最后一行直接显示截图：表里链接的截图中，是这份代码的、看到了要的，最多 4 张，和链接钉在同一个提交上，宽 360。验收的产品只看 GitLab，要让他直接看到。kit.json 没有 `gitlab.base` 和 `gitlab.project` 时没有这一行，链接也和以前一样。图片本来就在仓库里，用看的人自己的 GitLab 登录加载，不多给谁看。

别的图片都不显示（GitLab 也读成图片的别的写法，比如 `!\[`、`&#33;[`，一样失效，见 `docs/mr-desc.md`）。这一行在两个地方有不同的保证：

- **MR 描述**是 `brain handoff` 用 `render()` 的输出设的，只有 `render()` 自己从证据拼的这一行是图片。记录里的文字写成这样也照旧失效（`inert()`）。
- **桥的「变更说明」评论**贴的是分支上的 MR.md。这个文件是机器人提交的，人也能改，所以桥不信它，只在核对过以后放过这一行（`mrdesc.sealed_evidence`；桥没有 checkout，用 `gl.file_raw` 读 MR 最新提交上的 run.json、证据和 Lean 记录）。它只看第一行单独成段、整行只有图片的行（`mrdesc.is_thumbnail_line`：最多 4 张，每张的地址是 kit.json 的 `gitlab.base`、`gitlab.project` 加 `-/raw/<40 位提交>/<路径>`，后面只能带 `{width=360}` 或者什么都不带）。每张图都要满足下面三条，有一张不满足，整行失效：
  - 文件在这次 run 的 intent 的一套证据里（拆出来的小需求只认自己 clone 的），这套证据在 MR 最新提交上算数（见上面「算不算」：它那个 clone 的 Lean 记录里有封住它的 VERIFY，文件夹和清单对得上），而且那份 Lean 记录的哈希链没断（和 `brain trace` 用同一段代码核对）。
  - 清单列了这个文件和它的 sha256，结果是 `ok`（没看到要的那张，比如失败的 P-2，只在「证据」列留链接），图片的说明文字就是清单里它的名字（`P-1` 这样的行号，或者文件名）。
  - 地址里那个提交上的这个文件，sha256 和清单记的一样（和链接钉提交用的是同一条规则）。

  run.json、清单或 Lean 记录读不懂时（比如数字大到算不了、括号套得太深），那张图就不算，这一行照样失效，评论的其余部分照常发。

  人改过 MR.md 时，显示的可能和 `render()` 拼的那行不一样（比如换成另一张也封好的截图），但不会是过期的、别的需求的、或者 Lean 的 hook 之外放进来的图片。

  这是一道护栏，不是锁。它挡得住在 Lean 的 hook 里干活的 agent，也挡得住旧的和别处的证据；挡不住能往分支推送、自己手写一行 Lean 记录的人：哈希链不带密钥，谁都能算。要挡住这种人只能靠签名，这留给审核门禁的设计。

  包着的 `run` 命令写进 `$BRAIN_PROOF_DIR` 的文件（比如 Playwright 的截图）也会这样显示，但它们是项目自己的命令做的，不是 `shots` 拍的：封条只说明 verify 留下的就是这个文件，不说明它拍对了。审查的人要打开看。

`brain doctor`：没接上时给出要加的 lean.json 行（现有的每条检查包一层，再加 `shots`）；接上以后看 kit 文件在不在、是不是这个 brain-kit 的，这台机器的 Python 是不是按 UTF-8 读命令输出（不是的话，有中文的证据都封不上）；有 `shots` 时再看 Chrome、kit.json `proof` 和查监听地址的工具。都只是 WARN，不拦。

## 已知限制

- **只改权限，证据不会变旧。** 证据只比文件内容：只 `chmod` 过的文件（比如加了可执行位），证据还算这份代码的。Lean 的代码指纹却算权限，所以「最近一次 verify」那行会说改了代码，要重跑 verify。
- **`run` 留下的文件是项目自己的命令做的。** 包着的命令写进 `$BRAIN_PROOF_DIR` 的文件（比如 Playwright 的截图）不是 `shots` 拍的：封条只说明 verify 留下的就是这个文件，不说明它拍对了。审查的人要打开看。

## 给 app 的 JSON

app 不跑项目里的任何东西，只读提交。它通过 `bk.m("proof")` 调 `bind()`，拿 `to_json()`：

```python
P = bk.m("proof")
ev = P.bind(repo, files, raw, "aidlc-docs/<intent>", events, lcfg, commit,
            clone=None, units=None, acs=["AC-1", ...], tested=["AC-1"], ui_changed=[])
data = ev.to_json()
```

| 参数 | 是什么 |
|---|---|
| `repo` | 项目的 git 仓库（bare 也行）。`bind()` 在这里只跑 git：`cat-file -e`、`diff --name-only`、`ls-tree`；`commit` 是 `None` 时还跑 `status` 和 `hash-object`（工作区）。不跑项目的代码，也不跑项目的 Lean。 |
| `files(prefix)`、`raw(path)` | 读要审的那棵树：`prefix` 下的全部文件路径；一个文件的字节，没有就是 `None`（符号链接当没有）。和 `mrdesc._Tree` 的 `files`、`raw` 一样。 |
| `events` | 这个 intent 的 Lean 记录，从旧到新，每条带 `_file`（它所在 `aidlc-docs/audit/*.jsonl` 的路径）。拆出来的小需求只给它自己 clone 的，再传 `clone`（6 位十六进制）和 `units`（它的单元）。 |
| `lcfg` | 那棵树的 `aidlc-docs/lean.json` 内容（决定哪些路径是记录）。 |
| `commit` | 要审的提交（40 位）。`None`：工作区（本地的 `brain mr-desc`）。 |
| `acs`、`tested`、`ui_changed` | 需求的 AC 号（按顺序）、其中有测试的、改到的界面路径。不给就不报 `ac_without`、`ui_without_shot`。 |

顶层字段：

| 字段 | 说明 |
|---|---|
| `schema` | 1。字段只加不改；改了就升这个号。 |
| `judged` | 按哪份代码判的：`commit`；`worktree` 为 true 时还加上工作区没提交的文件；`dirty` 是那些代码文件有几个（`null`：太多，或者 git 没说清）。 |
| `configured` | lean.json 有证据检查，或者最近一次 verify 跑了证据检查。 |
| `state` | 整体一个词：`off`（不留证据）、`none`（还没有算数的证据）、`stale`（有旧代码的）、`unknown`（有核对不了的）、`failed`（有没看到要的，或者有拍完改过的）、`current`。 |
| `counts` | `shots`（有截图的行）、`logs`，和每种行状态各有几行。 |
| `env` | 最新一次拍截图时的浏览器和窗口，或者为什么没有。 |
| `rows[]` | 一行计划一项（见下）。 |
| `logs[]` | 每个 `run` 名字最新的一套：命令的全部输出和它留下的文件。 |
| `sets[]` | 找到的每一套（包括不算的），给「运行过程 → 构建测试 → 第 n 轮」用：按 `verify` 找到那一轮的。 |
| `plans[]` | 每份计划：`unit`、`path`、现在的 `sha256`、`rows`（读懂的行数）、`problems`、`shot`（最新一次拍时读到的 sha256；和 `sha256` 不同就是拍完改过）。 |
| `by_ac` | 每条 AC 有哪些证据：行的 `label`，`run` 留下的文件的 `id`。 |
| `without` | 没有测试、也没有证据的 AC。 |
| `gaps[]` | 缺什么：`kind`（上表）、`text`（原话，直接显示，app 不要自己另写一套词）、`refs`（说的是哪些行、文件、AC）。 |

`rows[]` 每项：`set`、`unit`、`id`、`label`（只有一个单元时就是 `P-2`，多个单元时是 `web/P-2`）、`ac`、`what`、`steps`、`state`、`replanned`（计划里这一行现在的写法和显示的那次拍时不一样）、`shown`、`last`。

- `shown`（没有就是 `null`）：显示的那张。`path`、`sha256`、`size`、`result`（`ok` 或 `failed`）、`note`、`secs`、`page`（浏览器最后停在哪：`path`、`title`）；`verify`（封它的 VERIFY：`at`、`ledger`、`seq`、`ok`、`fingerprint`、`cmd`、`exit`）；`code`（拍的时候的 `commit`，和几个没提交的代码文件 `dirty`）；`freshness`（`current`、`stale`、`unknown`）、`changed`（`stale` 时改了哪些代码文件）、`why`（`unknown` 时为什么）；`uncommitted`（只在工作区：还没提交）。
- `last`（没有就是 `null`）：最新一次拍，但不是显示的那张时：`result`、`note`、`path`、`ok`、`verify`。

`logs[]` 每项：`set`、`label`（「命令输出 tests：pytest -q」）、`path`（`output.txt`）、`exit`、`lines`、`problems`、`files[]`，和 `shown` 一样的 `verify`、`code`、`freshness`、`changed`、`why`、`uncommitted`。

`files[]`（`logs[]` 和 `sets[]` 里）每项：`unit`、`id`、`ac`、`what`、`result`、`note`、`path`、`sha256`、`size`、`ok`（文件在、而且和清单的 sha256 对得上；没拍到时 `path` 是 `null`、`ok` 是 false）。

`sets[]` 每项：`dir`、`set`、`kind`、`clone`、`output`、`output_sha256`、`lines`、`sealed`（算不算）、`verify`（不算时为 `null`）、`error`（读不懂时为什么）、`env`、`problems`、`files[]`。

建议的颜色（和「检查」那行一样）：`current` 用 ok，`old`、`unknown` 用 warn，`missed`、`void` 用 bad，`planned`、`not_taken` 用 idle。app 只显示 PNG 和 JPEG：按文件开头的字节认，不按文件名，不显示 SVG。截图都不超过 4 MB，和产出抽屉现在的上限一样。

下面是一次真实的 `bind()` 输出：负责人 verify 时 P-2 没看到要的，已提交；运行机（没有 Chrome）又 verify 了一次，计划里新加了 P-4，这两样都还没提交。按工作区判（`tests/test_proof_bind.py` 的 `docs_example`；clone id 和命令换成了通用写法，别的原样）。`tests/test_proof_bind.py` 会核对这里的字段和真实输出一个不差。

```json
{
 "schema": 1,
 "judged": {"commit": "47716f130ccd934f70c1dff4c6339c8e50685d7e", "worktree": true, "dirty": 0},
 "configured": true,
 "state": "failed",
 "counts": {"shots": 3, "logs": 1, "current": 2, "planned": 1, "not_taken": 0, "missed": 1, "old": 0, "unknown": 0, "void": 0},
 "env": "没有 Chrome（设 CHROME）",
 "rows": [
  {
   "set": "ui",
   "unit": "app",
   "id": "P-1",
   "label": "P-1",
   "ac": ["AC-1"],
   "what": "已付款订单有「退款」按钮",
   "steps": ["open /orders/123", "wait 退款"],
   "state": "current",
   "replanned": false,
   "shown": {
    "verify": {
     "at": "2026-11-18T05:36:58.232Z",
     "ledger": "aidlc-docs/audit/dev-mac-884a22.jsonl",
     "seq": 5,
     "ok": true,
     "fingerprint": "975861ff2c9463d0061b8994aac918f63252e7e6605d22740a78e0d16d35243c",
     "cmd": "python3 .kiro/brain/proof.py shots",
     "exit": 0
    },
    "freshness": "current",
    "changed": [],
    "why": "",
    "uncommitted": false,
    "code": {"commit": "5905999e93f366115478aec66121e58c2dafddf2", "dirty": 0},
    "path": "aidlc-docs/261118-demo/construction/build-and-test/proof/dev-mac-884a22/ui/app-P-1.png",
    "sha256": "373d1511a661fb5ef062fda71593a171cec9f39a93a4c286f8ed1c7e40a25ddc",
    "size": 20,
    "result": "ok",
    "note": "",
    "secs": 0.4,
    "page": {"path": "/orders/123", "title": "订单 123"}
   },
   "last": {
    "result": "skipped",
    "note": "没有 Chrome（设 CHROME）",
    "path": null,
    "ok": false,
    "verify": {
     "at": "2026-11-18T05:36:59.118Z",
     "ledger": "aidlc-docs/audit/runner-1-3f9c01.jsonl",
     "seq": 1,
     "ok": true,
     "fingerprint": "975861ff2c9463d0061b8994aac918f63252e7e6605d22740a78e0d16d35243c",
     "cmd": "python3 .kiro/brain/proof.py shots",
     "exit": 0
    }
   }
  },
  {
   "set": "ui",
   "unit": "app",
   "id": "P-2",
   "label": "P-2",
   "ac": ["AC-2"],
   "what": "点退款以后状态是「已退款」",
   "steps": ["open /orders/123", "click 退款", "wait 已退款"],
   "state": "missed",
   "replanned": false,
   "shown": {
    "verify": {
     "at": "2026-11-18T05:36:58.232Z",
     "ledger": "aidlc-docs/audit/dev-mac-884a22.jsonl",
     "seq": 5,
     "ok": true,
     "fingerprint": "975861ff2c9463d0061b8994aac918f63252e7e6605d22740a78e0d16d35243c",
     "cmd": "python3 .kiro/brain/proof.py shots",
     "exit": 0
    },
    "freshness": "current",
    "changed": [],
    "why": "",
    "uncommitted": false,
    "code": {"commit": "5905999e93f366115478aec66121e58c2dafddf2", "dirty": 0},
    "path": "aidlc-docs/261118-demo/construction/build-and-test/proof/dev-mac-884a22/ui/app-P-2.png",
    "sha256": "724cfccecd986a8945022349f21ff25d4bfb804323e0963a52ce382ca0078b13",
    "size": 20,
    "result": "failed",
    "note": "没等到「已退款」（10 秒）",
    "secs": 0.4,
    "page": {"path": "/orders/123", "title": "订单 123"}
   },
   "last": {
    "result": "skipped",
    "note": "没有 Chrome（设 CHROME）",
    "path": null,
    "ok": false,
    "verify": {
     "at": "2026-11-18T05:36:59.118Z",
     "ledger": "aidlc-docs/audit/runner-1-3f9c01.jsonl",
     "seq": 1,
     "ok": true,
     "fingerprint": "975861ff2c9463d0061b8994aac918f63252e7e6605d22740a78e0d16d35243c",
     "cmd": "python3 .kiro/brain/proof.py shots",
     "exit": 0
    }
   }
  },
  {
   "set": "ui",
   "unit": "app",
   "id": "P-3",
   "label": "P-3",
   "ac": ["AC-3"],
   "what": "退款以后显示退款单号",
   "steps": ["open /orders/123", "click 退款", "wait 退款单号"],
   "state": "current",
   "replanned": false,
   "shown": {
    "verify": {
     "at": "2026-11-18T05:36:58.232Z",
     "ledger": "aidlc-docs/audit/dev-mac-884a22.jsonl",
     "seq": 5,
     "ok": true,
     "fingerprint": "975861ff2c9463d0061b8994aac918f63252e7e6605d22740a78e0d16d35243c",
     "cmd": "python3 .kiro/brain/proof.py shots",
     "exit": 0
    },
    "freshness": "current",
    "changed": [],
    "why": "",
    "uncommitted": false,
    "code": {"commit": "5905999e93f366115478aec66121e58c2dafddf2", "dirty": 0},
    "path": "aidlc-docs/261118-demo/construction/build-and-test/proof/dev-mac-884a22/ui/app-P-3.png",
    "sha256": "4f1180ed01d85060f0e9ce2a79052f5ed34a6681277d08f16aa2c6b38bbd27a1",
    "size": 20,
    "result": "ok",
    "note": "",
    "secs": 0.4,
    "page": {"path": "/orders/123", "title": "订单 123"}
   },
   "last": {
    "result": "skipped",
    "note": "没有 Chrome（设 CHROME）",
    "path": null,
    "ok": false,
    "verify": {
     "at": "2026-11-18T05:36:59.118Z",
     "ledger": "aidlc-docs/audit/runner-1-3f9c01.jsonl",
     "seq": 1,
     "ok": true,
     "fingerprint": "975861ff2c9463d0061b8994aac918f63252e7e6605d22740a78e0d16d35243c",
     "cmd": "python3 .kiro/brain/proof.py shots",
     "exit": 0
    }
   }
  },
  {
   "set": "ui",
   "unit": "app",
   "id": "P-4",
   "label": "P-4",
   "ac": ["AC-4"],
   "what": "旧订单不显示退款按钮",
   "steps": ["open /orders/77", "wait 订单 77"],
   "state": "planned",
   "replanned": false,
   "shown": null,
   "last": null
  }
 ],
 "logs": [
  {
   "verify": {
    "at": "2026-11-18T05:36:59.118Z",
    "ledger": "aidlc-docs/audit/runner-1-3f9c01.jsonl",
    "seq": 1,
    "ok": true,
    "fingerprint": "975861ff2c9463d0061b8994aac918f63252e7e6605d22740a78e0d16d35243c",
    "cmd": "python3 .kiro/brain/proof.py run tests -- python3 -m pytest -q",
    "exit": 0
   },
   "freshness": "current",
   "changed": [],
   "why": "",
   "uncommitted": true,
   "code": {"commit": "47716f130ccd934f70c1dff4c6339c8e50685d7e", "dirty": 0},
   "set": "tests",
   "path": "aidlc-docs/261118-demo/construction/build-and-test/proof/runner-1-3f9c01/tests/output.txt",
   "exit": 0,
   "lines": 8,
   "label": "命令输出 tests：python3 -m pytest -q",
   "problems": [],
   "files": [
    {
     "unit": "",
     "id": "AC-2-refund.png",
     "ac": ["AC-2"],
     "what": "",
     "result": "ok",
     "note": "",
     "path": "aidlc-docs/261118-demo/construction/build-and-test/proof/runner-1-3f9c01/tests/AC-2-refund.png",
     "sha256": "91f756ec6f5e8d51bbea79f881fce572575759fdef58eca5a7215d41816923eb",
     "size": 11,
     "ok": true
    },
    {
     "unit": "",
     "id": "shots-home.png",
     "ac": [],
     "what": "",
     "result": "ok",
     "note": "",
     "path": "aidlc-docs/261118-demo/construction/build-and-test/proof/runner-1-3f9c01/tests/shots-home.png",
     "sha256": "cc57db3148c8771d181e7b6ba01599346c6040d1c7fdf20666cbe5316503cd57",
     "size": 9,
     "ok": true
    }
   ]
  }
 ],
 "sets": [
  {
   "dir": "aidlc-docs/261118-demo/construction/build-and-test/proof/dev-mac-884a22/tests",
   "set": "tests",
   "kind": "run",
   "clone": "dev-mac-884a22",
   "output": "aidlc-docs/261118-demo/construction/build-and-test/proof/dev-mac-884a22/tests/output.txt",
   "output_sha256": "86e7615417898eb3f0d52f6144ac3b6f653ac0d458013e8fa32b0071485a98c0",
   "lines": 8,
   "sealed": true,
   "verify": {
    "at": "2026-11-18T05:36:58.232Z",
    "ledger": "aidlc-docs/audit/dev-mac-884a22.jsonl",
    "seq": 5,
    "ok": true,
    "fingerprint": "975861ff2c9463d0061b8994aac918f63252e7e6605d22740a78e0d16d35243c",
    "cmd": "python3 .kiro/brain/proof.py run tests -- python3 -m pytest -q",
    "exit": 0
   },
   "error": "",
   "env": "",
   "problems": [],
   "files": [
    {
     "unit": "",
     "id": "AC-2-refund.png",
     "ac": ["AC-2"],
     "what": "",
     "result": "ok",
     "note": "",
     "path": "aidlc-docs/261118-demo/construction/build-and-test/proof/dev-mac-884a22/tests/AC-2-refund.png",
     "sha256": "91f756ec6f5e8d51bbea79f881fce572575759fdef58eca5a7215d41816923eb",
     "size": 11,
     "ok": true
    },
    {
     "unit": "",
     "id": "shots-home.png",
     "ac": [],
     "what": "",
     "result": "ok",
     "note": "",
     "path": "aidlc-docs/261118-demo/construction/build-and-test/proof/dev-mac-884a22/tests/shots-home.png",
     "sha256": "cc57db3148c8771d181e7b6ba01599346c6040d1c7fdf20666cbe5316503cd57",
     "size": 9,
     "ok": true
    }
   ]
  },
  {
   "dir": "aidlc-docs/261118-demo/construction/build-and-test/proof/dev-mac-884a22/ui",
   "set": "ui",
   "kind": "shots",
   "clone": "dev-mac-884a22",
   "output": "aidlc-docs/261118-demo/construction/build-and-test/proof/dev-mac-884a22/ui/output.txt",
   "output_sha256": "3f02b41f192742125d0d66d22cdb8cbf1a5643598ccb9b22b7c4c383a7e3bd65",
   "lines": 1,
   "sealed": true,
   "verify": {
    "at": "2026-11-18T05:36:58.232Z",
    "ledger": "aidlc-docs/audit/dev-mac-884a22.jsonl",
    "seq": 5,
    "ok": true,
    "fingerprint": "975861ff2c9463d0061b8994aac918f63252e7e6605d22740a78e0d16d35243c",
    "cmd": "python3 .kiro/brain/proof.py shots",
    "exit": 0
   },
   "error": "",
   "env": "Chrome 157.0.0.0 · 1280×800",
   "problems": [],
   "files": [
    {
     "unit": "app",
     "id": "P-1",
     "ac": ["AC-1"],
     "what": "已付款订单有「退款」按钮",
     "result": "ok",
     "note": "",
     "path": "aidlc-docs/261118-demo/construction/build-and-test/proof/dev-mac-884a22/ui/app-P-1.png",
     "sha256": "373d1511a661fb5ef062fda71593a171cec9f39a93a4c286f8ed1c7e40a25ddc",
     "size": 20,
     "ok": true
    },
    {
     "unit": "app",
     "id": "P-2",
     "ac": ["AC-2"],
     "what": "点退款以后状态是「已退款」",
     "result": "failed",
     "note": "没等到「已退款」（10 秒）",
     "path": "aidlc-docs/261118-demo/construction/build-and-test/proof/dev-mac-884a22/ui/app-P-2.png",
     "sha256": "724cfccecd986a8945022349f21ff25d4bfb804323e0963a52ce382ca0078b13",
     "size": 20,
     "ok": true
    },
    {
     "unit": "app",
     "id": "P-3",
     "ac": ["AC-3"],
     "what": "退款以后显示退款单号",
     "result": "ok",
     "note": "",
     "path": "aidlc-docs/261118-demo/construction/build-and-test/proof/dev-mac-884a22/ui/app-P-3.png",
     "sha256": "4f1180ed01d85060f0e9ce2a79052f5ed34a6681277d08f16aa2c6b38bbd27a1",
     "size": 20,
     "ok": true
    }
   ]
  },
  {
   "dir": "aidlc-docs/261118-demo/construction/build-and-test/proof/runner-1-3f9c01/tests",
   "set": "tests",
   "kind": "run",
   "clone": "runner-1-3f9c01",
   "output": "aidlc-docs/261118-demo/construction/build-and-test/proof/runner-1-3f9c01/tests/output.txt",
   "output_sha256": "fa691e0bf851ce5822b3f129fd91d3c6dc702a4a2cdd2c14edeb693a966c0524",
   "lines": 8,
   "sealed": true,
   "verify": {
    "at": "2026-11-18T05:36:59.118Z",
    "ledger": "aidlc-docs/audit/runner-1-3f9c01.jsonl",
    "seq": 1,
    "ok": true,
    "fingerprint": "975861ff2c9463d0061b8994aac918f63252e7e6605d22740a78e0d16d35243c",
    "cmd": "python3 .kiro/brain/proof.py run tests -- python3 -m pytest -q",
    "exit": 0
   },
   "error": "",
   "env": "",
   "problems": [],
   "files": [
    {
     "unit": "",
     "id": "AC-2-refund.png",
     "ac": ["AC-2"],
     "what": "",
     "result": "ok",
     "note": "",
     "path": "aidlc-docs/261118-demo/construction/build-and-test/proof/runner-1-3f9c01/tests/AC-2-refund.png",
     "sha256": "91f756ec6f5e8d51bbea79f881fce572575759fdef58eca5a7215d41816923eb",
     "size": 11,
     "ok": true
    },
    {
     "unit": "",
     "id": "shots-home.png",
     "ac": [],
     "what": "",
     "result": "ok",
     "note": "",
     "path": "aidlc-docs/261118-demo/construction/build-and-test/proof/runner-1-3f9c01/tests/shots-home.png",
     "sha256": "cc57db3148c8771d181e7b6ba01599346c6040d1c7fdf20666cbe5316503cd57",
     "size": 9,
     "ok": true
    }
   ]
  },
  {
   "dir": "aidlc-docs/261118-demo/construction/build-and-test/proof/runner-1-3f9c01/ui",
   "set": "ui",
   "kind": "shots",
   "clone": "runner-1-3f9c01",
   "output": "aidlc-docs/261118-demo/construction/build-and-test/proof/runner-1-3f9c01/ui/output.txt",
   "output_sha256": "c2c16cb579057f915a85ee763c1d722cb58efbccd19dc8e3ba3b8488a5227671",
   "lines": 1,
   "sealed": true,
   "verify": {
    "at": "2026-11-18T05:36:59.118Z",
    "ledger": "aidlc-docs/audit/runner-1-3f9c01.jsonl",
    "seq": 1,
    "ok": true,
    "fingerprint": "975861ff2c9463d0061b8994aac918f63252e7e6605d22740a78e0d16d35243c",
    "cmd": "python3 .kiro/brain/proof.py shots",
    "exit": 0
   },
   "error": "",
   "env": "没有 Chrome（设 CHROME）",
   "problems": [],
   "files": [
    {
     "unit": "app",
     "id": "P-1",
     "ac": ["AC-1"],
     "what": "已付款订单有「退款」按钮",
     "result": "skipped",
     "note": "没有 Chrome（设 CHROME）",
     "path": null,
     "sha256": null,
     "size": 0,
     "ok": false
    },
    {
     "unit": "app",
     "id": "P-2",
     "ac": ["AC-2"],
     "what": "点退款以后状态是「已退款」",
     "result": "skipped",
     "note": "没有 Chrome（设 CHROME）",
     "path": null,
     "sha256": null,
     "size": 0,
     "ok": false
    },
    {
     "unit": "app",
     "id": "P-3",
     "ac": ["AC-3"],
     "what": "退款以后显示退款单号",
     "result": "skipped",
     "note": "没有 Chrome（设 CHROME）",
     "path": null,
     "sha256": null,
     "size": 0,
     "ok": false
    }
   ]
  }
 ],
 "plans": [
  {
   "unit": "app",
   "path": "aidlc-docs/261118-demo/construction/app/proof-plan.md",
   "sha256": "091a00977914bb1c6cd588e45150f2d7952bf1ad27ed3ab6017c58fbe060d133",
   "rows": 4,
   "problems": [],
   "shot": "7f761b6d9d72d679acbff01bd97215eda9dcb58a1557186827eff23762a2bdb9"
  }
 ],
 "by_ac": {"AC-1": ["P-1"], "AC-2": ["P-2", "AC-2-refund.png"], "AC-3": ["P-3"]},
 "without": ["AC-4"],
 "gaps": [
  {"kind": "missed", "text": "P-2 没看到要的：没等到「已退款」（10 秒）", "refs": ["P-2"]},
  {"kind": "planned", "text": "P-4 在证据计划里，还没被 lean verify 拍过（新加的，或者改过步骤）", "refs": ["P-4"]},
  {"kind": "uncommitted", "text": "tests 还没提交，MR 里看不到", "refs": ["tests"]},
  {"kind": "ac_without", "text": "AC-4 没有证据（没有测试，也没有截图）", "refs": ["AC-4"]}
 ]
}
```
