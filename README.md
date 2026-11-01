# git prov

[![ci](https://github.com/Khushi-jain05/Git-over-it/actions/workflows/ci.yml/badge.svg)](https://github.com/Khushi-jain05/Git-over-it/actions/workflows/ci.yml)

Git-integrated conflict resolution with **testing provenance**. For every conflicting hunk, `git prov` tells you where
each side came from, where that change was tested and deployed, and which side is production-proven. Then it writes an
ordinary Git commit.

> Golden rule: **the LLM never sets a badge, a test status or an origin.** Facts come only from Git and recorded
> evidence. The LLM explains and drafts, and a draft must pass gates and be accepted by a human.

Team **Git Over It** — Problem 01, Intelligent Git Conflict Resolution with Testing Provenance.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[all]"            # CLI + LLM + tree-sitter AST + scipy assignment
brew install mergiraf              # optional: structural auto-merge
export GROQ_API_KEY=gsk_...        # optional: drafts/explanations (Ollama on localhost:11434 is the offline fallback)
git prov doctor
```

VS Code: build with `cd vscode-ext && npm i && npm run package`, then `code --install-extension vscode-ext/git-prov-*.vsix`.

## Five-minute tour (simulated dev / uat / production repo)

```bash
python scripts/make_sim_repo.py /tmp/sim --scenario 1 --stop-at-conflict
cd /tmp/sim
git prov analyze                    # origins, equivalents (L0-L5), evidence, badges, recommendation
git prov draft --hunk shop/billing.py#0           # LLM explanation + draft, gated on a temp copy
git prov apply --hunk shop/billing.py#0 --take theirs --hash <content_hash>
git prov apply --hunk shop/billing.py#1 --take theirs --hash <content_hash>
git prov commit                     # gates (markers, parse, dup defs, tests) -> cherry-pick --continue
git log -1                          # Prov-Change-Id / Prov-Resolved / Prov-Evidence trailers
```

Scenarios 1–7 (`--scenario N`): hotfix forward-port, promotion with an edited hunk (L2), independent same fix (L3),
rebased branch, fresh clone without notes, failed test, modify/delete. Expected badges are written to
`.git/prov/expected.json` and asserted by `pytest tests/e2e`.

## Examples

[examples/](examples/) holds real input and output: a conflicted cherry-pick, the `git prov analyze` report, a draft
written by `openai/gpt-oss-120b` (and rejected by the duplicate-definition gate), and the final commit with its
provenance trailers and resolution note. [examples/02-benchmarks.md](examples/02-benchmarks.md) has the benchmark scores.

## Badges

| Badge | Meaning |
| --- | --- |
| `PROD_VERIFIED` | matched with confidence ≥ 0.90, has a prod deploy **and** a passing uat/prod test |
| `TESTED_ELSEWHERE` | matched with confidence ≥ 0.60, a passing test somewhere |
| `UNVERIFIED` | identified, no test records |
| `FAILED` | the latest test record of the best match failed |
| `NO_METADATA` | no provenance notes in this clone |

Every badge carries a tier: `recorded` (from notes), `inferred` (branch/tag reachability), `none`. A fresh clone never
shows `recorded` until `git prov install && git prov sync`.

## Identity ladder

L0 trailers (`cherry picked from`, Django `Backport of`, `Prov-Change-Id`, transitive) · L1 `git patch-id --stable` ·
L2 path-less hunk hashes (Jaccard + cover) · L3 token similarity + function names + tree-sitter AST hashes with
range-diff style Hungarian assignment · L4 metadata adjustments · L5 optional LLM judge for the ambiguous band
(`--llm`, capped at 0.80).

## Commands

`init` · `install` · `doctor` · `index` · `sync` · `record` · `import-junit` · `status <rev>` · `analyze` · `draft` ·
`apply` · `resolve --mergiraf` · `commit` · `backport <sha> --to a,b [--preview]` · `export --in-toto <rev>` ·
`llm-warm` · `llm-show <hash>` · `rerere-import`. Every command takes `--json` (a contract, `schema_version: 1`),
`--repo <path>`, `-v`. Exit codes: 0 ok, 2 usage, 3 no conflict, 4 unresolved, 5 gates failed, 6 stale hunk,
7 git error, 8 LLM unavailable, 9 not installed.

## CI: record results in one line

```yaml
- name: Record provenance
  if: always()
  run: |
    pip install git-prov
    git prov install --remote origin
    git prov sync --fetch-only
    git prov record "$GITHUB_SHA" --env "${{ vars.PROV_ENV }}" --kind test \
      --result "${{ job.status == 'success' && 'passed' || 'failed' }}" \
      --suite pytest --url "$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID" --runner github-actions
    git prov sync --push-only
```

Deploy jobs: same with `--kind deploy --env prod --result passed`. JUnit: `git prov import-junit report.xml HEAD --env uat`.

## Standard Git in, standard Git out

Everything we add is a commit trailer or a note under `refs/notes/prov/*` (single-line JSON records; `git prov export
--in-toto` emits in-toto Statements). Proof:

```bash
pip uninstall -y git-prov
git log -3 --format='%h %s%n%(trailers)'      # history still reads cleanly
git notes --ref=prov/tests list               # records are plain git notes
```

## Evaluation

```bash
git clone --shallow-since=2024-01-01 --no-single-branch https://github.com/django/django.git ../django
python -m eval.mine_django --repo ../django                       # -> eval/data/django_pairs.jsonl
python -m eval.replay identity --repo ../django --name django --limit 342   # E1 (no trailers)
python -m eval.replay identity --repo ../django --name django --trailers    # E2
python -m eval.replay conflicts --repo ../django --name django --limit 50   # E3 replay
python -m eval.replay safety                                                # E4 on the simulated repo
python -m eval.report --out eval/report.md
```

Same for CPython with `eval.mine_cpython` and `--name cpython`. Results: [eval/report.md](eval/report.md).

Adversarial conflict benchmarks (testing-1 … testing-10, answer key never shown to the tool):

```bash
git -C ../testing-1 fetch origin 'refs/notes/*:refs/notes/*'     # the benchmark's provenance notes
python bench/run_testing1.py --repo ../testing-1 [-j 8] [S011 ...]  # config: bench/<repo>.prov.toml
```

## Development

```bash
pip install -e ".[dev]" && pytest -q && ruff check .
cd vscode-ext && npm i && npm test && npm run test:integration   # the latter launches a real VS Code
```

Layout: `prov/` (gitio, store, identity, analyze, resolve, llm, cli), `eval/`, `scripts/`, `vscode-ext/`, `tests/`.
Rules for contributors and coding agents: [AGENTS.md](AGENTS.md). Implementation notes: [docs/IMPLEMENTATION.md](docs/IMPLEMENTATION.md).
