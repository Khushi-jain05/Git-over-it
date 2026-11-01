"""Deterministic badge + recommendation rules (docs/05 §6-7). The LLM never touches this."""

from __future__ import annotations

from prov.models import Badge, SideReport, Tier


def decide(side: SideReport, metadata_state: Tier) -> tuple[Badge, Tier]:
    strong = [e for e in side.evidence if e.via.confidence >= 0.90]
    okay = [e for e in side.evidence if e.via.confidence >= 0.60]

    def has(items, kind, envs, result="PASSED"):
        return any(e.record and e.record.kind == kind and e.record.env in envs and e.record.result == result
                   and not e.rolled_back_by for e in items)

    tests = [e for e in okay if e.record and e.record.kind == "test"]
    latest = max(tests, key=lambda e: e.record.at, default=None)
    if latest and latest.record.result == "FAILED":
        return Badge.FAILED, Tier.recorded

    if has(strong, "deploy", {"prod"}) and has(strong, "test", {"uat", "prod"}):
        return Badge.PROD_VERIFIED, Tier.recorded

    inferred_prod = any(e.tier == Tier.inferred and e.via.env == "prod" for e in strong)
    if inferred_prod and has(strong, "test", {"uat", "prod", "release", "dev"}):
        return Badge.PROD_VERIFIED, Tier.inferred

    if has(okay, "test", {"uat", "prod", "release", "dev", "local"}):
        return Badge.TESTED_ELSEWHERE, Tier.recorded

    if metadata_state == Tier.none and not side.evidence:
        return Badge.NO_METADATA, Tier.none
    if inferred_prod:
        return Badge.UNVERIFIED, Tier.inferred
    return Badge.UNVERIFIED, Tier.recorded if metadata_state == Tier.recorded else Tier.none


def rank(badge: Badge, tier: Tier) -> int:
    if badge == Badge.PROD_VERIFIED:
        return 4 if tier == Tier.recorded else 3
    return {Badge.FAILED: 0, Badge.NO_METADATA: 1, Badge.UNVERIFIED: 1, Badge.TESTED_ELSEWHERE: 2}[badge]


def recommend(ours: SideReport, theirs: SideReport) -> tuple[str, str]:
    ro, rt = rank(ours.badge, ours.tier), rank(theirs.badge, theirs.tier)
    if not ours.text.strip() and rt >= 2:
        return "take_theirs", f"Other side deletes code; this side is {theirs.badge.value}"
    if not theirs.text.strip() and ro >= 2:
        return "take_ours", f"Other side deletes code; this side is {ours.badge.value}"
    if rt - ro >= 2:
        return "take_theirs", f"THEIRS is {theirs.badge.value}; OURS is {ours.badge.value}"
    if ro - rt >= 2:
        return "take_ours", f"OURS is {ours.badge.value}; THEIRS is {theirs.badge.value}"
    if ro >= 2 and rt >= 2:
        return "combine", "Both sides have test evidence; combine and re-test"
    return "manual", "No side has stronger evidence"
