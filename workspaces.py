"""Workspace resolution: tokens -> named workspaces, and name -> id lookup.

Two jobs, both about never making the user handle a UUID:

1. A Notion integration token is scoped to exactly ONE workspace, so
   "multiple workspaces" means multiple tokens. `/v1/users/me` on a bot token
   returns that bot's `workspace_name`, which is how a token gets a human name
   without asking the user to label anything.

2. The spec requires name-first targeting: the user says "the Roadmap page",
   not a UUID. `resolve_target` searches, and -- importantly -- refuses to guess
   when several things match, because silently picking one and then WRITING to
   it is the expensive kind of wrong.

Tokens live only in the Vault secret. The store caches workspace NAMES and IDS
so the picker can render without hitting Notion; never a token.
"""

from __future__ import annotations

import notion_client as nc
import notion_objects as no

WORKSPACES_COLLECTION = "workspaces"

# A Notion UUID with or without dashes -- used to let a user paste a raw id.
_UUID_CHARS = set("0123456789abcdefABCDEF-")


def looks_like_id(value: str) -> bool:
    """True when the string is plausibly a Notion object id.

    Guard, not validation: it decides whether to skip the name lookup, so it is
    deliberately strict about shape (32 hex digits) and never invents an id.
    """
    if not value:
        return False
    raw = value.strip()
    if not all(ch in _UUID_CHARS for ch in raw):
        return False
    return len(raw.replace("-", "")) == 32


def normalize_id(value: str) -> str:
    """Notion accepts dashed or undashed ids; send back what the user gave."""
    return value.strip()


async def load_tokens(ctx) -> list[str]:
    """Read the configured integration tokens, one per line.

    Blank lines and stray whitespace are tolerated: the user is pasting into a
    textarea, and a trailing newline should not create a phantom workspace.
    """
    try:
        raw = await ctx.secrets.get("notion_tokens")
    except Exception:
        return []
    if not raw:
        return []
    seen: set[str] = set()
    tokens: list[str] = []
    for line in raw.splitlines():
        token = line.strip()
        # Deduplicated so the same token pasted twice does not show up as two
        # identical workspaces in the picker.
        if token and token not in seen:
            seen.add(token)
            tokens.append(token)
    return tokens


async def describe_token(ctx, token: str) -> dict:
    """Identify the workspace behind one token via `/v1/users/me`.

    Returns a plain dict either way -- a bad token yields a describable entry
    (with its structured code) instead of an exception, so ONE broken token
    cannot blank out the whole workspace list.
    """
    out = await nc.request(ctx, "GET", "users/me", token)
    if not out.get("ok"):
        return {"ok": False, "code": out.get("code", ""), "error": out.get("error", "")}

    bot = out["data"]
    owner = (bot.get("bot") or {}).get("owner") or {}
    return {
        "ok": True,
        "bot_id": str(bot.get("id") or ""),
        "name": str(bot.get("name") or "Notion integration"),
        "workspace_name": str((bot.get("bot") or {}).get("workspace_name") or ""),
        "workspace_id": str((bot.get("bot") or {}).get("workspace_id") or ""),
        "owner_type": str(owner.get("type") or ""),
    }


async def list_workspaces(ctx, *, refresh: bool = False) -> list[dict]:
    """All configured workspaces, in the order their tokens were entered.

    Cached in the store so panels stay fast; `refresh=True` re-reads from
    Notion. The cache key is the bot id, never the token.
    """
    tokens = await load_tokens(ctx)
    if not tokens:
        return []

    cached: dict[str, dict] = {}
    if not refresh:
        try:
            page = await ctx.store.query(WORKSPACES_COLLECTION, limit=100)
            for doc in page.data:
                data = doc.data or {}
                slot = data.get("slot")
                if isinstance(slot, int):
                    cached[str(slot)] = data
        except Exception:
            cached = {}

    out: list[dict] = []
    for index, token in enumerate(tokens):
        hit = cached.get(str(index))
        if hit and hit.get("workspace_name"):
            entry = dict(hit)
            entry["slot"] = index
            out.append(entry)
            continue

        info = await describe_token(ctx, token)
        if not info.get("ok"):
            out.append({
                "slot": index,
                "workspace_name": f"Token #{index + 1} (not usable)",
                "workspace_id": "",
                "bot_id": "",
                "integration_name": "",
                "status": "error",
                "error": info.get("error", ""),
                "code": info.get("code", ""),
            })
            continue

        entry = {
            "slot": index,
            "workspace_name": info["workspace_name"] or f"Workspace #{index + 1}",
            "workspace_id": info["workspace_id"],
            "bot_id": info["bot_id"],
            "integration_name": info["name"],
            "owner_type": info["owner_type"],
            "status": "ok",
            "error": "",
            "code": "",
        }
        out.append(entry)
        await _cache_workspace(ctx, entry)

    return out


async def _cache_workspace(ctx, entry: dict) -> None:
    """Upsert one workspace record. Cache failures are never fatal."""
    try:
        page = await ctx.store.query(WORKSPACES_COLLECTION,
                                     where={"slot": entry["slot"]}, limit=1)
        if page.data:
            await ctx.store.update(WORKSPACES_COLLECTION, page.data[0].id, entry)
        else:
            await ctx.store.create(WORKSPACES_COLLECTION, entry)
    except Exception:
        pass


async def resolve_workspace(ctx, name: str = "") -> dict:
    """Pick the workspace to act on.

    No name + exactly one configured -> that one (the common case: the user has
    a single workspace and should never have to name it). No name + several ->
    an error that LISTS them, because picking one at random and then writing to
    it is unrecoverable.
    """
    tokens = await load_tokens(ctx)
    if not tokens:
        return nc.fail(nc.NOTION_TOKEN_MISSING)

    entries = await list_workspaces(ctx)
    wanted = (name or "").strip().lower()

    if not wanted:
        if len(tokens) == 1:
            return {"ok": True, "token": tokens[0],
                    "workspace": entries[0] if entries else {"slot": 0}}
        names = ", ".join(e.get("workspace_name", "?") for e in entries) or "-"
        return nc.fail(
            nc.NOTION_WORKSPACE_UNKNOWN,
            f"Several Notion workspaces are connected -- name the one to use: {names}.")

    exact = [e for e in entries
             if str(e.get("workspace_name", "")).strip().lower() == wanted]
    partial = [e for e in entries
               if wanted in str(e.get("workspace_name", "")).strip().lower()]
    matches = exact or partial

    if not matches:
        names = ", ".join(e.get("workspace_name", "?") for e in entries) or "-"
        return nc.fail(
            nc.NOTION_WORKSPACE_UNKNOWN,
            f"No connected Notion workspace matches '{name}'. Connected: {names}.")
    if len(matches) > 1:
        names = ", ".join(e.get("workspace_name", "?") for e in matches)
        return nc.fail(nc.NOTION_TARGET_AMBIGUOUS,
                       f"'{name}' matches several workspaces: {names}.")

    entry = matches[0]
    slot = entry.get("slot", 0)
    if not isinstance(slot, int) or slot >= len(tokens):
        return nc.fail(nc.NOTION_WORKSPACE_UNKNOWN,
                       "That workspace's token is no longer configured.")
    if entry.get("status") == "error":
        return nc.fail(entry.get("code") or nc.NOTION_TOKEN_REJECTED,
                       entry.get("error") or nc.message_for(nc.NOTION_TOKEN_REJECTED))
    return {"ok": True, "token": tokens[slot], "workspace": entry}


async def resolve_target(ctx, token: str, reference: str, *,
                         kind: str = "") -> dict:
    """Resolve a page/database NAME (or a pasted id) to a concrete object.

    `kind` is "page", "data_source" or "" (both). Ambiguity is an error, not a
    coin flip: the caller may be about to overwrite whatever comes back.
    """
    ref = (reference or "").strip()
    if not ref:
        return nc.fail(nc.NOTION_TARGET_NOT_FOUND, "No page or database was named.")

    if looks_like_id(ref):
        return {"ok": True, "id": normalize_id(ref), "title": "", "object": kind or "",
                "resolved_by": "id"}

    payload: dict = {"query": ref}
    if kind:
        payload["filter"] = {"property": "object", "value": kind}
    out = await nc.paginate(ctx, "POST", "search", token, json=payload,
                            limit=nc.MAX_PAGE_SIZE, max_pages=1)
    if not out.get("ok"):
        return out

    results = out.get("results", [])
    if not results:
        return nc.fail(
            nc.NOTION_TARGET_NOT_FOUND,
            f"Nothing named '{ref}' is visible to this integration. If it exists, "
            "share it with the integration in Notion first.")

    wanted = ref.lower()
    scored = [(no.title_of(item), item) for item in results]
    exact = [(t, i) for t, i in scored if t.strip().lower() == wanted]
    candidates = exact or scored

    if len(candidates) > 1:
        shown = ", ".join(f"'{t or 'untitled'}'" for t, _ in candidates[:5])
        return nc.fail(
            nc.NOTION_TARGET_AMBIGUOUS,
            f"'{ref}' matches {len(candidates)} objects ({shown}). "
            "Use a more specific name or paste the Notion id.")

    title, item = candidates[0]
    return {"ok": True, "id": str(item.get("id") or ""), "title": title,
            "object": str(item.get("object") or ""), "raw": item,
            "resolved_by": "name"}
