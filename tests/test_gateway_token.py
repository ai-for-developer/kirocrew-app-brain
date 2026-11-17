"""How the app gets its KiroCrew token. KiroCrew 0.13 keeps an app's secret where no app reads it and deletes the old
<app dir>/.app_secret; code running in the gateway process asks its context (``ctx.app_token``) for a token instead."""

import asyncio

from appkit import GatewayError, mod

gateway = mod("gateway")


class Resp:
    def __init__(self, status, text, headers=None):
        self.status, self._text, self.headers = status, text, headers or {}

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class Client:
    """aiohttp's ClientSession as the Gateway uses it: answers in order, remembers each call's token."""

    def __init__(self, *answers):
        self.answers, self.tokens, self.posts, self.closed = list(answers), [], [], False

    def request(self, method, url, params=None, json=None):
        self.tokens.append((params or {}).get("token"))
        return self.answers.pop(0)

    def post(self, url, headers=None):
        self.posts.append(url)
        return Resp(200, '{"token": "from-secret"}')


def gw_with(client, tmp_path, **kw):
    gw = gateway.Gateway("brain", tmp_path / ".app_secret", base=lambda: "http://127.0.0.1:5476", **kw)
    gw._client = lambda: client
    return gw


def test_a_token_from_the_context_is_used_and_no_secret_file_is_read(tmp_path):
    asked = []

    async def app_token():
        asked.append(1)
        return "ctx-token"

    client = Client(Resp(200, '{"ok": true}'))
    gw = gw_with(client, tmp_path, token_source=app_token)
    assert asyncio.run(gw.request("GET", "/api/chat/folders")) == {"ok": True}
    assert client.tokens == ["ctx-token"] and client.posts == [] and asked == [1]


def test_a_refused_token_is_minted_again_from_the_context(tmp_path):
    minted = iter(["old", "new"])

    async def app_token():
        return next(minted)

    client = Client(Resp(401, '{"error": "token expired"}', {"X-Auth-Required": "true"}), Resp(200, "{}"))
    gw = gw_with(client, tmp_path, token_source=app_token)
    asyncio.run(gw.request("GET", "/api/chat/slots"))
    assert client.tokens == ["old", "new"]


def test_no_token_from_the_context_says_the_app_is_not_enabled(tmp_path):
    async def app_token():
        return ""

    gw = gw_with(Client(), tmp_path, token_source=app_token)
    try:
        asyncio.run(gw.request("GET", "/api/chat/slots"))
    except GatewayError as exc:
        assert exc.code == "not_enabled"
        assert "启用" in gateway.said(exc)
    else:
        raise AssertionError("a request went out without a token")


def test_a_missing_secret_file_is_said_as_such_not_as_kirocrew_not_answering(tmp_path):
    gw = gw_with(Client(), tmp_path)
    try:
        asyncio.run(gw.request("GET", "/api/chat/slots"))
    except GatewayError as exc:
        assert exc.code == "no_secret"
        assert gateway.said(exc) != gateway.SAID_ELSE
    else:
        raise AssertionError("a request went out without a token")


def test_without_a_context_token_the_secret_file_is_exchanged_as_before(tmp_path):
    (tmp_path / ".app_secret").write_text("s3cret\n", encoding="utf-8")
    client = Client(Resp(200, "{}"))
    gw = gw_with(client, tmp_path)
    asyncio.run(gw.request("GET", "/api/chat/slots"))
    assert client.posts == ["http://127.0.0.1:5476/api/apps/brain/token"] and client.tokens == ["from-secret"]
