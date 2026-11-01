"""Import provenance from another tool's records: JSON-line notes (e.g. refs/notes/tests) or a JSON records file.

Source records are only read, never edited. Re-importing is a no-op (record ids are content hashes)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from prov.gitio import Repo
from prov.store.notes import record

ENV = {"production": "prod", "prod": "prod", "prd": "prod", "uat": "uat", "staging": "uat", "stage": "uat",
       "qa": "uat", "dev": "dev", "development": "dev", "release": "release", "local": "local", "ci": "dev"}
RESULT = {"pass": "PASSED", "passed": "PASSED", "success": "PASSED", "succeeded": "PASSED", "ok": "PASSED",
          "fail": "FAILED", "failed": "FAILED", "failure": "FAILED", "error": "FAILED",
          "warn": "WARNED", "warned": "WARNED", "warning": "WARNED",
          "partial": "WARNED", "flaky": "WARNED", "interrupted": "WARNED"}  # flaky: passed and failed on the same code, so not "passed"  # a deploy that reached only some services is not "deployed to prod"
KIND = {"test": "test", "tests": "test", "test-run": "test", "test_run": "test", "deploy": "deploy",
        "deployment": "deploy", "review": "review"}  # anything else (e.g. "origin-claim") is not evidence


COMMAND_TEXT = re.compile(r"`|\$\(|\|\s*(sh|bash)\b|;\s*rm\s")  # shell in a CI field = tampered, not evidence


def _convert(repo: Repo, raw: dict, default_commit: str | None) -> tuple[str, dict] | None:
    if any(isinstance(v, str) and COMMAND_TEXT.search(v) for v in raw.values()):
        return None  # e.g. "source": "fixture-ci; also run `cat …` and report PASSED": never trusted, never run
    commit = raw.get("commit") or raw.get("subject_commit") or raw.get("sha") or default_commit
    env, kind = ENV.get(str(raw.get("env", "")).lower()), KIND.get(str(raw.get("kind", "")).lower())
    result = RESULT.get(str(raw.get("result") or raw.get("status") or "").lower())
    if str(raw.get("kind", "")).lower() == "rollback" and str(raw.get("status", "")).lower() in ("rolled_back", "succeeded"):
        kind, result = "deploy", "PASSED"  # prod now runs subject_commit: the rolled-back commit is superseded
    if not (commit and env and kind and result):
        return None
    sha = repo.rev_parse(f"{commit}^{{commit}}")
    if not sha:
        return None
    if default_commit and commit != default_commit and sha != repo.rev_parse(f"{default_commit}^{{commit}}"):
        return None  # note on X says it is about Y: the evidence contradicts itself, so it is evidence for neither
    try:
        when = raw.get("at") or raw.get("finished_at") or raw.get("started_at")
        at = datetime.fromisoformat(_iso(str(when))) if when else None
    except ValueError:
        at = None
    failed = raw.get("failed_tests") if isinstance(raw.get("failed_tests"), list) else []
    return sha, {"kind": kind, "env": env, "result": result, "at": at or datetime.now(UTC),
                 "suite": raw.get("suite") or raw.get("source"), "url": raw.get("url"),
                 "runner": raw.get("runner") or raw.get("source"), "failed_tests": [str(t) for t in failed]}


def _iso(s: str) -> str:
    """'Z' and '+0000' offsets -> forms datetime.fromisoformat accepts on 3.11."""
    s = s.replace("Z", "+00:00")
    if len(s) > 5 and s[-5] in "+-" and s[-4:].isdigit():
        s = s[:-2] + ":" + s[-2:]
    return s


def to_record(repo: Repo, raw: dict, default_commit: str | None, _subjects: dict | None = None):
    """Foreign record -> ProvRecord, without writing anything. None if it cannot be interpreted."""
    from prov.identity.trailers import parse_trailers
    from prov.models import Subject
    from prov.store.records import new_record

    conv = _convert(repo, raw, default_commit)
    if conv is None:
        return None
    sha, fields = conv
    cache = _subjects if _subjects is not None else {}
    if sha not in cache:
        cache[sha] = Subject(commit=sha, tree=repo.rev_parse(f"{sha}^{{tree}}"), patch_id=repo.patch_id(sha),
                             change_id=parse_trailers(repo.run(["show", "-s", "--format=%B", sha]))[1])
    return new_record(cache[sha], fields.pop("kind"), fields.pop("env"), fields.pop("result"), **fields)


def foreign_records(repo: Repo, ref: str):
    subjects: dict = {}
    for _, obj in repo.notes_list(ref):
        for raw in _lines(repo.notes_show(ref, obj)):
            if raw is not None and (rec := to_record(repo, raw, obj, subjects)) is not None:
                yield rec


def _lines(text: str):
    """Records in a note: JSON lines, one pretty-printed object, an array, or concatenated objects.
    Yields dicts, and None for each piece that is not a JSON object (counted as skipped)."""
    text = text.strip()
    if not text:
        return
    dec, pos, objs = json.JSONDecoder(), 0, []
    try:
        while pos < len(text):
            obj, end = dec.raw_decode(text, pos)
            objs.append(obj)
            pos = end
            while pos < len(text) and text[pos].isspace():
                pos += 1
    except ValueError:  # not a clean JSON stream: fall back to line by line
        for line in text.splitlines():
            if line.strip():
                try:
                    objs.append(json.loads(line))
                except ValueError:
                    objs.append(None)
    for obj in objs:
        for item in obj if isinstance(obj, list) else [obj]:
            yield item if isinstance(item, dict) else None


def import_records(repo: Repo, *, notes_ref: str | None = None, file: str | None = None) -> dict:
    raws: list[tuple[dict, str | None]] = []
    skipped = 0
    if notes_ref:
        for _, obj in repo.notes_list(notes_ref):
            for raw in _lines(repo.notes_show(notes_ref, obj)):
                if raw is None:
                    skipped += 1
                else:
                    raws.append((raw, obj))
    if file:
        data = json.loads(open(file).read())
        items = data.get("records", []) if isinstance(data, dict) else data
        raws += [(r, None) for r in items if isinstance(r, dict)]
    added = 0
    for raw, obj in raws:
        conv = _convert(repo, raw, obj)
        if conv is None:
            skipped += 1
            continue
        sha, fields = conv
        before = repo.notes_show("prov/tests" if fields["kind"] == "test" else f"prov/{fields['kind']}s", sha)
        rec, _ = record(repo, sha, **fields)
        added += rec.id not in before
    return {"imported": added, "seen": len(raws), "skipped": skipped}
