"""Typed purchase extraction contract; amounts serialize as decimal strings."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

Money = Annotated[Decimal, Field(max_digits=12, decimal_places=2, allow_inf_nan=False)]


class PurchaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, from_attributes=True)

    @field_validator(
        "quantity",
        "unit_price",
        "discount",
        "line_total",
        "subtotal",
        "tax",
        "shipping",
        "tip",
        "total",
        "amount_paid",
        "balance_due",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def reject_inexact_decimal(cls, value: Any) -> Any:
        # Native tool SDKs may already have rounded JSON numbers to binary floats.
        # They cannot be reconstructed exactly; accept decimal strings/Decimal.
        if isinstance(value, float):
            raise ValueError("Decimal values must be supplied as strings")
        return value


class Evidence(PurchaseModel):
    quote: str = Field(max_length=500, min_length=1)
    page: int | None = Field(default=None, ge=1)
    verified: bool = False


class PurchaseLineItem(PurchaseModel):
    description: str | None = Field(default=None, max_length=500)
    quantity: Decimal | None = Field(
        default=None, max_digits=18, decimal_places=6, allow_inf_nan=False
    )
    unit_price: Decimal | None = Field(
        default=None, max_digits=16, decimal_places=4, allow_inf_nan=False
    )
    discount: Money | None = None
    line_total: Money | None = None
    suggested_category: str | None = Field(default=None, max_length=100)


class PurchaseData(PurchaseModel):
    document_type: Literal["receipt", "invoice", "unknown"] = "unknown"
    merchant: str | None = Field(default=None, max_length=255)
    document_number: str | None = Field(default=None, max_length=255)
    purchase_date: date | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    subtotal: Money | None = None
    tax: Money | None = None
    shipping: Money | None = None
    tip: Money | None = None
    discount: Money | None = None
    total: Money | None = None
    amount_paid: Money | None = None
    balance_due: Money | None = None
    line_items: list[PurchaseLineItem] = Field(default_factory=list, max_length=200)


class PurchaseExtractionResult(PurchaseModel):
    data: PurchaseData
    evidence: dict[str, Evidence] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    needs_review: list[str] = Field(default_factory=list)
    source_revision: str


class PurchaseExtractionJob(PurchaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    status: Literal["queued", "running", "completed", "failed"]
    mode: Literal["text", "image"]
    result: PurchaseExtractionResult | None = None
    error_code: str | None = None
    provider: str | None = None
    model: str | None = None
    processing_location: Literal["local", "cloud"] | None = None
    created_at: datetime
    updated_at: datetime


class PurchaseExtractionOptions(PurchaseModel):
    enabled: bool
    provider: str | None = None
    model: str | None = None
    processing_location: Literal["local", "cloud"] | None = None
    text_available: bool
    image_available: bool
    unavailable_reason: str | None = None


class PurchaseClientMixin:
    """All purchase requests use the client's normal auth and rotation path."""

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

    async def get_purchase_extraction_options(
        self, *, document_id: uuid.UUID, delegation_token: str
    ) -> PurchaseExtractionOptions:
        response = await self._request(
            "GET",
            f"/platform/v1/documents/{document_id}/purchase-extractions/options",
            headers=self._auth_headers(delegation_token),
        )
        return PurchaseExtractionOptions.model_validate(self._unwrap(response))

    async def latest_purchase_extraction(
        self, *, document_id: uuid.UUID, delegation_token: str
    ) -> PurchaseExtractionJob | None:
        response = await self._request(
            "GET",
            f"/platform/v1/documents/{document_id}/purchase-extractions",
            headers=self._auth_headers(delegation_token),
        )
        data = self._unwrap(response)
        return PurchaseExtractionJob.model_validate(data) if data is not None else None

    async def start_purchase_extraction(
        self,
        *,
        document_id: uuid.UUID,
        delegation_token: str,
        mode: Literal["text", "image"] = "text",
        reprocess: bool = False,
    ) -> PurchaseExtractionJob:
        response = await self._request(
            "POST",
            f"/platform/v1/documents/{document_id}/purchase-extractions",
            headers=self._auth_headers(delegation_token),
            json_body={"mode": mode, "reprocess": reprocess},
        )
        return PurchaseExtractionJob.model_validate(self._unwrap(response))

    async def get_purchase_extraction(
        self, *, document_id: uuid.UUID, extraction_id: uuid.UUID, delegation_token: str
    ) -> PurchaseExtractionJob:
        response = await self._request(
            "GET",
            f"/platform/v1/documents/{document_id}/purchase-extractions/{extraction_id}",
            headers=self._auth_headers(delegation_token),
        )
        return PurchaseExtractionJob.model_validate(self._unwrap(response))
