"""One licence statement per obligation.

The same two compliance sentences — LPSN is CC BY-SA 4.0 and the SeqCode
Registry is CC BY 4.0 — used to be typed out four times with three different
wordings: the JSON `attribution` block of a check report, the same block in a
project export, the `#` preamble of the CSV/Markdown exports, and the deposit
README footer. Nothing kept them together, so a licence correction would have
landed in some outputs and not others. For a tool whose deliverable *is* a
compliance statement, an auditor finding two obligations in two exports of the
same project is the failure mode to design out.

`prokname.LPSN_ATTRIBUTION` / `SEQCODE_ATTRIBUTION` / `source_attribution()` are
now the only place the wording exists, and these tests hold it that way.
"""

from __future__ import annotations

import json
import pathlib

import prokname
from prokname import LPSN_ATTRIBUTION, SEQCODE_ATTRIBUTION, source_attribution
from prokname.dedup import check_name
from prokname.storage.model import Candidate
from prokname.storage.store import ProjectStore

SRC = pathlib.Path(prokname.__file__).parent

#: Phrases unique to the licence sentences (not to an endpoint URL).
WORDINGS = ("cite the current LPSN reference", "attribute seqco.de")


def test_the_wording_exists_in_exactly_one_module() -> None:
    """A grep of the package is the whole audit; one file answers it."""
    found: dict[str, list[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        hits = [word for word in WORDINGS if word in text]
        if hits:
            found[path.relative_to(SRC).as_posix()] = hits
    assert found == {"__init__.py": list(WORDINGS)}, (
        f"licence wording was duplicated into {found}")


def test_both_obligations_keep_their_licence_tokens() -> None:
    """The export tests assert these tokens; consolidation must not lose them."""
    assert LPSN_ATTRIBUTION.startswith("CC BY-SA 4.0")
    assert SEQCODE_ATTRIBUTION.startswith("CC-BY 4.0")
    assert "lpsn.dsmz.de" in LPSN_ATTRIBUTION


def test_source_attribution_returns_a_fresh_mapping() -> None:
    """Callers serialise it and one report must not be able to edit the next."""
    first = source_attribution()
    first["LPSN"] = "tampered"
    assert source_attribution()["LPSN"] == LPSN_ATTRIBUTION


def test_a_check_and_its_export_state_the_same_obligation(tmp_path) -> None:
    """The behavioural half: two outputs, one sentence per licence."""
    report = check_name("Bacillus wukongus", online=False, near_match=False)
    store = ProjectStore(base_dir=tmp_path / "projects")
    store.create("Attribution", data_source="MAG", target_code="both")
    store.add_candidate("Attribution", Candidate(
        name="Bacillus wukongus", epithet="wukongus", rank="species",
        grammatical_category="appositive", gender="m", derivation="appositive",
        compliant=True,
    ))

    exported = json.loads(store.export_json("Attribution"))
    assert exported["attribution"] == report.attribution, (
        "the report and the export state different obligations")
    markdown = store.export_markdown("Attribution")
    csv_text = store.export_csv("Attribution")
    assert LPSN_ATTRIBUTION in markdown
    assert SEQCODE_ATTRIBUTION in markdown
    assert LPSN_ATTRIBUTION in csv_text
    assert SEQCODE_ATTRIBUTION in csv_text
