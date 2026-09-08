from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from tapestry_client import TapestryClient
from tapestry_client.credentials import CredentialStorageError, ServiceTokenStore
from tapestry_client.exceptions import TapestryAuthError


class Issuer:
    def __init__(self) -> None:
        self.current = "synthetic-seed"
        self.rotations = 0
        self.requests: list[httpx.Request] = []

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["X-Service-Token"] == self.current
        if request.url.path.endswith("/refresh"):
            await asyncio.sleep(0.01)  # Let competing workers reach the same boundary.
            self.rotations += 1
            self.current = f"synthetic-replacement-{self.rotations}"
            return httpx.Response(
                200,
                json={
                    "data": {
                        "service_token": self.current,
                        "rotated": True,
                        "expires_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
                    }
                },
            )
        if request.url.path.endswith("/token"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "app_id": "a0000000-0000-4000-8000-000000000001",
                        "app_slug": "sample",
                        "expires_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
                    }
                },
            )
        return httpx.Response(
            401, json={"error": {"code": "permission_denied", "message": "Delegation revoked"}}
        )


async def test_workers_share_one_rotation_and_restart_with_replacement(tmp_path: Path) -> None:
    issuer = Issuer()
    path = tmp_path / "credential.json"
    async with httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(issuer.handle)
    ) as http:
        clients = [
            TapestryClient(transport=http, service_token="synthetic-seed", service_token_file=path)
            for _ in range(4)
        ]
        await asyncio.gather(*(client.maybe_refresh_service_token() for client in clients))
        assert issuer.rotations == 1
        assert {client.service_token for client in clients} == {issuer.current}
        restarted = TapestryClient(
            transport=http, service_token="synthetic-seed", service_token_file=path
        )
        assert restarted.service_token == issuer.current
        await restarted.maybe_refresh_service_token()
        assert issuer.rotations == 1
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


async def test_status_is_read_only_and_delegation_failure_does_not_replay(tmp_path: Path) -> None:
    issuer = Issuer()
    path = tmp_path / "credential.json"
    async with httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(issuer.handle)
    ) as http:
        client = TapestryClient(
            transport=http, service_token="synthetic-seed", service_token_file=path
        )
        assert (await client.service_token_status()).app_slug == "sample"
        assert issuer.rotations == 0
        with pytest.raises(TapestryAuthError):
            await client.resolve_identity("synthetic-user-delegation")
        assert issuer.rotations == 1
        assert len([r for r in issuer.requests if r.url.path.endswith("/resolve")]) == 1
        assert "synthetic-user-delegation" not in path.read_text()


async def test_failed_persistence_retries_save_without_rotating_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    issuer = Issuer()
    original_save = ServiceTokenStore.save

    def fail_replacement(store: ServiceTokenStore, token: str, expires_at: datetime | None) -> None:
        if token != "synthetic-seed":
            raise CredentialStorageError("Storage full")
        original_save(store, token, expires_at)

    async with httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(issuer.handle)
    ) as http:
        client = TapestryClient(
            transport=http,
            service_token="synthetic-seed",
            service_token_file=tmp_path / "credential.json",
        )
        monkeypatch.setattr(ServiceTokenStore, "save", fail_replacement)
        with pytest.raises(CredentialStorageError):
            await client.maybe_refresh_service_token()
        monkeypatch.setattr(ServiceTokenStore, "save", original_save)
        await client.maybe_refresh_service_token()
        assert client.service_token == issuer.current
        assert issuer.rotations == 1


async def test_unwritable_storage_prevents_rotation(tmp_path: Path) -> None:
    issuer = Issuer()
    parent = tmp_path / "not-a-directory"
    parent.write_text("test")
    async with httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(issuer.handle)
    ) as http:
        client = TapestryClient(
            transport=http,
            service_token="synthetic-seed",
            service_token_file=tmp_path / "credential.json",
        )
        assert client._token_store is not None
        client._token_store.path = parent / "credential.json"
        with pytest.raises(CredentialStorageError):
            await client.maybe_refresh_service_token()
        assert issuer.rotations == 0


def test_credential_store_is_bound_to_deployment_and_registration_seed(tmp_path: Path) -> None:
    path = tmp_path / "credential.json"
    store = ServiceTokenStore(path, origin="http://one", seed="seed")
    store.save("replacement", datetime.now(UTC) + timedelta(days=30))
    assert ServiceTokenStore(path, origin="http://two", seed="seed").load() is None
    assert ServiceTokenStore(path, origin="http://one", seed="new-registration").load() is None
    loaded = ServiceTokenStore(path, origin="http://one", seed="seed").load()
    assert loaded is not None and loaded.token.get_secret_value() == "replacement"


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permissions")
def test_insecure_credential_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "credential.json"
    store = ServiceTokenStore(path, origin="http://test", seed="seed")
    store.save("replacement", None)
    path.chmod(0o644)
    with pytest.raises(CredentialStorageError, match="private"):
        store.load()


async def test_shutdown_finishes_and_persists_an_inflight_renewal(tmp_path: Path) -> None:
    issuer = Issuer()
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(request: httpx.Request) -> httpx.Response:
        entered.set()
        await release.wait()
        return await issuer.handle(request)

    path = tmp_path / "service.json"
    async with httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(delayed)
    ) as http:
        client = TapestryClient(
            transport=http, service_token="synthetic-seed", service_token_file=path
        )
        client.start_service_token_maintenance()
        await asyncio.wait_for(entered.wait(), timeout=2)
        closing = asyncio.create_task(client.aclose())
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        await asyncio.wait_for(closing, timeout=2)
        restarted = TapestryClient(
            transport=http, service_token="synthetic-seed", service_token_file=path
        )
        assert restarted.service_token == issuer.current
        assert issuer.rotations == 1
