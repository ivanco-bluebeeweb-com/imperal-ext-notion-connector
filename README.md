# Notion Connector

Read and operate on Notion workspaces from Imperal: search, read real page
content, query databases, create and update pages and rows, comment, and work
across several workspaces at once.

## Connecting

Notion is **not** one of the providers the platform's OAuth flow supports
(`ctx.oauth_authorize_url` accepts google / microsoft / yahoo only), so the
connector uses Notion **internal integration tokens**.

Open the app's **Connect Notion** screen — it walks through all three steps and
links straight to the token field.

1. Open <https://www.notion.so/my-integrations> → **New integration**, pick the
   workspace, and copy its **Internal Integration Secret** (`ntn_...`).
   Choose **Internal**: a public integration is what asks for a redirect URI,
   which this connector does not use.
2. Paste it into `notion_tokens` in the **Secrets** manager (the Connect screen
   has a button that opens it). The field lives there because a
   `write_mode="user"` secret can only be written by Panel UI — never by the
   app's own code.
3. In Notion, open each page or database the connector should reach → **⋯** →
   **Connections** → add the integration. Subpages inherit that access.

**Several workspaces?** A Notion integration belongs to exactly one workspace,
so connect one integration per workspace and paste **one token per line**. The
connector reads each token's workspace name from Notion and lets you say
*"in Acme HQ"*; with a single workspace you never have to name it.

Tokens live only in the Vault-encrypted secret. The app's own store keeps
workspace names and ids — never a token.

## The access model (why something is missing)

The connector sees **exactly what the integration was explicitly shared with**
— never the whole workspace by default. If a page is missing, it almost always
just has not been connected in Notion yet. Run `check_access` (or open the
**Workspaces** panel) for a report of what is currently reachable and what to
do about what is not.

## What it can do

**Reading**

| Tool | What it does |
|---|---|
| `list_workspaces` | Connected workspaces and whether each token still works |
| `search` | Find pages and databases by title |
| `read_page` | Page properties **plus its actual block content** |
| `browse` | Top-level shared objects, or one page's children |
| `list_databases` | Databases the integration can reach |
| `query_database` | Rows of a database, with sorting |
| `list_users` | Workspace members visible to the integration |
| `list_comments` | Unresolved comments on a page |
| `check_access` | What is reachable, and why something is not |

**Writing**

| Tool | What it does |
|---|---|
| `create_page` | New page under a page, or a new row in a database |
| `update_page_content` | Append content to an existing page |
| `update_page` | Set property values (Status, dates, people…) |
| `move_page` | Re-parent a page |
| `trash_page` | Move to Notion's trash (`restore: true` puts it back) |
| `add_comment` | Comment on a page |
| `create_database` | New database with the columns you name |

Each write emits an event (`notion-connector.create_page`, …) so automations
can subscribe.

Files are surfaced as URLs on the pages and properties that hold them; Notion's
file URLs are time-limited, so fetch them soon after reading.

## Notes for maintainers

Pinned to Notion API `2026-03-11`. Two recent breaking changes shape the code:

* **`2025-09-03`** split databases into a *container* (`/v1/databases`) and one
  or more *data sources* (`/v1/data_sources`). Rows are parented by
  `data_source_id`, and search returns `data_source` objects — shown to users
  as "database", which is the word they think in.
* **`2026-03-11`** renamed `archived` → `in_trash` and replaced the flat
  `after` parameter with a `position` object.

Development:

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q     # 73 tests
imperal validate               # 0 errors, 0 warnings
```
