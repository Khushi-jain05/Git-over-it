"""`git prov draft`: LLM explanation + draft for one hunk, gated on a temp copy (docs/07 §5)."""

from __future__ import annotations

import json

from prov import config
from prov.analyze import conflicts
from prov.analyze.report import analyze, load_report
from prov.errors import ProvError
from prov.gitio import Repo
from prov.llm.prompts import DRAFT_SYSTEM, build_draft_prompt
from prov.llm.provider import for_repo
from prov.llm.schemas import DraftOut
from prov.models import ConflictReport, Draft, HunkReport, ResolutionRecord
from prov.resolve.apply import replace_block
from prov.resolve.gates import check_text


def drafts_dir(repo: Repo):
    return repo.git_dir / "prov" / "drafts"


def load_draft(repo: Repo, content_hash: str) -> Draft | None:
    p = drafts_dir(repo) / f"{content_hash}.json"
    return Draft.model_validate_json(p.read_text()) if p.exists() else None


def _report(repo: Repo) -> ConflictReport:
    return load_report(repo) or analyze(repo)


def find_hunk(report: ConflictReport, hunk_id: str) -> HunkReport:
    for fc in report.files:
        for h in fc.hunks:
            if h.id == hunk_id:
                return h
    raise ProvError("usage", f"no hunk {hunk_id} in the current report", hint="run `git prov analyze`")


def past_resolutions(repo: Repo, path: str, limit: int = 2) -> list[str]:
    out = []
    for _, commit in reversed(repo.notes_list("prov/resolutions")):
        for line in repo.notes_show("prov/resolutions", commit).splitlines():
            try:
                rec = ResolutionRecord.model_validate_json(line)
            except ValueError:
                continue
            ds = [d for d in rec.decisions if d.hunk_id.rsplit("#", 1)[0] == path]
            if not ds:
                continue
            code = repo.run(["show", "--format=", "-U0", "--no-color", commit, "--", path], check=False)
            plus = [ln[1:] for ln in code.splitlines() if ln.startswith("+") and not ln.startswith("+++")][:10]
            choices = ", ".join(f"{d.hunk_id}={d.choice} (ours {d.ours_badge}, theirs {d.theirs_badge})" for d in ds)
            out.append(f"commit {commit[:7]}: {choices}\n" + "\n".join(plus))
        if len(out) >= limit:
            break
    return out[:limit]


def gate_draft(repo: Repo, report: ConflictReport, hunk: HunkReport, code: str) -> list[str]:
    """Apply the draft to an in-memory copy of the file and run the static gates on it."""
    block = next((h.block for h in conflicts.current_hunks(repo, hunk.path) if h.block.content_hash == hunk.content_hash),
                 None)
    if block is None:
        raise ProvError("stale_hunk", f"{hunk.id} changed since analysis", hint="re-run `git prov analyze`")
    working = (repo.root / hunk.path).read_bytes().decode("utf-8", "surrogateescape")
    candidate = replace_block(working, block, code)
    rest = conflicts.parse_blocks(candidate)  # other unresolved hunks in the file are not the draft's fault
    for b in reversed(rest):
        candidate = replace_block(candidate, b, b.ours)
    sides = [repo.run(["show", f"{r}:{hunk.path}"], check=False) for r in ("HEAD", report.theirs_ref) if r]
    return check_text(hunk.path, candidate, sides)


def draft(repo: Repo, hunk_id: str, *, use_cache: bool = True, model: str | None = None) -> Draft:
    cfg = config.load(repo.root)
    report = _report(repo)
    hunk = find_hunk(report, hunk_id)
    working = (repo.root / hunk.path).read_bytes().decode("utf-8", "surrogateescape")
    ours_branch = repo.run(["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    subject = repo.run(["show", "-s", "--format=%s", report.theirs_ref]).strip() if report.theirs_ref else ""
    user = build_draft_prompt(report, hunk, working, ours_branch, subject, past_resolutions(repo, hunk.path),
                              cfg.llm["max_prompt_tokens"])
    provider = for_repo(repo, cfg, use_cache=use_cache)
    out, meta = provider.complete_json(model=model or cfg.llm["draft_model"], system=DRAFT_SYSTEM, user=user,
                                       schema=DraftOut, max_tokens=2048)
    d = Draft(hunk_id=hunk.id, model=meta["model"], prompt_hash=meta["prompt_hash"], **out.model_dump())
    errors = gate_draft(repo, report, hunk, d.resolved_code)
    if d.strategy == "manual":
        errors.append("model declined to combine (strategy=manual)")
    d.gates_passed, d.gate_errors = not errors, errors
    drafts_dir(repo).mkdir(parents=True, exist_ok=True)
    (drafts_dir(repo) / f"{hunk.content_hash}.json").write_text(d.model_dump_json())
    return d


def warm(repo: Repo) -> dict:
    report = analyze(repo)
    done, failed = [], {}
    for fc in report.files:
        for h in fc.hunks:
            try:
                d = draft(repo, h.id)
                done.append({"hunk_id": h.id, "gates_passed": d.gates_passed})
            except ProvError as e:
                failed[h.id] = e.message
    return {"drafted": done, "failed": failed}


def dump(d: Draft) -> str:
    return json.dumps({"schema_version": 1, **d.model_dump(mode="json")})
