"""Share rerere resolutions through resolution notes (docs/06 §5, stretch). rr-cache is never pushed by Git."""

from __future__ import annotations

import json

from prov.gitio import Repo


def capture(repo: Repo, since: float) -> list[dict]:
    """rr-cache entries that got a postimage at or after `since` (i.e. during this resolution)."""
    cache = repo.common_dir / "rr-cache"
    out = []
    for d in sorted(cache.glob("*")) if cache.exists() else []:
        pre, post = d / "preimage", d / "postimage"
        if pre.exists() and post.exists() and post.stat().st_mtime >= since - 1:
            out.append({"rr_id": d.name, "preimage": pre.read_bytes().decode("utf-8", "surrogateescape"),
                        "postimage": post.read_bytes().decode("utf-8", "surrogateescape")})
    return out


def import_all(repo: Repo) -> int:
    cache = repo.common_dir / "rr-cache"
    n = 0
    for _, commit in repo.notes_list("prov/resolutions"):
        for line in repo.notes_show("prov/resolutions", commit).splitlines():
            try:
                entry = json.loads(line).get("rerere")
            except (ValueError, AttributeError):
                continue
            if not entry or "/" in entry["rr_id"] or entry["rr_id"].startswith("."):
                continue
            d = cache / entry["rr_id"]
            if (d / "postimage").exists():
                continue
            d.mkdir(parents=True, exist_ok=True)
            (d / "preimage").write_bytes(entry["preimage"].encode("utf-8", "surrogateescape"))
            (d / "postimage").write_bytes(entry["postimage"].encode("utf-8", "surrogateescape"))
            n += 1
    return n
