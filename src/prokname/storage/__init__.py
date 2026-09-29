"""Project and candidate storage.

Local persistence via JSON files (lightweight, diff-able, no SQLite dependency
for the MVP). Each project is a single JSON file under
~/.config/prokname/projects/ (or a caller-supplied base directory).

Exports carry mandatory compliance annotations: tool name/version, query
time, license/attribution, and the disclaimer.
"""

from .etymology import etymology_row, render_etymology_tables
from .model import Candidate, Project
from .store import ProjectLoadError, ProjectStore

__all__ = [
    "Candidate",
    "Project",
    "ProjectLoadError",
    "ProjectStore",
    "etymology_row",
    "render_etymology_tables",
]
