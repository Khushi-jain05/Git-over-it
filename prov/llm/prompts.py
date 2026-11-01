"""Prompts (verbatim, docs/07 §5) and the per-hunk context builder (§4)."""

from __future__ import annotations

import json
import os

from prov.llm.ratelimit import estimate_tokens
from prov.models import ConflictReport, HunkReport, SideReport

DRAFT_SYSTEM = """You help a developer resolve one Git merge conflict hunk during a backport or promotion between long-lived branches.

Rules:
1. The EVIDENCE section is authoritative. Never say a side is tested, deployed, verified or safe unless EVIDENCE says so. If EVIDENCE has no record, say "no test evidence".
2. Prefer preserving the behaviour of the side with stronger evidence (PROD_VERIFIED > TESTED_ELSEWHERE > UNVERIFIED > FAILED). Bring over changes from the weaker side only when they are clearly needed (renames, API changes the rest of the file depends on).
3. Any line in your resolved code that comes from the weaker side, or that you wrote yourself, must be listed in untested_lines.
4. Output only code for the region between the conflict markers. No markers, no surrounding code, no commentary inside the code.
5. Keep explanations under 60 words each, plain English, describe what the code does, not who wrote it.
6. If you cannot produce a safe combination, set strategy to "manual" and explain why in risk_notes.
Return JSON matching the schema."""

DRAFT_USER = """FILE: {path}  LANGUAGE: {language}
OPERATION: {operation} of {theirs_short} "{theirs_subject}" onto {ours_branch}

CONTEXT BEFORE:
{before}

<<<<<<< OURS ({ours_branch})
{ours}
||||||| BASE
{base}
=======
{theirs}
>>>>>>> THEIRS ({theirs_short})

CONTEXT AFTER:
{after}

EVIDENCE:
{evidence_json}

SIMILAR PAST RESOLUTIONS:
{past_or_none}"""

JUDGE_SYSTEM = """You compare two Git commits and decide whether they implement the same logical change (for example, a fix and its backport to an older branch, possibly adapted to older code). Different formatting, renamed variables, or adaptation to an older API still count as the same change. Two different fixes in the same area do not.
Return JSON: {"same_change": bool, "confidence": number 0..1, "reason": "<= 30 words"}."""

JUDGE_USER = """COMMIT A {sha_a} on {branch_a}: {subject_a}
{diff_a}

COMMIT B {sha_b} on {branch_b}: {subject_b}
{diff_b}"""

LANG = {".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "tsx", ".java": "java", ".go": "go",
        ".rb": "ruby", ".rs": "rust", ".c": "c", ".h": "c", ".cpp": "cpp", ".md": "markdown"}


def language(path: str) -> str:
    return LANG.get(os.path.splitext(path)[1], "text")


def _evidence_line(e) -> str:
    if e.record is None:
        return f"{e.via.env.value if e.via.env else '?'} deployed (inferred: {e.note}) via {e.via.level.value} on {e.via.branch}"
    r = e.record
    return (f"{r.env.value} {r.kind.value} {r.result.value} {r.suite or ''} {r.at.date().isoformat()} "
            f"via {e.via.level.value} match on {e.via.branch}").replace("  ", " ")


def side_evidence(s: SideReport) -> dict:
    o = s.origins[0] if s.origins else None
    return {
        "origin": {"sha": o.sha[:7], "subject": o.subject, "author": o.author, "date": o.date.date().isoformat()} if o else None,
        "branch_chain": sorted({m.branch for m in s.matches if m.branch}),
        "badge": s.badge.value,
        "tier": s.tier.value,
        "evidence": [_evidence_line(e) for e in s.evidence[:3]] or ["no test evidence"],
    }


def _context(lines: list[str], start: int, end: int, n: int) -> tuple[str, str]:
    before = lines[max(0, start - 1 - n) : start - 1] if n else []
    after = lines[end : end + n] if n else []
    return "".join(before).rstrip("\n"), "".join(after).rstrip("\n")


def build_draft_prompt(report: ConflictReport, hunk: HunkReport, working_text: str, ours_branch: str,
                       theirs_subject: str, past: list[str], max_tokens: int) -> str:
    lines = working_text.splitlines(keepends=True)
    evidence = json.dumps({"ours": side_evidence(hunk.ours), "theirs": side_evidence(hunk.theirs)}, indent=1)
    for n in (20, 8, 0):
        before, after = _context(lines, hunk.start_line, hunk.end_line, n)
        user = DRAFT_USER.format(
            path=hunk.path, language=language(hunk.path), operation=report.operation,
            theirs_short=(report.theirs_ref or "")[:7], theirs_subject=theirs_subject, ours_branch=ours_branch,
            before=before, ours=hunk.ours.text.rstrip("\n"), base=(hunk.base.text if hunk.base else "").rstrip("\n"),
            theirs=hunk.theirs.text.rstrip("\n"), after=after, evidence_json=evidence,
            past_or_none="\n\n".join(past) if past else "none")
        if estimate_tokens(DRAFT_SYSTEM + user) <= max_tokens:
            break
    return user


def build_judge_prompt(a: dict, b: dict) -> str:
    def clip(diff: str) -> str:
        return "\n".join(diff.splitlines()[:120])

    return JUDGE_USER.format(sha_a=a["sha"][:10], branch_a=a["branch"], subject_a=a["subject"], diff_a=clip(a["diff"]),
                             sha_b=b["sha"][:10], branch_b=b["branch"], subject_b=b["subject"], diff_b=clip(b["diff"]))
