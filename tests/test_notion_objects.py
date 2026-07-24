"""Pure conversion functions: Notion's nested JSON -> flat readable values."""

import notion_objects as no


# --- titles -----------------------------------------------------------------

def test_title_read_from_title_typed_property_whatever_its_name():
    """The title property's NAME is user-defined, so the TYPE is matched."""
    page = {"properties": {
        "Ф.И.О.": {"type": "title",
                    "title": [{"plain_text": "Иван"}]},
        "Notes": {"type": "rich_text", "rich_text": [{"plain_text": "x"}]},
    }}
    assert no.title_of(page) == "Иван"


def test_title_of_database_uses_top_level_title():
    db = {"object": "database", "title": [{"plain_text": "Tasks"}]}
    assert no.title_of(db) == "Tasks"


def test_untitled_object_says_so_rather_than_empty_string():
    assert no.title_of({"properties": {}}) == "Untitled"


def test_rich_text_prefers_plain_text_so_mentions_survive():
    """A mention has no text.content — only plain_text carries its label."""
    rich = [{"type": "mention", "plain_text": "@Vlad"},
            {"type": "text", "text": {"content": " ok"}, "plain_text": " ok"}]
    assert no.rich_text_to_plain(rich) == "@Vlad ok"


# --- parents ----------------------------------------------------------------

def test_row_parent_is_a_data_source_since_2025_09_03():
    obj = {"parent": {"type": "data_source_id",
                       "data_source_id": "ds-1"}}
    assert no.parent_ref(obj) == ("data_source_id", "ds-1")


def test_workspace_parent_is_reported_as_workspace():
    obj = {"parent": {"type": "workspace", "workspace": True}}
    kind, pid = no.parent_ref(obj)
    assert kind == "workspace"


# --- property values --------------------------------------------------------

def test_each_property_type_renders_as_short_text():
    props = {
        "Status": {"type": "select", "select": {"name": "Done"}},
        "Tags": {"type": "multi_select",
                  "multi_select": [{"name": "a"}, {"name": "b"}]},
        "Count": {"type": "number", "number": 42},
        "Ready": {"type": "checkbox", "checkbox": True},
        "Due": {"type": "date", "date": {"start": "2026-07-24"}},
    }
    flat = no.properties_to_plain(props)
    assert flat["Status"] == "Done"
    assert flat["Tags"] == "a, b"
    assert flat["Count"] == "42"
    assert flat["Ready"] == "yes"
    assert "2026-07-24" in flat["Due"]


def test_unknown_property_type_degrades_to_its_type_name_not_a_crash():
    out = no.property_to_plain({"type": "some_future_type",
                                 "some_future_type": {"x": 1}})
    assert isinstance(out, str)


# --- blocks -----------------------------------------------------------------

def test_blocks_flatten_to_readable_text_with_prefixes():
    blocks = [
        {"type": "heading_1", "heading_1": {"rich_text": [{"plain_text": "Title"}]}},
        {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Body"}]}},
        {"type": "bulleted_list_item",
         "bulleted_list_item": {"rich_text": [{"plain_text": "Point"}]}},
    ]
    text = no.blocks_to_text(blocks)
    assert "# Title" in text
    assert "Body" in text
    assert "- Point" in text


def test_meeting_notes_block_is_read_2026_03_11_renamed_transcription():
    block = {"type": "meeting_notes",
             "meeting_notes": {"rich_text": [{"plain_text": "standup"}]}}
    assert "standup" in no.block_to_text(block)


# --- text -> blocks ---------------------------------------------------------

def test_markdown_ish_text_becomes_typed_blocks():
    blocks = no.text_to_blocks("# Head\n- one\n- [x] done\nplain")
    kinds = [b["type"] for b in blocks]
    assert kinds == ["heading_1", "bulleted_list_item", "to_do", "paragraph"]
    assert blocks[2]["to_do"]["checked"] is True


def test_blank_lines_do_not_become_empty_blocks():
    assert no.text_to_blocks("a\n\n\nb") == no.text_to_blocks("a\nb")


# --- property values for writes ---------------------------------------------

def test_computed_property_types_return_none_so_callers_can_skip_them():
    """Formula/rollup are computed by Notion — writing them is an API error."""
    assert no.build_property_value("formula", "x") is None
    assert no.build_property_value("rollup", "x") is None


def test_multi_select_accepts_both_a_list_and_a_comma_string():
    from_list = no.build_property_value("multi_select", ["a", "b"])
    from_str = no.build_property_value("multi_select", "a, b")
    assert from_list == from_str


def test_checkbox_accepts_human_strings():
    assert no.build_property_value("checkbox", "yes")["checkbox"] is True
    assert no.build_property_value("checkbox", "no")["checkbox"] is False
