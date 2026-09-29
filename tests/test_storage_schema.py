"""Project files carry a schema version, and the loader honours it.

The scenario these tests exist for: a user has project files written by v0.1.0
— which had no version marker at all — and installs a later prokname whose
Project layout has changed. Without a marker the only two possible behaviours
are both bad: misread the file, or call it corrupt and lose the notes. With a
marker there is a third: migrate it, one reviewable step at a time, or refuse
loudly when the file is from a build newer than this one.
"""

from __future__ import annotations

import json

import pytest

from prokname.storage import schema
from prokname.storage.model import Candidate, Project
from prokname.storage.store import ProjectLoadError, ProjectStore


def _legacy_payload(name: str = "field notes") -> dict:
    """Exactly what v0.1.0 wrote: no schema_version anywhere."""
    return {
        "name": name,
        "created_at": "2026-08-16T10:00:00+00:00",
        "data_source": "MAG",
        "target_code": "SeqCode",
        "candidates": [{
            "name": "Escherichia coli", "epithet": "coli", "rank": "species",
            "grammatical_category": "adjective", "gender": "f",
            "derivation": "from a person", "compliant": True, "warnings": [],
            "score": None, "notes": "kept for the release",
            "check_verdict": "conflict", "check_query": "Escherichia coli",
            "checked_at": "2026-08-16T10:02:00+00:00",
            "created_at": "2026-08-16T10:02:00+00:00",
        }],
    }


# ---------------------------------------------------------------------------
# The module itself
# ---------------------------------------------------------------------------

def test_a_missing_marker_is_version_zero_not_an_error():
    assert schema.read_version({"name": "x"}) == 0


def test_the_version_this_build_writes_is_the_version_it_reads():
    stamped = schema.stamp({"name": "x"})
    assert stamped[schema.KEY] == schema.current_version()
    assert schema.read_version(stamped) == schema.current_version()
    # and it leads the payload, so `head project.json` answers the question
    assert next(iter(stamped)) == schema.KEY


def test_upgrade_from_legacy_is_lossless():
    legacy = _legacy_payload()
    upgraded = schema.upgrade(legacy)
    assert upgraded[schema.KEY] == schema.current_version()
    for key, value in legacy.items():
        assert upgraded[key] == value, f"migration {key} changed the data"


def test_upgrade_is_idempotent():
    once = schema.upgrade(_legacy_payload())
    twice = schema.upgrade(once)
    assert once == twice


def test_every_step_in_the_table_is_registered_without_gaps():
    """A hole in the chain would make old files unopenable, silently."""
    missing = [v for v in range(schema.MIN_SUPPORTED_VERSION,
                                schema.SCHEMA_VERSION)
               if v not in schema._MIGRATIONS]
    assert not missing, f"no migration registered from {missing}"


def test_a_future_version_is_refused_rather_than_guessed():
    payload = {schema.KEY: schema.current_version() + 1, "name": "x"}
    with pytest.raises(ValueError, match="this prokname build understands"):
        schema.upgrade(payload)


def test_a_garbage_marker_is_refused_not_coerced():
    for bad in ("1", 1.0, True, -3, None, []):
        with pytest.raises(ValueError):
            schema.upgrade({schema.KEY: bad, "name": "x"})


def test_a_non_object_payload_is_refused():
    with pytest.raises(ValueError, match="not a JSON object"):
        schema.upgrade(["not", "a", "project"])


def test_a_registered_duplicate_migration_is_rejected():
    """Two steps claiming the same version would make the chain ambiguous."""
    with pytest.raises(RuntimeError, match="duplicate migration"):
        @schema.migration(0)
        def _clash(payload):  # noqa: ANN001
            return payload


def test_upgrade_does_not_mutate_the_caller_s_dict():
    original = _legacy_payload()
    snapshot = json.dumps(original, sort_keys=True)
    schema.upgrade(original)
    assert json.dumps(original, sort_keys=True) == snapshot


# ---------------------------------------------------------------------------
# Through the real read/write path
# ---------------------------------------------------------------------------

def test_saved_projects_carry_the_version(tmp_path):
    store = ProjectStore(base_dir=tmp_path)
    store.create("notes", data_source="MAG", target_code="SeqCode")
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload[schema.KEY] == schema.current_version()


def test_a_v0_1_0_file_on_disk_still_loads(tmp_path):
    """The actual upgrade path: write a legacy file, read it with this build."""
    store = ProjectStore(base_dir=tmp_path)
    path = store._project_path("field notes")
    path.write_text(json.dumps(_legacy_payload()), encoding="utf-8")

    project = store.load("field notes")
    assert project is not None
    assert project.data_source == "MAG"
    assert project.candidates[0].notes == "kept for the release"
    assert project.candidates[0].check_verdict == "conflict"


def test_rewriting_a_loaded_legacy_file_upgrades_it_in_place(tmp_path):
    store = ProjectStore(base_dir=tmp_path)
    path = store._project_path("field notes")
    path.write_text(json.dumps(_legacy_payload()), encoding="utf-8")
    project = store.load("field notes")
    store.save(project)
    assert json.loads(path.read_text())[schema.KEY] == schema.current_version()


def test_a_future_file_surfaces_as_project_load_error(tmp_path):
    """Not a crash, not a partial read: the same error channel as corruption."""
    store = ProjectStore(base_dir=tmp_path)
    path = store._project_path("from the future")
    payload = _legacy_payload("from the future")
    payload[schema.KEY] = 99
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ProjectLoadError) as exc:
        store.load("from the future")
    assert "99" in str(exc.value)
    # the message tells the user what to do, not just that something failed
    assert "Upgrade prokname" in str(exc.value)


def test_list_projects_is_unbothered_by_the_marker(tmp_path):
    store = ProjectStore(base_dir=tmp_path)
    store.create("alpha")
    store.create("beta")
    assert store.list_projects() == ["alpha", "beta"]


def _candidate(name: str = "Escherichia coli", **kwargs):  # noqa: ANN003
    """A Candidate with the four mandatory fields filled in."""
    fields = {"epithet": "coli", "rank": "species",
              "grammatical_category": "adjective", "gender": "f",
              "derivation": "from a person", "compliant": True}
    fields.update(kwargs)
    return Candidate(name=name, **fields)


def test_candidates_are_not_individually_versioned(tmp_path):
    """The file owns the version; a per-record one would be a second truth."""
    project = Project(name="p")
    project.candidates.append(_candidate())
    payload = project.as_dict()
    assert schema.KEY in payload
    assert all(schema.KEY not in entry for entry in payload["candidates"])


def test_exports_do_not_leak_the_marker_into_read_formats(tmp_path):
    """Markdown and CSV are for reading; an internal key in a human-readable table
    would be a defect. The JSON export is a round-trip file, so it keeps it."""
    store = ProjectStore(base_dir=tmp_path)
    project = store.create("export demo")
    project.candidates.append(_candidate(notes="kept for the release"))
    store.save(project)

    markdown = store.export_markdown(project.name)
    assert "coli" in markdown
    assert schema.KEY not in markdown
    csv_text = store.export_csv(project.name)
    assert schema.KEY not in csv_text
    payload = json.loads(store.export_json(project.name))
    assert payload[schema.KEY] == schema.current_version(), (
        "the JSON export should stay re-loadable by a later version")
