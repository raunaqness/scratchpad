"""Shared test fixtures for Signal.

``openrouter_eval_model`` builds DeepEval's judge (needs a live key).
``install_models`` swaps every backend LLM call for a scripted, offline fake so
the graph, nodes, capabilities, checkpointer and streaming can be tested
deterministically.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from backend.config import settings
from backend.signal_models import Critique, GroundingNotes, TurnPlan

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def openrouter_eval_model():
    """Build DeepEval's OpenAI-compatible model adapter for OpenRouter."""

    if not settings.openrouter_api_key or not settings.openrouter_model:
        pytest.skip(
            "Set OPENROUTER_API_KEY and OPENROUTER_MODEL in .env "
            "before running DeepEval tests."
        )
    from deepeval.models import OpenAIModel

    return OpenAIModel(
        model=settings.openrouter_model,
        api_key=settings.openrouter_api_key,
        base_url=OPENROUTER_BASE_URL,
        temperature=settings.openrouter_temperature,
    )


# --------------------------------------------------------------------------- #
# Offline fake chat model
# --------------------------------------------------------------------------- #


@dataclass
class _Msg:
    content: str


@dataclass
class _Structured:
    value: Any

    def invoke(self, _messages: Any) -> Any:
        return self.value


@dataclass
class FakeChat:
    invoke_content: str = "{}"
    stream_chunks: list[str] = field(default_factory=lambda: ["ok"])
    structured: Any = None

    def invoke(self, _messages: Any) -> _Msg:
        return _Msg(self.invoke_content)

    def stream(self, _messages: Any):
        for chunk in self.stream_chunks:
            yield _Msg(chunk)

    def with_structured_output(self, _schema: Any) -> _Structured:
        return _Structured(self.structured)


def _chunks(text: str, size: int = 12) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


class FakeModelScript:
    """Maps a ``get_chat_model`` call (by its first tag) to a scripted FakeChat."""

    def __init__(
        self,
        *,
        plan: TurnPlan,
        angles: list[str] | None = None,
        outline: list[str] | None = None,
        brainstorm_questions: list[str] | None = None,
        expanded: str = "The idea, developed a little further with more detail.",
        tightened: str = "The idea, said more tightly.",
        skill_output: str = "# A built artifact\n\nGrounded in the scratchpad.",
        critique: Critique | None = None,
        grounding: list[str] | None = None,
        reply: str = "Here's where we are - tell me the next move.",
        summary: str = "Earlier: the user is working an idea on the scratchpad.",
    ) -> None:
        self.plan = plan
        self.expanded = expanded
        self.tightened = tightened
        self.skill_output = skill_output
        self.reply = reply
        self.summary = summary
        self._brainstorm_json = json.dumps(
            {
                "angles": angles or ["Angle A - lens one", "Angle B - lens two"],
                "outline": outline or [],
                "open_questions": brainstorm_questions or [],
            }
        )
        self._critique = critique or Critique(
            summary="A clear thread is forming; sharpen the second point.",
            points=["Name the reader the second point speaks to."],
        )
        self._grounding = GroundingNotes(items=grounding or [])

    def __call__(
        self,
        *,
        streaming: bool = False,
        temperature: float | None = None,
        tags: list[str] | None = None,
    ) -> FakeChat:
        tag = (tags or ["?"])[0]
        if tag == "signal:interpret":
            return FakeChat(structured=self.plan)
        if tag == "signal:brainstorm":
            return FakeChat(invoke_content=self._brainstorm_json)
        if tag == "signal:expand":
            return FakeChat(stream_chunks=_chunks(self.expanded))
        if tag == "signal:tighten":
            return FakeChat(stream_chunks=_chunks(self.tightened))
        if tag == "signal:skill":
            return FakeChat(stream_chunks=_chunks(self.skill_output))
        if tag == "signal:critique":
            return FakeChat(structured=self._critique)
        if tag == "signal:grounding":
            return FakeChat(structured=self._grounding)
        if tag == "signal:reply":
            return FakeChat(stream_chunks=_chunks(self.reply))
        if tag == "signal:summary":
            return FakeChat(invoke_content=self.summary)
        return FakeChat(structured=TurnPlan(mode="chat"))


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """Point persistence at a temp dir for one test."""

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "db_path", tmp_path / "signal.db")
    return tmp_path


@pytest.fixture
def install_models(monkeypatch):
    """Return a callable that installs a :class:`FakeModelScript` everywhere."""

    def _install(script: "FakeModelScript") -> "FakeModelScript":
        import backend.app as app
        import backend.capabilities.skills as skills
        import backend.capabilities.writing as writing
        import backend.llm as llm

        monkeypatch.setattr(llm, "get_chat_model", script)
        monkeypatch.setattr(app, "get_chat_model", script)
        monkeypatch.setattr(writing, "get_chat_model", script)
        monkeypatch.setattr(skills, "get_chat_model", script)
        return script

    return _install
