"""Storage module tests: project management and export (FR-08).

Covers the review defects in the export/durability chain:

* A stored dedup verdict must carry the query and timestamp it came from;
* Markdown cells must survive '|', '[', ']' and newlines, and the CSV's
        '#' preamble must be readable by the invocation printed in the file;
* Saving must not downgrade a 0644 project file to mkstemp's 0600;
* Every write goes through the shared atomic writer.
"""

import csv
import io
import json
import os
import stat

import pytest

from prokname.storage import Candidate, ProjectStore
from prokname.storage.store import (
    CSV_FIELDS,
    CSV_READ_HINT,
    md_escape_cell,
    md_unescape_cell,
    split_md_row,
)

# a name/notes payload with everything that used to corrupt the exports
NASTY_NAME = "Wukomonas beijingensis [?] | ünö\nsecond line"
NASTY_NOTES = "see | note [x]\nünïcøde #7"


def _candidate(**overrides):
    base = dict(
        name="Bacillus wukongus", epithet="wukongus", rank="species",
        grammatical_category="adjective", gender="m",
        derivation="stem + -us", compliant=True,
    )
    base.update(overrides)
    return Candidate(**base)


# The mode contract asserted below is a POSIX contract: Windows has no per-owner
# mode bits — os.chmod there only toggles the read-only attribute — so comparing
# 0o600 against 0o644 would test nothing about this code. These cases run on Linux
# and macOS; skipping them elsewhere is about the platform not having the feature,
# not about an optional dependency being absent.
POSIX_ONLY = pytest.mark.skipif(
    os.name != "posix",
    reason="no per-owner mode bits on this platform; the preserved-mode contract is POSIX-only",
)

def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _csv_body(exported: str) -> str:
    """The exported CSV minus its '#' preamble (i.e. ``comment='#'``)."""
    return "".join(
        line + "\n" for line in exported.splitlines() if not line.startswith("#")
    )


def _csv_header(exported: str) -> list[str]:
    return next(csv.reader(io.StringIO(_csv_body(exported))))


def _csv_rows(exported: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(_csv_body(exported))))


def _candidate_table(markdown: str) -> list[str]:
    """Lines of the candidate table only (header + separator + data rows).

    The Markdown export also carries the SeqCode-style etymology tables below
    it, whose rows must not be mistaken for candidate rows.
    """
    lines = markdown.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("| Name |"))
    table = [lines[start], lines[start + 1]]
    for line in lines[start + 2:]:
        if not line.startswith("|"):
            break
        table.append(line)
    return table


@pytest.fixture
def store(tmp_path):
    """A ProjectStore with a temporary base directory."""
    return ProjectStore(base_dir=tmp_path / "projects")


class TestProjectStore:

    def test_create_and_load(self, store):
        project = store.create("test-project", data_source="MAG", target_code="SeqCode")
        assert project.name == "test-project"
        assert project.data_source == "MAG"
        assert project.target_code == "SeqCode"

        loaded = store.load("test-project")
        assert loaded is not None
        assert loaded.name == "test-project"
        assert loaded.data_source == "MAG"

    def test_load_nonexistent(self, store):
        assert store.load("nonexistent") is None

    def test_list_projects(self, store):
        store.create("project-a")
        store.create("project-b")
        names = store.list_projects()
        assert "project-a" in names
        assert "project-b" in names

    def test_delete(self, store):
        store.create("to-delete")
        assert store.delete("to-delete") is True
        assert store.load("to-delete") is None
        assert store.delete("to-delete") is False

    def test_add_candidate(self, store):
        store.create("test")
        candidate = Candidate(
            name="Bacillus wukongus",
            epithet="wukongus",
            rank="species",
            grammatical_category="adjective",
            gender="m",
            derivation="test",
            compliant=True,
        )
        store.add_candidate("test", candidate)
        loaded = store.load("test")
        assert len(loaded.candidates) == 1
        assert loaded.candidates[0].name == "Bacillus wukongus"

    def test_rate_candidate(self, store):
        store.create("test")
        candidate = Candidate(
            name="Bacillus wukongus", epithet="wukongus", rank="species",
            grammatical_category="adjective", gender="m",
            derivation="test", compliant=True,
        )
        store.add_candidate("test", candidate)
        store.rate_candidate("test", "Bacillus wukongus", 5)
        loaded = store.load("test")
        assert loaded.candidates[0].score == 5

    def test_rate_nonexistent_candidate(self, store):
        store.create("test")
        with pytest.raises(ValueError, match="not found"):
            store.rate_candidate("test", "Nonexistent", 5)


class TestExport:

    @pytest.fixture
    def project_with_candidates(self, store):
        store.create("export-test", data_source="MAG", target_code="SeqCode")
        store.add_candidate("export-test", _candidate())
        store.add_candidate("export-test", _candidate(
            name="Bacillus wukong", epithet="wukong", rank="species",
            grammatical_category="appositive", gender="m",
            derivation="appositive", compliant=True,
        ))
        return store

    def test_export_json_has_compliance_annotations(self, project_with_candidates):
        exported = project_with_candidates.export_json("export-test")
        data = json.loads(exported)
        assert "disclaimer" in data
        assert "tool" in data
        assert "attribution" in data
        assert "LPSN" in data["attribution"]
        assert "CC BY-SA 4.0" in data["attribution"]["LPSN"]
        assert "SeqCode Registry" in data["attribution"]
        assert "CC-BY 4.0" in data["attribution"]["SeqCode Registry"]

    def test_export_csv_has_compliance_header(self, project_with_candidates):
        exported = project_with_candidates.export_csv("export-test")
        lines = exported.strip().split("\n")
        # First lines are compliance comments
        assert any("prokname v" in line for line in lines[:6])
        assert any("CC BY-SA 4.0" in line for line in lines[:6])
        assert any("CC-BY 4.0" in line for line in lines[:6])
        # the header row follows the comments and starts with the name column
        assert _csv_header(exported)[0] == "name"

    def test_export_csv_documents_how_to_read_it(self, project_with_candidates):
        """the preamble used to break naive readers silently — the exact
        invocation is now spelled out in the first line of the file itself."""
        exported = project_with_candidates.export_csv("export-test")
        first_line = exported.split("\n", 1)[0]
        assert first_line.startswith("#")
        assert CSV_READ_HINT in first_line
        assert "comment='#'" in first_line

    def test_export_csv_is_readable_by_the_documented_invocation(
        self, project_with_candidates
    ):
        exported = project_with_candidates.export_csv("export-test")
        rows = _csv_rows(exported)
        assert [r["name"] for r in rows] == ["Bacillus wukongus", "Bacillus wukong"]
        assert list(rows[0]) == CSV_FIELDS

    def test_export_csv_provenance_false_is_bare_csv(self, project_with_candidates):
        """The machine-readable escape hatch: no preamble at all."""
        exported = project_with_candidates.export_csv(
            "export-test", provenance=False
        )
        assert not any(line.startswith("#") for line in exported.splitlines())
        assert exported.splitlines()[0].startswith('"name"')

    @pytest.mark.parametrize("provenance", [True, False])
    def test_export_csv_pandas_round_trip(
        self, project_with_candidates, tmp_path, provenance
    ):
        """The documented invocation works in the tool the docs name."""
        pandas = pytest.importorskip("pandas")
        text = project_with_candidates.export_csv(
            "export-test", provenance=provenance
        )
        path = tmp_path / "export.csv"
        path.write_text(text, encoding="utf-8")
        frame = pandas.read_csv(path, comment="#")
        assert list(frame["name"]) == ["Bacillus wukongus", "Bacillus wukong"]
        assert list(frame["compliant"]) == [True, True]

    def test_export_markdown_has_compliance_footer(self, project_with_candidates):
        exported = project_with_candidates.export_markdown("export-test")
        assert "# Project: export-test" in exported
        assert "CC BY-SA 4.0" in exported
        assert "CC-BY 4.0" in exported
        assert "disclaimer" in exported.lower() or "decision support" in exported.lower()
        assert "| Name |" in exported


class TestCandidateModel:

    def test_candidate_serialization(self):
        c = Candidate(
            name="Shigella boydii", epithet="boydii", rank="species",
            grammatical_category="genitive", gender="f",
            derivation="test", compliant=True,
        )
        d = c.as_dict()
        assert d["name"] == "Shigella boydii"
        c2 = Candidate.from_dict(d)
        assert c2.name == "Shigella boydii"
        assert c2.compliant is True

    def test_candidate_created_at_auto(self):
        c = Candidate(name="test", epithet="test", rank="species",
                      grammatical_category=None, gender=None,
                      derivation="", compliant=None)
        assert c.created_at  # auto-populated

    def test_check_provenance_fields_round_trip(self):
        c = _candidate(
            check_verdict="conflict",
            check_query="Bacillus wukongus",
            checked_at="2026-09-21T09:00:00+00:00",
        )
        back = Candidate.from_dict(c.as_dict())
        assert back.check_verdict == "conflict"
        assert back.check_query == "Bacillus wukongus"
        assert back.checked_at == "2026-09-21T09:00:00+00:00"

    def test_legacy_project_without_provenance_keys_still_loads(self, store):
        """Files written before the provenance fields existed still load."""
        store.create("legacy")
        path = store._project_path("legacy")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["candidates"] = [{
            "name": "Bacillus oldus", "epithet": "oldus", "rank": "species",
            "grammatical_category": None, "gender": None, "derivation": "x",
            "compliant": None, "warnings": [], "score": 0, "notes": "",
            "check_verdict": "blocked", "created_at": "2026-01-01T00:00:00+00:00",
        }]
        path.write_text(json.dumps(data), encoding="utf-8")

        loaded = store.load("legacy")
        cand = loaded.candidates[0]
        assert cand.check_verdict == "blocked"
        assert cand.check_query is None and cand.checked_at is None
        # a verdict with no recorded query is exported as untraceable, not trusted
        assert cand.check_provenance == Candidate.UNTRACEABLE_PROVENANCE

    def test_check_provenance_rendering(self):
        assert _candidate().check_provenance == ""
        assert _candidate(
            check_verdict="no_clear_conflict", check_query="Bacillus wukongus",
            checked_at="2026-09-21T09:00:00+00:00",
        ).check_provenance == "Bacillus wukongus @ 2026-09-21T09:00:00+00:00"


# --------------------------------------------------------------------------- #
# Exports must survive table/CSV metacharacters in names and notes
# --------------------------------------------------------------------------- #


class TestExportEscaping:

    @pytest.mark.parametrize("value", [
        "plain", "a | b", "[bracketed] [?]", "line1\nline2", "ünïcøde ✓",
        "<br>", "&amp;", " leading and trailing ", "back\\slash", "",
    ])
    def test_markdown_cell_round_trip(self, value):
        assert md_unescape_cell(md_escape_cell(value)) == value

    def test_markdown_cell_never_breaks_the_table(self):
        cell = md_escape_cell(NASTY_NAME)
        assert "\n" not in cell
        assert "|" not in cell.replace("\\|", "")

    def test_markdown_export_round_trips_a_nasty_candidate(self, store):
        store.create("nasty")
        store.add_candidate("nasty", _candidate(
            name=NASTY_NAME, notes=NASTY_NOTES, derivation="stem | foo [?]",
        ))
        table = _candidate_table(store.export_markdown("nasty"))
        header = split_md_row(table[0])
        rows = [split_md_row(line) for line in table[2:]]
        assert len(header) == 8
        assert "provenance" in header[6].lower()
        for cells in rows:
            assert len(cells) == len(header), "one cell per column, always"
        assert rows[0][0] == NASTY_NAME
        assert rows[0][3] == "✓"
        assert rows[0][-1] == NASTY_NOTES

    def test_csv_export_round_trips_a_nasty_candidate(self, store):
        store.create("nasty")
        store.add_candidate("nasty", _candidate(name=NASTY_NAME, notes=NASTY_NOTES))
        rows = _csv_rows(store.export_csv("nasty"))
        assert rows[0]["name"] == NASTY_NAME
        assert rows[0]["notes"] == NASTY_NOTES

    def test_csv_export_every_field_is_quoted(self, store):
        """Quoting all fields is what makes comment='#' safe for the data."""
        store.create("nasty")
        store.add_candidate("nasty", _candidate(notes="a # hash"))
        data_lines = [
            line for line in store.export_csv("nasty").splitlines()
            if not line.startswith("#")
        ]
        assert all(line.startswith('"') for line in data_lines)
        assert _csv_rows(store.export_csv("nasty"))[0]["notes"] == "a # hash"

    def test_csv_pandas_round_trip_of_nasty_candidate(self, store, tmp_path):
        pandas = pytest.importorskip("pandas")
        store.create("nasty")
        store.add_candidate("nasty", _candidate(name=NASTY_NAME, notes=NASTY_NOTES))
        path = tmp_path / "nasty.csv"
        # Bytes, not text: on Windows a text-mode write would translate the LF
        # inside the quoted multi-line field to CRLF and pandas would hand that
        # back verbatim — the export string is LF-terminated on purpose.
        path.write_bytes(store.export_csv("nasty").encode("utf-8"))
        frame = pandas.read_csv(path, comment="#")
        assert frame.loc[0, "name"] == NASTY_NAME
        assert frame.loc[0, "notes"] == NASTY_NOTES

    def test_markdown_export_of_a_blocked_placeholder_name(self, store):
        """The engine's own placeholder name ("Genus [?]") must not corrupt it."""
        store.create("placeholder")
        store.add_candidate("placeholder", _candidate(
            name="Bacillus [?]", epithet=None, compliant=None,
            derivation="adjective formation blocked",
            notes="authority unavailable",
        ))
        table = _candidate_table(store.export_markdown("placeholder"))
        rows = [split_md_row(line) for line in table[2:]]
        assert rows[0][0] == "Bacillus [?]"
        assert rows[0][3] == "?"


# --------------------------------------------------------------------------- #
# An exported verdict must name the query it came from
# --------------------------------------------------------------------------- #


class TestVerdictProvenanceInExports:

    @pytest.fixture
    def traced(self, store):
        store.create("prov")
        store.add_candidate("prov", _candidate(
            name="Bacillus beijingensis",
            check_verdict="conflict",
            check_query="Bacillus beijingensis",
            checked_at="2026-09-21T09:00:00+00:00",
        ))
        store.add_candidate("prov", _candidate(
            name="Wukomonas otherensis",  # never checked: carries nothing
        ))
        return store

    def test_csv_carries_query_and_timestamp(self, traced):
        rows = _csv_rows(traced.export_csv("prov"))
        assert rows[0]["check_verdict"] == "conflict"
        assert rows[0]["check_query"] == "Bacillus beijingensis"
        assert rows[0]["checked_at"] == "2026-09-21T09:00:00+00:00"
        assert rows[1]["check_verdict"] == ""
        assert rows[1]["check_query"] == ""
        assert rows[1]["checked_at"] == ""

    def test_markdown_shows_the_provenance_column(self, traced):
        table = _candidate_table(traced.export_markdown("prov"))
        header = split_md_row(table[0])
        rows = [split_md_row(line) for line in table[2:]]
        assert "Check provenance" in header[6]
        assert rows[0][6] == "Bacillus beijingensis @ 2026-09-21T09:00:00+00:00"
        assert rows[1][5] == "-" and rows[1][6] == "-"

    def test_json_export_carries_provenance(self, traced):
        payload = json.loads(traced.export_json("prov"))
        cand = payload["candidates"][0]
        assert cand["check_query"] == "Bacillus beijingensis"
        assert cand["checked_at"] == "2026-09-21T09:00:00+00:00"

    def test_untraceable_verdict_is_flagged_in_markdown(self, store):
        store.create("orphan")
        store.add_candidate("orphan", _candidate(check_verdict="blocked"))
        text = store.export_markdown("orphan")
        assert Candidate.UNTRACEABLE_PROVENANCE in text
        table = _candidate_table(text)
        assert split_md_row(table[2])[6] == Candidate.UNTRACEABLE_PROVENANCE


class TestStoreWriteDiscipline:
    """saving must not silently change a project file's permissions."""

    @POSIX_ONLY
    def test_save_preserves_existing_project_file_mode(self, store):
        project = store.create("modes")
        path = store._project_path("modes")
        os.chmod(path, 0o600)
        store.save(project)
        assert _mode(path) == 0o600, "an overwrite is not a permission change"

        os.chmod(path, 0o644)
        store.save(project)
        assert _mode(path) == 0o644, "a shared project must stay world-readable"

    @POSIX_ONLY
    def test_new_project_file_is_not_private_by_accident(self, store):
        store.create("fresh")
        path = store._project_path("fresh")
        assert _mode(path) == 0o644 & ~_current_umask()

    def test_save_leaves_no_temp_file(self, store):
        store.create("clean")
        store.save(store.load("clean"))
        leftovers = [
            p.name for p in store.base_dir.iterdir()
            if p.name != store._project_path("clean").name
        ]
        assert leftovers == []


def _current_umask() -> int:
    previous = os.umask(0o077)
    os.umask(previous)
    return previous
