# 从旧 brain-kit 迁到「Brain 交付」app，并配好团队运行机

适用于：项目里已经装了 brain-kit，大家直接在 KiroCrew 会话里用 `brain-dev` agent 开发，还没装 app。

做完以后：

- 每个人的电脑上装好 Brain 交付 app，用的是 app 自带的 brain-kit 和 AI-DLC Lean。
- 以前的需求（认领了的、做到一半的、交接出去的、合并了的）都出现在 app 的需求列表里，做到一半的能接着做。
- 项目的 kit 文件升级到 app 这一版。
- 团队运行机按 app 的团队模式配好。这一部分**还没在真机上跑通过**，见第三部分开头。

这份文档分三部分，在不同的机器上做：

| 部分 | 在哪台机器 | 谁 | 次数 |
|---|---|---|---|
| 第一部分：每个人的电脑 | 每个组员自己的电脑（macOS 或 Linux） | 每个人 | 每台电脑一次 |
| 第二部分：GitLab 项目 | GitLab 网页 + 任意一台电脑 | 项目负责人 | 每个项目一次 |
| 第三部分：团队运行机 | 一台 Linux 服务器 | 管理员 | 一次 |

顺序：

1. 项目负责人先在自己电脑上做完第一部分，包括第 7 步（升级 kit 文件）。
2. 项目负责人做第二部分。
3. 管理员做第三部分。
4. 其他组员的第一部分随时可以做；第 7 步的 MR 合进默认分支以后，他们的仓库行才会全绿。

## 怎么用这份文档

在要做的那台机器上打开 KiroCrew，开一个新会话，把这份文档整份贴进去，再加一句：

```text
照这份文档做「第一部分」（或第二、第三部分）。按下面「给 KiroCrew 的规则」来。
```

### 给 KiroCrew 的规则

你（KiroCrew 会话里的 AI）照这份文档做，规则如下：

1. 每一步先说你要运行什么，运行后把结果原样告诉我。每一步都有「验证」，验证不过就停下，告诉我哪里不对，不要跳过、不要换办法绕过去。
2. 下面这些你必须停下来，告诉我怎么做，由我自己做：
   - KiroCrew 和 app 页面上的按钮、信任确认和任何权限弹窗。文档里写「人点」的都是。
   - 任何要 `sudo` 或管理员密码的命令。把完整命令写给我，我自己运行。
   - 改 KiroCrew 的全局设置（例如引擎）：先说明会影响什么，等我同意。
   - 在 GitLab 网页上建账号、建 token、加 SSH key 或 deploy key、改成员和权限、建标签、开 MR、合 MR。
   - 把 token 和密钥存进文件：我自己来。不要让我把 token、私钥贴进聊天，你也不要打开或打印这些文件，只能用 `ls -l` 看它在不在、权限对不对。
   - 切换一个仓库的当前分支、删除任何文件或分支、`git push`：先说清楚影响，等我同意。
3. 沙箱拒绝某条命令时，告诉我，不要换写法绕过。能用 app「设置」页按钮修的，让我去点按钮。
4. 文档里写着「未实测」的步骤，照做，但做完一定跑它的验证，并在最后的报告里单独列出来。
5. 最后给我一份报告：每一步是「好了」「要我做」还是「没过」，没过的写原因。

约定：

- `<BK>` 指 `~/.kiro/crew/apps/brain/vendor/brain-kit`，也就是 app 自带的 brain-kit。`brain` 命令一律写全路径：`python3 <BK>/bin/brain`。
- `kirocrew` 找不到时，用 `~/.local/bin/kirocrew`。
- `kirocrew config get` 可能先打印几行 WARNING，看最后一行。

## 第一部分：每个人的电脑

### 1. 盘点旧的设置（只读）

运行下面的命令，把结果整理成一张表给我。这一步什么都不改。

```bash
kirocrew --version || ~/.local/bin/kirocrew --version     # 要 0.11 或更新
kirocrew config get agent.acp_backend || ~/.local/bin/kirocrew config get agent.acp_backend
git --version; python3 --version                          # python3 要 3.8 或更新
ls -l ~/.kiro/agents/brain-dev.json ~/.kiro/agents/brain-dev-crew.json 2>&1
ls -l ~/.local/bin/brain 2>&1 && readlink ~/.local/bin/brain
ls -ld ~/.kiro/crew/apps/brain ~/.kiro/crew/apps/brain-bridge 2>&1
ls -l ~/.brain/gitlab-token 2>&1                          # 只看权限，不要打开
git config --global --get brain.user; git config --global --get brain.intake
```

再问我：这台电脑上用 brain-kit 的项目仓库在哪些目录。对每个仓库运行：

```bash
cd <仓库>
git remote -v | head -2
git branch --show-current
git status --short | head
grep -E '"(tracker|req_prefix|default_branch|branch_prefix|bridge_user)"' .brain/kit.json
grep -A3 '"pins"' .brain/kit.json
git worktree list
P=$(python3 -c "import json;print(json.load(open('.brain/kit.json')).get('branch_prefix') or 'ai')")
git branch -a --list '*claim/*' "*$P/*" | head -50
```

表里要有：每个仓库的路径、默认分支、当前分支、有没有没提交的改动、`tracker`、`branch_prefix`（没写就是 `ai`）、`pins.lean`、有哪些 `claim/<KEY>` 和 `<branch_prefix>/<用户>/<KEY>-<slug>` 分支、每个 worktree 的路径和分支。

验证：每个仓库都有 `.brain/kit.json`。没有的仓库不是旧 brain-kit 仓库，这份文档不管它（在 app 里用「导入仓库」→「安装」装）。

### 2. 装 app（人做）

1. KiroCrew 侧边栏 → **Apps** → **Discover（发现）** → 右上角齿轮 → **管理应用来源（Manage app sources）** → **添加注册表（Add Registry）**。仓库填 `https://github.com/ai-for-developer/kirocrew-app-brain`，分支填 **`release`**（不是 main），保存。
2. 回到 Discover，找到 **Brain 交付** → **安装（Install）**。找不到就点 **同步应用（Sync apps）**。
3. 弹出信任确认：这个 app 和 KiroCrew 同等权限运行。确认信任。
4. 报 `git clone timed out` 时：KiroCrew 拉仓库只等 60 秒，网慢就会超时。点 **Trust and try again** 多试几次。
5. 装好后点 **启用（Enable）**，同意 sessionApproval（app 可以让它自己开的会话不用逐个批准工具调用）。
6. 不要从本机的源码目录（main 分支）装：那里的界面文件不进 git，装上去是旧界面，也没有自带的 brain-kit。

验证（AI 运行）：

```bash
cat ~/.kiro/crew/apps/brain/VERSIONS
ls -l ~/.kiro/crew/apps/brain/vendor/brain-kit/bin/brain
python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.kiro/crew/apps/brain/installed.json')))['source'])"
```

第一行是 `brain (Brain 交付) <版本>`；`source` 以 `registry:` 开头。

### 3. 让 app 用它自带的 brain-kit

以前在 app 里填过 brain-kit 路径的，这个值会一直留着。打开 app → **设置**，看「brain-kit 在哪」那一栏：要以 `apps/brain/vendor/brain-kit` 结尾。不是的话，改成 `~/.kiro/crew/apps/brain/vendor/brain-kit`，保存（人做）。

验证：brain-kit 卡是绿的，路径以 `apps/brain/vendor/brain-kit` 结尾。

### 4. 换成 brain-dev-crew agent，引擎换成 kas

app 开的会话都用 `brain-dev-crew`，它带着 Lean 的三个拦截。旧的 `brain-dev` 不能再用：仓库里的 `.kiro/agents/brain-dev.md` 会盖住 `~/.kiro/agents/brain-dev.json`，拦截就没了。

1. 还没有 `~/.kiro/agents/brain-dev-crew.json`，而且我改过 `~/.kiro/agents/brain-dev.json`（模型、工具、提示词）：运行 `python3 <BK>/bin/brain crew-agent`。它把我改过的字段带进新建的 `brain-dev-crew.json`。
2. 其他情况：到 app → **设置** → **KiroCrew** 卡，点「装上 brain-dev-crew」（人点）。
3. 有「缺 Lean 的拦截」那一行：点「补上 Lean 的拦截」（人点）。只有之后新开的会话才带上。
4. 引擎不是 `kas`：在 **KiroCrew** 卡点「把引擎换成 kas」（人点）。它会改所有项目的会话，可能要重启 KiroCrew。

验证：**KiroCrew** 卡上这台机器的几行都是绿的。`kirocrew config get agent.acp_backend` 的最后一行是 `kas`。

### 5. 让 `brain` 命令指向新的 brain-kit

会话里的 AI 有时直接运行 `brain`（例如写变更说明），走的是 `~/.local/bin/brain`。它要指向 app 自带的那份：

```bash
python3 ~/.kiro/crew/apps/brain/vendor/brain-kit/bin/brain link
readlink ~/.local/bin/brain
```

验证：`readlink` 输出的路径是 `~/.kiro/crew/apps/brain/vendor/brain-kit/bin/brain`（展开后的绝对路径）。`brain link` 报「a file brain-kit did not make」时停下，告诉我那个文件是什么。

shell 配置里以前加过 `alias brain=...` 的，告诉我那一行在哪个文件，由我决定删不删。

### 6. 把仓库加进 app

1. 检查仓库放的位置。KiroCrew 只让 agent 在 `~/workplace` 或 `~/workspace` 下面干活。仓库或者做到一半的 worktree 不在这两个目录下面的，列给我，由我决定要不要挪（挪之前先关掉开着的会话）。
2. 仓库用 GitLab 的：`~/.brain/gitlab-token` 要在、权限 `600`。没有的话，我在 GitLab 建一个有 `api` 权限的个人 token，自己存进去，再 `chmod 600 ~/.brain/gitlab-token`。先做这一条，再做下面的检查。
3. app → 右上 **导入仓库** → 选仓库 → **检查**（人点）。检查只读，不改仓库。
4. 看检查里「你是谁」那一行：
   - 有 token 时，名字由 GitLab 认出，不能改。
   - 没有 token 时，那一行有输入框，填你在 GitLab 上的用户名，保存（它运行 `git config --local brain.user`）。
   - 这个名字要和你以前那些需求分支 `<branch_prefix>/<用户名>/...` 里的名字一样，不一样的话以前的需求不会算成你的。不一样就列给我。
5. 已经装过 brain-kit 的仓库，下一步是 **加入列表**（人点）。它只把仓库记进 app 的设置，仓库里什么都不改。

验证：需求列表里出现了这个仓库的需求。**设置** → **KiroCrew** 卡里这个仓库那几行，记下哪些是红的、哪些是黄的，下一步用。「这个仓库里叫 brain-dev 的会话没有 Lean 的拦截」这一行黄色是正常的，第 10 步删掉旧 agent 后才消失。

### 7. 升级仓库里的 kit 文件（每个仓库一次，项目负责人做）

旧仓库里的 kit 文件一般比 app 这一版旧。常见的是「Lean 的适配文件是旧版」和「Lean 把变更说明……也算成代码」这两行。只在本机做的仓库显示黄色；要交接给运行机的仓库显示红色，运行机跑不了。

这一步改的是默认分支上的文件，**每个仓库只要一个人做**。

1. 先比 Lean 的版本。看仓库里钉的版本和 app 自带的版本：
   ```bash
   grep -A3 '"pins"' <仓库>/.brain/kit.json
   grep -m1 '^VERSION' ~/.kiro/crew/apps/brain/vendor/brain-kit/vendor/aidlc-lean/kit/.kiro/aidlc-lean/aidlc_lean.py
   ```
2. `pins.lean` 比自带的旧（例如钉 `"0.2"`，自带 `0.3.x`）：**只用命令行，不要点按钮**。按钮不会挪这个钉子，升级后 `brain doctor` 会报错。在仓库目录里运行：
   ```bash
   python3 ~/.kiro/crew/apps/brain/vendor/brain-kit/bin/brain install --upgrade
   ```
   它从远端的默认分支建一个新分支（一般叫 `brain/upgrade-kit`，名字被占了就是 `brain/upgrade-kit-2` 这样，看输出），把 kit 文件、Lean 和钉子都写好、提交，不推送、不切换你当前的分支。推送先问我；MR 我在 GitLab 上开。
3. `pins.lean` 和自带的一样（或者没钉）：用按钮。先确认仓库有远端，并且主检出在默认分支上；不是的话先别点，告诉我（按钮在只有一个选项时会直接提交到当前分支，不再问）。然后在 **KiroCrew** 卡这个仓库的那一行点「重装 kit 文件」，选「更新到新分支，开 MR 给团队看」，提交后点「推送并开 MR」→「确定推送」（人点）。
4. MR 合进默认分支（人审、人合）。

验证：MR 合进以后，在仓库的主检出上 `git fetch`。主检出在默认分支上、没有没提交的改动的，`git pull`；不在默认分支上的，先问我，不要切换（它可能正检出着一个需求分支，切走了第 8 步会找不到它）。然后在默认分支的那个目录里运行 `python3 <BK>/bin/brain doctor`，没有 FAIL。**KiroCrew** 卡里这个仓库的行除了第 6 步那行黄色，都是绿的。

### 8. 把以前的需求一个个接上

app 只看 git：`claim/<KEY>` 分支、`<branch_prefix>/<用户>/<KEY>-<slug>` 分支（本地和远端）、默认分支上的 `runs/<KEY>/run.json`。远端的分支每 5 分钟取一次，也可以点「从远端取最新」。

先把第 1 步盘点出来的每个需求，按下表对上 app 里的状态，再照「要做的事」做：

| 以前的状态 | app 里显示 | 要做的事 |
|---|---|---|
| 认领了，还没开工（有 `claim/<KEY>`） | 已认领 | 在 app 里点 **去开工**。它在工作目录下建一个新 worktree，再开会话。 |
| 做到一半，这台电脑上有检出它的分支的目录 | 做到一半，会话空着 | 见下面「接着做一个做到一半的需求」。 |
| 做到一半，分支只在远端，或者本地有分支但没有检出它的目录 | 这台机器上没有它的工作区 | 只有需求的主人在自己电脑上接。`git fetch` 后建 worktree：`git worktree add <工作目录>/<KEY> <分支名>`（先问我工作目录放哪，要在 `~/workplace` 或 `~/workspace` 下面）。然后照下面「接着做」。 |
| 交接给运行机了，MR 上还挂着 `ai-handoff` | 等运行机接手 | 第三部分做完后，新运行机会接。 |
| 交接给运行机了，MR 上已经是 `ai-running`（旧运行机接过手） | 运行机在做 | 新运行机接不过去。切换前让旧运行机做完；做不完的，停掉旧运行机后在 MR 上去掉 `ai-running`，主人在自己电脑上接着做，或者第三部分做完后重新交接。 |
| 交接被拒，原因是 kit 文件旧了 | 运行机跑不了这个分支 | 先做完第 7 步，再在这个需求的会话里让 AI 把默认分支合进来（`git merge origin/<默认分支>`），然后重新交接。 |
| 合并了（默认分支上有 `runs/<KEY>/run.json`） | 已合并 | 不用做。app 会再盯 14 天。 |
| 大需求拆成的小需求 | 大需求下面列出小需求 | 每个小需求照上面对应的状态做。 |

**接着做一个做到一半的需求：**

1. 找出它的目录：`git worktree list` 里检出它的分支的那一个。旧的 `brain start` 会直接把主检出切到需求分支上，所以它也可能就是仓库目录本身。
2. 那个目录不在 `~/workplace` 或 `~/workspace` 下面：告诉我，由我决定要不要挪（第 6 步）。
3. 关掉以前用 `brain-dev` 开的那个会话（人做）。app 只认它自己开的会话，接管不了以前手开的会话，旧会话的记录和用量也不会显示在 app 里。
4. 在 app 里打开这个需求的 **需求现状** → **开会话**（人点）。把「开好后先发一句开场，让它接着做」那个勾去掉，会话开好后自己发：
   ```text
   先读 Lean 的 aidlc-state.md 里的 Session notes，从那里接着做。
   ```

验证：每个需求在 app 里的状态都和上表对得上。对不上的列给我（常见原因：分支名前缀不是 `branch_prefix`，主人的名字和「你是谁」不一样，分支从没推送过）。

app 导不进来的：以前手开会话的聊天记录；用 Jira 认领、还没开工的需求（要等 `brain start` 以后才有分支）；只在别人电脑上、从没推送过的认领和分支。

### 9. 验证关卡（必须做）

「需求没批不能写代码」这类关卡靠 brain-dev-crew 会话里的 Lean 拦截。拦截没跑的话，AI 什么都能写，表面上一切正常。所以一定要试：

1. 在 app 的 **开需求** 里开一个试用需求，点 **开工**（人点）。app 会打开一个 brain-dev-crew 会话。
2. 需求还没批的时候，在这个会话里发：
   `这是关卡测试：请直接用写文件工具建 src/hello.py（一行 print 就行）。被拦下的话，把拦截信息原样告诉我。`
3. 对的结果：AI 被拦下，回复里有 `[AI-DLC Lean] blocked`。
4. 文件建成了：关卡没生效，先别用。回第 4 步把 **KiroCrew** 卡修到全绿，在这个需求的 **需求现状** 点「重开一个会话」，再试一次。

验证：拦下了。

### 10. 清理

1. 所有需求都换到 app 开的会话以后，删掉 `~/.kiro/agents/brain-dev.json`（先问我）。不要删仓库里的 `.kiro/agents/brain-dev.md`，那是给 Kiro CLI 用的，`brain doctor` 要它在。
2. 个人电脑上装过旧的 `brain-bridge` app 的，在 KiroCrew 里卸载它（人点）。它只属于旧的运行机，个人电脑用不上。
3. 留着这些：`~/.brain/gitlab-token`、`~/.brain/intake/`（材料包）、`~/.brain/worktrees/`（`brain reflect` 的 worktree，git 还记着它们）、`git config brain.user` 和 `brain.intake`。
4. 旧的 brain-kit checkout 可以留着，app 不再用它。

验证：**KiroCrew** 卡全绿。

### 11. 报告

照「给 KiroCrew 的规则」第 5 条给我报告，另外列出：每个仓库升级 kit 的分支名或 MR；每个旧需求在 app 里的状态；还要我做的事。

## 第二部分：GitLab 项目（项目负责人，每个项目一次）

交接给运行机才需要这一部分。只在本机开发、本机合并的项目可以跳过。

这一部分大多在 GitLab 网页上做，AI 只能列清单、核对结果。

`tracker` 是 `jira` 的项目，先别交接给新运行机：app 的团队模式还没有地方配 Jira 的 token，交接会被拒。

### 1. 账号和 token（人做）

| 账号 | 做什么 | 项目里的角色 | 凭证 |
|---|---|---|---|
| 桥账号（`bridge_user`） | app 的桥用它在 MR 上贴问题、改标签 | **Developer**（Reporter 改不了 MR 标签，实测会 403） | `api` 权限的 token，给运行机 |
| 机器人账号（`bot_users`） | 运行机上的 AI 用它推 `ai/*` 分支 | Developer；brain-intake 仓库要能读 | 运行机 `brainbot` 的 SSH 公钥 |
| 签名服务 | 签名前到 GitLab 上再核对一次；把凭证推进 brain-intake | 能读这个项目 | `read_api` 或 `api` 的 token；brain-intake 的可写 deploy key（运行机 `brainbridge` 的 SSH 公钥） |

桥账号不能是机器人账号。可以用项目访问令牌（project access token）当桥账号，它的用户名形如 `project_<id>_bot_<hash>`。

### 2. 标签（人做）

在项目里建好这几个标签：`ai-handoff`、`ai-running`、`ai-handoff-rejected`、`ai-ready-to-merge`、`brain-reflect`。`ai-ready-to-merge` 是运行机做完的 MR：产品验收通过、会话把记录也推上来以后，运行机把 `ai-running` 换成它，在 MR 上说一次可以合并了、请需求的负责人合并；合并以后两个标签都去掉。

### 3. 两个小仓库（人做）

- **brain-authz**：只放 `identities.json`，写谁是谁、谁能回答哪类问题。格式见 brain-kit 的 `examples/identities.json`：`users` 里列出每个要交接的人的 GitLab 用户名，`roles` 里列出 `product`、`builder` 等角色各是谁。机器人账号不能读这个仓库。
- **brain-intake**：放材料包和签好的凭证。开需求时交了文档、或写了「用一段话说需求」的项目要它（那段话也收进材料包）；开了 CI 的项目也要它（CI 从这里读凭证）。

### 4. 改 `.brain/kit.json`（走 MR）

在默认分支上通过 MR 改（机器人改不了 `.brain/`）：

- `bridge_user`：桥账号的用户名。
- `bot_users`：机器人账号的用户名；`bot_emails`：机器人提交用的邮箱（第三部分 `runnerd.json` 的 `git_email` 要在里面）。
- `gitlab.base`、`gitlab.project`、`default_branch`：和 GitLab 上的一致。
- `decision_roles`：可以不写，有默认值。

AI 可以在一个新分支上帮我写好这个改动并提交。推送先问我，MR 我在 GitLab 上开。

验证：MR 合进默认分支后，照第一部分第 7 步的「验证」更新主检出，运行 `python3 <BK>/bin/brain doctor`，没有 FAIL。

### 5. 加固（可选，流程不依赖它们）

- 保护默认分支：要 MR 审批、流水线要过。
- CI：引入 brain-kit 的 `ci/brain-kit.gitlab-ci.yml`，把 `brain-run-check`、`brain-push-rules`、`brain-lint` 设成必过。CI 要一个能读 brain-intake 的 deploy token。**未实测**：CI 还没在真项目上跑过。
- 服务器钩子 `bin/brain-pre-receive`：只有自建或付费版 GitLab 能装。免费版用 CI 里的 `brain-push-rules` 代替，它拦合并、不拦推送。**未实测**。
- `gates/risk-paths`、CODEOWNERS。

## 第三部分：团队运行机（app 团队模式）

### 试用：在 app 里一键建（同一个系统用户）

只想在一台机器上试团队运行机的，不用照下面一步步做。app 的「一键建运行机」替你做第二部分的令牌、标签、身份表和 kit.json 的改动，也做这一部分的第 2 到第 10 步。人只要做三件事：

1. 在这台机器上装好 KiroCrew 和 Brain 交付 app（第一部分第 2 步）。每个项目的默认分支上要已经有 `.brain/kit.json`（第一部分第 6、7 步）。
2. 填表（人做）：app → **设置**，先在最下面「这台机器」勾上「这台机器是团队运行机（服务器）」，点「设成团队运行机」（装好的 app 默认是个人电脑，设置里没有团队模式和一键建运行机）。然后在 **团队模式** 下面的「一键建运行机」里填 GitLab 地址、项目和一个一次性的个人 token，点「开始建」。token 要有 `api` 权限，你在项目里要是 Maintainer 或 Owner。token 只用这一次，不存到任何地方。一台运行机要管几个项目的，「账号」选「机器人账号」，见下面。「巡检所有开着的 MR」默认勾着：运行机还会每一轮看项目里所有开着的 MR，流水线没过、评审意见没回、有冲突的，派 brain-patrol 去修。不想要就去掉这个勾。MR 的流水线在 Jenkins 上跑（Jenkins 用它的 GitLab 插件把结果报给 GitLab）的话，再填 Jenkins 地址（这台机器连得上的那个）、一个只读用户（要有 Overall/Read 和 Job/Read）和它的 token：巡检读没过的构建的日志，一起交给 brain-patrol。GitLab 上的检查没过时构建可能还在跑：巡检等它跑完再读，这个 MR 先不派，最多等 10 分钟；还没跑完就把读到的交出去，并说明构建还没跑完。token 存在 `~/.brain/team-setup/jenkins-token`，只有你能读。不填也行：巡检照样派，只给 Jenkins 的链接，说为什么没读到日志。
3. 合并 MR（人做）：在 GitLab 上合并它开的「运行机：更新 .brain/kit.json」（机器人账号：每个项目一个，都要合并），再回到 app 点「再跑一次」。

验证：每一步都是 ✓，最后一步写着「团队模式开了」。然后照第 11 步试交接一次。

用项目令牌建的运行机，它建的项目令牌一年后到期。到期前两周里，填一个一次性 token 再跑一次：它给令牌换新的值，账号不变，kit.json 不用改。过了期才跑，它会建新的令牌，kit.json 的 MR 要再合并一次。有巡检以前建的运行机，再跑一次时「巡检所有开着的 MR」勾着的话，它开的 kit.json MR 只在 work_sources 加上 mr-patrol：交接照常，合并以后巡检才开始。「从打了标签的 issue 接活」也一样：勾上再跑一次，kit.json MR 只在 work_sources 加上 issue-label，并建好标签 brain；合并以后，项目里 Developer 以上的人给 issue 打上 brain，运行机就开工。默认分支上 aidlc-docs/lean.json 的 checks 要写上 lean verify 跑的命令（构建、测试）：checks 是空的，verify 永远过不了，运行机写好的代码提交不了，所以它先不接，在 issue 上说一次为什么；人把 checks 合并到默认分支以后，下一轮它自己接，不用再打标签。

一台运行机管几个项目（比如一个团队的几个微服务），「账号」选「机器人账号」。先在 GitLab 上准备两样（人做）：一个专门给桥用的用户（比如 `brain-bridge`），加进每个项目，角色 Developer，给它建一个有 `api` 权限的个人令牌；运行机推代码用这台机器的 SSH（它的 SSH key 要能推每个项目），或者再建一个用户（比如 `brain-bot`，每个项目里也是 Developer），给它一个有 `write_repository` 和 `read_user` 权限的个人令牌。这两个用户不能是同一个，也不能是你自己。然后在卡片上「项目」一行写一个，填桥的账号的个人令牌，「运行机怎么推送」选「这台机器的 SSH」或者「机器人账号」并填它的令牌。一次性 token 可以不填：不填就用桥的账号建标签、开 kit.json 的 MR，「产品」和「开发」就都要填，安全和数据跟开发一样。这种方式不建也不换项目令牌。两个个人令牌存在这台机器上（`~/.brain/team-setup/`，只有你能读），以后再跑一次可以空着。个人令牌到期以前，在 GitLab 上给那个用户换一个新令牌，再跑一次填上。几个项目时，「巡检所有开着的 MR」和「从打了标签的 issue 接活」下面每个项目一个勾，分开勾：勾上的项目，它的 kit.json MR 才加上 `mr-patrol` 或 `issue-label`，标签 brain 也只建在勾了 issue 的项目里；没勾的项目不会为这一项开 MR。以前的版本这两项一勾就是所有项目：升级以后卡片上照旧是全勾（或者全不勾）。只想开其中几个项目的，去掉别的勾，再跑一次。加项目、减项目：改「项目」那一栏，再跑一次。加的项目建标签、开 MR，别的项目一点不动。去掉的项目不再交接、不再巡检，它在 GitLab 上的标签、kit.json 和没合并的 MR 都留着。

再跑一次只改它自己管的那几项。三份配置里你自己加的项都留着，比如 `intake_dir`、项目对象里的 `staging`、`signer` 里的 `poll`。它管的项是这些，你手改了，再跑一次会改回去：

- bridge.json：`projects`（写成对象的项目，对象里你加的项留着）、`gitlab` 里的 `base`、`token_env` 和 `token_file`、`identities`、`ledger`、`spool_dir`、`vouchers_dir`、`runner_runs_root`、`publish_vouchers`、`max_inflight`、`max_per_repo`、`pseudonym_salt`、`kirocrew` 里的 `agent` 和 `memory_mode`、`signer` 里的 `type`、`spool_dir`、`allowed_signers` 和 `timeout`、`jenkins`（卡片上没填 Jenkins 就去掉）。旧版本写的 `team_status` 也归它管，再跑一次会去掉。
- runnerd.json：`spool_dir`、`cache_dir`、`runs_root`、`allowed_signers`、`clone_url`、`kit_dir`、`agent_spec`、`git_name`、`git_email`。
- signer.json：`brainkit`、`spool`、`signing_key`、`projects`、`gitlab` 里的 `base`、`token_env` 和 `token_file`、`identities`、`vouchers_dir`、`publish_vouchers`、`ledger`、`pseudonym_salt`、`bridge_users`。

运行机自己写的代码，发布前要由没写过它的上下文评审（Lean 发布第 1 步）。KiroCrew 只让 app 开的会话起 app 自己带的 agent，所以 Brain 交付 app 带了两个只读的评审 agent：`lean-reviewer-opus` 和 `lean-reviewer-k3`，和 Lean 的质量包里的同名、同模型、同样只能读文件。KiroCrew 装 app、更新 app 和启动的时候，把它们写成 `~/.kiro/agents/brain--lean-reviewer-opus.json` 和 `brain--lean-reviewer-k3.json`。运行机的会话把自己写的代码交给它们评审，不用等人；它们改不了代码。一键建运行机的「评审 agent」这一步查它们在不在、是不是只读：不在或者能写，这一步写「等人」，照它说的重启 KiroCrew（或者在 KiroCrew 里更新一下 app）再跑一次。不要自己往 `~/.kiro/agents` 里放 `brain--` 开头的文件：KiroCrew 启动时会删掉 app.json 里没写的。

这样建的运行机里，签名服务、app 和 AI 的会话是同一个系统用户。它跳过了第 2 步分开系统用户的那层保护，签名挡不住 app 自己，所以只能用来试。正式给团队用，照下面第 0 到第 11 步做。它的文件在 `~/.brain/team-setup/`（令牌、密钥、三份配置，巡检的账本 `patrol.sqlite`）和 `~/workplace/brain-team/`（运行的工作区和缓存，巡检的克隆在 `patrol/`）。

### 试用：测试环境报错（staging-errors）

运行机还能看测试环境的日志。合并以后测试环境报错，它开一个 GitLab Issue（标签 `staging-error`），写上报错的日志、部署的提交和合进这个提交的 MR。然后派一个 brain-patrol 会话去修：从默认分支开 `fix/staging-<编号>`，修好、跑测试、推上去，开一个写着 `Closes #<Issue>` 的 MR。审和合并还是人做。它只读文件、调 GitLab，不开端口，不收 webhook。

打开它要改两处（人做）：

1. 项目的 `.brain/kit.json` 里，`work_sources` 写全要开的几样，比如 `"work_sources": ["mr-handoff", "mr-patrol", "staging-errors"]`，合并到默认分支。不要只写 `["staging-errors"]`：没写 `work_sources` 的项目只开交接（`mr-handoff`），写了就只开写上的那几样，交接就关了。运行机页面上，桥的状态下面会对这样的项目提醒一句。
2. 运行机的 bridge.json 里，把这个项目写成对象，加上 `staging`：测试环境的服务把日志写在哪个文件，把部署的提交号写在哪个 VERSION 文件。运行机要能读到这两个文件。

   ```json
   "projects": [{"project": "<组>/<项目>", "staging": {
     "log": {"kind": "file", "path": "/srv/staging/<项目>/app.log"},
     "version": {"kind": "file", "path": "/srv/staging/<项目>/VERSION"}}}]
   ```

日志可以是普通文本，也可以一行一个 JSON。算报错的有三种：ERROR 及以上级别的行、Python 的 Traceback、回了 5xx 的请求。同一种报错只开一个 Issue：Traceback 按异常类型和出错的那层项目代码算，别的按去掉引号里的值、邮箱、路径、编号、长串字符和数字以后的那句话算。一个项目一轮最多开 3 个新 Issue，同时开着的最多 20 个，其余的等着，运行机页面上写着为什么等。次数翻了倍、又多了 10 次以上，才在 Issue 上说一声。第一次读一个日志从它的末尾开始，以前写的不算。

修了 2 次没修好，或者修复 MR 被人关了，它在 Issue 上说一声，交给人。修复 MR 合并、Issue 跟着关掉以后，换了一个部署又报同样的错，它重新打开 Issue 再修；新的部署读到不满 5 分钟时看到的不算，那可能是旧版本最后写的。人关掉的 Issue（不是修复 MR 关的），或者打了 `wontfix` 标签（也认 `ai-skip`）的，它不会再打开，也不修：换了部署又出现，它在 Issue 上说一次。要它接着修，重新打开 Issue、去掉标签：下一个部署里再出现时它接着修。Issue 被删了的，又出现时它开一个新的。修复会话和巡检的会话加起来同时最多 2 个。它的账本是 bridge.json 旁边的 `staging.sqlite`，克隆在运行的工作区旁边的 `staging/`。一键建运行机还不会改这两处，要手改。

从 0.5.1 以前的版本升级的：第一次读旧的 `staging.sqlite` 时，它按现在认报错的方法重算一遍（以前只去掉编号和数字，现在引号里的值、邮箱、路径也去掉）。算出来是同一个报错的几行合成一行，留最早的那个 Issue，次数加在一起。别的几行各有自己的 Issue 的：在修的会话停掉、克隆删掉；Issue 还开着的，它在上面说一声以后在哪个 Issue 里跟进，然后关掉，这样它们不再占「同时开着最多 20 个」的名额。这只做一次；关 Issue 时 GitLab 没答应的，下一轮再关，不会再说第二遍。

#### 打开了 brain-push-rules 的项目

巡检的修复和测试环境报错的修复，都是运行机用机器人的身份（runnerd.json 的 `git_name`、`git_email`）提交的，推到 MR 自己的分支或者 `fix/staging-<编号>` 上。这些都不是 run 分支（`ai/<主人>/<需求号>-<名字>`）。项目的 CI 里带了 brain-kit 的 `brain-push-rules` 的话，它不让机器人在 run 分支以外提交：这样的 MR 流水线过不了，也合并不了。

我们没改 brain-kit 的这条规则，也没让运行机换成别人的身份提交：规则就是要分清哪些提交是机器人做的。运行机做的是看出来、停下、说清楚：

- 巡检：一个 MR 的流水线因为 brain-push-rules 没过，而且 MR 里有运行机的提交，它就不再修这个 MR（还在做的会话停掉），以后也不再派，直到这个 MR 合并或关掉。它在 MR 上说一次：哪几个是运行机的提交，怎么把它们改成你自己的再推上去（`git rebase <第一个>~1 --exec 'git commit --amend --no-edit --reset-author'`，再 `git push --force-with-lease`），不要的话怎么删掉。只 revert 不行：brain-push-rules 查的是 MR 里所有的提交。运行机页面上这个 MR 显示「推送规则不让修」。brain-push-rules 因为人的提交没过的，照常当流水线没过来修。
- 测试环境报错：修复 MR 的流水线因为 brain-push-rules 没过，它在 Issue 上说一次怎么把修复的提交改成你自己的，运行机页面上这一条写着为什么合并不了。之后的流水线过了，这句就去掉。

所以开了 brain-push-rules 的项目，最好别开巡检和测试环境报错（kit.json 的 `work_sources` 里不写 `mr-patrol` 和 `staging-errors`）：每个要推提交的修复都会停在这一步，要人接手。要开，就接受每次由人把运行机的提交改成自己的。

### 试用：状态中转（每个人在自己的 app 里看到全队）

团队服务器把每个项目的状态写进那个项目自己：一个专门的分支 `brain/status`，上面只有 `snapshot.json`。里面是这个项目的需求、运行机在做的交接、MR 巡检和测试环境报错，再加上团队服务器自己的版本和上一轮。能看这个项目代码的人，就能读它的状态；看不了这个项目的人，读不到。权限由 GitLab 管，app 不另外管。内容变了才写，最多 5 分钟写一次。没变化的话，30 分钟写一次，说明它还活着。每个人的 app 用自己的 GitLab 令牌，读自己导入的那些项目的 `brain/status`，在「团队运行机」里合在一起显示全队交付。服务器不开端口，个人的 app 也不连服务器。每个人的「交付总览」列自己的需求和还没人认领的需求，上面的数也只算这些；全队的去「团队运行机」看。

项目开了从 issue 接活的，状态里还有打了标签、还没开工的 issue，和没开工的原因。新版的团队服务器才写这一块：旧版写的状态，新版的 app 当作没有；新版写的，旧版的 app 不显示这一块，别的照常。

「团队运行机」里 MR 巡检和测试环境报错的「在修 n/2」，和团队服务器自己的运行机页面一样，数的是各自占着的 brain-patrol 会话，加起来就是顶上的「brain-patrol 会话在做」。下面没显示在修、却还占着会话的，数字下面写着是什么占着；只列你能读的项目，别的项目里的写「在别的项目里」。0.5.2 的团队服务器写的状态里没有这些数，那就照旧只数下面列着在修的。

`brain/status` 怎么来：团队服务器第一次写一个项目时，用这台机器的 git 推一个没有父提交的分支，里面只有一个 README。它用的是运行机推代码的地址和名字（runnerd.json 的 `clone_url`、`git_name`、`git_email`）。GitLab 的接口只能从已有的分支开新分支，所以这一次要用 git。以后每次写，都是桥的账号通过 GitLab 的接口提交。

这个分支不会触发构建：

- 分支上没有 `.gitlab-ci.yml`，也没有 Jenkinsfile。
- 每个提交的说明都带 `[skip ci] [ci-skip]`。GitLab CI 认这两种写法。Jenkins 的 gitlab-plugin 收到推送时，只看这次推送的最后一个提交：说明里有 `[ci-skip]` 就不构建。任务配置里的「Enable [ci-skip]」默认是开的。
- MR 巡检和交接都不管从这个分支开的、或者开到这个分支的 MR。测试环境报错找「带进这个提交的 MR」时，也跳过这个分支的 MR。

一键建运行机的「状态中转」这一步只检查一件事：每个项目的受保护分支规则有没有挡住桥的账号推 `brain/status`。挡住了，这个项目写「等人」，说是哪条规则，其他步骤照常做完。令牌读不了受保护分支的设置，它会说一声：团队服务器第一次写的时候就知道能不能推，推不了会写在运行机页面上。

人要做的：

1. 已经建好的运行机：升级 app 以后，团队服务器自己开始写，不用重跑一键建运行机。想先查一遍受保护分支，就在设置里再跑一次。
2. 项目里有 `*`、`brain/*` 这种受保护分支规则的，给 `brain/status` 放开，让 Developer 能推；或者不保护它。
3. Jenkins 的 GitLab 触发器关掉了「Enable [ci-skip]」的，把它打开。多分支流水线不用管，因为这个分支没有 Jenkinsfile。
4. 每个人的 app 要有自己的 GitLab 令牌，要有 `read_api` 权限。

以前试过第一版状态中转（一个单独的状态项目）的：

- 当时建的状态项目（默认是第一个项目的组下面的 `brain-status`）、它的成员和项目令牌 `brain-status`，现在都不用了。app 不碰它们。要删，在 GitLab 上自己删；根目录下的 `status-gitlab-token` 也可以删。
- bridge.json 里的 `team_status` 不用了：再跑一次一键建运行机，会重写 bridge.json 并去掉它。
- kit.json 里的 `team_status` 也不用了，留着没关系。想删，开一个 MR 删掉。

验证：

1. 运行机页面上，桥的状态下面写着「状态中转 N 个项目的 brain/status · X 前写的」。哪个项目没写成，下面写着为什么和做什么。
2. 在 GitLab 上打开一个项目的 `brain/status` 分支：第一个提交没有父提交，只有 README；后面的提交是桥的账号写的 `snapshot.json`。这个分支没有跑任何流水线，Jenkins 也没有构建它。
3. 个人电脑的「团队运行机」顶上写着团队服务器的版本和「状态写于 X 前」，每个项目一行。用一个看不了某个项目的人的令牌，看不到那个项目的任何一行。
4. 超过 75 分钟没写新的（两次心跳都没来），那里写着「团队服务器可能停了」。项目还没有 `brain/status` 的仓库，或者有这个分支、还没读到状态的，「团队运行机」照旧从 GitLab 读运行机在做的 MR，旧版本的团队服务器也能用。
5. 开了从 issue 接活的项目，「团队运行机」里有「从 issue 接活」这一块：打了标签、还没开工的 issue 都在，每个写着为什么还没开工。

> **现状，先读：** app 的团队模式还没跑通过。唯一一次试跑（2026-11-16，一台 macOS、一个系统用户）里，签名服务签了交接、运行机建好了工作区，但运行机的会话一直开不起来，原因没查到。Linux、systemd、分开的系统用户、机器人账号推送都还没在真机上跑过。一台运行机管几个项目（机器人账号）也只在假的 GitLab 上测过。
>
> 所以这一部分的每一步都要跑它的验证；第 11 步的试交接是判断它能不能用的唯一办法。

下面的命令大多要 `sudo`。AI 把命令写给我，由我运行，再把输出贴回会话。`sudo -iu brainbot` 用的是 brainbot 的登录环境，这样能找到装在 `~/.local/bin` 下的 `kirocrew`。

### 0. 这台机器要什么

- 一台 Linux 服务器，有 systemd，能连 GitLab、GitHub（装 app）和 KiroCrew 要用的模型服务。
- 管理员能 `sudo`。
- 第二部分已经做完：桥账号的 token、签名服务的 token、机器人账号、brain-authz 仓库都有了，`kit.json` 的 `bridge_user` 已经在默认分支上。

### 1. 这台机器跑过旧运行机的，先停掉它

旧运行机（brain-kit 的 `brain-bridge` 和 `brain-runnerd` 两个服务，加上 KiroCrew 里的 `brain-bridge` app）用的是同样的系统用户、同样的 `/etc/brain-kit`。两个桥同时跑会抢同一个 MR。

```bash
systemctl list-units --all 'brain-*'
getent group brainspool; id brainbot; id brainbridge
ls -la /etc/brain-kit 2>&1
```

有旧运行机的：

1. `sudo systemctl disable --now brain-bridge brain-runnerd`。
2. 在 brainbot 的 KiroCrew 里停用并卸载 `brain-bridge` app（人点）。
3. 备份：`sudo cp -a /etc/brain-kit /etc/brain-kit.bak-$(date +%Y%m%d)`；`/var/lib/brain-bridge` 也一样。
4. 已经有的用户和组，第 2 步跳过对应的那几条。想留旧的签名私钥的，第 5 步跳过生成私钥的那三条（公钥不变，各处的 `allowed_signers` 不用改）。

验证：`systemctl is-active brain-bridge brain-runnerd` 不是 `active`。

### 2. 系统用户和目录（人运行）

运行机上有两个系统用户：`brainbot` 跑 KiroCrew 和 app（桥和 runnerd 都在 app 里），`brainbridge` 只跑签名服务、只有它能读签名私钥。两人共用组 `brainspool`。

```bash
sudo groupadd brainspool
sudo useradd -m -s /bin/bash -G brainspool brainbot
sudo useradd -m -s /bin/bash -G brainspool brainbridge

# 签名服务的收发目录：app 写 requests，签名服务写 signed 和 refused
sudo install -d -o brainbot    -g brainspool -m 2770 /srv/brain-signer/requests
sudo install -d -o brainbridge -g brainspool -m 2750 /srv/brain-signer/signed /srv/brain-signer/refused
sudo install -d -o brainbridge -g brainspool -m 0750 /var/lib/brain-signer

# 配置目录归 root，app 只能读；身份表和签名服务的 brain-intake 归 brainbridge
sudo install -d -o root        -g brainspool -m 0750 /etc/brain-kit
sudo install -d -o brainbridge -g brainspool -m 0750 /srv/brain-authz
sudo install -d -o brainbridge -g brainspool -m 0750 /srv/brain-intake   # 没有 brain-intake 的项目也要建：签名服务的 systemd 配置要它在

# brainbot 自己的目录：工作区放在 ~/workplace 下面（KiroCrew 只让 agent 在这里干活）
sudo -u brainbot mkdir -p /home/brainbot/workplace/runs /home/brainbot/brain-cache /home/brainbot/.brain/team
```

验证：`sudo ls -ld /srv/brain-signer/* /var/lib/brain-signer /etc/brain-kit /srv/brain-authz /srv/brain-intake` 的属主、组、权限和上面一样。

### 3. 给 brainbot 装 KiroCrew 和 app（人做）

1. 以 `brainbot` 的身份装好 KiroCrew 并登录。
2. KiroCrew 的网页只听本机（`127.0.0.1:5476`），不要改成对外开放。从自己的电脑用 SSH 端口转发打开它，本地端口换一个，免得和自己电脑上的 KiroCrew 撞上：
   ```bash
   ssh -L 15476:127.0.0.1:5476 <管理员账号>@<运行机>
   ```
   然后在浏览器打开 `http://localhost:15476`。先确认打开的是运行机的 KiroCrew，不是自己电脑上的。
3. 引擎换成 kas：`sudo -iu brainbot kirocrew config set agent.acp_backend kas`，然后重启 brainbot 的 KiroCrew。
4. 照第一部分第 2 步，在运行机的 KiroCrew 里装 Brain 交付 app、确认信任、启用并同意 sessionApproval。
5. 运行机不用装 `brain-bridge` app。

验证：

```bash
sudo -u brainbot cat /home/brainbot/.kiro/crew/apps/brain/VERSIONS
sudo -iu brainbot kirocrew config get agent.acp_backend    # 最后一行是 kas
sudo -u brainbot ls /home/brainbot/.kiro/agents/ | grep '^brain--.*\.json$'   # brain--lean-reviewer-k3.json 和 brain--lean-reviewer-opus.json
```

最后一条列出的两个文件，是 app 带的两个只读评审 agent，KiroCrew 装 app、更新 app 和启动时写进去。运行机自己写的代码，发布前交给它们评审（Lean 发布第 1 步要一个没写过代码的上下文来审），不用等人。少了的话，重启 brainbot 的 KiroCrew 再看一次。

### 4. 放两份代码（人运行）

签名服务要一份 brain-kit 和一份 app 源码，放在 brainbot 改不了的地方（app 自带的 `signer/` 归 brainbot，签名服务不能用它）。版本要和 app 自带的一致：用第 3 步验证里 VERSIONS 的 `app` 和 `brain-kit` 两行的 commit。

```bash
sudo git clone https://github.com/ai-for-developer/brain-kit /opt/brain-kit
sudo git -C /opt/brain-kit checkout <VERSIONS 里 brain-kit 那行的 commit>
sudo git clone https://github.com/ai-for-developer/kirocrew-app-brain /opt/kirocrew-app-brain
sudo git -C /opt/kirocrew-app-brain checkout <VERSIONS 里 app 那行的 commit>
```

`/opt` 下这两份归 root，`brainbot` 不能写。

验证：`sudo git -C /opt/brain-kit rev-parse HEAD` 和 `sudo git -C /opt/kirocrew-app-brain rev-parse HEAD` 分别等于 VERSIONS 里的两个 commit。

以后运行机的 app 升级了，这两份要一起换到新 VERSIONS 里的 commit，再重启签名服务。顺序照第 12 步：先换签名服务，再换 app。

### 5. 签名密钥（人运行）

```bash
sudo -u brainbridge ssh-keygen -q -t ed25519 -N "" -f /home/brainbridge/bridge_ed25519 -C brain-bridge
sudo install -o brainbridge -g brainbridge -m 0600 /home/brainbridge/bridge_ed25519 /etc/brain-kit/bridge_ed25519
sudo -u brainbridge rm /home/brainbridge/bridge_ed25519
echo "brain-bridge namespaces=\"brain-voucher\" $(sudo -u brainbridge cut -d' ' -f1,2 /home/brainbridge/bridge_ed25519.pub)" \
  | sudo tee /etc/brain-kit/allowed_signers >/dev/null
sudo chmod 0644 /etc/brain-kit/allowed_signers
```

私钥只在 `/etc/brain-kit/bridge_ed25519`，只有 `brainbridge` 能读。公钥那一行（`allowed_signers`）可以随便拷：开了 CI 的项目，也要把它加进 CI 用的那份 brain-kit 的 `deploy/allowed_signers`。

验证：`sudo ls -l /etc/brain-kit/bridge_ed25519` 是 `-rw------- brainbridge`；`sudo cat /etc/brain-kit/allowed_signers` 是一行，以 `brain-bridge namespaces="brain-voucher" ssh-ed25519 AAAA` 开头。

### 6. token、身份表和 SSH key（人做）

1. 桥账号的 token 存进 `/home/brainbot/.brain/bridge-gitlab-token`，属主 `brainbot`，权限 `600`。
2. 签名服务的 token 写进 `/etc/brain-kit/signer.env`，一行 `SIGNER_GITLAB_TOKEN=<token>`，属主 root，权限 `600`。
3. 生成一个随机串当 `pseudonym_salt`（例如 `openssl rand -hex 16`），第 7 步的两份配置要用同一个。它也算机密，不要贴进聊天。
4. 把 brain-authz 仓库 clone 进 `/srv/brain-authz`（以 `brainbridge`），确认 `identities.json` 对 `brainbot` 可读、不可写：
   `sudo -u brainbot test -r /srv/brain-authz/identities.json && sudo -u brainbot test ! -w /srv/brain-authz/identities.json && echo ok`
5. 机器人账号的 SSH key：
   ```bash
   sudo -u brainbot mkdir -p -m 700 /home/brainbot/.ssh
   sudo -u brainbot ssh-keygen -q -t ed25519 -N "" -f /home/brainbot/.ssh/id_ed25519 -C brainbot
   sudo -u brainbot cat /home/brainbot/.ssh/id_ed25519.pub
   ```
   把输出的公钥加到 GitLab 的机器人账号上。
6. 有 brain-intake 的项目：
   ```bash
   sudo -u brainbridge mkdir -p -m 700 /home/brainbridge/.ssh
   sudo -u brainbridge ssh-keygen -q -t ed25519 -N "" -f /home/brainbridge/.ssh/id_ed25519 -C brain-signer
   sudo -u brainbridge cat /home/brainbridge/.ssh/id_ed25519.pub
   ```
   把这个公钥加成 brain-intake 的**可写** deploy key。然后：
   ```bash
   sudo -u brainbridge git clone ssh://git@<GitLab 主机>/<组>/brain-intake.git /srv/brain-intake
   sudo -u brainbridge git -C /srv/brain-intake config user.name brain-signer
   sudo -u brainbridge git -C /srv/brain-intake config user.email <签名服务提交用的邮箱>
   sudo -u brainbot git clone ssh://git@<GitLab 主机>/<组>/brain-intake.git /home/brainbot/brain-intake
   ```
   签名服务推不上 brain-intake 时，每个交接都会一直等着，所以这一条不能省。

验证：

```bash
sudo -u brainbot git ls-remote ssh://git@<GitLab 主机>/<组>/<项目>.git HEAD          # 机器人账号能读项目
sudo -u brainbridge git -C /srv/brain-intake push --dry-run origin HEAD 2>&1 | tail -2   # 有 brain-intake 时：能推
```

### 7. 写三份配置（AI 起草，人放到位）

AI 按下面的模板起草，把 `<…>` 换成真实值，把文件内容给我；我用 sudo 放到位。token、私钥、`pseudonym_salt` 的值留着 `<…>`，由我自己填。

**`/etc/brain-kit/bridge.json`**（root:brainspool，0640）：

```json
{
  "projects": ["<组>/<项目>"],
  "gitlab": {"base": "https://<GitLab 主机>", "token_file": "/home/brainbot/.brain/bridge-gitlab-token"},
  "identities": "/srv/brain-authz/identities.json",
  "ledger": "/home/brainbot/.brain/team/bridge-ledger.sqlite",
  "spool_dir": "/home/brainbot/.brain/team/spool",
  "vouchers_dir": "/home/brainbot/.brain/team/vouchers",
  "runner_runs_root": "/home/brainbot/workplace/runs",
  "intake_dir": "/home/brainbot/brain-intake",
  "kirocrew": {"agent": "brain-run", "memory_mode": "incognito"},
  "pseudonym_salt": "<随机串>",
  "signer": {"type": "spool", "spool_dir": "/srv/brain-signer", "allowed_signers": "/etc/brain-kit/allowed_signers"}
}
```

不能写 `signing_key`：写了团队模式不会启动。没有 brain-intake 的项目删掉 `intake_dir` 那一行。

**`/etc/brain-kit/runnerd.json`**（root:brainspool，0640）：

```json
{
  "spool_dir": "/home/brainbot/.brain/team/spool",
  "cache_dir": "/home/brainbot/brain-cache",
  "runs_root": "/home/brainbot/workplace/runs",
  "allowed_signers": "/etc/brain-kit/allowed_signers",
  "clone_url": "ssh://git@<GitLab 主机>/{project}.git",
  "intake_dir": "/home/brainbot/brain-intake",
  "agent_spec": "/home/brainbot/.kiro/agents/brain-run.json",
  "git_name": "<机器人账号的显示名>",
  "git_email": "<kit.json bot_emails 里的那个邮箱>"
}
```

`spool_dir` 要和 bridge.json 的一样。没有 brain-intake 的项目删掉 `intake_dir` 那一行。

**`/etc/brain-kit/signer.json`**（brainbridge:brainbridge，0600），格式见 `/opt/kirocrew-app-brain/signer/signer.example.json`：

```json
{
  "brainkit": "/opt/brain-kit",
  "spool": "/srv/brain-signer",
  "signing_key": "/etc/brain-kit/bridge_ed25519",
  "projects": ["<组>/<项目>"],
  "gitlab": {"base": "https://<GitLab 主机>", "token_env": "SIGNER_GITLAB_TOKEN"},
  "identities": "/srv/brain-authz/identities.json",
  "intake_dir": "/srv/brain-intake",
  "vouchers_dir": "/srv/brain-intake/vouchers",
  "publish_vouchers": true,
  "ledger": "/var/lib/brain-signer/ledger.sqlite",
  "pseudonym_salt": "<和 bridge.json 同一个>",
  "bridge_users": ["<桥账号的用户名>"]
}
```

没有 brain-intake 的项目：`publish_vouchers` 改成 `false`，删掉 `intake_dir` 那一行，`vouchers_dir` 改成 `/var/lib/brain-signer/vouchers`（签名服务一定要有这一项）。

验证（人运行）：

```bash
sudo -u brainbot test -r /etc/brain-kit/bridge.json  && sudo -u brainbot test ! -w /etc/brain-kit/bridge.json  && echo bridge.json-ok
sudo -u brainbot test -r /etc/brain-kit/runnerd.json && sudo -u brainbot test ! -w /etc/brain-kit/runnerd.json && echo runnerd.json-ok
sudo -u brainbot test ! -r /etc/brain-kit/signer.json && echo signer.json-hidden
sudo -u brainbot test ! -w /srv/brain-signer/signed && echo signed-not-writable
```

四行都要输出。

### 8. 写 brain-run agent（人运行）

```bash
sudo -u brainbot python3 -I /home/brainbot/.kiro/crew/apps/brain/vendor/brain-kit/bin/brain-runnerd \
  --config /etc/brain-kit/runnerd.json setup; echo "exit=$?"
```

验证：第一行以 `wrote /home/brainbot/.kiro/agents/brain-run.json` 开头，或者是 `... is up to date`；没有 `problem:` 行；`exit=0`。

### 9. 启动签名服务（人运行）

先空跑一轮，报错就停下，不要装服务：

```bash
sudo -u brainbridge env $(sudo cat /etc/brain-kit/signer.env) \
  python3 -I /opt/kirocrew-app-brain/signer/brain_signer.py --config /etc/brain-kit/signer.json once
```

没有报错再装成服务：

```bash
sudo cp /opt/kirocrew-app-brain/signer/brain-signer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now brain-signer
```

**未实测**：这个 systemd 服务还没在真机上装过。

验证：`systemctl status brain-signer` 是 `active (running)`；`sudo journalctl -u brain-signer -n 50` 没有报错。

### 10. 打开团队模式（人做）

在运行机的 KiroCrew 里打开 Brain 交付 → **设置**。先在最下面「这台机器」勾上「这台机器是团队运行机（服务器）」，点「设成团队运行机」：装好的 app 默认是个人电脑，设置里没有团队模式。然后在 **团队模式** 里：

1. 勾上「这台机器是团队运行机」。
2. 「桥的配置文件」填 `/etc/brain-kit/bridge.json`，「运行机的配置文件」填 `/etc/brain-kit/runnerd.json`。
3. 「本机试用：签名器和 app 用同一个系统用户」**不要勾**。
4. 保存。

验证：设置页顶上的「团队运行机」卡是正常的，没有红字。常见的红字和原因：

- 「桥的配置文件里写了私钥」：bridge.json 里删掉 `signing_key`。
- 「签名服务的答复这个 app 也能写：签名服务就拦不住它了」：第 2 步 `signed`、`refused` 的属主或权限不对。
- 「运行机的配置文件这个 app 也能改」：第 7 步配置文件的属主或权限不对。
- 「桥连不上 GitLab：没写 GitLab 的地址，或者找不到桥的 GitLab 令牌」：第 6 步第 1 条。

原话在「看运行机」里的「细节」下面。

kit.json 或身份表以后改了，要重启签名服务（`sudo systemctl restart brain-signer`），并在 app 里把团队模式关掉再打开。每次保存设置，团队模式都会重新开始：旧的那一路轮次先停下，新的马上跑一轮，以后还是每 60 秒一轮（bridge.json 的 `interval`）。0.5.3 以前，团队模式开着时保存一次设置会多出一路轮次，变成每 30 秒一轮；0.5.3 起不会了。

### 11. 试交接一次（判断能不能用）

1. 在一个组员的电脑上，挑一个试用需求，做到有交接记录，在 **需求现状** 点 **交接给运行机** → **确定交接**（人点）。
2. 在运行机上看：
   - MR 上出现「AI 已接手」的评论，标签从 `ai-handoff` 变成 `ai-running`。
   - `/srv/brain-signer/signed` 里出现签好的文件。
   - `/home/brainbot/workplace/runs` 下面出现这个需求的工作区。
   - 运行机 KiroCrew 的侧边栏「Brain 运行机」下面出现一个 `brain-run` 会话，并且它开始干活。
3. 前三条都有、第四条没有（会话开不起来）：这就是上次试跑卡住的地方。停下，收集下面这些给维护者，不要自己改代码：
   - `sudo -iu brainbot kirocrew logs -n 200`
   - 设置页「看运行机」里最后几十行记录。
   - `sudo journalctl -u brain-signer -n 100`

   然后在 app 里把团队模式关掉。这个需求的主人在自己电脑上接着做。

验证：四条都有，会话里的 AI 读了交接记录并接着做，MR 上能看到它的问题和进度。

KiroCrew 重启（或者模型那边断了）会把会话正在跑的那一轮打断，它写的改动留在工作区里，没提交也没推送。桥每一轮都看每个派出去的会话：停着、没在跑、没有排队的消息、这一轮是被打断的（KiroCrew 没给它写结尾）、MR 上也没有问题在等人回答，又过了 bridge.json 的 `stall_minutes`（默认 15 分钟）还没有新动静，就给会话发一条「会话中断：…」，让它提交、推送，接着做；再被打断就再发一次；还不行，MR 上会说「…的会话停住了」，运行机页上这个运行下面也写着，直到它再推送，这时请管运行机的人到 KiroCrew 里看一下这个会话。自己结束了这一轮的会话（比如「工作区和合同 … 对不上，不继续」）、有人在 app 或 KiroCrew 里按了停止的会话、签名服务拒签了最新那个问题的回答的运行，都在等人，桥不会让它们接着做。会话在跑、却这么久没有新动静的，MR 上也会说一声（brain-kit 自己的看门狗）。`stall_minutes` 写 0 就都不看。

### 12. 升级运行机的 app

先换签名服务，再换桥。0.5.4 起（brain-kit 81380eb），桥在 MR 上贴的问题编号是 `Q-<n>`，不再是 `D-<n>`（`D-<n>` 只指 run 的 decisions.md 里的一行）。顺序反了会出错：

- 旧的签名服务不认 `Q-<n>`，会拒签对它的回答。
- 旧的桥进程和新的用同一个 ledger 时，会把 `Q-<n>` 问题当成坏记录作废。

照第 0 到第 11 步建的运行机：

1. 读新版本的 VERSIONS：GitHub 上 kirocrew-app-brain 的 `release` 分支里的 `VERSIONS` 文件。记下 `app` 和 `brain-kit` 两行的 commit。
2. 先换签名服务（人运行）：照第 4 步，把 `/opt/brain-kit` 和 `/opt/kirocrew-app-brain` 换到这两个 commit，再运行 `sudo systemctl restart brain-signer`。
3. 停掉用同一个 ledger 的所有桥进程：在 app 的 **设置** 里关掉团队模式，保存（人点）。这台机器上还有旧运行机的 `brain-bridge` 服务在跑的，照第 1 步停掉它。
4. 在运行机的 KiroCrew 里更新 Brain 交付 app（人点）。然后在 **设置** 里打开团队模式，保存。

验证：

- `sudo git -C /opt/brain-kit rev-parse HEAD` 等于 `sudo -u brainbot cat /home/brainbot/.kiro/crew/apps/brain/VERSIONS` 里 `brain-kit` 那行的 commit，`sudo git -C /opt/kirocrew-app-brain rev-parse HEAD` 等于 `app` 那行的 commit。
- `sudo journalctl -u brain-signer -n 50` 里没有 `too old`。
- 设置页顶上的「团队运行机」卡没有红字。

用「一键建运行机」建的运行机：签名服务是 app 自己起的。在 KiroCrew 里更新 app 就行：团队模式开始时，app 先用新版本重启签名服务，再跑第一轮。

每个人电脑上的 app 也要更新：0.5.3 和更早的版本认不出 `Q-<n>` 问题，需求页和待办里都不会有它，只能去 MR 上回答。

升级以后怎么回答：

- 已经发出的 `D-<n>` 问题不重发，照旧回 `answer D-<n> …`。
- 新问题是 `Q-<n>`，和旧问题共用一个计数。照老习惯回 `answer D-12 …` 也算 Q-12 的回答。
- 只有一种例外：D-12 正好是这个验收问题的待审项时，这条回复不算，桥会请你改成 `answer Q-12 …`。

## 第四部分：最后核对

- [ ] 每台个人电脑：app 来源是 GitHub 的 `release` 分支；**设置** 里 brain-kit 卡和 KiroCrew 卡都是绿的；引擎是 `kas`；关卡验证拦下了。
- [ ] 每个仓库：kit 文件升级的 MR 已合进默认分支，`brain doctor` 没有 FAIL。
- [ ] 每个旧需求：在 app 里的状态和第一部分第 8 步的表对得上；做到一半的已经在 app 开的会话里接着做。
- [ ] 旧的 `~/.kiro/agents/brain-dev.json` 已删；个人电脑上没有 `brain-bridge` app。
- [ ] 要交接的项目：桥账号是 Developer；五个标签都在；`kit.json` 里有 `bridge_user`、`bot_users`、`bot_emails`。
- [ ] 运行机：旧运行机已停；签名服务在跑；「团队运行机」卡正常；试交接四条都有。这一条没过，就先别把需求交接给运行机，各自在本机做完、本机合并。
