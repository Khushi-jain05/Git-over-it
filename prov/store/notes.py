"""Write/read provenance records in refs/notes/prov/* (docs/03 §3)."""

from __future__ import annotations

from datetime import datetime

from prov.errors import ProvError
from prov.gitio import Repo
from prov.identity.trailers import parse_trailers
from prov.models import Env, Kind, ProvRecord, ResolutionRecord, Result, Subject
from prov.store.records import NOTES_REF, canonical, decode_lines, encode, new_record


def _append(repo: Repo, ref: str, obj: str, line: str, rec_id: str | None) -> bool:
    existing = repo.notes_show(ref, obj)
    if rec_id and any(r.id == rec_id for r in decode_lines(existing)):
        return False
    text = existing if not existing or existing.endswith("\n") else existing + "\n"
    repo.notes_add(ref, obj, text + line + "\n")
    return True


def append_note_line(repo: Repo, ref: str, obj: str, line: str) -> None:
    _append(repo, ref, obj, line, None)


def write_record(repo: Repo, rec: ProvRecord) -> list[str]:
    """Attach to the commit, and for tests also its tree. Duplicate writes are no-ops."""
    ref = NOTES_REF[rec.kind]
    targets = [(rec.subject.commit, "commit")]
    if rec.kind == Kind.test:
        targets.append((rec.subject.tree, "tree"))
    line = encode(rec)
    for obj, _ in targets:
        _append(repo, ref, obj, line, rec.id)
    return [f"{obj} ({what})" for obj, what in targets]


def records_for(repo: Repo, commit: str) -> list[ProvRecord]:
    """Union of records on the commit and its tree, de-duplicated by id."""
    tree = repo.rev_parse(f"{commit}^{{tree}}")
    seen: dict[str, ProvRecord] = {}
    for ref in NOTES_REF.values():
        for obj in filter(None, (commit, tree)):
            for r in decode_lines(repo.notes_show(ref, obj)):
                seen.setdefault(r.id, r)
    return list(seen.values())


def write_resolution(repo: Repo, rec: ResolutionRecord) -> None:
    _append(repo, "prov/resolutions", rec.commit, canonical(rec.model_dump(mode="json")), None)


def notes_present(repo: Repo, read_refs: list[str] = ()) -> bool:
    refs = ["refs/notes/prov/", *(f"refs/notes/{r}" for r in read_refs)]
    return bool(repo.run(["for-each-ref", "--count=1", "--format=x", *refs]).strip())


def record(repo: Repo, rev: str, *, kind: Kind, env: Env, result: Result, at: datetime | None = None,
           **extra) -> tuple[ProvRecord, list[str]]:
    """Build the subject for <rev> and write the record. -> (record, attached objects)"""
    sha = repo.rev_parse(f"{rev}^{{commit}}")
    if not sha:
        raise ProvError("usage", f"unknown revision {rev}")
    subj = Subject(commit=sha, tree=repo.rev_parse(f"{sha}^{{tree}}"), patch_id=repo.patch_id(sha),
                   change_id=parse_trailers(repo.run(["show", "-s", "--format=%B", sha]))[1])
    rec = new_record(subj, Kind(kind), Env(env), Result(result), at=at, **extra)
    return rec, write_record(repo, rec)
