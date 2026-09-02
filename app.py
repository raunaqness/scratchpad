"""Signal backend entry point.

The LangGraph workflow and capability implementation will be added alongside
the initial DeepEval conversation cases.
"""

from __future__ import annotations

from typing import Any


def run_conversation(
    *,
    user_id: str,
    conversation_id: str,
    user_message: str,
) -> dict[str, Any]:
    """Run one backend conversation turn.

    This contract is established before the graph implementation is added.
    """

    raise NotImplementedError("Signal's LangGraph workflow is not implemented yet")
