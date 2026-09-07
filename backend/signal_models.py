"""Typed contracts shared by Signal policy and DeepAgents tools."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ConfirmedFact(BaseModel):
    """A fact that may be used for grounded content."""

    value: str
    source: str = "user_conversation"
    confidence: Literal["explicit", "retrieved", "inferred"] = "explicit"
    product_name: str | None = None
    campaign_id: str | None = None
    confirmed: bool = True
    updated_at: datetime | None = None


class SignalAnalysis(BaseModel):
    """Structured interpretation of one Signal turn."""

    scope: Literal["linkedin_post", "out_of_scope"] = "linkedin_post"
    intent: str = "request_post"
    company: str | None = None
    product_name: str | None = None
    product_description: str | None = None
    tone: str | None = None
    length: str | None = None
    audience: str | None = None
    call_to_action: str | None = None
    product_facts: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_question: str | None = None
    edit_instruction: str | None = None
    tasks: list[str] = Field(default_factory=list)


class SignalRequest(BaseModel):
    """Validated input passed to writing capabilities."""

    company: str | None = None
    product_name: str
    product_description: str | None = None
    product_facts: list[str] = Field(min_length=3)
    tone: str | None = None
    length: str | None = None
    audience: str | None = None
    call_to_action: str | None = None


class PolicyDecision(BaseModel):
    """Result of an application-owned policy check."""

    allowed: bool
    reason: str | None = None
    requires_approval: bool = False
    limits: dict[str, Any] = Field(default_factory=dict)
