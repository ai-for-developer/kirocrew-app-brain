"""backend/core/stages.py copies AI-DLC Lean's stage table, which the app never imports (lean_view.py says why). These
tests parse the Lean that brain-kit vendors (vendor/aidlc-lean/kit/.kiro/aidlc-lean/aidlc_lean.py) with ast, never
importing or running it, and fail when the copy drifts: a stage's id, order, phase, per-unit flag or document, an
approval kind no stage carries, an optional phase. The names are held to brain-kit's own (mrdesc.py STAGE_NAMES), the
ones the 变更说明 uses."""

import ast
import re

from appkit import BRAINKIT, mod
from brainfix import needs_brainkit

LEAN = BRAINKIT / "vendor" / "aidlc-lean" / "kit" / ".kiro" / "aidlc-lean" / "aidlc_lean.py"
MRDESC = BRAINKIT / "brainkit" / "mrdesc.py"
stages = mod("stages")


def assigned(path, name):
    """The value of the module-level assignment `name = ...` in *path*."""
    for node in ast.parse(path.read_text(encoding="utf-8"), str(path)).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return node.value
    raise AssertionError("%s assigns no %s" % (path.name, name))


def lean_stages():
    """Lean's STAGES, each row by the field names its Stage namedtuple declares."""
    fields = ast.literal_eval(assigned(LEAN, "Stage").args[1]).split()
    rows = []
    for call in assigned(LEAN, "STAGES").elts:
        row = dict(zip(fields, (ast.literal_eval(a) for a in call.args)))
        row.update({k.arg: ast.literal_eval(k.value) for k in call.keywords})
        rows.append(row)
    return rows


@needs_brainkit
def test_the_stage_table_is_lean_s():
    ours = [(s.id, s.phase, s.per_unit, s.doc) for s in stages.LEAN]
    assert ours == [(r["id"], r["phase"], r["per_unit"], r["doc"]) for r in lean_stages()]


@needs_brainkit
def test_every_approval_lean_asks_for_covers_a_stage():
    assert {g for s in stages.STAGES for g in s.gates} == set(ast.literal_eval(assigned(LEAN, "APPROVAL_KINDS")))


@needs_brainkit
def test_lean_s_optional_phases_are_optional_here():
    optional = ast.literal_eval(assigned(LEAN, "PHASES").args[0])   # OrderedDict(((phase, name), ...))
    assert {p for p, _ in optional} == {"ideation", "operations"}
    assert [s.id for s in stages.LEAN if s.phase in ("ideation", "operations") and not s.optional] == []


@needs_brainkit
def test_a_stage_is_named_as_brain_kit_names_it():
    assert {s.id: s.label for s in stages.LEAN} == ast.literal_eval(assigned(MRDESC, "STAGE_NAMES"))


def test_a_band_names_its_stages_as_the_table_does():
    named = {(s.n, s.label): s.phase for s in stages.STAGES}
    for p in stages.PHASES:
        for n, label in re.findall(r"(\d\.\d) ([^·]+?)(?= ·|$)", p.sub):
            assert named.get((n, label)) == p.key, (p.key, n, label)
