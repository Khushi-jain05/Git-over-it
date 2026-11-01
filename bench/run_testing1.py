"""Score git prov against a testing-N benchmark repo, using only the tool's CLI output.

    python bench/run_testing1.py --repo ~/Desktop/test/testing-1 [S011 S022 ...] [--json-out out.json]
    python bench/run_testing1.py --repo ~/Desktop/test/testing-2      # config: bench/<repo name>.prov.toml
    python bench/run_testing1.py --repo ... -j 4                       # scenarios in parallel (default: all CPUs)

Each scenario runs in a throwaway clone (refs, tags and notes copied; `benchmark-answers` removed so the tool
can never read the answer key). The scorer alone reads the answers from the source repo.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
GIT_PROV = os.environ.get("GIT_PROV", str(HERE.parent / ".venv" / "bin" / "git-prov"))
PYTHON = os.environ.get("BENCH_PYTHON", str(HERE / ".venv-testing1" / "bin" / "python"))
ENV = {**os.environ, "GIT_EDITOR": "true", "GIT_AUTHOR_NAME": "bench", "GIT_AUTHOR_EMAIL": "bench@example.invalid",
       "GIT_COMMITTER_NAME": "bench", "GIT_COMMITTER_EMAIL": "bench@example.invalid"}
TESTED = {"PROD_VERIFIED", "TESTED_ELSEWHERE"}
EXACT = {"L0_trailer", "L1_patch_id"}
STATUS = {"FLAKY": "WARNED", "ROLLED_BACK": "PASSED", "SUCCEEDED": "PASSED", "PARTIAL": "WARNED", "INTERRUPTED": "WARNED"}  # answer-key words -> git prov results
                                                     # (a rollback record = a deploy of the commit prod returns to)
REF_KIND = {"ci": "test", "deploy": "deploy"}
TAKE = os.environ.get("BENCH_TAKE", "theirs")  # side taken for every hunk when a conflict scenario must fail its tests  # refs/notes/provenance holds claims: never evidence, so not scored


def git(cwd, *args, check=True):
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=ENV)
    if check and p.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip()}")
    return p.stdout


def prov(cwd, *args, env):
    p = subprocess.run([GIT_PROV, *args, "--json"], cwd=cwd, capture_output=True, text=True, env=env)
    try:
        return p.returncode, json.loads(p.stdout)
    except ValueError:
        return p.returncode, {"error": {"message": (p.stdout + p.stderr).strip()[-400:]}}


def answers(src: Path) -> list[dict]:
    idx = json.loads(git(src, "show", "origin/benchmark-answers:index.json"))
    return [json.loads(git(src, "show", f"origin/benchmark-answers:expected/{e['scenario_id']}.json")) for e in idx]


def clone(src: Path, dst: Path, profile: dict | str | None = None) -> list[str]:
    """Throwaway clone shaped by the scenario's clone_profile. -> profile effects that are NOT simulated."""
    p = profile if isinstance(profile, dict) else {}
    git(dst.parent, "clone", "-q", "--no-local", "--no-checkout", str(src), str(dst))
    notes = [] if p.get("drop_notes") else [f"+refs/notes/{r}:refs/notes/{r}" for r in p["keep_notes"]] \
        if p.get("keep_notes") else ["+refs/notes/*:refs/notes/*"]
    git(dst, "fetch", "-q", str(src), "+refs/remotes/origin/*:refs/remotes/origin/*", *notes, "+refs/tags/*:refs/tags/*")
    if p.get("drop_tags"):
        for t in git(dst, "tag", "-l", p["drop_tags"]).split():
            git(dst, "tag", "-d", t)
    if p.get("graft_from"):  # notes from an unrelated repository
        git(dst, "fetch", "-q", str(src.parent / p["graft_from"]), "+refs/notes/*:refs/notes/*", check=False)
    simulated = {"name", "effect", "drop_notes", "keep_notes", "drop_tags", "graft_from", "offline", "from_mirror"}
    git(dst, "update-ref", "-d", "refs/remotes/origin/benchmark-answers", check=False)
    git(dst, "remote", "set-url", "origin", "file:///nonexistent")  # nothing to fetch from: the clone is complete
    return [f"{k}={v}" for k, v in p.items() if k not in simulated]


def matches_of(cwd, sha, env) -> dict[str, dict]:
    _, out = prov(cwd, "status", sha, env=env)
    return {m["sha"]: m for m in out.get("matches", []) if m["level"] != "self"}, out


def score(exp: dict, cwd: Path, env: dict) -> list[tuple[str, bool, str]]:
    checks = []
    add = lambda name, ok, detail="": checks.append((name, bool(ok), detail))  # noqa: E731
    base, target = exp["merge_pair"]["base"], exp["merge_pair"]["target"]
    git(cwd, "switch", "-q", "--detach", base)
    merged = subprocess.run(["git", "merge", "--no-commit", "--no-ff", target], cwd=cwd, capture_output=True,
                            text=True, env=ENV).returncode == 0
    code, rep = prov(cwd, "analyze", env=env)
    outcome = exp["expected_git_outcome"]
    if outcome == "CONFLICT":
        got = sorted(f["path"] for f in rep.get("files", []))
        add("conflicted files", got == sorted(exp["expected_conflicted_files"]), f"got {got}")
        if exp.get("human_review_required"):
            recs = {h["recommendation"] for f in rep.get("files", []) for h in f["hunks"]}
            add("human review: no one-sided recommendation", recs <= {"manual", "combine"}, f"got {sorted(recs)}")
    else:
        got = [f["path"] for f in rep.get("files", [])] if code == 0 else []
        add("no conflict reported", merged and code in (0, 3) and not got, f"merge clean={merged}, exit={code}, files={got}")

    for rel in exp["change_relationships"]:
        a, b, kind = rel["a"], rel["b"], rel["relation"]
        ms, _ = matches_of(cwd, a, env)
        m = ms.get(b)
        label = f"{kind}: {a[:8]} ~ {b[:8]}"
        if kind == "identical":  # same +/- lines; context (hence patch-id) may differ after an interactive rebase
            whole = m and m["level"] == "L2_hunks" and "(J=1.00)" in m["reason"]
            add(label, m and (m["level"] in EXACT or whole), f"got {m['level'] if m else 'no match'}")
        elif kind == "equivalent":
            add(label, m is not None, f"got {m['level'] if m else 'no match'}")
        elif kind == "unrelated":
            add(label + " (must not match)", not m or m["confidence"] < 0.6, f"got {m['level'] if m else 'no match'}")
        elif kind == "partial":  # part of the change was taken: listed, but not as the identical patch
            add(label, m is not None and m["level"] != "L1_patch_id", f"got {m['level'] if m else 'no match'}")
        elif kind == "NEEDS_REVIEW":  # genuinely ambiguous: never a confident match
            add(label + " (no confident match)", not m or m["confidence"] < 0.9,
                f"got {m['level']} {m['confidence']}" if m else "no match")
        elif kind == "reverted":
            add(label + " (not the same change)", not m, f"got {m['level'] if m else 'no match'}")
            _, st = matches_of(cwd, a, env)
            add(label + " (revert detected)", b in st.get("reverts", []), f"reverts={st.get('reverts')}")

    for asr in exp["assertions"]:
        t = asr["type"]
        if t == "note" and asr.get("ref") not in REF_KIND:
            pass  # origin claims (refs/notes/provenance) are deliberately not read
        elif t == "note" and asr["expect"] == "absent":
            _, st = matches_of(cwd, asr["commit"], env)
            own = [e for e in st.get("evidence", []) if e["record"] and e["record"]["subject"]["commit"] == asr["commit"]
                   and e["record"]["kind"] == REF_KIND[asr["ref"]]]
            add(f"no own {asr['ref']} evidence claimed for {asr['commit'][:8]}", not own, f"{len(own)} own record(s)")
        elif t == "note" and asr["expect"] == "present":  # the tool must see this record as the commit's evidence
            _, st = matches_of(cwd, asr["commit"], env)
            want = asr.get("fields", {})
            own = [e["record"] for e in st.get("evidence", []) if e["record"] and e["record"]["subject"]["commit"] == asr["commit"]
                   and e["record"]["kind"] == REF_KIND[asr["ref"]]
                   and (not want.get("status") or e["record"]["result"] == STATUS.get(want["status"], want["status"]))
                   and (not want.get("env") or e["record"]["env"] == want["env"])]
            add(f"{asr.get('ref')} record seen for {asr['commit'][:8]} {want or ''}".rstrip(), own,
                f"{len(st.get('evidence', []))} evidence item(s), none matching")
        elif t == "patch_id_differs":
            ms, _ = matches_of(cwd, asr["a"], env)
            m = ms.get(asr["b"])
            add(f"patch-id differs: {asr['a'][:8]} ~ {asr['b'][:8]} not L1", not m or m["level"] != "L1_patch_id",
                f"got {m['level'] if m else 'no match'}")
        elif t == "tree_equal":  # same tree: records of one are evidence for the other
            _, sa = matches_of(cwd, asr["a"], env)
            _, sb = matches_of(cwd, asr["b"], env)
            ids_a = {e["record"]["id"] for e in sa.get("evidence", []) if e["record"]}
            ids_b = {e["record"]["id"] for e in sb.get("evidence", []) if e["record"]}
            add(f"same tree: {asr['a'][:8]} and {asr['b'][:8]} share records", ids_a == ids_b,
                f"{len(ids_a)} vs {len(ids_b)} records")
        elif t in ("behavior", "ast_differs", "real_tests", "author_is_not", "ast_equal_file", "note_subject_mismatch"):
            pass  # facts about the repository itself (checked by the benchmark validator), not git prov output
        elif t == "trailer":
            ms, _ = matches_of(cwd, asr["commit"], env)
            if asr["expect"] == "valid":
                m = ms.get(asr["origin"])
                add(f"trailer link {asr['commit'][:8]} -> {asr['origin'][:8]}", m and m["level"] == "L0_trailer",
                    f"got {m['level'] if m else 'no match'}")
            else:
                trusted = [m for m in ms.values() if m["level"] == "L0_trailer" and m["confidence"] >= 0.6]
                add(f"no trusted trailer link from {asr['commit'][:8]}", not trusted,  # listed-but-unverified is fine
                    ", ".join(f"{m['sha'][:8]} {m['confidence']}" for m in trusted))
        elif t == "message_contains":  # the commit *claims* something in text; git prov must not believe it
            _, st = matches_of(cwd, asr["commit"], env)
            own = [e for e in st.get("evidence", []) if e["record"] and e["record"]["subject"]["commit"] == asr["commit"]
                   and e["record"]["result"] == "PASSED"]
            by_match = st.get("badge") in TESTED and not own  # tested only via a genuinely matching other commit
            add(f"message claim on {asr['commit'][:8]} is not evidence", st.get("badge") not in TESTED or by_match,
                f"badge {st.get('badge')} from its own record(s)")
        elif t == "merge_then_tests" and asr["expect"] == "fail":
            head = git(cwd, "rev-parse", "HEAD").strip()
            code, res = prov(cwd, "commit", env=env)
            if code == 4 and exp["expected_git_outcome"] == "CONFLICT":
                add("half-resolved merge refused", git(cwd, "rev-parse", "HEAD").strip() == head, "exit 4")
                # now resolve naively (one side per hunk, as a hurried human would) so the tests gate has to catch it
                _, rep2 = prov(cwd, "analyze", env=env)
                for f in rep2.get("files", []):
                    for h in reversed(f["hunks"]):  # ids are positional: resolving from the end keeps the rest valid
                        prov(cwd, "apply", "--hunk", h["id"], "--take", TAKE, "--hash", h["content_hash"], env=env)
                    if not f["hunks"]:
                        git(cwd, "checkout", f"--{TAKE}", "--", f["path"], check=False)
                        git(cwd, "add", "--", f["path"], check=False)
                code, res = prov(cwd, "commit", env=env)
            gates = (res.get("error") or {}).get("message", "")
            add(f"broken merge blocked by tests gate{' (after taking ' + TAKE + ')' if exp['expected_git_outcome'] == 'CONFLICT' else ''}",
                code == 5 and git(cwd, "rev-parse", "HEAD").strip() == head, f"exit {code}: {gates[:120]}")
    return checks


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("only", nargs="*")
    ap.add_argument("--repo", type=Path, default=Path.home() / "Desktop/test/testing-1")
    ap.add_argument("--json-out")
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 4, help="scenarios in parallel")
    ap.add_argument("--config", type=Path, help="git prov config (default: bench/<repo name>.prov.toml)")
    a = ap.parse_args()
    src = a.repo.resolve()
    if not git(src, "for-each-ref", "refs/notes/ci").strip():
        print("notes missing: run  git -C", src, "fetch origin 'refs/notes/*:refs/notes/*'", file=sys.stderr)
        return 2
    results, total, passed = [], 0, 0
    with tempfile.TemporaryDirectory(prefix="prov-bench-") as tmp:
        cfg = Path(tmp) / "prov.toml"
        cfg.write_text((a.config or HERE / f"{src.name}.prov.toml").read_text().replace("@PYTHON@", PYTHON))
        env = {**ENV, "PROV_CONFIG": str(cfg)}
        todo = [e for e in answers(src) if not a.only or any(e["scenario_id"].startswith(o) for o in a.only)]

        def run(exp):  # each scenario has its own clone, so scenarios run in parallel safely
            work = Path(tmp) / exp["scenario_id"]
            try:
                skipped = clone(src, work, exp.get("clone_profile"))
                return score(exp, work, env) + [(f"clone profile not simulated: {', '.join(skipped)}", True, "")] * bool(skipped)
            except Exception as e:  # a crash is a failed scenario, not a crashed run
                return [("scenario ran", False, f"{type(e).__name__}: {e}")]

        with ThreadPoolExecutor(max_workers=a.jobs) as pool:
            for exp, checks in zip(todo, pool.map(run, todo), strict=True):  # map keeps scenario order in the output
                ok = all(c[1] for c in checks)
                total += len(checks)
                passed += sum(c[1] for c in checks)
                print(f"{'PASS' if ok else 'FAIL'}  {exp['scenario_id']}  {exp['title']}", flush=True)
                for name, good, detail in checks:
                    print(f"        {'ok ' if good else 'XX '} {name}" + ("" if good else f"   ({detail})"), flush=True)
                results.append({"scenario": exp["scenario_id"], "pass": ok,
                                "checks": [dict(zip(("check", "ok", "detail"), c, strict=True)) for c in checks]})
    print(f"\nscenarios {sum(r['pass'] for r in results)}/{len(results)} pass, checks {passed}/{total} pass")
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(results, indent=1))
    return 0 if all(r["pass"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
