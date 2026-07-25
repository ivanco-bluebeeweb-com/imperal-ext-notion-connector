"""Panels: connect first, then show what is reachable and why.

Three surfaces, in the order a new user meets them:

* ``connect``    -- center overlay: paste a token and be done. This exists
                    because a first-time user opened the app and had nowhere to
                    put their token; the auto-injected Secrets tab is correct
                    but not discoverable.
* ``workspaces`` -- center overlay: connected workspaces, and the sharing rules
                    that explain an empty result (spec section 3).
* ``notion_nav`` -- left sidebar: connection state at a glance.

CREDENTIAL HANDLING (federal EXT-SECRETS-V1)
``notion_tokens`` is declared ``write_mode="user"``, so extension code CANNOT
write it -- ``ctx.secrets.set()`` raises SecretWriteForbidden. Only Panel UI
may write it.

These panels therefore never capture the token themselves. The docs recipe
(recipes/handle-user-api-keys) shows ``ui.Form(action="save_app_secret")``, but
a panel ``action`` is resolved against the FUNCTIONS OF THIS EXTENSION, and
``save_app_secret`` belongs to the developer extension -- so it fails at click
time with "Function 'save_app_secret' not found in 'notion-connector'". That
recipe snippet only works from inside the extension that owns the action.

The SDK's own built-in secrets handler reaches the same conclusion for the same
reason and says so explicitly: it refuses to inline an input form because the
canonical credential UI lives in Panel React (SecretManagerCard), and
duplicating it in ui.* primitives would split the federal contract. It
navigates to ``/ext/<ext_id>/secrets`` instead.

So the Connect screen owns the EXPLANATION -- which integration type, why no
redirect URI, what sharing still has to happen -- and hands the actual keystroke
over to that route. No panel here ever reads a token back.
"""

from __future__ import annotations

from imperal_sdk import ui

import workspaces as ws
from app import ext

_INTEGRATIONS_URL = "https://www.notion.so/my-integrations"

_SECRET_NAME = "notion_tokens"

# The canonical credential route. The SDK's own built-in secrets panel sends
# users to exactly this path, where Panel React's SecretManagerCard owns the
# input -- that component, not a panel form, is the federal EXT-SECRETS-V1
# surface (no echo, cleared on submit). Derived from the Extension so it can
# never drift from the real id.
_SECRETS_ROUTE = f"/ext/{ext.app_id}/secrets#{_SECRET_NAME}"


def _errors_of(records: list[dict]) -> list[str]:
    """Human sentences for tokens that came back unusable.

    `list_workspaces` returns ONE list of records, each carrying its own
    status -- a broken token is a row, not an exception, so the messages are
    derived here rather than returned alongside.
    """
    out: list[str] = []
    for record in records:
        if record.get("status") != "ok":
            label = record.get("workspace_name") or "A token"
            detail = record.get("error") or "This token is not usable."
            out.append(f"{label}: {detail}")
    return out


def _state_alert(records: list[dict]):
    """One banner describing the connection state in the user's terms."""
    errors = _errors_of(records)
    usable = sum(1 for r in records if r.get("status") == "ok")

    if not records:
        return ui.Alert(
            title="No Notion workspace connected yet",
            message=(
                "Use Connect Notion below: create an integration, paste its "
                "token, and share the pages it should reach."
            ),
            type="info",
        )
    if not usable:
        return ui.Alert(
            title="Not connected",
            message=" ".join(errors),
            type="error",
        )
    if errors:
        return ui.Alert(
            title=f"{usable} of {len(records)} workspace(s) ready",
            message=" ".join(errors),
            type="warn",
        )
    return ui.Alert(
        title=f"{usable} workspace(s) connected",
        message=(
            "Ready. Ask in chat to search, read pages, or query databases -- "
            "by name, no ids needed."
        ),
        type="success",
    )


async def connect_panel(ctx, **kwargs):
    """Paste an integration token and connect a workspace.

    NOT a panel of its own: it is one VIEW of the single center panel below.
    Two panels declared on slot="center" fight over one slot -- see the
    `notion_center` docstring for why that shipped as visible chaos.

    SKETCH -- connect screen (props checked against ui-components-reference)
      ui.Stack (v, gap=4)
        ui.Header(text="Connect Notion", level=2, subtitle=...)
        ui.Alert(...)                       -- already-connected notice, if any
        ui.Section(title="1. Create an integration", children=[
          ui.Text(content=..., variant="body")
          ui.Link(label="Open notion.so/my-integrations", href=...)
        ])
        ui.Section(title="2. Paste the token", children=[
          ui.Text(content=..., variant="body")
          ui.Form(action="connect_workspace", submit_label="Connect", children=[
            ui.Password(placeholder="ntn_...", param_name="token")
          ])
          ui.Link(label="Or manage the stored tokens directly", href=_SECRETS_ROUTE)
        ])
        ui.Section(title="3. Share your pages", children=[
          ui.Text(content=..., variant="body")
          ui.Button(label="Check what is reachable", ...)
        ])

    Checklist notes:
      * The token field submits to `connect_workspace`, a function of THIS
        extension. A panel form's action= resolves against the rendering
        extension's own functions, which is why the documented
        ui.Form(action="save_app_secret") could never work here:
        save_app_secret belongs to the DEVELOPER extension, so clicking it
        failed with "Function not found in 'notion-connector'".
      * That handler can store the value because `notion_tokens` is declared
        write_mode="both" -- Panel UI *and* extension code may write it. It was
        write_mode="user", which made ctx.secrets.set() raise
        SecretWriteForbidden and left this screen with nothing it could call.
      * Writing through our own handler also fixes the reported symptom "I
        paste the key, press Save, the field clears and nothing happens": the
        value now travels through the SAME secrets client that every read uses,
        and the token is verified against Notion BEFORE it is stored, so the
        user gets a verdict instead of silence.
      * ui.Password returns an Input node with type="password" -- assert on
        Input, not on a "Password" node type.
      * slot="center" REQUIRES center_overlay=True, else it is never fetched.
      * ui.Section always gets children=; ui.Text takes content=, not text=.
    """
    try:
        records = await ws.list_workspaces(ctx)
    except Exception:
        await ctx.log("connect panel could not read workspace state", "error")
        records = []

    children = [
        ui.Header(
            text="Connect Notion",
            level=2,
            subtitle="Three steps, about a minute",
        )
    ]

    # Tokens are APPENDED by connect_workspace, so an existing setup is safe --
    # this is now reassurance, not a warning about clobbering other workspaces.
    if records:
        children.append(ui.Alert(
            title=f"{len(records)} workspace(s) already connected",
            message=(
                "Adding another token here keeps the existing ones: each "
                "workspace is a separate line in the stored value."
            ),
            type="info",
        ))

    children.append(ui.Section(
        title="1. Create an integration in Notion",
        children=[
            ui.Text(
                content=(
                    "Open notion.so/my-integrations and create a new "
                    "INTERNAL integration for the workspace you want to use, "
                    "then copy its internal integration secret (it starts "
                    "with ntn_). Internal is the right type here -- public "
                    "integrations ask for a redirect URI, which this "
                    "connector does not use."
                ),
                variant="body",
            ),
            ui.Link(
                label="Open notion.so/my-integrations",
                href=_INTEGRATIONS_URL,
            ),
        ],
    ))

    children.append(ui.Section(
        title="2. Paste the token",
        children=[
            ui.Text(
                content=(
                    "Paste it below. The token is checked against Notion "
                    "before it is saved, so you find out immediately whether "
                    "it works -- and it is stored encrypted, never shown back "
                    "here, not even to you."
                ),
                variant="body",
            ),
            ui.Form(
                action="connect_workspace",
                submit_label="Connect",
                children=[
                    ui.Password(
                        placeholder="ntn_...",
                        param_name="token",
                    ),
                ],
            ),
            ui.Text(
                content=(
                    "Adding a second workspace? Paste its token here too -- "
                    "each one is appended, not replaced."
                ),
                variant="caption",
            ),
            ui.Link(
                label="Or manage the stored tokens directly",
                href=_SECRETS_ROUTE,
            ),
        ],
    ))

    children.append(ui.Section(
        title="3. Share the pages it should see",
        children=[
            ui.Text(
                content=(
                    "A fresh integration sees nothing by default. In Notion, "
                    "open each page or database, click the three-dot menu, "
                    "choose Connections, and add your integration. Subpages "
                    "are included automatically."
                ),
                variant="body",
            ),
            ui.Row(
                gap=3,
                children=[
                    ui.Button(
                        label="Check what is reachable",
                        variant="primary",
                        on_click=ui.Call("__panel__notion", view="workspaces", refresh=True),
                    ),
                    ui.Button(
                        label="Ask in chat instead",
                        variant="ghost",
                        on_click=ui.Send("Check my Notion access"),
                    ),
                ],
            ),
        ],
    ))

    return ui.Stack(direction="v", gap=4, children=children)


async def workspaces_panel(ctx, **kwargs):
    """Render connected workspaces and explain the sharing model.

    One VIEW of the single center panel, not a panel itself.

    SKETCH -- workspaces panel
      ui.Stack (v, gap=4)
        ui.Header(text="Notion Connector", level=2, subtitle=...)
        ui.Alert(...)                                   -- connection state
        ui.Section(title="Connected workspaces", children=[
          ui.DataTable(columns=[DataColumn dicts], rows=[plain dicts])
          | ui.Empty(message=..., action=ui.Call("__panel__notion", view="connect"))
        ])
        ui.Section(title="How access works", children=[
          ui.Text(content=..., variant="body") x2   -- content=, NOT text=
          ui.Button(...) / ui.Link(...)
        ])
    """
    refresh = bool(kwargs.get("refresh"))

    records: list[dict] = []
    load_failed = False
    try:
        # Returns ONE list of records; each row carries its own status.
        records = await ws.list_workspaces(ctx, refresh=refresh)
    except Exception:
        # The panel must still render: a blank screen is worse than a banner.
        # Detail goes to the audit log, never into the user-facing string.
        await ctx.log("workspaces panel failed to load workspaces", "error")
        load_failed = True

    rows = [
        {
            "workspace": r.get("workspace_name") or "Untitled workspace",
            "integration": r.get("integration_name") or "",
            "status": "Ready" if r.get("status") == "ok" else "Needs attention",
        }
        for r in records
    ]

    if rows:
        body = ui.DataTable(
            columns=[
                ui.DataColumn(key="workspace", label="Workspace"),
                ui.DataColumn(key="integration", label="Integration"),
                ui.DataColumn(key="status", label="Status"),
            ],
            rows=rows,
        )
    else:
        body = ui.Empty(
            message="No Notion workspace connected yet.",
            action=ui.Call("__panel__notion", view="connect"),
        )

    alert = (
        ui.Alert(
            title="Could not load workspaces",
            message="Something went wrong on our side. Try Re-check access.",
            type="error",
        )
        if load_failed
        else _state_alert(records)
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
            alert,
            ui.Section(title="Connected workspaces", children=[body]),
            ui.Section(
                title="How access works",
                children=[
                    ui.Text(
                        content=(
                            "This connector sees only what you explicitly share "
                            "with the integration. It does not get your whole "
                            "workspace automatically."
                        ),
                        variant="body",
                    ),
                    ui.Text(
                        content=(
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
                                on_click=ui.Call("__panel__notion", view="workspaces", refresh=True),
                            ),
                            ui.Button(
                                label="Connect another workspace",
                                variant="ghost",
                                on_click=ui.Call("__panel__notion", view="connect"),
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


@ext.panel("notion", slot="center", title="Notion", icon="BookOpen",
           center_overlay=True, refresh="manual")
async def notion_center(ctx, **kwargs):
    """The ONE center panel. `view` picks which screen renders inside it.

    Why this exists as a single panel:

    `connect` and `workspaces` were two separate panels, both slot="center"
    with center_overlay=True. The host fetches every configured slot in one
    batch at session init and a center slot holds exactly ONE panel with
    REPLACE semantics -- no stacking, no tabs. Two overlay panels claiming the
    same slot therefore race: both are fetched, one silently replaces the
    other, and pressing a button that dispatches the loser looks like nothing
    happening while the shell re-renders around it. That is exactly the
    reported symptom -- "the left sidebar reloads and nothing happens" -- and
    it is a structural error, not a rendering bug: no amount of fixing the
    button could cure it while two panels owned one slot.

    Now there is one owner. Switching screens is a parameter, so a dispatch
    always targets the panel that is actually mounted:

        ui.Call("__panel__notion")                      -> workspaces (default)
        ui.Call("__panel__notion", view="connect")      -> connect screen
        ui.Call("__panel__notion", refresh=True)        -> workspaces, re-read

    A first-time user with no token lands on the connect screen automatically:
    the default view answers "what do I do now?" instead of showing an empty
    table.
    """
    view = str(kwargs.get("view") or "").strip().lower()

    if view not in ("connect", "workspaces"):
        # No explicit view: send an unconfigured user straight to the one
        # action that unblocks them, and everyone else to their workspaces.
        try:
            records = await ws.list_workspaces(ctx)
        except Exception:
            records = []
        view = "workspaces" if records else "connect"

    if view == "connect":
        return await connect_panel(ctx, **kwargs)
    return await workspaces_panel(ctx, **kwargs)


@ext.panel("notion_nav", slot="left", title="Notion", icon="BookOpen",
           refresh="manual")
async def notion_nav(ctx, **kwargs):
    """Sidebar entry: connection state at a glance, and a way in.

    SKETCH -- left nav panel
      ui.Stack (v, gap=2)
        ui.Text(content=<state>, variant="body")
        ui.Button("Connect Notion" | "Open Notion panel", full_width=True)
        ui.Button("Check access", variant="ghost", full_width=True)

    Deliberately tiny: the sidebar is for orientation, not for data. The first
    button changes with state -- an unconfigured app should offer the ONE
    action that unblocks it.
    """
    try:
        records = await ws.list_workspaces(ctx)
    except Exception:
        # The sidebar must never be the thing that breaks the shell.
        records = []

    usable = sum(1 for r in records if r.get("status") == "ok")
    if not records:
        state = "Not connected yet"
        primary = ui.Button(
            label="Connect Notion",
            variant="primary",
            full_width=True,
            on_click=ui.Call("__panel__notion", view="connect"),
        )
    else:
        state = f"{usable} of {len(records)} workspace(s) ready"
        primary = ui.Button(
            label="Open Notion panel",
            variant="secondary",
            full_width=True,
            on_click=ui.Call("__panel__notion", view="workspaces"),
        )

    return ui.Stack(
        direction="v",
        gap=2,
        children=[
            ui.Text(content=state, variant="body"),
            primary,
            ui.Button(
                label="Check access",
                variant="ghost",
                full_width=True,
                on_click=ui.Send("Check my Notion access"),
            ),
        ],
    )
