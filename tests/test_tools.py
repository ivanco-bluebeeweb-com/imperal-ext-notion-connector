"""End-to-end tool behaviour through the real handlers.

These assert the WIRE SHAPES that changed in recent Notion versions — a row's
parent is a data_source_id, trashing sends in_trash — because getting those
wrong fails in ways that are hard to read at runtime.
"""

import handlers_read as hr
import handlers_write as hw
import notion_client as nc
from conftest import (bot_payload_default, data_source_payload, list_payload,
                      page_payload)
from models import (AddCommentParams, BrowseParams, CheckAccessParams,
                    CreateDatabaseParams, MovePageParams,
                    CreatePageParams, ListWorkspacesParams, ReadPageParams,
                    SearchParams, TrashPageParams, UpdatePageParams)


# --- reading ----------------------------------------------------------------

async def test_search_returns_flat_titles_not_nested_json(connected_ctx, http):
    http.push(bot_payload_default())
    http.push(list_payload([page_payload(title="Q3 Roadmap")]))
    res = await hr.search(connected_ctx, SearchParams(query="Q3"))
    assert res.status == "success"
    assert res.data.items[0].title == "Q3 Roadmap"


async def test_search_uses_the_current_object_filter_values(connected_ctx, http):
    """Since 2025-09-03 the filter value is 'data_source', not 'database'."""
    http.push(bot_payload_default())
    http.push(list_payload([]))
    await hr.search(connected_ctx, SearchParams(query="x", kind="database"))
    body = http.calls[-1]["json"]
    assert body["filter"] == {"property": "object", "value": "data_source"}


async def test_reading_a_page_includes_actual_block_content(connected_ctx, http):
    """Spec section 5: metadata alone is not 'reading a page'."""
    http.push(bot_payload_default())
    http.push(list_payload([page_payload(title="Q3 Roadmap")]))
    http.push(page_payload(title="Q3 Roadmap"))
    http.push(list_payload([
        {"object": "block", "id": "b1", "type": "heading_1", "has_children": False,
         "heading_1": {"rich_text": [{"plain_text": "Goals"}]}},
        {"object": "block", "id": "b2", "type": "paragraph", "has_children": False,
         "paragraph": {"rich_text": [{"plain_text": "Ship the connector."}]}},
    ]))
    res = await hr.read_page(connected_ctx, ReadPageParams(page="Q3 Roadmap"))
    assert res.status == "success"
    assert "Goals" in res.data.content
    assert "Ship the connector." in res.data.content


async def test_empty_results_explain_sharing_instead_of_looking_broken(connected_ctx, http):
    http.push(bot_payload_default())
    http.push(list_payload([]))
    res = await hr.browse(connected_ctx, BrowseParams())
    assert res.status == "success"
    assert "shared" in res.summary.lower()


async def test_check_access_reports_what_is_reachable(connected_ctx, http):
    http.push(bot_payload_default())
    http.push(list_payload([page_payload(), data_source_payload()]))
    res = await hr.check_access(connected_ctx, CheckAccessParams())
    assert res.status == "success"
    assert res.data.pages_visible >= 1


# --- failures carry codes ---------------------------------------------------

async def test_missing_token_is_a_structured_error_not_a_crash(ctx):
    res = await hr.list_workspaces(ctx, ListWorkspacesParams())
    assert res.status == "error"
    assert res.error_code == nc.NOTION_TOKEN_MISSING


async def test_a_404_becomes_the_not_shared_explanation(connected_ctx, http):
    http.push(bot_payload_default())
    http.push({"code": "object_not_found", "message": "..."}, 404)
    res = await hr.search(connected_ctx, SearchParams(query="x"))
    assert res.status == "error"
    assert res.error_code == nc.NOTION_NOT_SHARED


async def test_no_error_message_ever_contains_the_token(connected_ctx, http):
    """A leaked token in prose would end up in logs and chat history."""
    http.push(bot_payload_default())
    http.push({"code": "unauthorized", "message": "bad token"}, 401)
    res = await hr.search(connected_ctx, SearchParams(query="x"))
    assert res.status == "error"
    assert "ntn_test_token_one" not in (res.error or "")


# --- writing ----------------------------------------------------------------

async def test_a_row_is_parented_by_data_source_id_not_database_id(connected_ctx, http):
    """The 2025-09-03 rule: rows hang off a data source."""
    http.push(bot_payload_default())
    http.push(list_payload([data_source_payload(title="Tasks")]))   # resolve target
    http.push(data_source_payload(title="Tasks"))                    # schema
    http.push(page_payload(title="New task"))                        # created
    res = await hw.create_page(connected_ctx, CreatePageParams(
        parent="Tasks", title="New task", properties={"Status": "Done"}))
    assert res.status == "success"
    body = http.calls[-1]["json"]
    assert "data_source_id" in body["parent"]
    assert "database_id" not in body["parent"]


async def test_property_values_are_converted_to_their_real_types(connected_ctx, http):
    http.push(bot_payload_default())
    http.push(list_payload([data_source_payload(title="Tasks")]))
    http.push(data_source_payload(title="Tasks"))
    http.push(page_payload())
    await hw.create_page(connected_ctx, CreatePageParams(
        parent="Tasks", title="T", properties={"Status": "Done", "Done": "yes"}))
    props = http.calls[-1]["json"]["properties"]
    assert props["Status"] == {"select": {"name": "Done"}}
    assert props["Done"] == {"checkbox": True}


async def test_computed_properties_are_reported_not_silently_dropped(connected_ctx, http):
    """'Score' is a formula — Notion cannot accept a value for it."""
    http.push(bot_payload_default())
    http.push(list_payload([data_source_payload(title="Tasks")]))
    http.push(data_source_payload(title="Tasks"))
    http.push(page_payload())
    res = await hw.create_page(connected_ctx, CreatePageParams(
        parent="Tasks", title="T", properties={"Score": 10}))
    assert res.status == "success"
    assert "Score" in res.summary


async def test_trashing_uses_in_trash_not_the_removed_archived_field(connected_ctx, http):
    """`archived` was renamed to `in_trash` in 2026-03-11."""
    http.push(bot_payload_default())
    http.push(list_payload([page_payload(title="Old page")]))
    http.push(page_payload(title="Old page", in_trash=True))
    res = await hw.trash_page(connected_ctx, TrashPageParams(page="Old page"))
    assert res.status == "success"
    body = http.calls[-1]["json"]
    assert body == {"in_trash": True}


async def test_an_ambiguous_name_refuses_to_write_to_a_guess(connected_ctx, http):
    """Two 'Roadmap' pages: writing to the wrong one is unrecoverable."""
    http.push(bot_payload_default())
    http.push(list_payload([page_payload(page_id="a" * 32, title="Roadmap"),
                            page_payload(page_id="b" * 32, title="Roadmap")]))
    res = await hw.update_page(connected_ctx, UpdatePageParams(
        page="Roadmap", properties={"Status": "Done"}))
    assert res.status == "error"
    assert res.error_code == nc.NOTION_TARGET_AMBIGUOUS


async def test_a_comment_targets_the_resolved_page(connected_ctx, http):
    http.push(bot_payload_default())
    http.push(list_payload([page_payload(page_id="c" * 32, title="Q3 Roadmap")]))
    http.push({"object": "comment", "id": "cm-1",
               "rich_text": [{"plain_text": "Nice"}]})
    res = await hw.add_comment(connected_ctx, AddCommentParams(page="Q3 Roadmap", comment="Nice"))
    assert res.status == "success"
    body = http.calls[-1]["json"]
    assert body["parent"]["page_id"] == "c" * 32


# --- parent payloads --------------------------------------------------------
#
# Notion requires the `type` discriminator on every parent. /v1/pages tolerates
# its absence (it infers the type from the single id key) but /v1/databases
# rejects the body: "body.parent.type should be defined". That asymmetry let a
# hand-built parent pass every existing test and still fail in production, so
# the shape is asserted here for EVERY write path that sends one.

async def test_creating_a_database_sends_a_typed_parent(connected_ctx, http):
    """The bug: a parent without `type` makes /v1/databases reject the body."""
    http.push(bot_payload_default())
    http.push(list_payload([page_payload(page_id="d" * 32, title="Summary")]))
    http.push({"object": "database", "id": "db-1",
               "url": "https://www.notion.so/db-1"})
    res = await hw.create_database(connected_ctx, CreateDatabaseParams(
        title="Sermons", parent="Summary", properties={"Date": "date"}))
    assert res.status == "success"
    parent = http.calls[-1]["json"]["parent"]
    assert parent == {"type": "page_id", "page_id": "d" * 32}


async def test_creating_a_database_always_has_exactly_one_title_column(connected_ctx, http):
    http.push(bot_payload_default())
    http.push(list_payload([page_payload(title="Summary")]))
    http.push({"object": "database", "id": "db-1", "url": ""})
    res = await hw.create_database(connected_ctx, CreateDatabaseParams(
        title="Sermons", parent="Summary",
        properties={"Date": "date", "Topic": "select"}))
    assert res.status == "success"
    columns = http.calls[-1]["json"]["initial_data_source"]["properties"]
    titles = [n for n, spec in columns.items() if "title" in spec]
    assert len(titles) == 1


async def test_an_unsupported_column_type_is_refused_with_a_code(connected_ctx, http):
    http.push(bot_payload_default())
    http.push(list_payload([page_payload(title="Summary")]))
    res = await hw.create_database(connected_ctx, CreateDatabaseParams(
        title="Sermons", parent="Summary", properties={"Score": "formula"}))
    assert res.status == "error"
    assert res.error_code == nc.NOTION_VALIDATION_FAILED


async def test_a_row_parent_is_a_typed_data_source(connected_ctx, http):
    http.push(bot_payload_default())
    http.push(list_payload([data_source_payload(title="Tasks")]))
    http.push(data_source_payload(title="Tasks"))
    http.push(page_payload())
    await hw.create_page(connected_ctx, CreatePageParams(
        parent="Tasks", title="New task"))
    parent = http.calls[-1]["json"]["parent"]
    assert parent["type"] == "data_source_id"
    assert parent["data_source_id"] == "2" * 32


async def test_a_comment_parent_is_typed_too(connected_ctx, http):
    http.push(bot_payload_default())
    http.push(list_payload([page_payload(page_id="c" * 32, title="Q3 Roadmap")]))
    http.push({"object": "comment", "id": "cm-1", "rich_text": []})
    await hw.add_comment(connected_ctx,
                         AddCommentParams(page="Q3 Roadmap", comment="Nice"))
    assert http.calls[-1]["json"]["parent"]["type"] == "page_id"


async def test_moving_a_page_sends_a_typed_parent(connected_ctx, http):
    http.push(bot_payload_default())
    http.push(list_payload([page_payload(page_id="a" * 32, title="Note")]))
    http.push(list_payload([page_payload(page_id="b" * 32, title="Archive")]))
    http.push(page_payload(page_id="a" * 32, title="Note"))
    res = await hw.move_page(connected_ctx, MovePageParams(
        page="Note", new_parent="Archive"))
    assert res.status == "success"
    assert http.calls[-1]["json"]["parent"] == {
        "type": "page_id", "page_id": "b" * 32}
