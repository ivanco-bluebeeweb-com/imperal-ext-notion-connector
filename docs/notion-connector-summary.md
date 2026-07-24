# Notion Connector — implementation summary

Built from the product spec note *"Notion Connector — Product Spec"*
(notes folder **Imperal**). Every spec section maps to something concrete below.

## Spec coverage

| Spec | Where it lives |
|---|---|
| §2 read workspace/content first | 9 read tools; `read_page` returns real blocks |
| §2 base layer for automations | every write tool declares `event=` |
| §3 explain the access model | `check_access`, `SHARING_NOTE`, Workspaces panel, README |
| §4 multiple workspaces | `notion_tokens`, one token per line → `workspaces.py` |
| §5 object coverage | pages, databases, rows, comments, users, blocks, files, search |
| §6 required actions | 7 write tools |
| §7/§9 name-first targeting | `resolve_target` — ids optional, ambiguity refused |

## Modules

| File | Responsibility |
|---|---|
| `app.py` | Extension + chat declaration, `notion_tokens` secret, health check |
| `notion_client.py` | The single request funnel: headers, version pin, error classification, pagination |
| `notion_objects.py` | Notion JSON → flat text (titles, properties, blocks) and back |
| `workspaces.py` | Tokens → named workspaces; name → object resolution |
| `models.py` | Pydantic params + SDL return entities |
| `handlers_read.py` | 9 read tools |
| `handlers_write.py` | 7 write tools |
| `panels.py` | Workspaces panel (connection state + access explanation) |

## Decisions worth remembering

**Integration tokens, not OAuth.** `ext.oauth` / `ctx.oauth_authorize_url`
support google, microsoft and yahoo only — `notion` raises `ValueError`. So the
connector takes internal integration tokens in a Vault secret. One integration
= one workspace, hence one token per line. The store caches workspace metadata
only; a test asserts no token can reach it.

**API version `2026-03-11`, pinned in one place.** `2025-09-03` split databases
into containers + data sources (rows are parented by `data_source_id`, search
filters on `data_source`); `2026-03-11` renamed `archived` → `in_trash` and
replaced `after` with a `position` object. `_parent_payload` is the only place
that decides page-vs-data-source, so the distinction cannot drift.

**Property values are converted against the real schema.** The user says
`{"Status": "Done"}`; `_build_properties` reads the data source's property
types and builds the right payload. Computed properties (formula, rollup) are
reported back as skipped rather than silently dropped.

**Ambiguity is an error, never a guess.** `resolve_target` refuses to pick when
a name matches several objects — the caller may be about to overwrite one.

**Errors always carry a structured code.** Direct lesson from WP Publisher: an
error without a `code` gets stamped `EXT_UNSTRUCTURED_ERROR` by the kernel, and
validator rule V32 misses it whenever the app routes through a local helper.
Here `_error(message, code)` takes the code as a mandatory positional argument,
and `tests/test_contract.py` walks the AST of every module to prove no error
path can omit it — a check that does not depend on which helper is used.

## Tests — 73, all green

| File | Focus |
|---|---|
| `test_notion_objects.py` | 15 — titles, property types, blocks ↔ text |
| `test_notion_client.py` | 21 — status→code mapping, pagination, no token in errors |
| `test_workspaces.py` | 17 — multi-token, name resolution, no token in the store |
| `test_tools.py` | 14 — end-to-end wire shapes (`data_source_id`, `in_trash`) |
| `test_contract.py` | 6 — AST sweep: structured codes, no leaked exception text |

`test_workspaces.py` caught a real bug during development: the code paged the
store with `page.items`, but `Page[T]` exposes `.data` — the workspace cache
would have raised `AttributeError` in production.

## Status

`imperal validate` → **0 errors, 0 warnings**. 16 tools, 7 events, 2 panels.
