"""Panel rendering.

These did not exist before, which is why two real bugs shipped: the center
panel unpacked `list_workspaces` (a LIST) into two values, so it fell into its
own except branch on every load, and the table read keys the records never had.
Rendering each panel against real fixtures is what catches that class of bug.
"""

import json

from imperal_sdk.testing import MockSecretStore

import panels
from conftest import bot_payload_default


def _flatten(node) -> list:
    """Every UINode in the tree, depth-first."""
    out = []
    if node is None:
        return out
    props = getattr(node, "props", None)
    if props is None:
        return out
    out.append(node)
    for value in props.values():
        if isinstance(value, list):
            for item in value:
                out.extend(_flatten(item))
        else:
            out.extend(_flatten(value))
    return out


def _types(node) -> list[str]:
    return [n.type for n in _flatten(node)]


def _dump(node) -> str:
    """Serialised tree — used to assert on text without walking by hand."""
    return json.dumps(node, default=lambda o: getattr(o, "props", str(o)))


# --- the connect screen -----------------------------------------------------

async def test_connect_screen_submits_to_a_function_of_THIS_extension(ctx):
    """The form's action must be one of our own functions.

    A panel form's `action=` is resolved against the functions of the extension
    that rendered the panel. The docs recipe suggests
    ui.Form(action="save_app_secret"), which belongs to the DEVELOPER
    extension, so clicking Connect died with
    "Function 'save_app_secret' not found in 'notion-connector'".
    connect_workspace is ours, so it resolves.
    """
    tree = await panels.connect_panel(ctx)
    form = next(n for n in _flatten(tree) if n.type == "Form")
    assert form.props["action"] == "connect_workspace"

    # Guard the regression directly: never target another app's function.
    assert form.props["action"] != "save_app_secret"


def test_the_form_action_is_a_registered_function_of_this_extension():
    """Static proof, independent of the panel: the action really exists.

    A form action that does not resolve fails at CLICK time, not at build or
    validate time -- exactly why the first version shipped broken. Asserting
    against the registry catches a rename before a user does.
    """
    import main  # noqa: F401  -- registers every handler module
    from app import ext

    names = {f.name for f in ext.functions} if hasattr(ext, "functions") else set()
    if not names:  # registry shape differs across SDK versions
        import handlers_write
        names = {"connect_workspace"} & set(dir(handlers_write))
    assert "connect_workspace" in names


async def test_connect_field_is_masked_and_never_prefilled(ctx):
    """ui.Password renders an Input pinned to type='password' (EXT-SECRETS-V1)."""
    tree = await panels.connect_panel(ctx)
    fields = [n for n in _flatten(tree) if n.type == "Input"]
    assert len(fields) == 1, "exactly one credential field"
    assert fields[0].props["type"] == "password"
    assert fields[0].props["param_name"] == "token"
    assert not fields[0].props.get("value"), "must never be pre-filled"


async def test_connect_screen_still_links_to_the_secrets_manager(ctx):
    """The manual route stays available for editing/removing stored tokens."""
    body = _dump(await panels.connect_panel(ctx))
    assert "/ext/notion-connector/secrets#notion_tokens" in body


async def test_connect_screen_never_shows_a_token(connected_ctx, http):
    """Nothing on this screen may echo a stored credential back."""
    http.push(bot_payload_default())
    body = _dump(await panels.connect_panel(connected_ctx))
    assert "ntn_test_token_one" not in body


async def test_connect_screen_tells_the_user_which_integration_type(ctx):
    """The redirect-URI confusion comes from picking a PUBLIC integration."""
    body = _dump(await panels.connect_panel(ctx))
    assert "INTERNAL" in body
    assert "redirect URI" in body


async def test_connect_screen_explains_sharing_since_a_fresh_token_sees_nothing(ctx):
    body = _dump(await panels.connect_panel(ctx))
    assert "Connections" in body


async def test_connect_reassures_that_an_existing_workspace_is_kept(connected_ctx, http):
    """Adding a token must not look like it will clobber the current setup.

    Earlier this screen WARNED that saving replaces the whole value, because
    the only way in was the Secrets manager, where the stored string is edited
    wholesale. connect_workspace appends instead, so the honest message is now
    reassurance -- and the old warning would be a lie.
    """
    http.push(bot_payload_default())
    body = _dump(await panels.connect_panel(connected_ctx))
    assert "keeps the existing ones" in body
    assert "replaces" not in body


# --- the workspaces panel ---------------------------------------------------

async def test_workspaces_panel_renders_connected_rows(connected_ctx, http):
    """The regression: this used to hit its except branch every single time."""
    http.push(bot_payload_default(workspace="Acme HQ"))
    tree = await panels.workspaces_panel(connected_ctx)

    table = next(n for n in _flatten(tree) if n.type == "DataTable")
    assert table.props["rows"] == [{
        "workspace": "Acme HQ",
        "integration": "Imperal",
        "status": "Ready",
    }]

    alert = next(n for n in _flatten(tree) if n.type == "Alert")
    assert alert.props["variant"] == "success"


async def test_workspaces_panel_row_keys_match_the_declared_columns(connected_ctx, http):
    """Rows keyed differently from columns render as blank cells."""
    http.push(bot_payload_default())
    tree = await panels.workspaces_panel(connected_ctx)
    table = next(n for n in _flatten(tree) if n.type == "DataTable")
    column_keys = {c["key"] for c in table.props["columns"]}
    for row in table.props["rows"]:
        assert set(row) == column_keys


async def test_empty_workspaces_panel_leads_to_the_connect_screen(ctx):
    """A first-time user must be able to get somewhere from the empty state."""
    tree = await panels.workspaces_panel(ctx)
    empty = next(n for n in _flatten(tree) if n.type == "Empty")
    action = empty.props["action"]
    assert action.params["function"] == "__panel__notion"
    assert action.params["params"]["view"] == "connect"


async def test_a_broken_token_is_a_warning_row_not_a_blank_screen(ctx, http):
    ctx.secrets = MockSecretStore({"notion_tokens": "bad\ngood"})
    http.push({"code": "unauthorized", "message": "invalid"}, 401)
    http.push(bot_payload_default(workspace="Good WS"))

    tree = await panels.workspaces_panel(ctx, refresh=True)
    alert = next(n for n in _flatten(tree) if n.type == "Alert")
    assert alert.props["variant"] == "warn"
    table = next(n for n in _flatten(tree) if n.type == "DataTable")
    assert len(table.props["rows"]) == 2
    assert "Needs attention" in [r["status"] for r in table.props["rows"]]


async def test_panel_renders_a_banner_when_loading_blows_up(ctx, monkeypatch):
    """A blank screen is worse than a banner — and no internals leak."""
    async def boom(*_a, **_k):
        raise RuntimeError("store exploded: /internal/path")

    monkeypatch.setattr(panels.ws, "list_workspaces", boom)
    tree = await panels.workspaces_panel(ctx)
    body = _dump(tree)
    assert "store exploded" not in body
    assert "/internal/path" not in body
    alert = next(n for n in _flatten(tree) if n.type == "Alert")
    assert alert.props["variant"] == "error"


# --- the sidebar ------------------------------------------------------------

async def test_sidebar_offers_connect_when_nothing_is_configured(ctx):
    tree = await panels.notion_nav(ctx)
    labels = [n.props.get("label") for n in _flatten(tree) if n.type == "Button"]
    assert "Connect Notion" in labels


async def test_sidebar_switches_to_open_panel_once_connected(connected_ctx, http):
    http.push(bot_payload_default())
    tree = await panels.notion_nav(connected_ctx)
    labels = [n.props.get("label") for n in _flatten(tree) if n.type == "Button"]
    assert "Open Notion panel" in labels
    assert "Connect Notion" not in labels


# --- slot ownership ---------------------------------------------------------

def test_at_most_one_panel_per_slot():
    """Two panels on one slot is a structural bug, not a rendering bug.

    `connect` and `workspaces` were both slot="center", center_overlay=True.
    The host fetches each configured slot once at session init and a slot holds
    exactly ONE panel with replace semantics -- so the two raced, one replaced
    the other, and dispatching the loser looked like "nothing happens while the
    shell reloads". Nothing caught it: the validator accepts it and every panel
    rendered fine in isolation. Only looking at the slot MAP reveals it.
    """
    import main  # noqa: F401  -- registers every panel
    from app import ext

    seen: dict[str, list[str]] = {}
    for panel_id, spec in ext.panels.items():
        slot = spec["slot"] if isinstance(spec, dict) else getattr(spec, "slot")
        seen.setdefault(slot, []).append(panel_id)

    clashes = {slot: ids for slot, ids in seen.items() if len(ids) > 1}
    assert not clashes, f"more than one panel claims a slot: {clashes}"


def test_every_dispatched_panel_id_exists():
    """A ui.Call naming a dead panel fails only when the user clicks it.

    Renaming the center panel is exactly when this breaks, so assert the whole
    graph of dispatches against the registry instead of trusting a grep.
    """
    import re

    import main  # noqa: F401
    from app import ext

    source = open("panels.py", encoding="utf-8").read()
    dispatched = set(re.findall(r'ui\.Call\(\s*"__panel__(\w+)"', source))
    unknown = dispatched - set(ext.panels)
    assert not unknown, f"panels dispatched but never declared: {unknown}"


def test_refresh_panels_name_real_panels():
    """`refresh_panels` takes bare ids -- a stale name refreshes nothing."""
    import re

    import main  # noqa: F401
    from app import ext

    named: set[str] = set()
    for path in ("handlers_write.py", "handlers_read.py"):
        source = open(path, encoding="utf-8").read()
        for block in re.findall(r"refresh_panels=\[([^\]]*)\]", source):
            named.update(re.findall(r'"(\w+)"', block))

    unknown = named - set(ext.panels)
    assert not unknown, f"refresh_panels names non-existent panels: {unknown}"
