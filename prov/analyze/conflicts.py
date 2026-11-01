"""Discover the operation, conflicted files and hunks (docs/05 §1-3)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from prov.errors import ProvError
from prov.gitio import Repo

MARKER = re.compile(r"^(<{7}|\|{7}|={7}|>{7})(?: |$)")

OPS = [("CHERRY_PICK_HEAD", "cherry-pick"), ("MERGE_HEAD", "merge"), ("REBASE_HEAD", "rebase"), ("REVERT_HEAD", "revert")]


@dataclass
class Block:
    ours: str
    base: str | None
    theirs: str
    start_line: int  # 1-based line of <<<<<<<
    end_line: int  # 1-based line of >>>>>>>
    offsets: dict[str, int] = field(default_factory=dict)  # side -> 0-based line where the side text starts in its blob

    @property
    def content_hash(self) -> str:
        return hashlib.sha1(f"{self.base or ''}\0{self.ours}\0{self.theirs}".encode("utf-8", "surrogateescape")).hexdigest()


@dataclass
class Hunk:
    path: str
    index: int
    block: Block  # structured texts (from stage blobs) + working-file line numbers


def operation(repo: Repo) -> tuple[str, str | None]:
    for fname, op in OPS:
        f = repo.git_dir / fname
        if f.exists():
            return op, f.read_text().split()[0]
    am = repo.git_dir / "rebase-apply"
    if (am / "applying").exists():  # git am: theirs = the "From <sha>" of the current patch, when we have it
        patch = am / f"{int((am / 'next').read_text()):04d}"
        m = re.match(r"From ([0-9a-f]{40}) ", patch.read_text(errors="replace") if patch.exists() else "")
        return "am", m.group(1) if m and repo.rev_parse(f"{m.group(1)}^{{commit}}") else None
    squash = repo.git_dir / "SQUASH_MSG"
    if squash.exists() and repo.ls_files_unmerged():  # merge --squash: no MERGE_HEAD; the message lists the commits
        m = re.search(r"^commit ([0-9a-f]{40})$", squash.read_text(errors="replace"), re.MULTILINE)
        return "squash-merge", m.group(1) if m else None
    return "none", None


def parse_blocks(text: str) -> list[Block]:
    """Marker blocks, with or without a ||||||| base section. Markers must start at column 0."""
    blocks: list[Block] = []
    pos = {"ours": 0, "base": 0, "theirs": 0}
    cur: dict | None = None
    mode = ""
    for n, line in enumerate(text.splitlines(keepends=True), start=1):
        m = MARKER.match(line.rstrip("\r\n"))
        kind = m.group(1)[0] if m else ""
        if cur is None:
            if kind == "<":
                cur, mode = {"ours": [], "base": None, "theirs": [], "start": n, "offsets": dict(pos)}, "ours"
            else:
                for k in pos:
                    pos[k] += 1
            continue
        if kind == "|" and mode == "ours":
            cur["base"], mode = [], "base"
        elif kind == "=" and mode in ("ours", "base"):
            mode = "theirs"
        elif kind == ">" and mode == "theirs":
            base = "".join(cur["base"]) if cur["base"] is not None else None
            blocks.append(Block("".join(cur["ours"]), base, "".join(cur["theirs"]), cur["start"], n, cur["offsets"]))
            for side in pos:
                pos[side] += len(cur[side] or [])
            cur = None
        else:
            cur[mode].append(line)
    return blocks


def file_kind(stages: dict[int, tuple[str, str]], blobs: dict[int, bytes]) -> str:
    present = set(stages)
    if present == {1, 2, 3}:
        return "binary" if any(b"\0" in b for b in blobs.values()) else "content"
    if present == {2, 3}:
        return "add_add"
    if 1 in present and len(present) == 2:
        return "modify_delete"
    return "other"


def hunks_for(repo: Repo, path: str, stages: dict[int, tuple[str, str]], blobs: dict[int, bytes]) -> list[Hunk]:
    try:
        working = parse_blocks((repo.root / path).read_bytes().decode("utf-8", "surrogateescape"))
    except FileNotFoundError:
        return []
    # re-merge in the working file's conflict style: git's default "merge" style splits conflicts into more,
    # smaller blocks than diff3, and a block we can't match would silently vanish from the report
    diff3 = not working or any(w.base is not None for w in working)
    args = (blobs.get(2, b""), blobs.get(1, b""), blobs.get(3, b""))
    structured = parse_blocks(repo.merge_file_diff3(*args, diff3))
    if not diff3:  # merge-style blocks carry no base: borrow it from the diff3 block that contains each one
        wide = parse_blocks(repo.merge_file_diff3(*args))
        for st in structured:
            st.base = next((b.base for b in wide if st.ours in b.ours and st.theirs in b.theirs), None)
    # i-th working block is matched to the first unused structured hunk with the same ours/theirs texts;
    # blocks the user already resolved (or edited inside) drop out.
    out, used = [], set()
    for w in working:
        for j, s in enumerate(structured):
            if j not in used and s.ours == w.ours and s.theirs == w.theirs:
                used.add(j)
                s.start_line, s.end_line = w.start_line, w.end_line
                out.append(Hunk(path, len(out), s))
                break
    return out


def discover(repo: Repo, paths: list[str] | None = None) -> tuple[str, str | None, list[tuple[str, str, list[Hunk]]]]:
    """-> (operation, theirs sha, [(path, kind, hunks)])"""
    op, theirs = operation(repo)
    if op == "none":
        hint = "start a cherry-pick, merge, rebase, revert or am"
        if repo.ls_files_unmerged():  # e.g. `cherry-pick --no-commit`: Git keeps no *_HEAD, so "theirs" is unknown
            hint = "conflicts exist but no operation is recorded; redo the pick without --no-commit / -n"
        raise ProvError("no_conflict", "no conflict in progress", hint=hint)
    files = []
    for path, stages in sorted(repo.ls_files_unmerged().items()):
        if paths and path not in paths:
            continue
        if any(mode == "160000" for mode, _ in stages.values()):  # gitlink: a commit id, not a blob
            files.append((path, "submodule", []))
            continue
        blobs = {st: repo.cat_blob(blob) for st, (_, blob) in stages.items()}
        kind = file_kind(stages, blobs)
        hunks = hunks_for(repo, path, stages, blobs) if kind in ("content", "add_add") else []
        files.append((path, kind, hunks))
    # git parks one side of a file/directory or file/symlink conflict at "<path>~<label>": report the real path once
    merged: dict[str, tuple[str, str, list[Hunk]]] = {}
    for path, kind, hunks in files:
        real = _real_path(repo, path, theirs)
        if real not in merged:
            merged[real] = (real, kind if real == path else "other", hunks)
        elif not merged[real][2] and hunks:
            merged[real] = (real, merged[real][1], hunks)
    return op, theirs, list(merged.values())


def _real_path(repo: Repo, path: str, theirs: str | None) -> str:
    base, sep, label = path.rpartition("~")
    if sep and base and "/" not in label and any(repo.ok(["cat-file", "-e", f"{r}:{base}"]) for r in ("HEAD", theirs) if r):
        return base
    return path


def current_hunks(repo: Repo, path: str) -> list[Hunk]:
    stages = repo.ls_files_unmerged().get(path)
    if not stages:
        return []
    blobs = {st: repo.cat_blob(blob) for st, (_, blob) in stages.items()}
    return hunks_for(repo, path, stages, blobs)
