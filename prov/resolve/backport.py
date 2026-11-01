"""`git prov backport`: cherry-pick into worktrees, never touching the user's checkout, never pushing (docs/06 §6)."""

from __future__ import annotations

from prov.analyze.report import analyze
from prov.errors import ProvError
from prov.gitio import Repo


def preview(repo: Repo, sha: str, targets: list[str]) -> dict:
    """Which targets would conflict, via `git merge-tree --write-tree` (no worktree touched)."""
    out = {}
    for b in targets:
        args = ["merge-tree", "--write-tree", "--name-only", "--no-messages", f"--merge-base={sha}^", b, sha]
        res = repo.run(args, check=False)
        lines = res.splitlines()
        conflicted = [ln for ln in lines[1:] if ln.strip()]
        out[b] = {"clean": not conflicted, "conflicted_files": conflicted}
    return out


def backport(repo: Repo, sha: str, targets: list[str], *, no_commit: bool = False) -> dict:
    full = repo.rev_parse(f"{sha}^{{commit}}")
    if not full:
        raise ProvError("usage", f"unknown revision {sha}")
    results = {}
    for b in targets:
        wt = repo.prov_dir / "wt" / b.replace("/", "_")
        if wt.exists():
            raise ProvError("usage", f"worktree {wt} already exists", hint=f"git worktree remove {wt}")
        if not repo.ok(["worktree", "add", "-q", str(wt), b]):  # branch checked out elsewhere: detach instead
            repo.run(["worktree", "add", "-q", "--detach", str(wt), b])
        w = Repo(wt)
        args = ["cherry-pick", "-x", *(["--no-commit"] if no_commit else []), full]
        if w.ok(args):
            results[b] = {"status": "clean", "worktree": str(wt), "head": w.rev_parse("HEAD")}
            continue
        if not w.ls_files_unmerged():
            results[b] = {"status": "failed", "worktree": str(wt), "error": "cherry-pick failed without conflicts"}
            continue
        rep = analyze(w)
        path = w.git_dir / "prov" / "report.json"
        results[b] = {"status": "conflict", "worktree": str(wt), "report": str(path),
                      "hunks": sum(len(f.hunks) for f in rep.files)}
    return {"commit": full, "targets": results}
