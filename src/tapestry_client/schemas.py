"""Pydantic models for Tapestry platform contract responses.

These schemas are intentionally decoupled from ``tapestry.app.schemas`` so the
client can be vendored into companion apps without pulling in the Tapestry
server package. Enum-typed fields are modelled as ``str`` to stay forward
compatible when Tapestry adds new enum values.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class IdentityMembership(BaseModel):
    organization_id: uuid.UUID
    organization_name: str
    organization_type: str
    role: str


class IdentityContext(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    is_admin: bool
    app_slug: str
    memberships: list[IdentityMembership] = Field(default_factory=list)


class UserProfile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: uuid.UUID
    display_name: str
    avatar_url: str | None = None


class OrganizationMember(BaseModel):
    user_id: uuid.UUID
    display_name: str
    avatar_url: str | None = None
    role: str


class AccessibleJar(BaseModel):
    jar_id: uuid.UUID
    jar_name: str
    organization_id: uuid.UUID
    visibility: str
    can_read: bool
    can_write: bool
    can_execute: bool


class JarRef(BaseModel):
    """Slim read-only view of a Jar returned by ``GET /platform/v1/jars``."""

    id: uuid.UUID
    display_name: str
    slug: str
    visibility: str


class PermissionCheck(BaseModel):
    allowed: bool
    reason: str


class EntityRef(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    jar_id: uuid.UUID
    entity_type_id: uuid.UUID
    name: str
    slug: str
    summary: str | None = None
    owner_user_id: uuid.UUID
    created_by_user_id: uuid.UUID
    created_at: datetime


class EntityTypeFieldRef(BaseModel):
    id: uuid.UUID
    entity_type_id: uuid.UUID
    field_key: str
    display_name: str
    field_type: str
    is_required: bool
    display_order: int
    ref_entity_type_slug: str | None = None
    enum_values: list[str] | None = None
    description: str | None = None


class EntityTypeRef(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str | None = None
    visibility: str
    contributing_app_id: uuid.UUID | None = None
    inherits_from_id: uuid.UUID | None = None
    version: str
    created_by_user_id: uuid.UUID | None = None
    created_at: datetime
    deprecated_at: datetime | None = None
    deprecation_reason: str | None = None
    deprecation_actor_user_id: uuid.UUID | None = None
    fields: list[EntityTypeFieldRef] = Field(default_factory=list)


class RelationshipRef(BaseModel):
    id: uuid.UUID
    from_entity_id: uuid.UUID
    to_entity_id: uuid.UUID
    relationship_type: str
    created_by_user_id: uuid.UUID
    created_at: datetime


class EntityFieldValue(BaseModel):
    entity_id: uuid.UUID
    field_key: str
    value: Any = None
    contributing_app_id: uuid.UUID | None = None
    updated_at: datetime


class EntityFieldUpdateResult(BaseModel):
    applied: list[EntityFieldValue] = Field(default_factory=list)
    proposed: list[str] = Field(default_factory=list)
    cargo_bay_item_id: uuid.UUID | None = None


class EmittedEvent(BaseModel):
    id: uuid.UUID
    idempotency_key: str | None
    event_type: str
    subject_entity_id: uuid.UUID | None = None
    actor_user_id: uuid.UUID | None = None
    organization_id: uuid.UUID | None = None
    occurred_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)
    visibility: str
    emitted_by_app_id: uuid.UUID | None = None
    created_at: datetime
    was_created: bool = True
    """True when this call actually persisted a new event, False on idempotent replay."""


class ServiceTokenRefresh(BaseModel):
    service_token: str
    expires_at: datetime
    rotated: bool = True


class DelegationToken(BaseModel):
    delegation_token: str
    token_type: str = "delegation"
    expires_at: datetime


# ---------------------------------------------------------------------------
# Pack registry
# ---------------------------------------------------------------------------


class PackItem(BaseModel):
    id: uuid.UUID
    pack_id: uuid.UUID
    slug: str
    display_name: str
    fields: dict[str, Any] = Field(default_factory=dict)
    pack_version_introduced: str
    pack_version_current: str
    created_at: datetime
    updated_at: datetime


class Pack(BaseModel):
    id: uuid.UUID
    slug: str
    app_id: uuid.UUID
    app_slug: str
    entity_type_slug: str
    display_name: str
    description: str | None = None
    version: str
    is_default: bool
    created_at: datetime
    updated_at: datetime
    items: list[PackItem] = Field(default_factory=list)


class PackInstallation(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    pack_id: uuid.UUID
    installed_version: str
    installed_at: datetime
    last_upgrade_at: datetime | None = None
    installed_by_user_id: uuid.UUID
    upgradeable: bool = False


class PackUpgrade(BaseModel):
    installation: PackInstallation
    added_items: list[PackItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Webhook subscriptions
# ---------------------------------------------------------------------------


class SubscriptionResponse(BaseModel):
    """Webhook subscription as returned by GET endpoints.

    Does not include ``signing_secret`` — the raw secret is only available
    in :class:`SubscriptionCreateResponse` at creation time.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    app_id: uuid.UUID
    organization_id: uuid.UUID
    url: str
    event_type_patterns: list[str]
    active: bool
    created_by_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class SubscriptionCreateResponse(BaseModel):
    """Response from ``POST /platform/v1/subscriptions``.

    The ``signing_secret`` field carries the raw HMAC secret for
    ``X-Tapestry-Signature`` verification. Persist it immediately —
    subsequent fetches do not return it.
    """

    subscription: SubscriptionResponse
    signing_secret: str
