"""Verify the timestamped HMAC envelope sent by Tapestry subscriptions."""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, SecretStr, ValidationError


class WebhookVerificationError(ValueError):
    """The envelope is malformed, expired, or from an unexpected subscription."""


class WebhookEvent(BaseModel):
    id: uuid.UUID
    event_type: str
    organization_id: uuid.UUID
    emitted_by_app_id: uuid.UUID
    subject_entity_id: uuid.UUID | None = None
    metadata: dict[str, Any]


def verify_webhook(
    *,
    body: bytes,
    timestamp: str,
    signature: str,
    secrets: Mapping[uuid.UUID, SecretStr],
    expected_app_id: uuid.UUID | None,
    now: float | None = None,
) -> WebhookEvent:
    """Authenticate raw bytes, a five-minute time window, organization and producer.

    Secrets are indexed by the organization of the registered subscription.
    Consumers must also make their business operations idempotent: a valid
    delivery can be retried within the signature's time window.
    """
    try:
        event = WebhookEvent.model_validate_json(body)
        sent_at = int(timestamp)
    except (ValidationError, ValueError):
        raise WebhookVerificationError("Invalid webhook") from None
    secret = secrets.get(event.organization_id)
    if (
        secret is None
        or not secret.get_secret_value()
        or event.emitted_by_app_id != expected_app_id
        or abs((time.time() if now is None else now) - sent_at) > 300
    ):
        raise WebhookVerificationError("Invalid webhook")
    expected = (
        "sha256="
        + hmac.new(
            secret.get_secret_value().encode(), timestamp.encode() + b"." + body, hashlib.sha256
        ).hexdigest()
    )
    if not hmac.compare_digest(expected, signature):
        raise WebhookVerificationError("Invalid webhook")
    return event
