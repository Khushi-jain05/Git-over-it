"""Replace one marker block with a chosen side (docs/06 §2)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from prov.analyze import conflicts
from prov.analyze.report import load_report
from prov.errors import ProvError
from prov.gitio import Repo

DECISION = {"ours": "ours", "theirs": "theirs", "draft": "draft", "base": "manual", "text": "manual"}


def pending_path(repo: Repo) -> Path:
    return repo.git_dir / "prov" / "pending.json"


def load_pending(repo: Repo) -> list[dict]:
    try:
        return json.loads(pending_path(repo).read_text())
    except FileNotFoundError:
        return []


def add_pending(repo: Repo, decision: dict) -> None:
    pending = [p for p in load_pending(repo) if p["content_hash"] != decision["content_hash"]]
    pending.append({"llm_model": None, "llm_prompt_hash": None, **decision})
    pending_path(repo).parent.mkdir(exist_ok=True)
    pending_path(repo).write_text(json.dumps(pending, indent=1))


def write_atomic(f: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=f.parent, prefix=f".{f.name}.")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.chmod(tmp, f.stat().st_mode)
    os.replace(tmp, f)


def replace_block(raw: str, block, new: str) -> str:
    """Swap the marker block for `new`, keeping the file's newline style."""
    lines = raw.splitlines(keepends=True)
    crlf = "\r\n" in raw
    if crlf:
        new = new.replace("\r\n", "\n").replace("\n", "\r\n")
    if new and not new.endswith(("\n", "\r\n")) and block.end_line < len(lines):
        new += "\r\n" if crlf else "\n"
    return "".join(lines[: block.start_line - 1]) + new + "".join(lines[block.end_line :])


def apply(repo: Repo, hunk_id: str, choice: str, *, expected_hash: str, text: str | None = None,
          llm_model: str | None = None, llm_prompt_hash: str | None = None) -> dict:
    path = hunk_id.rsplit("#", 1)[0]
    hunk = next((h for h in conflicts.current_hunks(repo, path) if h.block.content_hash == expected_hash), None)
    if hunk is None:
        raise ProvError("stale_hunk", f"{hunk_id} changed since analysis", hint="re-run `git prov analyze`")
    b = hunk.block
    if choice in ("draft", "text"):
        if text is None:
            raise ProvError("usage", f"--take {choice} needs replacement text")
        new = text
    else:
        new = {"ours": b.ours, "theirs": b.theirs, "base": b.base or ""}[choice]

    f = repo.root / path
    out = replace_block(f.read_bytes().decode("utf-8", "surrogateescape"), b, new)
    write_atomic(f, out.encode("utf-8", "surrogateescape"))

    report = load_report(repo)
    hr = next((h for fc in (report.files if report else []) for h in fc.hunks if h.content_hash == expected_hash), None)
    add_pending(repo, {
        "hunk_id": hr.id if hr else hunk_id, "path": path, "content_hash": expected_hash, "choice": DECISION[choice],
        "ours_badge": hr.ours.badge.value if hr else "NO_METADATA",
        "theirs_badge": hr.theirs.badge.value if hr else "NO_METADATA",
        "llm_model": llm_model, "llm_prompt_hash": llm_prompt_hash,
    })

    remaining = len(conflicts.parse_blocks(out))
    if remaining == 0:
        repo.run(["add", "--", path])
    return {"hunk_id": hunk_id, "applied": choice, "file_staged": remaining == 0, "remaining_hunks_in_file": remaining}
