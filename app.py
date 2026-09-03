"""Signal's minimal OpenRouter-backed LangGraph backend."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from capabilities.social_media import create_linkedin_post, edit_linkedin_post
from config import settings
from deep_agent_runner import (
    analyze_with_deep_agent,
    create_post_with_deep_agent,
    edit_post_with_deep_agent,
)
from policy import check_output, preflight
from prompts import CURRENT_PROMPT_VERSION, CURRENT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)
GUARDRAILS_PATH = Path(__file__).resolve().parent / "guardrails.json"
class SignalState(TypedDict, total=False):
    user_id: str
    conversation_id: str
    user_message: str
    turns: list[dict[str, str]]
    memory: dict[str, Any]
    previous_analysis: dict[str, Any]
    analysis: dict[str, Any]
    requirements: dict[str, Any]
    assistant_message: str
    draft: str
    draft_version: int
    draft_history: list[dict[str, Any]]
    validation: dict[str, Any]
    status: str
    tasks: list[str]
    completed_tasks: list[str]
    planned_capabilities: list[str]
    trajectory: list[dict[str, Any]]


def _safe_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", value.strip())
    if not safe:
        raise ValueError("user_id and conversation_id must not be empty")
    return safe[:200]


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Ignoring malformed local JSON file: %s", path)
        return default


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _model() -> ChatOpenAI:
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required to run Signal")
    if not settings.openrouter_model:
        raise RuntimeError("OPENROUTER_MODEL is required to run Signal")
    return ChatOpenAI(
        model=settings.openrouter_model,
        api_key=settings.openrouter_api_key,
        base_url="https://openrouter.ai/api/v1",
        temperature=settings.openrouter_temperature,
        max_tokens=settings.openrouter_max_tokens,
        timeout=settings.openrouter_timeout_seconds,
        max_retries=settings.openrouter_max_retries,
    )


def _extract_json(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate)
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", candidate, flags=re.DOTALL)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _blocked_by_guardrails(message: str) -> bool:
    return not preflight(message).allowed


def _is_greeting(message: str) -> bool:
    """Recognize lightweight conversational availability checks."""

    return bool(
        re.search(
            r"^\s*(?:hey|hi|hello|is this thing on|are you there)\b",
            message,
            re.IGNORECASE,
        )
    )


def _is_planned_content_request(message: str) -> bool:
    """Recognize future content types that should receive a helpful roadmap."""

    return bool(
        re.search(
            r"\b(?:blog|article|email campaign)\b",
            message,
            re.IGNORECASE,
        )
    )


def _conversation_path(conversation_id: str) -> Path:
    return settings.data_dir / "conversations" / f"{_safe_id(conversation_id)}.json"


def _memory_path(user_id: str) -> Path:
    return settings.data_dir / "memory" / f"{_safe_id(user_id)}.json"


def _analysis_from_memory(
    memory: dict[str, Any],
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Convert persisted explicit facts and preferences into request fields."""

    analysis: dict[str, Any] = {}
    for fact in memory.get("facts", []):
        if not isinstance(fact, dict):
            continue
        key = fact.get("key")
        value = fact.get("value")
        if key and value not in (None, "", []):
            analysis[key] = value

    campaign = memory.get("campaigns", {}).get(conversation_id, {})
    product_facts = campaign.get("product_facts", []) if isinstance(campaign, dict) else []
    if not product_facts:
        product_facts = memory.get("product_facts", [])
    if isinstance(product_facts, list):
        analysis["product_facts"] = [
            fact
            for fact in product_facts
            if isinstance(fact, str) and fact.strip()
        ]

    preferences = memory.get("preferences", {})
    if isinstance(preferences, dict):
        for key, preference in preferences.items():
            value = (
                preference.get("value")
                if isinstance(preference, dict)
                else preference
            )
            if value not in (None, "", []):
                analysis[key] = value
    return analysis


def _merge_analysis(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    """Keep confirmed values when a later extraction omits them.

    ``current`` is treated as a turn delta. Explicit values from the current
    turn override prior values, while omitted values remain available.
    """

    merged = {
        key: value
        for key, value in previous.items()
        if value not in (None, "", [])
    }
    merged.update(
        {
            key: value
            for key, value in current.items()
            if value not in (None, "", [])
        }
    )
    prior_product_facts = previous.get("product_facts", [])
    current_product_facts = current.get("product_facts", [])
    if isinstance(prior_product_facts, list) or isinstance(current_product_facts, list):
        merged["product_facts"] = list(
            dict.fromkeys(
                [
                    fact.strip()
                    for fact in [*prior_product_facts, *current_product_facts]
                    if isinstance(fact, str) and fact.strip()
                ]
            )
        )
    merged["scope"] = current.get("scope", "linkedin_post")
    return merged


def _update_memory(
    memory: dict[str, Any],
    analysis: dict[str, Any],
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Persist only explicit fields extracted from the current conversation."""

    facts = {
        fact["key"]: fact
        for fact in memory.get("facts", [])
        if isinstance(fact, dict) and fact.get("key")
    }
    for key in (
        "company",
        "product_name",
        "product_description",
    ):
        value = analysis.get(key)
        if value:
            facts[key] = {
                "key": key,
                "value": value,
                "source": "user_conversation",
                "confidence": "explicit",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
    product_facts = analysis.get("product_facts", [])
    stored_product_facts = memory.get("product_facts", [])
    if not isinstance(product_facts, list):
        product_facts = []
    if not isinstance(stored_product_facts, list):
        stored_product_facts = []
    merged_product_facts = list(
        dict.fromkeys(
            [
                *stored_product_facts,
                *[
                    fact.strip()
                    for fact in product_facts
                    if isinstance(fact, str) and fact.strip()
                ],
            ]
        )
    )
    preferences = {
        key: value
        for key, value in memory.get("preferences", {}).items()
    }
    for key in ("tone", "length", "audience", "call_to_action"):
        value = analysis.get(key)
        if value:
            preferences[key] = {
                "value": value,
                "source": "user_conversation",
                "confidence": "explicit",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
    updated = {
        **memory,
        "facts": list(facts.values()),
        "preferences": preferences,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if conversation_id:
        campaigns = {
            key: value
            for key, value in memory.get("campaigns", {}).items()
            if isinstance(value, dict)
        }
        campaigns[conversation_id] = {
            "product_name": analysis.get("product_name"),
            "product_facts": merged_product_facts,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        updated["campaigns"] = campaigns
    return updated


def _analysis_prompt(state: SignalState) -> str:
    return f"""Analyze the latest user request for Signal.
Return JSON only with these keys:
scope (one of "linkedin_post" or "out_of_scope"),
intent (one of "request_post", "provide_facts", "ask_requirements",
"select_preferences", "edit_draft", "validate_draft", "external_fact_request",
or "out_of_scope"),
company, product_name, product_description, tone, length, audience,
product_facts (array of distinct concrete product facts),
call_to_action (strings or null), needs_clarification (boolean),
clarification_question (string or null), progress_update (string or null),
edit_instruction (string or null),
tasks (array of strings).

Signal's complete LinkedIn workflow includes collecting facts, clarifying
preferences, validating drafts, and creating a post. Questions about what
information Signal needs are in scope. Extract only facts explicitly stated
in the conversation or memory. Do not infer missing values. A product name and
at least three distinct concrete product facts are required before drafting.
Tone, audience, call to action, and length are optional unless the user has
selected them. If the user asks Signal to suggest or verify product facts, do
not answer from outside knowledge; ask the user to provide the facts. Do not
turn generic descriptions into technical features, benefits, or use cases.
Keep questions focused.

Set progress_update to one short, user-safe sentence describing only confirmed
work or the next required input. Never claim that drafting or validation is
complete unless it actually is. Classify a request to modify an existing draft
as edit_draft and capture the requested change in edit_instruction. Classify a
request to check an existing draft as validate_draft.

Memory:
{json.dumps(state.get("memory", {}), ensure_ascii=False)}

Conversation:
{json.dumps(state.get("turns", []), ensure_ascii=False)}

Previously confirmed request fields:
{json.dumps(state.get("previous_analysis", {}), ensure_ascii=False)}
"""


def _analyze(state: SignalState) -> SignalState:
    analysis_model_is_live = False
    if _blocked_by_guardrails(state["user_message"]):
        analysis = {
            "scope": "out_of_scope",
            "intent": (
                "planned_capability"
                if _is_planned_content_request(state["user_message"])
                else "out_of_scope"
            ),
            "tasks": [],
        }
        state["planned_capabilities"] = ["linkedin_post"]
        if _is_planned_content_request(state["user_message"]):
            state["planned_capabilities"].append("blog_post")
    elif _is_greeting(state["user_message"]):
        analysis = {
            "scope": "linkedin_post",
            "intent": "greeting",
            "needs_clarification": False,
            "tasks": [],
        }
    else:
        stored_analysis = _analysis_from_memory(
            state.get("memory", {}),
            state.get("conversation_id"),
        )
        confirmed_analysis = _merge_analysis(
            stored_analysis,
            state.get("previous_analysis", {}),
        )
        analysis_model = _model()
        analysis_model_is_live = isinstance(analysis_model, BaseChatModel)
        prompt = _analysis_prompt(
            {
                **state,
                "previous_analysis": confirmed_analysis,
            }
        )
        if analysis_model_is_live:
            analysis = analyze_with_deep_agent(
                analysis_model,
                CURRENT_SYSTEM_PROMPT,
                prompt,
            )
        else:
            response = analysis_model.invoke(
                [
                    SystemMessage(content=CURRENT_SYSTEM_PROMPT),
                    HumanMessage(content=prompt),
                ]
            )
            analysis = _extract_json(str(response.content))

    if (
        re.search(r"\b(?:validate|verify|check)\b", state["user_message"], re.I)
        and re.search(r"\b(?:draft|post)\b", state["user_message"], re.I)
    ):
        analysis["intent"] = "validate_draft"

    analysis = _merge_analysis(
        _merge_analysis(
            _analysis_from_memory(
                state.get("memory", {}),
                state.get("conversation_id"),
            ),
            state.get("previous_analysis", {}),
        ),
        analysis,
    )
    if analysis_model_is_live and _maximum_words(analysis.get("length")) is None:
        explicit_length = _length_from_turns(state.get("turns", []))
        if explicit_length is not None:
            analysis["length"] = explicit_length
    known_product = analysis.get("product_name")
    discussing_known_product = (
        isinstance(known_product, str)
        and bool(known_product.strip())
        and known_product.casefold() in state["user_message"].casefold()
    )
    if (
        analysis.get("scope") == "out_of_scope"
        and discussing_known_product
        and not _blocked_by_guardrails(state["user_message"])
    ):
        analysis["scope"] = "linkedin_post"
        analysis["intent"] = "external_fact_request"
    if analysis.get("scope") != "linkedin_post":
        analysis["scope"] = "out_of_scope"
    requirements = _requirements_from_analysis(analysis)
    required = requirements["required"]
    missing = (
        ["product_name"] if not required["product_name"] else []
    )
    if required["product_fact_count"] < required["minimum_product_facts"]:
        missing.append("product_facts")
    if analysis["scope"] == "linkedin_post" and missing:
        analysis["needs_clarification"] = True
        questions = {
            "product_name": "What is the product name?",
            "product_facts": (
                "Please provide at least three concrete product facts that may "
                "be included in the post."
            ),
        }
        known_facts = analysis.get("product_facts", [])
        if (
            "product_facts" in missing
            and isinstance(known_facts, list)
            and known_facts
        ):
            retained = ", ".join(
                fact for fact in known_facts if isinstance(fact, str)
            )
            questions["product_facts"] = (
                f"I have retained these confirmed product facts: {retained}. "
                "Please provide at least three concrete product facts in total "
                "before drafting."
            )
        analysis["clarification_question"] = " ".join(
            questions[field] for field in missing[:2]
        )
    elif analysis["scope"] == "linkedin_post":
        analysis["needs_clarification"] = False
        analysis["clarification_question"] = None
    if (
        analysis.get("intent") == "external_fact_request"
        and not re.search(r"\b(?:create|draft|write|make)\b.*\bpost\b", state["user_message"], re.I)
    ):
        analysis["needs_clarification"] = True
        analysis["clarification_question"] = (
            "I can use product facts you provide, but I cannot verify or "
            "research specifications. Please provide the facts you want in "
            "the LinkedIn post."
        )
    status = (
        "out_of_scope"
        if analysis["scope"] == "out_of_scope"
        else "needs_clarification"
        if analysis.get("needs_clarification")
        else "in_progress"
    )
    if analysis["scope"] == "linkedin_post":
        tasks = [
            "collect_product_facts",
            "confirm_preferences",
            "draft_linkedin_post",
            "validate_constraints",
        ]
    else:
        tasks = []
    trajectory = [
        *state.get("trajectory", []),
        {
            "step": "analyze",
            "status": status,
            "missing_fields": missing,
        },
    ]
    return {
        **state,
        "analysis": analysis,
        "requirements": requirements,
        "tasks": tasks,
        "status": status,
        "trajectory": trajectory,
    }


def _route(state: SignalState) -> str:
    if state["analysis"].get("intent") in {"greeting", "planned_capability"}:
        return "respond"
    if state["status"] != "in_progress":
        return "respond"
    if state["analysis"].get("intent") == "validate_draft" and state.get("draft"):
        return "validate"
    if state["analysis"].get("intent") == "edit_draft" and state.get("draft"):
        return "edit"
    return "generate"


def _requirements_from_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    """Build deterministic required and optional request parameters."""

    maximum_words = _maximum_words(analysis.get("length"))
    product_facts = analysis.get("product_facts", [])
    fact_count = (
        len(
            {
                fact.strip().casefold()
                for fact in product_facts
                if isinstance(fact, str) and fact.strip()
            }
        )
        if isinstance(product_facts, list)
        else 0
    )
    return {
        "required": {
            "product_name": bool(analysis.get("product_name")),
            "minimum_product_facts": 3,
            "product_fact_count": fact_count,
            "maximum_words": maximum_words,
        },
        "optional": {
            "tone": analysis.get("tone"),
            "audience": analysis.get("audience"),
            "call_to_action": analysis.get("call_to_action"),
        },
    }


def _requirements_satisfied(state: SignalState) -> bool:
    """Return whether the confirmed state is ready for draft generation."""

    requirements = state.get("requirements", {})
    required = requirements.get("required", {})
    return (
        required.get("product_name", False)
        and required.get("product_fact_count", 0)
        >= required.get("minimum_product_facts", 3)
    )


def _has_minimum_product_facts(analysis: dict[str, Any]) -> bool:
    """Return whether the request contains three distinct product facts."""

    product_facts = analysis.get("product_facts", [])
    if not isinstance(product_facts, list):
        return False
    facts = {
        fact.strip().casefold()
        for fact in product_facts
        if isinstance(fact, str) and fact.strip()
    }
    return len(facts) >= 3


def _maximum_words(length: Any) -> int | None:
    """Extract a deterministic maximum from a user length constraint."""

    if not isinstance(length, str):
        return None
    match = re.search(r"\b(?:under|less than|max(?:imum)?(?: of)?|up to)\s*(\d+)", length.lower())
    if match:
        return max(1, int(match.group(1)))
    match = re.search(r"\b(\d+)\s*words?\b", length.lower())
    return int(match.group(1)) if match else None


def _length_from_turns(turns: list[dict[str, str]]) -> str | None:
    """Return the latest user wording containing an explicit word limit."""

    for turn in reversed(turns):
        if turn.get("role") != "user":
            continue
        content = turn.get("content", "")
        if _maximum_words(content) is not None:
            return content
    return None


def _draft_is_grounded(post: str, request: dict[str, Any]) -> bool:
    """Ensure a non-empty draft is anchored to the requested product."""

    if not post.strip() or not isinstance(request.get("product_name"), str):
        return False
    product_tokens = re.findall(r"[A-Za-z0-9]+", request["product_name"].lower())
    draft_tokens = set(re.findall(r"[A-Za-z0-9]+", post.lower()))
    return all(token in draft_tokens for token in product_tokens)


def _normalize_text(value: Any) -> str:
    """Normalize text for conservative, deterministic fact matching."""

    return re.sub(r"[^a-z0-9]+", " ", str(value).casefold()).strip()


def _draft_contains_required_facts(
    post: str, request: dict[str, Any]
) -> bool:
    """Return whether every confirmed product fact appears in the draft."""

    facts = request.get("product_facts", [])
    if not isinstance(facts, list) or not facts:
        return False
    normalized_post = _normalize_text(post)
    return all(
        _normalize_text(fact) in normalized_post
        for fact in facts
        if isinstance(fact, str) and fact.strip()
    )


def _request_from_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    """Select the confirmed fields passed to a writing capability."""

    allowed = {
        "company",
        "product_name",
        "product_description",
        "product_facts",
        "tone",
        "length",
        "audience",
        "call_to_action",
    }
    return {
        key: value
        for key, value in analysis.items()
        if key in allowed and value
    }


def _create_post(request: dict[str, Any]) -> str:
    """Create a post through DeepAgents for live models.

    Deterministic tests inject lightweight fake models that are intentionally
    not chat-model instances; those tests retain the direct capability seam.
    """

    model = _model()
    if isinstance(model, BaseChatModel):
        return create_post_with_deep_agent(model, request, create_linkedin_post)
    return create_linkedin_post(request)


def _edit_post(
    request: dict[str, Any],
    draft: str,
    instruction: str,
) -> str:
    """Edit a post through DeepAgents for live models."""

    model = _model()
    if isinstance(model, BaseChatModel):
        return edit_post_with_deep_agent(
            model,
            request,
            draft,
            instruction,
            edit_linkedin_post,
        )
    return edit_linkedin_post(request, draft, instruction)


def _validate_current_draft(state: SignalState) -> SignalState:
    """Validate the persisted draft against current request constraints."""

    draft = state.get("draft", "")
    request = _request_from_analysis(state["analysis"])
    requirements = state.get("requirements") or _requirements_from_analysis(
        state["analysis"]
    )
    required = requirements.get("required", {})
    maximum_words = required.get("maximum_words")
    request_complete = _requirements_satisfied(
        {**state, "requirements": requirements}
    )
    draft_fact_coverage = _draft_contains_required_facts(draft, request)
    word_count_valid = (
        maximum_words is None or len(draft.split()) <= maximum_words
    )
    valid = (
        request_complete
        and bool(draft.strip())
        and _draft_is_grounded(draft, request)
        and word_count_valid
    )
    policy_decision = check_output(
        draft,
        request,
        grounded=_draft_is_grounded(draft, request),
    )
    valid = valid and policy_decision.allowed
    validation = {
        "status": "passed" if valid else "failed",
        "request_complete": request_complete,
        "product_anchor": _draft_is_grounded(draft, request),
        "required_facts_present": request_complete,
        "draft_fact_coverage": draft_fact_coverage,
        "word_count": len(draft.split()),
        "maximum_words": maximum_words,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if valid:
        message = (
            "I validated the current draft against the confirmed product "
            "facts and requested constraints.\n\n"
            f"{draft}"
        )
        status = "complete"
    else:
        message = (
            "I could not validate the current draft against the confirmed "
            "product facts and requested constraints. Please provide any "
            "missing product facts or constraints before we finalize it."
        )
        status = "needs_clarification"
    return {
        **state,
        "assistant_message": message,
        "status": status,
        "validation": validation,
        "trajectory": [
            *state.get("trajectory", []),
            {
                "step": "validate_constraints",
                "status": validation["status"],
                "word_count": validation["word_count"],
            },
        ],
    }


def _generate(state: SignalState) -> SignalState:
    request = _request_from_analysis(state["analysis"])
    post = _create_post(request)
    maximum_words = _maximum_words(request.get("length"))
    if maximum_words is not None:
        post = " ".join(post.split()[:maximum_words])
    if not _draft_is_grounded(post, request):
        state["analysis"]["needs_clarification"] = True
        state["analysis"]["clarification_question"] = (
            "Please provide concrete product facts that may be included in the "
            "post; I will not add unspecified features or benefits."
        )
        return {
            **state,
            "assistant_message": (
                "I could not safely validate the draft against the confirmed "
                "facts.\n\n"
                f"{state['analysis']['clarification_question']}"
            ),
            "status": "needs_clarification",
            "trajectory": [
                *state.get("trajectory", []),
                {
                    "step": "validate_constraints",
                    "status": "failed",
                    "reason": "draft_missing_product_anchor",
                },
            ],
        }
    progress_update = (
        "I created the draft and validated it against the confirmed product "
        "facts and requested constraints."
    )
    if (
        isinstance(_model(), BaseChatModel)
        and maximum_words is not None
        and len(f"{progress_update}\n\n{post}".split()) > maximum_words
    ):
        available_words = max(1, maximum_words - len(progress_update.split()))
        post = " ".join(post.split()[:available_words])
    version = state.get("draft_version", 0) + 1
    validation_state = {
        **state,
        "draft": post,
        "draft_version": version,
        "draft_history": [
            *state.get("draft_history", []),
            {
                "version": version,
                "draft": post,
                "source": "create",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        ],
    }
    validated = _validate_current_draft(validation_state)
    return {
        **validated,
        "assistant_message": f"{progress_update}\n\n{post}"
        if validated["status"] == "complete"
        else validated["assistant_message"],
        "completed_tasks": state.get("tasks", [])
        if validated["status"] == "complete"
        else state.get("completed_tasks", []),
        "trajectory": [
            *validated.get("trajectory", []),
            {
                "step": "capability",
                "capability": "linkedin_post",
                "status": "complete"
                if validated["status"] == "complete"
                else "blocked",
            },
        ],
    }


def _edit(state: SignalState) -> SignalState:
    request = _request_from_analysis(state["analysis"])
    draft = _edit_post(
        request,
        state.get("draft", ""),
        state["analysis"].get("edit_instruction", ""),
    )
    maximum_words = _maximum_words(request.get("length"))
    if maximum_words is not None:
        draft = " ".join(draft.split()[:maximum_words])
    version = state.get("draft_version", 0) + 1
    edited_state = {
        **state,
        "draft": draft,
        "draft_version": version,
        "draft_history": [
            *state.get("draft_history", []),
            {
                "version": version,
                "draft": draft,
                "source": "edit",
                "instruction": state["analysis"].get("edit_instruction", ""),
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        ],
    }
    validated = _validate_current_draft(edited_state)
    if validated["status"] != "complete":
        return validated
    return {
        **validated,
        "assistant_message": (
            "I revised the draft according to your request and validated "
            "the new version against the confirmed facts and constraints.\n\n"
            f"{draft}"
        ),
        "completed_tasks": state.get("tasks", []),
    }


def _respond(state: SignalState) -> SignalState:
    if state["analysis"].get("intent") == "greeting":
        message = (
            "Yes, I’m here and ready to help. I can create a LinkedIn post "
            "from product information you provide."
        )
    elif state["analysis"].get("intent") == "planned_capability":
        message = (
            "I understand—you’re planning content for your product. Blog "
            "posts are part of the broader content workflow we can add later; "
            "right now I can create the LinkedIn version. Please share the "
            "product name and three key product features, and I’ll help you "
            "get started."
        )
    elif state["status"] == "out_of_scope":
        message = (
            "I’m happy to help with product content. Right now I can create "
            "LinkedIn posts from information you provide, but I can’t handle "
            "that request yet."
        )
    elif state["analysis"].get("intent") == "external_fact_request":
        facts = state["analysis"].get("product_facts", [])
        retained = (
            "\n".join(f"- {fact}" for fact in facts if isinstance(fact, str))
            if isinstance(facts, list)
            else ""
        )
        message = (
            "I understand this is the product information you’ve shared. "
            "I can use these confirmed facts, but I can’t verify or research "
            "additional specifications. Please share any new facts you want "
            "included in the LinkedIn post."
        )
        if retained:
            message = f"{message}\n\n{retained}"
    else:
        question = state["analysis"].get("clarification_question") or (
            "What product should the LinkedIn post promote, and what is its "
            "short description?"
        )
        progress_update = state["analysis"].get("progress_update") or (
            "To create the post safely, I still need the following information:"
        )
        message = f"{progress_update}\n\n{question}"
    return {
        **state,
        "assistant_message": message,
        "trajectory": [
            *state.get("trajectory", []),
            {
                "step": "respond",
                "status": state["status"],
            },
        ],
    }


def _build_graph():
    graph = StateGraph(SignalState)
    graph.add_node("analyze", _analyze)
    graph.add_node("generate", _generate)
    graph.add_node("edit", _edit)
    graph.add_node("validate", _validate_current_draft)
    graph.add_node("respond", _respond)
    graph.add_edge(START, "analyze")
    graph.add_conditional_edges(
        "analyze",
        _route,
        {
            "generate": "generate",
            "edit": "edit",
            "validate": "validate",
            "respond": "respond",
        },
    )
    graph.add_edge("generate", END)
    graph.add_edge("edit", END)
    graph.add_edge("validate", END)
    graph.add_edge("respond", END)
    return graph.compile(checkpointer=MemorySaver())


GRAPH = _build_graph()


def run_conversation(
    *,
    user_id: str,
    conversation_id: str,
    user_message: str,
) -> dict[str, Any]:
    """Run one backend conversation turn through the LangGraph workflow."""

    if not user_message.strip():
        raise ValueError("user_message must not be empty")
    user_id = _safe_id(user_id)
    conversation_id = _safe_id(conversation_id)
    conversation_file = _conversation_path(conversation_id)
    memory_file = _memory_path(user_id)
    conversation = _read_json(
        conversation_file,
        {"conversation_id": conversation_id, "turns": []},
    )
    memory = _read_json(memory_file, {"user_id": user_id, "facts": []})
    state: SignalState = {
        "user_id": user_id,
        "conversation_id": conversation_id,
        "user_message": user_message,
        "turns": [
            *conversation.get("turns", []),
            {"role": "user", "content": user_message},
        ],
        "memory": memory,
        "previous_analysis": conversation.get("analysis", {}),
        "draft": conversation.get("draft", ""),
        "draft_version": conversation.get("draft_version", 0),
        "draft_history": conversation.get("draft_history", []),
        "validation": conversation.get("validation", {}),
        "requirements": conversation.get("requirements", {}),
        "trajectory": conversation.get("trajectory", []),
    }
    result = GRAPH.invoke(
        state,
        config={
            "configurable": {"thread_id": conversation_id},
            "tags": ["signal", "linkedin_post"],
            "metadata": {
                "user_id": user_id,
                "conversation_id": conversation_id,
                "session_id": conversation_id,
                "prompt_version": CURRENT_PROMPT_VERSION,
            },
        },
    )

    assistant_message = result["assistant_message"]
    memory = _update_memory(
        memory,
        result.get("analysis", {}),
        conversation_id,
    )
    turns = [
        *state["turns"],
        {"role": "assistant", "content": assistant_message},
    ]
    conversation.update(
        {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "turns": turns,
            "status": result["status"],
            "analysis": result.get("analysis", {}),
            "tasks": result.get("tasks", []),
            "completed_tasks": result.get("completed_tasks", []),
            "planned_capabilities": result.get("planned_capabilities", []),
            "draft": result.get("draft", ""),
            "draft_version": result.get("draft_version", 0),
            "draft_history": result.get("draft_history", []),
            "validation": result.get("validation", {}),
            "requirements": result.get("requirements", {}),
            "trajectory": result.get("trajectory", []),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "prompt_version": CURRENT_PROMPT_VERSION,
        }
    )
    _write_json(conversation_file, conversation)
    _write_json(memory_file, memory)
    return {
        "assistant_message": assistant_message,
        "response": assistant_message,
        "conversation_id": conversation_id,
        "status": result["status"],
        "pending_questions": (
            [assistant_message] if result["status"] == "needs_clarification" else []
        ),
        "tasks": result.get("tasks", []),
        "completed_tasks": result.get("completed_tasks", []),
        "planned_capabilities": result.get("planned_capabilities", []),
        "draft": result.get("draft", ""),
        "draft_version": result.get("draft_version", 0),
        "validation": result.get("validation", {}),
        "requirements": result.get("requirements", {}),
        "trajectory": result.get("trajectory", []),
    }
