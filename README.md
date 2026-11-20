# Brain 交付（KiroCrew app）

在 KiroCrew 里用 [brain-kit](https://github.com/ai-for-developer/brain-kit) 交付需求：认领需求，材料过五道关，在 brain-dev-crew
会话里按 [AI-DLC Lean](https://github.com/ai-for-developer/aidlc-lean) 开发，交给团队运行机，跟进问题，维护仓库里的 Brain。

这个仓库本身就是 KiroCrew 的一个应用来源：`main` 是源码，`release` 分支是装好就能用的 app（界面已经构建好，brain-kit 和
Lean 也在里面）。安装时不用 node，也不用另外装 brain-kit。各部分的版本见 `release` 分支的 `VERSIONS`。

以前没装 app、直接在 KiroCrew 会话里用 brain-kit 的项目，照 [MIGRATE.md](MIGRATE.md) 迁过来：它也写了怎么把以前的需求接进
app、怎么配团队运行机。

## 1. 前提

1. KiroCrew 0.11 或更新，已经装好并登录。
2. macOS 或 Linux。Windows：用 WSL 里的 KiroCrew。
3. 这台机器上有 `git` 和 `python3`（3.8 或更新）。macOS 上没有的话，运行 `xcode-select --install`。

## 2. 安装

1. KiroCrew 侧边栏 → **Apps** → **Discover（发现）**。
2. 点 Discover 右上角的齿轮（Sources / 来源）→ **管理应用来源（Manage app sources）** → **添加注册表（Add Registry）**。
3. 仓库填 `https://github.com/ai-for-developer/kirocrew-app-brain`，分支填 **`release`**（不是 main），保存。显示名称可以不填。
4. 回到 Discover，找到 **Brain 交付** → **安装（Install）**。找不到就点 **同步应用（Sync apps）**；
   连 GitHub 慢的话第一次同步可能失败（KiroCrew 拉仓库只等 60 秒），再点一次。
   - 会先弹信任确认：**这个 app 和 KiroCrew 同等权限运行**（它的代码跑在 KiroCrew 里，能读写你的文件、运行命令）。
     只装你信任的。卸载会收回这个信任，再装要重新确认。
   - 安装只是从 GitHub 拷文件，不跑 npm，也不跑 pip。KiroCrew 装之前会先连它的官方应用目录（apps.crew.kiro.dev），
     确认没有同名的官方 app；连不上就拒绝安装，过一会儿再试。
5. 点 **启用（Enable）**。会请你同意 sessionApproval：app 可以让它自己开的会话不用逐个批准工具调用。

升级：有新版时，app 顶上 **Brain 交付** 旁边会写「有新版 v…」。到 KiroCrew 的 **Apps** → **Discover（发现）** →
**更新（Updates）**，在 **Brain 交付** 那一行点 **更新（Update）**。数据和设置都留着。这个提示是 KiroCrew 给的：
只有按上面从 GitHub 的 release 分支装的 app 才有；从本机目录装的，照上面的步骤重装一次。

## 3. 第一次打开

1. 侧边栏打开 **Brain 交付** → **设置**。
2. 看 **KiroCrew** 那张卡：不是绿色的行，照它的「做什么」点那一行的按钮（扳手图标）。常见三条：
   - 没有 brain-dev-crew：点「装上 brain-dev-crew」。app 开的会话都用这个 agent，它带着 Lean 的拦截。它写
     `~/.kiro/agents/brain-dev-crew.json`。不用以前的 brain-dev：仓库里的 `.kiro/agents/brain-dev.md` 会盖住它，拦截就没了。
   - 缺 Lean 的拦截：点「补上 Lean 的拦截」。只补缺的，已有的文件先留一份备份。修完要开新会话。
   - 引擎不是 kas：点「把引擎换成 kas」。它会先问你：这会改 KiroCrew 的全局设置，所有项目的会话都换成 kas。
     可能要重启 KiroCrew。
   - 「这个仓库里叫 brain-dev 的会话没有 Lean 的拦截」只是提醒：以前开的会话，在需求现状里点「重开一个会话」换成新的。
3. **brain-kit** 那张卡应该是绿的。「brain-kit 在哪」是 app 自带的那份（路径以 `apps/brain/vendor/brain-kit` 结尾），不用改。
4. 点右上的 **导入仓库**，导入你的项目仓库。
5. GitLab（仓库用 GitLab 时）：
   - 用户名：导入时检查里「你是谁」那一行（以后在 **设置** 的「仓库」里也能改），填你的 GitLab 用户名。
   - token：在 GitLab 网页上建一个有 `api` 权限的个人 token。自己把它存进 `~/.brain/gitlab-token`，再运行
     `chmod 600 ~/.brain/gitlab-token`（别人能读的话，brain-kit 不会读它）。不要把 token 贴进聊天。

## 4. 验证关卡（必须做）

「需求没批不能写代码」这类关卡，靠 brain-dev-crew 会话里的 Lean hook。hook 没跑的话，AI 什么都能写，表面上一切正常。
所以第一次一定要试：

1. 在 **开需求** 里开一个试用需求，点 **开工**。app 会打开一个 brain-dev-crew 会话。
2. 需求还没批的时候，在这个会话里发：
   `这是关卡测试：请直接用写文件工具建 src/hello.py（一行 print 就行）。被拦下的话，把拦截信息原样告诉我。`
3. 对的结果：AI 被拦下，回复里有 `[AI-DLC Lean] blocked`（后面括号里是哪道关，比如 requirements）。
4. 文件建成了：关卡没生效，先别用。回 **设置**，把检查修到全绿；再到这个需求的 **需求现状** 点「重开一个会话」
   （app 会开一个新会话，旧的留着、记录还能看），在新会话里再试一次。会话卡住了（打断以后一直不动）也是点它。
5. 批准只认你自己单独打的一行口令（比如 `approve REQUIREMENTS 8cf2cbce`）。说「行，开干」不算。

## 5. 让 KiroCrew 会话帮你装

装完、启用以后，可以开一个 KiroCrew 会话，把下面这段整段贴进去：

```text
请帮我检查这台机器能不能用 KiroCrew 的「Brain 交付」app（名字 brain）。每一步告诉我你运行了什么、结果是什么。

你可以自己做的：
1. 查版本：kirocrew --version（要 0.11 或更新；找不到 kirocrew 就用 ~/.local/bin/kirocrew）、git --version、
   python3 --version（要 3.8 或更新；macOS 上再看 /usr/bin/python3 --version）。
2. 查引擎：kirocrew config get agent.acp_backend，应该是 kas。
3. 看 app 和它自带的 brain-kit：cat ~/.kiro/crew/apps/brain/VERSIONS，
   ls -l ~/.kiro/crew/apps/brain/vendor/brain-kit/bin/brain。没有的话，说明 app 还没装，停下来告诉我。
4. 装 app 会话用的 agent：python3 ~/.kiro/crew/apps/brain/vendor/brain-kit/bin/brain crew-agent
   （这个版本的 app 自带的 brain-kit 写 ~/.kiro/agents/brain-dev-crew.json，带 Lean 的三个 hook；也可以让我在 app 的「设置」里点「装上 brain-dev-crew」）。
5. 问我要用哪个项目仓库。仓库里有 .brain/kit.json 的话，在仓库目录里运行
   python3 ~/.kiro/crew/apps/brain/vendor/brain-kit/bin/brain doctor，把 FAIL 和 WARN 原样列给我，并说怎么修。
   没有的话，告诉我先在 app 里「导入仓库」。
6. 看 ~/.brain/gitlab-token 在不在、权限是不是 600（ls -l）。不要打开它，不要打印它的内容。

下面这些你必须停下来，告诉我怎么做，由我自己做：
- KiroCrew 里的「安装」「启用」、信任确认和任何权限弹窗；
- 任何要 sudo 或管理员密码的命令（比如装 git、python3）；
- 改 KiroCrew 的设置（比如 kirocrew config set agent.acp_backend kas）：先说明要改什么，等我同意；
- 在 GitLab 网页上建 token、加 SSH key；
- 把 token 存进 ~/.brain/gitlab-token：我自己来，不要让我把 token 贴进聊天。
沙箱拒绝某条命令时，不要换办法绕过去：告诉我，我去 app 的「设置」页点修复按钮。
最后给我一个清单：哪些已经好了，哪些要我做。
```

## 6. 交接给运行机

本地做完一段、要交给团队的运行机接着做时：

1. 确认会话里的 AI 写好了交接记录（`aidlc-state.md` 的 Status 和 `### Next`：做到哪了、运行机接着做什么），改动也都提交了。
   AI 还在做事的时候不能交接。
2. 打开这个需求的 **需求现状**，在「交接」那一栏点 **交接给运行机**。看清它要做的三件事，再点 **确定交接**：
   提交交接记录、把需求的分支推到远端、在 MR 上加交接标签。一般一两分钟。
3. 看结果：
   - 「交出去了」：运行机接着做。它有问题会发在 MR 上，你在 MR 上回答（需求现状里也能答）。
   - 别的结果：页面写了为什么、该做什么（比如交接记录没写好、还有没提交的改动、kit 文件旧了）。照着改好，再点一次。
     点两次没关系。
4. 只在本机做的仓库（没有 GitLab 或没有远端）没有这一步：做完在本机合并。

按钮用不了时才用终端：在需求的工作目录里运行 `python3 <brain-kit 的位置>/bin/brain handoff`。`brain` 默认不在 PATH 上，
要写全路径。`<brain-kit 的位置>` 是 **设置** 里「brain-kit 在哪」那一栏（app 自带的那份以 `apps/brain/vendor/brain-kit`
结尾）。也可以在 shell 的配置里加一行 `alias brain='python3 <brain-kit 的位置>/bin/brain'`。

## 还没实测的

- 从这个 GitHub 来源安装、启用弹窗（信任和 sessionApproval）：只在一个隔离的本机 KiroCrew 里试过从本地装，没在真机上走过。
- Linux 和 WSL：没试过。
- 第 4 节的关卡在 app 开的 brain-dev-crew 会话里：没在真机上试过，所以必须自己验证。
- 第 5 节那段话：没试过。KiroCrew 会话的沙箱可能不让它读 `~/.kiro/crew` 或写 `~/.kiro/agents`，那就去「设置」页点修复按钮。

## 许可

MIT No Attribution（见 `LICENSE`）。这个 app 最初从 aws-samples 的示例 app 改出来：
[sample-ai-for-software-engineering-on-aws](https://github.com/aws-samples/sample-ai-for-software-engineering-on-aws)，
MIT No Attribution，Copyright Amazon.com, Inc. or its affiliates。
