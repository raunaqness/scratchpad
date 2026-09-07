"""DeepAgents-backed execution for Signal's writing capabilities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends import FilesystemBackend
from langchain_core.language_models import BaseChatModel

from backend.signal_models import SignalAnalysis
from backend.signal_tools import create_tools

_SIGNAL_PROFILE_REGISTERED = False
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SKILLS_PATH = "/backend/skills/"


def _register_signal_profile() -> None:
    """Remove unrelated built-ins from Signal's DeepAgents surface."""

    global _SIGNAL_PROFILE_REGISTERED
    if _SIGNAL_PROFILE_REGISTERED:
        return
    register_harness_profile(
        "openai",
        HarnessProfile(
            excluded_tools=frozenset(
                {
                    "ls",
                    "read_file",
                    "write_file",
                    "edit_file",
                    "delete",
                    "glob",
                    "grep",
                    "execute",
                    "task",
                }
            ),
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        ),
    )
    _SIGNAL_PROFILE_REGISTERED = True


def _content(result: dict[str, Any]) -> str:
    """Extract the final assistant text from a DeepAgents state."""

    messages = result.get("messages", [])
    for message in reversed(messages):
        text = getattr(message, "content", "")
        if isinstance(text, str) and text.strip():
            return text.strip()
    raise RuntimeError("DeepAgents returned no assistant content")


def _payload_from_content(text: str) -> dict[str, Any] | None:
    """Detect a tool payload accidentally returned as visible assistant text."""

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def analyze_with_deep_agent(
    model: BaseChatModel,
    system_prompt: str,
    analysis_prompt: str,
) -> dict[str, Any]:
    """Extract one Signal turn with DeepAgents structured output."""

    _register_signal_profile()
    agent = create_deep_agent(
        model=model,
        tools=[],
        backend=FilesystemBackend(root_dir=_PROJECT_ROOT),
        skills=[_SKILLS_PATH],
        system_prompt=(
            f"{system_prompt}\n\n"
            "You are Signal's structured turn analyzer. Return only the "
            "requested typed analysis. Never research, write a post, or use "
            "filesystem, shell, task, or unrelated tools."
        ),
        response_format=SignalAnalysis,
        name="signal-turn-analyzer",
    )
    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": analysis_prompt,
                }
            ]
        }
    )
    structured = result.get("structured_response")
    if isinstance(structured, SignalAnalysis):
        return structured.model_dump(exclude_none=True)
    if isinstance(structured, dict):
        return structured
    return json.loads(_content(result))


def create_post_with_deep_agent(
    model: BaseChatModel,
    request: dict[str, Any],
    capability,
) -> str:
    """Run Signal's create capability through the DeepAgents harness."""

    _register_signal_profile()
    create_tool = next(
        tool for tool in create_tools(
            create_capability=capability,
            edit_capability=lambda *_args: "",
            validate_capability=lambda *_args: "",
        )
        if tool.name == "create_linkedin_post"
    )

    agent = create_deep_agent(
        model=model,
        tools=[create_tool],
        backend=FilesystemBackend(root_dir=_PROJECT_ROOT),
        skills=[_SKILLS_PATH],
        system_prompt=(
            "You are Signal's LinkedIn writing execution agent. "
            "Use the create_linkedin_post_tool exactly once with the supplied "
            "confirmed request. Return the tool result exactly, without "
            "adding, removing, or rewriting any claims. Never use filesystem, "
            "shell, task, or unrelated tools."
        ),
        name="signal-linkedin-writer",
    )
    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": json.dumps(request, ensure_ascii=False),
                }
            ]
        }
    )
    post = _content(result)
    payload = _payload_from_content(post)
    if payload is not None:
        nested_request = payload.get("request")
        if isinstance(nested_request, dict):
            return capability(nested_request)
        if "product_name" in payload and "product_facts" in payload:
            return capability(payload)
    return post


def edit_post_with_deep_agent(
    model: BaseChatModel,
    request: dict[str, Any],
    draft: str,
    instruction: str,
    capability,
) -> str:
    """Run Signal's edit capability through the DeepAgents harness."""

    _register_signal_profile()
    edit_tool = next(
        tool for tool in create_tools(
            create_capability=lambda _request: "",
            edit_capability=capability,
            validate_capability=lambda *_args: "",
        )
        if tool.name == "edit_linkedin_post"
    )

    agent = create_deep_agent(
        model=model,
        tools=[edit_tool],
        backend=FilesystemBackend(root_dir=_PROJECT_ROOT),
        skills=[_SKILLS_PATH],
        system_prompt=(
            "You are Signal's LinkedIn editing execution agent. "
            "Use the edit_linkedin_post_tool exactly once with the supplied "
            "payload. Return the tool result exactly, without adding, "
            "removing, or rewriting claims. Never use filesystem, shell, "
            "task, or unrelated tools."
        ),
        name="signal-linkedin-editor",
    )
    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "request": request,
                            "draft": draft,
                            "instruction": instruction,
                        },
                        ensure_ascii=False,
                    ),
                }
            ]
        }
    )
    edited_post = _content(result)
    payload = _payload_from_content(edited_post)
    if payload is not None:
        nested_request = payload.get("request")
        nested_draft = payload.get("existing_draft", payload.get("draft"))
        nested_instruction = payload.get(
            "edit_instruction",
            payload.get("instruction", ""),
        )
        if (
            isinstance(nested_request, dict)
            and isinstance(nested_draft, str)
            and isinstance(nested_instruction, str)
        ):
            return capability(nested_request, nested_draft, nested_instruction)
    return edited_post
