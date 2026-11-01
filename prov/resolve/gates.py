"""Gates run before committing a resolution, and on drafts (docs/06 §3)."""

from __future__ import annotations

import ast
import glob
import os
import re
import shlex
import subprocess
from collections import Counter
from fnmatch import fnmatchcase
from pathlib import Path

from prov.analyze.conflicts import MARKER
from prov.config import Config
from prov.gitio import Repo


def check_markers(path: str, src: str) -> list[str]:
    for n, line in enumerate(src.splitlines(), start=1):
        if MARKER.match(line):
            return [f"{path}:{n} conflict marker left"]
    return []


def check_parse(path: str, src: str) -> list[str]:
    if path.endswith(".py"):
        try:
            compile(src, path, "exec")
        except SyntaxError as e:
            return [f"{path}:{e.lineno} {e.msg}"]
        return []
    from prov.identity.fuzzy import LANGS, _parser

    ext = os.path.splitext(path)[1]
    parser = _parser(LANGS[ext][0]) if ext in LANGS else None
    if parser is None:
        return []
    root = parser.parse(src.encode("utf-8", "surrogateescape")).root_node
    if not root.has_error:
        return []
    stack = [root]
    while stack:  # report the first ERROR/MISSING node
        n = stack.pop()
        if n.type == "ERROR" or n.is_missing:
            return [f"{path}:{n.start_point[0] + 1} syntax error"]
        stack.extend(reversed(n.children))
    return [f"{path}: syntax error"]


def top_level_dups(src: str) -> set[str]:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    names = Counter(n.name for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef))
    return {k for k, v in names.items() if v > 1}


def check_dup_defs(path: str, src: str, side_srcs: list[str]) -> list[str]:
    if not path.endswith(".py"):
        return []
    dups = top_level_dups(src) - set().union(*(top_level_dups(s) for s in side_srcs))
    return [f"{path}: duplicate top-level definitions {', '.join(sorted(dups))}"] if dups else []


def check_text(path: str, src: str, side_srcs: list[str] = ()) -> list[str]:
    """Static gates on one file's content (used for drafts on a temp copy)."""
    return check_markers(path, src) + check_parse(path, src) + check_dup_defs(path, src, list(side_srcs))


def select_tests(repo: Repo, cfg: Config, paths: list[str]) -> list[str]:
    tests = set()
    for path in paths:
        p = Path(path)
        for rule in cfg.test_map:
            if fnmatchcase(path, rule["src"]):
                pattern = rule["tests"].format(stem=p.stem, name=p.name, dir=str(p.parent))
                tests |= {os.path.relpath(t, repo.root) for t in glob.glob(str(repo.root / pattern), recursive=True)}
        if fnmatchcase(p.name, "test_*") and (repo.root / path).exists():
            tests.add(path)
    return sorted(tests)


TEST_FILE = re.compile(r"(^|/)(tests?/|test_[^/]*\.py$|[^/]*_test\.(py|go|js|ts)$|[^/]*\.(test|spec)\.[jt]sx?$)")


def has_tests(repo: Repo) -> bool:
    return any(TEST_FILE.search(p) for p in repo.run(["ls-files", "-z"]).split("\0") if p)


def run_tests(repo: Repo, cfg: Config, paths: list[str], *, quick: bool = False) -> tuple[bool | None, str]:
    """-> (passed, command or error text). passed=None means skipped (never counts as tested)."""
    tests = select_tests(repo, cfg, paths)
    if not tests and quick:
        return None, "no mapped tests (--quick)"
    if not tests and not has_tests(repo):
        return None, "repository has no tests"
    cmd = cfg.test_command.replace("{tests}", " ".join(shlex.quote(t) for t in tests)).strip()
    try:
        # The test command comes from the repo's own committed .prov.toml, like a Makefile target.
        p = subprocess.run(cmd, shell=True, cwd=repo.root, capture_output=True, text=True, timeout=cfg.test_timeout_s)
    except subprocess.TimeoutExpired:
        return False, f"timeout after {cfg.test_timeout_s}s: {cmd}"
    if p.returncode != 0:
        tail = "\n".join((p.stdout + p.stderr).splitlines()[-40:])
        return False, f"{cmd}\n{tail}"
    return True, cmd


def run_gates(repo: Repo, cfg: Config, paths: list[str], *, side_refs: list[str], tests: bool = True,
              quick: bool = False) -> tuple[dict[str, bool], list[str], str | None]:
    """-> (gates, errors, test command that passed or None)"""
    errors: dict[str, list[str]] = {"no_markers": [], "parse": [], "no_dup_defs": []}
    for path in paths:
        f = repo.root / path
        if not f.exists():
            continue
        src = f.read_bytes().decode("utf-8", "surrogateescape")
        errors["no_markers"] += check_markers(path, src)
        errors["parse"] += check_parse(path, src)
        sides = [repo.run(["show", f"{r}:{path}"], check=False) for r in side_refs if r]
        errors["no_dup_defs"] += check_dup_defs(path, src, sides)
    gates = {k: not v for k, v in errors.items()}
    flat = [e for v in errors.values() for e in v]
    passed_cmd = None
    if tests and all(gates.values()):  # no point running tests on a file that does not parse
        ok, info = run_tests(repo, cfg, paths, quick=quick)
        if ok is not None:
            gates["tests"] = ok
            if ok:
                passed_cmd = info
            else:
                flat.append(info)
    return gates, flat, passed_cmd
