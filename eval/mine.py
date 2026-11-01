"""Ground-truth backport pairs from real history (docs/10 §1)."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from prov.gitio import Repo

DJANGO = re.compile(r"^Backport of ([0-9a-f]{7,40}) from (\S+)", re.M)
CHERRY = re.compile(r"\(cherry picked from commit ([0-9a-f]{7,40})\)")

PROFILES = {
    "django": {"branches": r"stable/\d+\.\d+\.x", "grep": "^Backport of ", "regex": DJANGO, "source": "main"},
    "cpython": {"branches": r"3\.\d+", "grep": "cherry picked from commit", "regex": CHERRY, "source": "main"},
}


def remote_branches(repo: Repo, pattern: str) -> list[str]:
    refs = repo.run(["for-each-ref", "--format=%(refname:short)", "refs/remotes/origin"]).split()
    return sorted(r.removeprefix("origin/") for r in refs if re.fullmatch(pattern, r.removeprefix("origin/")))


def patch_ids(repo: Repo, shas: list[str]) -> dict[str, str]:
    if not shas:
        return {}
    diffs = repo.run(["diff-tree", "-p", "--root", "--stdin"], input="\n".join(shas) + "\n")
    return {s: p for p, s in (ln.split() for ln in repo.run(["patch-id", "--stable"], input=diffs).splitlines())}


def mine(repo: Repo, name: str, since: str = "2024-01-01", branches: list[str] | None = None) -> list[dict]:
    prof = PROFILES[name]
    pairs = []
    for b in branches or remote_branches(repo, prof["branches"]):
        log = repo.run(["log", f"origin/{b}", "--no-merges", f"--since={since}", f"--grep={prof['grep']}",
                        "--format=%H%x00%s%x00%B%x1e"], check=False)
        raw = []
        for rec in log.split("\x1e"):
            rec = rec.strip("\n")
            if not rec:
                continue
            sha, subject, body = rec.split("\x00", 2)
            m = prof["regex"].findall(body)
            if m:
                raw.append((sha, m[-1] if isinstance(m[-1], str) else m[-1][0], subject))
        resolved = repo.resolve_commits(sorted({o for _, o, _ in raw}))
        pids = patch_ids(repo, [s for s, _, _ in raw] + sorted(set(resolved.values())))
        for sha, orig_raw, subject in raw:
            orig = resolved.get(orig_raw)
            if not orig:
                continue  # original outside the shallow window
            files = repo.run(["diff-tree", "--no-commit-id", "--name-only", "-r", sha]).split()
            pairs.append({"repo": name, "branch": b, "backport": sha, "original": orig, "subject": subject,
                          "same_patch_id": pids.get(sha) is not None and pids.get(sha) == pids.get(orig), "files": files})
    return pairs


def negatives(repo: Repo, name: str, since: str, branches: list[str], limit: int) -> list[dict]:
    """Stable-branch commits that are not backports (release notes, version bumps): any match is a false positive."""
    prof = PROFILES[name]
    out = []
    for b in branches:
        log = repo.run(["log", f"origin/{b}", "--no-merges", f"--since={since}", "--format=%H%x00%B%x1e"], check=False)
        for rec in log.split("\x1e"):
            rec = rec.strip("\n")
            if rec and not prof["regex"].search(rec.split("\x00", 1)[1]):
                out.append({"repo": name, "branch": b, "backport": rec.split("\x00")[0], "original": None})
    return out[:limit]


def main(name: str, argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--since", default="2024-01-01")
    ap.add_argument("--branches", default="")
    ap.add_argument("--out", default=f"eval/data/{name}_pairs.jsonl")
    a = ap.parse_args(argv)
    repo = Repo(a.repo)
    pairs = mine(repo, name, a.since, [b for b in a.branches.split(",") if b] or None)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text("".join(json.dumps(p) + "\n" for p in pairs))
    edited = sum(not p["same_patch_id"] for p in pairs)
    print(f"{len(pairs)} pairs ({edited} edited) -> {a.out}")
