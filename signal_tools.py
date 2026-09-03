"""Policy-wrapped tools exposed to the Signal DeepAgent."""

from __future__ import annotations

import json
from typing import Any, Callable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from policy import check_request, check_publish


class CreatePostInput(BaseModel):
    request: dict[str, Any] = Field(description="Validated Signal request JSON.")


class EditPostInput(BaseModel):
    request: dict[str, Any] = Field(description="Validated Signal request JSON.")
    draft: str = Field(description="Existing LinkedIn draft.")
    instruction: str = Field(description="Explicit user edit instruction.")


class ValidateDraftInput(BaseModel):
    request: dict[str, Any] = Field(description="Validated Signal request JSON.")
    draft: str = Field(description="Draft to validate.")


class RetrieveFactsInput(BaseModel):
    facts: list[str] = Field(description="Confirmed facts available to Signal.")
    product_name: str = Field(description="Product whose facts are requested.")


def create_tools(
    *,
    create_capability: Callable[[dict[str, Any]], str],
    edit_capability: Callable[[dict[str, Any], str, str], str],
    validate_capability: Callable[[dict[str, Any], str], str],
) -> list[StructuredTool]:
    """Build the policy-wrapped Signal tool set."""

    def create_post(request: dict[str, Any]) -> str:
        decision = check_request(request)
        if not decision.allowed:
            return json.dumps({"status": "blocked", "reason": decision.reason})
        return create_capability(request)

    def edit_post(request: dict[str, Any], draft: str, instruction: str) -> str:
        decision = check_request(request)
        if not decision.allowed:
            return json.dumps({"status": "blocked", "reason": decision.reason})
        return edit_capability(request, draft, instruction)

    def validate_draft(request: dict[str, Any], draft: str) -> str:
        decision = check_request(request)
        if not decision.allowed:
            return json.dumps({"status": "blocked", "reason": decision.reason})
        return validate_capability(request, draft)

    def retrieve_facts(facts: list[str], product_name: str) -> str:
        return json.dumps(
            {
                "product_name": product_name,
                "facts": [fact for fact in facts if isinstance(fact, str) and fact.strip()],
                "source": "user_confirmed",
                "confidence": "explicit",
            }
        )

    def publish_disabled(_draft_id: str) -> str:
        decision = check_publish()
        return json.dumps({"status": "blocked", "reason": decision.reason})

    return [
        StructuredTool.from_function(
            func=create_post,
            name="create_linkedin_post",
            description="Create a grounded LinkedIn post from a validated Signal request.",
            args_schema=CreatePostInput,
        ),
        StructuredTool.from_function(
            func=edit_post,
            name="edit_linkedin_post",
            description="Edit a LinkedIn draft using only a validated Signal request.",
            args_schema=EditPostInput,
        ),
        StructuredTool.from_function(
            func=validate_draft,
            name="validate_linkedin_draft",
            description="Validate an existing LinkedIn draft against Signal constraints.",
            args_schema=ValidateDraftInput,
        ),
        StructuredTool.from_function(
            func=retrieve_facts,
            name="retrieve_confirmed_facts",
            description="Retrieve only explicitly confirmed facts for a product.",
            args_schema=RetrieveFactsInput,
        ),
        StructuredTool.from_function(
            func=publish_disabled,
            name="publish_linkedin_post",
            description="Publishing is disabled and always requires an unavailable approval path.",
        ),
    ]
