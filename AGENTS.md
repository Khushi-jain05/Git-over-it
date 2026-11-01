# AGENTS.md — rules for anyone (human or coding agent) writing code here

## Hard rules (never break)

1. **The LLM never decides facts.** Origin commits, identity matches at L0–L4, test results, deploy status, badges and evidence tiers are computed deterministically. LLM output may only appear in fields named `explanation_*`, `draft_*`, `llm_*` or `risk_notes`.
2. **Output is standard Git.** No custom object types, no required custom refs to read history. Everything we add is a commit message trailer or a note under `refs/notes/prov/*`.
3. **Never claim more than the evidence.** Every badge carries an `evidence_tier` (`recorded`, `inferred`, `none`). A badge with tier `inferred` must never render as the same colour as `recorded`.
4. **Never auto-commit an LLM draft.** A human (or the `--auto` eval mode, which never pushes) must accept it, and the resolution record must say an LLM drafted it.
5. **Never write to the user's working tree outside the conflicted files**, and never run `git reset --hard`, `git clean`, or force-push in any code path.
6. **`--json` output is a contract.** Change it only by bumping `schema_version` and updating `docs/08-cli-and-json-api.md` and the extension together.

## Conventions

- Python 3.11+, type hints everywhere, `from __future__ import annotations`.
- Pydantic v2 models in `prov/models.py` are the single source of truth for data shapes.
- All Git access goes through `prov/gitio.py`. No other module calls `subprocess` for git.
- `gitio` runs `git -c core.quotepath=off` with `LC_ALL=C` and decodes stdout as UTF-8 with `errors="surrogateescape"`.
- Use NUL-separated (`-z`) plumbing output whenever a path is involved.
- Logging: `rich` console for humans; when `--json` is set, stdout carries **only** the JSON document and logs go to stderr.
- Errors: raise `ProvError(code, message, hint)`; the CLI maps it to exit codes in `docs/08`.
- Tests: `pytest`, fixtures build tiny repos in `tmp_path` with `tests/helpers/repo.py` (`make_repo()`, `commit(files, msg)`, `branch(name)`).
- Formatting: `ruff check`. TypeScript: `tsc --strict`.

## Commands

```bash
pytest -q                                   # unit + integration tests
python -m prov --help                       # CLI (also installed as git-prov)
python scripts/make_sim_repo.py /tmp/sim    # simulated dev/uat/prod repo
python -m eval.replay identity --repo ../django --name django --limit 200
cd vscode-ext && npm i && npm run compile && npm test
```

## Definition of done for any ticket

- Code + tests merged on `main`, CI green.
- If it touches `--json` output, `docs/08` example updated (and `docs/IMPLEMENTATION.md` for new commands).
- The simulated repo scenarios still pass `pytest tests/e2e`.
