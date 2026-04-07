"""Lightweight typed models for internal documentation and future API growth."""

from __future__ import annotations

from pydantic import BaseModel


class ServiceSuggestion(BaseModel):
    service_name: str
    quantity: float
    unit: str
    unit_price_gross: float
    vat_rate: float
    source: str


class MailPreview(BaseModel):
    subject: str
    body: str
