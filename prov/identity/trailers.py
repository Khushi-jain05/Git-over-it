"""L0: trailers (docs/04 §L0, docs/03 §5)."""

from __future__ import annotations

import hashlib
import re

CHERRY = re.compile(r"\(cherry picked from commit ([0-9a-f]{7,40})\)")
DJANGO = re.compile(r"^Backport of ([0-9a-f]{7,40}) from (\S+)", re.MULTILINE)
CHANGE = re.compile(r"^(?:Prov-Change-Id|Change-Id):\s*(I[0-9a-f]{40})\s*$", re.MULTILINE)


def parse_trailers(body: str) -> tuple[str | None, str | None]:
    """-> (picked_from as written, may be short; change_id)"""
    m = CHERRY.findall(body) or DJANGO.findall(body)
    picked = (m[-1] if isinstance(m[-1], str) else m[-1][0]) if m else None
    c = CHANGE.findall(body)
    return picked, (c[-1] if c else None)


def picked_all(body: str) -> list[str]:
    """Every picked-from / Backport-of target, in order (a squashed backport names several)."""
    return list(dict.fromkeys([*CHERRY.findall(body), *(m[0] for m in DJANGO.findall(body))]))


def change_id_for(original_sha: str, existing: str | None) -> str:
    if existing:  # inherit from the original commit's trailer
        return existing
    return "I" + hashlib.sha1(f"prov:{original_sha}".encode()).hexdigest()


def linked(idx, sha: str, depth: int = 3) -> dict[str, str]:
    """Commits linked to `sha` by picked-from / change-id chains, up to `depth` hops. -> {sha: reason}"""
    out: dict[str, str] = {}
    seen, frontier = {sha}, [sha]
    for _ in range(depth):
        nxt = []
        for s in frontier:
            row = idx.commit(s)
            if row is None:
                continue
            nbrs = [(c, f"trailer: cherry picked from {s[:7]}") for c in idx.by_picked_from(s)]
            if row["picked_from"]:
                nbrs.append((row["picked_from"], f"trailer: {s[:7]} cherry picked from it"))
            nbrs += [(c, "same Prov-Change-Id") for c in idx.by_change_id(row["change_id"])]
            for c, reason in nbrs:
                if c not in seen:
                    seen.add(c)
                    out[c] = reason
                    nxt.append(c)
        frontier = nxt
    return out
