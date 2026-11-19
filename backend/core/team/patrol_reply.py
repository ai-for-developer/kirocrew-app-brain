#!/usr/bin/env python3
"""A brain-patrol session's reply on a GitLab MR, posted as the bridge's GitLab account (team/patrol.py puts this
command line in every message it sends):

  patrol_reply.py --config BRIDGE_JSON --brainkit DIR --project P --mr IID --discussion ID TEXT   reply in that thread
  patrol_reply.py --config BRIDGE_JSON --brainkit DIR --project P --mr IID --note TEXT            a comment on the MR

TEXT "-" reads it from stdin. The token is the one bridge.json names (gitlab.token_env, else gitlab.token_file), and the
request goes through brain-kit's GitLab client, so a GitLab behind single sign-on gets its sign-in helper as everywhere
else. The session runs as the app's OS user and can read that token anyway: the same-user trial only. Run on its own
(python3 -I): it imports nothing of the app. Prints the new note's id. Exit 1: GitLab refused or could not be reached;
2: the arguments or bridge.json.
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
MAX_TEXT = 20000


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
    ap = argparse.ArgumentParser(prog="patrol_reply.py", description="reply on a GitLab MR as the bridge's account")
    ap.add_argument("--config", required=True, help="bridge.json")
    ap.add_argument("--brainkit", required=True, help="the brain-kit checkout")
    ap.add_argument("--project", required=True, help="group/repo, or the project's id")
    ap.add_argument("--mr", required=True, type=int, help="the MR's number (iid)")
    where = ap.add_mutually_exclusive_group(required=True)
    where.add_argument("--discussion", help="the thread to reply in")
    where.add_argument("--note", action="store_true", help="a comment on the MR itself")
    ap.add_argument("text", help='what to say; "-": read it from stdin')
    a = ap.parse_args(argv)
    text = sys.stdin.read() if a.text == "-" else a.text
    if not text.strip() or len(text) > MAX_TEXT:
        print("the text is empty or longer than %d characters" % MAX_TEXT, file=sys.stderr)
        return 2
    if not PROJECT_RE.match(a.project) or a.mr <= 0 or (a.discussion and not DISCUSSION_RE.match(a.discussion)):
        print("--project is group/repo, --mr a number, --discussion GitLab's id of the thread", file=sys.stderr)
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
        if a.discussion:
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
