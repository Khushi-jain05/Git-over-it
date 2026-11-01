"""Collect evidence for a side's matches (docs/05 §5)."""

from __future__ import annotations

import re

from prov.models import Env, EvidenceItem, Level, Match, Tier

TAG = re.compile(r"^v\d")  # release tags count as deployed (inferred)
ENV_RANK = {Env.prod: 0, Env.uat: 1, Env.release: 2, Env.dev: 3, Env.local: 4}


def collect(idx, matches: list[Match]) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    seen: set[str] = set()
    inferred_seen: set[tuple[str, str]] = set()
    for m in matches:
        if m.confidence < 0.60:
            continue
        row = idx.commit(m.sha)
        found = idx.records(sha=m.sha)
        if row is not None:
            found += idx.records(tree=row["tree"])
            if m.level == Level.L1:
                found += idx.records(patch_id=row["patch_id"])
        has_deploy = False
        for r in found:
            if r.id in seen:
                continue
            seen.add(r.id)
            has_deploy |= r.kind == "deploy"
            note, via = f"{r.env.value} {r.kind.value} {r.result.value}", m
            if r.subject.commit != m.sha:  # reached through an identical tree or patch: say whose record it is
                how = "same tree as" if row is not None and r.subject.tree == row["tree"] else "same patch as"
                note += f" ({how} {r.subject.commit[:7]})"
                via = m.model_copy(update={"sha": r.subject.commit, "reason": f"{how} {m.sha[:7]}"})
            back = rolled_back_by(idx, r)
            if back:
                note += f"; rolled back: prod later deployed {back[:7]}, which does not contain it"
            items.append(EvidenceItem(record=r, via=via, tier=Tier.recorded, note=note, rolled_back_by=back))
        if not has_deploy:
            items += [e for e in _inferred(idx, m) if (e.via.sha, e.note) not in inferred_seen]
            inferred_seen |= {(e.via.sha, e.note) for e in items if e.record is None}
    items.sort(key=lambda e: (e.tier != Tier.recorded, ENV_RANK.get(e.record.env if e.record else e.via.env, 9),
                              -(e.record.at.timestamp() if e.record else 0)))
    return items


def rolled_back_by(idx, r) -> str | None:
    """A prod deploy is rolled back when the latest prod deploy is of a commit that does not contain it:
    not a descendant, or a descendant that reverts it (s25, s45, s49)."""
    if r.kind.value != "deploy" or r.env != Env.prod or r.result.value != "PASSED":
        return None
    later = idx.deploys("prod")
    last = later[-1] if later else None
    if last is None or last.at <= r.at or last.subject.tree == r.subject.tree:
        return None
    c, tip, repo = r.subject.commit, last.subject.commit, idx.repo
    if not (repo.rev_parse(f"{c}^{{commit}}") and repo.rev_parse(f"{tip}^{{commit}}")):
        return None  # can't tell without both commits
    if not repo.ok(["merge-base", "--is-ancestor", c, tip]):
        return tip
    from prov.identity.hunks import reverts

    if any(rv != c and repo.ok(["merge-base", "--is-ancestor", c, rv]) and repo.ok(["merge-base", "--is-ancestor", rv, tip])
           for rv in reverts(idx, c)):
        return tip
    return None


def _inferred(idx, m: Match) -> list[EvidenceItem]:
    """Reachable from a prod/uat branch (or a v* tag) -> inferred deploy. `via` names that branch; the sha is on it."""
    out, done = [], set()
    for ref in idx.repo.for_each_ref_contains(m.sha):
        env = Env.prod if TAG.match(ref) else idx.env_of(ref)
        if env in (Env.prod, Env.uat) and env not in done:
            done.add(env)
            via = m.model_copy(update={"branch": ref, "env": env})
            out.append(EvidenceItem(record=None, via=via, tier=Tier.inferred, note=f"reachable from {ref}"))
    return out
