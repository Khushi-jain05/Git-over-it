"""analyze(): conflict -> ConflictReport (docs/05)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from prov import config
from prov.analyze import badges, blame, conflicts, evidence
from prov.errors import ProvError
from prov.gitio import Repo
from prov.identity.ladder import find_equivalents
from prov.models import Badge, ConflictReport, Env, FileConflict, HunkReport, Origin, SideReport, Tier
from prov.store.index import Index
from prov.store.notes import notes_present


def metadata_state(repo: Repo, read_refs: list[str] = ()) -> Tier:
    # ponytail: "inferred" (remote advertises notes but fetch skipped) needs a network call; add with sync status.
    return Tier.recorded if notes_present(repo, read_refs) else Tier.none


def base_ref(repo: Repo, op: str, theirs: str) -> str | None:
    if not theirs:  # e.g. git am of a patch whose commit is not in this repo
        return None
    if op in ("cherry-pick", "rebase", "am"):
        return repo.rev_parse(f"{theirs}^")
    if op == "revert":
        return theirs
    return repo.run(["merge-base", "HEAD", theirs], check=False).strip() or None


def tip_tree_tested(idx: Index, rev: str) -> list[Env]:
    tree = idx.repo.rev_parse(f"{rev}^{{tree}}")
    recs = idx.records(tree=tree) if tree else []
    return sorted({r.env for r in recs if r.kind == "test" and r.result == "PASSED"}, key=lambda e: e.value)


def side_path(repo: Repo, ref: str, path: str, bref: str | None) -> str:
    """The path in `ref` holding the conflicted file: follows a rename on either side since the base (s08)."""
    if not bref or repo.ok(["cat-file", "-e", f"{ref}:{path}"]):
        return path
    for side in ("HEAD", ref):
        out = repo.run(["diff", "-M", "--name-status", "-z", bref, side], check=False).split("\0")
        for i, f in enumerate(out):
            if f.startswith("R") and i + 2 < len(out) and path in (out[i + 1], out[i + 2]):
                other = out[i + 1] if out[i + 2] == path else out[i + 2]
                if repo.ok(["cat-file", "-e", f"{ref}:{other}"]):
                    return other
    return path


def tip_tree_deployed(idx: Index, rev: str | None) -> list[Env]:
    tree = idx.repo.rev_parse(f"{rev}^{{tree}}") if rev else None
    recs = idx.records(tree=tree) if tree else []
    return sorted({r.env for r in recs if r.kind == "deploy" and r.result == "PASSED" and not evidence.rolled_back_by(idx, r)},
                  key=lambda e: e.value)


def moved_to(repo: Repo, ref: str, path: str, base_text: str) -> str | None:
    """Lines deleted on `ref` that now live, all of them, in another file of `ref` (s42)."""
    lines = [ln.strip() for ln in base_text.splitlines() if len(ln.strip()) > 3]
    if not lines:
        return None
    pats = [a for ln in lines[:20] for a in ("-e", ln)]
    out = repo.run(["grep", "-l", "-F", "--all-match", *pats, ref, "--", f":!{path}"], check=False)
    hits = [h.split(":", 1)[1] for h in out.splitlines() if ":" in h]
    return hits[0] if len(hits) == 1 else None


_merge_trees: dict[tuple[str, str], str | None] = {}


def previous_resolution(repo: Repo, path: str, ours: str, theirs: str, limit: int = 100) -> str | None:
    """The most recent merge (any ref) whose own merge hit this same conflict block: its result is a resolution
    someone already made (and maybe tested). Like rerere, but from history, so it works in a fresh clone (s15)."""
    # ponytail: replays up to `limit` recent merges with merge-tree; index merge conflicts if that gets slow
    for line in repo.run(["log", "--all", "--merges", f"-n{limit}", "--format=%H %P"]).splitlines():
        m, *parents = line.split()
        if len(parents) != 2:
            continue
        if (m, path) not in _merge_trees:
            out = repo.run(["merge-tree", "--write-tree", "--no-messages", *parents], check=False).split()
            blob = repo.run(["show", f"{out[0]}:{path}"], check=False) if out else ""
            _merge_trees[(m, path)] = blob
        for blk in conflicts.parse_blocks(_merge_trees[(m, path)] or ""):
            if {blk.ours, blk.theirs} == {ours, theirs}:
                return m
    return None


class Analyzer:
    def __init__(self, repo: Repo, use_llm_identity: bool = False):
        self.repo, self.cfg = repo, config.load(repo.root)
        self.idx = Index(repo, self.cfg)
        self.state = metadata_state(repo, self.cfg.read_refs)
        self.use_llm = use_llm_identity
        self.provider = None
        if use_llm_identity:
            from prov.llm.provider import for_repo

            self.provider = for_repo(repo, self.cfg)
        self._eq: dict[str, list] = {}
        self.base: str | None = None  # merge base of the operation; origins reachable from it are context

    def _one(self, name: str, text: str, origins: list[Origin], tip: list[Env]) -> SideReport:
        matches = []
        if origins:
            sha = origins[0].sha
            if sha not in self._eq:
                self._eq[sha] = find_equivalents(self.idx, sha, use_llm=self.use_llm, provider=self.provider)
            matches = self._eq[sha]
        ev = evidence.collect(self.idx, matches)
        s = SideReport(side=name, text=text, origins=origins, matches=matches, evidence=ev,
                       badge=Badge.UNVERIFIED, tier=Tier.none, tip_tree_tested=tip)
        s.badge, s.tier = badges.decide(s, self.state)
        return s

    def side(self, name: str, text: str, origins: list[Origin], tip: list[Env]) -> SideReport:
        """A side is only as proven as its weakest origin: that origin goes first and its badge is the side's."""
        own = [o for o in origins if not (self.base and self.repo.ok(["merge-base", "--is-ancestor", o.sha, self.base]))]
        if len(own) < 2:
            return self._one(name, text, origins, tip)
        each = [self._one(name, text, [o, *(p for p in origins if p is not o)], tip) for o in own]
        return min(each, key=lambda s: badges.rank(s.badge, s.tier))  # min() keeps the first on ties


def analyze(repo: Repo, *, paths: list[str] | None = None, use_llm_identity: bool = False) -> ConflictReport:
    op, theirs, files = conflicts.discover(repo, paths)
    a = Analyzer(repo, use_llm_identity)
    a.idx.refresh()
    head = repo.rev_parse("HEAD") or ""
    bref = base_ref(repo, op, theirs)
    a.base = bref
    tips = {"ours": tip_tree_tested(a.idx, "HEAD"), "theirs": tip_tree_tested(a.idx, theirs) if theirs else []}
    deployed = {"ours": tip_tree_deployed(a.idx, "HEAD"), "theirs": tip_tree_deployed(a.idx, theirs)}
    out_files = []
    for path, kind, hunks in files:
        reports = []
        if not hunks:  # file-level provenance: the last commit touching the path on each side (docs/05 §2)
            sides = {}
            for name, ref in (("ours", "HEAD"), ("theirs", theirs)):
                sha = repo.run(["log", "-1", "--format=%H", ref, "--", path], check=False).strip() if ref else ""
                sides[name] = a.side(name, "", [blame.origin_of(repo, sha, 0)] if sha else [], tips[name])
            for name, sd in sides.items():
                sd.tip_tree_deployed = deployed[name]
            prev = None
            stages = repo.ls_files_unmerged().get(path, {})
            if kind == "content" and set(stages) == {1, 2, 3}:  # markers gone (rerere replayed it): use the stages
                blobs = {st: repo.cat_blob(blob) for st, (_, blob) in stages.items()}
                for blk in conflicts.parse_blocks(repo.merge_file_diff3(blobs[2], blobs[1], blobs[3])):
                    prev = prev or previous_resolution(repo, path, blk.ours, blk.theirs)
            out_files.append(FileConflict(path=path, kind=kind, hunks=[], previous_resolution=prev, **sides))
            continue
        for h in hunks:
            b = h.block
            o_or = blame.origins(repo, "HEAD", side_path(repo, "HEAD", path, bref), b.ours, b.offsets["ours"], bref, b.base or "")
            if not theirs:  # the incoming commit is unknown (git am of a foreign patch)
                t_or = []
            elif b.theirs.strip():
                t_ref = f"{theirs}^" if op == "revert" else theirs  # a revert's theirs side is the parent's text
                t_or = blame.origins(repo, t_ref, side_path(repo, t_ref, path, bref), b.theirs, b.offsets["theirs"], bref)
            else:  # theirs deleted these lines: the theirs commit did it
                t_or = [blame.origin_of(repo, theirs, 0)]
            ours = a.side("ours", b.ours, o_or, tips["ours"])
            th = a.side("theirs", b.theirs, t_or, tips["theirs"])
            ours.tip_tree_deployed, th.tip_tree_deployed = deployed["ours"], deployed["theirs"]
            base = None
            if b.base is not None and bref:
                b_or = blame.origins(repo, bref, path, b.base, b.offsets["base"], None) if b.base.strip() else []
                base = a.side("base", b.base, b_or, [])
            moved = None
            if b.base and b.base.strip() and not (b.ours.strip() and b.theirs.strip()):
                moved = moved_to(repo, "HEAD" if not b.ours.strip() else theirs, path, b.base) if theirs else None
            prev = previous_resolution(repo, path, b.ours, b.theirs)
            rec, why = badges.recommend(ours, th)
            reports.append(HunkReport(
                id=f"{path}#{h.index}", path=path, index=h.index, start_line=b.start_line, end_line=b.end_line,
                content_hash=b.content_hash, base=base, ours=ours, theirs=th,
                recommendation=rec, recommendation_reason=why, moved_to=moved, previous_resolution=prev))
        out_files.append(FileConflict(path=path, kind=kind, hunks=reports))
    report = ConflictReport(operation=op, ours_ref=head, theirs_ref=theirs, metadata_state=a.state,
                            files=out_files, generated_at=datetime.now(UTC))
    save_report(repo, report)
    return report


def save_report(repo: Repo, report: ConflictReport) -> None:
    """Cache for apply/commit. Hunks resolved since the first analysis of this operation stay in the cache
    (the printed report only shows what is still open), so commit can record a decision for every hunk."""
    (repo.git_dir / "prov").mkdir(exist_ok=True)
    old = load_report(repo)
    cached = report.model_copy(deep=True)
    if old and old.theirs_ref == report.theirs_ref and old.operation == report.operation:
        now = {f.path: f for f in cached.files}
        for f in old.files:
            if f.path not in now:  # file already staged: keep its hunks
                cached.files.append(f)
                continue
            known = {h.content_hash for h in now[f.path].hunks}
            now[f.path].hunks += [h for h in f.hunks if h.content_hash not in known]
    (repo.git_dir / "prov" / "report.json").write_text(cached.model_dump_json())


def load_report(repo: Repo) -> ConflictReport | None:
    p = repo.git_dir / "prov" / "report.json"
    try:
        return ConflictReport.model_validate(json.loads(p.read_text()))
    except (FileNotFoundError, ValueError):
        return None


def status(repo: Repo, rev: str, use_llm: bool = False) -> dict:
    a = Analyzer(repo, use_llm)
    a.idx.refresh()
    sha = repo.rev_parse(f"{rev}^{{commit}}")
    if not sha:
        raise ProvError("usage", f"unknown revision {rev}")
    s = a.side("theirs", "", [blame.origin_of(repo, sha, 0)], tip_tree_tested(a.idx, sha))
    from prov.identity.hunks import reverts

    return {"commit": sha, "matches": [m.model_dump(mode="json") for m in s.matches], "reverts": reverts(a.idx, sha),
            "evidence": [e.model_dump(mode="json") for e in s.evidence], "badge": s.badge.value, "tier": s.tier.value}
