"""python -m eval.report --out eval/report.md  (reads eval/data/*.json written by eval.replay)"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median

DATA = Path("eval/data")
NOTES = """
Notes

- Django is the tuning repo (`l3_accept`), CPython is held out. Samples oversample edited backports (patch-id differs):
  half of each sample. Negatives are stable commits without a backport trailer, minus unlabeled backports
  (docs/IMPLEMENTATION.md §Evaluation).
- With trailers, the single Django miss is a commit whose `Backport of` trailer names a different CVE fix.
- E3: Mergiraf alone solved none of the replayed Django/CPython backport conflicts (they are textual, not structural);
  the LLM stage was not run because no `GROQ_API_KEY` was available when this report was generated. Rerun with
  `python -m eval.replay conflicts ... --llm`.
- Commands: see README §Evaluation. Raw per-pair results: `eval/data/*.json` (git-ignored).
"""
REPOS = ["django", "cpython"]


def _load(name: str) -> dict | None:
    p = DATA / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else None


def _prf(m: dict | None) -> str:
    return f"{m['precision']:.2f} / {m['recall']:.2f} / {m['f1']:.2f}" if m else "-"


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "-"


def replay_summary(rep: dict | None) -> dict:
    if not rep:
        return {}
    rs = rep["results"]
    conflicts = [r for r in rs if r["status"] not in ("clean", "error")]
    n = len(conflicts)
    auto_m = sum(r["status"] == "auto_mergiraf" for r in conflicts)
    auto_ml = auto_m + sum(r["status"] == "auto_mergiraf_llm" for r in conflicts)
    auto = [r for r in conflicts if r["status"].startswith("auto")]
    exact = sum(all(v == "exact" for v in r.get("compare", {}).values()) for r in auto)
    ast_eq = sum(all(v in ("exact", "ast_equal") for v in r.get("compare", {}).values()) for r in auto)
    drafts = sum(r.get("llm_drafts", 0) for r in conflicts)
    return {"picks": len(rs), "clean": sum(r["status"] == "clean" for r in rs), "conflicts": n,
            "auto_mergiraf": auto_m, "auto_mergiraf_llm": auto_ml, "exact": exact, "ast_equal": ast_eq,
            "auto": len(auto), "llm": rep.get("llm", False), "drafts": drafts,
            "gates_passed": sum(r.get("llm_gates_passed", 0) for r in conflicts),
            "median_seconds": median([r["seconds"] for r in conflicts]) if conflicts else None}


def build() -> tuple[str, dict]:
    ident = {r: _load(f"{r}_identity") for r in REPOS}
    trail = {r: _load(f"{r}_identity_trailers") for r in REPOS}
    llm = {r: _load(f"{r}_identity_llm") for r in REPOS}
    replay = {r: replay_summary(_load(f"{r}_replay")) for r in REPOS}
    safety = _load("safety")

    def row(label: str, f) -> str:
        return f"  {label:<34}" + "".join(f"{f(r):<26}" for r in REPOS)

    lines = ["Identity (no trailers)              " + "".join(f"{r:<26}" for r in REPOS)]
    lines.append(row("pairs (edited)", lambda r: f"{ident[r]['pairs']} ({ident[r]['metrics']['edited']['positives']})"
                     if ident[r] else "-"))
    lines.append(row("all pairs   P / R / F1", lambda r: _prf(ident[r] and ident[r]["metrics"]["all"])))
    lines.append(row("edited      P / R / F1", lambda r: _prf(ident[r] and ident[r]["metrics"]["edited"])))
    lines.append(row("negatives: false matches", lambda r: f"{ident[r]['metrics']['false_positive_on_negatives']}"
                     f"/{ident[r]['metrics']['negatives']}" if ident[r] else "-"))
    for lvl in ("L1_patch_id", "L2_hunks", "L3_fuzzy", "L5_llm"):
        lines.append(row(f"{lvl} n (precision)", lambda r, lvl=lvl: (
            f"{v['predicted']} ({v['precision']:.2f})" if ident[r] and (v := ident[r]["metrics"]["by_level"].get(lvl))
            else "0")))
    if any(llm.values()):
        lines.append(row("with L5   P / R / F1", lambda r: _prf(llm[r] and llm[r]["metrics"]["all"])))
    cold = _load("index_cold") or {}
    lines.append(row("cold index build (s, commits)", lambda r: f"{cold[r]['cold_index_seconds']} ({cold[r]['commits']})"
                     if r in cold else "-"))
    lines.append(row("identity per commit (s)", lambda r: str(ident[r]["seconds_per_commit"]) if ident[r] else "-"))
    lines += ["Trailers (E2)", row("all pairs   P / R / F1", lambda r: _prf(trail[r] and trail[r]["metrics"]["all"]))]
    lines += ["Replay (E3)",
              row("cherry-picks replayed / clean", lambda r: f"{replay[r]['picks']} / {replay[r]['clean']}" if replay[r] else "-"),
              row("conflicts", lambda r: str(replay[r].get("conflicts", "-"))),
              row("auto-resolved (mergiraf)", lambda r: _pct(replay[r]["auto_mergiraf"], replay[r]["conflicts"]) if replay[r] else "-"),
              row("auto-resolved (mergiraf + LLM)", lambda r: (_pct(replay[r]["auto_mergiraf_llm"], replay[r]["conflicts"])
                                                               if replay[r].get("llm") else "not run (no LLM key)") if replay[r] else "-"),
              row("exact / AST-equal to real (auto)", lambda r: f"{_pct(replay[r]['exact'], replay[r]['auto'])} / "
                  f"{_pct(replay[r]['ast_equal'], replay[r]['auto'])}" if replay[r] else "-"),
              row("gates passed (LLM drafts)", lambda r: _pct(replay[r]["gates_passed"], replay[r]["drafts"]) if replay[r] else "-"),
              row("median seconds per conflict", lambda r: str(replay[r].get("median_seconds", "-")))]
    lines += ["Safety (E4, simulated dev/uat/prod repo)",
              f"  {'false PROD_VERIFIED claims':<34}{safety['false_prod_verified'] if safety else '-'}",
              f"  {'badge coverage (non-grey sides)':<34}{_pct(round(safety['coverage'] * 100), 100) if safety else '-'}",
              f"  {'no recorded tier without notes':<34}{safety['fresh_clone_never_recorded'] if safety else '-'}"]
    table = "\n".join(lines)
    summary = {"identity": {r: ident[r] and {k: v for k, v in ident[r].items() if k != "rows"} for r in REPOS},
               "trailers": {r: trail[r] and trail[r]["metrics"]["all"] for r in REPOS},
               "replay": replay, "safety": safety}
    return table, summary


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="eval/report.md")
    a = ap.parse_args(argv)
    table, summary = build()
    print(table)
    Path(a.out).write_text("# git prov evaluation\n\nGenerated by `python -m eval.report` from `eval/data/`.\n\n"
                           f"```text\n{table}\n```\n" + NOTES)
    Path(a.out).with_suffix(".json").write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
