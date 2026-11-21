#!/usr/bin/env python3
"""A brain-patrol session's reply on a GitLab MR or Issue, and the MR of a fix, posted as the bridge's GitLab account
(team/patrol.py and team/staging.py put this command line in every message they send):

  patrol_reply.py --config BRIDGE_JSON --brainkit DIR --project P --mr IID --discussion ID TEXT   reply in that thread
  patrol_reply.py --config BRIDGE_JSON --brainkit DIR --project P --mr IID --note TEXT            a comment on the MR
  patrol_reply.py --config BRIDGE_JSON --brainkit DIR --project P --issue IID --note TEXT         a comment on the Issue
  patrol_reply.py --config BRIDGE_JSON --brainkit DIR --project P --issue IID --open-mr --source BRANCH --target BRANCH
                  --title TITLE TEXT          an MR from BRANCH, TEXT its description, which ends with Closes #IID

TEXT "-" reads it from stdin. The token is the one bridge.json names (gitlab.token_env, else gitlab.token_file), and the
request goes through brain-kit's GitLab client, so a GitLab behind single sign-on gets its sign-in helper as everywhere
else. The session runs as the app's OS user and can read that token anyway: the same-user trial only. Run on its own
(python3 -I): it imports nothing of the app. Prints the new note's id; for --open-mr, the MR's !iid and address (an MR
already open from that branch is the one printed, and no second one is made). Exit 1: GitLab refused or could not be
reached; 2: the arguments or bridge.json.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import re
import sys

PKG = "_patrol_reply_brainkit"
PROJECT_RE = re.compile(r"^(?:[1-9][0-9]*|[A-Za-z0-9_.][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_.][A-Za-z0-9_.-]*)+)$")
DISCUSSION_RE = re.compile(r"^[0-9a-f]{8,64}$")
BRANCH_RE = re.compile(r"^(?!.*\.\.)[A-Za-z0-9_][A-Za-z0-9._/-]{0,199}$")
MAX_TEXT = 20000
MAX_TITLE = 255


def brainkit_rest(kit_dir: str):
    """brain-kit's rest module from the checkout *kit_dir*, under a private name (as the app loads it)."""
    pkg = os.path.join(kit_dir, "brainkit")
    spec = importlib.util.spec_from_file_location(PKG, os.path.join(pkg, "__init__.py"), submodule_search_locations=[pkg])
    if spec is None or spec.loader is None:
        raise ValueError("no brain-kit in %s" % kit_dir)
    root = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = root
    spec.loader.exec_module(root)
    return importlib.import_module(PKG + ".rest")


def main(argv=None, transport=None) -> int:
    ap = argparse.ArgumentParser(prog="patrol_reply.py",
                                 description="reply on a GitLab MR or Issue, or open a fix's MR, as the bridge's account")
    ap.add_argument("--config", required=True, help="bridge.json")
    ap.add_argument("--brainkit", required=True, help="the brain-kit checkout")
    ap.add_argument("--project", required=True, help="group/repo, or the project's id")
    on = ap.add_mutually_exclusive_group(required=True)
    on.add_argument("--mr", type=int, help="the MR's number (iid)")
    on.add_argument("--issue", type=int, help="the Issue's number (iid)")
    what = ap.add_mutually_exclusive_group(required=True)
    what.add_argument("--discussion", help="the MR's thread to reply in")
    what.add_argument("--note", action="store_true", help="a comment on the MR or the Issue itself")
    what.add_argument("--open-mr", action="store_true", help="an MR from --source into --target that closes --issue")
    ap.add_argument("--source", help="--open-mr: the branch of the fix")
    ap.add_argument("--target", help="--open-mr: the branch it goes into")
    ap.add_argument("--title", help="--open-mr: the MR's title")
    ap.add_argument("text", help='what to say (--open-mr: the MR\'s description); "-": read it from stdin')
    a = ap.parse_args(argv)
    text = sys.stdin.read() if a.text == "-" else a.text
    if not text.strip() or len(text) > MAX_TEXT:
        print("the text is empty or longer than %d characters" % MAX_TEXT, file=sys.stderr)
        return 2
    iid = a.mr if a.mr is not None else a.issue
    if not PROJECT_RE.match(a.project) or iid <= 0 or (a.discussion and not DISCUSSION_RE.match(a.discussion)):
        print("--project is group/repo, --mr and --issue a number, --discussion GitLab's id of the thread", file=sys.stderr)
        return 2
    if a.discussion and a.mr is None:
        print("--discussion is a thread of an MR: give --mr", file=sys.stderr)
        return 2
    if a.open_mr and (a.issue is None or not all(BRANCH_RE.match(b or "") for b in (a.source, a.target))
                      or not (a.title or "").strip() or len(a.title) > MAX_TITLE):
        print("--open-mr needs --issue (the MR closes it), --source and --target branches, and a --title of at most %d "
              "characters" % MAX_TITLE, file=sys.stderr)
        return 2
    try:
        with open(a.config, encoding="utf-8") as fh:
            g = json.load(fh).get("gitlab") or {}
        rest = brainkit_rest(a.brainkit)
    except (OSError, ValueError, AttributeError, ImportError) as exc:
        print("bridge.json or brain-kit cannot be used: %s" % exc, file=sys.stderr)
        return 2
    try:
        token = rest.read_token(g.get("token_env", "BRIDGE_GITLAB_TOKEN"),
                                default_file=g.get("token_file", rest.DEFAULT_TOKEN_FILE))
    except (OSError, rest.BrainError) as exc:
        print("the bridge's token cannot be read: %s" % exc, file=sys.stderr)
        return 2
    if not g.get("base") or not token:
        print("bridge.json names no gitlab.base, or its token is empty", file=sys.stderr)
        return 2
    try:
        gl = rest.GitLab(g["base"], token, transport)
        if a.open_mr:
            mr = gl.mr_for_branch(a.project, a.source)
            if mr is None:
                closes = "Closes #%d" % a.issue
                body = text if re.search(r"(?i)\bcloses\s+#%d\b" % a.issue, text) else text.rstrip() + "\n\n" + closes
                mr = gl.create_mr(a.project, a.source, a.target, a.title.strip(), (), description=body)
            print("!%s %s" % ((mr or {}).get("iid", ""), (mr or {}).get("web_url", "")))
            return 0
        if a.issue is not None:
            note = gl.call("POST", "/projects/%s/issues/%d/notes" % (rest.q(a.project), a.issue), body={"body": text})
        elif a.discussion:
            note = gl.call("POST", "/projects/%s/merge_requests/%d/discussions/%s/notes"
                           % (rest.q(a.project), a.mr, a.discussion), body={"body": text})
        else:
            note = gl.comment(a.project, a.mr, text)
    except rest.BrainError as exc:
        print("GitLab did not take it: %s" % exc, file=sys.stderr)
        return 1
    print((note or {}).get("id", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
