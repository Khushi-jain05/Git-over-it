from prov.analyze import conflicts

TWO = """a
<<<<<<< ours
o1
||||||| base
b1
=======
t1
>>>>>>> theirs
mid
<<<<<<< HEAD
o2
=======
t2
t2b
>>>>>>> abc (msg)
end
"""


def test_parse_two_blocks_with_and_without_base():
    b0, b1 = conflicts.parse_blocks(TWO)
    assert (b0.ours, b0.base, b0.theirs, b0.start_line, b0.end_line) == ("o1\n", "b1\n", "t1\n", 2, 8)
    assert (b1.ours, b1.base, b1.theirs) == ("o2\n", None, "t2\nt2b\n")
    assert b1.offsets == {"ours": 3, "base": 3, "theirs": 3}  # 0-based start of each side in its own blob


def test_equals_line_outside_block_is_text():
    assert conflicts.parse_blocks("=======\nx\n") == []


def test_modify_delete_classified(repo):
    repo.commit({"f.py": "x = 1\n", "keep": "k\n"}, "init")
    repo.branch("other")
    repo.commit({"f.py": None}, "delete")
    repo.checkout("other")
    edit = repo.commit({"f.py": "x = 2\n"}, "edit")
    repo.checkout("main")
    repo.cherry_pick(edit, check=False)
    op, theirs, files = conflicts.discover(repo)
    assert op == "cherry-pick" and theirs == edit
    assert files == [("f.py", "modify_delete", [])]


def test_two_hunk_cherry_pick(repo):
    base = "def a():\n    return 1\n\n\nx = 0\ny = 0\nz = 0\n\n\ndef b():\n    return 2\n"
    repo.commit({"m.py": base}, "init")
    repo.branch("other")
    repo.commit({"m.py": base.replace("return 1", "return 10").replace("return 2", "return 20")}, "ours")
    repo.checkout("other")
    t = repo.commit({"m.py": base.replace("return 1", "return 11").replace("return 2", "return 22")}, "theirs")
    repo.checkout("main")
    repo.cherry_pick(t, check=False)
    hunks = conflicts.current_hunks(repo, "m.py")
    assert [h.block.theirs for h in hunks] == ["    return 11\n", "    return 22\n"]
    assert [h.block.ours for h in hunks] == ["    return 10\n", "    return 20\n"]
