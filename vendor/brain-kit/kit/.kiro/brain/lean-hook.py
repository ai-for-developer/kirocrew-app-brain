#!/usr/bin/env python3
"""KiroCrew -> AI-DLC Lean hook adapter (installed by brain-kit; Lean itself is unchanged).

  python3 .kiro/brain/lean-hook.py <event>
      a local KiroCrew session (crew/brain-dev-crew.json, `brain crew-agent`): the project's own copy
  python3 .kiro/brain/lean-hook.py --observe UserPromptSubmit
      a local Kiro CLI session (.kiro/hooks/brain-review-turn.json, next to Lean's own workspace hooks): the review
      sheet's record only; Lean is not run, and it exits 0 whatever happens
  /usr/bin/python3 -I /opt/brain-kit/kit/.kiro/brain/lean-hook.py --runner /etc/brain-kit/runnerd.json <event>
      the team runner (remote/agents/brain-run.json): the installed brain-kit's copy, never the workspace's (the run
      could change that one). The file after --runner is runnerd's config: spool_dir, runs_root, allowed_signers, and
      hook_path, the PATH for git and ssh-keygen here (default /usr/bin:/bin, not the session's).

KiroCrew does not read a project's .kiro/hooks/ (v3 workspace hooks). It fires the hooks written in an agent's spec
instead, in the session's workspace, with the session id as `session_id` (seen 2026-11-12), or as `acp_session_id`
/ $KIRO_SESSION_ID. Lean reads `session_id`. This fills it in when it is missing and runs Lean's own hook (the project's .kiro/aidlc-lean/aidlc_lean.py, in the
nearest folder at or above the hook's cwd that has one), its output and exit code going straight through (exit 2 on
PreToolUse blocks the tool). Lean runs in this process from the bytes the kit check below just read and compared, not
from a second read of the file. Without Lean nothing can be checked, so PreToolUse is refused.

The kit's own files (KIT_PATHS: .kiro/brain/, .kiro/agents/, .kiro/hooks/, .kiro/aidlc-lean/, .brain/kit.json,
gates/; on the team runner also RUNNER_KIT_PATHS: .kiro/steering/, .kiro/skills/) decide what a session may do. On
every call, before anything else, they must be in the working tree exactly as in the reference commit: on the team runner the hand-over commit (the sha in the bridge-signed hand-over request
runnerd prepared this workspace from, <spool_dir>/requests/<RUN-ID>.json, verified with allowed_signers), in a local
session HEAD. A file there changed, added or deleted, a symlink or a hard link: PreToolUse is refused (exit 2), the
other events do not reach Lean (approvals in that message are not recorded), and the session is told that a person
has to restore the files. The same when this cannot be checked (fail closed). Whatever made the change (a write
tool, any shell command, git), the next call sees it. An OS's or an editor's leftover that git ignores (.DS_Store,
a vim swap file: JUNK_NAMES) is no change. A file-write tool call whose target resolves under KIT_PATHS is
refused up front as well; where Lean refuses such writes itself (its .kiro/hooks/, .kiro/aidlc-lean/,
.kiro/agents/lean-*) the call goes to Lean, which records it.

One write does not go to Lean: the run's own question, runs/<KEY>/park.json (park_target). It is how a run reaches a
person, so it must be writable in every approval state (before REQUIREMENTS, before DESIGN, with PLAN pending, after
a rejection), while Lean gates every file outside aidlc-docs/, .kiro/ and free_paths like product code. Only a
file-write tool whose single target is exactly <runs_dir>/<KEY>/park.json of this branch's run is let through
(however the path spells it: ./runs/..., runs//..., or absolute through a symlinked folder above the workspace): KEY
from the branch name <prefix>/<owner>/<KEY>-<slug>, confirmed by <runs_dir>/<KEY>/run.json naming this branch (or,
when the branch name has no key, the one run.json that names it). Anything else, and anything this cannot tell
(another key, run.json, any other runs/ file, '..', a symlink in the workspace or a hard link, a path outside the
workspace, a shell command, an unknown tool or tool input), goes to Lean unchanged. run.json itself is guarded against
the bot by the pre-receive hook and CI, not here. runs/<KEY>/MR.md needs no allowance: `brain mr-desc --write` writes
it through the shell, and Lean's shell guard does not gate that command.

Lean takes a prompt with `<original_user_request>` anywhere in it for a host message, not a person's turn (no
approval or rejection read from it, the turn not counted). Text the bridge forwards (CI logs, MR notes) may contain
that string, so it is escaped (HOST_MARK_SAFE) in the prompt Lean gets, and alike in the prompts a delegation tool
call hands to subagents: Lean matches a subagent's prompt to its dispatch by hash.

The review sheet's record (review_turn.py) reads only what the person typed in a turn (person_text: KiroCrew puts a
replay of the conversation before its current-request header, and blocks of its own after it). Lean gets the whole
prompt, as before.
"""
import fnmatch
import hashlib
import json
import os
import re
import stat
import subprocess
import sys

# brain doctor, brain handoff and brain-runnerd check this number (2: the park.json allowance; 3: the kit-file
# check, runner mode, the escaped host marker; 4: the review sheet's record in local sessions, --observe). Bump it
# when the adapter changes in a way the team runner or brain doctor depends on.
ADAPTER_VERSION = 4

# The kit's own files (brainkit.pre_receive.KIT_PATHS: bot commits may not change them either).
KIT_PATHS = (".kiro/brain/", ".kiro/agents/", ".kiro/hooks/", ".kiro/aidlc-lean/", ".brain/kit.json", "gates/")
# Kit files checked on the team runner only: every session reads them, and the run may not change them, but people
# change their own steering and skills while they work, so a local session is not frozen over an uncommitted skill
# (bot commits may not change them either way: pre_receive.KIT_PATHS is both lists).
RUNNER_KIT_PATHS = (".kiro/steering/", ".kiro/skills/")
# Those of them Lean's own write guard refuses (KIT_PATHS in aidlc_lean.py): a write tool call there goes to Lean.
LEAN_KIT_PATHS = (".kiro/hooks/", ".kiro/aidlc-lean/", ".kiro/agents/lean-")
# Lean's tool classes (SHELL_TOOLS, READ_TOOL_RE in aidlc_lean.py): any other tool given a path writes a file.
SHELL_TOOLS = frozenset(("execute_bash", "execute_pwsh", "execute_cmd", "shell", "bash", "run_command"))
READ_TOOL_RE = re.compile(r"(read|list|glob|grep|search|fetch|view|thinking|todo|report|load|disclose"
                          r"|subagent_response)", re.I)
# Lean's HOST_PROMPT_RE matches this anywhere in a prompt.
HOST_MARK = "<original_user_request>"
HOST_MARK_SAFE = "&lt;original_user_request&gt;"
# KiroCrew's prompt hook gets the whole turn KiroCrew assembled (kiro_crew/context_assembly/turn_message.py): a session
# block, a replay of the conversation so far, then this header and what the person typed. KiroCrew folds the turn's
# em dashes to "--" and scrubs this header out of the text it did not write (the replay, the person's own words), so
# the person's turn is what follows the last one.
PERSON_MARK_RE = re.compile(r"\[CURRENT USER REQUEST (?:—|--) respond to this\]\n?")
# What KiroCrew itself puts after that header, in front of what the person typed (kiro_crew 0.13,
# dashboard/chat_turn/prompt_assembly.py _turn_build_prompt; first here is outermost): (opener, closer, the first
# closer is the real one, what it is). KiroCrew scrubs copies of the background-context frame out of its content; the
# Shared Crew sender line is one line; the restore of a turn the person stopped holds their old request and the
# agent's partial reply as they were, so it ends at its closer only when that closer is there once.
KIROCREW_FRAMES = (
    (re.compile(r'\[Background context from "[^"\n]*"\]\n'), "\n[End of background context]\n", True, "后台上下文"),
    (re.compile(r"\[from: [^\n]*\]\n"), None, True, "发送人"),
    (re.compile(r"\[PREVIOUS TURN WAS CANCELLED BY THE USER (?:—|--) context restore\]\n"), "\n[END PREVIOUS TURN]",
     False, "被停下那一轮的回放"),
    # a dashboard with no context builder sends the chat so far, then the message, with no header
    # (dashboard/chat_persistence.py _build_history_prefix)
    (re.compile(r"\[Previous chat history for this tab (?:—|--) session was reset after stop\]\n"), "\n[End of history]\n",
     False, "之前的对话回放"),
)
# Text after the header that KiroCrew wrote with no end that can be found (a sub-agent's notice), or a turn that is
# KiroCrew's from the header on (a report, a task, a notification, an automatic recovery, a quick prompt's or an
# @prompt's text): no line of such a turn is the person's for sure. (opener, what it is.)
KIROCREW_UNFRAMED = (
    ("[Subagent completion event]", "子代理通知"), ("[Subagent batch completion event]", "子代理通知"),
    ("You are a focused sub-agent.", "子代理的任务"), ("[system] Your previous attempt", "子代理的任务"),
    ("[Cron notification from ", "定时通知"), ("[MCP app message from ", "应用消息"),
    ("[sent by session ", "别的会话发来的消息"), ("[Dashboard card]", "卡片消息"), ("[Monitor wake]", "监控提醒"),
    ("[Worker report]", "队友会话的消息"), ("[Worker upgrade]", "队友会话的消息"), ("[Agent change]", "队友会话的消息"),
    ("[HEARTBEAT TASK", "心跳任务"), ("[Hook continuation", "自动续写的提示"), ("[Stop-hook nudge cap reached]", "自动续写的提示"),
    ("[Continue -- requested by the user]", "自动续写的提示"), ("[Content filter -- ", "自动续写的提示"),
    ("[Tool blocked -- ", "自动续写的提示"), ("[marker-removed] automatic recovery]", "自动续写的提示"),
    ("[QUICK PROMPT ", "快捷指令"), ("Execute the following instructions:", "@ 提示词"),
)
RECOVERY_RE = re.compile(r"\[[A-Za-z ]+ -- automatic recovery\]")     # [Connection lost -- automatic recovery] …
# A block of KiroCrew's that carries other people's or the agent's words, at a line start further down: something
# came in front of it (an operator's transform hook prefix, kiro_crew/hooks.py), so where the person's text starts is
# not known.
KIROCREW_INSIDE_RE = re.compile(r"(?m)^(?:(\[PREVIOUS TURN WAS CANCELLED BY THE USER )|(\[Background context from \")|"
                                r"(\[Subagent completion event\])|(\[Previous chat history for this tab ))")
INSIDE_WHAT = ("被停下那一轮的回放", "后台上下文", "子代理通知", "之前的对话回放")
# What a channel appends after the typed text (messaging/attachments.py append_attachment_context: files, documents,
# voice memo transcripts, image and rejection notes, each after a blank line; a file's body may hold copies of its
# closer, so the person's text ends where the first one starts), and the context entry a Slack options click brings
# (slack/handler_runtime/inbound.py: the text before it is the agent's option labels).
APPENDED_RE = re.compile(r"(?:^|\n\n)(?=\[(?:File|Document|Attached file|Attached document): |\[Attachment |"
                         r"\[Audio attachment |\[Voice memo transcription\]|\[Image [^\n\]]* is saved in the folder |"
                         r"--- CONTEXT ENTRY BEGIN ---\n)")
RUN_ID_RE = re.compile(r"^RUN-[A-Z][A-Z0-9]{0,9}-[1-9][0-9]{0,6}-[1-9][0-9]{0,5}$")   # brainkit.bridge.runnerd
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
REGULAR = ("100644", "100755")
MAX_FILE = 64 * 1024 * 1024
MAX_REQUEST = 64 * 1024
LEAN = ".kiro/aidlc-lean/aidlc_lean.py"
REVIEW_TURN = ".kiro/brain/review_turn.py"
RUNNER_PATH = "/usr/bin:/bin"        # runner mode: where git and ssh-keygen come from (runnerd.json hook_path)

# File-write tools (Kiro / KiroCrew names). Shell, delegation and unknown tools never take the allowance.
WRITE_TOOLS = frozenset(("fs_write", "write", "write_file", "create_file", "str_replace", "strReplace", "fsWrite",
                         "edit", "edit_file"))
PATH_KEYS = ("path", "file_path", "filePath")
CONTENT_KEYS = frozenset(("command", "text", "file_text", "fileText", "content", "contents", "new_str", "old_str",
                          "newStr", "oldStr", "new_text", "old_text", "newText", "oldText", "insert_line", "insertLine",
                          "summary", "explanation"))
WRITE_COMMANDS = frozenset(("create", "str_replace", "strReplace", "insert", "append", "write", "overwrite"))
KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{0,9}-[1-9][0-9]{0,6}$")
BRANCH_RE = re.compile(r"^[a-z]+/[a-z0-9][a-z0-9._-]{0,63}/([A-Z][A-Z0-9]{0,9}-[1-9][0-9]{0,6})"
                       r"(?:-[a-z0-9][a-z0-9-]{0,39})?$")   # brainkit.common.BRANCH_RE
PART_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
# Names of files an OS or an editor leaves next to the files it shows or edits (Finder's .DS_Store, vim's swap
# files, backups). Added under KIT_PATHS and ignored by git, such a file is no change to the kit: no agent spec, hook,
# script or module has such a name. Any other added file counts, git-ignored or not: Kiro and Python read the kit
# folders whatever git ignores.
JUNK_NAMES = (".DS_Store", "Thumbs.db", "desktop.ini", ".*.sw[a-p]", "*~", "#*#")
# environment that would point git at another repository or add configuration
GIT_ENV_DROP = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_CEILING_DIRECTORIES", "GIT_REPLACE_REF_BASE", "GIT_GRAFT_FILE",
                "GIT_CONFIG_PARAMETERS")


class Refused(Exception):
    """The kit's own files cannot be checked: the session may not go on."""


def git(cwd, *args, ok=(0,), stdin=None) -> str:
    """stdout of a read-only git command (replace refs off: a commit id names that commit) that reads `stdin` (bytes,
    if given); Refused when it fails (exits with a status not in `ok`)."""
    env = {k: v for k, v in os.environ.items() if k not in GIT_ENV_DROP and not k.startswith("GIT_CONFIG_")}
    env.update(GIT_NO_REPLACE_OBJECTS="1", GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    try:
        p = subprocess.run(["git", "-C", cwd] + list(args), input=stdin, capture_output=True, timeout=60, env=env)
    except (OSError, subprocess.SubprocessError) as e:
        raise Refused("git %s: %s" % (args[0], e))
    if p.returncode not in ok:
        raise Refused("git %s failed: %s" % (args[0], p.stderr.decode("utf-8", "replace").strip()[:200]
                                             or "exit %d" % p.returncode))
    return p.stdout.decode("utf-8", "surrogateescape")


def _read_regular(path, limit):
    """The bytes of a regular file, opened without following a symlink or blocking on a FIFO; None when it is no
    regular file or larger than `limit`."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
            return None
        chunks, size = [], 0
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                return b"".join(chunks)
            size += len(b)
            if size > limit:
                return None
            chunks.append(b)
    finally:
        os.close(fd)


# ---------- where: the project, the reference commit ----------

def lean_root(start, top):
    """The nearest folder from `start` up to `top` (both included) that has .kiro/aidlc-lean/aidlc_lean.py, or None."""
    d, top = os.path.realpath(start), os.path.realpath(top)
    while True:
        if os.path.isfile(os.path.join(d, ".kiro", "aidlc-lean", "aidlc_lean.py")):
            return d
        if d == top or os.path.dirname(d) == d:
            return None
        d = os.path.dirname(d)


def runner_workspace(start, runs_root):
    """The run's workspace on the team runner (the folder right under runs_root that holds `start`), or None."""
    d, base = os.path.realpath(start), os.path.realpath(runs_root)
    while os.path.dirname(d) != d:
        if os.path.dirname(d) == base:
            return d
        d = os.path.dirname(d)
    return None


def kit_module(name):
    """brainkit.<name> of the brain-kit this adapter is part of (<kit>/kit/.kiro/brain/lean-hook.py): runner mode."""
    kit = os.path.realpath(os.path.abspath(__file__))
    for _ in range(4):
        kit = os.path.dirname(kit)
    if not os.path.isfile(os.path.join(kit, "brainkit", name + ".py")):
        raise Refused("--runner needs the installed brain-kit's adapter (<brain-kit>/kit/.kiro/brain/lean-hook.py); "
                      "%s has no brainkit/" % kit)
    import importlib
    sys.path.insert(0, kit)
    try:
        return importlib.import_module("brainkit." + name)
    finally:
        sys.path.remove(kit)


def contract(cfg, ws):
    """(sha, voucher id) of the hand-over this runner workspace was prepared from: the bridge-signed request in
    runnerd's spool, verified against allowed_signers. The session can write the spool, but not sign."""
    run_id = os.path.basename(ws)
    if not RUN_ID_RE.match(run_id):
        raise Refused("%s is not a run's workspace (RUN-<KEY>-<n>)" % ws)
    try:
        raw = _read_regular(os.path.join(cfg["spool_dir"], "requests", run_id + ".json"), MAX_REQUEST)
        if raw is None:
            raise ValueError("not a regular file of at most %d bytes" % MAX_REQUEST)
        doc = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as e:
        raise Refused("the hand-over request of %s cannot be read: %s" % (run_id, e))
    v, s = (doc.get("voucher"), doc.get("sig")) if isinstance(doc, dict) else (None, None)
    if not isinstance(v, dict) or not isinstance(s, str) or \
            not kit_module("sign").verify(v, s, cfg["allowed_signers"]):
        raise Refused("the hand-over request of %s is not signed by the bridge (%s)" % (run_id, cfg["allowed_signers"]))
    if v.get("kind") != "handoff" or v.get("run_id") != run_id or not SHA40_RE.match(str(v.get("sha") or "")):
        raise Refused("the signed request in the spool is not the hand-over of %s" % run_id)
    return v["sha"], str(v.get("voucher_id") or "?")


def locate(start, runner):
    """(Lean's project root, the reference commit, what that commit is, how a person restores the kit files)."""
    if runner is None:
        top = git(start, "rev-parse", "--show-toplevel").strip()
        root = lean_root(start, top)
        if not root:
            raise Refused("no AI-DLC Lean (.kiro/aidlc-lean/aidlc_lean.py) in %s or above it in this repository"
                          % start)
        head = git(root, "rev-parse", "--verify", "-q", "HEAD^{commit}").strip()
        return root, head, "HEAD (%s)" % head[:12], ("git checkout HEAD -- <path>, delete what was added; or, if "
                                                       "a person changed them on purpose, commit them")
    with open(runner, encoding="utf-8") as f:
        cfg = json.load(f)
    if not isinstance(cfg, dict) or not all(isinstance(cfg.get(k), str) and cfg[k]
                                            for k in ("spool_dir", "runs_root", "allowed_signers")):
        raise Refused("%s does not name spool_dir, runs_root and allowed_signers" % runner)
    # git and ssh-keygen from the system, not from a folder on the session's PATH it could write a fake one to
    os.environ["PATH"] = cfg.get("hook_path") if isinstance(cfg.get("hook_path"), str) and cfg["hook_path"] \
        else RUNNER_PATH
    ws = runner_workspace(start, cfg["runs_root"])
    if not ws:
        raise Refused("%s is not in a run's workspace under %s" % (start, cfg["runs_root"]))
    me = os.path.realpath(os.path.abspath(__file__))
    if me.startswith(ws + os.sep):
        raise Refused("this adapter (%s) is the workspace's own copy: the runner's agent spec must run the installed "
                      "brain-kit's" % me)
    root = lean_root(start, ws)
    if not root:
        raise Refused("no AI-DLC Lean (.kiro/aidlc-lean/aidlc_lean.py) in %s" % ws)
    sha, vid = contract(cfg, ws)
    return root, sha, "the hand-over commit %s (%s)" % (sha[:12], vid), (
        "git checkout %s -- <path> in %s, delete what was added; or hand the run over again" % (sha[:12], ws))


# ---------- the kit's own files ----------

def _blob_ids(data, algo) -> set:
    """git's blob ids of a file's content as checked out: as it is, and with LF or CRLF line ends (a checkout may
    convert them; that is no change)."""
    lf = data.replace(b"\r\n", b"\n")
    return {algo(b"blob %d\0" % len(x) + x).hexdigest() for x in (data, lf, lf.replace(b"\n", b"\r\n"))}


def _scan_file(path, rel, st, have, odd):
    if stat.S_ISLNK(st.st_mode):
        odd[rel] = "a symlink"
    elif not stat.S_ISREG(st.st_mode):
        odd[rel] = "not a regular file"
    elif st.st_nlink != 1:
        odd[rel] = "a hard link"
    else:
        data = _read_regular(path, MAX_FILE)
        if data is None:
            odd[rel] = "not a regular file"
        else:
            have[rel] = data


def _scan_dir(path, rel, have, odd):
    with os.scandir(path) as it:
        names = sorted(e.name for e in it)
    for name in names:
        p, r = os.path.join(path, name), rel + "/" + name
        st = os.lstat(p)
        if stat.S_ISDIR(st.st_mode):
            _scan_dir(p, r, have, odd)
        else:
            _scan_file(p, r, st, have, odd)


def _scan(root, top, have, odd):
    """What the working tree has at the KIT_PATHS entry `top`: have {path: bytes} for regular files, odd {path: how}
    for what is no regular file (or no folder) where one belongs, a symlink on the way included."""
    parts = top.rstrip("/").split("/")
    cur, st = root, None
    for i, part in enumerate(parts):
        cur, rel = os.path.join(cur, part), "/".join(parts[:i + 1])
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            return
        if stat.S_ISLNK(st.st_mode):
            odd[rel] = "a symlink"
            return
        if (i < len(parts) - 1 or top.endswith("/")) and not stat.S_ISDIR(st.st_mode):
            odd[rel] = "not a folder"
            return
    if top.endswith("/"):
        _scan_dir(cur, top.rstrip("/"), have, odd)
    else:
        _scan_file(cur, top, st, have, odd)


def kit_differences(root, ref, contents=None, tops=KIT_PATHS) -> list:
    """[(path, how)] where the kit's own files (KIT_PATHS) in the working tree at `root` are not those of commit
    `ref`: changed, added, deleted, a symlink, a hard link, not a regular file, not a folder. Contents count, as git
    stores them (line ends aside), not file modes. `contents` (a dict) gets the bytes read of each regular file there.
    Refused or OSError when it cannot be told."""
    want = {}
    for rec in git(root, "ls-tree", "-r", "-z", ref, "--", *tops).split("\0"):
        if rec:
            meta, _, path = rec.partition("\t")
            mode, kind, oid = (meta.split(" ") + ["", "", ""])[:3]
            want[path] = (mode, kind, oid)
    algo = hashlib.sha256 if any(len(w[2]) == 64 for w in want.values()) else hashlib.sha1
    have, odd = {}, {}
    for top in tops:
        _scan(root, top, have, odd)
    if contents is not None:
        contents.update(have)
    out = dict(odd)
    for path, data in have.items():
        w = want.get(path)
        if w is None:
            out[path] = "added"
        elif w[1] == "blob" and w[0] in REGULAR and w[2] not in _blob_ids(data, algo):
            out[path] = "changed"
    for path in _ignored_junk(root, [p for p, how in out.items() if how == "added"]):
        del out[path]
    for path, (mode, kind, _) in want.items():
        if path in out or any(path.startswith(o + "/") for o in odd):
            continue
        if kind != "blob" or mode not in REGULAR:
            out[path] = "a symlink" if mode == "120000" else "not a regular file"
        elif path not in have:
            out[path] = "deleted"
    return sorted(out.items())


def _ignored_junk(root, added) -> list:
    """Those of the added regular files that git ignores and that are named like an OS's or an editor's leftovers
    (JUNK_NAMES): no change to the kit."""
    junk = [p for p in added if any(fnmatch.fnmatchcase(p.rsplit("/", 1)[-1], j) for j in JUNK_NAMES)]
    if not junk:
        return []
    names = "".join(p + "\0" for p in junk).encode("utf-8", "surrogateescape")
    return [p for p in git(root, "check-ignore", "-z", "--stdin", ok=(0, 1), stdin=names).split("\0") if p in junk]


def _case_insensitive(root) -> bool:
    for name in (".kiro", "aidlc-docs"):
        p = os.path.join(root, name)
        if os.path.exists(p):
            try:
                return os.path.samefile(p, os.path.join(root, name.upper()))
            except OSError:
                return False
    return False


def _under(rel, prefixes) -> bool:
    for p in prefixes:
        if (p.endswith("/") and (rel == p[:-1] or rel.startswith(p))) or (p.endswith("-") and rel.startswith(p)) \
                or rel == p:
            return True
    return False


def protected_target(root, payload, tops=KIT_PATHS):
    """The kit path a file-write tool call names, resolved as Lean resolves it (symlinks followed, relative to the
    project, letter case as the file system compares it), or None. Those Lean guards itself are left to Lean."""
    tool = str(payload.get("tool_name") or "")
    tin = payload.get("tool_input")
    if not isinstance(tin, dict) or tool in SHELL_TOOLS or READ_TOOL_RE.search(tool):
        return None
    fold = _case_insensitive(root)
    for k in PATH_KEYS:
        given = tin.get(k)
        if not isinstance(given, str) or not given:
            continue
        p = given if os.path.isabs(given) else os.path.join(root, given)
        r = os.path.relpath(os.path.realpath(p), os.path.realpath(root)).replace(os.sep, "/")
        key = r.casefold() if fold else r
        if _under(key, tops) and not _under(key, LEAN_KIT_PATHS):
            return r
    return None


# ---------- the park.json allowance ----------

def _runs_dir(root):
    """runs_dir from .brain/kit.json (default "runs") as path parts, or None when it is not a plain relative path."""
    value = "runs"
    p = os.path.join(root, ".brain", "kit.json")
    if os.path.lexists(p):
        with open(p, encoding="utf-8") as f:
            cfg = json.load(f)
        value = cfg.get("runs_dir", "runs") if isinstance(cfg, dict) else None
    if not isinstance(value, str):
        return None
    parts = value.strip("/").split("/")
    return parts if all(PART_RE.match(x) and x not in (".", "..") for x in parts) else None


def _regular(path):
    """A regular file, not a symlink, with one link (a hard link would let a write change another file)."""
    st = os.lstat(path)
    return stat.S_ISREG(st.st_mode) and st.st_nlink == 1


def _run_branch(root, runs, key, branch):
    """Does <runs_dir>/<key>/run.json name this key and branch?"""
    p = os.path.join(root, *(runs + [key, "run.json"]))
    if not os.path.lexists(p) or not _regular(p) or os.path.realpath(p) != os.path.join(
            os.path.realpath(root), *(runs + [key, "run.json"])):
        return False
    with open(p, encoding="utf-8") as f:
        rec = json.load(f)
    return isinstance(rec, dict) and rec.get("key") == key and rec.get("branch") == branch


def run_key(root, runs):
    """This workspace's run KEY, or None when it cannot be told."""
    try:
        branch = git(root, "symbolic-ref", "-q", "--short", "HEAD").strip()
    except Refused:
        return None
    if not branch:
        return None
    m = BRANCH_RE.match(branch)
    if m:
        return m.group(1) if _run_branch(root, runs, m.group(1), branch) else None
    base = os.path.join(root, *runs)
    keys = [k for k in (sorted(os.listdir(base)) if os.path.isdir(base) else []) if KEY_RE.match(k)]
    found = [k for k in keys if _run_branch(root, runs, k, branch)]
    return found[0] if len(found) == 1 else None


def _workspace_parts(root, given):
    """The parts of a write tool's path below the workspace root, however it is spelled: relative to the root (as
    Lean takes it) or absolute, through any folder that resolves to the root (/tmp/ws for /private/tmp/ws: the
    shortest such start of the path, so a symlink inside the workspace stays in the parts); empty and '.' parts
    dropped. None when it is not below the root, has a '..' part, or does not end in a file name."""
    comps = given.split("/")
    if ".." in comps or comps[-1] in ("", "."):
        return None
    if os.path.isabs(given):
        real = os.path.realpath(root)
        for i in range(2, len(comps)):
            if os.path.realpath("/".join(comps[:i])) == real:
                comps = comps[i:]
                break
        else:
            return None
    return [c for c in comps if c not in ("", ".")]


def park_target(root, payload):
    """True when this PreToolUse is a file write whose only target is this run's <runs_dir>/<KEY>/park.json."""
    tool = payload.get("tool_name")
    tin = payload.get("tool_input")
    if tool not in WRITE_TOOLS or not isinstance(tin, dict) or not tin:
        return False
    if any(k not in PATH_KEYS and k not in CONTENT_KEYS for k in tin):
        return False
    paths = [tin[k] for k in PATH_KEYS if k in tin]
    if not paths or any(not isinstance(x, str) or not x for x in paths) or len(set(paths)) != 1:
        return False
    if "command" in tin and tin["command"] not in WRITE_COMMANDS:
        return False
    runs = _runs_dir(root)
    if not runs:
        return False
    parts = _workspace_parts(root, paths[0])
    if parts is None or len(parts) != len(runs) + 2 or parts[:len(runs)] != runs or parts[-1] != "park.json" \
            or not KEY_RE.match(parts[-2]):
        return False
    want = os.path.join(os.path.realpath(root), *parts)
    if os.path.realpath(os.path.join(root, *parts)) != want:
        return False                                    # a symlinked runs/, runs/<KEY>/ or park.json
    if os.path.lexists(want) and not _regular(want):
        return False
    return run_key(root, runs) == parts[-2]


# ---------- the host marker ----------

def neutral(value, key=None, depth=0):
    """`value` with HOST_MARK escaped in every string under a "prompt" key (dicts and lists, any depth)."""
    if isinstance(value, str):
        return value.replace(HOST_MARK, HOST_MARK_SAFE) if key == "prompt" else value
    if depth > 16:
        return value
    if isinstance(value, dict):
        return {k: neutral(v, k, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [neutral(v, key, depth + 1) for v in value]
    return value


def person_text(prompt):
    """(what the person typed in this turn, why that cannot be told): the text after KiroCrew's last current-request
    header, or the whole prompt when it has none or starts with KiroCrew's own block (Kiro CLI hands the prompt over
    as typed; a backend for which KiroCrew fires the hook itself hands over the turn's message, unfolded and
    unscrubbed), without the blocks KiroCrew puts in front of the typed text (KIROCREW_FRAMES) and what a channel
    appends after it (APPENDED_RE). `why` is '' when the text is the person's, else what KiroCrew put there that has no
    end to cut at (KIROCREW_UNFRAMED, a frame without its closer, a Slack options click): then no line of the text is
    the person's for sure."""
    last = None
    if not _kirocrew_block_at(prompt):          # else the turn's own message: any header in it is a copy
        for last in PERSON_MARK_RE.finditer(prompt):
            pass
    text = prompt if last is None else prompt[last.end():]
    while True:
        for opener, closer, first_is_real, what in KIROCREW_FRAMES:
            m = opener.match(text)
            if m is None:
                continue
            rest = text[m.end():]
            if closer is None:
                text = rest
            elif rest.count(closer) == 1 or (first_is_real and closer in rest):
                text = rest[rest.index(closer) + len(closer):]
            else:
                return text, what
            text = text.lstrip("\n")
            break
        else:
            break
    head = text[:200].replace("—", "--")          # a turn's message the hook gets unfolded
    for opener, what in KIROCREW_UNFRAMED:
        if head.startswith(opener):
            return text, what
    if RECOVERY_RE.match(head):
        return text, "自动续写的提示"
    inside = KIROCREW_INSIDE_RE.search(text)
    if inside is not None:
        return text, INSIDE_WHAT[inside.lastindex - 1]
    appended = APPENDED_RE.search(text)
    if appended is None:
        return text, ""
    if text[appended.end():].startswith("--- CONTEXT ENTRY BEGIN ---"):
        return text, "Slack 上点的选项"
    return text[:appended.start()], ""


def _kirocrew_block_at(prompt):
    """Does the prompt start with a block KiroCrew puts in front of typed text (the turn's message itself)?"""
    head = prompt[:200].replace("—", "--")
    return any(opener.match(head) for opener, _, _, _ in KIROCREW_FRAMES) or \
        any(head.startswith(opener) for opener, _ in KIROCREW_UNFRAMED[:2])


# ---------- the hook ----------

def refuse(event, msg) -> int:
    """PreToolUse: the tool call is refused (exit 2, the reason on stderr). Other events: the session is told (stdout
    is added to its context) and Lean is not called."""
    if event == "PreToolUse":
        sys.stderr.write(msg)
        return 2
    sys.stdout.write(msg)
    return 0


def say(text):
    """Lines for the session on stdout, as UTF-8 whatever the locale's encoding is (Lean's lines follow them)."""
    sys.stdout.flush()
    sys.stdout.buffer.write(text.encode("utf-8", "replace") + b"\n")
    sys.stdout.buffer.flush()


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    runner = None
    observe_only = args[:1] == ["--observe"]
    if observe_only:
        args = args[1:]
    elif args[:1] == ["--runner"]:
        runner, args = (args[1] if len(args) > 1 else ""), args[2:]
    event = args[0] if args else ""
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    start = payload.get("cwd") if isinstance(payload.get("cwd"), str) and payload.get("cwd") else os.getcwd()
    files = {}
    try:
        root, ref, what, fix = locate(start, runner)
        tops = KIT_PATHS + (RUNNER_KIT_PATHS if runner is not None else ())
        diffs = kit_differences(root, ref, files, tops)
        if not diffs and LEAN not in files:
            raise Refused("%s is not a regular file" % LEAN)
    except Exception as e:  # noqa: BLE001  fail closed: what cannot be checked is not let through
        if observe_only:
            say("[brain-kit] 审批单没记下这条消息：kit 文件没法检查（%s）。" % " ".join(str(e).split()))
            return 0
        return refuse(event, "[brain-kit] blocked (kit files not checked): the kit's own files could not be "
                             "checked: %s. Until they can, every tool call is refused and nothing said in this chat "
                             "reaches AI-DLC Lean. A person has to look at it; stop and say so.\n" % e)
    if diffs:
        shown = "; ".join("%s (%s)" % d for d in diffs[:6]) + ("; and %d more" % (len(diffs) - 6)
                                                             if len(diffs) > 6 else "")
        if observe_only:
            say("[brain-kit] 审批单没记下这条消息：kit 文件和 %s 不一样（%s）。" % (what, " ".join(shown.split())))
            return 0
        return refuse(event, "[brain-kit] blocked (kit files changed): the kit's own files are not those of %s: %s. "
                             "They decide what this session may do (AI-DLC Lean, its hooks, the agent specs, this "
                             "adapter, .brain/kit.json, gates/), so the session may not change them. A person has to "
                             "restore them (%s). Until then every tool call is refused and nothing said in this chat "
                             "reaches AI-DLC Lean (approvals in it are not recorded). Stop and say so.\n"
                             % (what, shown, fix))
    if not observe_only and not payload.get("session_id"):     # --observe: as Lean's own workspace hook reads it
        sid = payload.get("acp_session_id") or os.environ.get("KIRO_SESSION_ID") or payload.get("session_key")
        if sid:
            payload["session_id"] = sid
    if event == "PreToolUse" and not observe_only:
        try:
            allowed = park_target(root, payload)
        except Exception:  # noqa: BLE001  fail closed: whatever cannot be told goes to Lean
            allowed = False
        if allowed:
            return 0
        try:
            hit = protected_target(root, payload, tops)
        except Exception as e:  # noqa: BLE001  fail closed
            return refuse(event, "[brain-kit] blocked (kit file): the target of this tool call could not be checked "
                                 "(%s).\n" % e)
        if hit:
            return refuse(event, "[brain-kit] blocked (kit file): %s is one of the kit's own files (%s): a session "
                                 "may not change it. People change these through a merge request to the default "
                                 "branch.\n" % (hit, ", ".join(tops)))
        if "tool_input" in payload:
            payload["tool_input"] = neutral(payload["tool_input"])
    elif isinstance(payload.get("prompt"), str):
        if not observe_only:
            payload = neutral(payload)      # Lean's own workspace hook (Kiro CLI) gets the prompt as typed
        if event == "UserPromptSubmit":
            note = observe(root, files, payload, runner)
            if note:
                say(note)
    return 0 if observe_only else run_lean(root, files[LEAN], event, payload)


def observe(root, files, payload, runner) -> str:
    if runner is not None or REVIEW_TURN not in files:
        return ""
    try:
        lean = vars(library("aidlc_lean_library", files[LEAN], os.path.join(root, *LEAN.split("/"))))
        rt = library("brain_review_turn", files[REVIEW_TURN], os.path.join(root, *REVIEW_TURN.split("/")))
        runs = _runs_dir(root) or ["runs"]
        said, unsure = person_text(payload["prompt"])
        return rt.observe(root, payload["prompt"], payload.get("session_id"), lean, run_key(root, runs),
                          "/".join(runs), said=said, unsure=unsure)
    except (Exception, SystemExit) as e:  # noqa: BLE001  Lean's Project raises SystemExit too
        return "[brain-kit] 审批单没记下这条消息：%s" % " ".join(("%s: %s" % (type(e).__name__, e)).split())[:300]


def library(name, code, path):
    import types
    mod = types.ModuleType(name)
    mod.__file__ = path
    # In sys.modules before it runs: a dataclass under postponed annotations looks its module up there.
    sys.modules[name] = mod
    exec(compile(code, path, "exec", dont_inherit=True), mod.__dict__)
    return mod


def run_lean(root, code, event, payload) -> int:
    """`aidlc_lean.py hook <event>` with this payload on stdin, run here as __main__ from `code`, the bytes the kit
    check compared with the reference commit: what runs is what was checked. Lean's exit is this process's."""
    import builtins
    import io
    import types
    lean = os.path.join(root, *LEAN.split("/"))
    here = os.path.realpath(os.path.dirname(os.path.abspath(__file__)))
    sys.path[:] = [p for p in sys.path if p and os.path.realpath(p) != here]   # not this adapter's folder
    os.chdir(root)
    sys.argv = [lean, "hook", event]
    sys.stdin = io.StringIO(json.dumps(payload))
    mod = types.ModuleType("__main__")
    mod.__file__, mod.__builtins__ = lean, builtins
    sys.modules["__main__"] = mod
    exec(compile(code, lean, "exec", dont_inherit=True), mod.__dict__)    # noqa: S102 - Lean's code, as checked
    return 0


if __name__ == "__main__":
    sys.exit(main())
