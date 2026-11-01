"""ProvRecord <-> canonical JSON line (docs/03 §2)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from prov.models import Kind, ProvRecord, Subject

NOTES_REF = {Kind.test: "prov/tests", Kind.deploy: "prov/deploys", Kind.review: "prov/reviews"}
ALL_REFS = ["prov/tests", "prov/deploys", "prov/reviews", "prov/resolutions"]


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def new_record(subject: Subject, kind, env, result, *, at: datetime | None = None, **extra) -> ProvRecord:
    body = ProvRecord(
        id="", subject=subject, kind=kind, env=env, result=result, at=at or datetime.now(UTC), **extra
    ).model_dump(mode="json", exclude={"id"})
    return ProvRecord(id=hashlib.sha1(canonical(body).encode()).hexdigest(), **body)


def encode(rec: ProvRecord) -> str:
    return canonical(rec.model_dump(mode="json"))


def decode_lines(text: str) -> list[ProvRecord]:
    out = []
    for line in text.splitlines():
        try:
            out.append(ProvRecord.model_validate_json(line))
        except ValueError:
            continue  # foreign or corrupt line: keep the note, skip the record
    return out


def to_in_toto(rec: ProvRecord) -> dict:
    """Standard in-toto Statement (docs/03 §2)."""
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [{"name": "git-commit", "digest": {"gitCommit": rec.subject.commit, "gitTree": rec.subject.tree}}],
        "predicateType": "https://in-toto.io/attestation/test-result/v0.1",
        "predicate": {
            "result": rec.result.value,
            "configuration": [{"name": "suite", "uri": rec.suite or ""}],
            "url": rec.url,
            "passedTests": [],
            "warnedTests": [],
            "failedTests": rec.failed_tests,
            "x-prov": {"env": rec.env.value, "kind": rec.kind.value, "patch_id": rec.subject.patch_id,
                       "change_id": rec.subject.change_id, "runner": rec.runner, "at": rec.at.isoformat()},
        },
    }


def parse_junit(xml_text: str) -> tuple[str, list[str]]:
    """-> (result, failed test ids). FAILED if any <failure> or <error>."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml_text)
    failed = []
    for case in root.iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            cls = case.get("classname")
            failed.append(f"{cls}.{case.get('name')}" if cls else case.get("name", "?"))
    return ("FAILED" if failed else "PASSED"), failed
