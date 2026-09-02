"""Signal's minimal OpenRouter-backed LangGraph backend."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from capabilities.social_media import create_linkedin_post
from config import settings
from prompts import CURRENT_PROMPT_VERSION, CURRENT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)
GUARDRAILS_PATH = Path(__file__).resolve().parent / "guardrails.json"


class SignalState(TypedDict, total=False):
    user_id: str
    conversation_id: str
    user_message: str
    turns: list[dict[str, str]]
    memory: dict[str, Any]
    analysis: dict[str, Any]
    assistant_message: str
    status: str
    tasks: list[str]
    completed_tasks: list[str]


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
    rules = _read_json(GUARDRAILS_PATH, {}).get("blocked_capabilities", [])
    lowered = message.lower()
    blocked_terms = {
        "write a blog": "blog_post",
        "blog post": "blog_post",
        "email campaign": "email_campaign",
        "publish to linkedin": "publish_content",
        "post this to linkedin": "publish_content",
    }
    return any(term in lowered and capability in rules for term, capability in blocked_terms.items())


def _conversation_path(conversation_id: str) -> Path:
    return settings.data_dir / "conversations" / f"{_safe_id(conversation_id)}.json"


def _memory_path(user_id: str) -> Path:
    return settings.data_dir / "memory" / f"{_safe_id(user_id)}.json"


def _update_memory(memory: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    """Persist only explicit fields extracted from the current conversation."""

    facts = {
        fact["key"]: fact
        for fact in memory.get("facts", [])
        if isinstance(fact, dict) and fact.get("key")
    }
    for key in ("company", "product_name", "product_description"):
        value = analysis.get(key)
        if value:
            facts[key] = {
                "key": key,
                "value": value,
                "source": "user_conversation",
                "confidence": "explicit",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
    return {
        **memory,
        "facts": list(facts.values()),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _analysis_prompt(state: SignalState) -> str:
    return f"""Analyze the latest user request for Signal.
Return JSON only with these keys:
scope (one of "linkedin_post" or "out_of_scope"),
company, product_name, product_description, tone, length, audience,
call_to_action (strings or null), needs_clarification (boolean),
clarification_question (string or null), tasks (array of strings).

Signal can only create a LinkedIn post. Extract only facts explicitly stated
in the conversation or memory. Do not infer missing values. A product name and
product description are required before drafting. Tone and length must be
clarified if the user has not selected them. Keep questions focused.

Memory:
{json.dumps(state.get("memory", {}), ensure_ascii=False)}

Conversation:
{json.dumps(state.get("turns", []), ensure_ascii=False)}
"""


def _analyze(state: SignalState) -> SignalState:
    if _blocked_by_guardrails(state["user_message"]):
        analysis = {"scope": "out_of_scope", "tasks": []}
    else:
        response = _model().invoke(
            [
                SystemMessage(content=CURRENT_SYSTEM_PROMPT),
                HumanMessage(content=_analysis_prompt(state)),
            ]
        )
        analysis = _extract_json(str(response.content))

    if analysis.get("scope") != "linkedin_post":
        analysis["scope"] = "out_of_scope"
    missing = [
        field
        for field in ("company", "product_name", "product_description", "tone", "length")
        if not analysis.get(field)
    ]
    if analysis["scope"] == "linkedin_post" and missing:
        analysis["needs_clarification"] = True
        questions = {
            "company": "Which company is promoting the product?",
            "product_name": "What is the product name?",
            "product_description": "What does the product do?",
            "tone": "What tone should the post use, such as professional or confident?",
            "length": "How long should the post be?",
        }
        analysis["clarification_question"] = " ".join(
            questions[field] for field in missing[:2]
        )
    status = (
        "out_of_scope"
        if analysis["scope"] == "out_of_scope"
        else "needs_clarification"
        if analysis.get("needs_clarification")
        else "in_progress"
    )
    return {
        **state,
        "analysis": analysis,
        "tasks": analysis.get("tasks", []),
        "status": status,
    }


def _route(state: SignalState) -> str:
    return "respond" if state["status"] != "in_progress" else "generate"


def _generate(state: SignalState) -> SignalState:
    allowed = {
        "company",
        "product_name",
        "product_description",
        "tone",
        "length",
        "audience",
        "call_to_action",
    }
    request = {
        key: value
        for key, value in state["analysis"].items()
        if key in allowed and value
    }
    post = create_linkedin_post(request)
    return {
        **state,
        "assistant_message": post,
        "status": "complete",
        "completed_tasks": state.get("tasks", []),
    }


def _respond(state: SignalState) -> SignalState:
    if state["status"] == "out_of_scope":
        message = (
            "I can currently help create LinkedIn posts from product "
            "information you provide. I can't write blog posts, publish "
            "content, or handle unrelated requests."
        )
    else:
        message = state["analysis"].get("clarification_question") or (
            "What product should the LinkedIn post promote, and what is its "
            "short description?"
        )
    return {**state, "assistant_message": message}


def _build_graph():
    graph = StateGraph(SignalState)
    graph.add_node("analyze", _analyze)
    graph.add_node("generate", _generate)
    graph.add_node("respond", _respond)
    graph.add_edge(START, "analyze")
    graph.add_conditional_edges(
        "analyze",
        _route,
        {"generate": "generate", "respond": "respond"},
    )
    graph.add_edge("generate", END)
    graph.add_edge("respond", END)
    return graph.compile()


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
    }
    result = GRAPH.invoke(
        state,
        config={
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
    memory = _update_memory(memory, result.get("analysis", {}))
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
    }
