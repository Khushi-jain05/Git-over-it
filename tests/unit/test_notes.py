from prov.store.notes import record, records_for


def test_record_read_back_and_duplicate_is_noop(repo):
    sha = repo.commit({"a.txt": "1\n"}, "init")
    rec, attached = record(repo, sha, kind="test", env="uat", result="PASSED", suite="pytest")
    assert len(attached) == 2  # commit + tree
    record(repo, sha, kind="test", env="uat", result="PASSED", suite="pytest", at=rec.at)
    assert repo.notes_show("prov/tests", sha).count("\n") == 1
    assert [r.id for r in records_for(repo, sha)] == [rec.id]


def test_tree_note_survives_message_edit(repo):
    sha = repo.commit({"a.txt": "1\n"}, "init")
    record(repo, sha, kind="test", env="dev", result="PASSED")
    repo.run(["commit", "-q", "--amend", "-m", "reworded"])  # same tree, new sha, no notes rewrite configured
    assert len(records_for(repo, repo.rev_parse("HEAD"))) == 1
