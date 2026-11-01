# Examples: real input, real output

Everything in this folder was produced by running the tool; nothing is hand-written except this README.

## 01: hotfix forward-port (`01-hotfix-forward-port/`)

A production hotfix (`Fix rounding in totals`) is cherry-picked onto `dev`, which refactored the same function.
The repository has dev / uat / production branches and provenance notes (test and deploy records).

| File | What it is |
| --- | --- |
| `00-setup.txt` | output of `python scripts/make_sim_repo.py /tmp/prov-example --scenario 1 --stop-at-conflict` |
| `01-input-conflict.txt` | **input**: history graph, `git status` and the conflicted diff |
| `02-analyze.txt` / `.json` | `git prov analyze`: origin commit of each side, equivalent commits found, evidence, badges, recommendation |
| `03-llm-draft.txt` / `.json` | `git prov draft --hunk shop/billing.py#0`: explanation + resolution drafted by **openai/gpt-oss-120b via Groq** |
| `04-resolve-and-commit.txt` | `git prov apply` (theirs, as recommended) + `git prov commit`: trailers and the resolution note |

What it shows:

- THEIRS is `PROD-VERIFIED` (recorded prod deploy + passing UAT test of the same tree); OURS only passed dev tests,
  so the deterministic recommendation is `take_theirs`. The LLM never sets a badge.
- The LLM draft (03) explains both sides correctly, but its code defined `tax()` twice: the duplicate-definition
  gate **rejected** it (`gates=failed`). Drafts are suggestions; gates and a human decide.
- The final commit (04) carries `Prov-Resolved` / `Prov-Evidence` trailers, and a git note records each hunk's
  choice, both badges, and that all gates (markers, parse, duplicate defs, tests) passed.

Reproduce (needs `GROQ_API_KEY` for step 03 only):

```bash
python scripts/make_sim_repo.py /tmp/prov-example --scenario 1 --stop-at-conflict
cd /tmp/prov-example
git prov analyze
git prov draft --hunk 'shop/billing.py#0'
git prov apply --hunk 'shop/billing.py#1' --take theirs --hash <content_hash from analyze>
git prov apply --hunk 'shop/billing.py#0' --take theirs --hash <content_hash from analyze>
git prov commit
```

## 02: benchmark results (`02-benchmarks.md`)

Scores of `git prov` on the adversarial conflict benchmarks we tested against, produced by `bench/run_testing1.py`.
