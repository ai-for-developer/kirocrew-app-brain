"""测试环境报错's log side (team/stagelog.py), offline: which lines of a staging log are errors and which are noise, the
signature each gets and how they group, a traceback still being written, and the file reader's cursor (read on from
it, a partial line left, a truncated or rotated log read from its start). bridge.json's staging block as its readers."""

import json
import os

import pytest

from appkit import mod

LOG = """\
2026-11-21 10:00:00,001 INFO  app.web: GET /health ok
2026-11-21 10:00:00,002 WARNING app.pay: retrying after an error
2026-11-21 10:00:01,100 ERROR app.orders: discount failed for order 1234
Traceback (most recent call last):
  File "/srv/releases/20261121/venv/lib/python3.12/site-packages/flask/app.py", line 880, in full_dispatch_request
    rv = self.dispatch_request()
  File "/srv/releases/20261121/shop/orders/views.py", line 41, in create
    total = discount.apply(order)
  File "/srv/releases/20261121/shop/orders/discount.py", line 12, in apply
    return order.total / order.rate
ZeroDivisionError: division by zero
127.0.0.1 - - [21/Nov/2026:10:00:01 +0000] "POST /api/orders HTTP/1.1" 500 291
2026-11-21 10:00:02,000 ERROR app.pay: gateway timeout after 30s (order 77)
2026-11-21 10:00:02,100 INFO  app.web: GET /health ok
2026-11-21 10:00:02,200 INFO  app.web: GET /health ok
2026-11-21 10:00:02,300 INFO  app.web: GET /health ok
10.0.0.5 - - [21/Nov/2026:10:00:03 +0000] "GET /api/orders/88/receipt?x=1 HTTP/1.1" 502 0
10.0.0.5 - - [21/Nov/2026:10:00:04 +0000] "GET /api/orders/88 HTTP/1.1" 404 12
2026-11-21 10:00:05,000 INFO  app.web: the user typed ERROR in the search box
\x1b[32mINFO\x1b[0m:     127.0.0.1:5000 - "GET /api/cart HTTP/1.1" 200 OK
{"level": "info", "msg": "paid", "status": 200}
{"levelname": "ERROR", "message": "refund 991 failed", "exc_info": "Traceback (most recent call last):\\n  File \\"/srv/shop/refunds.py\\", line 9, in refund\\n    raise RefundError(order)\\nshop.errors.RefundError: 991"}
{"level": "warning", "msg": "slow", "status": 503, "method": "GET", "path": "/api/stock/5"}
2026/11/21 10:00:06 [error] 31#31: *7 upstream timed out (110: Connection timed out)
"""


def lines(text):
    return text.splitlines()


def test_a_traceback_an_error_line_and_a_5xx_are_errors_and_info_warnings_and_4xx_are_noise():
    sl = mod("team.stagelog")
    events, used = sl.errors_in(lines(LOG))
    assert used == len(lines(LOG))
    assert [e.signature for e in events] == [
        "ZeroDivisionError at orders/discount.py in apply",
        "app.pay: gateway timeout after <n>s (order <n>)",
        "HTTP 502 GET /api/orders/<n>/receipt",
        "shop.errors.RefundError at shop/refunds.py in refund",
        "HTTP 503 GET /api/stock/<n>",
        "<n>#<n>: *<n> upstream timed out (<n>: Connection timed out)",
    ]
    tb = events[0].lines
    # the ERROR line that logged it, the whole traceback, and the request it answered with a 500
    assert tb[0].endswith("ERROR app.orders: discount failed for order 1234") and tb[-2] == "ZeroDivisionError: division by zero"
    assert tb[-1].endswith('"POST /api/orders HTTP/1.1" 500 291') and len(tb) == 10
    assert events[1].lines == ("2026-11-21 10:00:02,000 ERROR app.pay: gateway timeout after 30s (order 77)",)
    assert events[3].lines[1:] == ("Traceback (most recent call last):", '  File "/srv/shop/refunds.py", line 9, in refund',
                                   "    raise RefundError(order)", "shop.errors.RefundError: 991")
    coloured, _ = sl.errors_in(["\x1b[31mERROR\x1b[0m:    Exception in ASGI application"])
    assert [(e.signature, e.lines) for e in coloured] == [("Exception in ASGI application",
                                                           ("ERROR:    Exception in ASGI application",))]


def test_a_5xx_joins_the_traceback_just_before_it_and_one_further_on_is_its_own():
    sl = mod("team.stagelog")
    tb = ["Traceback (most recent call last):", '  File "/srv/app/cart.py", line 3, in add', "KeyError: 'sku'"]
    req = '10.0.0.1 - - [21/Nov/2026:10:00:00 +0000] "POST /api/cart HTTP/1.1" 500 0'
    joined, _ = sl.errors_in(tb + ["INFO x", req])
    apart, _ = sl.errors_in(tb + ["INFO x", "INFO y", "INFO z", req])
    assert [e.signature for e in joined] == ["KeyError at app/cart.py in add"] and joined[0].lines[-1] == req
    assert [e.signature for e in apart] == ["KeyError at app/cart.py in add", "HTTP 500 POST /api/cart"]


def test_errors_group_by_signature_whatever_their_numbers_ids_release_folder_or_line():
    sl = mod("team.stagelog")

    def tb(root, line, exc):
        return ["Traceback (most recent call last):", '  File "%s/shop/orders/discount.py", line %d, in apply' % (root, line),
                "    return a / b", exc]
    log = (tb("/srv/releases/20261121", 12, "ZeroDivisionError: division by zero")
           + ["INFO a", "2026-11-21 10:00:02 ERROR app.pay: gateway timeout after 30s (order 77, trace 9f3a2b7c1d)", "INFO b"]
           + tb("/srv/releases/20261122", 14, "ZeroDivisionError: float division by zero")
           + ["2026-11-21 10:00:09 ERROR app.pay: gateway timeout after 45s (order 78, trace 0c1d2e3f4a)", "INFO c"]
           + tb("/srv/releases/20261122", 14, "TypeError: unsupported operand type(s)"))
    events, _ = sl.errors_in(log)
    groups = sl.grouped(events, 1000.0, "abc1234")
    assert [(g.signature, g.count) for g in groups] == [
        ("ZeroDivisionError at orders/discount.py in apply", 2),
        ("app.pay: gateway timeout after <n>s (order <n>, trace <id>)", 2),
        ("TypeError at orders/discount.py in apply", 1)]
    first = groups[0]
    assert (first.first_seen, first.last_seen, first.deployed_sha) == (1000.0, 1000.0, "abc1234")
    assert first.sample[1] == '  File "/srv/releases/20261122/shop/orders/discount.py", line 14, in apply'   # the newest
    later = first.merged(sl.grouped(events[:1], 2000.0, "def5678")[0])
    assert (later.first_seen, later.last_seen, later.count, later.deployed_sha) == (1000.0, 2000.0, 3, "def5678")


def test_a_message_keeps_its_words_without_the_quoted_values_e_mails_paths_and_long_tokens_each_error_has_its_own():
    sl = mod("team.stagelog")
    logged = ["ERROR app.auth: no account for alice@example.com",
              "ERROR app.auth: no account for bob.smith+qa@mail.example.org",
              "ERROR app.cfg: missing key 'payment_url' in \"/etc/shop/app.yaml\"",
              "ERROR app.cfg: missing key `refund_url` in “/etc/shop/other.yaml”",
              "ERROR app.files: cannot open /srv/uploads/2026/alice/receipt.pdf: permission denied",
              "ERROR app.files: cannot open C:\\data\\bob\\r.pdf: permission denied",
              "ERROR app.files: cannot open ~/inbox/carol.csv: permission denied",
              "ERROR app.api: token eyJhbGciOiJIUzI1NiJ9xYz9Q2kL7mN0pR3sT6uV expired",
              "ERROR app.api: token QWxhZGRpbjpvcGVuIHNlc2FtZQ2026_ab-cd expired",
              "ERROR app.web: can't render the page for user 'carol' and/or 'dave'"]
    events, _ = sl.errors_in(logged)
    assert [e.signature for e in events] == [
        "app.auth: no account for <email>", "app.auth: no account for <email>",
        "app.cfg: missing key <str> in <str>", "app.cfg: missing key <str> in <str>",
        "app.files: cannot open <path>: permission denied", "app.files: cannot open <path>: permission denied",
        "app.files: cannot open <path>: permission denied",
        "app.api: token <token> expired", "app.api: token <token> expired",
        "app.web: can't render the page for user <str> and/or <str>"]
    kept, _ = sl.errors_in(["ERROR app.pay: retry_payment_request_handler_for_refunds gave up after 3 tries at /health",
                            "ERROR 订单处理失败：找不到用户'erin'的地址"])
    assert [e.signature for e in kept] == ["app.pay: retry_payment_request_handler_for_refunds gave up after <n> tries at /health",
                                           "订单处理失败：找不到用户<str>的地址"]


def test_a_traceback_still_being_written_is_left_for_the_next_read_unless_the_read_began_with_it():
    sl = mod("team.stagelog")
    head = ["INFO up", "2026-11-21 10:00:01 ERROR app: boom", "Traceback (most recent call last):",
            '  File "/srv/app/x.py", line 3, in run']
    events, used = sl.errors_in(head)
    assert (events, used) == ([], 1)              # from its ERROR line on, read again next round
    events, used = sl.errors_in(head[1:] + ["ValueError: bad", "INFO next"])
    assert [e.signature for e in events] == ["ValueError at app/x.py in run"] and used == 5
    events, used = sl.errors_in(head[1:])         # nothing more came: taken as it is
    assert [e.signature for e in events] == ["Traceback at app/x.py in run"] and used == 3


def test_a_chained_traceback_is_one_error_keyed_by_the_last_exception_and_its_innermost_project_frame():
    sl = mod("team.stagelog")
    log = ["app-1  | Traceback (most recent call last):",
           'app-1  |   File "/app/shop/pay.py", line 8, in charge',
           "app-1  |     return client.post(body)",
           'app-1  |   File "/usr/local/lib/python3.12/site-packages/httpx/_client.py", line 1145, in post',
           "app-1  |     raise ConnectError(msg)",
           "app-1  | httpx.ConnectError: [Errno 111] Connection refused",
           "app-1  | ",
           "app-1  | The above exception was the direct cause of the following exception:",
           "app-1  | ",
           "app-1  | Traceback (most recent call last):",
           'app-1  |   File "/app/shop/orders.py", line 20, in checkout',
           "app-1  |     pay.charge(order)",
           'app-1  |   File "/app/shop/pay.py", line 10, in charge',
           "app-1  |     raise PaymentDown() from exc",
           "app-1  | shop.pay.PaymentDown",
           "app-1  | INFO: shutting down"]
    events, used = sl.errors_in(log)
    assert [e.signature for e in events] == ["shop.pay.PaymentDown at shop/pay.py in charge"] and used == len(log)
    assert len(events[0].lines) == 15
    inner, _ = sl.errors_in([x[len("app-1  | "):] for x in log[:6]])     # raised inside a library: the project's frame
    assert [e.signature for e in inner] == ["httpx.ConnectError at shop/pay.py in charge"]


def test_an_excerpt_keeps_at_most_80_lines_its_start_and_its_end():
    sl = mod("team.stagelog")
    frames = [x for n in range(200) for x in ('  File "/app/r.py", line %d, in f%d' % (n, n), "    f()")]
    events, _ = sl.errors_in(["Traceback (most recent call last):"] + frames + ["RecursionError: too deep"])
    shown = events[0].lines
    assert len(shown) == 80 and shown[0] == "Traceback (most recent call last):" and shown[-1] == "RecursionError: too deep"
    assert shown[10] == "…（中间 323 行略去）" and events[0].signature == "RecursionError at app/r.py in f199"


def test_the_file_reader_reads_on_from_its_cursor_leaves_a_partial_line_and_starts_over_on_a_new_or_shorter_file(tmp_path):
    sl = mod("team.stagelog")
    path = tmp_path / "app.log"
    path.write_text("old 1\nold 2\n")
    log = sl.FileLog(str(path))
    first = log.read(None)                        # the first read starts at the end: what came before is not ours
    assert first.lines == () and first.cursors[0]["at"] == len("old 1\nold 2\n")
    with open(path, "a") as fh:
        fh.write("new 1\nnew 2\nhalf")
    got = log.read(first.cursors[-1])
    assert got.lines == ("new 1", "new 2")
    with open(path, "a") as fh:
        fh.write(" done\n")
    got = log.read(got.cursors[-1])
    assert got.lines == ("half done",)
    again = log.read(got.cursors[1])
    assert again.lines == ()
    path.write_text("after truncate\n")           # copytruncate: the same file, shorter
    assert log.read(again.cursors[-1]).lines == ("after truncate",)
    cur = log.read(log.read(again.cursors[-1]).cursors[-1]).cursors[-1]
    os.rename(path, tmp_path / "app.log.1")       # rotated: a new file at the path
    path.write_text("fresh 1\n")
    assert log.read(cur).lines == ("fresh 1",)
    small = sl.FileLog(str(path), limit=8)
    path.write_text("aaa\nbbb\nccc\n")
    start = {"file": small.read(None).cursors[0]["file"], "at": 0}
    part = small.read(start)
    assert part.lines == ("aaa", "bbb") and small.read(part.cursors[-1]).lines == ("ccc",)


def test_a_staging_block_becomes_its_readers_and_a_wrong_one_is_said_in_words(tmp_path):
    sl = mod("team.stagelog")
    version = tmp_path / "VERSION"
    version.write_text("3F2A9C1D0E4B5A6978C8D7E6F5A4B3C2D1E0F9A8\n")
    st = sl.staging_of({"log": {"kind": "file", "path": str(tmp_path / "app.log")}, "version": {"kind": "file", "path": str(version)}})
    assert isinstance(st.log, sl.FileLog) and st.version.read() == "3f2a9c1d0e4b5a6978c8d7e6f5a4b3c2d1e0f9a8"
    version.write_text("v1.2.3\n")
    with pytest.raises(ValueError, match="不是一个提交号"):
        st.version.read()
    for raw, said in (([], "staging 要写成"), ({"log": {"kind": "http", "url": "x"}, "version": {"kind": "file", "path": "/v"}},
                                                 "staging.log 的 kind 要是 file"),
                      ({"log": {"kind": "file"}, "version": {"kind": "file", "path": "/v"}}, "staging.log：要写 path")):
        with pytest.raises(ValueError, match=said):
            sl.staging_of(json.loads(json.dumps(raw)))
