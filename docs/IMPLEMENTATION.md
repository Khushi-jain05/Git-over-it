# Implementation notes

Status against the build spec (README, TASKS.md, docs/01–10). Everything in Phases 0–4 and stretch items 1–2 is built
and covered by tests; stretch 3 (SSH-signed records) and 4 (JetBrains) are not.

## Where things live

| Spec | Code | Tests |
| --- | --- | --- |
| 03 data model | `prov/models.py`, `store/records.py`, `store/notes.py`, `store/index.py` | `test_models`, `test_notes` |
| 03 §3 sync | `store/sync.py` (fetch → remote-tracking notes → `cat_sort_uniq` → push with retry) | `test_backport_sync::test_two_clones_share_records_us4` |
| 04 L0–L5 | `identity/trailers.py`, `hunks.py`, `fuzzy.py`, `metadata.py`, `llm_judge.py`, `ladder.py` | `test_identity` (all 9 spec cases + L5) |
| 05 analysis | `analyze/conflicts.py`, `blame.py`, `evidence.py`, `badges.py`, `report.py` | `test_conflicts`, `test_badges`, e2e |
| 06 resolve | `resolve/apply.py`, `gates.py`, `commit.py`, `mergiraf.py`, `rerere.py`, `backport.py` | `test_apply`, `test_gates`, `test_mergiraf`, e2e |
| 07 LLM | `llm/provider.py`, `cache.py`, `ratelimit.py`, `prompts.py` (verbatim), `schemas.py`, `draft.py` | `test_llm` (fake OpenAI server), `test_prompts`, e2e draft |
| 08 CLI | `prov/cli.py` | e2e |
| 09 extension | `vscode-ext/src/*` | `npm test`, `npm run test:integration` (real VS Code) |
| 10 eval | `eval/mine*.py`, `replay.py`, `metrics.py`, `report.py`, `scripts/make_sim_repo.py` | e2e scenarios 1–7 |

## Decisions that differ from, or add to, the spec

- Entry point is `prov.cli:main` (not `app`) so `--json`, `--repo`, `-v` work anywhere on the command line.
- Blame ranks origin commits that are not ancestors of the merge base first, then by line count. Pure "lines desc"
  made unchanged context lines inside a hunk the origin.
- AST hashes are computed lazily (only for X and its L3 candidates), not for every indexed commit: keeps the Django
  index build at ~9 s instead of minutes. L3 keeps the 50 time-nearest candidates after the basename/function filter.
- The index lives in the shared git dir (`<common-dir>/prov/index.db`); `report.json`, `pending.json`, drafts are per
  worktree, so `backport` worktrees work.
- `commit` stages conflicted files that no longer contain markers (resolved by hand or replayed by rerere) and records
  them as `manual`.
- The `tests` gate reports *skipped* (key absent) when `--quick` finds no mapped tests; it never reports a skipped run
  as passed, and only a real pass writes the `env=local` record on the new commit.
- Inferred evidence items set `via.branch/env` to the prod/uat branch (or `v*` tag) the matched sha is reachable from.
- Notes for `kind=review` go to `refs/notes/prov/reviews`.
- LLM provider: Groq first (if `GROQ_API_KEY` is set), then `[llm] fallback_base_url` (Ollama, `qwen2.5-coder:7b`).
- Mergiraf runs with `solve -p` (stdout); partial results are accepted only if every remaining block maps back to an
  original hunk, so decisions stay attributable.
- `metadata_state` is `recorded` or `none`; `inferred` (remote advertises notes but fetch skipped) would need a network
  call during `analyze` and is not computed.

## JSON outputs added beyond docs/08 (schema_version 1)

```text
git prov resolve --mergiraf --json   {"files": {"<path>": {"solved": n, "remaining": n, "staged": bool, "note"?: str}}}
git prov backport <sha> --to a,b     {"commit": sha, "targets": {"<branch>": {"status": "clean|conflict|failed", "worktree": path, "hunks"?: n, "report"?: path}}}
git prov backport ... --preview      {"commit": sha, "preview": {"<branch>": {"clean": bool, "conflicted_files": [..]}}}
git prov import-junit                {"record_id", "attached_to", "ref", "result"}
git prov export --in-toto <rev>      {"commit": sha, "statements": [in-toto Statement]}
git prov llm-warm                    {"drafted": [{"hunk_id", "gates_passed"}], "failed": {"<hunk_id>": message}}
git prov llm-show <hash>             {"entries": [{"at", "model", "prompt_hash", "cached", "usage", "outcome", "system", "user", "response"}]}
git prov sync                        {"merged": [refs], "pushed": [refs]}
git prov doctor                      {"checks": {"<name>": bool}}
git prov rerere-import               {"imported": n}
unexpected bug with --json           {"error": {"code": "internal", "message", "hint", "exit": 1}}
```

Additive fields (optional, old readers ignore them; schema_version stays 1):

```text
ConflictReport.operation      also "am" (rebase-apply/applying) and "squash-merge" (SQUASH_MSG, no MERGE_HEAD)
FileConflict.kind             also "submodule" (gitlink, mode 160000; no blob is read)
FileConflict.ours/theirs      file-level SideReport (last commit touching the path) for conflicts without hunks
FileConflict / HunkReport     previous_resolution: sha of an earlier merge whose own merge hit this same block
HunkReport.moved_to           path where one side moved the lines it deleted here
SideReport.tip_tree_deployed  envs with a recorded, not rolled-back deploy of this side's tip tree
EvidenceItem.rolled_back_by   later prod deploy (ordered by `at`) of a commit that does not contain this one
```

Identity rules added from the fixture suite (`tests/unit/test_fixture_regressions.py`):

- A trailer link keeps 1.0 only with an identical patch-id. Otherwise the code must back it: hunks contained
  (squashed backport) → 0.95, similarity ≥ 0.40 → 0.95 with the behaviour guard, else 0.30 ("code does not match").
  A trailer naming a commit the repo lacks is listed at 0.0.
- The behaviour guard compares only files both diffs touch (partial backports).
- Missing prerequisite: if the change calls a function that the match's tree defines and this tree does not, the
  match is capped at 0.55 (listed, carries no evidence).
- A side with several origins takes the weakest origin's badge; origins already in the merge base are ignored.
- Commits with records but on no branch (rebased away) and merges named by `cherry-pick -m` trailers are indexed;
  merges are diffed against their first parent.

## Evaluation methodology notes

- `l3_accept` was tuned on Django E1 only (0.55 → 0.72: removes the three templated-doc false matches, "Added CVE-… to
  security archive", "Added stub release notes for 5.1.x"); `l5_band` widened to 0.40–0.72 so everything below the cut
  goes to the LLM judge when `--llm` is on. CPython is the held-out repo.
- L3 token lists are capped at 3,000 tokens (`SequenceMatcher(autojunk=False)` is quadratic; one CPython diff stalled
  the run). Huge changes are matched by L1/L2 anyway.
- Ground truth on the searched branch: CPython chains backports (3.11 ← 3.12 ← main); the miner records the immediate
  source, so a prediction is scored against the root reached by following `cherry picked from` to the source branch.
- Negatives are stable-branch commits without a backport trailer, **minus** unlabeled backports: commits whose identical
  patch or exact subject (without `[x.y]` prefix and PR refs) exists on the source branch. Before this rule, 14 of 15
  CPython "false positives" were genuine manual backports.
- E3 runs Mergiraf on every replayed conflict; the LLM stage (`--llm`) needs `GROQ_API_KEY` or Ollama.
