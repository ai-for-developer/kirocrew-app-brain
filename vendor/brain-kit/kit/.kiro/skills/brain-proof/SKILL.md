---
name: brain-proof
description: Evidence (证据) for review. Use when a requirement's acceptance criteria can be seen in the running app, before `lean verify`, and when `brain mr-desc` says evidence is missing, failed or of older code.
---
# Evidence the reviewer looks at

Reviewers accept a requirement by looking at evidence: a screenshot of the running app for each criterion a person
can see, and the whole test output. You do not take that evidence yourself. `lean verify` takes it every time it
runs, with the `.kiro/brain/proof.py` checks people put in `aidlc-docs/lean.json`. Your part is the plan: what to
show, and how to reach it.

If `brain doctor` says this project's verify keeps no evidence, tell the person (only they can add the checks) and
go on. `brain mr-desc` says so to the reviewers too.

## 1. Write the plan

Write `aidlc-docs/<intent>/construction/<unit>/proof-plan.md` while you write the code. The unit is `app` when the
intent has no `unit-of-work.md`; on a split child it is your own unit (never write another unit's plan):

```markdown
# 证据计划：PAY-12 退款

| ID | AC | 看什么 | 步骤 |
|---|---|---|---|
| P-1 | AC-1 | 已付款订单有「退款」按钮 | open /orders/123 · wait 退款 |
| P-2 | AC-2 | 点退款以后状态是「已退款」 | open /orders/123 · click 退款 · wait 已退款 |
| P-3 | AC-4 | 旧订单没有退款单号时不显示按钮 | open /orders/77 · wait 订单 77 |
```

- One row per state a reviewer should see. `AC`: the criteria it shows (`AC-2`, `BR-1`), or `—`.
- `看什么`: one line in the reviewer's words. It is printed next to the screenshot.
- `步骤`: separated by ` · `. The screenshot is taken after the last step, and also after a step that fails, so it
  shows what was there. There are four step words:
  - `open <path>`: a path of the app (starts with `/`; never a full URL). Every row starts with one.
  - `click <text>`: a button or link by its visible text.
  - `type <field> = <text>`: a field by its label, placeholder or name.
  - `wait <text>`: until the text shows (10 s). End each row with a `wait` for the thing the row proves: a
    screenshot that does not show it is then marked 没看到.
- Keep the ids. A row you take out stops counting at the next verify. At most 20 rows are shot per verify.
- Steps never run commands and never hold a password. If a state needs data the app does not have, say so in the
  intent's Open questions; do not weaken the row.

## 2. Look while you work

```bash
python3 .kiro/brain/proof.py look "open /orders/123 · click 退款 · wait 已退款"
```

It starts the app the way verify does, runs the steps, and prints the screenshot's path, where the page ended up,
each step's result and the visible text. It writes nothing in the repository: what `look` shows is never evidence.
`--no-launch` uses a server you started yourself at the address in `.brain/kit.json` `proof.url`. Never point `--out`
inside the repository: Lean counts any stray file there as code, so the evidence would read as old.

## 3. Verify, commit, read

- `lean verify` runs the tests and takes the screenshots (a few seconds more).
- Commit the records after it, as usual: `git add aidlc-docs/ && git commit -m "<KEY>: verify"`. The evidence is
  under `construction/build-and-test/proof/`, so that commit needs no new verify.
- Run `brain mr-desc` and read 「验收标准和证据」. For each line that says evidence is missing (没有证据, 还没拍,
  没拍成), failed (没看到) or of older code (旧代码的): fix the plan or the code, then run `lean verify` again.

Files under `construction/build-and-test/proof/` are written by verify, and Lean's own record holds their
fingerprint. A file changed afterwards stops counting, and the description says so. Rerun `lean verify` instead of
editing them.
