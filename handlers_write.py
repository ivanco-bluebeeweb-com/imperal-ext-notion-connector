"""Write tools: create/update pages, database rows, move, trash, comment,
create database (product spec section 6).

Two things shape this file:

* Since 2025-09-03 a database ROW's parent is a `data_source_id`, not a
  `database_id`. `_parent_payload` is the single place that decides which
  shape to send, so the distinction cannot drift between tools.
* Property VALUES must match the target's property TYPES. The user says
  {'Status': 'Done'}; `_build_properties` looks the real types up from the
  data source schema and converts. Unwritable computed properties (formula,
  rollup) are reported back rather than silently dropped.
"""

from __future__ import annotations

from imperal_sdk import ActionResult

import notion_client as nc
import notion_objects as no
import workspaces as ws
from app import chat
import shared
from shared import SHARING_NOTE

_error = shared.error
_from_envelope = shared.from_envelope
_resolve = shared.resolve
from models import (
    AddCommentParams,
    CreateDatabaseParams,
    CreatePageParams,
    MovePageParams,
    TrashPageParams,
    UpdatePageContentParams,
    UpdatePageParams,
    WriteResult,
)

# Column types accepted when creating a database. Anything else is rejected up
# front with the list, instead of letting Notion answer with a schema error.
_CREATABLE_COLUMN_TYPES = {
    "title", "rich_text", "number", "select", "multi_select", "status", "date",
    "people", "files", "checkbox", "url", "email", "phone_number",
}


async def _data_source_schema(ctx, token: str, data_source_id: str) -> dict:
    """Fetch {property_name: type} for a data source."""
    out = await nc.request(ctx, "GET", f"data_sources/{data_source_id}", token)
    if not out.get("ok"):
        return out
    props = out["data"].get("properties")
    schema = {}
    if isinstance(props, dict):
        for name, spec in props.items():
            if isinstance(spec, dict):
                schema[name] = str(spec.get("type") or "rich_text")
    return {"ok": True, "schema": schema}


def _build_properties(values: dict, schema: dict) -> tuple[dict, list[str]]:
    """Convert plain user values into Notion property payloads.

    Returns (payload, skipped). Property names are matched case-insensitively:
    a user typing 'status' should hit the column named 'Status'.
    """
    payload: dict = {}
    skipped: list[str] = []
    lowered = {name.lower(): name for name in schema}

    for raw_name, value in (values or {}).items():
        actual = lowered.get(str(raw_name).strip().lower())
        if not actual:
            skipped.append(f"{raw_name} (no such property)")
            continue
        built = no.build_property_value(schema[actual], value)
        if built is None:
            skipped.append(f"{raw_name} ({schema[actual]} is computed by Notion)")
            continue
        payload[actual] = built
    return payload, skipped


def _title_property(schema: dict) -> str:
    """Name of the title column -- user-defined, so it is found by TYPE."""
    for name, kind in schema.items():
        if kind == "title":
            return name
    return "Name"


async def _parent_payload(ctx, token: str, reference: str) -> dict:
    """Resolve a parent reference into the right Notion parent object.

    A database target must become `data_source_id` (2025-09-03+), so this
    resolves the reference, works out what it is, and returns both the parent
    payload and the schema when the parent is a database.
    """
    target = await ws.resolve_target(ctx, token, reference)
    if not target.get("ok"):
        return target

    target_id = target["id"]
    kind = target.get("object") or ""

    # A pasted id carries no type; ask Notion what it is rather than guessing.
    if not kind or target.get("resolved_by") == "id":
        probe = await nc.request(ctx, "GET", f"data_sources/{target_id}", token)
        if probe.get("ok"):
            kind = "data_source"
        else:
            page_probe = await nc.request(ctx, "GET", f"pages/{target_id}", token)
            if not page_probe.get("ok"):
                return page_probe
            kind = "page"

    if kind in ("data_source", "database"):
        # A database CONTAINER id cannot parent a row; its data source can.
        if kind == "database":
            container = await nc.request(ctx, "GET", f"databases/{target_id}", token)
            if not container.get("ok"):
                return container
            sources = container["data"].get("data_sources")
            if not isinstance(sources, list) or not sources:
                return nc.fail(nc.NOTION_NO_DATA_SOURCE)
            target_id = str(sources[0].get("id") or "")

        schema_out = await _data_source_schema(ctx, token, target_id)
        if not schema_out.get("ok"):
            return schema_out
        return {"ok": True, "parent": {"data_source_id": target_id},
                "kind": "data_source", "schema": schema_out["schema"],
                "title": target.get("title", "")}

    return {"ok": True, "parent": {"page_id": target_id}, "kind": "page",
            "schema": {}, "title": target.get("title", "")}


@chat.function(
    "create_page",
    "Create a Notion page, either inside another page or as a row in a database.",
    action_type="write", chain_callable=True, effects=["notion.page.created"],
    data_model=WriteResult,
    event="notion-connector.create_page",
)
async def create_page(ctx, params: CreatePageParams) -> ActionResult:
    """Create a page inside another page, or a row inside a database."""
    token, _workspace, err = await _resolve(ctx, params.workspace)
    if err:
        return err

    skipped: list[str] = []
    if params.parent.strip():
        resolved = await _parent_payload(ctx, token, params.parent)
        if not resolved.get("ok"):
            return _from_envelope(resolved)
        parent = resolved["parent"]
        schema = resolved["schema"]
    else:
        # No parent given: a page must live somewhere, so the first shared page
        # is used and named in the summary rather than failing outright.
        search = await nc.paginate(ctx, "POST", "search", token,
                                   json={"filter": {"property": "object",
                                                    "value": "page"}},
                                   limit=1, max_pages=1)
        if not search.get("ok"):
            return _from_envelope(search)
        if not search["results"]:
            return _error(
                "There is no page this integration can create content in. "
                f"{SHARING_NOTE}", nc.NOTION_NOT_SHARED)
        parent = {"page_id": str(search["results"][0].get("id") or "")}
        schema = {}

    if schema:
        properties, skipped = _build_properties(params.properties, schema)
        properties[_title_property(schema)] = no.build_property_value(
            "title", params.title)
    else:
        # A page parented by another PAGE has exactly one property: its title.
        # build_property_value returns {"title": [...]}, which is the VALUE, so
        # it is stored under the "title" key Notion expects for page parents.
        properties = {"title": no.build_property_value("title", params.title)}

    body: dict = {"parent": parent, "properties": properties}
    if params.content.strip():
        body["children"] = no.text_to_blocks(params.content)

    out = await nc.request(ctx, "POST", "pages", token, json=body)
    if not out.get("ok"):
        return _from_envelope(out)

    page = out["data"]
    detail = f" Skipped: {', '.join(skipped)}." if skipped else ""
    result = WriteResult(
        id=str(page.get("id") or ""),
        title=params.title,
        notion_id=str(page.get("id") or ""),
        url=str(page.get("url") or ""),
        action="created",
        detail=detail.strip(),
    )
    return ActionResult.success(result, f"Created '{params.title}' in Notion.{detail}")


@chat.function(
    "update_page_content",
    "Add content to an existing Notion page.",
    action_type="write", chain_callable=True, effects=["notion.page.updated"],
    data_model=WriteResult,
    event="notion-connector.update_page_content",
)
async def update_page_content(ctx, params: UpdatePageContentParams) -> ActionResult:
    """Append block content to the end of an existing page."""
    token, _workspace, err = await _resolve(ctx, params.workspace)
    if err:
        return err

    target = await ws.resolve_target(ctx, token, params.page, kind="page")
    if not target.get("ok"):
        return _from_envelope(target)

    blocks = no.text_to_blocks(params.content)
    if not blocks:
        return _error("There is no text to add to the page.",
                      nc.NOTION_VALIDATION_FAILED)

    body: dict = {"children": blocks}
    # 2026-03-11 replaced the flat `after` parameter with a position object.
    if params.position.strip().lower() == "start":
        body["position"] = {"type": "start"}

    out = await nc.request(ctx, "PATCH", f"blocks/{target['id']}/children", token,
                           json=body)
    if not out.get("ok"):
        return _from_envelope(out)

    label = target.get("title") or params.page
    result = WriteResult(
        id=target["id"], title=label, notion_id=target["id"],
        action="content added", detail=f"{len(blocks)} block(s)",
    )
    return ActionResult.success(result, f"Added {len(blocks)} block(s) to '{label}'.")


@chat.function(
    "update_page",
    "Update a Notion page's title or its database property values.",
    action_type="write", chain_callable=True, effects=["notion.page.updated"],
    data_model=WriteResult,
    event="notion-connector.update_page",
)
async def update_page(ctx, params: UpdatePageParams) -> ActionResult:
    """Update property values on an existing page or database row."""
    token, _workspace, err = await _resolve(ctx, params.workspace)
    if err:
        return err

    target = await ws.resolve_target(ctx, token, params.page, kind="page")
    if not target.get("ok"):
        return _from_envelope(target)

    current = await nc.request(ctx, "GET", f"pages/{target['id']}", token)
    if not current.get("ok"):
        return _from_envelope(current)
    page = current["data"]

    schema = {}
    props = page.get("properties")
    if isinstance(props, dict):
        for name, spec in props.items():
            if isinstance(spec, dict):
                schema[name] = str(spec.get("type") or "rich_text")

    payload: dict = {}
    skipped: list[str] = []
    if params.properties:
        payload, skipped = _build_properties(params.properties, schema)
    if params.title.strip():
        payload[_title_property(schema)] = no.build_property_value(
            "title", params.title.strip())

    if not payload:
        detail = f" Skipped: {', '.join(skipped)}." if skipped else ""
        return _error(f"Nothing to update on this page.{detail}",
                      nc.NOTION_VALIDATION_FAILED)

    out = await nc.request(ctx, "PATCH", f"pages/{target['id']}", token,
                           json={"properties": payload})
    if not out.get("ok"):
        return _from_envelope(out)

    label = params.title.strip() or target.get("title") or params.page
    detail = f" Skipped: {', '.join(skipped)}." if skipped else ""
    result = WriteResult(
        id=target["id"], title=label, notion_id=target["id"],
        url=str(out["data"].get("url") or ""), action="updated",
        detail=", ".join(payload.keys()),
    )
    return ActionResult.success(
        result, f"Updated {len(payload)} field(s) on '{label}'.{detail}")


@chat.function(
    "move_page",
    "Move a Notion page under a different parent page or database.",
    action_type="write", chain_callable=True, effects=["notion.page.moved"],
    data_model=WriteResult,
    event="notion-connector.move_page",
)
async def move_page(ctx, params: MovePageParams) -> ActionResult:
    """Move a page under a different parent page."""
    token, _workspace, err = await _resolve(ctx, params.workspace)
    if err:
        return err

    target = await ws.resolve_target(ctx, token, params.page, kind="page")
    if not target.get("ok"):
        return _from_envelope(target)

    destination = await _parent_payload(ctx, token, params.new_parent)
    if not destination.get("ok"):
        return _from_envelope(destination)

    out = await nc.request(ctx, "PATCH", f"pages/{target['id']}", token,
                           json={"parent": destination["parent"]})
    if not out.get("ok"):
        return _from_envelope(out)

    label = target.get("title") or params.page
    where = destination.get("title") or params.new_parent
    result = WriteResult(
        id=target["id"], title=label, notion_id=target["id"],
        url=str(out["data"].get("url") or ""), action="moved",
        detail=f"now under {where}",
    )
    return ActionResult.success(result, f"Moved '{label}' under '{where}'.")


@chat.function(
    "trash_page",
    "Move a Notion page to the trash, or restore it back out.",
    action_type="write", chain_callable=True, effects=["notion.page.trashed"],
    data_model=WriteResult,
    event="notion-connector.trash_page",
)
async def trash_page(ctx, params: TrashPageParams) -> ActionResult:
    """Move a page to Notion's trash, where the user can restore it."""
    token, _workspace, err = await _resolve(ctx, params.workspace)
    if err:
        return err

    target = await ws.resolve_target(ctx, token, params.page, kind="page")
    if not target.get("ok"):
        return _from_envelope(target)

    # 2026-03-11 renamed `archived` to `in_trash`.
    out = await nc.request(ctx, "PATCH", f"pages/{target['id']}", token,
                           json={"in_trash": not params.restore})
    if not out.get("ok"):
        return _from_envelope(out)

    label = target.get("title") or params.page
    action = "restored" if params.restore else "moved to trash"
    result = WriteResult(
        id=target["id"], title=label, notion_id=target["id"],
        url=str(out["data"].get("url") or ""), action=action,
        detail="recoverable from Notion's trash" if not params.restore else "",
    )
    summary = (f"Restored '{label}' from the trash."
               if params.restore
               else f"Moved '{label}' to Notion's trash -- it stays recoverable there.")
    return ActionResult.success(result, summary)


@chat.function(
    "add_comment",
    "Add a comment to a Notion page.",
    action_type="write", chain_callable=True, effects=["notion.comment.created"],
    data_model=WriteResult,
    event="notion-connector.add_comment",
)
async def add_comment(ctx, params: AddCommentParams) -> ActionResult:
    """Add a comment to a page or reply in an existing discussion."""
    token, _workspace, err = await _resolve(ctx, params.workspace)
    if err:
        return err

    target = await ws.resolve_target(ctx, token, params.page, kind="page")
    if not target.get("ok"):
        return _from_envelope(target)

    body = {
        "parent": {"page_id": target["id"]},
        "rich_text": [{"type": "text", "text": {"content": params.comment}}],
    }
    out = await nc.request(ctx, "POST", "comments", token, json=body)
    if not out.get("ok"):
        return _from_envelope(out)

    label = target.get("title") or params.page
    result = WriteResult(
        id=str(out["data"].get("id") or ""), title=label,
        notion_id=str(out["data"].get("id") or ""), action="commented",
        detail=params.comment[:120],
    )
    return ActionResult.success(result, f"Added a comment to '{label}'.")


@chat.function(
    "create_database",
    "Create a Notion database inside a page, with the columns you name.",
    action_type="write", chain_callable=True, effects=["notion.database.created"],
    data_model=WriteResult,
    event="notion-connector.create_database",
)
async def create_database(ctx, params: CreateDatabaseParams) -> ActionResult:
    """Create a database inside a page with the named columns."""
    token, _workspace, err = await _resolve(ctx, params.workspace)
    if err:
        return err

    parent = await ws.resolve_target(ctx, token, params.parent, kind="page")
    if not parent.get("ok"):
        return _from_envelope(parent)

    columns: dict = {}
    rejected: list[str] = []
    for name, kind in (params.properties or {}).items():
        wanted = str(kind).strip().lower()
        if wanted == "title":
            continue  # the title column is added below, exactly once
        if wanted not in _CREATABLE_COLUMN_TYPES:
            rejected.append(f"{name} ({kind})")
            continue
        columns[str(name)] = {wanted: {}}

    if rejected:
        allowed = ", ".join(sorted(_CREATABLE_COLUMN_TYPES - {"title"}))
        return _error(
            f"These column types aren't supported: {', '.join(rejected)}. "
            f"Supported types: {allowed}.", nc.NOTION_VALIDATION_FAILED)

    # Every Notion database needs exactly one title column.
    columns["Name"] = {"title": {}}

    body = {
        "parent": {"page_id": parent["id"]},
        "title": [{"type": "text", "text": {"content": params.title}}],
        "initial_data_source": {"properties": columns},
    }
    out = await nc.request(ctx, "POST", "databases", token, json=body)
    if not out.get("ok"):
        return _from_envelope(out)

    database = out["data"]
    where = parent.get("title") or params.parent
    result = WriteResult(
        id=str(database.get("id") or ""), title=params.title,
        notion_id=str(database.get("id") or ""),
        url=str(database.get("url") or ""), action="database created",
        detail=f"{len(columns)} column(s) in {where}",
    )
    return ActionResult.success(
        result, f"Created database '{params.title}' with {len(columns)} column(s).")
