"""Pydantic parameter models and SDL return entities.

Every parameter that names a Notion object accepts a TITLE, not just an id
(product spec section 7/9: name-first). Ids are still accepted -- pasting one
from a Notion URL must keep working -- but nothing here ever requires the user
to find one.
"""

from pydantic import BaseModel, Field
from imperal_sdk import sdl


# --------------------------- parameters ---------------------------

class WorkspaceScoped(BaseModel):
    """Base for every tool: which connected workspace to act in."""
    workspace: str = Field(
        "", description="Workspace name, e.g. 'Acme HQ'. Omit when only one "
                        "Notion workspace is connected.")


class ListWorkspacesParams(BaseModel):
    refresh: bool = Field(
        False, description="Re-read workspace details from Notion instead of the cache")


class ConnectWorkspaceParams(BaseModel):
    """The token the user pastes on the Connect screen.

    Not WorkspaceScoped: this is the one action that runs BEFORE any workspace
    exists, so asking which workspace to act in would be circular. The
    workspace is discovered FROM the token.
    """
    token: str = Field(
        "", description="Notion Internal Integration Secret, starts with 'ntn_'. "
                        "Create one at notion.so/my-integrations.")


class ConnectResult(sdl.Entity):
    """Outcome of connecting a token -- what got connected, and what is next."""
    workspace_name: str = ""
    integration_name: str = ""
    already_connected: bool = False
    workspace_count: int = 0
    next_step: str = ""


class SearchParams(WorkspaceScoped):
    query: str = Field(
        "", description="Text to search for in page and database titles. "
                        "Empty returns everything shared with the integration.")
    kind: str = Field(
        "", description="Limit results: 'page', 'database', or empty for both")
    limit: int = Field(25, ge=1, le=100, description="Maximum results to return")


class ReadPageParams(WorkspaceScoped):
    page: str = Field(
        description="Page title or Notion page id/URL id, e.g. 'Q3 Roadmap'")
    include_content: bool = Field(
        True, description="Include the page's actual block content, not just properties")
    max_blocks: int = Field(
        200, ge=1, le=500, description="Maximum blocks to read from the page body")


class BrowseParams(WorkspaceScoped):
    parent: str = Field(
        "", description="Page or database to list children of. Empty lists the "
                        "top-level objects shared with the integration.")
    limit: int = Field(50, ge=1, le=100, description="Maximum children to return")


class ListDatabasesParams(WorkspaceScoped):
    query: str = Field("", description="Filter databases by title text")
    limit: int = Field(25, ge=1, le=100, description="Maximum databases to return")


class QueryDatabaseParams(WorkspaceScoped):
    database: str = Field(
        description="Database title or id, e.g. 'Tasks'")
    limit: int = Field(25, ge=1, le=100, description="Maximum rows to return")
    sort_by: str = Field(
        "", description="Property name to sort by, e.g. 'Due date'")
    descending: bool = Field(False, description="Sort newest/highest first")


class ListUsersParams(WorkspaceScoped):
    limit: int = Field(50, ge=1, le=100, description="Maximum users to return")


class ListCommentsParams(WorkspaceScoped):
    page: str = Field(description="Page title or id whose comments to read")
    limit: int = Field(50, ge=1, le=100, description="Maximum comments to return")


class CheckAccessParams(WorkspaceScoped):
    pass


class CreatePageParams(WorkspaceScoped):
    title: str = Field(description="Title for the new page")
    parent: str = Field(
        "", description="Parent page or database title/id. Empty places the page "
                        "at the top level the integration can reach.")
    content: str = Field(
        "", description="Page body as plain text or simple markdown "
                        "(# heading, - bullet, 1. numbered, > quote)")
    properties: dict = Field(
        default_factory=dict,
        description="Database properties when creating a row, e.g. "
                    "{'Status': 'In progress', 'Priority': 'High'}")


class UpdatePageContentParams(WorkspaceScoped):
    page: str = Field(description="Page title or id to add content to")
    content: str = Field(
        description="Text to add, as plain text or simple markdown")
    position: str = Field(
        "end", description="Where to insert: 'end' (default) or 'start'")


class UpdatePageParams(WorkspaceScoped):
    page: str = Field(description="Page or database row title/id to update")
    title: str = Field("", description="New title (omit to keep the current one)")
    properties: dict = Field(
        default_factory=dict,
        description="Database properties to set, e.g. {'Status': 'Done'}")


class MovePageParams(WorkspaceScoped):
    page: str = Field(description="Page title or id to move")
    new_parent: str = Field(
        description="Destination page or database title/id")


class TrashPageParams(WorkspaceScoped):
    page: str = Field(description="Page title or id to move to Notion's trash")
    restore: bool = Field(
        False, description="Set true to restore a page out of the trash instead")


class AddCommentParams(WorkspaceScoped):
    page: str = Field(description="Page title or id to comment on")
    comment: str = Field(description="Comment text")


class CreateDatabaseParams(WorkspaceScoped):
    title: str = Field(description="Title for the new database")
    parent: str = Field(
        description="Parent page title or id the database will live in")
    properties: dict = Field(
        default_factory=dict,
        description="Column name -> type, e.g. {'Status': 'select', "
                    "'Due': 'date', 'Done': 'checkbox'}. A Name title column "
                    "is always created.")


# --------------------------- SDL return entities ---------------------------

class WorkspaceRecord(sdl.Entity):
    """One connected Notion workspace."""
    workspace_name: str = ""
    workspace_id: str = ""
    integration_name: str = ""
    state: str = ""
    detail: str = ""


class WorkspaceList(sdl.EntityList[WorkspaceRecord]):
    pass


class NotionObject(sdl.Entity):
    """A page or database visible to the integration."""
    object_type: str = ""
    notion_id: str = ""
    parent_kind: str = ""
    parent_id: str = ""
    url: str = ""
    last_edited: str = ""
    in_trash: bool = False


class NotionObjectList(sdl.EntityList[NotionObject]):
    pass


class PageContent(sdl.Entity):
    """A page with its properties and actual block content."""
    notion_id: str = ""
    url: str = ""
    content: str = ""
    block_count: int = 0
    truncated: bool = False
    properties: dict = {}
    last_edited: str = ""
    in_trash: bool = False


class DatabaseRow(sdl.Entity):
    """One row of a Notion database."""
    notion_id: str = ""
    url: str = ""
    values: dict = {}
    last_edited: str = ""


class DatabaseRowList(sdl.EntityList[DatabaseRow]):
    pass


class UserRecord(sdl.Entity):
    """A person or bot in the workspace."""
    notion_id: str = ""
    user_type: str = ""
    email: str = ""


class UserList(sdl.EntityList[UserRecord]):
    pass


class CommentRecord(sdl.Entity):
    """One comment on a page."""
    notion_id: str = ""
    author: str = ""
    text: str = ""
    created: str = ""


class CommentList(sdl.EntityList[CommentRecord]):
    pass


class AccessReport(sdl.Entity):
    """What the integration can and cannot reach, and why."""
    workspace_name: str = ""
    pages_visible: int = 0
    databases_visible: int = 0
    total_visible: int = 0
    explanation: str = ""


class WriteResult(sdl.Entity):
    """Outcome of a write action."""
    notion_id: str = ""
    url: str = ""
    action: str = ""
    detail: str = ""
