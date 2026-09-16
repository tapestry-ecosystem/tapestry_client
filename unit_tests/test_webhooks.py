from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import TypedDict

import pytest
from pydantic import SecretStr

from tapestry_client.webhooks import WebhookVerificationError, verify_webhook

pytestmark = [pytest.mark.unit, pytest.mark.isolated_unit]


class VerificationArgs(TypedDict):
    body: bytes
    timestamp: str
    signature: str
    secrets: dict[uuid.UUID, SecretStr]
    expected_app_id: uuid.UUID
    now: float


@pytest.mark.parametrize(
    "change", [None, "body", "timestamp", "stale", "future", "app", "org", "empty"]
)
def test_webhook_authenticates_body_time_organization_and_producer(change: str | None) -> None:
    org, app = uuid.uuid4(), uuid.uuid4()
    payload = {
        "id": str(uuid.uuid4()),
        "event_type": "stash.strain.imported",
        "organization_id": str(org),
        "emitted_by_app_id": str(app),
        "metadata": {},
    }
    body = json.dumps(payload).encode()
    secret = "a-private-subscription-signing-secret"
    timestamp = "1000"
    signature = "sha256=" + hmac.new(secret.encode(), b"1000." + body, hashlib.sha256).hexdigest()
    kwargs: VerificationArgs = {
        "body": body,
        "timestamp": timestamp,
        "signature": signature,
        "secrets": {org: SecretStr(secret)},
        "expected_app_id": app,
        "now": 1000,
    }
    if change == "body":
        kwargs["body"] = body + b" "
    if change == "timestamp":
        kwargs["timestamp"] = "1001"
    if change == "stale":
        kwargs["now"] = 1301
    if change == "future":
        kwargs["now"] = 699
    if change == "app":
        kwargs["expected_app_id"] = uuid.uuid4()
    if change == "org":
        kwargs["secrets"] = {uuid.uuid4(): SecretStr(secret)}
    if change == "empty":
        kwargs["secrets"] = {org: SecretStr("")}
    if change:
        with pytest.raises(WebhookVerificationError):
            verify_webhook(**kwargs)
    else:
        assert verify_webhook(**kwargs).organization_id == org
