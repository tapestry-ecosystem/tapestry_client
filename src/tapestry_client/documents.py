"""Typed document metadata and evidence from Tapestry's authorized collection."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class DocumentSummary(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    jar_id: uuid.UUID | None
    title: str
    status: str
    document_kind: str | None = None
    other_document_kind: str | None = None
    categories: list[str] = Field(default_factory=list)
    classification_source: str | None = None
    classification_confidence: float | None = None
    classification_review_required: bool = True
    created_at: datetime
    updated_at: datetime


class DocumentDetail(DocumentSummary):
    extracted_text: str | None = None
    summary: str | None = None
    original_filename: str | None = None
    content_type: str | None = None


class DocumentPage(BaseModel):
    items: list[DocumentSummary]
    next_cursor: str | None = None


class DocumentTagRequirement(BaseModel):
    slug: str
    name: str
    criteria: str
    examples: list[str] = Field(default_factory=list)


class DocumentRequirements(BaseModel):
    kinds: list[str] = Field(default_factory=list)
    tags: list[DocumentTagRequirement] = Field(default_factory=list)


class DocumentKindOption(BaseModel):
    value: str
    label: str
    description: str
    examples: list[str] = Field(default_factory=list)
    allows_custom_label: bool
    ai_selectable: bool
