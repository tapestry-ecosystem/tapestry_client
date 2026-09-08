"""Async client for the Tapestry platform contracts.

The client wraps every ``/platform/v1/*`` endpoint (and the user-facing
``/api/v1/auth/delegate`` mint helper) with typed responses and consistent
error translation. It holds the app's service token internally so that
rotations can happen transparently; delegation tokens are passed per call
because they're scoped to a specific user session.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import httpx

from tapestry_client.credentials import ServiceTokenStore
from tapestry_client.exceptions import (
    TapestryAuthError,
    TapestryClientError,
    TapestryConflictError,
    TapestryNotFoundError,
    TapestryRateLimitError,
    TapestryServerError,
    TapestryValidationError,
)
from tapestry_client.schemas import (
    AccessibleJar,
    DelegationToken,
    EmittedEvent,
    EntityFieldUpdateResult,
    EntityRef,
    EntityTypeRef,
    IdentityContext,
    JarRef,
    OrganizationMember,
    Pack,
    PackInstallation,
    PackUpgrade,
    PermissionCheck,
    RelationshipRef,
    ServiceTokenRefresh,
    ServiceTokenStatus,
    SubscriptionCreateResponse,
    SubscriptionResponse,
    UserProfile,
)

_DEFAULT_TIMEOUT = 10.0
"""Default HTTP timeout in seconds."""

_REFRESH_WINDOW_SECONDS = 3600.0
"""Default window (1 hour) before expiry in which ``maybe_refresh_service_token`` rotates."""


TokenRefreshCallback = Callable[[ServiceTokenRefresh], Awaitable[None]]
"""Signature for the optional persistence hook invoked after a refresh."""


class TapestryClient:
    """Thin async client over the Tapestry platform contracts.

    Args:
        base_url: Root URL of the Tapestry deployment, e.g.
            ``https://tapestry.local``. Ignored when ``transport`` is supplied.
        service_token: The app's current service token (``tap_service_...``).
            Rotations update this in place.
        transport: Optional pre-built :class:`httpx.AsyncClient`. Useful for
            ASGI wiring in tests.
        timeout: HTTP timeout in seconds. Ignored when ``transport`` is supplied.
        on_token_refresh: Optional async callback invoked after a successful
            service-token refresh. Typical implementation: persist the new
            token to secure storage.

    The client is an async context manager; prefer ``async with`` over manual
    ``aclose()`` calls.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        service_token: str,
        transport: httpx.AsyncClient | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        on_token_refresh: TokenRefreshCallback | None = None,
        service_token_file: Path | str | None = None,
        service_token_refresh_window: float = _REFRESH_WINDOW_SECONDS,
    ) -> None:
        if transport is None:
            if base_url is None:
                raise ValueError("Either base_url or transport must be provided")
            self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout)
            self._owns_transport = True
        else:
            self._http = transport
            self._owns_transport = False
        self._refresh_window = service_token_refresh_window
        self._service_token = service_token
        self._service_token_expires_at: datetime | None = None
        self._on_token_refresh = on_token_refresh
        self._refresh_lock = asyncio.Lock()
        self._token_store = (
            ServiceTokenStore(
                Path(service_token_file), origin=str(self._http.base_url), seed=service_token
            )
            if service_token_file is not None
            else None
        )
        self._pending_refresh: ServiceTokenRefresh | None = None
        self._maintenance_task: asyncio.Task[None] | None = None
        self._load_stored_token()

    def _load_stored_token(self) -> None:
        if self._token_store is not None and self._pending_refresh is None:
            record = self._token_store.load()
            if record is not None:
                self._service_token = record.token.get_secret_value()
                self._service_token_expires_at = record.expires_at

    def start_service_token_maintenance(self) -> None:
        """Renew durable service credentials while the app is idle as well as active."""
        if self._token_store is not None and self._service_token and self._maintenance_task is None:
            self._maintenance_task = asyncio.create_task(self._maintain_service_token())

    async def _maintain_service_token(self) -> None:
        while True:
            try:
                await self.maybe_refresh_service_token(window_seconds=self._refresh_window)
            except TapestryClientError as exc:
                logging.getLogger(__name__).warning(
                    "Platform renewal failed (%s)", type(exc).__name__
                )
            await asyncio.sleep(60)

    # ------------------------------------------------------------------
    # Context management
    # ------------------------------------------------------------------

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP transport if the client owns it."""

        if self._maintenance_task is not None:
            self._maintenance_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._maintenance_task
            self._maintenance_task = None
        if self._owns_transport:
            await self._http.aclose()

    # ------------------------------------------------------------------
    # Token accessors
    # ------------------------------------------------------------------

    @property
    def service_token(self) -> str:
        """Current service token. Mutates after ``refresh_service_token``."""

        return self._service_token

    @property
    def service_token_expires_at(self) -> datetime | None:
        """Expiry of the service token when known (set after a refresh)."""

        return self._service_token_expires_at

    # ------------------------------------------------------------------
    # Low-level request plumbing
    # ------------------------------------------------------------------

    def _service_headers(self) -> dict[str, str]:
        return {"X-Service-Token": self._service_token}

    def _auth_headers(self, delegation_token: str | None) -> dict[str, str]:
        headers = {"X-Service-Token": self._service_token}
        if delegation_token is not None:
            headers["X-Delegation-Token"] = delegation_token
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        if "X-Service-Token" in headers:
            if self._token_store is not None and path not in (
                "/platform/v1/apps/token",
                "/platform/v1/apps/token/refresh",
            ):
                await self.maybe_refresh_service_token(window_seconds=self._refresh_window)
            headers = {**headers, "X-Service-Token": self._service_token}
        try:
            response = await self._http.request(
                method,
                path,
                headers=headers,
                json=json_body,
                params=params,
            )
        except httpx.HTTPError as exc:
            raise TapestryClientError(f"Transport error calling {path}: {exc}") from exc
        if response.status_code >= 400:
            self._raise_for_response(response)
        return response

    @staticmethod
    def _raise_for_response(response: httpx.Response) -> None:
        status_code = response.status_code
        payload: dict[str, Any] | None = None
        try:
            decoded = response.json()
            if isinstance(decoded, dict):
                payload = decoded
        except ValueError:
            payload = None
        error_block = payload.get("error") if payload else None
        code: str | None = None
        message: str = response.text or f"HTTP {status_code}"
        if isinstance(error_block, dict):
            raw_code = error_block.get("code")
            raw_message = error_block.get("message")
            if isinstance(raw_code, str):
                code = raw_code
            if isinstance(raw_message, str):
                message = raw_message

        kwargs: dict[str, Any] = {
            "status_code": status_code,
            "code": code,
            "payload": payload,
        }
        if status_code in (401, 403):
            raise TapestryAuthError(message, **kwargs)
        if status_code == 404:
            raise TapestryNotFoundError(message, **kwargs)
        if status_code == 409:
            raise TapestryConflictError(message, **kwargs)
        if status_code == 422:
            raise TapestryValidationError(message, **kwargs)
        if status_code == 429:
            retry_after_header = response.headers.get("Retry-After")
            retry_after: float | None = None
            if retry_after_header:
                try:
                    retry_after = float(retry_after_header)
                except ValueError:
                    retry_after = None
            if retry_after is None and isinstance(payload, dict):
                raw_retry = payload.get("retry_after_seconds")
                if isinstance(raw_retry, int | float):
                    retry_after = float(raw_retry)
            raise TapestryRateLimitError(message, retry_after_seconds=retry_after, **kwargs)
        if status_code >= 500:
            raise TapestryServerError(message, **kwargs)
        raise TapestryClientError(message, **kwargs)

    @staticmethod
    def _unwrap(response: httpx.Response) -> Any:
        decoded = response.json()
        if not isinstance(decoded, dict) or "data" not in decoded:
            raise TapestryClientError(
                "Unexpected response shape: missing 'data' envelope",
                status_code=response.status_code,
                payload=decoded if isinstance(decoded, dict) else None,
            )
        return decoded["data"]

    # ------------------------------------------------------------------
    # Service-token lifecycle
    # ------------------------------------------------------------------

    async def refresh_service_token(self) -> ServiceTokenRefresh:
        """Rotate the service token.

        Updates :attr:`service_token` in place, records the new expiry, and
        invokes ``on_token_refresh`` if configured.
        """

        result = await self._refresh(force=True, window_seconds=_REFRESH_WINDOW_SECONDS)
        assert result is not None
        return result

    async def service_token_status(self) -> ServiceTokenStatus:
        """Validate the current app credential without renewing it."""
        self._load_stored_token()
        response = await self._request(
            "GET", "/platform/v1/apps/token", headers=self._service_headers()
        )
        return ServiceTokenStatus.model_validate(self._unwrap(response))

    async def _refresh(self, *, force: bool, window_seconds: float) -> ServiceTokenRefresh | None:
        # Finish receiving and persisting an issued replacement before shutdown.
        operation = asyncio.create_task(
            self._refresh_serialized(force=force, window_seconds=window_seconds)
        )
        try:
            return await asyncio.shield(operation)
        except asyncio.CancelledError:
            try:
                await operation
            finally:
                raise

    async def _refresh_serialized(
        self, *, force: bool, window_seconds: float
    ) -> ServiceTokenRefresh | None:
        async with self._refresh_lock:
            if self._token_store is None:
                return await self._refresh_locked(force=force, window_seconds=window_seconds)
            async with self._token_store.locked():
                if self._pending_refresh is not None:
                    self._persist_refresh(self._pending_refresh)
                self._load_stored_token()
                return await self._refresh_locked(force=force, window_seconds=window_seconds)

    def _persist_refresh(self, refreshed: ServiceTokenRefresh) -> None:
        if self._token_store is not None:
            self._pending_refresh = refreshed
            self._token_store.save(refreshed.service_token, refreshed.expires_at)
        self._service_token = refreshed.service_token
        self._service_token_expires_at = refreshed.expires_at
        self._pending_refresh = None

    async def _refresh_locked(
        self, *, force: bool, window_seconds: float
    ) -> ServiceTokenRefresh | None:
        expires_at = self._service_token_expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if (
            not force
            and expires_at is not None
            and (expires_at - datetime.now(UTC)).total_seconds() > window_seconds
        ):
            return None
        if self._token_store is not None:
            # Verify durable storage before asking the server to supersede a credential.
            self._token_store.save(self._service_token, self._service_token_expires_at)
        response = await self._request(
            "POST", "/platform/v1/apps/token/refresh", headers=self._service_headers()
        )
        refreshed = ServiceTokenRefresh.model_validate(self._unwrap(response))
        self._persist_refresh(refreshed)
        if self._on_token_refresh is not None:
            await self._on_token_refresh(refreshed)
        return refreshed

    async def maybe_refresh_service_token(
        self,
        *,
        window_seconds: float = _REFRESH_WINDOW_SECONDS,
    ) -> ServiceTokenRefresh | None:
        """Refresh the service token if it is within ``window_seconds`` of expiry.

        Returns the refresh result when a rotation happens, otherwise ``None``.
        Expiry is loaded from durable storage or established by the first refresh
        request. The server can return a no-op while the credential is still fresh.
        """

        return await self._refresh(force=False, window_seconds=window_seconds)

    # ------------------------------------------------------------------
    # Delegation-token minting (user-authenticated helper)
    # ------------------------------------------------------------------

    async def mint_delegation_token(
        self,
        *,
        user_access_token: str,
        app_slug: str,
        consent: bool = False,
    ) -> DelegationToken:
        """Mint a delegation token for an app on behalf of a logged-in user.

        This hits ``POST /api/v1/apps/{slug}/delegate`` using the user's
        Tapestry JWT (not the service token). It's primarily useful for
        test harnesses, hello-apps, and internal tooling where the user flow
        is driven outside a browser.

        Args:
            user_access_token: The user's Tapestry access token.
            app_slug: The registered app slug to delegate to.
            consent: Whether the user explicitly consents to delegating to
                this app. Required on the first delegation for a user/app
                pair; subsequent delegations can omit it.
        """

        response = await self._request(
            "POST",
            f"/api/v1/apps/{app_slug}/delegate",
            headers={"Authorization": f"Bearer {user_access_token}"},
            json_body={"consent": consent},
        )
        return DelegationToken.model_validate(self._unwrap(response))

    # ------------------------------------------------------------------
    # Identity & permissions
    # ------------------------------------------------------------------

    async def resolve_identity(self, delegation_token: str) -> IdentityContext:
        """Resolve a delegation token to the full user context."""

        response = await self._request(
            "POST",
            "/platform/v1/identity/resolve",
            headers=self._auth_headers(delegation_token),
        )
        return IdentityContext.model_validate(self._unwrap(response))

    async def get_user_profile(
        self,
        user_id: uuid.UUID,
        *,
        delegation_token: str,
    ) -> UserProfile:
        response = await self._request(
            "GET",
            f"/platform/v1/identity/users/{user_id}",
            headers=self._auth_headers(delegation_token),
        )
        return UserProfile.model_validate(self._unwrap(response))

    async def list_organization_members(
        self,
        organization_id: uuid.UUID,
        *,
        delegation_token: str,
    ) -> list[OrganizationMember]:
        response = await self._request(
            "GET",
            f"/platform/v1/identity/organizations/{organization_id}/members",
            headers=self._auth_headers(delegation_token),
        )
        data = self._unwrap(response)
        return [OrganizationMember.model_validate(item) for item in data]

    async def check_permission(
        self,
        *,
        resource_type: str,
        resource_id: uuid.UUID,
        action: str,
        delegation_token: str,
    ) -> PermissionCheck:
        response = await self._request(
            "POST",
            "/platform/v1/permissions/check",
            headers=self._auth_headers(delegation_token),
            json_body={
                "resource_type": resource_type,
                "resource_id": str(resource_id),
                "action": action,
            },
        )
        return PermissionCheck.model_validate(self._unwrap(response))

    async def list_accessible_jars(
        self,
        *,
        delegation_token: str,
    ) -> list[AccessibleJar]:
        response = await self._request(
            "GET",
            "/platform/v1/permissions/accessible-jars",
            headers=self._auth_headers(delegation_token),
        )
        data = self._unwrap(response)
        return [AccessibleJar.model_validate(item) for item in data]

    async def search_jars(
        self,
        *,
        display_name: str | None = None,
        slug: str | None = None,
        delegation_token: str,
    ) -> list[JarRef]:
        """Search Jars accessible to this app by display name or slug.

        Args:
            display_name: Case-insensitive partial match on Jar name.
            slug: Exact slug match.
            delegation_token: Delegation token for the acting user.

        Returns:
            Matching Jars. Returns all accessible Jars when no filters are given.
        """
        params: dict[str, str] = {}
        if display_name is not None:
            params["display_name"] = display_name
        if slug is not None:
            params["slug"] = slug
        response = await self._request(
            "GET",
            "/platform/v1/jars",
            headers=self._auth_headers(delegation_token),
            params=params or None,
        )
        data = self._unwrap(response)
        return [JarRef.model_validate(item) for item in data]

    # ------------------------------------------------------------------
    # Entities & relationships
    # ------------------------------------------------------------------

    async def create_entity(
        self,
        *,
        entity_type_id: uuid.UUID,
        jar_id: uuid.UUID,
        name: str,
        summary: str | None = None,
        delegation_token: str,
    ) -> EntityRef:
        body: dict[str, Any] = {
            "entity_type_id": str(entity_type_id),
            "jar_id": str(jar_id),
            "name": name,
        }
        if summary is not None:
            body["summary"] = summary
        response = await self._request(
            "POST",
            "/platform/v1/entities",
            headers=self._auth_headers(delegation_token),
            json_body=body,
        )
        return EntityRef.model_validate(self._unwrap(response))

    async def get_entity(
        self,
        entity_id: uuid.UUID,
        *,
        delegation_token: str,
    ) -> EntityRef:
        response = await self._request(
            "GET",
            f"/platform/v1/entities/{entity_id}",
            headers=self._auth_headers(delegation_token),
        )
        return EntityRef.model_validate(self._unwrap(response))

    async def search_entities(
        self,
        *,
        query: str,
        limit: int = 5,
        entity_type_slugs: list[str] | None = None,
        organization_id: uuid.UUID | None = None,
        jar_ids: list[uuid.UUID] | None = None,
        delegation_token: str,
    ) -> list[EntityRef]:
        body: dict[str, Any] = {"query": query, "limit": limit}
        if entity_type_slugs is not None:
            body["entity_type_slugs"] = list(entity_type_slugs)
        if organization_id is not None:
            body["organization_id"] = str(organization_id)
        if jar_ids is not None:
            body["jar_ids"] = [str(jid) for jid in jar_ids]
        response = await self._request(
            "POST",
            "/platform/v1/entities/search",
            headers=self._auth_headers(delegation_token),
            json_body=body,
        )
        data = self._unwrap(response)
        return [EntityRef.model_validate(item) for item in data]

    async def get_entity_type(
        self,
        slug: str,
        *,
        delegation_token: str,
    ) -> EntityTypeRef:
        """Look up an entity type by slug. Returns the type plus its declared fields."""

        response = await self._request(
            "GET",
            f"/platform/v1/entity-types/{slug}",
            headers=self._auth_headers(delegation_token),
        )
        return EntityTypeRef.model_validate(self._unwrap(response))

    async def suggest_entities(
        self,
        *,
        query: str,
        limit: int = 5,
        delegation_token: str,
    ) -> list[EntityRef]:
        response = await self._request(
            "POST",
            "/platform/v1/entities/suggest",
            headers=self._auth_headers(delegation_token),
            json_body={"query": query, "limit": limit},
        )
        data = self._unwrap(response)
        return [EntityRef.model_validate(item) for item in data]

    async def patch_entity_fields(
        self,
        entity_id: uuid.UUID,
        *,
        fields: dict[str, Any],
        delegation_token: str,
    ) -> EntityFieldUpdateResult:
        response = await self._request(
            "PATCH",
            f"/platform/v1/entities/{entity_id}/fields",
            headers=self._auth_headers(delegation_token),
            json_body={"fields": fields},
        )
        return EntityFieldUpdateResult.model_validate(self._unwrap(response))

    async def get_entity_fields(
        self,
        entity_id: uuid.UUID,
        *,
        delegation_token: str,
    ) -> dict[str, Any]:
        """Return a flat ``{field_key: value}`` dict for a given entity."""

        response = await self._request(
            "GET",
            f"/platform/v1/entities/{entity_id}/fields",
            headers=self._auth_headers(delegation_token),
        )
        data = self._unwrap(response)
        if not isinstance(data, dict):
            raise TapestryClientError(
                "Unexpected response shape for entity fields",
                status_code=response.status_code,
                payload=None,
            )
        return data

    async def create_relationship(
        self,
        *,
        from_entity_id: uuid.UUID,
        to_entity_id: uuid.UUID,
        relationship_type: str,
        delegation_token: str,
    ) -> RelationshipRef:
        response = await self._request(
            "POST",
            f"/platform/v1/entities/{from_entity_id}/relationships",
            headers=self._auth_headers(delegation_token),
            json_body={
                "from_entity_id": str(from_entity_id),
                "to_entity_id": str(to_entity_id),
                "relationship_type": relationship_type,
            },
        )
        return RelationshipRef.model_validate(self._unwrap(response))

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    async def emit_event(
        self,
        *,
        idempotency_key: str,
        event_type: str,
        delegation_token: str | None = None,
        subject_entity_id: uuid.UUID | None = None,
        organization_id: uuid.UUID | None = None,
        occurred_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
        visibility: str = "organization",
    ) -> EmittedEvent:
        """Emit an event. Replaying the same idempotency key is a no-op.

        ``delegation_token`` may be omitted for app-actor (service-only) event
        emission; when omitted the platform records the event with no user
        actor.

        ``EmittedEvent.was_created`` is ``True`` on the first call and
        ``False`` on idempotent replays so callers can distinguish the two
        without probing HTTP status codes directly.
        """

        body: dict[str, Any] = {
            "idempotency_key": idempotency_key,
            "event_type": event_type,
            "metadata": metadata or {},
            "visibility": visibility,
        }
        if subject_entity_id is not None:
            body["subject_entity_id"] = str(subject_entity_id)
        if organization_id is not None:
            body["organization_id"] = str(organization_id)
        if occurred_at is not None:
            body["occurred_at"] = occurred_at.isoformat()
        response = await self._request(
            "POST",
            "/platform/v1/events",
            headers=self._auth_headers(delegation_token),
            json_body=body,
        )
        data = self._unwrap(response)
        event = EmittedEvent.model_validate({**data, "was_created": response.status_code == 201})
        return event

    # ------------------------------------------------------------------
    # Pack registry
    # ------------------------------------------------------------------

    async def list_packs(
        self,
        delegation_token: str,
        *,
        app_slug: str | None = None,
        entity_type_slug: str | None = None,
        is_default: bool | None = None,
    ) -> list[Pack]:
        """List all packs visible to this app."""
        params: dict[str, str] = {}
        if app_slug is not None:
            params["app_slug"] = app_slug
        if entity_type_slug is not None:
            params["entity_type_slug"] = entity_type_slug
        if is_default is not None:
            params["is_default"] = str(is_default).lower()
        response = await self._request(
            "GET",
            "/platform/v1/packs",
            headers=self._auth_headers(delegation_token),
            params=params,
        )
        return [Pack.model_validate(p) for p in self._unwrap(response)]

    async def get_pack(self, delegation_token: str, pack_slug: str) -> Pack:
        """Get a pack with all its items."""
        response = await self._request(
            "GET",
            f"/platform/v1/packs/{pack_slug}",
            headers=self._auth_headers(delegation_token),
        )
        return Pack.model_validate(self._unwrap(response))

    async def list_pack_items(
        self,
        delegation_token: str,
        pack_slug: str,
        *,
        since_version: str | None = None,
    ) -> list[Any]:
        """Paginated pack items; optionally filter to items added after `since_version`."""
        params: dict[str, str] = {}
        if since_version is not None:
            params["since_version"] = since_version
        response = await self._request(
            "GET",
            f"/platform/v1/packs/{pack_slug}/items",
            headers=self._auth_headers(delegation_token),
            params=params,
        )
        return list(self._unwrap(response))

    async def list_pack_installations(
        self,
        delegation_token: str,
        organization_id: uuid.UUID,
    ) -> list[PackInstallation]:
        """List packs installed for an organization."""
        response = await self._request(
            "GET",
            f"/platform/v1/orgs/{organization_id}/pack-installations",
            headers=self._auth_headers(delegation_token),
        )
        return [PackInstallation.model_validate(i) for i in self._unwrap(response)]

    async def install_pack(
        self,
        delegation_token: str,
        organization_id: uuid.UUID,
        pack_slug: str,
    ) -> PackInstallation:
        """Install a pack for an organization. Idempotent."""
        response = await self._request(
            "POST",
            f"/platform/v1/orgs/{organization_id}/pack-installations",
            headers=self._auth_headers(delegation_token),
            json_body={"pack_slug": pack_slug},
        )
        return PackInstallation.model_validate(self._unwrap(response))

    async def upgrade_pack(
        self,
        delegation_token: str,
        organization_id: uuid.UUID,
        pack_slug: str,
    ) -> PackUpgrade:
        """Upgrade an installed pack to the latest version."""
        response = await self._request(
            "POST",
            f"/platform/v1/orgs/{organization_id}/pack-installations/{pack_slug}/upgrade",
            headers=self._auth_headers(delegation_token),
        )
        return PackUpgrade.model_validate(self._unwrap(response))

    async def uninstall_pack(
        self,
        delegation_token: str,
        organization_id: uuid.UUID,
        pack_slug: str,
    ) -> None:
        """Uninstall a pack for an organization (soft delete)."""
        await self._request(
            "DELETE",
            f"/platform/v1/orgs/{organization_id}/pack-installations/{pack_slug}",
            headers=self._auth_headers(delegation_token),
        )

    # ------------------------------------------------------------------
    # Webhook subscriptions
    # ------------------------------------------------------------------

    async def create_subscription(
        self,
        *,
        delegation_token: str,
        organization_id: uuid.UUID,
        webhook_url: str,
        event_type_patterns: list[str],
        signing_secret: str | None = None,
        active: bool = True,
    ) -> SubscriptionCreateResponse:
        """Register a webhook subscription. Returns the raw signing secret.

        Calls ``POST /platform/v1/subscriptions``. The returned
        ``signing_secret`` is shown only here — persist it immediately for
        ``X-Tapestry-Signature`` HMAC verification on inbound deliveries.

        Args:
            webhook_url: HTTPS URL to deliver matching events to. Sent as
                ``url`` on the wire (the platform contract field name).
            event_type_patterns: Glob-style patterns such as ``shopping.*`` or
                ``expenses.expense.confirmed``.
            signing_secret: Optional bring-your-own secret. If ``None``, the
                platform generates one and returns it in the response.
        """
        body: dict[str, Any] = {
            "organization_id": str(organization_id),
            "url": webhook_url,
            "event_type_patterns": list(event_type_patterns),
            "active": active,
        }
        if signing_secret is not None:
            body["signing_secret"] = signing_secret
        response = await self._request(
            "POST",
            "/platform/v1/subscriptions",
            headers=self._auth_headers(delegation_token),
            json_body=body,
        )
        return SubscriptionCreateResponse.model_validate(self._unwrap(response))

    async def list_subscriptions(
        self,
        *,
        delegation_token: str,
    ) -> list[SubscriptionResponse]:
        """List the calling app's webhook subscriptions.

        Calls ``GET /platform/v1/subscriptions``. Scope is implicit — only
        subscriptions owned by the app behind the service token are returned.
        """
        response = await self._request(
            "GET",
            "/platform/v1/subscriptions",
            headers=self._auth_headers(delegation_token),
        )
        return [SubscriptionResponse.model_validate(item) for item in self._unwrap(response)]

    async def delete_subscription(
        self,
        subscription_id: uuid.UUID,
        *,
        delegation_token: str,
    ) -> None:
        """Soft-delete a webhook subscription.

        Calls ``DELETE /platform/v1/subscriptions/{id}``. Raises
        :class:`TapestryNotFoundError` if the subscription does not exist or
        is not owned by the calling app.
        """
        await self._request(
            "DELETE",
            f"/platform/v1/subscriptions/{subscription_id}",
            headers=self._auth_headers(delegation_token),
        )
