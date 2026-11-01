from __future__ import annotations

from pathlib import Path

from prov.gitio import Repo, git


class TestRepo(Repo):
    __test__ = False

    def commit(self, files: dict[str, str | None], msg: str, author: str = "Dev") -> str:
        for rel, text in files.items():
            p = self.root / rel
            if text is None:
                p.unlink()
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text)
        self.run(["add", "-A"])
        self.run(["-c", f"user.name={author}", "commit", "-q", "-m", msg])
        return self.rev_parse("HEAD")

    def branch(self, name: str, start: str = "HEAD") -> None:
        self.run(["branch", name, start])

    def checkout(self, name: str) -> None:
        self.run(["checkout", "-q", name])

    def cherry_pick(self, sha: str, *, check: bool = True) -> str:
        self.run(["cherry-pick", "-x", sha], check=check)
        return self.rev_parse("HEAD")


def make_repo(path: Path, branch: str = "main") -> TestRepo:
    git(["init", "-q", "-b", branch, str(path)], ".")
    for k, v in [("user.name", "Dev"), ("user.email", "dev@example.com"), ("commit.gpgsign", "false"),
                 ("merge.conflictStyle", "diff3")]:
        git(["config", k, v], path)
    return TestRepo(path)
