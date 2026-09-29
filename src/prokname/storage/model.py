"""Data models for project storage."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from . import schema


@dataclass
class Candidate:
    """A stored candidate name with its metadata."""
    name: str
    epithet: str | None
    rank: str
    grammatical_category: str | None
    gender: str | None
    derivation: str
    compliant: bool | None
    warnings: list[str] = field(default_factory=list)
    score: int = 0  # user rating 0-5
    notes: str = ""
    # `check_verdict` is only a *ruling about a name* if it can be traced back
    # to the authority query that produced it. All three fields are therefore
    # written together by the Studio's add-to-project path (and never copied
    # onto an unrelated candidate); a verdict with no `check_query` is exported
    # as untraceable rather than dropped, so nothing is silently lost.
    check_verdict: str | None = None  # dedup check result
    check_query: str | None = None  # the name the dedup check actually queried
    checked_at: str | None = None  # when that check ran (ISO-8601)
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat(timespec="seconds")

    #: Rendered in the export when a verdict exists but its query was not
    #: recorded — an untraceable verdict must not read like a ruling.
    UNTRACEABLE_PROVENANCE = "UNTRACEABLE (no authority query recorded)"

    @property
    def check_provenance(self) -> str:
        """Where a stored verdict came from: the queried name + check time.

        Empty when nothing was checked; the queried name and check time when the
        verdict is traceable; :attr:`UNTRACEABLE_PROVENANCE` when a verdict was
        stored without the query it was derived from.
        """
        if not self.check_verdict:
            return ""
        if not self.check_query:
            return self.UNTRACEABLE_PROVENANCE
        if self.checked_at:
            return f"{self.check_query} @ {self.checked_at}"
        return self.check_query

    def as_dict(self) -> dict:
        """Candidate payload, as stored inside a project's `candidates` list.

        Deliberately *not* version-stamped: the schema marker belongs to the
        file (see Project.as_dict), and stamping every nested candidate would
        put a per-record version that nothing reads into every export.
        """
        return {
            "name": self.name,
            "epithet": self.epithet,
            "rank": self.rank,
            "grammatical_category": self.grammatical_category,
            "gender": self.gender,
            "derivation": self.derivation,
            "compliant": self.compliant,
            "warnings": self.warnings,
            "score": self.score,
            "notes": self.notes,
            "check_verdict": self.check_verdict,
            "check_query": self.check_query,
            "checked_at": self.checked_at,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Candidate:
        # Structural validation raises ValueError (not KeyError/TypeError) so
        # store.load() can wrap every malformed-file shape into
        # ProjectLoadError — a half-written project must never surface as an
        # unhandled exception.
        if not isinstance(data, dict):
            raise ValueError(
                f"candidate entry is not a JSON object: {type(data).__name__}"
            )
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"candidate entry has no usable 'name' field: {data!r}")
        warnings = data.get("warnings", [])
        if not isinstance(warnings, list):
            raise ValueError(
                f"candidate 'warnings' must be a list, got {type(warnings).__name__}"
            )
        return cls(
            name=name,
            epithet=data.get("epithet"),
            rank=data.get("rank", "species"),
            grammatical_category=data.get("grammatical_category"),
            gender=data.get("gender"),
            derivation=data.get("derivation", ""),
            compliant=data.get("compliant"),
            warnings=warnings,
            score=data.get("score", 0),
            notes=data.get("notes", ""),
            check_verdict=data.get("check_verdict"),
            check_query=data.get("check_query"),
            checked_at=data.get("checked_at"),
            created_at=data.get("created_at", ""),
        )


@dataclass
class Project:
    """A named project containing candidates and metadata."""
    name: str
    created_at: str = ""
    data_source: str = ""  # pure_culture | MAG | SAG | unknown
    target_code: str = ""  # ICNP | SeqCode | both
    candidates: list[Candidate] = field(default_factory=list)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat(timespec="seconds")

    def as_dict(self) -> dict:
        """Project payload, stamped with the storage schema version.

        Stamped here rather than in ProjectStore.save() so that anything which
        serialises a Project — the store, an export, a test fixture — produces
        a versioned file. A version that only one code path writes is a version
        that quietly stops existing.
        """
        return schema.stamp({
            "name": self.name,
            "created_at": self.created_at,
            "data_source": self.data_source,
            "target_code": self.target_code,
            "candidates": [c.as_dict() for c in self.candidates],
        })

    @classmethod
    def from_dict(cls, data: dict) -> Project:
        # Same contract as Candidate.from_dict: structural problems are
        # ValueError, so the store can convert them into ProjectLoadError.
        if not isinstance(data, dict):
            raise ValueError(
                f"project file is not a JSON object: {type(data).__name__}"
            )
        # Upgrade first: a pre-versioning file (schema_version absent = 0) is
        # supported and migrates; a file from a newer prokname is refused with
        # a reason instead of being half-read.
        data = schema.upgrade(data)
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("project file has no usable 'name' field")
        raw_candidates = data.get("candidates", [])
        if not isinstance(raw_candidates, list):
            raise ValueError(
                f"project 'candidates' must be a list, "
                f"got {type(raw_candidates).__name__}"
            )
        try:
            candidates = [Candidate.from_dict(c) for c in raw_candidates]
        except ValueError:
            raise
        except (TypeError, KeyError, AttributeError) as exc:
            raise ValueError(f"project candidate entry is malformed: {exc!r}") from exc
        return cls(
            name=name,
            created_at=data.get("created_at", ""),
            data_source=data.get("data_source", ""),
            target_code=data.get("target_code", ""),
            candidates=candidates,
        )
