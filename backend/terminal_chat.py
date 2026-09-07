"""Interactive terminal client for Signal.

Prints the reply and a compact view of the live artifact after every turn.
"""

from __future__ import annotations

import argparse
import textwrap

from backend.app import run_conversation


def _render_artifact(artifact: dict) -> str:
    if not artifact or artifact.get("status") == "empty":
        return "(artifact: empty)"
    lines = [
        f"── artifact v{artifact.get('version', 0)} "
        f"[{artifact.get('kind')} · {artifact.get('format')} · {artifact.get('status')}] ──"
    ]
    if artifact.get("title"):
        lines.append(f"title: {artifact['title']}")
    for angle in artifact.get("angles", []):
        lines.append(f"  • {angle}")
    for i, beat in enumerate(artifact.get("outline", []), 1):
        lines.append(f"  {i}. {beat}")
    if artifact.get("body"):
        lines.append("")
        lines.append(textwrap.indent(artifact["body"], "  "))
    if artifact.get("open_questions"):
        lines.append("")
        lines.append("  open questions:")
        lines.extend(f"    - {q}" for q in artifact["open_questions"])
    if artifact.get("sources"):
        lines.append("  sources: " + "; ".join(artifact["sources"]))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat with Signal in the terminal.")
    parser.add_argument("--user-id", default="terminal-user")
    parser.add_argument("--conversation-id", default="terminal-session")
    args = parser.parse_args()

    print("Signal terminal chat — type 'exit' or 'quit' to stop.\n")

    while True:
        try:
            user_message = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if user_message.casefold() in {"exit", "quit"}:
            break
        if not user_message:
            continue
        try:
            result = run_conversation(
                user_id=args.user_id,
                conversation_id=args.conversation_id,
                user_message=user_message,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"\nSignal error: {exc}\n")
            continue
        print(f"\nSignal: {result['assistant_message']}\n")
        print(_render_artifact(result.get("artifact", {})))
        print()


if __name__ == "__main__":
    main()
