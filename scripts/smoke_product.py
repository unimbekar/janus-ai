#!/usr/bin/env python3
"""Exercise knowledge ingest/search, an agent run, and /v1/responses."""

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
    payload = response.json()
    if not isinstance(payload, dict):
        _fail(response)
    return payload


def main() -> None:
    _load_dotenv()
    port = os.environ.get("JANUS_API_PORT", "8080")
    base = f"http://127.0.0.1:{port}"
    email = os.environ.get("JANUS_SMOKE_EMAIL", DEFAULT_EMAIL)
    password = os.environ.get("JANUS_SMOKE_PASSWORD", DEFAULT_PASSWORD)

    with httpx.Client(base_url=base, timeout=60.0) as client:
        ready = client.get("/readyz")
        if ready.status_code != 200:
            sys.exit(
                f"Nothing Janus-like on {base} (HTTP {ready.status_code}). "
                "Check JANUS_API_PORT in .env."
            )

        login = client.post("/v1/auth/login", json={"email": email, "password": password})
        if login.status_code >= 400:
            _json(
                client.post(
                    "/v1/auth/register",
                    json={
                        "email": email,
                        "password": password,
                        "name": "You",
                        "organization_name": "Acme",
                    },
                )
            )

        base_kb = _json(client.post("/v1/knowledge-bases", json={"name": "Smoke handbook"}))
        ingest = _json(
            client.post(
                f"/v1/knowledge-bases/{base_kb['id']}/documents",
                json={
                    "title": "Gateway",
                    "content": "Every model call goes through the Janus gateway.",
                },
            )
        )
        hits = _json(
            client.post(
                f"/v1/knowledge-bases/{base_kb['id']}/search",
                json={"query": "gateway", "limit": 2},
            )
        )
        agent = _json(
            client.post(
                "/v1/agents",
                json={
                    "name": "Smoke agent",
                    "slug": "smoke-agent",
                    "knowledge_base_ids": [base_kb["id"]],
                    "tools": ["knowledge_search"],
                },
            )
        )
        run = _json(
            client.post(
                f"/v1/agents/{agent['id']}/runs",
                json={"input": "Where do model calls go?"},
            )
        )
        response = _json(client.post("/v1/responses", json={"input": "Hello from smoke."}))
        usage = _json(client.get("/v1/usage"))

        print(f"api            {base}")
        print(f"knowledge      {base_kb['id']} chunks={ingest.get('chunk_count')}")
        print(f"search hits    {len(hits.get('data') or [])}")
        print(f"agent run      {run.get('status')} steps={run.get('step_count')}")
        print(f"citations      {len(run.get('citations') or [])}")
        print(f"responses      {response.get('status')}")
        print(f"usage reqs     {usage.get('requests')}")
        if run.get("status") != "completed" or not hits.get("data"):
            sys.exit("smoke_product: expected a completed run and at least one search hit")


if __name__ == "__main__":
    main()
