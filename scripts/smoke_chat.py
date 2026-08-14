#!/usr/bin/env python3
"""Log in (or register), create a conversation, and send one message.

The copy-paste curl in the docs fails for three reasons this script avoids:
register conflicts once the email exists, a failed register writes no cookie,
and ``cnv_…`` is a placeholder, not an id.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EMAIL = "you@example.com"
DEFAULT_PASSWORD = "correct-horse-battery"  # noqa: S105 — local demo only


def _load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


def _fail(response: httpx.Response) -> None:
    try:
        body = response.json()
        message = json.dumps(body, indent=2)
    except json.JSONDecodeError:
        message = response.text or f"HTTP {response.status_code}"
    sys.exit(
        f"{response.request.method} {response.request.url} -> {response.status_code}\n{message}"
    )


def _json(response: httpx.Response) -> dict:
    if response.status_code >= 400:
        _fail(response)
    try:
        payload = response.json()
    except json.JSONDecodeError:
        _fail(response)
    if not isinstance(payload, dict):
        _fail(response)
    return payload


def main() -> None:
    _load_dotenv()
    port = os.environ.get("JANUS_API_PORT", "8080")
    base = f"http://127.0.0.1:{port}"
    email = os.environ.get("JANUS_SMOKE_EMAIL", DEFAULT_EMAIL)
    password = os.environ.get("JANUS_SMOKE_PASSWORD", DEFAULT_PASSWORD)
    prompt = os.environ.get("JANUS_SMOKE_PROMPT", "Hello")

    with httpx.Client(base_url=base, timeout=30.0) as client:
        ready = client.get("/readyz")
        if ready.status_code != 200:
            sys.exit(
                f"Nothing Janus-like on {base} (HTTP {ready.status_code}). "
                "Check JANUS_API_PORT in .env — on this host it is 8090, not 8080."
            )

        login = client.post("/v1/auth/login", json={"email": email, "password": password})
        if login.status_code >= 400:
            register = client.post(
                "/v1/auth/register",
                json={
                    "email": email,
                    "password": password,
                    "name": "You",
                    "organization_name": "Acme",
                },
            )
            session = _json(register)
        else:
            session = _json(login)

        conversation = _json(client.post("/v1/conversations", json={}))
        conversation_id = conversation.get("id")
        if not conversation_id:
            sys.exit(f"create conversation returned no id:\n{json.dumps(conversation, indent=2)}")

        print(f"api            {base}")
        print(f"user           {session.get('user', {}).get('email')}")
        print(f"conversation   {conversation_id}")
        print("--- stream ---")

        with client.stream(
            "POST",
            f"/v1/conversations/{conversation_id}/messages",
            json={"content": prompt},
        ) as stream:
            if stream.status_code >= 400:
                stream.read()
                _fail(stream)
            for line in stream.iter_lines():
                print(line)

        detail = _json(client.get(f"/v1/conversations/{conversation_id}"))
        messages = detail.get("messages") or []
        assistant = next(
            (item for item in reversed(messages) if item.get("role") == "assistant"),
            None,
        )
        print("--- persisted ---")
        print(f"messages       {len(messages)}")
        if assistant:
            print(f"model          {assistant.get('model')}")
            print(f"answer         {(assistant.get('content') or '')[:160]}")


if __name__ == "__main__":
    main()
