"""Structural auto-solve with Mergiraf (docs/06 §1). Only via `git prov resolve --mergiraf`, never during analyze."""

from __future__ import annotations

import shutil
import subprocess

from prov import config
from prov.analyze import conflicts
from prov.analyze.report import load_report
from prov.errors import ProvError
from prov.gitio import Repo
from prov.resolve.apply import add_pending, write_atomic
from prov.resolve.gates import check_parse


def solve(repo: Repo, paths: list[str] | None = None) -> dict:
    cfg = config.load(repo.root)
    binary = shutil.which(cfg.mergiraf_binary)
    if not cfg.mergiraf_enabled or binary is None:
        raise ProvError("usage", "mergiraf not available", hint="install it (https://mergiraf.org) or enable [mergiraf]")
    report = load_report(repo)
    by_hash = {h.content_hash: h for fc in (report.files if report else []) for h in fc.hunks}
    results = {}
    for path in sorted(repo.ls_files_unmerged()):
        if paths and path not in paths:
            continue
        before = conflicts.current_hunks(repo, path)
        if not before:
            continue
        f = repo.root / path
        original = f.read_bytes()
        backup = repo.git_dir / "prov" / "backup" / path
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(original)
        p = subprocess.run([binary, "solve", "-p", str(f)], capture_output=True)
        out = p.stdout.decode("utf-8", "surrogateescape")
        remaining = conflicts.parse_blocks(out)
        originals = {(h.block.ours, h.block.theirs) for h in before}
        if not out.strip() or p.returncode not in (0, 1):
            note = "mergiraf failed"
        elif remaining and (len(remaining) >= len(before) or any((b.ours, b.theirs) not in originals for b in remaining)):
            note = "no structural resolution"  # partial output we cannot map back to hunks: keep ours
        elif not remaining and check_parse(path, out):
            note = "mergiraf output does not parse"
        else:
            note = None
        if note:
            results[path] = {"solved": 0, "remaining": len(before), "note": note}
            continue
        write_atomic(f, out.encode("utf-8", "surrogateescape"))
        still = {(b.ours, b.theirs) for b in remaining}
        solved = [h for h in before if (h.block.ours, h.block.theirs) not in still]
        for h in solved:
            hr = by_hash.get(h.block.content_hash)
            add_pending(repo, {"hunk_id": hr.id if hr else f"{path}#{h.index}", "path": path,
                               "content_hash": h.block.content_hash, "choice": "mergiraf",
                               "ours_badge": hr.ours.badge.value if hr else "NO_METADATA",
                               "theirs_badge": hr.theirs.badge.value if hr else "NO_METADATA"})
        if not remaining:
            repo.run(["add", "--", path])
        results[path] = {"solved": len(solved), "remaining": len(remaining), "staged": not remaining}
    return {"files": results}
