"""The backend's public port must not accept a spoofed identity.

When ``GOOGLE_AUTH_ENABLED`` is on, ``/agent`` and ``/api/threads`` are only for
the Next.js BFF, which proves itself with the shared ``x-signal-proxy-secret``
header. Without it (or with auth off) the guard is a no-op.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.agent as agent
from backend.config import settings

SECRET = "test-shared-secret"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "session_secret", SECRET)

    async def fake_stream(**_kwargs):
        yield {
            "type": "final",
            "assistant_message": "ok",
            "scratchpad": {"body": "", "version": 0},
            "derived": [],
            "status": "notes",
            "plan": {},
            "versions": [],
            "head": 0,
        }

    monkeypatch.setattr(agent, "astream_conversation", fake_stream)
    return TestClient(agent.app)


def _run_input() -> dict:
    return {
        "threadId": "t-1",
        "runId": "r-1",
        "state": {},
        "messages": [{"id": "m0", "role": "user", "content": "hi"}],
        "tools": [],
        "context": [],
        "forwardedProps": {"user_id": "attacker"},
    }


def test_guard_is_off_when_auth_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "google_auth_enabled", False)
    # No secret header — still allowed through to the stream.
    resp = client.post("/agent", json=_run_input())
    assert resp.status_code == 200


def test_agent_rejects_calls_without_the_proxy_secret(client, monkeypatch):
    monkeypatch.setattr(settings, "google_auth_enabled", True)
    resp = client.post("/agent", json=_run_input())
    assert resp.status_code == 403


def test_agent_accepts_the_proxy_secret(client, monkeypatch):
    monkeypatch.setattr(settings, "google_auth_enabled", True)
    resp = client.post(
        "/agent",
        json=_run_input(),
        headers={"x-signal-proxy-secret": SECRET},
    )
    assert resp.status_code == 200


def test_threads_list_is_guarded(client, monkeypatch):
    monkeypatch.setattr(settings, "google_auth_enabled", True)
    assert client.get("/api/threads", params={"user_id": "u1"}).status_code == 403
    ok = client.get(
        "/api/threads",
        params={"user_id": "u1"},
        headers={"x-signal-proxy-secret": SECRET},
    )
    assert ok.status_code == 200
    assert "threads" in ok.json()
