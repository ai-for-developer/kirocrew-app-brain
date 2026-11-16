"""KiroCrew's sidebar: 「Brain 需求」, one folder per repo in it, one folder per requirement in that. The app moves its own
older requirement folders there once, and never a person's folder. The fake gateway refuses what KiroCrew refuses."""

import asyncio
from pathlib import Path

from appkit import FakeGateway, GatewayError, mod

gateway = mod("gateway")


def test_moving_a_folder_asks_kirocrew_to_change_only_its_parent(tmp_path):
    gw = gateway.Gateway("brain", tmp_path / ".app_secret")
    sent = []

    async def request(method, path, *, params=None, body=None):
        sent.append((method, path, body))
        return {}

    gw.request = request
    asyncio.run(gw.move_folder("ab7b85458c31", "f0d1"))
    assert sent == [("PATCH", "/api/chat/folders/ab7b85458c31", {"parent_id": "f0d1"})]


def test_the_fake_refuses_what_kirocrew_refuses_an_app():
    async def go():
        gw = FakeGateway()
        person = {"id": "p1", "name": "oms-brain-trial", "parent_id": "", "project_dir": ""}
        gw.folder_rows.append(person)
        root = await gw.create_folder("Brain 需求")
        a = await gw.create_folder("A", parent_id=root["id"])
        b = await gw.create_folder("B", parent_id=root["id"])
        refused = []
        for call in (lambda: gw.create_folder("x", parent_id="p1"), lambda: gw.create_folder("x", parent_id="nope"),
                     lambda: gw.move_folder("p1", root["id"]), lambda: gw.move_folder(a["id"], "p1"),
                     lambda: gw.move_folder(root["id"], a["id"]), lambda: gw.hide_folder("p1")):
            try:
                await call()
            except GatewayError as exc:
                refused.append((exc.status, exc.code))
        gw.folder_rows.append({"id": "p2", "name": "mine", "parent_id": b["id"]})
        try:
            await gw.move_folder(b["id"], a["id"])
        except GatewayError as exc:
            refused.append((exc.status, exc.code))
        await gw.move_folder(a["id"], b["id"])
        return refused, a

    refused, a = asyncio.run(go())
    assert refused == [(403, "folder_not_owned"), (400, "folder_parent_not_found"), (403, "folder_not_owned"),
                       (403, "folder_not_owned"), (400, "folder_cycle"), (403, "folder_not_owned"),
                       (403, "folder_not_owned")]
    assert a["parent_id"] == "f3"
