"""Unit tests: SeqCode Registry-style etymology tables in project exports."""

from prokname.storage import Candidate, Project, ProjectStore
from prokname.storage.etymology import etymology_row, render_etymology_tables


def _candidate(**overrides) -> Candidate:
    fields = dict(
        name="Klebsiella beijingensis",
        epithet="beijingensis",
        rank="species",
        grammatical_category="adjective",
        gender="f",
        derivation=(
            "stem 'beijing' + -ensis (adjective, formation 'place', "
            "genus Klebsiella is f, lexicon)"
        ),
        compliant=True,
    )
    fields.update(overrides)
    return Candidate(**fields)


def test_adjective_row_uses_genus_gender_for_grammar():
    row = etymology_row(_candidate())
    assert row["grammar"] == "N.L. fem. adj."
    assert row["language"] == "N.L."
    assert "beijing" in row["description"]


def test_genitive_row_does_not_echo_genus_gender():
    row = etymology_row(_candidate(
        name="Shigella boydii",
        epithet="boydii",
        grammatical_category="genitive",
        gender="f",
        derivation="stem 'Boyd' + -ii (genitive noun, honoured person male)",
    ))
    assert row["grammar"] == "N.L. genit. n."


def test_appositive_row():
    row = etymology_row(_candidate(
        name="Bacillus wukongii",
        epithet="wukongii",
        grammatical_category="appositive",
        derivation="stem 'Wukong' as noun in apposition (indeclinable)",
    ))
    assert row["grammar"] == "N.L. n. in app."


def test_participle_is_never_rendered_as_noun_in_apposition():
    """M2: Clostridium perfringens / Streptococcus pyogenes are participles. The
    registration etymology table must say 'part. adj.', not 'n. in app.'."""
    for name, epithet in [
        ("Clostridium perfringens", "perfringens"),
        ("Streptococcus pyogenes", "pyogenes"),
        ("Mycobacterium tuberculosis", "tuberculosis"),
    ]:
        from prokname.engine.validate import validate_agreement

        vr = validate_agreement(name.split()[0], epithet, "feature")
        assert vr.grammatical_category == "participle"
        row = etymology_row(_candidate(
            name=name, epithet=epithet,
            grammatical_category=vr.grammatical_category,
            gender="n", derivation=vr.notes[0] if vr.notes else "",
            compliant=vr.compliant,
        ))
        assert row["grammar"] == "N.L. part. adj."
        assert "in app." not in row["grammar"]


def test_unasserted_category_is_marked_unverified_for_the_registrar():
    """A needs-review analysis must not read as a settled one in the export."""
    row = etymology_row(_candidate(
        name="Escherichia coli", epithet="coli",
        grammatical_category="genitive", compliant=None,
    ))
    assert row["grammar"] == "N.L. genit. n. (unverified)"


def test_blocked_placeholders_and_bare_candidates_render_nothing():
    assert render_etymology_tables([]) == ""
    blocked = _candidate(
        name="Klebsiella [?]", epithet=None, derivation="adjective formation blocked",
    )
    bare = _candidate(epithet=None, derivation="", grammatical_category=None)
    assert render_etymology_tables([blocked, bare]) == ""


def test_render_produces_registry_style_tables():
    text = render_etymology_tables([_candidate()])
    assert "## Etymology tables (SeqCode Registry style" in text
    assert "### *Klebsiella beijingensis*" in text
    assert "| Component | Language | Grammar | Description or derivation |" in text
    assert "**Full word**" in text
    assert "1st morpheme" in text  # morpheme rows left for the author
    assert "not invent" in text


def test_markdown_export_appends_etymology_section(tmp_path):
    store = ProjectStore(base_dir=tmp_path)
    project = store.create("etym-project", target_code="SeqCode")
    project.candidates = [_candidate()]
    store.save(project)
    exported = store.export_markdown("etym-project")
    assert "## Etymology tables (SeqCode Registry style" in exported
    assert "### *Klebsiella beijingensis*" in exported


def test_project_model_roundtrip_unaffected(tmp_path):
    store = ProjectStore(base_dir=tmp_path)
    project = Project(name="roundtrip")
    project.candidates = [_candidate()]
    store.save(project)
    loaded = store.load("roundtrip")
    assert loaded is not None
    assert loaded.candidates[0].name == "Klebsiella beijingensis"
