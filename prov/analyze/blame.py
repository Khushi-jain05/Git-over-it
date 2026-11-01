"""Per-side origin commits via blame (docs/05 §4)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from prov.gitio import Repo
from prov.models import Origin

HEADER = re.compile(r"^([0-9a-f]{40}) \d+ \d+(?: \d+)?$")


def _find(blob_lines: list[str], side_lines: list[str], hint: int) -> int | None:
    n = len(side_lines)
    if blob_lines[hint : hint + n] == side_lines:
        return hint
    hits = [i for i in range(len(blob_lines) - n + 1) if blob_lines[i : i + n] == side_lines]
    return min(hits, key=lambda i: abs(i - hint)) if hits else None


def origin_of(repo: Repo, sha: str, lines: int, info: dict | None = None) -> Origin:
    if info is None:
        out = repo.run(["show", "-s", "--format=%s%x00%an%x00%at", sha])
        subj, author, at = out.rstrip("\n").split("\x00")
        info = {"summary": subj, "author": author, "author-time": at}
    subject = "(boundary)" if "boundary" in info else info.get("summary", "")
    date = datetime.fromtimestamp(int(info.get("author-time", 0)), tz=UTC)
    return Origin(sha=sha, subject=subject, author=info.get("author", ""), date=date, lines=lines,
                  branches=repo.for_each_ref_contains(sha))


def origins(repo: Repo, ref: str, path: str, text: str, hint: int, base_ref: str | None,
            base_text: str = "") -> list[Origin]:
    side_lines = text.splitlines()
    if not side_lines:
        return [origin_of(repo, _deleting_commit(repo, ref, path, base_text), 0)]
    blob = repo.run(["show", f"{ref}:{path}"], check=False).splitlines()
    start = _find(blob, side_lines, hint)
    if start is None:
        sha = repo.run(["log", "-1", "--format=%H", ref, "--", path]).strip()
        return [origin_of(repo, sha, len(side_lines))] if sha and sha not in _shallow(repo) else []
    out = repo.blame_porcelain(ref, path, start + 1, start + len(side_lines))
    counts: dict[str, int] = {}
    info: dict[str, dict] = {}
    cur = ""
    for line in out.splitlines():
        if line.startswith("\t"):
            counts[cur] = counts.get(cur, 0) + 1
        elif m := HEADER.match(line):
            cur = m.group(1)
            info.setdefault(cur, {})
        else:
            k, _, v = line.partition(" ")
            info[cur][k] = v
    result = [origin_of(repo, sha, n, info[sha]) for sha, n in counts.items() if sha not in _shallow(repo)]
    # Lines that already existed at the merge base are context, not this side's change: rank side-specific
    # commits first, then by line count (docs/05 says "by lines desc"; this keeps origins[0] meaningful).
    in_base = {o.sha: bool(base_ref) and repo.ok(["merge-base", "--is-ancestor", o.sha, base_ref]) for o in result}
    return sorted(result, key=lambda o: (in_base[o.sha], -o.lines))


def _shallow(repo: Repo) -> set[str]:
    """Shallow-clone boundary commits: history was cut there, so they are not where lines came from (-> unknown)."""
    common = Path(repo.run(["rev-parse", "--git-common-dir"]).strip())
    f = common if common.is_absolute() else repo.root / common
    return set((f / "shallow").read_text().split()) if (f / "shallow").exists() else set()


def _deleting_commit(repo: Repo, ref: str, path: str, base_text: str) -> str:
    first = next((ln.strip() for ln in base_text.splitlines() if ln.strip()), "")
    if first:
        sha = repo.run(["log", "-1", "--format=%H", f"-S{first}", ref, "--", path], check=False).strip()
        if sha:
            return sha
    return repo.run(["log", "-1", "--format=%H", ref, "--", path]).strip() or repo.rev_parse(ref) or ref
