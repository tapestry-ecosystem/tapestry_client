from __future__ import annotations

import json
import uuid

import httpx
import pytest

from tapestry_client import TapestryClient
from tapestry_client.exceptions import TapestryNotFoundError

pytestmark = [pytest.mark.unit, pytest.mark.isolated_unit]

ORG = uuid.UUID("a0000000-0000-4000-8000-000000000001")
DOC = uuid.UUID("d0000000-0000-4000-8000-000000000002")
JAR = uuid.UUID("b0000000-0000-4000-8000-000000000003")
DATA = {
    "id": str(DOC),
    "organization_id": str(ORG),
    "jar_id": str(JAR),
    "title": "Legal invoice",
    "status": "ready",
    "document_kind": "invoice",
    "categories": ["legal"],
    "classification_source": "human",
    "classification_review_required": False,
    "created_at": "2026-09-12T12:00:00Z",
    "updated_at": "2026-09-12T12:00:00Z",
}


async def test_document_page_keeps_opaque_cursor_filters_and_auth_in_headers() -> None:
    cursor = "a+/=?&encoded"

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/platform/v1/documents"
        assert request.url.params["cursor"] == cursor
        assert request.url.params.get_list("kind") == ["invoice", "receipt"]
        assert request.url.params["organization_id"] == str(ORG)
        assert request.url.params["jar_id"] == str(JAR)
        assert request.url.params["category"] == "legal"
        assert request.headers["X-Delegation-Token"] == "caller"
        assert request.headers["X-Service-Token"] == "service"
        assert "caller" not in str(request.url)
        return httpx.Response(200, json={"data": [DATA], "meta": {"next_cursor": cursor}})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://test"
    ) as http:
        client = TapestryClient(service_token="service", transport=http)
        page = await client.list_documents(
            organization_id=ORG,
            jar_id=JAR,
            kinds=["invoice", "receipt"],
            category="legal",
            cursor=cursor,
            delegation_token="caller",
        )
    assert page.next_cursor == cursor
    assert page.items[0].id == DOC
    assert not hasattr(page.items[0], "extracted_text")


async def test_detail_and_human_classification_use_current_request_credentials() -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "PATCH":
            assert request.url.path == f"/platform/v1/documents/{DOC}/classification"
            assert b'"categories":["legal"]' in request.content
        return httpx.Response(200, json={"data": {**DATA, "extracted_text": "Source evidence"}})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://test"
    ) as http:
        client = TapestryClient(service_token="service", transport=http)
        detail = await client.get_document(DOC, delegation_token="first")
        saved = await client.update_document_classification(
            DOC, document_kind="invoice", categories=["legal"], delegation_token="second"
        )
    assert detail.extracted_text == "Source evidence" and saved.id == DOC
    assert [r.headers["X-Delegation-Token"] for r in calls] == ["first", "second"]


async def test_document_revocation_uses_standard_sdk_errors() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                404, json={"error": {"code": "not_found", "message": "Document unavailable"}}
            )
        ),
        base_url="http://test",
    ) as http:
        client = TapestryClient(service_token="service", transport=http)
        with pytest.raises(TapestryNotFoundError):
            await client.get_document(DOC, delegation_token="revoked")


async def test_declared_discovery_and_custom_kind_metadata() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Delegation-Token"] == "caller"
        if request.url.path.endswith("/requirements"):
            return httpx.Response(200, json={"data": {"kinds": ["invoice"], "tags": []}})
        if request.url.path.endswith("/kinds"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "value": "custom.warranty",
                            "label": "Warranty",
                            "description": "Coverage and exclusions",
                            "ai_selectable": True,
                            "allows_custom_label": False,
                        }
                    ]
                },
            )
        assert request.url.path == "/platform/v1/documents/discovery"
        assert request.url.params["jar_id"] == str(JAR)
        assert request.url.params["cursor"] == "opaque+/="
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        **DATA,
                        "document_kind": "other",
                        "other_document_kind": "Packing slip",
                        "jar_id": None,
                    }
                ],
                "meta": {},
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://test"
    ) as http:
        client = TapestryClient(service_token="service", transport=http)
        assert (await client.get_document_requirements(delegation_token="caller")).kinds == [
            "invoice"
        ]
        assert (await client.list_document_kinds(delegation_token="caller"))[
            0
        ].value == "custom.warranty"
        page = await client.discover_documents(
            organization_id=ORG, jar_id=JAR, cursor="opaque+/=", delegation_token="caller"
        )
        assert page.items[0].other_document_kind == "Packing slip" and page.items[0].jar_id is None
        with pytest.raises(ValueError):
            await client.discover_documents(
                organization_id=ORG, delegation_token="caller", limit=101
            )


@pytest.mark.parametrize("custom_label", [None, "Packing slip"])
async def test_custom_classification_preserves_optional_label(custom_label: str | None) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["document_kind"] == "other"
        assert body["categories"] == ["shipping"]
        assert request.headers["X-Delegation-Token"] == "current-user"
        assert request.headers["X-Service-Token"] == "service"
        if custom_label is None:
            assert "other_document_kind" not in body
        else:
            assert body["other_document_kind"] == custom_label
        return httpx.Response(200, json={"data": {**DATA, "other_document_kind": custom_label}})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://test"
    ) as http:
        client = TapestryClient(service_token="service", transport=http)
        saved = await client.update_document_classification(
            DOC,
            document_kind="other",
            categories=["shipping"],
            delegation_token="current-user",
            other_document_kind=custom_label,
        )
    assert saved.other_document_kind == custom_label


@pytest.mark.parametrize("limit", [0, -1, 101])
async def test_invalid_discovery_page_does_not_send_credentials(limit: int) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        pytest.fail("An invalid page limit must fail before sending a request")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://test"
    ) as http:
        client = TapestryClient(service_token="service", transport=http)
        with pytest.raises(ValueError, match="between 1 and 100"):
            await client.discover_documents(
                organization_id=ORG, delegation_token="current-user", limit=limit
            )
