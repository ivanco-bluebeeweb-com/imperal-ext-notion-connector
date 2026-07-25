"""Helpers shared by the read and write tool layers.

These lived in `handlers_read.py`, which meant `handlers_write.py` imported
PRIVATE names from a sibling layer -- a dependency that says "write is built on
read" when the two are really peers. Both now depend on this instead.
"""

from __future__ import annotations

from imperal_sdk import ActionResult

import notion_client as nc
import notion_objects as no
import workspaces as ws
from models import NotionObject

# The one sentence that explains Notion's access model. Reused verbatim wherever
# emptiness might otherwise read as a bug -- an empty result here almost always
# means "not shared yet", not "nothing exists".
SHARING_NOTE = (
    "Notion only exposes what the integration was explicitly shared with. "
    "In Notion, open the page or database, click the three-dot menu, and use "
    "Connections to add this integration -- subpages inherit that access."
)


def error(message: str, code: str, retryable: bool = False) -> ActionResult:
    """Error result carrying a structured code.

    `code` is mandatory on purpose. The kernel stamps EXT_UNSTRUCTURED_ERROR on
    any error emitted without one (I-EXT-ERROR-CODE-NORMALIZED), which turns a
    precise failure into un-actionable prose. Validator rule V32 only flags
    literal `ActionResult.error(` call sites, so routing every error through a
    helper would hide this app from the rule -- hence the positional argument:
    a code-less error here is a TypeError at authoring time.
    """
    return ActionResult.error(message, retryable, code=code)


def from_envelope(out: dict) -> ActionResult:
    """Convert a notion_client error envelope into an ActionResult."""
    return error(out.get("error") or nc.message_for(out.get("code", "")),
                 out.get("code") or nc.NOTION_HTTP_ERROR,
                 bool(out.get("retryable")))


async def resolve(ctx, workspace: str) -> tuple[str, dict, ActionResult | None]:
    """Resolve the workspace token, or hand back a ready-made error."""
    picked = await ws.resolve_workspace(ctx, workspace)
    if not picked.get("ok"):
        return "", {}, from_envelope(picked)
    return picked["token"], picked.get("workspace", {}), None


async def resolve_data_source(ctx, token: str, reference: str) -> dict:
    """Resolve a database reference to a queryable DATA SOURCE id.

    Since 2025-09-03 a database is a *container* holding one or more data
    sources, and only a data source answers `/query`. A pasted id may be either
    one -- and `create_database` hands back the CONTAINER id, so the id a user
    copies straight out of a create result is exactly the case that must work.
    Guessing "data source" makes that id 404 as if it were not shared, so the
    container is probed first and unwrapped to its first data source.

    Returns {"ok": True, "id", "title"} or a notion_client error envelope.
    """
    target = await ws.resolve_target(ctx, token, reference)
    if not target.get("ok"):
        return target

    target_id = target["id"]
    title = target.get("title", "")

    if target.get("resolved_by") == "id" or not target.get("object"):
        probe = await nc.request(ctx, "GET", f"data_sources/{target_id}", token)
        if probe.get("ok"):
            return {"ok": True, "id": target_id,
                    "title": title or no.title_of(probe["data"])}
        container = await nc.request(ctx, "GET", f"databases/{target_id}", token)
        if not container.get("ok"):
            return container
        sources = container["data"].get("data_sources")
        if not isinstance(sources, list) or not sources:
            return nc.fail(nc.NOTION_NO_DATA_SOURCE)
        return {"ok": True, "id": str(sources[0].get("id") or ""),
                "title": title or no.title_of(container["data"])}

    if str(target.get("object")) == "database":
        container = await nc.request(ctx, "GET", f"databases/{target_id}", token)
        if not container.get("ok"):
            return container
        sources = container["data"].get("data_sources")
        if not isinstance(sources, list) or not sources:
            return nc.fail(nc.NOTION_NO_DATA_SOURCE)
        return {"ok": True, "id": str(sources[0].get("id") or ""), "title": title}

    return {"ok": True, "id": target_id, "title": title}


def object_entity(item: dict) -> NotionObject:
    """Map a raw search/children result onto the flat NotionObject entity."""
    kind, parent_id = no.parent_ref(item)
    raw_type = str(item.get("object") or "")
    # Since 2025-09-03 search returns `data_source` objects; users think
    # "database", so that is what they are shown.
    friendly = "database" if raw_type in ("database", "data_source") else raw_type
    return NotionObject(
        id=str(item.get("id") or ""),
        title=no.title_of(item),
        object_type=friendly,
        notion_id=str(item.get("id") or ""),
        parent_kind=kind,
        parent_id=parent_id,
        url=str(item.get("url") or ""),
        last_edited=str(item.get("last_edited_time") or ""),
        in_trash=bool(item.get("in_trash")),
    )
