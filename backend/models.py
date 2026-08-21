"""Typed domain models for deterministic checkout."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ProposalStatus(str, Enum):
    DRAFT = "draft"
    AWAITING_ADDON_DECISION = "awaiting_addon_decision"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMED = "confirmed"
    PAYMENT_PENDING = "payment_pending"
    PAID = "paid"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"


class PaymentStatus(str, Enum):
    CREATED = "created"
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ComplementRef(BaseModel):
    product_id: str
    priority: int = 99
    reason: str = ""


class Product(BaseModel):
    id: str
    name: str
    price_inr: int
    category: str
    stock: int
    tags: list[str] = Field(default_factory=list)
    complements: list[ComplementRef] = Field(default_factory=list)
    substitute_with: str | None = None

    @field_validator("price_inr")
    @classmethod
    def price_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("price_inr must be >= 0")
        return value


class LineItem(BaseModel):
    product_id: str
    name: str
    qty: int = 1
    unit_price_inr: int
    line_total_inr: int
    reason: str = "requested"

    @field_validator("qty")
    @classmethod
    def qty_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("qty must be >= 1")
        return value


class Cart(BaseModel):
    user_id: str
    items: list[LineItem] = Field(default_factory=list)
    currency: str = "INR"

    @property
    def total_inr(self) -> int:
        return sum(i.line_total_inr for i in self.items)

    @property
    def item_count(self) -> int:
        return sum(i.qty for i in self.items)


class ValidationIssue(BaseModel):
    code: str
    message: str
    product_id: str | None = None


class GrowthCandidate(BaseModel):
    product_id: str
    name: str
    price_inr: int
    reason: str
    priority: int
    source: str = "catalog_complements"
    remaining_budget_after_inr: int
    projected_total_inr: int
    uplift_amount_inr: int
    uplift_percent: float


class GrowthOffer(BaseModel):
    product_id: str
    name: str
    price_inr: int
    reason: str
    source: str
    offer_text: str
    projected_total_inr: int
    uplift_amount_inr: int
    uplift_percent: float


class GrowthMetrics(BaseModel):
    baseline_order_value: int
    projected_order_value: int | None = None
    accepted_order_value: int | None = None
    uplift_amount: int | None = None
    uplift_percent: float | None = None
    recommendation_shown: bool = False
    recommendation_accepted: bool | None = None
    recommendation_declined: bool | None = None
    realized_paid_uplift: int | None = None
    candidates_considered: int = 0


class Proposal(BaseModel):
    id: str
    user_id: str
    session_id: str | None = None
    status: ProposalStatus
    items: list[LineItem]
    total_inr: int
    stated_budget_inr: int | None = None
    currency: str = "INR"
    reasons: list[str] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)
    created_at: datetime
    expires_at: datetime
    confirmed_at: datetime | None = None
    baseline_total_inr: int | None = None
    growth_offer: GrowthOffer | None = None
    growth_metrics: GrowthMetrics | None = None
    rejected_addon_ids: list[str] = Field(default_factory=list)

    @property
    def line_summary(self) -> str:
        return " + ".join(f"{i.name} (₹{i.line_total_inr})" for i in self.items)


class ConfirmationRequest(BaseModel):
    expected_total_inr: int
    idempotency_key: str | None = None
    user_id: str | None = None


class AddonDecisionRequest(BaseModel):
    decision: str  # "accept" | "skip"
    product_id: str | None = None


class CreateProposalRequest(BaseModel):
    user_id: str
    product_ids: list[str] = Field(default_factory=list)
    quantities: dict[str, int] = Field(default_factory=dict)
    stated_budget_inr: int | None = None
    session_id: str | None = None
    use_usual: bool = False
    with_growth: bool = True
    allow_substitute: bool = True
    allow_trim: bool = False


class PaymentRecord(BaseModel):
    id: str
    proposal_id: str
    status: PaymentStatus
    amount_inr: int
    razorpay_order_id: str | None = None
    mock: bool = True
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class SearchProductsResponse(BaseModel):
    products: list[Product]
    count: int


class UsualOrderResponse(BaseModel):
    user_id: str
    order_id: str | None
    items: list[LineItem]
    total_inr: int
    source: str
