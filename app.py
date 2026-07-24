"""Extension declaration, secrets, lifecycle hooks.

CONNECTION MODEL -- why integration tokens and not platform OAuth.

The platform's `ext.oauth(...)` flow only knows three providers: `google`,
`microsoft` and `yahoo` (`ctx.oauth_authorize_url` raises ValueError on
anything else). Notion is not among them, so there is no platform-run OAuth
dance to hand this off to.

So the connector uses Notion *internal integration tokens*: the user creates an
integration at notion.so/my-integrations, shares the pages/databases it should
see, and pastes the token here. The product spec requires support for MULTIPLE
workspaces, and a Notion integration is scoped to exactly one workspace -- so
the secret holds ONE TOKEN PER LINE, one line per workspace. Workspace names
and ids are discovered from the API and cached in the store; the tokens
themselves never leave the Vault-encrypted secret.
"""

from imperal_sdk import Extension, ChatExtension

ext = Extension(
    "notion-connector",
    version="1.0.0",
    # Declared so the kernel enforces `tool.required_scopes subset-of declared`
    # instead of falling back to a WILDCARD scope grant (validator V34).
    capabilities=["notion:read", "notion:write"],
    display_name="Notion Connector",
    description=(
        "Read and operate on Notion workspaces: search, browse pages and their "
        "actual block content, query databases, create and update pages, "
        "comment, and manage database records across multiple workspaces."
    ),
    icon="icon.svg",
    actions_explicit=True,
)

chat = ChatExtension(
    ext,
    tool_name="notion",
    description=(
        "Notion Connector -- search a Notion workspace, read page content, query "
        "databases, create and update pages and records, and manage comments."
    ),
)

# Credentials never flow through chat arguments -- the user pastes them into the
# platform Secrets tab (auto-added because the secret is declared here).
# rotation_hint_days mirrors ordinary API-key hygiene; Notion does not expire
# internal integration tokens on its own.
ext.secret(
    "notion_tokens",
    "Notion integration token(s) -- one per line, one line per workspace. "
    "Create at notion.so/my-integrations, then share the pages or databases "
    "the integration should reach.",
    required=True,
    write_mode="user",
    max_bytes=4096,
    rotation_hint_days=180,
)(lambda: None)


@ext.health_check
async def health_check(ctx) -> dict:
    """Liveness probe: report whether at least one Notion token is configured.

    Deliberately does NOT call Notion: a health check must stay fast and must
    not fail because a third party is briefly unreachable. It answers
    "is this app configured", not "is Notion up".
    """
    try:
        raw = await ctx.secrets.get("notion_tokens")
        count = len([ln for ln in (raw or "").splitlines() if ln.strip()])
    except Exception:
        count = 0
    return {
        "healthy": count > 0,
        "tokens_configured": count,
        "detail": ("No Notion integration token configured yet."
                   if count == 0 else f"{count} workspace token(s) configured."),
    }
