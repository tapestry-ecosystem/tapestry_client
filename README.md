# Tapestry Python client

The canonical `tapestry-client` package for the Tapestry ecosystem. Tapestry itself
and every Python companion app depend on this distribution. The old core package
copy is retired.

```python
from tapestry_client import TapestryClient

async with TapestryClient(
    base_url="https://tapestry.example.com",
    service_token=registration_seed,
    service_token_file="storage/my-app-service-token.json",
) as client:
    client.start_service_token_maintenance()
    identity = await client.resolve_identity(current_request_delegation_token)
```

Companion apps use `tapestry_shared_kit.client.create_tapestry_client(settings)`;
the shared lifespan starts maintenance and closes the client automatically.

The private file persists renewed service credentials across restarts. Workers of
one installation share the same file; advisory locking serializes their renewal.
Changing the origin or environment seed explicitly starts a new credential chain.
Keep the file on persistent storage, readable only by the app owner. The SDK never
stores delegation tokens, changes `.env`, or replays a write after auth failure.
`service_token_status()` performs a read-only authenticated health probe.

Use `tapestry_client.webhooks.verify_webhook` to verify Tapestry's timestamped HMAC
body, the per-organization subscription secret and the expected producer app UUID.
Business handlers must remain idempotent for legitimate retries.

See Tapestry's `docs/platform/authentication.md` for headers, recovery and rollout,
and private apps' `deploy/AUTHENTICATION.md` for the Stash/Budtender event bridge.

Development: `uv sync --extra dev`, `uv run pytest`, `uv run ruff check .`,
`uv run ruff format --check .`, `uv run mypy src tests`. Platform compatibility is
also exercised by Tapestry's `tests/test_tapestry_client` and companion compound tests.
