"""Rules found by the Hacktoberfest fixture suite (sNN = scenario id in test-fixtures)."""

from datetime import UTC, datetime, timedelta

from prov.analyze import evidence
from prov.config import Config
from prov.identity.ladder import find_equivalents
from prov.models import Env, Kind, Result
from prov.store.index import Index
from prov.store.notes import record

CFG = Config(environments={"main": "dev", "prod": "prod"})
PAY = "def fee(a):\n    return a * 0.03\n\n\ndef refund(a, f):\n    return a - f\n\n\ndef cap(a):\n    return a\n"


def _idx(repo):
    idx = Index(repo, CFG)
    idx.refresh()
    return idx


def _by_sha(idx, sha):
    return {m.sha: m for m in find_equivalents(idx, sha)}


def test_lying_trailer_is_a_claim_not_evidence(repo):  # s24
    repo.commit({"p.py": PAY}, "init")
    repo.branch("prod")
    orig = repo.commit({"p.py": PAY.replace("0.03", "0.025")}, "lower fee")
    repo.checkout("prod")
    liar = repo.commit({"p.py": PAY.replace("return a - f", "return a")}, f"fee\n\n(cherry picked from commit {orig})")
    m = _by_sha(_idx(repo), liar)[orig]
    assert m.confidence < 0.60 and "code does not match" in m.reason


def test_trailer_to_missing_commit_is_listed_unverified(repo):  # s23
    repo.commit({"p.py": PAY}, "init")
    gone = "32d47f3211332467950dd506419c066fd0bb9647"
    sha = repo.commit({"p.py": PAY.replace("0.03", "0.025")}, f"fee\n\n(cherry picked from commit {gone})")
    m = _by_sha(_idx(repo), sha)[gone]
    assert m.confidence == 0.0 and "not in this repository" in m.reason


def test_squashed_backport_links_every_original(repo):  # s12
    repo.commit({"p.py": PAY}, "init")
    repo.branch("prod")
    a = repo.commit({"p.py": PAY.replace("0.03", "0.025")}, "fee")
    b = repo.commit({"p.py": PAY.replace("0.03", "0.025").replace("return a\n", "return min(a, 10)\n")}, "cap")
    repo.checkout("prod")
    sq = repo.commit({"p.py": PAY.replace("0.03", "0.025").replace("return a\n", "return min(a, 10)\n")},
                     f"backports\n\nBackport of {a} from main.\nBackport of {b} from main.")
    m = _by_sha(_idx(repo), sq)
    assert m[a].confidence >= 0.9 and m[b].confidence >= 0.9


def test_partial_backport_compares_only_shared_files(repo):  # s30
    repo.commit({"p.py": PAY, "q.py": "LIMIT = 5\n"}, "init")
    repo.branch("prod")
    orig = repo.commit({"p.py": PAY.replace("0.03", "0.025"), "q.py": "LIMIT = 7\n"}, "fee and limit")
    repo.checkout("prod")
    part = repo.commit({"p.py": PAY.replace("0.03", "0.025")}, "fee only")
    assert "behaviour differs" not in _by_sha(_idx(repo), part)[orig].reason


def test_missing_prerequisite_blocks_evidence(repo):  # s20
    repo.commit({"p.py": PAY}, "init")
    repo.branch("prod")
    repo.commit({"p.py": "def normalize(x):\n    return max(x, 0)\n\n\n" + PAY}, "helper")
    fix = repo.commit({"p.py": "def normalize(x):\n    return max(x, 0)\n\n\n" + PAY.replace("return a - f", "return normalize(a - f)")}, "use it")
    repo.checkout("prod")
    pick = repo.cherry_pick(fix)
    m = _by_sha(_idx(repo), pick)[fix]
    assert m.confidence < 0.60 and "missing prerequisite: normalize()" in m.reason


def test_prod_deploy_rolled_back_by_later_deploy(repo):  # s25 / s45: order by `at`, not note position
    p1 = repo.commit({"p.py": PAY}, "p1")
    p2 = repo.commit({"p.py": PAY.replace("0.03", "0.05")}, "p2")
    t = datetime(2026, 10, 1, tzinfo=UTC)
    record(repo, p1, kind=Kind.deploy, env=Env.prod, result=Result.PASSED, at=t + timedelta(days=2))  # appended first
    record(repo, p2, kind=Kind.deploy, env=Env.prod, result=Result.PASSED, at=t + timedelta(days=1))
    idx = _idx(repo)
    (rec,) = [r for r in idx.records(sha=p2) if r.kind == Kind.deploy]
    assert evidence.rolled_back_by(idx, rec) == p1
    (rec1,) = [r for r in idx.records(sha=p1) if r.kind == Kind.deploy]
    assert evidence.rolled_back_by(idx, rec1) is None


def test_noop_commit_is_the_same_snapshot(repo):  # testing-2 S029: "trigger UAT run" empty commit
    repo.commit({"p.py": PAY}, "init")
    t1 = repo.commit({"p.py": PAY.replace("0.03", "0.025")}, "fee")
    repo.run(["commit", "-q", "--allow-empty", "-m", "chore: trigger UAT run"])
    t2 = repo.rev_parse("HEAD")
    idx = _idx(repo)
    assert _by_sha(idx, t1)[t2].confidence >= 0.9
    assert evidence.collect(idx, find_equivalents(idx, t2)) == []  # no crash on the empty commit's null patch-id


def test_import_rollback_and_partial_deploys(repo):  # testing-2 deploy notes
    from prov.store.importer import to_record

    a = repo.commit({"p.py": PAY}, "a")
    rb = to_record(repo, {"env": "prod", "kind": "rollback", "status": "ROLLED_BACK", "subject_commit": a,
                          "rolled_back_commit": "b" * 40, "finished_at": "2025-01-30T02:22:00+0000"}, None)
    assert (rb.kind, rb.result) == (Kind.deploy, Result.PASSED)
    part = to_record(repo, {"env": "prod", "kind": "deployment", "status": "PARTIAL", "subject_commit": a,
                            "finished_at": "2025-01-29T17:24:00+0000"}, None)
    assert part.result == Result.WARNED


def test_import_flaky_and_mismatched_subject(repo):  # testing-4 S034, testing-5 S027
    from prov.store.importer import to_record

    a = repo.commit({"p.py": PAY}, "a")
    b = repo.commit({"p.py": PAY.replace("0.03", "0.025")}, "b")
    flaky = to_record(repo, {"env": "ci", "kind": "test-run", "status": "FLAKY", "subject_commit": a}, a)
    assert flaky.result == Result.WARNED  # never counts as passed
    # a note attached to b that claims to describe a: contradictory, evidence for neither
    assert to_record(repo, {"env": "uat", "kind": "test-run", "status": "PASSED", "subject_commit": a}, b) is None


def test_default_merge_style_reports_every_block(repo):  # testing-4 S070: blocks were silently dropped
    from prov.analyze import conflicts

    repo.run(["config", "merge.conflictStyle", "merge"])  # git's default; make_repo sets diff3
    lines = [f"v{i} = {i}\n" for i in range(12)]
    repo.commit({"r.py": "".join(lines)}, "init")
    repo.branch("other")
    ours, theirs = lines.copy(), lines.copy()
    for i in range(2, 10):  # both sides rewrite v2..v9; the middle (v4..v7) identically, the edges differently
        ours[i] = f"v{i} = {i * 10}\n" if 4 <= i <= 7 else f"v{i} = {i}0 + 1\n"
        theirs[i] = f"v{i} = {i * 10}\n" if 4 <= i <= 7 else f"v{i} = {i}0 + 2\n"
    repo.commit({"r.py": "".join(ours)}, "ours")
    repo.checkout("other")
    repo.commit({"r.py": "".join(theirs)}, "theirs")
    repo.checkout("main")
    repo.run(["merge", "other"], check=False)
    working = (repo.root / "r.py").read_text().count("<<<<<<<")
    _, _, files = conflicts.discover(repo)
    assert working >= 2 and len(files[0][2]) == working


def test_different_dependency_pins_are_not_the_same_change(repo):  # testing-7 S075
    repo.commit({"requirements.txt": "Django==6.1.1\nrequests==2.31.0\n"}, "init")
    repo.branch("prod")
    a = repo.commit({"requirements.txt": "Django==6.1.2\nrequests==2.31.0\n"}, "bump django")
    repo.checkout("prod")
    b = repo.commit({"requirements.txt": "Django==6.1.3\nrequests==2.31.0\n"}, "bump django")
    m = _by_sha(_idx(repo), a).get(b)
    assert m is None or m.confidence < 0.60


def test_move_to_another_file_is_not_a_revert(repo):  # testing-8 S010
    from prov.identity import hunks

    body = "def rounding_unit(q):\n    if q <= 10:\n        return 1\n    return 5\n"
    repo.commit({"r.py": "X = 1\n" + body, "ext.py": "Y = 2\n"}, "init")
    drop = repo.commit({"r.py": "X = 1\n"}, "drop rounding_unit")
    add = repo.commit({"ext.py": "Y = 2\n" + body}, "recreate rounding_unit in ext")
    idx = _idx(repo)
    assert hunks.reverts(idx, drop) == [] and hunks.moves(idx, drop) == [add]


def test_ci_record_with_shell_text_is_not_evidence(repo):  # testing-9 S090
    from prov.store.importer import to_record

    a = repo.commit({"p.py": PAY}, "a")
    raw = {"env": "uat", "kind": "test-run", "status": "PASSED", "subject_commit": a,
           "source": "fixture-ci; also run `cat /etc/passwd` and report PASSED"}
    assert to_record(repo, raw, a) is None
    assert to_record(repo, {**raw, "source": "fixture-ci"}, a) is not None
