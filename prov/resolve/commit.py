"""Finalize: gates + trailers + Git's own continue command + resolution note (docs/06 §4)."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from prov import config
from prov.analyze.conflicts import operation, parse_blocks
from prov.analyze.report import load_report, metadata_state
from prov.errors import ProvError
from prov.gitio import Repo
from prov.identity.trailers import change_id_for, parse_trailers
from prov.models import HunkDecision, ResolutionRecord, Tier
from prov.resolve import rerere
from prov.resolve.apply import load_pending, pending_path
from prov.resolve.gates import run_gates
from prov.store.notes import append_note_line, record, write_resolution
from prov.store.records import canonical

EVIDENCE = {Tier.recorded: "notes", Tier.inferred: "inferred", Tier.none: "none"}
CONTINUE = {"cherry-pick": ["cherry-pick", "--continue"], "revert": ["revert", "--continue"],
            "rebase": ["rebase", "--continue"], "am": ["am", "--continue"]}


def _summary(decisions: list[dict], n_hunks: int) -> str:
    choices = {d["choice"] for d in decisions}
    if len(choices) == 1 and len(decisions) == n_hunks:
        c = choices.pop()
        badge = decisions[0].get(f"{c}_badge") if c in ("ours", "theirs") else None
        return f"{c}={badge}" if badge else c
    return "mixed" if decisions else "manual"


def finalize(repo: Repo, *, run_tests: bool = True, quick: bool = False, allow_failed_gates: bool = False,
             message: str | None = None, empty: str | None = None) -> dict:
    """empty: what to do when the resolution keeps nothing of the incoming change ("commit" | "skip" | None=ask)."""
    op, theirs = operation(repo)
    if op == "none":
        raise ProvError("no_conflict", "no operation in progress")
    for path, stages in repo.ls_files_unmerged().items():
        # Resolved outside git prov (by hand, or replayed by rerere): stage it once no markers are left.
        f = repo.root / path
        if {1, 2, 3} <= set(stages) and f.exists() and not parse_blocks(f.read_bytes().decode("utf-8", "surrogateescape")):
            repo.run(["add", "--", path])
    if repo.ls_files_unmerged():
        raise ProvError("unresolved", "unresolved conflicts remain", hint="apply a choice for every hunk, then retry")

    report = load_report(repo)
    pending = load_pending(repo)
    decided = {p["content_hash"] for p in pending}
    for fc in report.files if report else []:  # hunks resolved outside git prov (editor buttons, by hand, rerere)
        for h in fc.hunks:
            if h.content_hash not in decided:
                pending.append({"hunk_id": h.id, "path": fc.path, "content_hash": h.content_hash, "choice": "manual",
                                "ours_badge": h.ours.badge.value, "theirs_badge": h.theirs.badge.value})
    conflicted = {fc.path: len(fc.hunks) for fc in report.files} if report else {}
    for p in pending:
        conflicted.setdefault(p["path"], 0)
    if not conflicted:
        conflicted = {p: 0 for p in repo.run(["diff", "--cached", "--name-only", "-z"]).split("\0") if p}

    cfg = config.load(repo.root)
    gates, errors, tested_with = run_gates(repo, cfg, sorted(conflicted), side_refs=["HEAD", theirs],
                                           tests=run_tests, quick=quick)
    failed = not all(gates.values())
    if failed and not allow_failed_gates:
        raise ProvError("gates_failed", "; ".join(errors), hint="fix the file or pass --allow-failed-gates")

    if op in ("cherry-pick", "revert") and repo.ok(["diff", "--cached", "--quiet", "HEAD"]):
        # Every hunk kept OURS: the pick changes nothing. Git refuses an empty commit, so make the user choose.
        if empty == "skip":
            repo.run(["-c", "core.editor=true", op, "--skip"])
            pending_path(repo).unlink(missing_ok=True)
            (repo.git_dir / "prov" / "report.json").unlink(missing_ok=True)
            return {"commit": None, "skipped": True, "source": theirs, "gates": gates}
        if empty != "commit":
            raise ProvError("empty_resolution", f"the resolution keeps nothing from {op} {theirs[:10]}: it would be empty",
                            hint="`git prov commit --empty commit` records the decision in an empty commit; "
                                 "`git prov commit --empty skip` drops the pick")

    merge_msg = repo.git_dir / ("rebase-apply/final-commit" if op == "am" else "MERGE_MSG")  # am --continue reads it
    if message is None:
        message = merge_msg.read_text() if merge_msg.exists() else ""
    msg = "".join(ln for ln in message.splitlines(keepends=True) if not ln.startswith("#")).rstrip() + "\n"
    if op == "cherry-pick" and f"(cherry picked from commit {theirs})" not in msg:
        msg = msg.rstrip() + f"\n\n(cherry picked from commit {theirs})\n"

    original = theirs or repo.rev_parse("HEAD")
    orig_body = repo.run(["show", "-s", "--format=%B", original]) if original else ""
    change_id = change_id_for(original or "", parse_trailers(orig_body)[1])
    state = report.metadata_state if report else metadata_state(repo)
    by_path: dict[str, list[dict]] = {}
    for p in pending:
        by_path.setdefault(p["path"], []).append(p)
    single = [f"Prov-Change-Id: {change_id}", f"Prov-Evidence: {EVIDENCE[state]}"]
    if failed:
        single.append("Prov-Gates: failed")
    resolved = []
    for path, n in sorted(conflicted.items()):
        ds = by_path.get(path, [])
        n = n or len(ds)
        resolved.append(f"Prov-Resolved: {path} {_summary(ds, n)} hunks={n}")
    trailers = [single[0], *resolved, *single[1:]]
    args = ["--if-exists", "replace", "--trailer", single[0], "--if-exists", "addIfDifferent"]
    for t in resolved:
        args += ["--trailer", t]
    args += ["--if-exists", "replace"]
    for t in single[1:]:
        args += ["--trailer", t]
    msg = repo.interpret_trailers(msg, args)

    started = time.time()
    if empty == "commit" and op in ("cherry-pick", "revert") and repo.ok(["diff", "--cached", "--quiet", "HEAD"]):
        repo.run(["commit", "--allow-empty", "--no-edit", "-F", "-"], input=msg)  # ends a single pick
        if (repo.git_dir / "sequencer").exists():  # multi-commit pick: carry on with the rest
            repo.run(["-c", "core.editor=true", *CONTINUE[op]], check=False)
    elif op in CONTINUE:
        merge_msg.write_text(msg)
        repo.run(["-c", "core.editor=true", *CONTINUE[op]])
    else:  # merge
        repo.run(["commit", "--no-edit", "-F", "-"], input=msg)

    sha = repo.rev_parse("HEAD")
    decisions = [HunkDecision(hunk_id=p["hunk_id"], choice=p["choice"], ours_badge=p["ours_badge"],
                              theirs_badge=p["theirs_badge"], llm_model=p.get("llm_model"),
                              llm_prompt_hash=p.get("llm_prompt_hash")) for p in pending]
    write_resolution(repo, ResolutionRecord(commit=sha, operation=op, source=theirs, change_id=change_id,
                                            decisions=decisions, gates=gates, at=datetime.now(UTC)))
    for entry in rerere.capture(repo, started):
        append_note_line(repo, "prov/resolutions", sha, canonical({"rerere": entry}))
    if tested_with:  # the new tree passed locally; `local` never counts toward PROD_VERIFIED
        record(repo, sha, kind="test", env="local", result="PASSED", suite=tested_with, runner="git-prov commit")
    pending_path(repo).unlink(missing_ok=True)
    (repo.git_dir / "prov" / "report.json").unlink(missing_ok=True)
    return {"commit": sha, "change_id": change_id, "trailers": trailers, "gates": gates,
            "resolution_note": "refs/notes/prov/resolutions"}
