"""Matching ladder L0-L5 (docs/04)."""

from __future__ import annotations

from prov.errors import ProvError
from prov.identity import fuzzy, hunks, llm_judge, metadata, trailers
from prov.models import Level, Match

DIFFERENT_BEHAVIOUR_CAP = 0.55  # below the 0.60 evidence threshold: listed, but never carries test evidence
L3_CAP = 50  # ponytail: keep the 50 candidates closest in time after the L3 pre-filter; raise if recall suffers


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _behaviour_guard(idx, x: str, c: str, conf: float, reason: str) -> tuple[float, str]:
    bad = fuzzy.behaviour_differs(idx, x, c)
    if not bad:
        return conf, reason
    shown = ", ".join(dict.fromkeys(bad[:6]))
    return min(conf, DIFFERENT_BEHAVIOUR_CAP), f"{reason}; behaviour differs ({shown})"


TRAILER_MIN_SIM = 0.40  # trailer link whose code is less similar than this is a claim, not evidence
TRAILER_UNVERIFIED = 0.30


def _verify_trailer(idx, x: str, c: str, reason: str) -> tuple[float, str]:
    """A trailer is a claim; the code must back it (s24 lying trailer, edited resolutions, sibling variants)."""
    xr, cr = idx.commit(x), idx.commit(c)
    if xr is None or cr is None:
        return 1.0, reason
    if xr["patch_id"] and xr["patch_id"] == cr["patch_id"]:
        return 1.0, f"{reason}; identical patch-id"
    hx, hc = idx.hunk_hashes(x), idx.hunk_hashes(c)
    if hc and hc <= hx and len(hx) > len(hc):  # the original is one part of a squashed backport
        return 0.95, f"{reason}; all {len(hc)} of its hunks are in this commit"
    s2 = hunks.score(hx, hc)[0]
    sim = max(s2, fuzzy.s3(fuzzy.features(idx, x, c)))
    if sim < TRAILER_MIN_SIM:
        return TRAILER_UNVERIFIED, f"{reason}; code does not match (similarity {sim:.2f})"
    return _behaviour_guard(idx, x, c, 0.95, f"{reason}; code edited (similarity {sim:.2f})")


def find_equivalents(idx, sha: str, branches: list[str] | None = None, *, use_trailers: bool = True,
                     use_llm: bool = False, provider=None) -> list[Match]:
    return find_equivalents_batch(idx, [sha], branches, use_trailers=use_trailers, use_llm=use_llm,
                                  provider=provider)[sha]


def find_equivalents_batch(idx, shas: list[str], branches: list[str] | None = None, *, use_trailers: bool = True,
                           use_llm: bool = False, provider=None) -> dict[str, list[Match]]:
    cfg = idx.cfg
    brs = [b for b in idx.branches() if branches is None or b[1] in branches or b[1].split("/", 1)[-1] in branches]
    names = {b[1] for b in brs}
    found: dict[str, dict[tuple[str, str], Match]] = {s: {} for s in shas}

    def add(src: str, sha: str, branch: str, level: Level, conf: float, reason: str) -> None:
        key = (branch, sha)
        if key not in found[src] and level != Level.SELF and conf > DIFFERENT_BEHAVIOUR_CAP:
            need = fuzzy.missing_prereqs(idx, src, sha)
            if need:
                conf = DIFFERENT_BEHAVIOUR_CAP
                reason += f"; missing prerequisite: {', '.join(need[:4])}() is defined where {sha[:7]} was tested, not here"
        if key not in found[src]:
            found[src][key] = Match(sha=sha, branch=branch, env=idx.env_of(branch) if branch else None, level=level,
                                    confidence=round(conf, 4), reason=reason)

    # SELF, L0, L1: exact links, any configured branch
    for s in shas:
        x = idx.commit(s)
        exact: dict[str, tuple[Level, float, str]] = {s: (Level.SELF, 1.0, "origin commit")}
        if x is not None:
            if use_trailers:
                for c, reason in trailers.linked(idx, s).items():
                    exact.setdefault(c, (Level.L0, *_verify_trailer(idx, s, c, reason)))
                raws = trailers.picked_all(x["body"] or "")
                known = idx.repo.resolve_commits(raws)
                for raw in raws:
                    if raw not in known:  # names a commit this repo doesn't have: a claim we can't check (s23)
                        add(s, raw, "", Level.L0, 0.0, f"trailer names {raw}, not in this repository (rewritten or deleted)")
                    elif known[raw] not in exact:  # squashed backport: one commit, several targets (s12)
                        idx.index_commits([known[raw]])
                        exact[known[raw]] = (Level.L0, *_verify_trailer(idx, s, known[raw], f"trailer: backport of {raw[:7]}"))
            for c in idx.by_patch_id(x["patch_id"]):
                exact.setdefault(c, (Level.L1, 0.98, "identical patch-id"))
            for c in idx.by_tree(x["tree"]):  # a no-op commit ("trigger UAT run") re-labels the same snapshot
                if c != s and (x["patch_id"] is None or idx.commit(c)["patch_id"] is None):
                    exact.setdefault(c, (Level.L1, 0.98, "identical tree (no-op commit on the same snapshot)"))
        for c, (level, conf, reason) in exact.items():
            bs = [b for b in idx.branches_of(c) if b in names]
            if not bs and (level in (Level.SELF, Level.L0) or idx.records(sha=c)):  # off-branch: rebased (s21), merge picked with -m (s60)
                bs = [""]
            for b in bs:
                add(s, c, b, level, conf, reason)
        for c in hunks.moves(idx, s) if x is not None else []:  # code deleted here, recreated elsewhere (or back)
            for b in [b for b in idx.branches_of(c) if b in names] or [""]:
                add(s, c, b, Level.L2, DIFFERENT_BEHAVIOUR_CAP, "moved: the same lines removed in one file, added in another")

    # L2, L3 (+L4 adjustments, L5 judge) per target branch
    lo, hi = cfg.l5_band
    for ref, name, _ in brs:
        scores, feats = {}, {}
        for s in shas:
            x = idx.commit(s)
            if x is None:
                continue
            taken = {sha for (_, sha) in found[s]}
            cands = idx.candidates(s, ref, name) - taken - set(hunks.reverts(idx, s))  # a revert is not the change
            l2 = hunks.match(idx, s, cands, cfg.l2_min_jaccard)
            for c, (conf, reason) in l2.items():
                conf = _clamp(conf + metadata.adjust(x, idx.commit(c)), 0.60, 0.95)
                conf, reason = _behaviour_guard(idx, s, c, conf, reason)
                add(s, c, name, Level.L2, conf, reason)
            rest = fuzzy.prefilter(idx, s, cands - set(l2))
            rest = sorted(rest, key=lambda c: abs(idx.commit(c)["author_date"] - x["author_date"]))[:L3_CAP]
            for c in rest:
                f = fuzzy.features(idx, s, c)
                scores[(s, c)], feats[(s, c)] = fuzzy.s3(f), f
        ambiguous = []
        for (s, c), sc in fuzzy.assign(scores).items():
            conf = _clamp(0.40 + 0.45 * sc + metadata.adjust(idx.commit(s), idx.commit(c)), 0.40, 0.85)
            if use_llm and cfg.l5_enabled and lo <= sc <= hi:
                ambiguous.append((sc, s, c, conf))
            elif sc >= cfg.l3_accept:
                conf, reason = _behaviour_guard(idx, s, c, conf, fuzzy.reason(feats[(s, c)]))
                add(s, c, name, Level.L3, conf, reason)
        for _, s, c, conf in sorted(ambiguous, reverse=True)[:3]:  # top 3 per branch
            try:
                v = llm_judge.judge(provider, cfg.llm["judge_model"], idx, s, (idx.branches_of(s) or [""])[0], c, name)
            except ProvError:
                if scores[(s, c)] >= cfg.l3_accept:  # LLM down: keep the deterministic L3 decision
                    add(s, c, name, Level.L3, conf, fuzzy.reason(feats[(s, c)]))
                continue
            if v.same_change and v.confidence >= 0.7:
                add(s, c, name, Level.L5, min(0.80, max(conf, 0.5 + 0.3 * v.confidence)), f"LLM: {v.reason}")
    return {s: sorted(found[s].values(), key=lambda m: -m.confidence) for s in shas}
