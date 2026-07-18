"""Reference client library for the Tapestry platform contracts.

Companion apps consume Tapestry exclusively through ``/platform/v1/`` and
``/api/v1/auth/delegate``. This package provides a typed, async client that
wraps every contract, handles service-token refresh, and exposes the optional
cache tiers documented in the v0.2 companion-app template.

Usage::

    from tapestry_client import TapestryClient

    client = TapestryClient(
        base_url="https://tapestry.local",
        service_token="tap_service_...",
    )
    async with client:
        identity = await client.resolve_identity(delegation_token)
        entity = await client.create_entity(
            entity_type_id=uuid.UUID(...),
            jar_id=identity.memberships[0].organization_id,
            name="Laptop",
            delegation_token=delegation_token,
        )
"""

from __future__ import annotations

from tapestry_client.cache import TTLCache
from tapestry_client.client import TapestryClient
from tapestry_client.exceptions import (
    TapestryAuthError,
    TapestryClientError,
    TapestryConflictError,
    TapestryNotFoundError,
    TapestryServerError,
    TapestryValidationError,
)
from tapestry_client.schemas import (
    AccessibleJar,
    DelegationToken,
    EmittedEvent,
    EntityFieldUpdateResult,
    EntityFieldValue,
    EntityRef,
    EntityTypeFieldRef,
    EntityTypeRef,
    IdentityContext,
    IdentityMembership,
    OrganizationMember,
    PermissionCheck,
    RelationshipRef,
    ServiceTokenRefresh,
    SubscriptionCreateResponse,
    SubscriptionResponse,
    UserProfile,
)

__all__ = [
    "AccessibleJar",
    "DelegationToken",
    "EmittedEvent",
    "EntityFieldUpdateResult",
    "EntityFieldValue",
    "EntityRef",
    "EntityTypeFieldRef",
    "EntityTypeRef",
    "IdentityContext",
    "IdentityMembership",
    "OrganizationMember",
    "PermissionCheck",
    "RelationshipRef",
    "ServiceTokenRefresh",
    "SubscriptionCreateResponse",
    "SubscriptionResponse",
    "TTLCache",
    "TapestryAuthError",
    "TapestryClient",
    "TapestryClientError",
    "TapestryConflictError",
    "TapestryNotFoundError",
    "TapestryServerError",
    "TapestryValidationError",
    "UserProfile",
]
