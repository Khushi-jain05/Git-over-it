"""Experiments E1-E4 (docs/10 §2).

    python -m eval.replay identity  --repo ../django --name django [--limit 300] [--trailers] [--llm]
    python -m eval.replay conflicts --repo ../django --name django [--limit 50] [--llm]
    python -m eval.replay safety
"""

from __future__ import annotations

import argparse
import ast
import json
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

from eval.metrics import identity_metrics
from eval.mine import PROFILES, negatives
from prov.config import Config
from prov.errors import ProvError
from prov.gitio import Repo
from prov.identity.ladder import find_equivalents
from prov.models import Level
from prov.store.index import Index

DATA = Path("eval/data")


def eval_config(name: str) -> Config:
    pattern = {"django": "stable/*", "cpython": "3.*"}[name]
    return Config(environments={"main": "dev", pattern: "release"}, window_days=1200)


def load_pairs(name: str, limit: int, seed: int = 7) -> list[dict]:
    pairs = [json.loads(ln) for ln in (DATA / f"{name}_pairs.jsonl").read_text().splitlines()]
    random.Random(seed).shuffle(pairs)
    edited = [p for p in pairs if not p["same_patch_id"]]
    rest = [p for p in pairs if p["same_patch_id"]]
    half = min(len(edited), limit // 2)  # edited backports are the interesting subset: oversample them
    return edited[:half] + rest[: limit - half]


# ---------------- E1 / E2 ----------------
def truth_on_source(idx, original: str | None, source: str) -> str | None:
    """Ground truth on the searched branch: follow `cherry picked from` up to the commit on `source`.
    CPython chains backports (3.11 <- 3.12 <- main); the miner records the immediate source only."""
    o = original
    for _ in range(5):
        if source in idx.branches_of(o):
            return o
        row = idx.commit(o)
        if not (row and row["picked_from"]):
            break
        o = row["picked_from"]
    return original


def unlabeled_backport(idx, sha: str, source: str) -> bool:
    """Label rule for negatives: a stable commit whose identical patch, or whose exact subject (minus the [x.y]
    prefix and PR refs), already exists on the source branch is an unlabeled backport, not a true negative."""
    from prov.identity.metadata import clean_subject

    row = idx.commit(sha)
    if not row:
        return False
    if any(source in idx.branches_of(s) for s in idx.by_patch_id(row["patch_id"]) if s != sha):
        return True
    if not hasattr(idx, "_subjects"):
        q = "SELECT c.subject FROM commits c JOIN branch_commits b ON b.sha=c.sha WHERE b.branch=?"
        idx._subjects = {clean_subject(r[0]) for r in idx.db.execute(q, (source,))}
    return clean_subject(row["subject"]) in idx._subjects


def rescore(repo_path: str, name: str) -> None:
    idx = Index(Repo(repo_path), eval_config(name))
    for path in DATA.glob(f"{name}_identity*.json"):
        out = json.loads(path.read_text())
        source = PROFILES[name]["source"]
        rows = [r for r in out["rows"] if r.get("original_raw", r["original"]) or not unlabeled_backport(idx, r["backport"], source)]
        out["excluded_unlabeled_negatives"] = out.get("excluded_unlabeled_negatives", 0) + len(out["rows"]) - len(rows)
        for r in rows:
            raw = r.setdefault("original_raw", r["original"])
            r["original"] = truth_on_source(idx, raw, source) if raw else None
        out["rows"] = rows
        out["metrics"] = identity_metrics(rows)
        path.write_text(json.dumps(out, indent=1))
        print(path.name, json.dumps(out["metrics"]["all"]), json.dumps(out["metrics"]["edited"]))

def run_identity(repo_path: str, name: str, limit: int, trailers: bool, llm: bool) -> dict:
    repo = Repo(repo_path)
    cfg = eval_config(name)
    idx = Index(repo, cfg)
    t0 = time.monotonic()
    idx.refresh()
    index_s = time.monotonic() - t0
    pairs = load_pairs(name, limit)
    branches = sorted({p["branch"] for p in pairs})
    source = PROFILES[name]["source"]
    sampled = negatives(repo, name, "2024-01-01", branches, 10 * max(len(pairs) // 4, 10))
    negs = [n for n in sampled if not unlabeled_backport(idx, n["backport"], source)][: max(len(pairs) // 4, 10)]
    provider = None
    if llm:
        from prov.llm.provider import for_repo

        provider = for_repo(repo, cfg)
    rows, t1 = [], time.monotonic()
    for i, p in enumerate(pairs + negs):
        ms = find_equivalents(idx, p["backport"], [PROFILES[name]["source"]], use_trailers=trailers, use_llm=llm,
                              provider=provider)
        top = next((m for m in ms if m.level != Level.SELF), None)
        truth = truth_on_source(idx, p["original"], PROFILES[name]["source"]) if p["original"] else None
        rows.append({"backport": p["backport"], "original": truth, "original_raw": p["original"], "edited": not p.get("same_patch_id", True),
                     "predicted": top.sha if top else None, "level": top.level.value if top else None,
                     "confidence": top.confidence if top else None})
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(pairs) + len(negs)}", file=sys.stderr)
    out = {"repo": name, "trailers": trailers, "llm": llm, "pairs": len(pairs), "index_seconds": round(index_s, 1),
           "seconds_per_commit": round((time.monotonic() - t1) / max(len(rows), 1), 3),
           "metrics": identity_metrics(rows), "rows": rows}
    suffix = ("_trailers" if trailers else "") + ("_llm" if llm else "")
    (DATA / f"{name}_identity{suffix}.json").write_text(json.dumps(out, indent=1))
    return out


# ---------------- E3 ----------------
def _same_ast(path: str, a: str, b: str) -> bool:
    if path.endswith(".py"):
        try:
            return ast.dump(ast.parse(a)) == ast.dump(ast.parse(b))
        except SyntaxError:
            return False
    from prov.identity.fuzzy import LANGS, _norm_node, _parser, supported

    if not supported(path):
        return False
    parser = _parser(LANGS[Path(path).suffix][0])
    ta, tb = parser.parse(a.encode()), parser.parse(b.encode())
    return _norm_node(ta.root_node, a.encode()) == _norm_node(tb.root_node, b.encode())


def _norm_ws(s: str) -> str:
    return "\n".join(ln.rstrip() for ln in s.splitlines()).strip()


def run_conflicts(repo_path: str, name: str, limit: int, llm: bool) -> dict:
    from prov.analyze.report import analyze
    from prov.resolve.apply import apply
    from prov.resolve.mergiraf import solve

    repo = Repo(repo_path)
    pairs = [p for p in load_pairs(name, 10_000) if not p["same_patch_id"]]  # edited backports = likely conflicts
    base = Path(tempfile.mkdtemp(prefix="prov-replay-"))
    results = []
    for p in pairs:
        if sum(r["status"] != "clean" for r in results) >= limit:
            break
        wt = base / p["backport"][:12]
        repo.run(["worktree", "add", "-q", "--detach", str(wt), f"{p['backport']}^"])
        w = Repo(wt)
        t0 = time.monotonic()
        res = {"backport": p["backport"], "original": p["original"], "files": p["files"]}
        try:
            picked = w.ok(["-c", "rerere.enabled=false", "cherry-pick", "-x", p["original"]])
            if picked:
                res["status"] = "clean"
            elif not w.ls_files_unmerged():
                res["status"] = "error"
            else:
                rep = analyze(w)
                res["hunks"] = sum(len(f.hunks) for f in rep.files)
                res["kinds"] = [f.kind for f in rep.files]
                try:
                    solved = solve(w)["files"]
                    res["mergiraf_solved"] = sum(v["solved"] for v in solved.values())
                except ProvError:
                    res["mergiraf_solved"] = 0
                after_mergiraf = bool(w.ls_files_unmerged())
                res["llm_drafts"] = res["llm_gates_passed"] = 0
                if after_mergiraf and llm:
                    from prov.llm.draft import draft

                    rep = analyze(w)
                    for fc in rep.files:
                        for h in reversed(fc.hunks):
                            try:
                                d = draft(w, h.id)
                            except ProvError:
                                continue
                            res["llm_drafts"] += 1
                            if d.gates_passed:  # --auto eval mode: accept gated drafts, never commit or push
                                res["llm_gates_passed"] += 1
                                apply(w, h.id, "draft", expected_hash=h.content_hash, text=d.resolved_code)
                remaining = bool(w.ls_files_unmerged()) or any(
                    "<<<<<<<" in (wt / f).read_text(errors="replace") for f in p["files"] if (wt / f).exists())
                res["status"] = ("unresolved" if remaining else
                                 "auto_mergiraf" if not after_mergiraf else "auto_mergiraf_llm")
            if res["status"] != "error":
                cmp = {}
                for f in p["files"]:
                    real = repo.run(["show", f"{p['backport']}:{f}"], check=False)
                    ours = (wt / f).read_text(errors="replace") if (wt / f).exists() else ""
                    cmp[f] = ("exact" if _norm_ws(real) == _norm_ws(ours) else
                              "ast_equal" if _same_ast(f, real, ours) else "different")
                res["compare"] = cmp
        except ProvError as e:
            res["status"], res["error"] = "error", e.message
        res["seconds"] = round(time.monotonic() - t0, 2)
        results.append(res)
        repo.run(["worktree", "remove", "--force", str(wt)], check=False)
        print(f"  {p['backport'][:10]} {res['status']}", file=sys.stderr)
    shutil.rmtree(base, ignore_errors=True)
    repo.run(["worktree", "prune"], check=False)
    out = {"repo": name, "llm": llm, "results": results}
    (DATA / f"{name}_replay.json").write_text(json.dumps(out, indent=1))
    return out


# ---------------- E4 ----------------
def run_safety() -> dict:
    sys.path.insert(0, "scripts")
    import make_sim_repo

    from prov.analyze.report import analyze

    base = Path(tempfile.mkdtemp(prefix="prov-safety-"))
    sides = colored = false_verified = 0
    fresh_ok = True
    per = {}
    for n in range(1, 8):
        root = make_sim_repo.main([str(base / f"s{n}"), "--scenario", str(n), "--stop-at-conflict"])
        exp = json.loads((root / ".git" / "prov" / "expected.json").read_text())
        rep = analyze(Repo(root))
        fv = 0
        for fc in rep.files:
            for h in fc.hunks:
                for s in (h.ours, h.theirs):
                    sides += 1
                    colored += s.badge.value in ("PROD_VERIFIED", "TESTED_ELSEWHERE", "FAILED")
                    if s.badge.value == "PROD_VERIFIED" and exp["hunks"][h.id][s.side][0] != "PROD_VERIFIED":
                        fv += 1
        # same repo with every notes ref deleted: nothing may claim "recorded"
        r = Repo(root)
        for ref in r.run(["for-each-ref", "--format=%(refname)", "refs/notes/"]).split():
            r.run(["update-ref", "-d", ref])
        bare = analyze(r)
        tiers = {s.tier.value for fc in bare.files for h in fc.hunks for s in (h.ours, h.theirs)}
        fresh_ok &= "recorded" not in tiers
        false_verified += fv
        per[n] = {"false_prod_verified": fv, "tiers_without_notes": sorted(tiers)}
    shutil.rmtree(base, ignore_errors=True)
    out = {"sides": sides, "coverage": round(colored / sides, 4) if sides else 0, "false_prod_verified": false_verified,
           "fresh_clone_never_recorded": fresh_ok, "scenarios": per}
    (DATA / "safety.json").write_text(json.dumps(out, indent=1))
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("experiment", choices=["identity", "conflicts", "safety", "rescore"])
    ap.add_argument("--repo")
    ap.add_argument("--name", choices=sorted(PROFILES))
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--trailers", action="store_true")
    ap.add_argument("--llm", action="store_true")
    a = ap.parse_args(argv)
    DATA.mkdir(parents=True, exist_ok=True)
    if a.experiment == "safety":
        out = run_safety()
        print(json.dumps({k: v for k, v in out.items() if k != "scenarios"}))
        return
    if not (a.repo and a.name):
        ap.error("--repo and --name are required")
    if a.experiment == "rescore":
        rescore(a.repo, a.name)
        return
    if a.experiment == "identity":
        out = run_identity(a.repo, a.name, a.limit, a.trailers, a.llm)
        print(json.dumps(out["metrics"]["all"]), json.dumps(out["metrics"]["edited"]))
    else:
        out = run_conflicts(a.repo, a.name, a.limit, a.llm)
        print(json.dumps({s: sum(r["status"] == s for r in out["results"]) for s in
                          ("clean", "auto_mergiraf", "auto_mergiraf_llm", "unresolved", "error")}))


if __name__ == "__main__":
    main()
