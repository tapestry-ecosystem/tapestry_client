"""Durable service credentials shared by workers of one companion app.

The environment supplies the registration/recovery credential. The private file
holds its current replacement. Delegation tokens are never written here.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from filelock import AsyncFileLock, Timeout
from pydantic import BaseModel, SecretStr, ValidationError

from tapestry_client.exceptions import TapestryClientError


class CredentialStorageError(TapestryClientError):
    """The current credential could not be safely loaded or persisted."""


class StoredServiceToken(BaseModel):
    origin: str
    seed_hash: str
    token: SecretStr
    expires_at: datetime | None = None


class ServiceTokenStore:
    def __init__(self, path: Path, *, origin: str, seed: str) -> None:
        self.path = path
        self.origin = origin.rstrip("/")
        self.seed_hash = hashlib.sha256(seed.encode()).hexdigest()

    @asynccontextmanager
    async def locked(self) -> AsyncIterator[None]:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            async with AsyncFileLock(str(self.path) + ".lock", timeout=30, mode=0o600):
                yield
        except (OSError, Timeout) as exc:
            raise CredentialStorageError("Service credential storage is unavailable") from exc

    def load(self) -> StoredServiceToken | None:
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise CredentialStorageError("Cannot read service credential storage") from exc
        if not stat.S_ISREG(info.st_mode) or (os.name == "posix" and info.st_mode & 0o077):
            raise CredentialStorageError("Service credential file must be private (mode 0600)")
        try:
            record = StoredServiceToken.model_validate_json(self.path.read_bytes())
        except (OSError, ValidationError):
            # ValidationError can include the secret input; never expose it in the message.
            raise CredentialStorageError("Cannot read service credential storage") from None
        if record.origin != self.origin or record.seed_hash != self.seed_hash:
            return None  # Explicit reconfiguration/recovery starts a new credential chain.
        return record

    def save(self, token: str, expires_at: datetime | None) -> None:
        record = StoredServiceToken(
            origin=self.origin,
            seed_hash=self.seed_hash,
            token=SecretStr(token),
            expires_at=expires_at,
        )
        # SecretStr masks JSON by default; only this private file contains the raw value.
        body = record.model_dump(mode="json")
        body["token"] = token
        temporary: str | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, temporary = tempfile.mkstemp(prefix=".tapestry-token-", dir=self.path.parent)
            with os.fdopen(fd, "w") as stream:
                json.dump(body, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
            if os.name == "posix":
                directory = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        except OSError as exc:
            raise CredentialStorageError("Cannot persist service credential") from exc
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
