"""Read tools: workspaces, search, browse, page content, databases, users,
comments, access report.

The spec is "readable first" (section 9): reading has to be genuinely useful
before any write flow matters. So `read_page` returns ACTUAL BLOCK CONTENT
(section 5), and `check_access` exists purely to explain why something the user
can see in Notion is not visible here (section 3).
"""

from __future__ import annotations

from imperal_sdk import ActionResult

import notion_client as nc
import notion_objects as no
import workspaces as ws
from app import chat
from models import (
    AccessReport,
    BrowseParams,
    CheckAccessParams,
    CommentList,
    CommentRecord,
    DatabaseRow,
    DatabaseRowList,
    ListCommentsParams,
    ListDatabasesParams,
    ListUsersParams,
    ListWorkspacesParams,
    NotionObject,
    NotionObjectList,
    PageContent,
    QueryDatabaseParams,
    ReadPageParams,
    SearchParams,
    UserList,
    UserRecord,
    WorkspaceList,
    WorkspaceRecord,
)

# The one sentence that explains Notion's access model. Reused verbatim wherever
# emptiness might otherwise read as a bug -- an empty result here almost always
# means "not shared yet", not "nothing exists".
SHARING_NOTE = (
    "Notion only exposes what the integration was explicitly shared with. "
    "In Notion, open the page or database, click the three-dot menu, and use "
    "Connections to add this integration -- subpages inherit that access."
)


def _error(message: str, code: str, retryable: bool = False) -> ActionResult:
    """Error result carrying a structured code.

    `code` is mandatory on purpose. The kernel stamps EXT_UNSTRUCTURED_ERROR on
    any error emitted without one (I-EXT-ERROR-CODE-NORMALIZED), which turns a
    precise failure into un-actionable prose. Validator rule V32 only flags
    literal `ActionResult.error(` call sites, so routing every error through a
    helper would hide this app from the rule -- hence the positional argument:
    a code-less error here is a TypeError at authoring time.
    """
    return ActionResult.error(message, retryable, code=code)


def _from_envelope(out: dict) -> ActionResult:
    """Convert a notion_client error envelope into an ActionResult."""
    return _error(out.get("error") or nc.message_for(out.get("code", "")),
                  out.get("code") or nc.NOTION_HTTP_ERROR,
                  bool(out.get("retryable")))


async def _resolve(ctx, workspace: str) -> tuple[str, dict, ActionResult | None]:
    """Resolve the workspace token, or hand back a ready-made error."""
    picked = await ws.resolve_workspace(ctx, workspace)
    if not picked.get("ok"):
        return "", {}, _from_envelope(picked)
    return picked["token"], picked.get("workspace", {}), None


def _object_entity(item: dict) -> NotionObject:
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


@chat.function(
    "list_workspaces",
    "List the connected Notion workspaces and whether each token still works.",
    action_type="read", chain_callable=True,
    data_model=WorkspaceRecord,
)
async def list_workspaces(ctx, params: ListWorkspacesParams) -> ActionResult:
    """List connected Notion workspaces and verify each token still works."""
    entries = await ws.list_workspaces(ctx, refresh=params.refresh)
    if not entries:
        return _error(
            "No Notion integration token is configured yet. Create an integration "
            "at notion.so/my-integrations, share your pages with it, then paste "
            "the token into this app's Secrets tab.",
            nc.NOTION_TOKEN_MISSING)

    records = [
        WorkspaceRecord(
            id=str(entry.get("bot_id") or f"slot-{entry.get('slot', 0)}"),
            title=str(entry.get("workspace_name") or "Notion workspace"),
            workspace_name=str(entry.get("workspace_name") or ""),
            workspace_id=str(entry.get("workspace_id") or ""),
            integration_name=str(entry.get("integration_name") or ""),
            state="connected" if entry.get("status") == "ok" else "error",
            detail=str(entry.get("error") or ""),
        )
        for entry in entries
    ]
    working = sum(1 for r in records if r.state == "connected")
    summary = (f"{working} Notion workspace(s) connected"
               if working == len(records)
               else f"{working} of {len(records)} Notion workspace(s) usable")
    return ActionResult.success(WorkspaceList(items=records, total=len(records)),
                                summary)


@chat.function(
    "search",
    "Search pages and databases across a Notion workspace by title text.",
    action_type="read", chain_callable=True,
    data_model=NotionObject,
)
async def search(ctx, params: SearchParams) -> ActionResult:
    """Search pages and databases the integration can reach, by title text."""
    token, workspace, err = await _resolve(ctx, params.workspace)
    if err:
        return err

    payload: dict = {}
    if params.query.strip():
        payload["query"] = params.query.strip()
    if params.kind:
        wanted = params.kind.strip().lower()
        # "database" is the user's word; the API filter value is data_source.
        if wanted in ("database", "databases", "data_source"):
            payload["filter"] = {"property": "object", "value": "data_source"}
        elif wanted in ("page", "pages"):
            payload["filter"] = {"property": "object", "value": "page"}

    out = await nc.paginate(ctx, "POST", "search", token, json=payload,
                            limit=params.limit)
    if not out.get("ok"):
        return _from_envelope(out)

    items = [_object_entity(item) for item in out["results"]]
    if not items:
        detail = f" matching '{params.query}'" if params.query.strip() else ""
        return ActionResult.success(
            NotionObjectList(items=[], total=0),
            f"Nothing{detail} is visible to this integration. {SHARING_NOTE}")

    name = workspace.get("workspace_name") or "Notion"
    more = " (more available)" if out.get("has_more") else ""
    return ActionResult.success(
        NotionObjectList(items=items, total=len(items)),
        f"Found {len(items)} object(s) in {name}{more}")


@chat.function(
    "read_page",
    "Read a Notion page: its properties and its actual block content.",
    action_type="read", chain_callable=True,
    data_model=PageContent,
)
async def read_page(ctx, params: ReadPageParams) -> ActionResult:
    """Read one page: its properties plus its actual block content."""
    token, _workspace, err = await _resolve(ctx, params.workspace)
    if err:
        return err

    target = await ws.resolve_target(ctx, token, params.page, kind="page")
    if not target.get("ok"):
        return _from_envelope(target)

    page_id = target["id"]
    out = await nc.request(ctx, "GET", f"pages/{page_id}", token)
    if not out.get("ok"):
        return _from_envelope(out)
    page = out["data"]

    content = ""
    block_count = 0
    truncated = False
    if params.include_content:
        blocks = await nc.paginate(ctx, "GET", f"blocks/{page_id}/children", token,
                                   limit=params.max_blocks)
        if not blocks.get("ok"):
            return _from_envelope(blocks)
        raw_blocks = blocks["results"]
        block_count = len(raw_blocks)
        truncated = bool(blocks.get("has_more"))
        # One level of nesting is fetched: toggles and list items hold their
        # text in children, so skipping them loses real content. Deeper
        # recursion is left out on purpose -- it multiplies API calls without
        # much gain for a chat-sized read.
        for block in raw_blocks:
            if block.get("has_children") and block.get("type") not in ("child_page",
                                                                       "child_database"):
                kids = await nc.paginate(ctx, "GET", f"blocks/{block['id']}/children",
                                         token, limit=100, max_pages=1)
                if kids.get("ok"):
                    block["_children"] = kids["results"]
        content = no.blocks_to_text(raw_blocks)

    title = no.title_of(page)
    entity = PageContent(
        id=page_id,
        title=title,
        notion_id=page_id,
        url=str(page.get("url") or ""),
        content=content,
        block_count=block_count,
        truncated=truncated,
        properties=no.properties_to_plain(page.get("properties")),
        last_edited=str(page.get("last_edited_time") or ""),
        in_trash=bool(page.get("in_trash")),
    )
    note = " (content truncated)" if truncated else ""
    return ActionResult.success(entity, f"Read '{title}' -- {block_count} block(s){note}")


@chat.function(
    "browse",
    "Browse the workspace structure: top-level objects, or the children of a page.",
    action_type="read", chain_callable=True,
    data_model=NotionObject,
)
async def browse(ctx, params: BrowseParams) -> ActionResult:
    """List top-level shared objects, or the children of one page."""
    token, workspace, err = await _resolve(ctx, params.workspace)
    if err:
        return err

    if not params.parent.strip():
        out = await nc.paginate(ctx, "POST", "search", token, json={},
                                limit=params.limit)
        if not out.get("ok"):
            return _from_envelope(out)
        # Top level = everything whose parent is the workspace itself. If
        # nothing qualifies (only subpages were shared), showing the shared set
        # is more useful than an empty list.
        roots = [i for i in out["results"] if no.parent_ref(i)[0] == "workspace"]
        chosen = roots or out["results"]
        items = [_object_entity(i) for i in chosen]
        if not items:
            return ActionResult.success(
                NotionObjectList(items=[], total=0),
                f"Nothing is shared with this integration yet. {SHARING_NOTE}")
        name = workspace.get("workspace_name") or "Notion"
        label = "top-level" if roots else "shared"
        return ActionResult.success(
            NotionObjectList(items=items, total=len(items)),
            f"{len(items)} {label} object(s) in {name}")

    target = await ws.resolve_target(ctx, token, params.parent)
    if not target.get("ok"):
        return _from_envelope(target)

    out = await nc.paginate(ctx, "GET", f"blocks/{target['id']}/children", token,
                            limit=params.limit)
    if not out.get("ok"):
        return _from_envelope(out)

    items: list[NotionObject] = []
    for block in out["results"]:
        kind = str(block.get("type") or "")
        if kind not in ("child_page", "child_database"):
            continue
        payload = block.get(kind) or {}
        items.append(NotionObject(
            id=str(block.get("id") or ""),
            title=str(payload.get("title") or "Untitled"),
            object_type="page" if kind == "child_page" else "database",
            notion_id=str(block.get("id") or ""),
            parent_kind="page",
            parent_id=target["id"],
        ))

    label = target.get("title") or params.parent
    if not items:
        return ActionResult.success(
            NotionObjectList(items=[], total=0),
            f"'{label}' has no subpages or databases inside it.")
    return ActionResult.success(NotionObjectList(items=items, total=len(items)),
                                f"{len(items)} child object(s) inside '{label}'")


@chat.function(
    "list_databases",
    "List the Notion databases the integration can reach.",
    action_type="read", chain_callable=True,
    data_model=NotionObject,
)
async def list_databases(ctx, params: ListDatabasesParams) -> ActionResult:
    """List databases shared with this integration."""
    token, workspace, err = await _resolve(ctx, params.workspace)
    if err:
        return err

    payload: dict = {"filter": {"property": "object", "value": "data_source"}}
    if params.query.strip():
        payload["query"] = params.query.strip()

    out = await nc.paginate(ctx, "POST", "search", token, json=payload,
                            limit=params.limit)
    if not out.get("ok"):
        return _from_envelope(out)

    items = [_object_entity(item) for item in out["results"]]
    if not items:
        return ActionResult.success(
            NotionObjectList(items=[], total=0),
            f"No databases are shared with this integration. {SHARING_NOTE}")
    name = workspace.get("workspace_name") or "Notion"
    return ActionResult.success(NotionObjectList(items=items, total=len(items)),
                                f"{len(items)} database(s) in {name}")


@chat.function(
    "query_database",
    "Read the rows of a Notion database, with their property values.",
    action_type="read", chain_callable=True,
    data_model=DatabaseRow,
)
async def query_database(ctx, params: QueryDatabaseParams) -> ActionResult:
    """Query rows of one database, newest first unless filtered."""
    token, _workspace, err = await _resolve(ctx, params.workspace)
    if err:
        return err

    target = await ws.resolve_target(ctx, token, params.database, kind="data_source")
    if not target.get("ok"):
        return _from_envelope(target)

    payload: dict = {}
    if params.sort_by.strip():
        payload["sorts"] = [{
            "property": params.sort_by.strip(),
            "direction": "descending" if params.descending else "ascending",
        }]

    out = await nc.paginate(ctx, "POST", f"data_sources/{target['id']}/query",
                            token, json=payload, limit=params.limit)
    if not out.get("ok"):
        return _from_envelope(out)

    rows = [
        DatabaseRow(
            id=str(row.get("id") or ""),
            title=no.title_of(row),
            notion_id=str(row.get("id") or ""),
            url=str(row.get("url") or ""),
            values=no.properties_to_plain(row.get("properties")),
            last_edited=str(row.get("last_edited_time") or ""),
        )
        for row in out["results"]
    ]
    label = target.get("title") or params.database
    if not rows:
        return ActionResult.success(DatabaseRowList(items=[], total=0),
                                    f"'{label}' has no rows.")
    more = " (more available)" if out.get("has_more") else ""
    return ActionResult.success(DatabaseRowList(items=rows, total=len(rows)),
                                f"{len(rows)} row(s) from '{label}'{more}")


@chat.function(
    "list_users",
    "List the people and bots in a connected Notion workspace.",
    action_type="read", chain_callable=True,
    data_model=UserRecord,
)
async def list_users(ctx, params: ListUsersParams) -> ActionResult:
    """List workspace members visible to this integration."""
    token, workspace, err = await _resolve(ctx, params.workspace)
    if err:
        return err

    out = await nc.paginate(ctx, "GET", "users", token, limit=params.limit)
    if not out.get("ok"):
        return _from_envelope(out)

    people = [
        UserRecord(
            id=str(user.get("id") or ""),
            title=str(user.get("name") or "Unnamed"),
            notion_id=str(user.get("id") or ""),
            user_type=str(user.get("type") or ""),
            email=str((user.get("person") or {}).get("email") or ""),
        )
        for user in out["results"]
    ]
    name = workspace.get("workspace_name") or "Notion"
    return ActionResult.success(UserList(items=people, total=len(people)),
                                f"{len(people)} user(s) in {name}")


@chat.function(
    "list_comments",
    "Read the comments on a Notion page.",
    action_type="read", chain_callable=True,
    data_model=CommentRecord,
)
async def list_comments(ctx, params: ListCommentsParams) -> ActionResult:
    """List unresolved comments on one page or block."""
    token, _workspace, err = await _resolve(ctx, params.workspace)
    if err:
        return err

    target = await ws.resolve_target(ctx, token, params.page, kind="page")
    if not target.get("ok"):
        return _from_envelope(target)

    out = await nc.paginate(ctx, "GET", "comments", token,
                            params={"block_id": target["id"]}, limit=params.limit)
    if not out.get("ok"):
        return _from_envelope(out)

    comments = [
        CommentRecord(
            id=str(c.get("id") or ""),
            title=no.rich_text_to_plain(c.get("rich_text"))[:80] or "Comment",
            notion_id=str(c.get("id") or ""),
            author=str((c.get("created_by") or {}).get("id") or ""),
            text=no.rich_text_to_plain(c.get("rich_text")),
            created=str(c.get("created_time") or ""),
        )
        for c in out["results"]
    ]
    label = target.get("title") or params.page
    if not comments:
        return ActionResult.success(CommentList(items=[], total=0),
                                    f"'{label}' has no comments.")
    return ActionResult.success(CommentList(items=comments, total=len(comments)),
                                f"{len(comments)} comment(s) on '{label}'")


@chat.function(
    "check_access",
    "Report what this integration can currently reach in Notion, and explain "
    "why anything missing is not visible.",
    action_type="read", chain_callable=True,
    data_model=AccessReport,
)
async def check_access(ctx, params: CheckAccessParams) -> ActionResult:
    """Report what the integration can reach, and explain what it cannot."""
    token, workspace, err = await _resolve(ctx, params.workspace)
    if err:
        return err

    out = await nc.paginate(ctx, "POST", "search", token, json={}, limit=100,
                            max_pages=3)
    if not out.get("ok"):
        return _from_envelope(out)

    results = out["results"]
    pages = sum(1 for i in results if i.get("object") == "page")
    databases = sum(1 for i in results
                    if i.get("object") in ("data_source", "database"))
    name = workspace.get("workspace_name") or "Notion"

    if not results:
        explanation = (
            "This integration is connected but has not been shared with any "
            f"content yet. {SHARING_NOTE}")
    else:
        explanation = (
            f"This integration can reach {pages} page(s) and {databases} "
            f"database(s) in {name}. Anything missing simply has not been "
            f"shared with it. {SHARING_NOTE}")

    report = AccessReport(
        id=str(workspace.get("bot_id") or "access"),
        title=f"Access in {name}",
        workspace_name=name,
        pages_visible=pages,
        databases_visible=databases,
        total_visible=len(results),
        explanation=explanation,
    )
    return ActionResult.success(report, explanation)
