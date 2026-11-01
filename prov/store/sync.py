"""Notes sync and install (docs/02, docs/03 §3)."""

from __future__ import annotations

from prov.errors import ProvError
from prov.gitio import Repo
from prov.store.records import ALL_REFS

HOOK = '\n# git-prov\ngit prov sync --push-only --quiet || true\n'


def install(repo: Repo, remote: str = "origin") -> list[str]:
    done = []
    remotes = repo.run(["remote"]).split()
    if remote in remotes:
        spec = f"+refs/notes/prov/*:refs/notes/remotes/{remote}/prov/*"
        if spec not in repo.run(["config", "--get-all", f"remote.{remote}.fetch"], check=False).split():
            repo.run(["config", "--add", f"remote.{remote}.fetch", spec])
            done.append(f"remote.{remote}.fetch += {spec}")
    rewrite = repo.run(["config", "--get-all", "notes.rewriteRef"], check=False).split()
    for ref in ("refs/notes/prov/tests", "refs/notes/prov/deploys"):
        if ref not in rewrite:
            repo.run(["config", "--add", "notes.rewriteRef", ref])
            done.append(f"notes.rewriteRef += {ref}")
    for k, v in [("notes.rewrite.amend", "true"), ("notes.rewrite.rebase", "true"), ("rerere.enabled", "true"),
                 ("merge.conflictStyle", "diff3"), ("prov.installed", "1")]:
        repo.run(["config", k, v])
    hooks = repo.common_dir / "hooks"
    hooks.mkdir(exist_ok=True)
    hook = hooks / "pre-push"
    text = hook.read_text() if hook.exists() else "#!/bin/sh\n"
    if "git prov sync" not in text:
        hook.write_text(text + HOOK)
        hook.chmod(0o755)
        done.append("pre-push hook")
    return done


def sync(repo: Repo, remote: str = "origin", *, fetch: bool = True, push: bool = True) -> dict:
    if remote not in repo.run(["remote"]).split():
        raise ProvError("usage", f"no remote named {remote}")
    refs = [r.removeprefix("prov/") for r in ALL_REFS]
    merged, pushed = [], []

    def fetch_merge():
        for r in refs:
            tracking = f"refs/notes/remotes/{remote}/prov/{r}"
            repo.run(["fetch", "--quiet", remote, f"+refs/notes/prov/{r}:{tracking}"], check=False)
            if not repo.rev_parse(tracking):
                continue
            if repo.rev_parse(f"refs/notes/prov/{r}"):
                repo.run(["notes", f"--ref=prov/{r}", "merge", "--quiet", "-s", "cat_sort_uniq", tracking])
            else:
                repo.run(["update-ref", f"refs/notes/prov/{r}", tracking])
            merged.append(r)

    if fetch:
        fetch_merge()
    if push:
        for r in refs:
            if not repo.rev_parse(f"refs/notes/prov/{r}"):
                continue
            for _ in range(3):
                # --no-verify: our own pre-push hook would call sync again
                if repo.ok(["push", "--quiet", "--no-verify", remote, f"refs/notes/prov/{r}:refs/notes/prov/{r}"]):
                    pushed.append(r)
                    break
                fetch_merge()  # non-fast-forward: merge remote side, retry
    return {"merged": sorted(set(merged)), "pushed": pushed}
