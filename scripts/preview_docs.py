"""What PAY-9's and PAY-2's runs wrote, round by round, for the preview's 产出 drawer (scripts/preview_world.py).

Each document is a list of versions in the order the story writes them (Req.drafts: a text, or made from the
requirement when it is written, so release.md and the build-and-test summary name the commit they are for); each
code commit's files by its subject (Req.code). The emails in release.md's approval table are what Lean writes: the
drawer shows their pseudonyms.

PAY-9's verifies keep evidence (证据, brain-kit's proof kit): its lean.json wraps the tests in `proof.py run` and adds
`proof.py shots`, so each verify leaves the tests' whole output and a screenshot of each row of web's evidence plan,
in the kit's own format, sealed by the output_sha256 the VERIFY records (pay9_proof). The screenshots are real ones
of a mock of the refund page (preview_shots/, shoot.sh makes them).
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any, Callable

PASS = [("pytest -q", "41 秒"), ("ruff check .", "2 秒"), ("python -m mypy src", "9 秒")]
PAY9_TESTS = "python3 .kiro/brain/proof.py run tests -- pytest -q"
PAY9_SHOTS = "python3 .kiro/brain/proof.py shots"
PAY9_PASS = [(PAY9_TESTS, "43 秒"), ("ruff check .", "2 秒"), ("python -m mypy src", "9 秒"), (PAY9_SHOTS, "8 秒")]
# its lean.json: the checks, and what brain install leaves out of Lean's code fingerprint (the run's own records)
PAY9_LEAN = {"checks": [c for c, _ in PAY9_PASS], "fingerprint_exclude": ["aidlc-docs/**", ".kiro/**", "runs/**"]}
SHOTS = Path(__file__).resolve().parent / "preview_shots"


def _day(at: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(at))


def summary_of(checks: list[tuple[str, str]]) -> Callable[[Any, float], str]:
    """build-and-test-summary.md as `lean verify` writes it at *at*, its *checks* all passed."""
    def make(r: Any, at: float) -> str:
        n = sum(1 for e in r.events if e["event"] == "VERIFY") + 1
        rows = "".join("| `%s` | 通过 | %s |\n" % c for c in checks)
        return ("# 构建测试（第 %d 次）\n\n- 提交：`%s`\n- 结果：PASS，%d 项检查都过了\n\n| 检查 | 结果 | 用时 |\n|---|---|---|\n%s"
                % (n, (r.head(at) or "")[:12], len(checks), rows))
    return make


summary = summary_of(PASS)


def release(units: str, approvals: list[tuple[str, str]], since: list[str], open_items: list[str]) -> Callable[[Any, float], str]:
    """release.md as `lean release` writes it at *at*: the commit it is for, the stages, the approvals (*approvals*:
    kind and stage, with the owner's git email, as Lean writes it), and what changed since the last request."""
    def make(r: Any, at: float) -> str:
        fp = hashlib.sha256(("%s/fp/%s" % (r.key, at)).encode()).hexdigest()[:12]
        rows = "".join("| %s | %s | `%s` | %s@example.com | %s |\n" % (
            kind, r.doc(sid).rsplit("/", 1)[-1], (r.hash_at(r.doc(sid), at) or "")[:8], r.owner, _day(at)) for kind, sid in approvals)
        text = ("# Release: %s\n\nIntent: `%s` · written by `lean release` on %s\n\n"
                "- commit: `%s` (%s)\n- code fingerprint: `%s`\n"
                "- verify: PASS — `construction/build-and-test/build-and-test-summary.md`\n\n"
                "## Stages\n\n- Requirements Analysis — done · `requirements.md` · approved\n"
                "- Application Design — done · `application-design.md` · approved\n- %s\n"
                "- Build and Test — verify PASS: 3 checks\n- Release — this page; next: `request RELEASE` and the human's approval\n\n"
                "## Approvals\n\n| what | file | sha256 | by (team) | when |\n|---|---|---|---|---|\n%s"
                % (r.title, r.intent, _day(at), (r.head(at) or "")[:12], r.branch, fp, units, rows))
        if since:
            text += "\n## Since the last request\n\n" + "".join("- %s\n" % x for x in since)
        return text + "\n## Open items\n\n" + "".join("- %s\n" % x for x in open_items)
    return make


# ---------------------------------------------------------------- PAY-9 退款通知短信 (Scenario B)

PAY9_REQUIREMENTS = """# 需求：退款通知短信（PAY-9）

## 背景

退款成功以后，用户常打客服问「钱退了没有」。退款成功时给用户发一条短信，这类电话能少一半。

## 验收标准

- [ ] AC-1：退款成功以后 1 分钟内，给下单手机号发一条短信
- [ ] AC-2：短信写明退款金额和预计到账时间
- [ ] AC-3：发送失败重试三次，三次都失败记一条告警
- [ ] AC-4：退款详情页能看到短信发了没有
- [ ] AC-5：同一笔退款只发一次

## 不做

- 营销短信
- 部分退款的逐笔通知（下个版本再做）

## 假设

- 短信文案先用默认模板（D-1：运营下周给正式文案）
- 走统一通知服务，不直接接短信供应商
"""

PAY9_DESIGN = """# 应用设计：退款通知短信

## 组件

| 组件 | 放在哪 | 做什么 |
|---|---|---|
| `RefundSucceeded` 事件 | `services/api/refund/events.py` | 退款记账成功时发出 |
| `notify_refund` | `services/api/refund/notify.py` | 收到事件，拼短信，交给统一通知服务 |
| `SmsClient` | `services/api/refund/sms_client.py` | 调统一通知服务，失败重试三次 |
| 短信状态 | `web/src/refund/SmsStatus.tsx` | 退款详情页显示发了没有 |

## 流程

1. 退款服务记账成功，发出 `RefundSucceeded`
2. `notify_refund` 查手机号和金额，用默认模板拼短信
   - 同一笔退款只发一次：按退款单号去重
   - 失败重试三次，间隔 1、5、30 秒
3. 结果写进 `refund_notifications` 表，详情页读它

## 接口

- `GET /refunds/{id}/sms` 返回 `{"state": "sent" | "failed" | "pending", "at": "…"}`

> 短信通道的限流沿用统一通知服务的配置（Brain：P-7）
"""

PAY9_UNITS = """# 拆分单元

| 单元 | Paths | 做什么 |
|---|---|---|
| api | services/api/** | 事件、拼短信、发短信、记结果 |
| web | web/** | 退款详情页的短信状态 |

- api 先做；web 读 api 的 `GET /refunds/{id}/sms`
"""

PAY9_PLAN_API = """# 代码生成计划：api

- [x] `RefundSucceeded` 事件（`events.py`）
- [x] `notify_refund`：拼短信、按退款单号去重（`notify.py`）
- [x] `SmsClient`：失败重试三次（`sms_client.py`）
- [x] 测试：发一次、重试、去重（`tests/test_notify.py`）
"""

PAY9_PLAN_WEB = """# 代码生成计划：web

- [x] `fetchSmsState`：读 `GET /refunds/{id}/sms`（`api.ts`）
- [x] `SmsStatus`：发了、没发成、在发（`SmsStatus.tsx`）
- [x] 测试：三种状态各一个（`SmsStatus.test.tsx`）
"""

PAY9_PROOF_PLAN = """# 证据计划：web

| ID | AC | 看什么 | 步骤 |
|---|---|---|---|
| P-1 | AC-4 | 退款详情页显示「短信已发送」 | open /refunds/R-1001 · wait 短信已发送 |
| P-2 | AC-2 | 短信内容里有退款金额和预计到账时间 | open /refunds/R-1001 · click 短信内容 · wait 预计 |
| P-3 | AC-4 | 发送失败的退款显示「短信没发出去」 | open /refunds/R-1002 · wait 短信没发出去 |
"""
# added after the last verify: in the plan, not shot yet
PAY9_PROOF_PLAN_P4 = PAY9_PROOF_PLAN + "| P-4 | AC-5 | 同一笔退款只发一条短信 | open /refunds/R-1001 · wait 已发 1 条 |\n"
# what each row shows from the given verify on: the page before web's code (its texts are not there yet), then after
PAY9_SHOWN = [(1, {"P-1": ("before.png", "没等到「短信已发送」（10 秒）"), "P-2": ("before.png", "没找到「短信内容」（10 秒）"),
                   "P-3": ("before.png", "没等到「短信没发出去」（10 秒）")}),
              (2, {"P-1": ("sent.png", ""), "P-2": ("preview.png", ""), "P-3": ("sending.png", "没等到「短信没发出去」（10 秒）")})]
PAY9_PASSED = [(1, 4), (2, 9), (3, 11)]    # tests passed from that verify on (the third has D-2's test)


def pay9_proof(r: Any, at: float, K: Any) -> dict[str, str]:
    """The evidence PAY-9's verify at *at* leaves on the runner (K: brainkit.proof.kit(), the kit's own format): the
    tests' whole output, and a screenshot of each row of web's plan, in the clone's set folders, as Lean's checks
    print them; written into the run's files (committed with its next commit, as verify's records are). Returns
    {check: sha256 of what it printed}, which the VERIFY records."""
    n = sum(1 for e in r.events if e["event"] == "VERIFY") + 1
    clone = r.file.rsplit("/", 1)[-1][:-len(".jsonl")]
    code = K.CodeState(r.head(at), ())
    plan_path = K.plan_path(r.intent, "web")
    plan_text = next(x for t, x in reversed(r.texts[plan_path]) if t <= at)
    plan = K.parse_plan(plan_text.encode("utf-8"), "web", plan_path)
    shown = next(v for k, v in reversed(PAY9_SHOWN) if n >= k)
    passed = next(v for k, v in reversed(PAY9_PASSED) if n >= k)
    sealed = {}
    tests = K.ProofSet(K.SCHEMA, "tests", K.Kind.RUN, r.intent, clone, 0, code, (), (), "", ())
    body = ("============================= test session starts ==============================\ncollected %d items\n\n"
            "services/api/tests/test_notify.py %s [100%%]\n\n============================== %d passed in %.2fs "
            "==============================\n" % (passed, "." * passed, passed, 3.1 + 0.07 * n))
    out = K.render_set(body, tests).encode("utf-8")
    r.write(at, K.set_path(r.intent, clone, "tests") + "/" + K.OUTPUT, out)
    sealed[PAY9_TESTS] = hashlib.sha256(out).hexdigest()
    items, lines = [], []
    for row in plan.rows:
        name, note = shown[row.id]
        data = (SHOTS / name).read_bytes()
        file = "web-%s.png" % row.id
        r.write(at, K.set_path(r.intent, clone, "ui") + "/" + file, data)
        items.append(K.Item("web", row.id, row.ac, row.what, tuple(x.text() for x in row.steps), file,
                            hashlib.sha256(data).hexdigest(), len(data), K.Result.FAILED if note else K.Result.OK, note,
                            1.8, K.Page("/refunds/R-1001" if row.id != "P-3" else "/refunds/R-1002", "商城后台")))
        lines.append("%-6s %s %s %s" % ("没看到" if note else "ok", row.id, ",".join(row.ac), note or row.what))
    ui = K.ProofSet(K.SCHEMA, "ui", K.Kind.SHOTS, r.intent, clone, 0, code, tuple(items),
                    ((plan.unit, plan.path, plan.sha256),), "Chrome 142.0.0.0 · 800×420", ())
    out = K.render_set("\n".join(lines), ui).encode("utf-8")
    r.write(at, K.set_path(r.intent, clone, "ui") + "/" + K.OUTPUT, out)
    sealed[PAY9_SHOTS] = hashlib.sha256(out).hexdigest()
    return sealed


PAY9_REVIEW = """# 审查：退款通知短信

## 结论

可以发布。

## 看了什么

- [x] AC-1 到 AC-5 都有测试
- [x] 重试不会重复发：按退款单号去重
- [ ] 正式文案：等运营（D-1）

## 风险

- 统一通知服务限流时会排队，最慢 30 秒才发出去
"""

NOTIFY_V1 = '''"""退款成功以后给用户发短信（PAY-9）。"""

from .events import RefundSucceeded
from .sms_client import SmsClient

TEMPLATE = "您的退款 {amount} 元已原路退回，预计 {days} 个工作日内到账。"


def notify_refund(event: RefundSucceeded, client: SmsClient, sent: set) -> bool:
    """给下单手机号发一条退款短信；同一笔退款只发一次。"""
    if event.refund_id in sent:
        return False
    text = TEMPLATE.format(amount=event.amount, days=event.days)
    ok = client.send(event.phone, text)
    if ok:
        sent.add(event.refund_id)
    return ok
'''

NOTIFY_V2 = NOTIFY_V1.replace('''TEMPLATE = "您的退款''', '''OLD_ORDER_DAYS = 180   # D-2：下单超过半年的旧订单不发
TEMPLATE = "您的退款''').replace('''    """给下单手机号发一条退款短信；同一笔退款只发一次。"""
    if event.refund_id in sent:''', '''    """给下单手机号发一条退款短信；同一笔退款只发一次，旧订单不发。"""
    if event.refund_id in sent or event.order_age_days > OLD_ORDER_DAYS:''')

SMS_V1 = '''"""统一通知服务的短信通道：失败重试三次。"""

import time
import json
from typing import Callable

DELAYS = (1, 5, 30)


class SmsClient:
    def __init__(self, post: Callable[[str, dict], int]) -> None:
        self.post = post

    def send(self, phone: str, text: str) -> bool:
        for wait in (0,) + DELAYS:
            time.sleep(wait)
            if self.post("/notify/sms", {"to": phone, "text": text}) == 200:
                return True
        return False
'''

SMS_V2 = SMS_V1.replace("import time\nimport json\n", "import time\n")

EVENTS = '''"""退款服务发出的事件。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RefundSucceeded:
    refund_id: str
    phone: str
    amount: str
    days: int
    order_age_days: int = 0
'''

TEST_NOTIFY_V1 = '''from services.api.refund.events import RefundSucceeded
from services.api.refund.notify import notify_refund


class Fake:
    def __init__(self):
        self.sent = []

    def send(self, phone, text):
        self.sent.append((phone, text))
        return True


def test_one_refund_one_message():
    c, sent = Fake(), set()
    e = RefundSucceeded("RF-1", "13800000000", "25.00", 3)
    assert notify_refund(e, c, sent) and not notify_refund(e, c, sent)
    assert c.sent == [("13800000000", "您的退款 25.00 元已原路退回，预计 3 个工作日内到账。")]
'''

TEST_NOTIFY_V2 = TEST_NOTIFY_V1 + '''

def test_an_old_order_gets_no_message():
    c = Fake()
    assert not notify_refund(RefundSucceeded("RF-2", "13800000000", "9.90", 3, order_age_days=200), c, set())
    assert c.sent == []
'''

API_V1 = '''// 退款详情页读短信发了没有（GET /refunds/{id}/sms）
export type SmsState = { state: 'sent' | 'failed' | 'pending'; at: string | null }

export async function fetchSmsState(id: string): Promise<SmsState> {
  const r = await fetch(`/refunds/${encodeURIComponent(id)}/sms`)
  return r.json()
}
'''

API_V2 = API_V1.replace("  return r.json()\n", "  if (!r.ok) return { state: 'pending', at: null }\n  return (await r.json()) as SmsState\n")

STATUS_V1 = '''import { useEffect, useState } from 'react'
import { fetchSmsState, type SmsState } from './api'

const WORDS = { sent: '短信发了', failed: '短信没发成', pending: '短信在发' }

export function SmsStatus({ refundId }: { refundId: string }) {
  const [s, setS] = useState<SmsState | null>(null)
  useEffect(() => { fetchSmsState(refundId).then(setS) }, [refundId])
  if (!s) return null
  return <span className={`sms is-${s.state}`}>{WORDS[s.state]}</span>
}
'''

STATUS_V2 = STATUS_V1.replace("const WORDS = {", "const WORDS: Record<SmsState['state'], string> = {")

STATUS_TEST = '''import { render, screen } from '@testing-library/react'
import { SmsStatus } from './SmsStatus'

test('a sent message says so', async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ state: 'sent', at: '2026-11-08T12:00:00Z' }))
  render(<SmsStatus refundId="RF-1" />)
  expect(await screen.findByText('短信发了')).toBeTruthy()
})
'''


def pay9(r: Any) -> None:
    inc = [("INCEPTION", "requirements"), ("INCEPTION", "application-design")]
    opens = ["短信文案先用默认模板（D-1 已答：运营下周给正式文案）", "短信通道的限流沿用统一通知服务的配置（P-7）"]
    units = "Units Generation — done · 2 units: api, web · Code Generation (api) — done · Code Generation (web) — done"
    r.drafts.update({
        ("requirements", None): [PAY9_REQUIREMENTS], ("application-design", None): [PAY9_DESIGN], ("units", None): [PAY9_UNITS],
        ("code-generation", "api"): [PAY9_PLAN_API], ("code-generation", "web"): [PAY9_PLAN_WEB],
        ("build-and-test", None): [summary_of(PAY9_PASS)] * 5, ("review", None): [PAY9_REVIEW],
        ("release", None): [release(units, inc, [], opens), release(units, inc, ["旧订单不发短信（D-2 退回了第 1 轮；见 decisions.md 的 D-5）"], opens),
                            release(units, inc, ["旧订单不发短信（D-5）", "修好 CI：api 的 lint"], opens),
                            release(units, inc, ["旧订单不发短信（D-5）", "修好 CI：api 的 lint", "修好 CI：web 的类型检查"], opens)],
    })
    api, web = "services/api/refund/", "web/src/refund/"
    r.code.update({
        "api 的短信通知": {api + "notify.py": NOTIFY_V1, api + "sms_client.py": SMS_V1, api + "events.py": EVENTS,
                       "services/api/tests/test_notify.py": TEST_NOTIFY_V1},
        "web 的退款短信入口": {web + "api.ts": API_V1, web + "SmsStatus.tsx": STATUS_V1, web + "SmsStatus.test.tsx": STATUS_TEST},
        "按 D-2 改：旧订单不发短信": {api + "notify.py": NOTIFY_V2, "services/api/tests/test_notify.py": TEST_NOTIFY_V2},
        "修好 CI：api 的 lint": {api + "sms_client.py": SMS_V2},
        "修好 CI：web 的类型检查": {web + "api.ts": API_V2, web + "SmsStatus.tsx": STATUS_V2},
    })


# ---------------------------------------------------------------- PAY-2 退款单号校验 (Scenario C)

PAY2_REQUIREMENTS = """# 需求：退款单号校验（PAY-2）

## 验收标准

- [ ] AC-1：退款单号是 RF 加 9 位数字，最后一位是校验位
- [ ] AC-2：旧的 7 位单号也认（D-1 的回答）
- [ ] AC-3：重复的单号在入口就拒掉，返回 409
- [ ] AC-4：非法单号返回 400，写明是哪一位不对
"""

PAY2_DESIGN_V1 = """# 应用设计：退款单号校验

## 组件

| 组件 | 做什么 |
|---|---|
| `check_refund_id` | 纯函数：格式和校验位 |
| `RefundIdGuard` | 入口中间件：调 `check_refund_id`，再查重 |

## 接口

- `POST /refunds` 带 `refund_id`：非法返回 400，重复返回 409
- `GET /refunds?ids=` 一次查多个单号
"""

PAY2_DESIGN_V2 = PAY2_DESIGN_V1.replace("- `GET /refunds?ids=` 一次查多个单号\n", """- `GET /refunds?ids=&page=&per_page=` 一次查多个单号，分页
  - 每页最多 100 个，默认 50 个
  - 返回 `next_page`，没有下一页时是 `null`

## 第 2 轮改了什么

- 按退回改：批量查询加了分页
""")

PAY2_PLAN = """# 代码生成计划：refund-api

- [x] `check_refund_id`：RF + 9 位、旧的 7 位、校验位
- [x] `RefundIdGuard`：400 和 409
- [x] `GET /refunds` 分页
- [x] 测试：合法、非法、重复、旧单号
"""

PAY2_REVIEW = """# 审查：退款单号校验

## 结论

可以发布。

- [x] 校验是纯函数，接口和批量导入共用（Brain：P-7）
- [x] 分页上限 100，对账不会一次拉全表
"""


def pay2(r: Any) -> None:
    r.drafts.update({
        ("requirements", None): [PAY2_REQUIREMENTS], ("application-design", None): [PAY2_DESIGN_V1, PAY2_DESIGN_V2],
        ("code-generation", "refund-api"): [PAY2_PLAN], ("build-and-test", None): [summary] * 2, ("review", None): [PAY2_REVIEW],
        ("release", None): [release("Code Generation (refund-api) — done", [("REQUIREMENTS", "requirements"), ("DESIGN", "application-design")],
                                    [], ["旧的 7 位单号明年停用（D-1）"])],
    })
