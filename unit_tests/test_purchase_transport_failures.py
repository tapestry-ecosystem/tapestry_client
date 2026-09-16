from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest

from tapestry_client import TapestryClient
from tapestry_client.exceptions import (
    TapestryAuthError,
    TapestryClientError,
    TapestryConflictError,
    TapestryRateLimitError,
    TapestryServerError,
    TapestryValidationError,
)

pytestmark = [pytest.mark.unit, pytest.mark.isolated_unit]
DOC = uuid.UUID("d0000000-0000-4000-8000-000000000002")


@pytest.mark.parametrize(
    ("status", "exception"),
    [
        (401, TapestryAuthError),
        (403, TapestryAuthError),
        (409, TapestryConflictError),
        (422, TapestryValidationError),
        (429, TapestryRateLimitError),
        (503, TapestryServerError),
    ],
)
async def test_failed_purchase_start_never_replays_a_mutation(
    status: int, exception: type[TapestryClientError]
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            status,
            headers={"Retry-After": "7"},
            json={"error": {"code": "synthetic_failure", "message": "Cannot start extraction"}},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://test"
    ) as http:
        client = TapestryClient(service_token="synthetic-service", transport=http)
        with pytest.raises(exception) as caught:
            await client.start_purchase_extraction(document_id=DOC, delegation_token="caller")
    assert caught.value.status_code == status
    assert caught.value.code == "synthetic_failure"
    if isinstance(caught.value, TapestryRateLimitError):
        assert caught.value.retry_after_seconds == 7
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.path == f"/platform/v1/documents/{DOC}/purchase-extractions"
    assert json.loads(requests[0].content) == {"mode": "text", "reprocess": False}


async def test_timeout_after_purchase_submission_does_not_send_another_post() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ReadTimeout("Synthetic response timeout", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://test"
    ) as http:
        client = TapestryClient(service_token="synthetic-service", transport=http)
        with pytest.raises(TapestryClientError) as caught:
            await client.start_purchase_extraction(document_id=DOC, delegation_token="caller")
    assert isinstance(caught.value.__cause__, httpx.ReadTimeout)
    assert caught.value.status_code is None
    assert len(requests) == 1 and requests[0].method == "POST"


async def test_concurrent_purchase_requests_keep_each_callers_delegation() -> None:
    other = uuid.UUID("d0000000-0000-4000-8000-000000000003")
    expected = {str(DOC): "first-user", str(other): "second-user"}
    requests: list[httpx.Request] = []
    both_arrived = asyncio.Event()

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 2:
            both_arrived.set()
        await asyncio.wait_for(both_arrived.wait(), timeout=2)
        document_id = request.url.path.split("/")[-2]
        assert request.headers["X-Delegation-Token"] == expected[document_id]
        assert request.headers["X-Service-Token"] == "synthetic-service"
        assert not request.url.query
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": document_id,
                    "document_id": document_id,
                    "status": "queued",
                    "mode": "text",
                    "created_at": "2026-09-15T12:00:00Z",
                    "updated_at": "2026-09-15T12:00:00Z",
                }
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://test"
    ) as http:
        client = TapestryClient(service_token="synthetic-service", transport=http)
        results = await asyncio.gather(
            *(
                client.start_purchase_extraction(document_id=uuid.UUID(doc), delegation_token=token)
                for doc, token in expected.items()
            )
        )
    assert {result.document_id for result in results} == {DOC, other}
    assert len(requests) == 2
