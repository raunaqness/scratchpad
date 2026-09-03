"""Signal business-policy and guardrail enforcement."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from signal_models import PolicyDecision, SignalRequest

GUARDRAILS_PATH = Path(__file__).resolve().parent / "guardrails.json"


def _rules() -> dict[str, Any]:
    return json.loads(GUARDRAILS_PATH.read_text(encoding="utf-8"))


def preflight(message: str) -> PolicyDecision:
    """Reject unsupported capability requests before agent execution."""

    lowered = message.casefold()
    blocked = _rules().get("blocked_capabilities", [])
    terms = {
        "write a blog": "blog_post",
        "blog post": "blog_post",
        "email campaign": "email_campaign",
        "publish to linkedin": "publish_content",
        "post this to linkedin": "publish_content",
        "recipe": "unrelated_general_question",
    }
    for term, capability in terms.items():
        if term in lowered and capability in blocked:
            return PolicyDecision(
                allowed=False,
                reason=f"Capability '{capability}' is outside Signal's scope.",
            )
    return PolicyDecision(allowed=True, limits={"allowed_capability": "linkedin_post"})


def check_request(request: dict[str, Any]) -> PolicyDecision:
    """Validate normalized request data before a tool call."""

    try:
        normalized = SignalRequest.model_validate(request)
    except Exception as exc:
        return PolicyDecision(allowed=False, reason=f"Invalid Signal request: {exc}")
    if not normalized.product_name.strip():
        return PolicyDecision(allowed=False, reason="A product name is required.")
    if any(not fact.strip() for fact in normalized.product_facts):
        return PolicyDecision(allowed=False, reason="Product facts must be non-empty.")
    maximum_words = _maximum_words(normalized.length)
    return PolicyDecision(
        allowed=True,
        limits={
            "maximum_words": maximum_words,
            "confirmed_facts": list(normalized.product_facts),
        },
    )


def check_output(
    post: str,
    request: dict[str, Any],
    *,
    grounded: bool,
) -> PolicyDecision:
    """Validate generated content independently of model instructions."""

    request_decision = check_request(request)
    if not request_decision.allowed:
        return request_decision
    product_name = request["product_name"].casefold()
    if not post.strip() or product_name not in post.casefold():
        return PolicyDecision(allowed=False, reason="Draft is not anchored to the product.")
    maximum_words = request_decision.limits.get("maximum_words")
    if maximum_words is not None and len(post.split()) > maximum_words:
        return PolicyDecision(allowed=False, reason="Draft exceeds the requested word limit.")
    if not grounded:
        return PolicyDecision(allowed=False, reason="Draft contains unsupported claims.")
    return PolicyDecision(allowed=True)


def check_publish(*, approved: bool = False) -> PolicyDecision:
    """Keep side-effecting publishing disabled until an approval path exists."""

    return PolicyDecision(
        allowed=False,
        reason="LinkedIn publishing is not enabled in Signal.",
        requires_approval=not approved,
    )


def _maximum_words(length: Any) -> int | None:
    if not isinstance(length, str):
        return None
    match = re.search(
        r"\b(?:under|less than|max(?:imum)?(?: of)?|up to)\s*(\d+)",
        length.casefold(),
    )
    if match:
        return max(1, int(match.group(1)))
    match = re.search(r"\b(\d+)\s*words?\b", length.casefold())
    return int(match.group(1)) if match else None
