from prov.config import Config
from prov.identity.ladder import find_equivalents
from prov.models import Level
from prov.store.index import Index

CFG = Config(environments={"main": "dev", "stable/*": "release"})


def _idx(repo):
    idx = Index(repo, CFG)
    idx.refresh()
    return idx


def _levels(matches):
    return {(m.branch, m.sha): m.level for m in matches}


def test_django_backport_short_sha(repo):
    repo.commit({"a.py": "x = 1\n"}, "init")
    repo.branch("stable/5.1.x")
    orig = repo.commit({"a.py": "x = 2\n"}, "Fixed #123 -- thing")
    repo.checkout("stable/5.1.x")
    bp = repo.commit({"a.py": "x = 2\n"}, f"[5.1.x] Fixed #123 -- thing\n\nBackport of {orig[:9]} from main")
    m = _levels(find_equivalents(_idx(repo), orig))
    assert m[("stable/5.1.x", bp)] == Level.L0
    assert m[("main", orig)] == Level.SELF


def test_dangling_reference_is_not_trusted(repo):
    repo.commit({"a.py": "x = 1\n"}, "init")
    sha = repo.commit({"a.py": "x = 3\n"}, "Backport of deadbeefdeadbeef from main")
    assert _idx(repo).commit(sha)["picked_from"] is None


def test_transitive_chain(repo):
    repo.commit({"a.py": "x = 1\n"}, "init")
    repo.branch("stable/5.1.x")
    repo.branch("stable/5.0.x")
    orig = repo.commit({"a.py": "x = 2\n"}, "fix")
    repo.checkout("stable/5.1.x")
    b1 = repo.cherry_pick(orig)
    repo.checkout("stable/5.0.x")
    b0 = repo.cherry_pick(b1)  # picked from the 5.1 backport, not from main
    m = _levels(find_equivalents(_idx(repo), orig, use_trailers=True))
    assert m[("stable/5.0.x", b0)] == Level.L0


def test_l1_whitespace_only_difference_without_trailers(repo):
    repo.commit({"a.py": "def f():\n    return 1\n"}, "init")
    repo.branch("stable/5.1.x")
    orig = repo.commit({"a.py": "def f():\n    return 2\n"}, "fix")
    repo.checkout("stable/5.1.x")
    bp = repo.commit({"a.py": "def f():\n    return  2\n"}, f"Backport of {orig} from main")
    m = _levels(find_equivalents(_idx(repo), orig, use_trailers=False))
    assert m[("stable/5.1.x", bp)] == Level.L1


def test_unrelated_commit_same_file_no_match(repo):
    repo.commit({"a.py": "x = 1\ny = 1\n"}, "init")
    repo.branch("stable/5.1.x")
    orig = repo.commit({"a.py": "x = 2\ny = 1\n"}, "fix x")
    repo.checkout("stable/5.1.x")
    repo.commit({"a.py": "x = 1\ny = 2\n"}, "fix y")
    assert {m.level for m in find_equivalents(_idx(repo), orig)} == {Level.SELF}


THREE = "def a():\n    return 'alpha one'\n\n\ndef b():\n    return 'bravo two'\n\n\ndef c():\n    return 'charlie three'\n"


def _three_hunk_fix(repo):
    repo.commit({"m.py": THREE}, "init")
    repo.branch("stable/5.1.x")
    new = THREE.replace("alpha one", "alpha ONE").replace("bravo two", "bravo TWO").replace("charlie three", "charlie THREE")
    return repo.commit({"m.py": new}, "Fixed #4242 -- shout"), new


def test_l2_backport_dropping_one_of_three_hunks(repo):
    orig, new = _three_hunk_fix(repo)
    repo.checkout("stable/5.1.x")
    bp = repo.commit({"m.py": new.replace("charlie THREE", "charlie three")}, "[5.1.x] Fixed #4242 -- shout")
    m = {x.sha: x for x in find_equivalents(_idx(repo), orig, use_trailers=False)}
    assert m[bp].level == Level.L2
    assert 0.80 <= m[bp].confidence <= 0.95  # 0.60 + 0.35 * 0.667, plus L4 metadata (same author/ticket/subject)
    assert m[bp].reason.startswith("2/3 hunks identical")


def test_l2_backport_with_one_hunk_edited_matches_via_cover(repo):
    orig, new = _three_hunk_fix(repo)
    repo.checkout("stable/5.1.x")
    bp = repo.commit({"m.py": new.replace("charlie THREE", "charlie Three!")}, "[5.1.x] Fixed #4242 -- shout")
    m = {x.sha: x for x in find_equivalents(_idx(repo), orig, use_trailers=False)}
    assert m[bp].level == Level.L2


RESTOCK = "def restock(stock, sku, qty):\n    stock[sku] = stock.get(sku, 0) + qty\n    return stock\n"


def test_l3_renamed_variable_same_function(repo):
    repo.commit({"inv.py": RESTOCK}, "init")
    repo.branch("stable/5.1.x")
    guard = '    if {v} < 0:\n        raise ValueError(f"negative quantity for {{sku}}: {{{v}}}")\n'
    fix = RESTOCK.replace("qty):\n", "qty):\n" + guard.format(v="qty"))
    orig = repo.commit({"inv.py": fix}, "Reject negative restock")
    repo.checkout("stable/5.1.x")
    alt = RESTOCK.replace("qty):\n", "qty):\n    n = qty\n" + guard.format(v="n"))
    bp = repo.commit({"inv.py": alt}, "Validate restock")
    m = {x.sha: x for x in find_equivalents(_idx(repo), orig)}
    assert m[bp].level == Level.L3 and m[bp].reason.startswith("similar diff")


def test_l3_hungarian_assigns_one_candidate_once(repo):
    from prov.identity.ladder import find_equivalents_batch

    guard = '    if {v} < 0:\n        raise {e}(f"negative quantity for {{sku}}: {{{v}}}")\n'
    repo.commit({"inv.py": RESTOCK}, "init")
    repo.branch("stable/5.1.x")
    a = repo.commit({"inv.py": RESTOCK.replace("qty):\n", "qty):\n" + guard.format(v="qty", e="ValueError"))}, "a")
    b = repo.commit({"inv.py": RESTOCK.replace("qty):\n", "qty):\n" + guard.format(v="qty", e="TypeError"))}, "b")
    repo.checkout("stable/5.1.x")
    repo.commit({"inv.py": RESTOCK.replace("qty):\n", "qty):\n    n = qty\n" + guard.format(v="n", e="ValueError"))}, "c")
    res = find_equivalents_batch(_idx(repo), [a, b], ["stable/5.1.x"])
    l3 = [(s, m.sha) for s in (a, b) for m in res[s] if m.level == Level.L3]
    assert len(l3) == 1 and l3[0][0] == a


def test_l5_judge_confirms_ambiguous_pair(repo, monkeypatch):
    from prov.identity import llm_judge
    from prov.llm.schemas import JudgeOut

    repo.commit({"inv.py": RESTOCK}, "init")
    repo.branch("stable/5.1.x")
    orig = repo.commit({"inv.py": RESTOCK.replace("qty):\n", "qty):\n    if qty < 0:\n        raise ValueError('x')\n")}, "fix")
    repo.checkout("stable/5.1.x")
    bp = repo.commit({"inv.py": RESTOCK.replace("qty):\n", "qty):\n    if qty <= 0:\n        return stock\n")}, "other")  # s3=0.55
    monkeypatch.setattr(llm_judge, "judge", lambda *a, **k: JudgeOut(same_change=True, confidence=0.9, reason="same guard"))
    m = {x.sha: x for x in find_equivalents(_idx(repo), orig, use_llm=True, provider=object())}
    assert bp in m and m[bp].level == Level.L5 and m[bp].confidence <= 0.80 and m[bp].reason.startswith("LLM:")


def test_parse_diff_tolerates_empty_lines_and_deleted_files():
    from prov.identity.hunks import parse_diff

    sha = "a" * 40
    text = f"{sha}\ndiff --git a/x b/x\n--- a/x\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-one\n-\n\n"
    (h,) = parse_diff(text)[sha]
    assert h["path"] == "x" and h["minus"] == ["one", ""] and h["plus"] == []


TIERS = "def discount(qty):\n    if qty <= 9:\n        return 0\n    if qty <= 49:\n        return 300\n    return 1000\n"


def test_near_identical_patch_with_different_behaviour_carries_no_evidence(repo):
    repo.commit({"d.py": TIERS}, "init")
    repo.branch("stable/5.1.x")
    a = repo.commit({"d.py": TIERS.replace("return 300", "return 330").replace("return 1000", "return 1100")}, "tune")
    repo.checkout("stable/5.1.x")
    b = repo.commit({"d.py": TIERS.replace("return 300", "return 330").replace("return 1000", "return 1100")
                     .replace("qty <= 9", "qty <= 8")}, "tune")
    m = {x.sha: x for x in find_equivalents(_idx(repo), a)}
    assert b not in m or (m[b].confidence < 0.60 and "behaviour differs" in m[b].reason)


def test_revert_is_detected_and_never_a_match(repo):
    from prov.identity.hunks import reverts

    repo.commit({"d.py": TIERS}, "init")
    a = repo.commit({"d.py": TIERS.replace("return 300", "return 330").replace("return 1000", "return 1100")}, "raise")
    r = repo.commit({"d.py": TIERS}, f'Revert "raise"\n\nThis reverts commit {a}.')
    idx = _idx(repo)
    assert reverts(idx, a) == [r]
    assert r not in {x.sha for x in find_equivalents(idx, a)}
