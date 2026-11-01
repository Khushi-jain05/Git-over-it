import pytest

from prov.analyze import conflicts
from prov.errors import ProvError
from prov.resolve.apply import apply

BASE = "def a():\n    return 1\n\n\nx = 0\ny = 0\nz = 0\n\n\ndef b():\n    return 2\n"


@pytest.fixture
def two_hunks(repo):
    repo.commit({"m.py": BASE}, "init")
    repo.branch("other")
    repo.commit({"m.py": BASE.replace("return 1", "return 10").replace("return 2", "return 20")}, "ours")
    repo.checkout("other")
    t = repo.commit({"m.py": BASE.replace("return 1", "return 11").replace("return 2", "return 22")}, "theirs")
    repo.checkout("main")
    repo.cherry_pick(t, check=False)
    return repo


def test_apply_one_then_both(two_hunks):
    r = two_hunks
    h0, h1 = conflicts.current_hunks(r, "m.py")
    res = apply(r, "m.py#1", "theirs", expected_hash=h1.block.content_hash)
    assert res == {"hunk_id": "m.py#1", "applied": "theirs", "file_staged": False, "remaining_hunks_in_file": 1}
    text = (r.root / "m.py").read_text()
    assert "return 22" in text and "<<<<<<<" in text and "return 10" in text
    res = apply(r, "m.py#0", "ours", expected_hash=h0.block.content_hash)
    assert res["file_staged"] and not r.ls_files_unmerged()
    assert (r.root / "m.py").read_text() == BASE.replace("return 1", "return 10").replace("return 2", "return 22")


def test_stale_hash(two_hunks):
    with pytest.raises(ProvError) as e:
        apply(two_hunks, "m.py#0", "ours", expected_hash="0" * 40)
    assert e.value.exit == 6


def test_crlf_preserved(repo):
    base = "a = 1\r\nb = 2\r\n"
    repo.run(["config", "core.autocrlf", "false"])
    repo.commit({"c.py": base}, "init")
    repo.branch("other")
    repo.commit({"c.py": "a = 10\r\nb = 2\r\n"}, "ours")
    repo.checkout("other")
    t = repo.commit({"c.py": "a = 11\r\nb = 2\r\n"}, "theirs")
    repo.checkout("main")
    repo.cherry_pick(t, check=False)
    (h,) = conflicts.current_hunks(repo, "c.py")
    apply(repo, "c.py#0", "theirs", expected_hash=h.block.content_hash)
    assert (repo.root / "c.py").read_bytes() == b"a = 11\r\nb = 2\r\n"
