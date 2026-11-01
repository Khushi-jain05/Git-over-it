"""The only module that runs git (AGENTS.md)."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from prov.errors import ProvError

_ENV = {**os.environ, "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0"}


def _git(args: list[str], cwd: str | Path, input: bytes | str | None, check: bool) -> subprocess.CompletedProcess:
    if isinstance(input, str):
        input = input.encode("utf-8", "surrogateescape")
    p = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=cwd, input=input, capture_output=True, env=_ENV, check=False)
    if check and p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace").strip()
        raise ProvError("git_error", f"git {' '.join(args[:3])} failed: {err}", hint=None)
    return p


def _dec(b: bytes) -> str:
    return b.decode("utf-8", "surrogateescape")


def git(args: list[str], cwd: str | Path, *, input: bytes | str | None = None, check: bool = True) -> str:
    """For commands that run outside an existing repo (init, clone)."""
    return _dec(_git(args, cwd, input, check).stdout)


class Repo:
    def __init__(self, path: str | Path = "."):
        p = _git(["rev-parse", "--show-toplevel", "--absolute-git-dir", "--git-common-dir"], path, None, False)
        if p.returncode != 0:
            raise ProvError("usage", f"not a git repository: {path}")
        top, git_dir, common = _dec(p.stdout).splitlines()
        self.root = Path(top)
        self.git_dir = Path(git_dir)  # per-worktree: *_HEAD, MERGE_MSG, pending.json
        common_p = Path(common)
        self.common_dir = common_p if common_p.is_absolute() else (self.root / common_p).resolve()
        self.prov_dir = self.common_dir / "prov"  # shared: index.db
        self._contains: dict[str, list[str]] = {}

    # ---- core ----
    def run(self, args: list[str], *, input: bytes | str | None = None, check: bool = True) -> str:
        return _dec(_git(args, self.root, input, check).stdout)

    def ok(self, args: list[str]) -> bool:
        return _git(args, self.root, None, False).returncode == 0

    # ---- wrappers ----
    def rev_parse(self, rev: str) -> str | None:
        p = _git(["rev-parse", "--verify", "--quiet", rev], self.root, None, False)
        return _dec(p.stdout).strip() or None if p.returncode == 0 else None

    def show(self, rev_path: str) -> str:
        return self.run(["show", rev_path])

    def ls_files_unmerged(self) -> dict[str, dict[int, tuple[str, str]]]:
        """path -> {stage: (mode, blob)}"""
        out: dict[str, dict[int, tuple[str, str]]] = {}
        for entry in self.run(["ls-files", "-u", "-z"]).split("\0"):
            if not entry:
                continue
            meta, path = entry.split("\t", 1)
            mode, blob, stage = meta.split()
            out.setdefault(path, {})[int(stage)] = (mode, blob)
        return out

    def cat_blob(self, blob: str) -> bytes:
        return _git(["cat-file", "blob", blob], self.root, None, True).stdout

    def merge_file_diff3(self, ours: bytes, base: bytes, theirs: bytes, diff3: bool = True) -> str:
        """diff3=False gives git's default `merge` style, whose (zealous) blocks are smaller and more numerous."""
        with tempfile.TemporaryDirectory() as d:
            names = []
            for name, data in (("ours", ours), ("base", base), ("theirs", theirs)):
                (Path(d) / name).write_bytes(data)
                names.append(str(Path(d) / name))
            args = ["merge-file", "-p", *(["--diff3"] if diff3 else []), "-L", "ours", "-L", "base", "-L", "theirs", *names]
            return _dec(_git(args, self.root, None, False).stdout)  # exit code = number of conflicts

    def blame_porcelain(self, ref: str, path: str, start: int, end: int) -> str:
        return self.run(["blame", "--porcelain", "--root", "-w", "-M", "-C", "-L", f"{start},{end}", ref, "--", path])

    def for_each_ref_contains(self, sha: str) -> list[str]:
        if sha not in self._contains:
            out = self.run(
                ["for-each-ref", "--contains", sha, "--format=%(refname:short)", "refs/heads", "refs/remotes", "refs/tags"],
                check=False,
            )
            self._contains[sha] = [r for r in out.splitlines() if r and not r.endswith("/HEAD")]
        return self._contains[sha]

    def notes_show(self, ref: str, obj: str) -> str:
        return self.run(["notes", f"--ref={ref}", "show", obj], check=False)

    def notes_add(self, ref: str, obj: str, text: str) -> None:
        self.run(["notes", f"--ref={ref}", "add", "-f", "-F", "-", obj], input=text)

    def notes_list(self, ref: str) -> list[tuple[str, str]]:
        """[(note_blob, annotated_object)]"""
        out = self.run(["notes", f"--ref={ref}", "list"], check=False)
        return [tuple(line.split()) for line in out.splitlines() if line.strip()]  # type: ignore[misc]

    def interpret_trailers(self, message: str, args: list[str]) -> str:
        return self.run(["interpret-trailers", *args], input=message)

    def patch_id(self, sha: str) -> str | None:
        diff = _git(["diff-tree", "-p", "--root", sha], self.root, None, True).stdout
        out = _dec(_git(["patch-id", "--stable"], self.root, diff, True).stdout).split()
        return out[0] if out else None

    def cat_file_batch(self, objects: list[str]) -> dict[str, bytes]:
        """Read many blobs in one process."""
        if not objects:
            return {}
        raw = _git(["cat-file", "--batch"], self.root, "\n".join(objects) + "\n", True).stdout
        out, pos = {}, 0
        for obj in objects:
            nl = raw.index(b"\n", pos)
            header = raw[pos:nl].split()
            pos = nl + 1
            if len(header) < 3 or header[1] == b"missing":
                continue
            size = int(header[2])
            out[obj] = raw[pos : pos + size]
            pos += size + 1
        return out

    def resolve_commits(self, revs: list[str]) -> dict[str, str]:
        """rev -> full commit sha, dropping dangling/ambiguous ones (one process)."""
        if not revs:
            return {}
        out = self.run(["cat-file", "--batch-check"], input="".join(f"{r}^{{commit}}\n" for r in revs))
        res = {}
        for rev, line in zip(revs, out.splitlines(), strict=True):
            parts = line.split()
            if len(parts) == 3 and parts[1] == "commit":
                res[rev] = parts[0]
        return res
