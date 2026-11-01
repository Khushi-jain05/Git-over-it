"""L2: hunk overlap (docs/04 §L2). Diff parsing is shared with L3 and the index."""

from __future__ import annotations

import hashlib
import re

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")
SHA_LINE = re.compile(r"^[0-9a-f]{40}$")
FUNC = re.compile(r"\b(?:def|class|function|func|fn|sub)\s+([A-Za-z_$][\w$]*)")
IDENT = re.compile(r"([A-Za-z_$][\w$]*)\s*\(")

MIN_NORM = 8  # normalised hunks shorter than this (blank lines, lone braces) collide; skip them


def func_name(header: str) -> str | None:
    """Enclosing function/class from the @@ header's funcname context."""
    m = FUNC.search(header) or IDENT.search(header)
    return m.group(1) if m else None


def parse_diff(text: str) -> dict[str, list[dict]]:
    """`git diff-tree -p -U0 --stdin` output -> {sha: [{path, func, minus, plus, new_start, new_len}]}"""
    out: dict[str, list[dict]] = {}
    sha, path, cur = None, None, None
    for line in text.splitlines():
        if SHA_LINE.match(line):
            sha, path, cur = line, None, None
            out.setdefault(sha, [])
        elif line.startswith("diff --git "):
            path, cur = None, None
        elif line.startswith("--- ") and cur is None:
            p = line[4:]
            path = p[2:] if p.startswith("a/") else None
        elif line.startswith("+++ ") and cur is None:
            p = line[4:]
            if p.startswith("b/"):
                path = p[2:]  # else /dev/null: deleted file keeps its old path
        elif m := HUNK.match(line):
            if sha is None:
                continue
            cur = {"path": path or "", "func": func_name(m.group(5)), "minus": [], "plus": [],
                   "new_start": int(m.group(3)), "new_len": int(m.group(4) or 1)}
            out[sha].append(cur)
        elif cur is not None and line[:1] in ("-", "+"):
            cur["minus" if line[0] == "-" else "plus"].append(line[1:])
    return out


def _norm(lines: list[str]) -> str:
    return "\n".join(re.sub(r"\s+", "", ln) for ln in lines if ln.strip())


def hunk_hash(minus: list[str], plus: list[str]) -> str | None:
    """Path-less hash, whitespace-insensitive. None for trivially short hunks."""
    nm, np = _norm(minus), _norm(plus)
    if len(nm) + len(np) < MIN_NORM:
        return None
    return hashlib.sha1(("-" + nm + "\n+" + np).encode("utf-8", "surrogateescape")).hexdigest()


def score(hx: set[str], hc: set[str]) -> tuple[float, int, float]:
    """-> (s2, shared, J)"""
    if not hx or not hc:
        return 0.0, 0, 0.0
    shared = len(hx & hc)
    j = shared / len(hx | hc)
    return max(j, 0.9 * shared / len(hx)), shared, j


def match(idx, x: str, candidates: set[str], min_jaccard: float) -> dict[str, tuple[float, str]]:
    """-> {candidate: (confidence, reason)} for candidates passing the L2 threshold."""
    hx = idx.hunk_hashes(x)
    if not hx or not candidates:
        return {}
    out = {}
    for c, _ in idx.shas_sharing_hunks(hx):
        if c == x or c not in candidates:
            continue
        s2, k, j = score(hx, idx.hunk_hashes(c))
        if s2 >= min_jaccard:
            out[c] = (0.60 + 0.35 * s2, f"{k}/{len(hx)} hunks identical (J={j:.2f})")
    return out


def _swapped(idx, x: str, min_share: float) -> dict[str, bool]:
    """Commits holding X's hunks with - and + swapped -> whether any of those hunks sits in the same file."""
    sw = {hh: h["path"] for h in idx.changes(x) if (hh := hunk_hash(h["plus"], h["minus"]))}
    if not sw:
        return {}
    rows = idx.db.execute(f"SELECT sha, path, hhash FROM hunks WHERE hhash IN ({','.join('?' * len(sw))})", list(sw))
    hits: dict[str, dict[str, bool]] = {}
    for sha, path, hh in rows:
        if sha != x:
            hits.setdefault(sha, {})[hh] = hits.get(sha, {}).get(hh, False) or path == sw[hh]
    return {c: any(same.values()) for c, same in hits.items() if len(same) / len(sw) >= min_share}


def reverts(idx, x: str, min_share: float = 0.5) -> list[str]:
    """Commits that undo X (or that X undoes): X's hunks with - and + swapped, in the same file."""
    return sorted(c for c, same in _swapped(idx, x, min_share).items() if same)


def moves(idx, x: str, min_share: float = 0.5) -> list[str]:
    """Commits that re-add in another file the code X deleted (or delete what X added): a move, not a revert."""
    return sorted(c for c, same in _swapped(idx, x, min_share).items() if not same)
