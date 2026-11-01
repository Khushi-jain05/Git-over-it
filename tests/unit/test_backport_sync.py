from prov.resolve.backport import backport, preview
from prov.store.notes import record, records_for
from prov.store.sync import install, sync
from tests.helpers.repo import TestRepo, git, make_repo


def test_preview_and_worktree_backport(repo):
    repo.commit({"a.txt": "1\n2\n3\n"}, "init")
    repo.branch("old")
    repo.branch("older")
    fix = repo.commit({"a.txt": "1\nTWO\n3\n"}, "fix")
    repo.checkout("older")
    repo.commit({"a.txt": "1\ndeux\n3\n"}, "diverge")
    repo.checkout("main")
    p = preview(repo, fix, ["old", "older"])
    assert p["old"]["clean"] and not p["older"]["clean"] and p["older"]["conflicted_files"] == ["a.txt"]
    res = backport(repo, fix, ["old", "older"])
    assert res["targets"]["old"]["status"] == "clean"
    assert res["targets"]["older"]["status"] == "conflict" and res["targets"]["older"]["hunks"] == 1
    assert repo.rev_parse("HEAD") == fix  # user's checkout untouched


def test_two_clones_share_records_us4(tmp_path):
    remote = tmp_path / "remote.git"
    git(["init", "-q", "--bare", str(remote)], tmp_path)
    one = make_repo(tmp_path / "one")
    sha = one.commit({"a.txt": "1\n"}, "init")
    one.run(["remote", "add", "origin", str(remote)])
    one.run(["push", "-q", "origin", "main"])
    git(["clone", "-q", str(remote), str(tmp_path / "two")], tmp_path)
    two = TestRepo(tmp_path / "two")
    for r in (one, two):
        install(r)
    record(one, sha, kind="test", env="uat", result="PASSED")
    sync(one)
    record(two, sha, kind="deploy", env="prod", result="PASSED")  # concurrent write in clone 2
    sync(two)  # fetch + cat_sort_uniq merge + push
    sync(one)
    kinds = sorted(r.kind.value for r in records_for(one, sha))
    assert kinds == ["deploy", "test"] and sorted(r.kind.value for r in records_for(two, sha)) == kinds
