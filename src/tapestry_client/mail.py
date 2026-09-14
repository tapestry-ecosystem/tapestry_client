"""Typed, request-scoped access to Tapestry's canonical mail controls."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field


class MailAccountRead(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)
    id: int
    name: str
    username: str
    imap_server: str
    imap_port: int | None = 993
    imap_security: int = 2
    account_type: int = 1
    password_configured: bool = True


class MailRuleRead(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)
    id: int
    name: str
    account: int
    enabled: bool = True
    folder: str = "INBOX"
    filter_from: str | None = None
    filter_to: str | None = None
    filter_subject: str | None = None
    filter_body: str | None = None
    filter_attachment_filename_include: str | None = None
    filter_attachment_filename_exclude: str | None = None
    maximum_age: int = 30
    consumption_scope: int = 1
    attachment_type: int = 1
    pdf_layout: int = 0
    action: int = 3
    action_parameter: str | None = None
    destination_jar_id: uuid.UUID | None = None
    destination_name: str = "Default intake Jar"
    bypass_review: bool = False
    managed: bool = False


class MailActivity(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)
    id: int
    rule: int
    subject: str = ""
    status: str = ""
    processed: datetime | None = None


class SavedSenderRead(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    account_id: int | None


class MailSettingsRead(BaseModel):
    accounts: list[MailAccountRead] = Field(default_factory=list)
    rules: list[MailRuleRead] = Field(default_factory=list)
    activity: list[MailActivity] = Field(default_factory=list)
    error: str | None = None
    legacy_accounts: list[dict[str, str]] = Field(default_factory=list)
    legacy_senders: list[SavedSenderRead] = Field(default_factory=list)


class MailMigrationResult(BaseModel):
    account_id: int
    rules_migrated: int
    complete: bool


class MailJar(BaseModel):
    id: uuid.UUID
    name: str


class MailClientMixin:
    """Mail administration always requires a delegated server administrator."""

    def _auth_headers(self, delegation_token: str | None) -> dict[str, str]:
        raise NotImplementedError

    async def _request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        raise NotImplementedError

    @staticmethod
    def _unwrap(response: httpx.Response) -> Any:
        raise NotImplementedError

    async def get_mail_settings(self, *, delegation_token: str) -> MailSettingsRead:
        response = await self._request(
            "GET", "/platform/v1/mail", headers=self._auth_headers(delegation_token)
        )
        return MailSettingsRead.model_validate(self._unwrap(response))

    async def list_mail_jars(self, *, delegation_token: str) -> list[MailJar]:
        response = await self._request(
            "GET", "/platform/v1/mail/jars", headers=self._auth_headers(delegation_token)
        )
        return [MailJar.model_validate(row) for row in self._unwrap(response)]

    async def get_mail_account(self, account_id: int, *, delegation_token: str) -> MailAccountRead:
        response = await self._request(
            "GET",
            f"/platform/v1/mail/accounts/{account_id}",
            headers=self._auth_headers(delegation_token),
        )
        return MailAccountRead.model_validate(self._unwrap(response))

    async def save_mail_account(
        self, *, data: dict[str, Any], delegation_token: str, account_id: int | None = None
    ) -> MailAccountRead:
        response = await self._request(
            "POST" if account_id is None else "PATCH",
            "/platform/v1/mail/accounts" + (f"/{account_id}" if account_id is not None else ""),
            headers=self._auth_headers(delegation_token),
            json_body=data,
        )
        return MailAccountRead.model_validate(self._unwrap(response))

    async def delete_mail_account(self, account_id: int, *, delegation_token: str) -> None:
        await self._request(
            "DELETE",
            f"/platform/v1/mail/accounts/{account_id}",
            headers=self._auth_headers(delegation_token),
        )

    async def test_mail_account(self, account_id: int, *, delegation_token: str) -> bool:
        response = await self._request(
            "POST",
            f"/platform/v1/mail/accounts/{account_id}/test",
            headers=self._auth_headers(delegation_token),
        )
        return bool(self._unwrap(response)["connected"])

    async def fetch_mail_account(self, account_id: int, *, delegation_token: str) -> None:
        await self._request(
            "POST",
            f"/platform/v1/mail/accounts/{account_id}/fetch",
            headers=self._auth_headers(delegation_token),
        )

    async def get_mail_rule(self, rule_id: int, *, delegation_token: str) -> MailRuleRead:
        response = await self._request(
            "GET",
            f"/platform/v1/mail/rules/{rule_id}",
            headers=self._auth_headers(delegation_token),
        )
        return MailRuleRead.model_validate(self._unwrap(response))

    async def save_mail_rule(
        self, *, data: dict[str, Any], delegation_token: str, rule_id: int | None = None
    ) -> MailRuleRead:
        response = await self._request(
            "POST" if rule_id is None else "PATCH",
            "/platform/v1/mail/rules" + (f"/{rule_id}" if rule_id is not None else ""),
            headers=self._auth_headers(delegation_token),
            json_body=data,
        )
        return MailRuleRead.model_validate(self._unwrap(response))

    async def delete_mail_rule(self, rule_id: int, *, delegation_token: str) -> None:
        await self._request(
            "DELETE",
            f"/platform/v1/mail/rules/{rule_id}",
            headers=self._auth_headers(delegation_token),
        )

    async def import_mail_account(
        self, legacy_id: str, *, delegation_token: str
    ) -> MailMigrationResult:
        response = await self._request(
            "POST",
            "/platform/v1/mail/import/" + quote(legacy_id, safe=""),
            headers=self._auth_headers(delegation_token),
        )
        return MailMigrationResult.model_validate(self._unwrap(response))

    async def import_mail_sender(
        self, entry_id: uuid.UUID, *, account_id: int, delegation_token: str
    ) -> MailRuleRead:
        response = await self._request(
            "POST",
            f"/platform/v1/mail/import-sender/{entry_id}",
            headers=self._auth_headers(delegation_token),
            json_body={"account_id": account_id},
        )
        return MailRuleRead.model_validate(self._unwrap(response))
