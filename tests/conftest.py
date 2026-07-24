"""Shared fixtures.

MockHTTP from the SDK only registers GET/POST and returns the first pattern
match, which cannot express "PATCH this page" or "the same URL answers
differently on the second call" -- both of which the write tools do. So the
HTTP double here is queue-based: each test states the exact sequence of
responses it expects, and every request is recorded for assertions.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class FakeResponse:
    """Mirrors imperal_sdk HTTPResponse closely enough for notion_client."""

    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self.body = body
        self.headers: dict = {}

    def json(self):
        # Mirrors imperal_sdk HTTPResponse.json(): a str/bytes body is PARSED,
        # so invalid JSON raises — which is what drives the NOT_JSON path.
        if isinstance(self.body, (dict, list)):
            return self.body
        if isinstance(self.body, (str, bytes, bytearray)):
            import json as _json
            return _json.loads(self.body)
        raise ValueError(f"Cannot parse {type(self.body).__name__} body as JSON")

    def text(self) -> str:
        return self.body if isinstance(self.body, str) else str(self.body)

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


class QueueHTTP:
    """HTTP double: queue up responses, then inspect what was requested."""

    def __init__(self):
        self.queued: list = []
        self.calls: list[dict] = []

    def push(self, body, status: int = 200):
        """Queue one response (or an Exception instance to raise)."""
        self.queued.append((status, body))
        return self

    def _next(self, method: str, url: str, kwargs) -> FakeResponse:
        self.calls.append({
            "method": method,
            "url": url,
            "json": kwargs.get("json"),
            "params": kwargs.get("params"),
            "headers": kwargs.get("headers") or {},
        })
        if not self.queued:
            raise AssertionError(f"unexpected {method} {url} — no response queued")
        status, body = self.queued.pop(0)
        if isinstance(body, Exception):
            raise body
        return FakeResponse(status, body)

    async def get(self, url, **kw):
        return self._next("GET", url, kw)

    async def post(self, url, **kw):
        return self._next("POST", url, kw)

    async def patch(self, url, **kw):
        return self._next("PATCH", url, kw)

    async def put(self, url, **kw):
        return self._next("PUT", url, kw)

    async def delete(self, url, **kw):
        return self._next("DELETE", url, kw)

    # -- assertions helpers -------------------------------------------------
    def last_body(self) -> dict:
        return self.calls[-1]["json"] or {}

    def urls(self) -> list[str]:
        return [c["url"] for c in self.calls]


@pytest.fixture
def http():
    return QueueHTTP()


@pytest.fixture
def ctx(http):
    from imperal_sdk.testing import MockContext, MockSecretStore

    mock = MockContext()
    mock.secrets = MockSecretStore({})
    mock.http = http
    return mock


@pytest.fixture
def connected_ctx(ctx):
    """A ctx with one usable workspace token already configured."""
    from imperal_sdk.testing import MockSecretStore

    ctx.secrets = MockSecretStore({"notion_tokens": "ntn_test_token_one"})
    return ctx


# --- Notion payload builders ------------------------------------------------

def page_payload(page_id="11111111111111111111111111111111", title="Q3 Roadmap",
                 **extra) -> dict:
    payload = {
        "object": "page",
        "id": page_id,
        "url": f"https://www.notion.so/{page_id}",
        "in_trash": False,
        "created_time": "2026-07-01T10:00:00.000Z",
        "last_edited_time": "2026-07-20T12:00:00.000Z",
        "parent": {"type": "workspace", "workspace": True},
        "properties": {
            "Name": {
                "type": "title",
                "title": [{"plain_text": title, "text": {"content": title}}],
            }
        },
    }
    payload.update(extra)
    return payload


def data_source_payload(ds_id="22222222222222222222222222222222",
                        title="Tasks") -> dict:
    return {
        "object": "data_source",
        "id": ds_id,
        "title": [{"plain_text": title, "text": {"content": title}}],
        "properties": {
            "Name": {"type": "title", "title": {}},
            "Status": {"type": "select", "select": {}},
            "Done": {"type": "checkbox", "checkbox": {}},
            "Score": {"type": "formula", "formula": {}},
        },
    }


def list_payload(results: list, has_more: bool = False, cursor=None) -> dict:
    return {
        "object": "list",
        "results": results,
        "has_more": has_more,
        "next_cursor": cursor,
    }


def bot_payload_default(workspace: str = "Acme HQ") -> dict:
    """The `/v1/users/me` reply every tool makes first to identify the workspace."""
    return {
        "object": "user",
        "id": "bot-1",
        "name": "Imperal",
        "type": "bot",
        "bot": {
            "workspace_name": workspace,
            "workspace_id": "w-1",
            "owner": {"type": "workspace"},
        },
    }
