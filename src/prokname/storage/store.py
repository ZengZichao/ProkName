"""Project store: local persistence and export.

Projects are stored as JSON files under ~/.config/prokname/projects/
(or a caller-supplied base directory). Exports support Markdown / CSV /
JSON formats with mandatory compliance annotations.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from .. import DISCLAIMER, LPSN_ATTRIBUTION, SEQCODE_ATTRIBUTION, __version__
from .. import source_attribution as _source_attribution
from .._atomic import atomic_write_json as _atomic_write_json
from .etymology import render_etymology_tables
from .model import Candidate, Project

#: The exact invocation that reads an exported CSV: the provenance/licence
#: preamble uses '#' comment lines, and every data field is quoted
#: (``csv.QUOTE_ALL``), so ``comment='#'`` drops the preamble without touching
#: a '#' inside a value.
CSV_READ_HINT = (
    "read with pandas.read_csv(path, comment='#') "
    "or csv.reader over the lines after the '#' preamble"
)

# CSV columns. The last three carry the dedup verdict *and its provenance*, so a
# reader can see which authority query a verdict came from and when it ran.
CSV_FIELDS = [
    "name", "epithet", "rank", "grammatical_category", "gender",
    "compliant", "score", "notes",
    "check_verdict", "check_query", "checked_at",
]

# Markdown table cells may contain none of these verbatim: a raw '|' ends the
# cell (the engine emits bracketed placeholders such as "... [?]" and derivation
# copy with pipes), a raw newline breaks the row, and a raw '<' would let an
# entity-like sequence collide with the newline marker.
_MD_BREAK = "<br>"
_MD_ESCAPES = (("\\", "\\\\"), ("|", "\\|"), ("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"))
_MD_TOKEN = re.compile(r"\\(.)|<br>|&amp;|&lt;|&gt;|&nbsp;", re.DOTALL)
_MD_ENTITY = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&nbsp;": " "}


def md_escape_cell(value: object) -> str:
    """Make ``value`` safe to place inside a Markdown table cell.

    Backslashes and pipes are escaped, HTML-significant characters become
    entities, leading/trailing spaces become ``&nbsp;``, and line breaks become
    ``<br>`` — so the rendered cell is intact and :func:`md_unescape_cell`
    recovers the original text exactly.
    """
    text = "" if value is None else str(value)
    for needle, replacement in _MD_ESCAPES:
        text = text.replace(needle, replacement)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", _MD_BREAK)
    if text[:1] == " ":
        text = "&nbsp;" + text[1:]
    if text[-1:] == " ":
        text = text[:-1] + "&nbsp;"
    return text


def md_unescape_cell(cell: str) -> str:
    """Inverse of :func:`md_escape_cell` (round-trip for readers/tests)."""
    def _sub(match: re.Match[str]) -> str:
        token = match.group(0)
        if token == "<br>":
            return "\n"
        escaped = match.group(1)
        if escaped is not None:
            return escaped
        return _MD_ENTITY[token]

    return _MD_TOKEN.sub(_sub, cell)


def _md_row(cells: list[str]) -> str:
    return "| " + " | ".join(md_escape_cell(cell) for cell in cells) + " |"


def split_md_row(row: str) -> list[str]:
    """Split an exported Markdown table row into unescaped cell values.

    Escaped pipes (``\\|``) are not treated as separators, which is what makes
    the exporter's round-trip testable.
    """
    cells: list[str] = []
    buffer: list[str] = []
    index = 0
    text = row.strip()
    if text.startswith("|"):
        index = 1
    if text.endswith("|") and len(text) > 1:
        text = text[:-1]
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            buffer.append(char)
            buffer.append(text[index + 1])
            index += 2
            continue
        if char == "|":
            cells.append("".join(buffer).strip())
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1
    cells.append("".join(buffer).strip())
    return [md_unescape_cell(cell) for cell in cells]


class ProjectLoadError(Exception):
    """Raised when a project file exists but cannot be read/parsed."""


def _default_projects_dir() -> Path:
    """Default project storage directory (~/.config/prokname/projects/)."""
    config_home = os.environ.get(
        "XDG_CONFIG_HOME",
        os.path.join(os.path.expanduser("~"), ".config"),
    )
    return Path(config_home) / "prokname" / "projects"


class ProjectStore:
    """Manages project persistence and export."""

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or _default_projects_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _project_path(self, name: str) -> Path:
        """Collision-free project file path for a (possibly lossy) name.

        The on-disk sanitisation (spaces/slashes → underscores) is lossy:
        'my project' and 'my_project' must not silently map to one file,
        so a short content hash of the raw name is appended.
        """
        safe_name = name.replace("/", "_").replace(" ", "_")
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
        return self.base_dir / f"{safe_name}.{digest}.json"

    def create(self, name: str, data_source: str = "", target_code: str = "") -> Project:
        """Create a new project."""
        project = Project(name=name, data_source=data_source, target_code=target_code)
        self.save(project)
        return project

    def load(self, name: str) -> Project | None:
        """Load a project by name; returns None if not found.

        Raises ProjectLoadError when the file exists but is unreadable or
        corrupt — a half-written project must never silently look like a
        missing one.
        """
        path = self._project_path(name)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Project.from_dict(data)
        except (
            json.JSONDecodeError, OSError, ValueError, TypeError, KeyError,
            AttributeError,
        ) as exc:
            # from_dict converts structural problems to ValueError, so every
            # "parses but is malformed" shape lands here too — never as an
            # unhandled KeyError/TypeError in the caller.
            raise ProjectLoadError(
                f"project file for {name!r} is corrupt or unreadable "
                f"({path}): {exc!r}"
            ) from exc

    def save(self, project: Project) -> None:
        """Save a project to disk (atomically)."""
        _atomic_write_json(self._project_path(project.name), project.as_dict())

    def list_projects(self) -> list[str]:
        """List all project names.

        Names come from each file's stored "name" field (the on-disk filename
        carries a collision-guard hash suffix, which is not user-facing).
        Unreadable files fall back to their stem rather than vanishing.
        """
        names = []
        for path in sorted(self.base_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                names.append(data.get("name") or path.stem)
            except (json.JSONDecodeError, OSError, AttributeError):
                names.append(path.stem)
        return sorted(names)

    def delete(self, name: str) -> bool:
        """Delete a project; returns True if it existed."""
        path = self._project_path(name)
        if path.exists():
            path.unlink()
            return True
        return False

    def add_candidate(self, project_name: str, candidate: Candidate) -> Project:
        """Add a candidate to a project."""
        project = self.load(project_name)
        if project is None:
            project = self.create(project_name)
        project.candidates.append(candidate)
        self.save(project)
        return project

    def rate_candidate(self, project_name: str, name: str, score: int) -> Project:
        """Rate a candidate (0-5)."""
        project = self.load(project_name)
        if project is None:
            raise FileNotFoundError(f"project {project_name!r} not found")
        for c in project.candidates:
            if c.name == name:
                c.score = max(0, min(5, score))
                break
        else:
            raise ValueError(f"candidate {name!r} not found in project {project_name!r}")
        self.save(project)
        return project

    def list_candidates(self, project_name: str) -> list[Candidate]:
        """List candidates in a project."""
        project = self.load(project_name)
        if project is None:
            return []
        return project.candidates

    def export_json(self, project_name: str) -> str:
        """Export a project as JSON with compliance annotations."""
        project = self.load(project_name)
        if project is None:
            raise FileNotFoundError(f"project {project_name!r} not found")
        payload = {
            **project.as_dict(),
            "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "tool": f"prokname v{__version__}",
            "disclaimer": DISCLAIMER,
            # One source of truth for the licence wording.
            "attribution": _source_attribution(),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def export_csv(self, project_name: str, provenance: bool = True) -> str:
        """Export candidates as CSV, by default with a provenance preamble.

        The preamble is made of '#' comment lines and *every* data field is
        quoted, so the only thing a reader must know is to skip comments —
        see :data:`CSV_READ_HINT`, spelled out in the first line of the file
        itself. Pass ``provenance=False`` for a strictly bare, header-first CSV
        (a sidecar, or a caller that documents the attribution elsewhere); the
        last three columns keep the dedup verdict traceable either way: which
        name was queried, and when.
        """
        project = self.load(project_name)
        if project is None:
            raise FileNotFoundError(f"project {project_name!r} not found")
        output = io.StringIO()
        if provenance:
            # Compliance header as comment lines (the first one says how to read it)
            output.write(f"# prokname v{__version__} export — {CSV_READ_HINT}\n")
            output.write(f"# exported: {datetime.now(UTC).isoformat(timespec='seconds')}\n")
            output.write(f"# {DISCLAIMER}\n")
            output.write(f"# LPSN data: {LPSN_ATTRIBUTION}\n")
            output.write(f"# SeqCode data: {SEQCODE_ATTRIBUTION}\n")
        writer = csv.DictWriter(
            output, fieldnames=CSV_FIELDS, quoting=csv.QUOTE_ALL,
            lineterminator="\n",
        )
        writer.writeheader()
        for c in project.candidates:
            writer.writerow({
                "name": c.name,
                "epithet": c.epithet or "",
                "rank": c.rank,
                "grammatical_category": c.grammatical_category or "",
                "gender": c.gender or "",
                "compliant": c.compliant if c.compliant is not None else "review",
                "score": c.score,
                "notes": c.notes,
                "check_verdict": c.check_verdict or "",
                "check_query": c.check_query or "",
                "checked_at": c.checked_at or "",
            })
        return output.getvalue()

    def export_markdown(self, project_name: str) -> str:
        """Export candidates as a Markdown table with compliance footer.

        Every interpolated value goes through :func:`md_escape_cell`: names,
        notes and derivation copy may contain pipes, square brackets or line
        breaks (the engine's blocked-placeholder name is literally
        ``"Genus [?]"``), which would otherwise corrupt the table handed to
        co-authors and registrars.
        """
        project = self.load(project_name)
        if project is None:
            raise FileNotFoundError(f"project {project_name!r} not found")
        lines = [
            f"# Project: {project.name}",
            "",
            f"- **Created:** {project.created_at}",
            f"- **Data source:** {project.data_source or 'unspecified'}",
            f"- **Target code:** {project.target_code or 'unspecified'}",
            f"- **Candidates:** {len(project.candidates)}",
            "",
            "| Name | Category | Gender | Compliant | Score "
            "| Check verdict | Check provenance (query @ time) | Notes |",
            "|------|----------|--------|-----------|-------"
            "|---------------|-------------------------------|-------|",
        ]
        for c in project.candidates:
            comp = "✓" if c.compliant else "✗" if c.compliant is False else "?"
            lines.append(_md_row([
                c.name,
                c.grammatical_category or "-",
                c.gender or "-",
                comp,
                str(c.score),
                c.check_verdict or "-",
                c.check_provenance or "-",
                c.notes,
            ]))
        etymologies = render_etymology_tables(project.candidates)
        if etymologies:
            lines.extend(["", etymologies])
        lines.extend([
            "",
            "---",
            f"*Exported by prokname v{__version__} on "
            f"{datetime.now(UTC).isoformat(timespec='seconds')}*",
            f"*{DISCLAIMER}*",
            "",
            "**Data licenses:**",
            f"- LPSN-derived data: {LPSN_ATTRIBUTION}",
            f"- SeqCode-derived data: {SEQCODE_ATTRIBUTION}",
        ])
        return "\n".join(lines)
