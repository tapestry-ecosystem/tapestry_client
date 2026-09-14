"""Mail transport keeps canonical IDs and per-call delegation; reads discard secrets."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tapestry_client import TapestryClient


@pytest.mark.asyncio
async def test_mail_reads_strip_secrets_and_write_preserves_password() -> None:
    seen: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": 27,
                    "name": "Receipts",
                    "username": "user@example.test",
                    "imap_server": "imap.example.test",
                    "password": "upstream-secret",
                }
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://core"
    ) as http:
        client = TapestryClient(service_token="synthetic-service", transport=http)
        read = await client.get_mail_account(27, delegation_token="first-user")
        assert "password" not in read.model_dump()
        assert "upstream-secret" not in repr(read)
        result = await client.save_mail_account(
            account_id=27,
            data={
                "name": "Receipts",
                "username": "user@example.test",
                "imap_server": "imap.example.test",
                "password": "new-write-only-secret",
            },
            delegation_token="second-user",
        )
        assert result.id == 27
    assert [r.method for r in seen] == ["GET", "PATCH"]
    assert all(r.url.path == "/platform/v1/mail/accounts/27" for r in seen)
    assert [r.headers["X-Delegation-Token"] for r in seen] == ["first-user", "second-user"]
    assert all(r.headers["X-Service-Token"] == "synthetic-service" for r in seen)
    assert all("Authorization" not in r.headers for r in seen)
    assert all(not r.url.query for r in seen)
    assert json.loads(seen[1].content)["password"] == "new-write-only-secret"


@pytest.mark.asyncio
async def test_mail_rule_and_actions_use_canonical_contract() -> None:
    seen: list[tuple[str, str]] = []

    def transport(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        data: dict[str, Any]
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.url.path.endswith("/test"):
            data = {"connected": False}
        elif request.url.path.endswith("/fetch"):
            data = {"queued": True}
        else:
            data = {"id": 31, "account": 27, "name": "Receipt rule", "filter_subject": "receipt"}
        return httpx.Response(200, json={"data": data})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://core"
    ) as http:
        client = TapestryClient(service_token="synthetic-service", transport=http)
        rule = await client.save_mail_rule(
            rule_id=31, data={"name": "Receipt rule", "account": 27}, delegation_token="user"
        )
        assert rule.id == 31 and rule.account == 27
        assert await client.test_mail_account(27, delegation_token="user") is False
        await client.fetch_mail_account(27, delegation_token="user")
        await client.delete_mail_rule(31, delegation_token="user")
        await client.delete_mail_account(27, delegation_token="user")
    assert seen == [
        ("PATCH", "/platform/v1/mail/rules/31"),
        ("POST", "/platform/v1/mail/accounts/27/test"),
        ("POST", "/platform/v1/mail/accounts/27/fetch"),
        ("DELETE", "/platform/v1/mail/rules/31"),
        ("DELETE", "/platform/v1/mail/accounts/27"),
    ]
