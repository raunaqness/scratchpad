"""Interactive terminal client for Signal."""

from __future__ import annotations

import argparse

from backend.app import run_conversation


def main() -> None:
    """Run an interactive Signal conversation in the terminal."""

    parser = argparse.ArgumentParser(description="Chat with Signal in the terminal.")
    parser.add_argument("--user-id", default="terminal-user")
    parser.add_argument("--conversation-id", default="terminal-session")
    args = parser.parse_args()

    print("Signal terminal chat")
    print("Type 'exit' or 'quit' to stop.")

    while True:
        try:
            user_message = input("\nYou: ").strip()
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
            print(f"\nSignal: {result['assistant_message']}")
        except Exception as exc:
            print(f"\nSignal error: {exc}")


if __name__ == "__main__":
    main()
