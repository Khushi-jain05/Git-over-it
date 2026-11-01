"""SQLite index at <common-git-dir>/prov/index.db (docs/03 §4). Safe to delete."""

from __future__ import annotations

import json
import sqlite3

from prov.config import Config
from prov.gitio import Repo
from prov.identity.hunks import hunk_hash, parse_diff
from prov.identity.trailers import parse_trailers
from prov.models import Env, ProvRecord
from prov.store.records import ALL_REFS, decode_lines

SCHEMA = """
CREATE TABLE IF NOT EXISTS commits (
  sha TEXT PRIMARY KEY, tree TEXT NOT NULL, parent TEXT,
  author TEXT, author_email TEXT, author_date INTEGER,
  subject TEXT, body TEXT, patch_id TEXT, change_id TEXT, picked_from TEXT, files TEXT);
CREATE INDEX IF NOT EXISTS commits_patch_id ON commits(patch_id);
CREATE INDEX IF NOT EXISTS commits_change_id ON commits(change_id);
CREATE INDEX IF NOT EXISTS commits_picked_from ON commits(picked_from);
CREATE TABLE IF NOT EXISTS branch_commits (branch TEXT, sha TEXT, PRIMARY KEY(branch, sha));
CREATE INDEX IF NOT EXISTS branch_commits_sha ON branch_commits(sha);
CREATE TABLE IF NOT EXISTS hunks (sha TEXT, path TEXT, hhash TEXT, func TEXT);
CREATE INDEX IF NOT EXISTS hunks_hhash ON hunks(hhash);
CREATE INDEX IF NOT EXISTS hunks_sha ON hunks(sha);
CREATE TABLE IF NOT EXISTS ast (sha TEXT, path TEXT, func TEXT, ahash TEXT);
CREATE INDEX IF NOT EXISTS ast_ahash ON ast(ahash);
CREATE TABLE IF NOT EXISTS records (
  id TEXT PRIMARY KEY, sha TEXT, tree TEXT, patch_id TEXT, change_id TEXT,
  kind TEXT, env TEXT, result TEXT, at TEXT, json TEXT);
CREATE INDEX IF NOT EXISTS records_sha ON records(sha);
CREATE INDEX IF NOT EXISTS records_tree ON records(tree);
CREATE INDEX IF NOT EXISTS records_patch_id ON records(patch_id);
CREATE TABLE IF NOT EXISTS changes (sha TEXT PRIMARY KEY, json TEXT);  -- -U0 hunks for L3
CREATE TABLE IF NOT EXISTS ast_done (sha TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS llm_cache (key TEXT PRIMARY KEY, model TEXT, response TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

LOG_FMT = "%H%x00%T%x00%P%x00%an%x00%ae%x00%at%x00%s%x00%B%x1e"


class Index:
    def __init__(self, repo: Repo, cfg: Config):
        self.repo, self.cfg = repo, cfg
        repo.prov_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(repo.prov_dir / "index.db", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._branches: list[tuple[str, str, Env]] | None = None

    # ---- branches ----
    def branches(self) -> list[tuple[str, str, Env | None]]:
        """[(ref to read, display name, env)]. Local branch wins over <remote>/<same name>."""
        if self._branches is None:
            out = self.repo.run(["for-each-ref", "--format=%(refname)", "refs/heads", f"refs/remotes/{self.cfg.remote}"])
            found: dict[str, tuple[str, str, Env]] = {}
            for ref in out.splitlines():
                if ref.startswith("refs/heads/"):
                    name, local = ref[len("refs/heads/") :], True
                else:
                    name, local = ref[len(f"refs/remotes/{self.cfg.remote}/") :], False
                env = self.cfg.env_for(name)
                if env is None or name == "HEAD" or (name in found and not local):
                    continue
                display = name if local else f"{self.cfg.remote}/{name}"
                found[name] = (ref, display, env)
            for ref in self.repo.run(["for-each-ref", "--format=%(refname)", *self.cfg.extra_refs]).splitlines() \
                    if self.cfg.extra_refs else []:
                short = ref.removeprefix("refs/tags/").removeprefix("refs/heads/").removeprefix("refs/remotes/")
                if short not in found and f"{self.cfg.remote}/{short}" not in {b[1] for b in found.values()}:
                    found[short] = (ref, short, None)  # indexed for identity, carries no environment
            self._branches = list(found.values())
        return self._branches

    def env_of(self, branch: str) -> Env | None:
        return self.cfg.env_for(branch.removeprefix(f"{self.cfg.remote}/"))

    # ---- refresh ----
    def refresh(self) -> None:
        for ref, name, _ in self.branches():
            self._index_branch(ref, name)
        self._refresh_records()
        self.db.commit()

    def _meta(self, k: str) -> str | None:
        row = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return row[0] if row else None

    def _set_meta(self, k: str, v: str | None) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (k, v))

    def _index_branch(self, ref: str, name: str) -> None:
        tip = self.repo.rev_parse(ref)
        last = self._meta(f"tip:{name}")
        if tip is None or tip == last:
            return
        if last and self.repo.ok(["merge-base", "--is-ancestor", last, tip]):
            rng = [f"{last}..{tip}"]
        else:  # first build, or branch was rewritten
            self.db.execute("DELETE FROM branch_commits WHERE branch=?", (name,))
            rng = [tip, f"--since={self.cfg.window_days} days ago"]
        shas = self.repo.run(["rev-list", "--no-merges", *rng]).split()
        self.index_commits(shas)
        self.db.executemany("INSERT OR IGNORE INTO branch_commits VALUES (?, ?)", [(name, s) for s in shas])
        self._set_meta(f"tip:{name}", tip)

    def index_commits(self, shas: list[str]) -> None:
        known = {r[0] for r in self.db.execute(f"SELECT sha FROM commits WHERE sha IN ({','.join('?' * len(shas))})", shas)} if shas else set()
        new = [s for s in shas if s not in known]
        for i in range(0, len(new), 2000):
            self._index_batch(new[i : i + 2000])
        if new:  # trailer targets outside the walked range (e.g. a merge picked with -m 1, which rev-list skips)
            q = f"SELECT DISTINCT picked_from FROM commits WHERE sha IN ({','.join('?' * len(new))}) AND picked_from IS NOT NULL"
            self.index_commits([r[0] for r in self.db.execute(q, new)])

    def _index_batch(self, shas: list[str]) -> None:
        stdin = "\n".join(shas) + "\n"
        log = self.repo.run(["log", "--no-walk=unsorted", "--stdin", f"--format={LOG_FMT}"], input=stdin)
        diffs = self.repo.run(["diff-tree", "-p", "-m", "--first-parent", "--root", "--stdin"], input=stdin)
        pids = {}
        for line in self.repo.run(["patch-id", "--stable"], input=diffs).splitlines():
            pid, sha = line.split()
            pids[sha] = pid
        u0 = parse_diff(self.repo.run(["diff-tree", "-p", "-m", "--first-parent", "-U0", "-M", "--no-color", "--root",
                                       "--stdin"], input=stdin))
        rows, picks = [], {}
        for rec in log.split("\x1e"):
            rec = rec.lstrip("\n")
            if not rec:
                continue
            sha, tree, parents, an, ae, at, subj, body = rec.split("\x00")
            picked_raw, change_id = parse_trailers(body)
            if picked_raw:
                picks[sha] = picked_raw
            parent = parents.split()[0] if parents else None
            hs = u0.get(sha, [])
            files = sorted({h["path"] for h in hs})
            rows.append([sha, tree, parent, an, ae, int(at), subj, body, pids.get(sha), change_id, None, json.dumps(files)])
            self.db.execute("DELETE FROM hunks WHERE sha=?", (sha,))
            self.db.executemany("INSERT INTO hunks VALUES (?,?,?,?)",
                                [(sha, h["path"], hh, h["func"]) for h in hs if (hh := hunk_hash(h["minus"], h["plus"]))])
            self.db.execute("INSERT OR REPLACE INTO changes VALUES (?,?)", (sha, json.dumps(hs)))
        resolved = self.repo.resolve_commits(sorted(set(picks.values())))  # drop dangling references
        for row in rows:
            row[10] = resolved.get(picks.get(row[0], ""))
        self.db.executemany("INSERT OR REPLACE INTO commits VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    def _refresh_records(self) -> None:
        tips = {ref: self.repo.rev_parse(f"refs/notes/{ref}") for ref in ALL_REFS if ref != "prov/resolutions"}
        foreign = {ref: self.repo.rev_parse(f"refs/notes/{ref}") for ref in self.cfg.read_refs}
        state = {**tips, **{f"foreign:{r}": t for r, t in foreign.items()}}
        if all(self._meta(f"notes:{r}") == t for r, t in state.items()):
            return
        self.db.execute("DELETE FROM records")
        recs = []
        for ref, tip in tips.items():
            pairs = self.repo.notes_list(ref) if tip else []
            for data in self.repo.cat_file_batch(sorted({b for b, _ in pairs})).values():
                recs += decode_lines(data.decode("utf-8", "replace"))
        for ref, tip in foreign.items():
            if tip:
                from prov.store.importer import foreign_records

                recs += list(foreign_records(self.repo, ref))
        for r in recs:
            self.db.execute(
                "INSERT OR IGNORE INTO records VALUES (?,?,?,?,?,?,?,?,?,?)",
                (r.id, r.subject.commit, r.subject.tree, r.subject.patch_id, r.subject.change_id,
                 r.kind.value, r.env.value, r.result.value, r.at.isoformat(), r.model_dump_json()),
            )
        # tested commits that no branch holds any more (rebased, force-pushed): index them so L0/L1 can reach them
        self.index_commits(sorted(set(self.repo.resolve_commits(sorted({r.subject.commit for r in recs})).values())))
        for r, t in state.items():
            self._set_meta(f"notes:{r}", t)

    # ---- lookups ----
    def commit(self, sha: str) -> sqlite3.Row | None:
        row = self.db.execute("SELECT * FROM commits WHERE sha=?", (sha,)).fetchone()
        if row is None and self.repo.rev_parse(f"{sha}^{{commit}}"):
            self.index_commits([sha])
            self.db.commit()
            row = self.db.execute("SELECT * FROM commits WHERE sha=?", (sha,)).fetchone()
        return row

    def branches_of(self, sha: str) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT branch FROM branch_commits WHERE sha=? ORDER BY branch", (sha,))]

    def _shas(self, col: str, value: str | None) -> list[str]:
        if not value:
            return []
        return [r[0] for r in self.db.execute(f"SELECT sha FROM commits WHERE {col}=?", (value,))]

    def by_picked_from(self, sha: str) -> list[str]:
        return self._shas("picked_from", sha)

    def by_change_id(self, cid: str | None) -> list[str]:
        return self._shas("change_id", cid)

    def by_patch_id(self, pid: str | None) -> list[str]:
        return self._shas("patch_id", pid)

    def by_tree(self, tree: str | None) -> list[str]:
        return self._shas("tree", tree)

    def records(self, *, sha: str | None = None, tree: str | None = None, patch_id: str | None = None) -> list[ProvRecord]:
        col, val = next(((c, v) for c, v in (("sha", sha), ("tree", tree), ("patch_id", patch_id)) if v), (None, None))
        if col is None:  # e.g. the patch-id of an empty commit
            return []
        return [ProvRecord.model_validate_json(r[0]) for r in self.db.execute(f"SELECT json FROM records WHERE {col}=?", (val,))]

    def deploys(self, env: str) -> list[ProvRecord]:
        """Passing deploys to `env`, oldest first by `at` (never by position in the note)."""
        rows = self.db.execute("SELECT json FROM records WHERE kind='deploy' AND env=? AND result='PASSED'", (env,))
        return sorted((ProvRecord.model_validate_json(r[0]) for r in rows), key=lambda r: r.at)

    # ---- L2/L3 support ----
    def hunk_hashes(self, sha: str) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT hhash FROM hunks WHERE sha=?", (sha,))}

    def shas_sharing_hunks(self, hashes: set[str]) -> list[tuple[str, int]]:
        hs = list(hashes)
        q = f"SELECT sha, COUNT(DISTINCT hhash) FROM hunks WHERE hhash IN ({','.join('?' * len(hs))}) GROUP BY sha"
        return [(r[0], r[1]) for r in self.db.execute(q, hs)]

    def changes(self, sha: str) -> list[dict]:
        row = self.db.execute("SELECT json FROM changes WHERE sha=?", (sha,)).fetchone()
        return json.loads(row[0]) if row else []

    def funcs(self, sha: str) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT DISTINCT func FROM hunks WHERE sha=? AND func IS NOT NULL", (sha,))}

    def files(self, sha: str) -> list[str]:
        row = self.commit(sha)
        return json.loads(row["files"]) if row and row["files"] else []

    def candidates(self, x: str, branch_ref: str, branch: str) -> set[str]:
        """docs/04 §Candidate set: commits on `branch` near X in time, minus X's own history."""
        xrow = self.commit(x)
        if xrow is None:
            return set()
        mb = self.repo.run(["merge-base", x, branch_ref], check=False).strip()
        mb_row = self.commit(mb) if mb else None
        cutoff = min(xrow["author_date"], mb_row["author_date"] if mb_row else xrow["author_date"]) - 30 * 86400
        rows = self.db.execute(
            "SELECT b.sha FROM branch_commits b JOIN commits c ON c.sha=b.sha WHERE b.branch=? AND c.author_date>=?",
            (branch, cutoff))
        cands = {r[0] for r in rows}
        if cands:  # X's ancestors in the same time window are history, not equivalents
            own = set(self.repo.run(["rev-list", f"--since={cutoff}", x]).split())
            cands -= own
        cands.discard(x)
        return cands

    def ast_hashes(self, sha: str) -> set[str]:
        """Lazily computed AST hashes of the functions/classes `sha` changed (L3)."""
        if self.db.execute("SELECT 1 FROM ast_done WHERE sha=?", (sha,)).fetchone() is None:
            from prov.identity.fuzzy import compute_ast

            rows = compute_ast(self.repo, sha, self.changes(sha))
            if self.db.execute("INSERT OR IGNORE INTO ast_done VALUES (?)", (sha,)).rowcount:  # racing writer may win
                self.db.executemany("INSERT INTO ast VALUES (?,?,?,?)", [(sha, p, f, h) for p, f, h in rows])
            self.db.commit()
        return {r[0] for r in self.db.execute("SELECT ahash FROM ast WHERE sha=?", (sha,))}
