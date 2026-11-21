"""Thin wrapper over AI-DLC Lean (installed in the project as .kiro/aidlc-lean/aidlc_lean.py).

brain-kit does not change Lean; it only calls its CLI and reads aidlc-state.md. The one exception is `brain install`
adding brain-kit's record folder to aidlc-docs/lean.json fingerprint_exclude (merge_fingerprint_exclude): additions
only, never a removal, and the person who runs it reviews and commits the file (it is human-owned).
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile

from .common import BrainError, run

SCRIPT = os.path.join(".kiro", "aidlc-lean", "aidlc_lean.py")
DOCS = "aidlc-docs"
CONFIG = DOCS + "/lean.json"
TEMPLATE_STATUS = "Status: Inception: starting"
# The '### Next' items a new intent's aidlc-state.md starts with: Lean's templates/aidlc-state.md (the same since
# 0.2.0) and, for `lean new --ideation`, IDEATION_NEXT in aidlc_lean.py. A note whose Next items are all still
# these has not been filled in.
TEMPLATE_NEXT = (
    "Read the code the change touches. Brownfield: `lean stage start reverse-engineering`; greenfield: "
    "`lean stage skip reverse-engineering --reason \"greenfield\"`.",
    "Ask the clarifying questions in one batch, then fill in inception/requirements/requirements.md "
    "(skill aidlc-lean-inception).",
    "Fill in ideation/intent-capture/intent-statement.md with the product owner (skill aidlc-lean-ideation), then "
    "`lean stage done intent-capture --note ...`.",
    "Scope Definition and Approval Handoff are required, the other Ideation stages optional; then `lean request "
    "IDEATION` and wait for the approval before Inception.",
)
# Lean's DEFAULT_CONFIG fingerprint_exclude (aidlc_lean.py). A lean.json that sets the key replaces this list.
DEFAULT_FINGERPRINT_EXCLUDE = (DOCS + "/**", ".kiro/**")
# Lean's RECORD_GLOBS (aidlc_lean.py, since 0.3.1): records whatever fingerprint_exclude lists, which adds to them.
RECORD_GLOBS = (DOCS + "/**", ".kiro/**")


def installed(repo) -> bool:
    return os.path.exists(os.path.join(repo, SCRIPT)) and os.path.exists(os.path.join(repo, DOCS, "lean.json"))


def version(repo) -> str:
    """VERSION of the AI-DLC Lean installed in `repo` ('' when there is none or it cannot be read)."""
    try:
        with open(os.path.join(repo, SCRIPT), encoding="utf-8", errors="replace") as f:
            return version_in(f.read())
    except OSError:
        return ""


def version_in(text) -> str:
    """VERSION of an aidlc_lean.py text ('' when it has none)."""
    m = re.search(r'^VERSION = "([^"\n]+)"', text or "", re.M)
    return m.group(1) if m else ""


def _release(version_text) -> tuple:
    return tuple(int(n) for n in re.findall(r"[0-9]+", version_text or "")[:3])


NO_INTENT_SINCE = (0, 3, 2)     # the Lean that has `lean use --none`


def holds_no_intent(version_text) -> bool:
    """Has this Lean version `lean use --none`?"""
    return _release(version_text) >= NO_INTENT_SINCE


def version_matches(have, pin) -> bool:
    """A pin names a version or a release line: "0.3" takes 0.3, 0.3.0, 0.3.1, not 0.30 or 10.3."""
    return have == pin or have.startswith(pin + ".")


def call(repo, *args, check=True):
    if not installed(repo):
        raise BrainError("AI-DLC Lean is not installed here (run `brain install --lean <path>`)")
    return run([sys.executable, SCRIPT] + list(args), cwd=repo, check=check)


def use_none(repo) -> None:
    """`lean use --none` in this clone: no active intent, not even the only intent folder. BrainError in Lean's words
    when it refuses (regulated mode; a Lean older than 0.3.2 reads --none as an intent name)."""
    p = call(repo, "use", "--none", check=False)
    if p.returncode != 0:
        raise BrainError("AI-DLC Lean refused `use --none`: %s" % (p.stdout + p.stderr).strip())


# Run by open_requests with the project's own Lean on sys.path: Lean's rules, executed, never copied.
_OPEN_REQUESTS = """import json, sys
sys.path.insert(0, sys.argv[1])
import aidlc_lean as L
pj = L.Project(sys.argv[2])
intent = pj.active()
requests, stale = [], []
for doc in ((L.REQ_DOC, L.DESIGN_DOC) if intent else ()):
    path = pj.doc(intent, doc)
    e = pj.pending_request(path)
    if e is not None:
        requests.append(e)
        continue
    last = None
    for x in pj.events():
        if x.get("event") in ("APPROVAL_REQUESTED", "APPROVED", "REJECTED") and L.covers(x, L.rel(pj.root, path)):
            last = x
    if last is not None and last["event"] == "APPROVAL_REQUESTED":
        stale.append(last)
def once(es):
    out = []
    for e in es:
        if e not in out:
            out.append(e)
    return out
print(json.dumps({"intent": intent, "requests": once(requests), "stale": once(stale)}, ensure_ascii=False))
"""


def open_requests(repo) -> dict:
    """{"intent": Lean's active intent (or None), "requests": the APPROVAL_REQUESTED records Lean's own
    Project.pending_request returns for the intent's requirements.md and application-design.md (an INCEPTION
    request once), "stale": the requests still last on one of those pages that no longer hash as requested}, from
    the project's own Lean run in a subprocess. BrainError when Lean is not installed or cannot run."""
    kit = os.path.join(repo, ".kiro", "aidlc-lean")
    if not os.path.isfile(os.path.join(kit, "aidlc_lean.py")):
        raise BrainError("这里没装 AI-DLC Lean（先 `brain install`）")
    try:
        # -B: no __pycache__ written into the project's .kiro/aidlc-lean/ (it would show as an uncommitted file)
        p = subprocess.run([sys.executable, "-I", "-B", "-c", _OPEN_REQUESTS, kit, repo], cwd=repo,
                           capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        raise BrainError("没能运行 Lean 读审批请求：%s" % e)
    try:
        out = json.loads(p.stdout) if p.returncode == 0 else None
    except ValueError:
        out = None
    if not isinstance(out, dict):
        raise BrainError("Lean 没能读出审批请求：%s" % (p.stderr or p.stdout).strip()[-500:])
    return out


def install(repo, lean_src, extra=()):
    script = os.path.join(lean_src, "install.sh")
    if not os.path.exists(script):
        raise BrainError("not an AI-DLC Lean checkout (no install.sh): %s" % lean_src)
    return run(["bash", script, repo] + list(extra))


def fingerprint_exclude(cfg) -> list:
    """The fingerprint_exclude Lean uses for this lean.json content: its own list when it sets one, else Lean's
    default. BrainError when it is set but is not a list of strings (Lean would not read it the way it looks)."""
    if "fingerprint_exclude" not in cfg:
        return list(DEFAULT_FINGERPRINT_EXCLUDE)
    pats = cfg["fingerprint_exclude"]
    if not isinstance(pats, list) or not all(isinstance(p, str) for p in pats):
        raise BrainError("%s fingerprint_exclude is not a list of patterns" % CONFIG)
    return list(pats)


def is_record_path(path, lcfg) -> bool:
    """Lean's is_record_path for a repo path, as brain-kit reads lean.json (`lcfg`, its content): RECORD_GLOBS
    (aidlc-docs/**, .kiro/**) and fingerprint_exclude, except contracts, and the per-clone .aidlc-lean/ state. Not
    code: changing it leaves Lean's code fingerprint as it is. One rule for the verify line (brainkit.mrdesc) and for
    evidence (brainkit.proof)."""
    if path.startswith(".aidlc-lean/"):
        return True
    contracts = str(lcfg.get("contracts_dir") or DOCS + "/contracts").strip("/") + "/"
    if path.startswith(contracts) and path != contracts + "OWNERS" and not path.startswith(contracts + "changes/"):
        return False
    pats = lcfg.get("fingerprint_exclude")
    for pat in RECORD_GLOBS + tuple(pats if isinstance(pats, list) else DEFAULT_FINGERPRINT_EXCLUDE):
        if isinstance(pat, str) and ((pat.endswith("/") and path.startswith(pat)) or fnmatch.fnmatchcase(path, pat)):
            return True
    return False


def merge_fingerprint_exclude(repo, patterns) -> list:
    """Add `patterns` (and Lean's own defaults, when lean.json's list leaves them out) to aidlc-docs/lean.json
    fingerprint_exclude. Never removes or reorders an entry, and leaves the rest of the file as it is (key order
    kept). Returns what was added ([] when nothing was missing). lean.json is human-owned: the caller tells the
    person to review and commit it. BrainError when lean.json is missing or cannot be read as Lean reads it."""
    path = os.path.join(repo, CONFIG)
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except OSError as e:
        raise BrainError("%s cannot be read: %s" % (CONFIG, e))
    except ValueError as e:
        raise BrainError("%s is not valid JSON: %s" % (CONFIG, e))
    if not isinstance(cfg, dict):
        raise BrainError("%s is not a JSON object" % CONFIG)
    have = fingerprint_exclude(cfg)
    added = []
    for p in list(DEFAULT_FINGERPRINT_EXCLUDE) + list(patterns):
        if p not in have and p not in added:
            added.append(p)
    if not added:
        return []
    cfg["fingerprint_exclude"] = have + added
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")     # as Lean's `init` writes it
        os.chmod(tmp, os.stat(path).st_mode & 0o777)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return added


def new_intent(repo, slug, title) -> str:
    """Create a Lean intent; return its folder name (yymmdd-slug)."""
    out = call(repo, "new", slug, "--title", title).stdout
    m = re.search(r"Created %s/([0-9]{6}-[a-z0-9-]+)" % DOCS, out)
    if not m:
        raise BrainError("could not read the new intent name from Lean:\n" + out)
    return m.group(1)


def state_path(repo, intent) -> str:
    return os.path.join(repo, DOCS, intent, "aidlc-state.md")


def next_steps(text: str) -> list:
    """Items under '### Next' in the Session notes."""
    m = re.search(r"^### Next\s*$(.*?)(?=^### |\Z)", text, re.M | re.S)
    if not m:
        return []
    items = []
    for line in m.group(1).splitlines():
        s = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip()
        if s:
            items.append(s)
    return items


def team_key(name) -> str:
    """A team name as Lean compares it (aidlc_lean.py team_key): letters of any script, digits and _, the rest as _."""
    return re.sub(r"[^\w]", "_", str(name or "").strip("`* \t"))


def same_team(repo, a, b) -> bool:
    """Would the Lean installed in `repo` read a and b as one team? 0.3.1 on compares team names through team_key
    (`web-ui` is `web_ui`); 0.3.0 compared the Owner team cell as written."""
    return team_key(a) == team_key(b) if _release(version(repo)) >= (0, 3, 1) else a == b


def team_next_steps(text: str, team: str) -> list:
    """Items under '### Next (team <team>)' in the Session notes (Lean's per-team subsection; the team named the way
    Lean compares team names, so `web-ui` and `web_ui` are one team)."""
    for m in re.finditer(r"^#{2,4}[ \t]*Next[ \t]*\([ \t]*team[ \t:]+`?([A-Za-z0-9_.-]+)`?[ \t]*\)[ \t]*$"
                         r"(.*?)(?=^#{1,6} |\Z)", text, re.M | re.S | re.I):
        if team_key(m.group(1)) == team_key(team):
            return [s for s in (re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", l).strip() for l in m.group(2).splitlines())
                    if s]
    return []


def handover_problems(repo, intent, team=None) -> list:
    """Why aidlc-state.md is not yet a usable hand-over note (team: a split child's, whose note is its own
    '### Next (team <unit>)' subsection: the parent's Status and Next are shared by every child)."""
    p = state_path(repo, intent)
    if not os.path.exists(p):
        return ["%s is missing" % os.path.relpath(p, repo)]
    with open(p, encoding="utf-8") as f:
        return handover_text_problems(f.read(), team)


def handover_text_problems(text: str, team=None) -> list:
    if team:
        if not team_next_steps(text, team):
            return ["Session notes have no '### Next (team %s)' items: a child of a split keeps its own Status and "
                    "Next there" % team]
        return []
    probs = []
    if TEMPLATE_STATUS in text:
        probs.append("Session notes still say '%s': update Status" % TEMPLATE_STATUS)
    steps = next_steps(text)
    if not steps:
        probs.append("Session notes have no '### Next' items")
    elif all(_same_text(s) in _TEMPLATE_NEXT for s in steps):
        probs.append("Session notes' '### Next' is still Lean's template: write what the next session does")
    return probs


def _same_text(s) -> str:
    return " ".join(str(s).split())


_TEMPLATE_NEXT = frozenset(_same_text(s) for s in TEMPLATE_NEXT)
