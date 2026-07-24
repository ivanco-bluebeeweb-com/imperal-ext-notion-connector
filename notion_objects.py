"""Turn raw Notion JSON into flat, readable values.

Notion's wire format is deeply nested: a title lives in
``properties -> <the one title property> -> title[] -> plain_text``, and page
content is a tree of typed blocks. The product spec is explicit that reading a
page must yield ACTUAL CONTENT, not just metadata -- so this module is where the
nesting stops and plain text begins.

Pure functions only: no ctx, no I/O, no token ever passes through here. That is
what makes the awkward shapes cheap to test.
"""

from __future__ import annotations

# Block types whose rich_text is rendered as a plain line of text.
_TEXT_BLOCKS = {
    "paragraph", "heading_1", "heading_2", "heading_3",
    "bulleted_list_item", "numbered_list_item", "to_do", "toggle", "quote",
    "callout", "code", "template", "meeting_notes",
}

# Markdown-ish prefixes so a flattened page still reads like the original.
_PREFIX = {
    "heading_1": "# ",
    "heading_2": "## ",
    "heading_3": "### ",
    "bulleted_list_item": "- ",
    "numbered_list_item": "1. ",
    "quote": "> ",
    "to_do": "- [ ] ",
}


def rich_text_to_plain(rich: object) -> str:
    """Flatten a Notion rich_text array to plain text.

    ``plain_text`` is preferred over ``text.content`` because it is the field
    Notion fills for every rich_text variant -- mentions and equations included,
    where ``text.content`` is absent.
    """
    if not isinstance(rich, list):
        return ""
    out: list[str] = []
    for span in rich:
        if not isinstance(span, dict):
            continue
        plain = span.get("plain_text")
        if isinstance(plain, str) and plain:
            out.append(plain)
            continue
        text = span.get("text")
        if isinstance(text, dict) and isinstance(text.get("content"), str):
            out.append(text["content"])
    return "".join(out).strip()


def title_of(obj: dict) -> str:
    """Best-effort human title of a page, data source or database.

    Pages carry their title inside whichever property has type ``title`` (its
    NAME is user-defined -- "Name", "Task", "Ф.И.О." -- so the type is matched,
    never the key). Databases and data sources carry a top-level ``title``.
    """
    if not isinstance(obj, dict):
        return ""

    top = obj.get("title")
    if isinstance(top, list):
        text = rich_text_to_plain(top)
        if text:
            return text

    props = obj.get("properties")
    if isinstance(props, dict):
        for value in props.values():
            if isinstance(value, dict) and value.get("type") == "title":
                text = rich_text_to_plain(value.get("title"))
                if text:
                    return text

    # An untitled object is normal in Notion; say so rather than showing "".
    return "Untitled"


def parent_ref(obj: dict) -> tuple[str, str]:
    """Return (parent_kind, parent_id) for a Notion object.

    Since 2025-09-03 a database row's parent is a DATA SOURCE, while a subpage's
    parent is a page -- the caller needs to know which it got.
    """
    parent = obj.get("parent") if isinstance(obj, dict) else None
    if not isinstance(parent, dict):
        return "", ""
    kind = str(parent.get("type") or "")
    for key in ("page_id", "data_source_id", "database_id", "block_id", "workspace"):
        if key in parent and isinstance(parent[key], str):
            return kind, parent[key]
    if parent.get("workspace") is True:
        return "workspace", ""
    return kind, ""


def property_to_plain(prop: object) -> str:
    """Render one page property value as a short human string.

    Notion has ~25 property types; each nests its value differently. Unknown or
    future types degrade to their type name rather than raising -- a connector
    must not break on a property Notion added last week.
    """
    if not isinstance(prop, dict):
        return ""
    kind = str(prop.get("type") or "")

    if kind in ("title", "rich_text"):
        return rich_text_to_plain(prop.get(kind))
    if kind == "number":
        value = prop.get("number")
        return "" if value is None else str(value)
    if kind == "select":
        sel = prop.get("select")
        return str(sel.get("name", "")) if isinstance(sel, dict) else ""
    if kind == "status":
        st = prop.get("status")
        return str(st.get("name", "")) if isinstance(st, dict) else ""
    if kind == "multi_select":
        items = prop.get("multi_select")
        if isinstance(items, list):
            return ", ".join(str(i.get("name", "")) for i in items
                             if isinstance(i, dict))
        return ""
    if kind == "date":
        date = prop.get("date")
        if not isinstance(date, dict):
            return ""
        start = str(date.get("start") or "")
        end = str(date.get("end") or "")
        return f"{start} -> {end}" if end else start
    if kind == "checkbox":
        return "yes" if prop.get("checkbox") else "no"
    if kind in ("url", "email", "phone_number"):
        return str(prop.get(kind) or "")
    if kind == "people":
        people = prop.get("people")
        if isinstance(people, list):
            return ", ".join(str(p.get("name") or p.get("id", ""))
                             for p in people if isinstance(p, dict))
        return ""
    if kind == "files":
        files = prop.get("files")
        return f"{len(files)} file(s)" if isinstance(files, list) else ""
    if kind == "relation":
        rel = prop.get("relation")
        return f"{len(rel)} linked" if isinstance(rel, list) else ""
    if kind in ("created_time", "last_edited_time"):
        return str(prop.get(kind) or "")
    if kind in ("created_by", "last_edited_by"):
        who = prop.get(kind)
        if isinstance(who, dict):
            return str(who.get("name") or who.get("id", ""))
        return ""
    if kind == "formula":
        formula = prop.get("formula")
        if isinstance(formula, dict):
            inner = formula.get(str(formula.get("type") or ""))
            return "" if inner is None else str(inner)
        return ""
    if kind == "rollup":
        rollup = prop.get("rollup")
        if isinstance(rollup, dict):
            rtype = str(rollup.get("type") or "")
            if rtype == "array" and isinstance(rollup.get("array"), list):
                return ", ".join(filter(None, (property_to_plain(i)
                                               for i in rollup["array"])))
            inner = rollup.get(rtype)
            return "" if inner is None else str(inner)
        return ""
    if kind == "unique_id":
        uid = prop.get("unique_id")
        if isinstance(uid, dict):
            prefix = str(uid.get("prefix") or "")
            number = uid.get("number")
            return f"{prefix}-{number}" if prefix else str(number or "")
        return ""
    if kind == "verification":
        ver = prop.get("verification")
        return str(ver.get("state", "")) if isinstance(ver, dict) else ""
    return kind


def properties_to_plain(props: object) -> dict:
    """Render every property of a page into {name: short string}."""
    if not isinstance(props, dict):
        return {}
    return {name: property_to_plain(value) for name, value in props.items()}


def block_to_text(block: dict) -> str:
    """Render one block as a line of text (empty string if it carries none)."""
    if not isinstance(block, dict):
        return ""
    kind = str(block.get("type") or "")
    payload = block.get(kind)

    if kind == "child_page":
        name = payload.get("title", "") if isinstance(payload, dict) else ""
        return f"[subpage] {name}".rstrip()
    if kind == "child_database":
        name = payload.get("title", "") if isinstance(payload, dict) else ""
        return f"[database] {name}".rstrip()
    if kind == "divider":
        return "---"
    if kind == "table_of_contents":
        return "[table of contents]"
    if kind == "unsupported":
        return "[unsupported block]"

    if kind in ("image", "video", "file", "pdf", "audio"):
        caption = rich_text_to_plain(payload.get("caption")) if isinstance(payload, dict) else ""
        url = ""
        if isinstance(payload, dict):
            inner = payload.get(str(payload.get("type") or ""))
            if isinstance(inner, dict):
                url = str(inner.get("url") or "")
        label = caption or url
        return f"[{kind}] {label}".rstrip()

    if kind in _TEXT_BLOCKS and isinstance(payload, dict):
        text = rich_text_to_plain(payload.get("rich_text"))
        if kind == "to_do":
            mark = "x" if payload.get("checked") else " "
            return f"- [{mark}] {text}".rstrip()
        if kind == "code":
            lang = str(payload.get("language") or "")
            return f"```{lang}\n{text}\n```"
        return f"{_PREFIX.get(kind, '')}{text}".rstrip()

    if isinstance(payload, dict) and "rich_text" in payload:
        return rich_text_to_plain(payload.get("rich_text"))
    return ""


def blocks_to_text(blocks: list, indent: int = 0) -> str:
    """Flatten a block list (with nested children) into readable text."""
    lines: list[str] = []
    pad = "  " * indent
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        text = block_to_text(block)
        if text:
            for line in text.split("\n"):
                lines.append(f"{pad}{line}" if line else "")
        kids = block.get("_children")
        if isinstance(kids, list) and kids:
            nested = blocks_to_text(kids, indent + 1)
            if nested:
                lines.append(nested)
    return "\n".join(lines)


def text_to_blocks(text: str) -> list:
    """Convert plain text into Notion paragraph/heading/list blocks.

    Deliberately small: it covers the shapes a user dictates in chat (headings,
    bullets, checkboxes, paragraphs). Anything else becomes a paragraph, which
    is lossless for the text itself.
    """
    blocks: list = []
    for raw_line in (text or "").split("\n"):
        line = raw_line.rstrip()
        if not line.strip():
            continue
        stripped = line.lstrip()
        kind = "paragraph"
        content = stripped
        extra: dict = {}

        if stripped.startswith("### "):
            kind, content = "heading_3", stripped[4:]
        elif stripped.startswith("## "):
            kind, content = "heading_2", stripped[3:]
        elif stripped.startswith("# "):
            kind, content = "heading_1", stripped[2:]
        elif stripped.startswith("> "):
            kind, content = "quote", stripped[2:]
        elif stripped[:6].lower() in ("- [ ] ", "- [x] "):
            kind = "to_do"
            extra = {"checked": stripped[3].lower() == "x"}
            content = stripped[6:]
        elif stripped.startswith(("- ", "* ")):
            kind, content = "bulleted_list_item", stripped[2:]

        payload = {"rich_text": [{"type": "text", "text": {"content": content}}]}
        payload.update(extra)
        blocks.append({"object": "block", "type": kind, kind: payload})
    return blocks


def build_property_value(kind: str, value):
    """Build a Notion property VALUE payload for a write.

    Callers pass plain Python (a string, number, bool, list) and the data
    source's property TYPE; this puts it in the shape Notion expects.
    """
    kind = (kind or "rich_text").lower()
    if kind == "title":
        return {"title": [{"type": "text", "text": {"content": str(value)}}]}
    if kind == "rich_text":
        return {"rich_text": [{"type": "text", "text": {"content": str(value)}}]}
    if kind == "number":
        try:
            return {"number": float(value) if value not in ("", None) else None}
        except (TypeError, ValueError):
            return None
    if kind == "checkbox":
        if isinstance(value, bool):
            return {"checkbox": value}
        return {"checkbox": str(value).strip().lower() in ("1", "true", "yes", "y", "on")}
    if kind == "select":
        return {"select": {"name": str(value)} if value else None}
    if kind == "status":
        return {"status": {"name": str(value)} if value else None}
    if kind == "multi_select":
        items = value if isinstance(value, list) else [
            part.strip() for part in str(value).split(",") if part.strip()]
        return {"multi_select": [{"name": str(i)} for i in items]}
    if kind == "date":
        if isinstance(value, dict):
            return {"date": value}
        return {"date": {"start": str(value)} if value else None}
    if kind in ("url", "email", "phone_number"):
        return {kind: str(value) if value else None}
    if kind == "people":
        ids = value if isinstance(value, list) else [
            part.strip() for part in str(value).split(",") if part.strip()]
        return {"people": [{"object": "user", "id": str(i)} for i in ids]}
    if kind == "relation":
        ids = value if isinstance(value, list) else [
            part.strip() for part in str(value).split(",") if part.strip()]
        return {"relation": [{"id": str(i)} for i in ids]}
    # Formulas, rollups, unique_id and friends are computed by Notion and
    # cannot be written -- returning None lets the caller skip them cleanly.
    return None
