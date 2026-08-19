"""Plausible Scenario Tests (PST) -- Notion Connector.

Method: Docs/session-notes/SCENARIO_TESTING_STANDARD.md. This app has 17
chat functions and 8 existing test files with deep coverage of search,
page reading, database creation, comments, rows, moves and trashing. A
name-based coverage audit found 4 functions never exercised by any
existing test:

    list_comments, list_databases, list_users, update_page_content

This file closes those 4 gaps.
"""
from __future__ import annotations

import pytest

import handlers_read as hr
import handlers_write as hw
import notion_client as nc
from models import (
    ListCommentsParams, ListDatabasesParams, ListUsersParams,
    UpdatePageContentParams,
)

from conftest import bot_payload_default, data_source_payload, list_payload, page_payload

pytestmark = pytest.mark.asyncio


# ── list_databases ──────────────────────────────────────────────────────────

async def test_happy_list_databases(connected_ctx, http):
    http.push(bot_payload_default())
    http.push(list_payload([data_source_payload(title="Tasks")]))
    res = await hr.list_databases(connected_ctx, ListDatabasesParams())
    assert res.status == "success"
    assert res.data.items[0].title == "Tasks"
    # search must be filtered to data_source objects, not every object type.
    assert http.calls[-1]["json"]["filter"] == {"property": "object", "value": "data_source"}


async def test_happy_list_databases_empty_explains_sharing(connected_ctx, http):
    http.push(bot_payload_default())
    http.push(list_payload([]))
    res = await hr.list_databases(connected_ctx, ListDatabasesParams())
    assert res.status == "success"
    assert res.data.total == 0
    assert "shared with this integration" in res.summary


async def test_blocked_list_databases_without_token(ctx):
    res = await hr.list_databases(ctx, ListDatabasesParams())
    assert res.status == "error"


# ── list_users ───────────────────────────────────────────────────────────────

async def test_happy_list_users(connected_ctx, http):
    http.push(bot_payload_default())
    http.push(list_payload([
        {"object": "user", "id": "u-1", "name": "Vlad", "type": "person",
         "person": {"email": "vlad@example.com"}},
    ]))
    res = await hr.list_users(connected_ctx, ListUsersParams())
    assert res.status == "success"
    assert res.data.items[0].email == "vlad@example.com"
    assert http.calls[-1]["url"].endswith("/users")


async def test_blocked_list_users_without_token(ctx):
    res = await hr.list_users(ctx, ListUsersParams())
    assert res.status == "error"


# ── list_comments ────────────────────────────────────────────────────────────

async def test_happy_list_comments_targets_the_resolved_page(connected_ctx, http):
    http.push(bot_payload_default())
    http.push(list_payload([page_payload(page_id="c" * 32, title="Q3 Roadmap")]))
    http.push(list_payload([
        {"id": "cm-1", "rich_text": [{"plain_text": "Looks good"}],
         "created_by": {"id": "u-1"}},
    ]))
    res = await hr.list_comments(connected_ctx, ListCommentsParams(page="Q3 Roadmap"))
    assert res.status == "success"
    assert res.data.items[0].title.startswith("Looks good")
    # comments must be scoped to the resolved page's block id, not sitewide.
    assert http.calls[-1]["params"]["block_id"] == "c" * 32


async def test_error_list_comments_target_not_found(connected_ctx, http):
    http.push(bot_payload_default())
    http.push(list_payload([]))  # search finds nothing
    res = await hr.list_comments(connected_ctx, ListCommentsParams(page="Nonexistent"))
    assert res.status == "error"


# ── update_page_content ─────────────────────────────────────────────────────

async def test_happy_update_page_content_appends_at_end(connected_ctx, http):
    http.push(bot_payload_default())
    http.push(list_payload([page_payload(page_id="a" * 32, title="Notes")]))
    http.push({"object": "list", "results": [{"id": "block-1"}]})
    res = await hw.update_page_content(
        connected_ctx, UpdatePageContentParams(page="Notes", content="New line"))
    assert res.status == "success"
    body = http.calls[-1]["json"]
    assert "position" not in body  # default 'end' sends no position override
    assert len(body["children"]) >= 1


async def test_happy_update_page_content_prepends_at_start(connected_ctx, http):
    http.push(bot_payload_default())
    http.push(list_payload([page_payload(page_id="b" * 32, title="Notes")]))
    http.push({"object": "list", "results": [{"id": "block-1"}]})
    res = await hw.update_page_content(
        connected_ctx, UpdatePageContentParams(page="Notes", content="New line", position="start"))
    assert res.status == "success"
    body = http.calls[-1]["json"]
    assert body["position"] == {"type": "start"}


async def test_error_update_page_content_with_empty_text(connected_ctx, http):
    http.push(bot_payload_default())
    http.push(list_payload([page_payload(page_id="e" * 32, title="Notes")]))
    res = await hw.update_page_content(
        connected_ctx, UpdatePageContentParams(page="Notes", content="   "))
    assert res.status == "error"
    assert res.error_code == nc.NOTION_VALIDATION_FAILED
