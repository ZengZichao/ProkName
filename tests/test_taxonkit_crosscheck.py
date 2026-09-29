"""CI cross-validation of the built-in NCBI taxdump parser against taxonkit.

taxonkit (shenwei356/taxonkit, MIT)
is the de-facto standard for NCBI taxdump processing. Following the same
CLI-isolation protocol as the C-set gan comparison (no Go code imported,
the binary only invoked as a subprocess), this test asserts that prokname's
zero-dependency taxdump parser (dedup/ncbi.py) agrees with taxonkit on a
synthetic dump.

The test is SKIPPED when the taxonkit binary is not on PATH. It is an OPT-IN
gate: no shipped image or CI job installs taxonkit (see
`.github/workflows/ci.yml`, whose steps install only the project extras), so
the cross-check never runs in this repository's CI. Run it locally by
installing the binary yourself, e.g. `conda install -c bioconda taxonkit`.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from prokname.dedup.ncbi import _check_taxdump, load_taxdump_names

taxonkit = pytest.mark.skipif(
    shutil.which("taxonkit") is None,
    reason=(
        "opt-in cross-check: taxonkit is not on PATH, and no shipped CI job or "
        "image installs it, so this gate stays skipped unless you "
        "`conda install -c bioconda taxonkit` yourself"
    ),
)

# (tax_id, parent_tax_id, rank, name) — a tiny synthetic taxonomy
TAXA = [
    ("1", "1", "no rank", "root"),
    ("2", "1", "genus", "Syntheticus"),
    ("3", "2", "species", "Syntheticus testis"),
    ("4", "2", "species", "Syntheticus exemplaris"),
]


def _write_taxdump(directory: Path) -> None:
    nodes = directory / "nodes.dmp"
    names = directory / "names.dmp"
    nodes.write_text(
        "".join(
            f"{tid}\t|\t{parent}\t|\t{rank}\t|\tnot used\t|\n"
            for tid, parent, rank, _ in TAXA
        ),
        encoding="utf-8",
    )
    names.write_text(
        "".join(
            f"{tid}\t|\t{name}\t|\t\t|\tscientific name\t|\n"
            for tid, _, _, name in TAXA
        ),
        encoding="utf-8",
    )


def _taxonkit_name2taxid(dump_dir: Path, name: str) -> str:
    """Return the taxid taxonkit resolves for `name` ("" when unmatched)."""
    result = subprocess.run(
        ["taxonkit", "name2taxid", "--data-dir", str(dump_dir)],
        input=name, capture_output=True, text=True, check=True,
    )
    # output: "<name>\t<taxid>" (taxid empty when not found)
    fields = result.stdout.strip().split("\t")
    return fields[1] if len(fields) > 1 else ""


@taxonkit
def test_parser_taxids_agree_with_taxonkit(tmp_path):
    dump_dir = tmp_path / "taxdump"
    dump_dir.mkdir()
    _write_taxdump(dump_dir)

    for tax_id, _, _, name in TAXA:
        # prokname's parser must resolve the exact same taxid
        result = _check_taxdump(name, str(dump_dir))
        assert result is not None and result.status == "found_reference"
        assert f"taxid={tax_id}" in result.detail
        # ... and so must taxonkit
        assert _taxonkit_name2taxid(dump_dir, name) == tax_id


@taxonkit
def test_parser_and_taxonkit_agree_on_misses(tmp_path):
    dump_dir = tmp_path / "taxdump"
    dump_dir.mkdir()
    _write_taxdump(dump_dir)

    assert _check_taxdump("Notagenus nowherei", str(dump_dir)) is not None
    assert _check_taxdump("Notagenus nowherei", str(dump_dir)).status == "not_found"
    assert _taxonkit_name2taxid(dump_dir, "Notagenus nowherei") == ""


@taxonkit
def test_corpus_loader_sees_the_same_scientific_names(tmp_path):
    dump_dir = tmp_path / "taxdump"
    dump_dir.mkdir()
    _write_taxdump(dump_dir)

    entries = load_taxdump_names(str(dump_dir))
    assert {e["name"] for e in entries} == {name for _, _, _, name in TAXA}
