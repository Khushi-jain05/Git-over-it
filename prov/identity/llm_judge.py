"""L5: LLM judge for ambiguous L3 pairs (docs/04 §L5). Output can only drop a candidate or set a capped confidence."""

from __future__ import annotations

from prov.llm.prompts import JUDGE_SYSTEM, build_judge_prompt
from prov.llm.schemas import JudgeOut


def _side(idx, sha: str, branch: str) -> dict:
    row = idx.commit(sha)
    diff = idx.repo.run(["show", "--format=", "-U3", "--no-color", sha])
    return {"sha": sha, "branch": branch, "subject": row["subject"] if row else "", "diff": diff}


def judge(provider, model: str, idx, a: str, branch_a: str, b: str, branch_b: str) -> JudgeOut:
    user = build_judge_prompt(_side(idx, a, branch_a), _side(idx, b, branch_b))
    out, _ = provider.complete_json(model=model, system=JUDGE_SYSTEM, user=user, schema=JudgeOut, max_tokens=512)
    return out
