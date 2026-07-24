"""Workspaces panel: connection state, what is accessible, and why not.

Section 3 of the spec is the reason this panel exists: the connector must SHOW
what is accessible, what is not, and why. An empty Notion connector is almost
never broken -- it is un-shared -- and that sentence belongs on screen, not in
a support conversation.

SKETCH -- workspaces panel (every component checked against PRE-PANEL CHECKLIST)
  ui.Stack (v, gap=4)
    ui.Header(text="Notion Connector", level=2, subtitle=...)
    ui.Alert(message=..., type=...)                 -- connection state
    ui.Section(title="Connected workspaces", children=[   -- children REQUIRED
      ui.DataTable(columns=[DataColumn dicts], rows=[plain dicts])
      | ui.Empty(message=...)                       -- never return None
    ])
    ui.Section(title="How access works", children=[
      ui.Text(variant="body") x2
      ui.Button(label="Re-check access", variant="secondary",
                on_click=ui.Call("__panel__workspaces", refresh=True))
      ui.Link(label="Open notion.so/my-integrations", href=...)
    ])

Checklist notes that shaped the above:
  * ui.DataColumn returns a DICT -> passed as a list of dicts to DataTable.
  * ui.Section requires children= -> always passed, never omitted.
  * ui.Badge would need label= (not text=) -- not used here; the table renders
    plain strings instead, which keeps the rows simple dicts.
  * ui.Empty() returned instead of None when there is nothing to show.
  * @ext.panel slot="center" REQUIRES center_overlay=True or it is never
    fetched; refresh_seconds does not exist, so refresh="manual" + a button.
  * ui.Input is NOT used: tokens must never travel through a panel form field.
    They belong in the platform Secrets tab, which is Vault-encrypted.
"""

from __future__ import annotations

from imperal_sdk import ui

import notion_client as nc
import workspaces as ws
from app import ext

_INTEGRATIONS_URL = "https://www.notion.so/my-integrations"


def _state_alert(count: int, errors: list[str]):
    """One banner describing the connection state in the user's terms."""
    if errors and not count:
        return ui.Alert(
            title="Not connected",
            message=" ".join(errors),
            type="error",
        )
    if not count:
        return ui.Alert(
            title="No Notion workspace connected yet",
            message=(
                "Create an integration at notion.so/my-integrations, copy its "
                "internal integration token, and paste it into the Secrets tab "
                "as notion_tokens. One token per line connects several "
                "workspaces."
            ),
            type="info",
        )
    if errors:
        return ui.Alert(
            title=f"{count} workspace(s) connected, some tokens need attention",
            message=" ".join(errors),
            type="warn",
        )
    return ui.Alert(
        title=f"{count} workspace(s) connected",
        message=(
            "Ready. Ask in chat to search, read pages, or query databases -- "
            "by name, no ids needed."
        ),
        type="success",
    )


@ext.panel("workspaces", slot="center", title="Notion", icon="BookOpen",
           center_overlay=True, refresh="manual")
async def workspaces_panel(ctx, **kwargs):
    """Render connected workspaces and explain the sharing model."""
    refresh = bool(kwargs.get("refresh"))

    records: list[dict] = []
    errors: list[str] = []
    try:
        records, errors = await ws.list_workspaces(ctx, refresh=refresh)
    except Exception:
        # The panel must still render: a blank screen is worse than a banner.
        # Detail goes to the audit log, never into the user-facing string.
        await ctx.log("workspaces panel failed to load workspaces", "error")
        errors = ["Could not load workspaces just now. Try Re-check access."]

    rows = [
        {
            "name": r.get("name") or "Untitled workspace",
            "accessible": str(r.get("accessible_objects", 0)),
            "bot": r.get("bot_name") or "",
            "status": "OK" if r.get("ok") else "Needs attention",
        }
        for r in records
    ]

    if rows:
        table = ui.DataTable(
            columns=[
                ui.DataColumn(key="name", label="Workspace"),
                ui.DataColumn(key="accessible", label="Shared objects"),
                ui.DataColumn(key="bot", label="Integration"),
                ui.DataColumn(key="status", label="Status"),
            ],
            rows=rows,
        )
    else:
        table = ui.Empty(
            message="No workspace connected yet -- add a token in the Secrets tab.",
        )

    return ui.Stack(
        direction="v",
        gap=4,
        children=[
            ui.Header(
                text="Notion Connector",
                level=2,
                subtitle="Search, read and update Notion from Imperal",
            ),
            _state_alert(len(records), errors),
            ui.Section(title="Connected workspaces", children=[table]),
            ui.Section(
                title="How access works",
                children=[
                    ui.Text(
                        text=(
                            "This connector sees only what you explicitly share "
                            "with the integration. It does not get your whole "
                            "workspace automatically."
                        ),
                        variant="body",
                    ),
                    ui.Text(
                        text=(
                            "To share a page or database: open it in Notion, "
                            "click the three-dot menu, choose Connections, and "
                            "add the integration. Subpages inherit that access."
                        ),
                        variant="body",
                    ),
                    ui.Row(
                        gap=3,
                        children=[
                            ui.Button(
                                label="Re-check access",
                                variant="secondary",
                                on_click=ui.Call("__panel__workspaces", refresh=True),
                            ),
                            ui.Link(
                                label="Open notion.so/my-integrations",
                                href=_INTEGRATIONS_URL,
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
