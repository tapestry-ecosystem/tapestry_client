from __future__ import annotations

import json
import uuid
from decimal import Decimal

import httpx
import pytest

from tapestry_client import TapestryClient
from tapestry_client.exceptions import TapestryNotFoundError
from tapestry_client.purchases import PurchaseData

pytestmark = [pytest.mark.unit, pytest.mark.isolated_unit]

DOC = uuid.UUID("d0000000-0000-4000-8000-000000000002")
JOB = uuid.UUID("e0000000-0000-4000-8000-000000000003")


@pytest.mark.parametrize("document_type", ["receipt", "invoice", "order-confirmation"])
async def test_purchase_methods_flags_null_and_delegation(document_type: str) -> None:
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["X-Service-Token"] == "service"
        assert request.headers["X-Delegation-Token"] == "caller"
        if request.url.path.endswith("options"):
            return httpx.Response(
                200,
                json={"data": {"enabled": True, "text_available": True, "image_available": False}},
            )
        if request.method == "GET" and not request.url.path.endswith(str(JOB)):
            return httpx.Response(200, json={"data": None})
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": str(JOB),
                    "document_id": str(DOC),
                    "status": "queued" if request.method == "POST" else "completed",
                    "mode": "image",
                    "result": (
                        {
                            "data": {"document_type": document_type, "total": "108.25"},
                            "source_revision": "synthetic",
                        }
                        if request.method == "GET"
                        else None
                    ),
                    "created_at": "2026-09-12T12:00:00Z",
                    "updated_at": "2026-09-12T12:00:00Z",
                }
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://test"
    ) as http:
        client = TapestryClient(service_token="service", transport=http)
        assert (
            await client.get_purchase_extraction_options(document_id=DOC, delegation_token="caller")
        ).enabled
        assert (
            await client.latest_purchase_extraction(document_id=DOC, delegation_token="caller")
            is None
        )
        assert (
            await client.start_purchase_extraction(
                document_id=DOC, delegation_token="caller", mode="image", reprocess=True
            )
        ).id == JOB
        job = await client.get_purchase_extraction(
            document_id=DOC, delegation_token="caller", extraction_id=JOB
        )
        assert job.id == JOB and job.result is not None
        assert job.result.data.document_type == document_type
        assert job.result.data.total == Decimal("108.25")
        assert job.result.data.amount_paid is None and job.result.data.balance_due is None
    assert json.loads(calls[2].content) == {"mode": "image", "reprocess": True}
    data = PurchaseData(total="108.25")
    assert data.total == Decimal("108.25") and data.model_dump(mode="json")["total"] == "108.25"
    assert data.currency is None and data.purchase_date is None and data.amount_paid is None


async def test_purchase_revocation_standard_errors() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                404, json={"error": {"code": "not_found", "message": "Unavailable"}}
            )
        ),
        base_url="http://test",
    ) as http:
        with pytest.raises(TapestryNotFoundError):
            await TapestryClient(
                service_token="service", transport=http
            ).latest_purchase_extraction(document_id=DOC, delegation_token="revoked")


def test_sdk_rejects_inexact_binary_float_amounts() -> None:
    from pydantic import ValidationError

    from tapestry_client.purchases import PurchaseLineItem

    with pytest.raises(ValidationError):
        PurchaseData(total=0.1)
    with pytest.raises(ValidationError):
        PurchaseLineItem(quantity=123456789012.123456)
