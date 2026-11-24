"""Tests for the module as built, not as described."""

from __future__ import annotations

import pytest

from code_forge import config_edit
from code_forge.config_edit import (
    Document,
    _has_merge_keys,
    _lost_comments,
    dropped_headers,
    _finish,
    load_document,
    set_fields,
    Guards,
    NotLocatable,
    Refused,
    add_backend,
    addressable,
    locality,
    remove_backend,
    rename_backend,
    set_default,
    set_field,
    set_headers,
)

GATE = 'backends:\n  alpha:\n    type: api\n    model: "a"\n'
TWO = GATE + "  beta:\n    type: api\n    default: true\n"
SPEC = {
    "format": "openai",
    "base_url": "https://b/v1",
    "key_env": "B_KEY",
    "model": "b",
    "max_tokens": 4096,
    "timeout_s": 60,
}


# --- document layer --------------------------------------------------------


def test_a_load_that_changes_nothing_dumps_the_same_bytes():
    doc = Document(GATE)
    assert doc.dumped() == GATE
    assert doc.changed is False


def test_the_line_ending_comes_from_the_input():
    crlf = GATE.replace("\n", "\r\n")
    doc = Document(crlf)
    assert doc.line_ending == "\r\n"
    assert doc.dumped() == crlf


def test_the_span_covers_the_entry_and_stops_at_the_next_key():
    assert Document(TWO).span("alpha") == (1, 3)


# --- locality --------------------------------------------------------------


def test_an_edit_inside_the_entry_is_local():
    after = GATE.replace('"a"', '"z"')
    assert locality(GATE, after, [(1, 3)]) == []


def test_a_file_written_at_four_spaces_is_not_reindented():
    """The loader takes the file's own indent, so a one-field edit stays one
    field and the rest of the block is untouched."""
    before = "backends:\n    alpha:\n        type: api\n    beta:\n        type: api\n"
    doc = load_document(before)
    assert doc.dumped() == before


def test_an_edit_that_reaches_a_neighbours_line_is_not_local():
    """Locality compares the bytes on disk: a change on another entry's line is
    outside the span, whatever produced it."""
    before = "backends:\n  alpha:\n    type: api\n  beta:\n    type: api\n"
    after = "backends:\n  alpha:\n    type: http\n  beta:\n    type: api\n"
    assert locality(before, after, [(1, 2)]) == []
    assert locality(before, after, [(3, 4)]) != []


def test_an_insertion_right_after_the_entry_is_local():
    before = "backends:\n  alpha:\n    type: api\n"
    after = before + "  beta:\n    type: api\n"
    assert locality(before, after, [(1, 2)], insertion_after=3) == []


# --- names -----------------------------------------------------------------


@pytest.mark.parametrize("name", ["yes", "on", "1.5", "null", "", "a: b"])
def test_a_name_the_reader_cannot_address_is_refused(name):
    assert addressable(name) is False


def test_an_ordinary_name_is_addressable():
    assert addressable("alpha-2") is True


# --- mutations -------------------------------------------------------------


def test_adding_writes_the_keys_forge_reads():
    doc = Document(GATE)
    assert add_backend(doc, "beta", SPEC) is True
    entry = doc.data["backends"]["beta"]
    assert entry["api_key_env"] == "B_KEY"
    assert "key_env" not in entry
    assert entry["type"] == "api" and entry["stream"] is False
    reparsed = Document(doc.dumped())
    assert reparsed.data["backends"]["beta"]["model"] == "b"


def test_adding_refuses_a_name_that_cannot_be_addressed():
    doc = Document(GATE)
    with pytest.raises(Refused, match="read back as a string key"):
        add_backend(doc, "yes", SPEC)
    assert doc.dumped() == GATE


def test_a_field_edit_keeps_the_files_own_indent():
    before = "backends:\n    alpha:\n        type: api\n    beta:\n        type: api\n"
    doc = Document(before)
    assert set_field(doc, "alpha", "type", "http") is True
    body = doc.dumped()
    assert any(line.startswith("    alpha:") for line in body.splitlines())
    assert not any(line.startswith("  alpha:") for line in body.splitlines())


def test_adding_a_name_that_exists_is_refused_and_changes_nothing():
    doc = Document(GATE)
    with pytest.raises(Refused):
        add_backend(doc, "alpha", SPEC)
    assert doc.dumped() == GATE


def test_setting_a_field_that_already_holds_the_value_is_a_no_op():
    doc = Document(GATE)
    assert set_field(doc, "alpha", "model", "a") is False
    assert doc.changed is False


def test_setting_a_field_keeps_the_quoting_of_an_untouched_value():
    doc = Document(GATE)
    assert set_field(doc, "alpha", "type", "http") is True
    assert 'model: "a"' in doc.dumped()


def test_headers_are_written_as_a_mapping():
    doc = Document(GATE)
    assert set_headers(doc, "alpha", {"User-Agent": "x/1"}) is True
    assert doc.data["backends"]["alpha"]["headers"]["User-Agent"] == "x/1"
    assert Document(doc.dumped()).data["backends"]["alpha"]["headers"] == {"User-Agent": "x/1"}


def test_setting_the_default_clears_the_other_flag_and_says_so():
    doc = Document(TWO)
    assert set_default(doc, "alpha") is True
    assert "default" not in doc.data["backends"]["beta"]
    assert doc.data["backends"]["alpha"]["default"] is True
    assert doc.notes == ["cleared the default flag on 'beta'"]


def test_renaming_keeps_position_body_and_quoting():
    doc = Document(TWO)
    assert rename_backend(doc, "alpha", "a2") is True
    assert list(doc.data["backends"]) == ["a2", "beta"]
    assert doc.data["backends"]["a2"]["model"] == "a"
    assert '"a"' in doc.dumped()


THREE = GATE + "  beta:\n    type: api\n  gamma:\n    type: api\n"


def test_a_removal_keeps_the_survivor_byte_identical():
    doc = Document(THREE)
    assert remove_backend(doc, "gamma") is True
    assert doc.dumped() == GATE + "  beta:\n    type: api\n"


def test_removing_the_last_entry_is_refused():
    doc = Document(GATE)
    with pytest.raises(Refused, match="leave no backends"):
        remove_backend(doc, "alpha")
    assert "alpha" in doc.data["backends"]


def test_removing_the_default_is_refused():
    doc = Document(TWO)
    with pytest.raises(Refused, match="default"):
        remove_backend(doc, "beta")
    assert "beta" in doc.data["backends"]


def test_a_name_the_user_layer_also_declares_is_removed_when_resolution_holds():
    """The user layer having the same name is not itself a refusal; what
    matters is whether the resolved backend moves."""
    doc = Document(TWO)
    guards = Guards(
        user_backends={"alpha": {"type": "api"}}, resolve=lambda backends, user: (("alpha",), None)
    )
    assert remove_backend(doc, "alpha", guards) is True


def test_a_surviving_declaration_that_keeps_resolving_is_not_a_refusal():
    """The same shape as the user-layer case, without any layer knowledge:
    the module only asks whether the resolved backend moves."""
    doc = Document(TWO)
    guards = Guards(
        user_backends={"alpha": {"type": "api"}}, resolve=lambda backends, user: (("beta",), None)
    )
    assert remove_backend(doc, "alpha", guards) is True


def test_removing_an_entry_that_would_change_the_effective_backend_is_refused():
    doc = Document(TWO)
    guards = Guards(
        resolve=lambda backends, user: (("alpha",) if "alpha" in backends else ("beta",), None)
    )
    with pytest.raises(Refused, match="effective backend"):
        remove_backend(doc, "alpha", guards)


def test_removing_the_anchor_host_is_refused_because_a_survivor_aliases_it():
    text = (
        "backends:\n  alpha: &shared\n    type: api\n    model: m\n"
        "  beta:\n    <<: *shared\n    base_url: https://b/v1\n"
    )
    doc = Document(text)
    with pytest.raises(Refused, match="anchor"):
        remove_backend(doc, "alpha")


def test_a_removal_that_would_hand_its_comment_on_is_refused():
    text = "backends:\n  # alpha mirrors beta\n  alpha:\n    type: api\n  beta:\n    type: api\n"
    doc = Document(text)
    with pytest.raises(Refused, match="comment"):
        remove_backend(doc, "alpha")


def test_a_key_line_comment_goes_with_its_entry_and_is_reported():
    """A comment on the entry's own key line belongs to that entry; the removal
    reports it rather than refusing, because nothing outside the entry loses a
    comment."""
    text = "backends:\n  alpha:\n    type: api\n  beta:  # keep an eye on beta\n    type: api\n"
    doc = Document(text)
    assert remove_backend(doc, "beta") is True
    assert "beta" not in doc.data["backends"]
    assert any("keep an eye on beta" in note for note in doc.notes)


# --- a refusal must not leave the model half-mutated ----------------------


def test_a_refused_set_default_leaves_the_earlier_flag_in_place():
    """The entries before the refusing one are already cleared when it raises.

    Nothing is written on a refusal, but `sync` keeps using the same document
    afterwards, so a half-applied mutation would be written by the next step.
    """
    text = (
        "backends:\n"
        "  alpha:\n    type: api\n    default: true\n"
        "  base: &b\n    type: api\n    default: true\n"
        "  gamma:\n    <<: *b\n    model: g\n"
    )
    doc = Document(text)
    with pytest.raises(Refused, match="anchor"):
        set_default(doc, "gamma")
    assert doc.data["backends"]["alpha"]["default"] is True


def test_a_refused_field_write_does_not_leave_the_value_behind():
    """A flow mapping cannot be located, so the write is refused; the model
    must not keep the field that was never written."""
    doc = Document("backends: {alpha: {type: api}}\n")
    with pytest.raises(NotLocatable):
        set_field(doc, "alpha", "model", "x")
    assert "model" not in doc.data["backends"]["alpha"]


# --- the merge branch is chosen by the model, not by the text -------------


def test_only_a_real_merge_key_puts_the_document_on_the_structural_guard():
    merged = Document("a: &x\n  k: 1\nb:\n  <<: *x\n")
    quoted = Document("b:\n  note: 'see <<: *x in old files'\n")
    commented = Document("b:\n  # was <<: *x once\n  k: 1\n")
    assert _has_merge_keys(merged.data) is True
    assert _has_merge_keys(quoted.data) is False
    assert _has_merge_keys(commented.data) is False


def test_a_comment_that_mentions_the_merge_key_still_gets_the_read_back_guard(monkeypatch):
    """A `<<` in a comment used to switch the value check off for the file."""
    calls = []
    original = config_edit._same_data

    def spy(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(config_edit, "_same_data", spy)
    doc = Document("backends:\n  # ids look like <<: *base here\n  alpha:\n    type: api\n")
    set_field(doc, "alpha", "model", "a")
    assert calls, "the value guard never ran"


# --- two identical comments are two comments ------------------------------


def test_two_identical_comments_lose_one_of_them_visibly():
    before = "backends:\n  alpha:\n    type: api\n    # same note\n"
    after = "backends:\n  alpha:\n    type: api\n"
    assert _lost_comments(before, after) == ["# same note"]
    assert _lost_comments("# same note\n# same note\n", "# same note\n") == ["# same note"]


def test_a_refused_mutation_leaves_the_notes_as_they_were():
    text = (
        "backends:\n"
        "  alpha:\n    type: api\n    default: true\n"
        "  base: &b\n    type: api\n    default: true\n"
        "  gamma:\n    <<: *b\n    model: g\n"
    )
    doc = Document(text)
    doc.notes.append("written before the refusal")
    with pytest.raises(Refused):
        set_default(doc, "gamma")
    assert doc.notes == ["written before the refusal"]


def test_a_refused_removal_does_not_materialise_an_empty_entry():
    """`beta:` with no body is read as an empty mapping by the mutation layer;
    a refusal must not leave that mapping in the document."""
    text = "backends:\n  beta:\n"
    doc = Document(text)
    with pytest.raises(Refused, match="no backends"):
        remove_backend(doc, "beta")
    assert doc.data["backends"]["beta"] is None


def test_a_hash_inside_a_block_scalar_is_data_not_a_comment():
    from code_forge.config_edit import _comments_in_text

    text = "note: |\n  # not a comment\n  still data\nkey: 1\n"
    assert _comments_in_text(text) == []
    assert _comments_in_text("key: 1  # a real comment\n") == ["# a real comment"]


def test_two_identical_comments_that_stay_put_are_not_reported_as_moved():
    """Removing one entry renumbers the survivor's occurrence; that is not a move."""
    text = (
        "backends:\n"
        "  alpha:\n    type: api\n    # same\n"
        "  beta:\n    type: api\n    # same\n    default: true\n"
    )
    doc = Document(text)
    assert remove_backend(doc, "alpha") is True
    assert not [note for note in doc.notes if "move comment" in note]


# --- guards at their install points ---------------------------------------


def test_a_change_outside_the_span_is_refused_where_the_mutation_ends():
    """Pins the call site: `locality` is only useful if `_finish` runs it."""
    text = GATE + "  beta:\n    type: api\n"
    doc = Document(text)
    before = doc.dumped()
    doc.data["backends"]["beta"]["type"] = "http"
    with pytest.raises(Refused, match="outside"):
        _finish(doc, before, [doc.span("alpha")])


def test_writing_a_field_into_an_aliased_entry_is_refused():
    text = "backends:\n  alpha: &shared\n    type: api\n  beta: *shared\n"
    doc = Document(text)
    with pytest.raises(Refused, match="shares its mapping"):
        set_field(doc, "beta", "type", "http")


def test_writing_headers_into_an_aliased_entry_is_refused():
    text = "backends:\n  alpha: &shared\n    type: api\n  beta: *shared\n"
    doc = Document(text)
    with pytest.raises(Refused, match="shares its mapping"):
        set_headers(doc, "beta", {"X-A": "1"})


def test_a_comment_that_would_move_to_the_next_entry_is_refused():
    text = (
        "backends:\n  alpha:\n    type: api\n"
        "  # this describes beta\n  beta:\n    type: api\n"
        "  gamma:\n    type: api\n"
    )
    doc = Document(text)
    with pytest.raises(Refused, match="move comment"):
        remove_backend(doc, "beta")


def test_a_file_with_mixed_line_endings_is_refused():
    text = "backends:\r\n  alpha:\r\n    type: api\n"
    doc = Document(text)
    with pytest.raises(Refused, match="mixes CRLF"):
        set_field(doc, "alpha", "type", "http")


def test_adding_into_an_empty_block_works():
    doc = Document("backends: {}\n")
    assert add_backend(doc, "beta", SPEC) is True
    assert "beta" in Document(doc.dumped()).data["backends"]


def test_several_fields_are_aligned_in_one_write():
    text = (
        "backends:\n  alpha:\n    type: api\n    model: old\n"
        "    base_url: https://old/v1\n  beta:\n    type: api\n"
    )
    doc = Document(text)
    changed = set_fields(doc, "alpha", {"model": "new", "base_url": "https://new/v1"})
    assert changed == ["model", "base_url"]
    body = doc.dumped()
    assert "model: new" in body and "https://new/v1" in body
    assert body.count("  alpha:") == 1


def test_fields_the_backend_does_not_carry_are_not_invented():
    doc = Document(GATE)
    assert set_fields(doc, "alpha", {"max_tokens": 5}) == []
    assert "max_tokens" not in doc.dumped()


def test_a_refused_multi_field_write_leaves_the_document_alone():
    mixed = "backends:\r\n  alpha:\r\n    type: api\n    model: a\r\n"
    doc = Document(mixed)
    baseline = doc.dumped()
    with pytest.raises(Refused, match="mixes CRLF"):
        set_fields(doc, "alpha", {"model": "z"})
    assert doc.dumped() == baseline


def test_a_root_that_is_not_a_mapping_is_refused_not_crashed():
    doc = Document("- x\n- y\n")
    with pytest.raises(Refused, match="not a mapping"):
        set_field(doc, "alpha", "model", "m")


def test_a_flow_block_is_declared_but_not_addressable():
    """The file parses and declares the entry; the refusal must not claim
    otherwise, because callers report "not declared" as a skipped file."""
    from code_forge.config_edit import NotLocatable

    doc = Document("backends: {alpha: {type: api}, beta: {type: api}}\n")
    with pytest.raises(NotLocatable):
        remove_backend(doc, "alpha")


def test_aligning_fields_refuses_an_aliased_entry():
    doc = Document("backends:\n  alpha: &shared\n    type: api\n  beta: *shared\n")
    with pytest.raises(Refused, match="shares its mapping"):
        set_fields(doc, "beta", {"type": "http"})


def test_a_value_that_reads_back_the_same_is_not_rewritten():
    """`1.0` is a string in the file and a float on the command line; an
    idempotent set must not rewrite, back up and re-seal for that."""
    doc = Document("backends:\n  alpha:\n    type: api\n    max_tokens: 1.0\n")
    assert set_fields(doc, "alpha", {"max_tokens": 1.0}) == []
    assert set_fields(doc, "alpha", {"max_tokens": "1.0"}) == []


def test_a_long_neighbour_scalar_survives_an_edit():
    """ruamel's default 80-column width re-wrapped long scalars, so an edit to
    one entry was refused for touching the next one."""
    long_ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) " * 2
    text = (
        f"backends:\n  alpha:\n    type: api\n    model: a\n"
        f"  beta:\n    type: api\n    headers:\n      User-Agent: {long_ua!r}\n"
    )
    doc = Document(text)
    assert set_fields(doc, "alpha", {"model": "b"}) == ["model"]
    body = doc.dumped()
    assert long_ua in body
    assert body.count("User-Agent") == 1


def test_headers_that_are_not_a_mapping_are_refused():
    doc = Document("backends:\n  alpha:\n    type: api\n    headers: none\n")
    with pytest.raises(Refused, match="not a mapping"):
        set_headers(doc, "alpha", {"X-A": "1"})


def test_an_int_field_stored_as_a_float_is_still_a_change():
    """`timeout_s: 2400.0` and `--timeout-s 2400` are not the same setting: the
    timeout reader takes an exact int and refuses the float."""
    doc = Document("backends:\n  alpha:\n    type: api\n    timeout_s: 2400.0\n")
    assert set_fields(doc, "alpha", {"timeout_s": 2400}) == ["timeout_s"]
    assert "timeout_s: 2400\n" in doc.dumped()


def test_a_field_the_entry_lacks_can_be_added_by_set():
    doc = Document("backends:\n  alpha:\n    type: api\n")
    assert set_fields(doc, "alpha", {"max_tokens": 99}, add_missing=True) == ["max_tokens"]
    assert "max_tokens: 99" in doc.dumped()


def test_sync_style_alignment_leaves_a_missing_field_missing():
    doc = Document("backends:\n  alpha:\n    type: api\n")
    assert set_fields(doc, "alpha", {"max_tokens": 99}) == []
    assert "max_tokens" not in doc.dumped()


def test_a_comment_at_entry_indent_that_ruamel_keeps_inside_is_part_of_it():
    """ruamel attaches a comment at the entry's own indent to the entry when
    the next thing below is deeper, so a span that stops at the comment makes
    every edit look like it reached outside."""
    text = (
        "backends:\n  alpha:\n    type: api\n  # inside alpha's mapping\n"
        "    model: a\n  beta:\n    type: api\n"
    )
    doc = Document(text)
    assert set_fields(doc, "alpha", {"model": "z"}) == ["model"]
    assert "# inside alpha's mapping" in doc.dumped()


def test_a_file_whose_levels_step_by_different_amounts_still_edits():
    """The dumper has one mapping indent, so such a file comes back re-indented.
    Refusing it left a third of this host's configs uneditable, and
    re-indentation changes no setting."""
    text = "backends:\n  alpha:\n    type: api\n    headers:\n        User-Agent: x\n"
    doc = Document(text)
    assert set_fields(doc, "alpha", {"type": "http"}) == ["type"]
    body = doc.dumped()
    assert "type: http" in body and "User-Agent: x" in body


def test_a_neighbour_reindented_by_the_dump_is_reported_not_refused():
    """The re-indent can land on a neighbour; the edit still goes through and
    the note says what moved."""
    text = (
        "backends:\n  alpha:\n    type: api\n  beta:\n    type: api\n"
        "    headers:\n        User-Agent: x\n"
    )
    doc = Document(text)
    assert set_fields(doc, "alpha", {"type": "http"}) == ["type"]
    assert any("re-formatted" in note for note in doc.notes)


def test_a_header_key_that_is_not_text_is_reported_as_text():
    """YAML happily makes `1:` an integer key; the report joins names, so they
    have to be strings or the join raises after the file was written."""
    doc = Document("backends:\n  alpha:\n    type: api\n    headers:\n      1: x\n")
    assert dropped_headers(doc, "alpha", {"X-A": "1"}) == ["1"]


def test_a_top_level_scalar_change_outside_the_entry_is_refused():
    """An earlier escape compared the containing section's parsed data and
    accepted this: the changed line sat under a key the scan mis-attributed."""
    text = "outlet: subprocess\nbackends:\n  alpha:\n    type: api\n"
    doc = Document(text)
    baseline = doc.dumped()
    doc.data["outlet"] = "inline"
    with pytest.raises(Refused, match="outside"):
        _finish(doc, baseline, [doc.span("alpha")])


def test_a_numeric_string_or_float_is_rewritten_to_an_int():
    """The readers take a real int; `"2400"` and `2400.0` both fail to load, so
    writing the int is the repair and must not be skipped as "no change"."""
    for stored in ('"2400"', "2400.0"):
        doc = Document(f"backends:\n  alpha:\n    type: api\n    timeout_s: {stored}\n")
        assert set_fields(doc, "alpha", {"timeout_s": 2400}) == ["timeout_s"]
        assert "timeout_s: 2400\n" in doc.dumped()


def test_a_block_scalar_next_to_a_reindent_keeps_its_value():
    import yaml as _yaml

    """The tolerance may normalise indentation, but a block scalar's value has
    to survive it; the whole-document value comparison is the guard."""
    text = (
        "backends:\n  alpha:\n    type: api\n  beta:\n    type: api\n"
        "    headers:\n        User-Agent: x\n    prompt: |\n      hello\n"
    )
    doc = Document(text)
    assert set_fields(doc, "alpha", {"type": "http"}) == ["type"]
    body = doc.dumped()
    assert "prompt: |" in body
    assert "hello" in body
    assert _yaml.safe_load(body)["backends"]["beta"]["prompt"] == "hello\n"


def test_renaming_onto_an_existing_name_is_refused_and_changes_nothing():
    text = "backends:\n  alpha:\n    type: api\n  beta:\n    type: api\n"
    doc = Document(text)
    with pytest.raises(Refused, match="already declared"):
        rename_backend(doc, "alpha", "beta")
    assert doc.dumped() == text
    assert list(doc.data["backends"]) == ["alpha", "beta"]


def test_a_backends_block_that_is_not_a_mapping_is_refused():
    doc = Document("backends: nonsense\n")
    with pytest.raises(Refused, match="no backends"):
        set_field(doc, "alpha", "model", "m")


def test_a_null_sibling_entry_is_skipped_when_moving_the_default():
    text = "backends:\n  alpha:\n    type: api\n  beta:\n  gamma:\n    type: api\n"
    doc = Document(text)
    assert set_default(doc, "alpha") is True
    body = doc.dumped()
    assert "default: true" in body


def test_a_respaced_flow_collection_is_tolerated_but_a_rewritten_value_is_not():
    """The tolerance's oracle is the reader: `[a,b]` -> `[a, b]` means the same
    thing to PyYAML, `!!float 1e3` -> `1e3` does not (it becomes a string)."""
    from code_forge.config_edit import _region_same_reading

    assert _region_same_reading("[a,b]", "[a, b]") is True
    assert _region_same_reading("timeout_s: !!float 1e3", "timeout_s: 1e3") is False
    assert _region_same_reading("a: 1", "a: true") is False
    assert _region_same_reading("a: [1]", "a: [1, 2]") is False
    # dedent removes the shared prefix, not the structure: a sibling and a
    # nested mapping are different documents
    assert _region_same_reading("  a:\n  b: 1", "a:\n  b: 1") is False
    assert _region_same_reading("  a: 1\n  b: 2", "a: 1\nb: 2") is True

    text = "presubmit:\n  - command: [python3, --select=E,W]\nbackends:\n  alpha:\n    type: api\n"
    doc = Document(text)
    assert set_fields(doc, "alpha", {"type": "http"}) == ["type"]
    assert "[python3, --select=E, W]" in doc.dumped()
    assert doc.notes, "a tolerated region must be reported"


def test_a_removal_beside_a_reindented_neighbour_goes_through():
    """Deleting an entry re-indents the neighbour ruamel keeps in the same
    block; that is a move, not an edit, and the removal must not be refused."""
    text = (
        "backends:\n  alpha:\n    type: api\n    headers:\n"
        "        x-a: '1'\n        x-b: '2'\n  beta:\n    type: api\n    model: b\n"
    )
    doc = Document(text)
    assert remove_backend(doc, "beta") is True
    body = doc.dumped()
    assert "beta" not in body and "alpha" in body
    assert "x-a" in body and "x-b" in body


def test_a_removal_that_would_change_another_backend_is_not_tolerated():
    """The fallback only covers the entries the mutation declared."""
    from code_forge.config_edit import _only_backends_moved

    before = "backends:\n  alpha:\n    type: api\n  beta:\n    type: api\n"
    dropped = "backends:\n  beta:\n    type: api\n"
    changed = "backends:\n  alpha:\n    type: api\n  beta:\n    type: http\n"
    moved_outside = "top: 1\nbackends:\n  alpha:\n    type: api\n"
    assert _only_backends_moved(before, dropped, [(1, 2)]) is True
    assert _only_backends_moved(before, changed, [(1, 2)]) is False
    assert _only_backends_moved(before, moved_outside, [(1, 2)]) is False


def test_a_document_with_many_untouched_keys_still_edits():
    """The size of a document is not a reason to refuse: only the number of
    differing paths is, and an edit has one."""
    text = "backends:\n  alpha:\n    type: api\n  beta:\n    type: api\n" + "".join(
        f"pad{i}: {i}\n" for i in range(3000)
    )
    doc = Document(text)
    assert remove_backend(doc, "beta") is True
    assert "beta" not in doc.dumped()


def test_a_deep_document_with_many_leaves_still_answers():
    """The work budget must not be so tight that a legal deep document is refused."""
    from code_forge.config_edit import _changed_paths

    def nested(depth, leaves, leaf):
        node = {f"k{i}": leaf for i in range(leaves)}
        for _ in range(depth):
            node = {"n": node}
        return node

    found = _changed_paths(nested(20, 3000, 1), nested(20, 3000, 2))
    assert len(found) == 3000


def test_a_field_write_into_the_anchor_source_is_allowed():
    """The merge view of the siblings is a load-time snapshot, so a write into
    the anchor's own entry used to look like a change the file did not hold."""
    text = (
        "backends:\n  base: &base\n    default: true\n    type: api\n"
        "  alpha:\n    <<: *base\n    type: api\n"
    )
    doc = Document(text)
    assert set_field(doc, "base", "timeout_s", "0") is True
    dumped = doc.dumped()
    assert "timeout_s" in dumped
    assert "<<: *base" in dumped


def test_an_anchor_shared_inside_backends_is_refused_not_crashed():
    """When the anchor source is itself a backend entry, removing its `default`
    raised ruamel's KeyError mid-sweep, after earlier files were written."""
    text = (
        "backends:\n  base: &base\n    default: true\n    type: api\n"
        "  alpha:\n    <<: *base\n    type: api\n"
    )
    doc = Document(text)
    with pytest.raises(Refused, match="anchor"):
        set_default(doc, "alpha")
    assert "default: true" in doc.dumped()


def test_default_inherited_from_a_merge_key_is_refused_with_the_reason():
    """`default` that comes from `<<: *base` cannot be cleared on the entry."""
    text = (
        "defaults: &base\n  default: true\nbackends:\n  alpha:\n    type: api\n"
        "  beta:\n    <<: *base\n    type: api\n"
    )
    doc = Document(text)
    with pytest.raises(Refused, match="merge key"):
        set_default(doc, "alpha")


def test_a_nesting_deeper_than_any_configuration_is_refused_not_recursed():
    from code_forge.config_edit import _TooManyPaths, _changed_paths

    deep_left = {"leaf": 1}
    deep_right = {"leaf": 2}
    for _ in range(200):
        deep_left = {"n": deep_left}
        deep_right = {"n": deep_right}
    with pytest.raises(_TooManyPaths):
        _changed_paths(deep_left, deep_right)


def test_the_block_is_found_through_a_multiline_quoted_scalar():
    """A `backends:` line inside a quoted scalar is text, not the key: the parse
    knows where the real one is and the text scan cannot."""
    text = (
        'notes: "line one\nbackends:\n  fake: x\n"\n'
        "backends:\n  alpha:\n    type: api\n  gamma:\n    type: api\n"
    )
    doc = Document(text)
    start, _end = doc.span("gamma")
    assert text.splitlines()[start] == "  gamma:"


def test_a_file_with_two_indent_steps_says_what_it_reflowed():
    """Two styles cannot both survive a dump, so the note must say the rest of
    the file moved instead of claiming the write matched the file's style."""
    text = "other:\n    wide: 1\nbackends:\n  alpha:\n    type: api\n  gamma:\n    type: api\n"
    doc = Document(text)
    assert set_field(doc, "alpha", "timeout_s", "10") is True
    assert any("more than one step" in note for note in doc.notes), doc.notes


def test_a_nested_backends_lookalike_does_not_move_the_entries():
    """A `backends:` inside another key (or a block scalar) is not the map, and
    taking it puts every span in the wrong place."""
    text = (
        "example:\n  backends:\n    fake: {type: api}\n"
        "backends:\n  alpha:\n    type: api\n  gamma:\n    type: api\n"
    )
    doc = Document(text)
    start, end = doc.span("gamma")
    assert start >= 4, (start, end)
    assert "gamma" in text.splitlines()[start]


def test_a_wide_alias_fan_out_stops_before_allocating():
    """Charging the budget after building the list let 12 000 aliases allocate
    over two gigabytes before the refusal; it is charged as paths are added."""
    from code_forge.config_edit import _TooManyPaths, _changed_paths

    def chain(depth, leaf):
        node = {"leaf": leaf}
        for _ in range(depth):
            node = {"a": node, "b": node}
        return node

    shared_left = chain(12, 1)
    shared_right = chain(12, 2)
    left = {f"k{i}": shared_left for i in range(3000)}
    right = {f"k{i}": shared_right for i in range(3000)}
    with pytest.raises(_TooManyPaths):
        _changed_paths(left, right)


def test_a_document_that_would_cost_more_work_than_the_budget_is_refused():
    """3 000 leaves nested 70 deep is 210 000 paths produced while walking:
    above the 200 000 work budget, below the 10 000 reported-path limit."""
    from code_forge.config_edit import _TooManyPaths, _changed_paths

    def nested(depth, leaves, leaf):
        node = {f"k{i}": leaf for i in range(leaves)}
        for _ in range(depth):
            node = {"n": node}
        return node

    with pytest.raises(_TooManyPaths):
        _changed_paths(nested(70, 3000, 1), nested(70, 3000, 2))


def test_too_many_differences_inside_the_backends_map_is_a_refusal():
    """The other `_TooManyPaths` handler: a walk of the backends map that cannot
    finish must answer "not local", not "nothing changed"."""
    from code_forge.config_edit import _only_backends_moved, _TooManyPaths

    def entry(value):
        body = "".join(f"    k{i}: {i + value}\n" for i in range(10_500))
        return "backends:\n  alpha:\n    type: api\n" + body

    with pytest.raises(_TooManyPaths):
        _only_backends_moved(entry(0), entry(1), [(1, 2)])


def test_too_many_differences_outside_the_entries_is_a_refusal():
    """The `_TooManyPaths` handler must answer "not local", not "no difference"."""
    from code_forge.config_edit import _only_backends_moved, _TooManyPaths

    before = "backends:\n  alpha:\n    type: api\n" + "".join(f"k{i}: {i}\n" for i in range(10_500))
    after = "backends:\n  alpha:\n    type: api\n" + "".join(f"k{i}: {i + 1}\n" for i in range(10_500))
    with pytest.raises(_TooManyPaths):
        _only_backends_moved(before, after, [(1, 2)])


def test_a_structure_with_more_differing_paths_than_the_limit_is_refused():
    from code_forge.config_edit import _TooManyPaths, _changed_paths

    def symmetric(depth, leaf):
        node = {"leaf": leaf}
        for _ in range(depth):
            node = {"a": dict(node), "b": dict(node)}
        return node

    with pytest.raises(_TooManyPaths):
        _changed_paths(symmetric(13, 1), symmetric(13, 2), limit=100)

    # few differences, many nodes: reported, not refused
    assert _changed_paths(symmetric(13, 1), symmetric(13, 1), limit=100) == []


def test_a_shared_structure_too_large_to_enumerate_is_reported_as_such():
    """Every key that reaches a shared node is a separate path, so an alias
    chain has exponentially many. Enumerating them is not an answer: the walk
    stops and says so, and the caller refuses."""
    from code_forge.config_edit import _TooManyPaths, _changed_paths

    left = {"leaf": 1}
    for _ in range(22):
        left = {"a": left, "b": left}
    right = {"leaf": 2}
    for _ in range(22):
        right = {"a": right, "b": right}
    with pytest.raises(_TooManyPaths):
        _changed_paths(left, right)

    # every differing path is reported when they are few, even when the values
    # are the same object (a small int, an alias)
    assert _changed_paths({"a": 1, "b": 1}, {"a": 2, "b": 2}) == [("a",), ("b",)]


def test_a_cyclic_document_is_refused_rather_than_crashing():
    """A self-referential alias must not reach the recursion limit."""
    from code_forge.config_edit import _only_backends_moved

    text = "backends:\n  alpha:\n    type: api\n    loop: &loop [*loop]\n  beta:\n    type: api\n"
    assert isinstance(_only_backends_moved(text, "backends:\n  beta:\n    type: api\n", [(1, 3)]), bool)


def test_a_removal_that_would_flatten_a_recursive_alias_is_refused():
    """`&c [*c]` re-emitted as `[null]` in a survivor is a value loss, and the
    anchor-only rewrite is not an excuse for it."""
    text = "backends:\n  alpha:\n    type: api\n  beta:\n    type: api\n    loop: &loop [*loop]\n"
    doc = Document(text)
    with pytest.raises(Refused):
        remove_backend(doc, "alpha")


def test_the_reader_comparison_is_linear_in_the_alias_depth():
    """Without the memo an alias chain is exponential: depth 22 took 9-16 s."""
    import time

    from code_forge.config_edit import _reading_equal

    data = {"leaf": 1}
    for _ in range(22):
        data = {"a": data, "b": data}
    started = time.monotonic()
    assert _reading_equal(data, data) is True
    assert time.monotonic() - started < 2.0


def test_the_backends_block_is_found_however_its_key_is_spelled():
    """`"backends":`, `backends :` and `backends:  # pool` are all legal."""
    from code_forge.config_edit import _only_backends_moved

    for opening in ("backends:", '"backends":', "backends :", "backends:  # pool"):
        before = f"{opening}\n  alpha:\n    type: api\n  beta:\n    type: api\n"
        after = f"{opening}\n  beta:\n    type: api\n"
        assert _only_backends_moved(before, after, [(1, 2)]) is True, opening


def test_the_read_back_predicate_separates_types_and_tags():
    """The end-to-end shape is unreachable on ruamel 0.19.1 (it keeps tags and
    scalar text), so the predicate is what is pinned: a type or tag difference
    is a difference, an equal value is not."""
    from code_forge.config_edit import _same_data

    assert _same_data("1", 1) is False
    assert _same_data(1, 1.0) is False
    assert _same_data(True, 1) is False
    assert _same_data({"a": "1"}, {"a": 1}) is False
    assert _same_data({"a": [1, "2"]}, {"a": [1, "2"]}) is True


def test_a_tagged_scalar_survives_an_unrelated_edit():
    """ruamel keeps the tag through a dump, so the guard must not refuse it."""
    text = "backends:\n  alpha:\n    type: api\n    command: !!str |2\n        line one\n"
    doc = Document(text)
    assert set_fields(doc, "alpha", {"type": "http"}) == ["type"]
    assert "!!str" in doc.dumped()


def test_a_write_that_keeps_every_value_is_allowed():
    text = "backends:\n  alpha:\n    type: api\n    command: |\n      line one\n"
    doc = Document(text)
    assert set_fields(doc, "alpha", {"type": "http"}) == ["type"]
    assert "line one" in doc.dumped()
