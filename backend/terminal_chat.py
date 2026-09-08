"""Interactive terminal client for Scratchpad.

Prints the reply, the scratchpad, and any derived-artifact tabs after each turn.
"""

from __future__ import annotations

import argparse
import textwrap

from backend.app import run_conversation


def _render_scratchpad(scratch: dict) -> str:
    if not scratch or scratch.get("status") == "empty":
        return "(scratchpad: empty)"
    lines = [
        f"── scratchpad v{scratch.get('version', 0)} [{scratch.get('status')}] ──"
    ]
    if scratch.get("title"):
        lines.append(f"title: {scratch['title']}")
    for angle in scratch.get("angles", []):
        lines.append(f"  • {angle}")
    for i, beat in enumerate(scratch.get("outline", []), 1):
        lines.append(f"  {i}. {beat}")
    if scratch.get("body"):
        lines.append("")
        lines.append(textwrap.indent(scratch["body"], "  "))
    if scratch.get("open_questions"):
        lines.append("")
        lines.append("  open questions:")
        lines.extend(f"    - {q}" for q in scratch["open_questions"])
    if scratch.get("sources"):
        lines.append("  sources: " + "; ".join(scratch["sources"]))
    return "\n".join(lines)


def _render_derived(derived: list[dict]) -> str:
    if not derived:
        return ""
    blocks = []
    for d in derived:
        head = f"── tab · {d.get('skill_name')} (from v{d.get('from_version')}) ──"
        block = [head, textwrap.indent(d.get("body", ""), "  ")]
        if d.get("open_questions"):
            block.append("  unverified in output:")
            block.extend(f"    - {q}" for q in d["open_questions"])
        blocks.append("\n".join(block))
    return "\n\n" + "\n\n".join(blocks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat with Scratchpad in the terminal.")
    parser.add_argument("--user-id", default="terminal-user")
    parser.add_argument("--conversation-id", default="terminal-session")
    args = parser.parse_args()

    print("Scratchpad terminal chat — type 'exit' or 'quit' to stop.\n")

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
            print(f"\nScratchpad error: {exc}\n")
            continue
        print(f"\nScratchpad: {result['assistant_message']}\n")
        print(_render_scratchpad(result.get("scratchpad", {})))
        print(_render_derived(result.get("derived", [])))
        print()


if __name__ == "__main__":
    main()
