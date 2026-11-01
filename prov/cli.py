"""`git prov` CLI. Contract for --json output: docs/08-cli-and-json-api.md."""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console

from prov import config, dotenv
from prov.errors import ProvError
from prov.gitio import Repo
from prov.models import SCHEMA_VERSION, Env, Kind, Result

app = typer.Typer(add_completion=False, no_args_is_help=True, pretty_exceptions_enable=False, help="Conflict resolution with testing provenance.")
err = Console(stderr=True)
out = Console()

# Global flags accepted anywhere on the line (parsed in main()): --json, --repo <path>, -v/--verbose
G = {"json": False, "repo": ".", "verbose": False}


def repo() -> Repo:
    r = Repo(G["repo"])
    dotenv.load(r.root / ".env", dotenv.PROJECT_ENV)  # repo-local first, then the git-prov checkout
    return r


def emit(doc: dict | str, human: str | None = None) -> None:
    if G["json"]:
        if isinstance(doc, dict):
            doc = json.dumps({"schema_version": SCHEMA_VERSION, **doc}, default=str)
        sys.stdout.write(doc + "\n")
    else:
        out.print(human if human is not None else doc)


BADGE_STYLE = {"PROD_VERIFIED": "green", "TESTED_ELSEWHERE": "yellow", "FAILED": "red"}


def _badge(side) -> str:
    style = BADGE_STYLE.get(side.badge.value, "dim")
    suffix = f" ({side.tier.value})" if side.tier.value != "recorded" else ""
    return f"[{style}]{side.badge.value.replace('_', '-')}{suffix}[/]"


# ---------------- commands ----------------
@app.command()
def init(yes: bool = typer.Option(False, "--yes", help="Write without asking")):
    """Write .prov.toml from existing branch names."""
    r = repo()
    names = {ref.split("/", 1)[1] if ref.startswith("origin/") else ref
             for ref in r.run(["for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes"]).split()}
    envs = {pat: env for pat, env in config.DEFAULT_ENVIRONMENTS.items()
            if any(config.Config(environments={pat: env}).env_for(n) for n in names)}
    path = r.root / ".prov.toml"
    if not yes and not G["json"] and not typer.confirm(f"Write {path} with {envs}?"):
        raise typer.Exit(2)
    path.write_text(config.render(envs))
    emit({"path": str(path), "environments": envs}, f"wrote {path}")


@app.command()
def install(remote: str = typer.Option("origin", "--remote")):
    """Configure notes refspecs, notes rewrite, rerere, diff3 and the pre-push hook (idempotent)."""
    from prov.store.sync import install as do_install

    done = do_install(repo(), remote)
    emit({"changed": done}, "\n".join(done) or "already installed")


def _reachable(url: str) -> bool:
    import urllib.request

    try:
        urllib.request.urlopen(url, timeout=1)
        return True
    except OSError:
        return False


def _has_module(name: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(name) is not None


@app.command()
def doctor():
    """Check the environment."""
    import os

    r = repo()
    ver = r.run(["version"]).split()[2]
    checks = {
        "git>=2.38": tuple(int(x) for x in ver.split(".")[:2]) >= (2, 38),
        "installed": r.run(["config", "prov.installed"], check=False).strip() == "1",
        ".prov.toml": (r.root / ".prov.toml").exists(),
        "notes": bool(r.run(["for-each-ref", "--count=1", "refs/notes/prov/"]).strip()),
        "mergiraf": shutil.which("mergiraf") is not None,
        "GROQ_API_KEY": bool(os.environ.get("GROQ_API_KEY")),
        "ollama (offline LLM)": _reachable("http://localhost:11434/api/tags"),
        "tree-sitter": _has_module("tree_sitter_language_pack"),
    }
    emit({"checks": checks}, "\n".join(f"{'[green]ok[/]' if v else '[red]--[/]'} {k}" for k, v in checks.items()))


@app.command()
def index(rebuild: bool = typer.Option(False, "--rebuild")):
    """Build/refresh the SQLite index."""
    from prov.store.index import Index

    r = repo()
    if rebuild:
        (r.prov_dir / "index.db").unlink(missing_ok=True)
    idx = Index(r, config.load(r.root))
    idx.refresh()
    n = idx.db.execute("SELECT COUNT(*) FROM commits").fetchone()[0]
    emit({"commits": n, "branches": [b for _, b, _ in idx.branches()]}, f"indexed {n} commits")


@app.command()
def sync(fetch_only: bool = typer.Option(False, "--fetch-only"), push_only: bool = typer.Option(False, "--push-only"),
         quiet: bool = typer.Option(False, "--quiet")):
    """Fetch notes -> cat_sort_uniq merge -> push."""
    from prov.store.sync import sync as do_sync

    r = repo()
    res = do_sync(r, config.load(r.root).remote, fetch=not push_only, push=not fetch_only)
    if not quiet:
        emit(res, f"merged: {', '.join(res['merged']) or '-'}  pushed: {', '.join(res['pushed']) or '-'}")


@app.command()
def record(
    rev: str,
    env: Env = typer.Option(..., "--env"),
    kind: Kind = typer.Option(..., "--kind"),
    result: str = typer.Option(..., "--result", help="passed|failed|warned"),
    suite: str | None = typer.Option(None, "--suite"),
    url: str | None = typer.Option(None, "--url"),
    failed_test: list[str] = typer.Option([], "--failed-test"),
    runner: str | None = typer.Option(None, "--runner"),
    at: datetime | None = typer.Option(None, "--at", hidden=True),
):
    """Write a provenance record for <rev>."""
    from prov.store.notes import record as do_record
    from prov.store.records import NOTES_REF

    rec, attached = do_record(repo(), rev, kind=kind, env=env, result=Result(result.upper()), at=at, suite=suite,
                              url=url, failed_tests=failed_test, runner=runner)
    emit({"record_id": rec.id, "attached_to": attached, "ref": f"refs/notes/{NOTES_REF[kind]}"},
         f"recorded {kind.value} {env.value} {rec.result.value} on {rec.subject.commit[:7]}")


@app.command()
def status(rev: str, llm: bool = typer.Option(False, "--llm")):
    """Equivalents across branches + evidence + badge for one commit."""
    from prov.analyze.report import status as do_status

    res = do_status(repo(), rev, llm)
    lines = [f"{res['commit'][:7]}  {res['badge']} ({res['tier']})"]
    lines += [f"  {m['branch']:<20} {m['sha'][:7]} {m['level']:<12} {m['confidence']:.2f} {m['reason']}" for m in res["matches"]]
    lines += [f"  - {e['tier']}: {e['note']} via {e['via']['sha'][:7]}" for e in res["evidence"]]
    emit(res, "\n".join(lines))


@app.command()
def analyze(path: list[str] = typer.Option([], "--path"), llm: bool = typer.Option(False, "--llm")):
    """Full conflict report for the current operation."""
    from prov.analyze.report import analyze as do_analyze

    rep = do_analyze(repo(), paths=path or None, use_llm_identity=llm)
    if G["json"]:
        sys.stdout.write(rep.model_dump_json() + "\n")
        return
    out.print(f"[bold]{rep.operation}[/] of {(rep.theirs_ref or '')[:7]} onto {rep.ours_ref[:7]}  "
              f"metadata: {rep.metadata_state.value}")
    for fc in rep.files:
        out.print(f"\n[bold]{fc.path}[/] ({fc.kind})")
        for h in fc.hunks:
            out.print(f"  {h.id}  lines {h.start_line}-{h.end_line}  hash {h.content_hash[:10]}")
            for side in (h.ours, h.theirs):
                o = side.origins[0] if side.origins else None
                origin = f"{o.sha[:7]} {o.subject} — {o.author}" if o else "-"
                out.print(f"    {side.side.upper():<7} {_badge(side)}  {origin}")
                for e in side.evidence[:4]:
                    out.print(f"            · {e.note} [{e.tier.value}] via {e.via.sha[:7]} on {e.via.branch or '?'}"
                              f" ({e.via.level.value})")
            out.print(f"    [bold]→ {h.recommendation}[/]: {h.recommendation_reason}")


@app.command()
def apply(
    hunk: str = typer.Option(..., "--hunk"),
    take: str = typer.Option(..., "--take", help="ours|theirs|base|draft|text"),
    hash: str = typer.Option(..., "--hash", help="content_hash from analyze"),
    text_file: Path | None = typer.Option(None, "--text-file"),
):
    """Replace one marker block with the chosen side."""
    from prov.resolve.apply import apply as do_apply

    if take not in ("ours", "theirs", "base", "draft", "text"):
        raise ProvError("usage", f"--take must be ours|theirs|base|draft|text, got {take}")
    r = repo()
    text = text_file.read_text() if text_file else None
    model = phash = None
    if take == "draft":
        from prov.llm.draft import load_draft

        d = load_draft(r, hash)
        if d is None:
            raise ProvError("usage", f"no draft for {hunk}", hint=f"run `git prov draft --hunk {hunk}` first")
        if not d.gates_passed:
            raise ProvError("gates_failed", "draft failed gates: " + "; ".join(d.gate_errors))
        text, model, phash = d.resolved_code, d.model, d.prompt_hash
    res = do_apply(r, hunk, take, expected_hash=hash, text=text, llm_model=model, llm_prompt_hash=phash)
    emit(res, f"{hunk}: took {take}; {res['remaining_hunks_in_file']} hunk(s) left in file"
              + (" (staged)" if res["file_staged"] else ""))


@app.command()
def commit(
    no_tests: bool = typer.Option(False, "--no-tests"),
    quick: bool = typer.Option(False, "--quick"),
    allow_failed_gates: bool = typer.Option(False, "--allow-failed-gates"),
    message: str | None = typer.Option(None, "-m", "--message"),
    empty: str | None = typer.Option(None, "--empty", help="if the resolution keeps nothing new: commit | skip"),
):
    """Gates + trailers + continue the operation + resolution note."""
    from prov.resolve.commit import finalize

    if empty not in (None, "commit", "skip"):
        raise ProvError("usage", "--empty must be commit or skip")
    res = finalize(repo(), run_tests=not no_tests, quick=quick, allow_failed_gates=allow_failed_gates, message=message,
                   empty=empty)
    if res.get("skipped"):
        emit(res, f"skipped {res['source'][:10]}: nothing from it was kept")
        return
    emit(res, f"committed {res['commit'][:10]}\n" + "\n".join(res["trailers"]))


@app.command()
def draft(hunk: str = typer.Option(..., "--hunk"), no_cache: bool = typer.Option(False, "--no-cache"),
          model: str | None = typer.Option(None, "--model")):
    """LLM explanation + draft for one hunk (gates run on a temp copy)."""
    from prov.llm.draft import draft as do_draft

    d = do_draft(repo(), hunk, use_cache=not no_cache, model=model)
    human = (f"[bold]{d.hunk_id}[/] strategy={d.strategy} gates={'[green]passed[/]' if d.gates_passed else '[red]failed[/]'}"
             f"\nOURS:   {d.explanation_ours}\nTHEIRS: {d.explanation_theirs}\n\n{d.resolved_code}"
             + "".join(f"\n[yellow]risk:[/] {n}" for n in d.risk_notes)
             + "".join(f"\n[red]gate:[/] {e}" for e in d.gate_errors)
             + (f"\nuntested lines: {d.untested_lines}" if d.untested_lines else ""))
    emit(d.model_dump(mode="json"), human)


@app.command("llm-warm")
def llm_warm():
    """Pre-compute drafts for all hunks (demo safety)."""
    from prov.llm.draft import warm

    res = warm(repo())
    if res["failed"] and not res["drafted"]:
        raise ProvError("llm_unavailable", "no hunk could be drafted: " + next(iter(res["failed"].values())),
                        hint="set GROQ_API_KEY or start Ollama (`ollama serve`)")
    emit(res, f"drafted {len(res['drafted'])} hunk(s)" + "".join(f"\n  {k}: {v}" for k, v in res["failed"].items()))


@app.command("llm-show")
def llm_show(prompt_hash: str):
    """Show a logged prompt + response."""
    from prov.llm.provider import show

    entries = show(repo(), prompt_hash)
    if not entries:
        raise ProvError("usage", f"no logged LLM call for {prompt_hash}")
    e = entries[-1]
    emit({"entries": entries}, f"[bold]{e['model']}[/] {e['at']} cached={e['cached']}\n--- system\n{e['system']}"
                               f"\n--- user\n{e['user']}\n--- response\n{e['response']}")


@app.command()
def resolve(mergiraf: bool = typer.Option(False, "--mergiraf"), path: list[str] = typer.Option([], "--path")):
    """Auto-solve structural conflicts with Mergiraf."""
    from prov.resolve.mergiraf import solve

    if not mergiraf:
        raise ProvError("usage", "nothing to do", hint="pass --mergiraf")
    res = solve(repo(), path or None)
    emit(res, "\n".join(f"{p}: solved {v['solved']}, remaining {v['remaining']}" + (f" ({v['note']})" if v.get("note") else "")
                         for p, v in res["files"].items()) or "no conflicted files")


@app.command("import-junit")
def import_junit(file: Path, rev: str, env: Env = typer.Option(..., "--env"),
                 suite: str | None = typer.Option(None, "--suite"), url: str | None = typer.Option(None, "--url")):
    """Record a test result from JUnit XML (FAILED if any failure/error)."""
    from prov.store.notes import record as do_record
    from prov.store.records import parse_junit

    result, failed = parse_junit(file.read_text())
    rec, attached = do_record(repo(), rev, kind=Kind.test, env=env, result=Result(result), suite=suite or file.stem,
                              url=url, failed_tests=failed)
    emit({"record_id": rec.id, "attached_to": attached, "ref": "refs/notes/prov/tests", "result": result},
         f"recorded {env.value} test {result} on {rec.subject.commit[:7]} ({len(failed)} failed)")


@app.command("import-notes")
def import_notes(ref: str = typer.Option("tests", "--ref", help="foreign notes ref with JSON-line records"),
                 file: Path | None = typer.Option(None, "--file", help="JSON file: {records: [...]} or [...]")):
    """Import test/deploy records written by another tool (env/result names are normalised)."""
    from prov.store.importer import import_records

    res = import_records(repo(), notes_ref=ref if ref else None, file=str(file) if file else None)
    emit(res, f"imported {res['imported']} new record(s) from {res['seen']} ({res['skipped']} skipped)")


@app.command()
def export(rev: str, in_toto: bool = typer.Option(True, "--in-toto")):
    """Print in-toto statements for a commit."""
    from prov.store.notes import records_for
    from prov.store.records import to_in_toto

    r = repo()
    sha = r.rev_parse(f"{rev}^{{commit}}")
    if not sha:
        raise ProvError("usage", f"unknown revision {rev}")
    statements = [to_in_toto(x) for x in records_for(r, sha)]
    emit({"commit": sha, "statements": statements}, json.dumps(statements, indent=1))


@app.command()
def backport(sha: str, to: str = typer.Option(..., "--to", help="comma-separated target branches"),
             preview: bool = typer.Option(False, "--preview"), no_commit: bool = typer.Option(False, "--no-commit")):
    """Cherry-pick into worktrees per target branch and analyze conflicts. Never pushes."""
    from prov.resolve import backport as bp

    r = repo()
    targets = [t for t in to.split(",") if t]
    if preview:
        res = bp.preview(r, sha, targets)
        emit({"commit": sha, "preview": res}, "\n".join(
            f"{b:<20} {'clean' if v['clean'] else 'CONFLICT ' + ', '.join(v['conflicted_files'])}" for b, v in res.items()))
        return
    res = bp.backport(r, sha, targets, no_commit=no_commit)
    emit(res, "\n".join(f"{b:<20} {v['status']:<9} {v['worktree']}" + (f"  ({v['hunks']} hunks)" if "hunks" in v else "")
                         for b, v in res["targets"].items()))


@app.command("rerere-import")
def rerere_import():
    """Install rerere resolutions shared through resolution notes."""
    from prov.resolve.rerere import import_all

    n = import_all(repo())
    emit({"imported": n}, f"imported {n} rerere resolution(s)")


# ---------------- entry ----------------
def _pop_globals(argv: list[str]) -> list[str]:
    rest, i = [], 0
    while i < len(argv):
        a = argv[i]
        if a == "--json":
            G["json"] = True
        elif a in ("-v", "--verbose"):
            G["verbose"] = True
        elif a == "--repo" and i + 1 < len(argv):
            G["repo"] = argv[i + 1]
            i += 1
        elif a.startswith("--repo="):
            G["repo"] = a.split("=", 1)[1]
        else:
            rest.append(a)
        i += 1
    return rest


def main(argv: list[str] | None = None) -> None:
    args = _pop_globals(list(sys.argv[1:] if argv is None else argv))
    try:
        app(args, standalone_mode=False)
    except ProvError as e:
        if G["json"]:
            sys.stdout.write(json.dumps({"schema_version": SCHEMA_VERSION, "error": e.to_json()}) + "\n")
        else:
            err.print(f"[red]error[/] ({e.code}): {e.message}" + (f"\n  hint: {e.hint}" if e.hint else ""))
        sys.exit(e.exit)
    except typer.Exit as e:
        sys.exit(e.exit_code)
    except (typer.Abort, KeyboardInterrupt):
        sys.exit(1)
    except Exception as e:
        if hasattr(e, "show") and hasattr(e, "exit_code"):  # click usage error (typer vendors click)
            if G["json"]:
                sys.stdout.write(json.dumps({"schema_version": SCHEMA_VERSION, "error": {
                    "code": "usage", "message": e.format_message(), "hint": None, "exit": 2}}) + "\n")
            else:
                e.show()
            sys.exit(2)
        if G["json"]:  # bug: keep the contract, details on stderr
            import traceback

            traceback.print_exc()
            sys.stdout.write(json.dumps({"schema_version": SCHEMA_VERSION, "error": {
                "code": "internal", "message": f"{type(e).__name__}: {e}", "hint": "please report", "exit": 1}}) + "\n")
            sys.exit(1)
        raise
