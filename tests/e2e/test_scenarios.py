"""Integration heartbeat (docs/10 §4): build each sim scenario, assert against expected.json. Run before every merge."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import make_sim_repo  # noqa: E402

from tests.helpers.fake_llm import FakeLLM  # noqa: E402


def prov(cwd: Path, *args: str, ok: bool = True) -> dict:
    p = subprocess.run([sys.executable, "-m", "prov", *args, "--json"], cwd=cwd, capture_output=True, text=True)
    if ok:
        assert p.returncode == 0, p.stdout + p.stderr
    return json.loads(p.stdout)


def git(cwd: Path, *args: str, input: str | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, input=input).stdout


def badges(report: dict) -> dict:
    return {h["id"]: {s: [h[s]["badge"], h[s]["tier"]] for s in ("ours", "theirs")}
            for f in report["files"] for h in f["hunks"]}


def hunks(report: dict) -> list[dict]:
    return [h for f in report["files"] for h in f["hunks"]]


def build(tmp_path: Path, scenario: int) -> tuple[Path, dict]:
    root = make_sim_repo.main([str(tmp_path / "sim"), "--scenario", str(scenario), "--stop-at-conflict"])
    return root, json.loads((root / ".git" / "prov" / "expected.json").read_text())


@pytest.mark.parametrize("scenario", [1, 2, 3, 4, 5, 6, 7])
def test_scenario_matches_expected(tmp_path, scenario):
    root, exp = build(tmp_path, scenario)
    rep = prov(root, "analyze")
    assert rep["metadata_state"] == exp["metadata_state"]
    assert badges(rep) == exp["hunks"]
    if "recommendation" in exp:
        assert {h["recommendation"] for h in hunks(rep)} == {exp["recommendation"]}
    for side, levels in exp.get("levels", {}).items():
        got = {(m["branch"], m["level"]) for m in hunks(rep)[0][side]["matches"]}
        assert {tuple(x) for x in levels} <= got, got
    if "files" in exp:
        assert [[f["path"], f["kind"]] for f in rep["files"]] == exp["files"]
    # E4 safety: never a PROD_VERIFIED the scenario does not expect
    for hid, sides in badges(rep).items():
        for s, (badge, _) in sides.items():
            if badge == "PROD_VERIFIED":
                assert exp["hunks"][hid][s][0] == "PROD_VERIFIED"


def test_scenario_1_resolve_commit_with_tests(tmp_path):
    root, exp = build(tmp_path, 1)
    rep = prov(root, "analyze")
    hs = hunks(rep)
    assert hs[0]["theirs"]["origins"][0]["sha"] == exp["shas"]["fix"]
    assert hs[0]["base"]["origins"][0]["subject"] == "Initial shop"
    for h in reversed(hs):
        prov(root, "apply", "--hunk", h["id"], "--take", "theirs", "--hash", h["content_hash"])
    res = prov(root, "commit")
    assert res["gates"] == {"no_markers": True, "parse": True, "no_dup_defs": True, "tests": True}
    assert "Prov-Resolved: shop/billing.py theirs=PROD_VERIFIED hunks=2" in res["trailers"]
    msg = git(root, "log", "-1", "--format=%B")
    assert f"(cherry picked from commit {exp['shas']['fix']})" in msg
    parsed = git(root, "interpret-trailers", "--parse", input=msg)
    assert f"Prov-Change-Id: {res['change_id']}" in parsed and "Prov-Evidence: notes" in parsed
    note = git(root, "notes", "--ref=prov/resolutions", "show", "HEAD").splitlines()[0]
    assert json.loads(note)["decisions"][0]["theirs_badge"] == "PROD_VERIFIED"
    local = prov(root, "status", "HEAD")  # tests gate recorded a local test on the new commit itself
    assert any(e["record"] and e["record"]["env"] == "local" and e["via"]["level"] == "self" for e in local["evidence"])
    # in-toto export of the original fix
    stmts = prov(root, "export", exp["shas"]["fix"])["statements"]
    assert stmts and stmts[0]["_type"] == "https://in-toto.io/Statement/v1"


def test_same_change_id_on_every_backport(tmp_path):
    root, exp = build(tmp_path, 1)
    hs = hunks(prov(root, "analyze"))
    for h in reversed(hs):
        prov(root, "apply", "--hunk", h["id"], "--take", "theirs", "--hash", h["content_hash"])
    first = prov(root, "commit", "--no-tests")["change_id"]
    note = git(root, "notes", "--ref=prov/resolutions", "show", "HEAD")
    assert '"rerere"' in note  # resolution shared through notes (rr-cache itself is never pushed)
    import shutil

    shutil.rmtree(root / ".git" / "rr-cache")
    assert prov(root, "rerere-import")["imported"] >= 1
    git(root, "checkout", "-q", "-b", "release", exp["shas"]["refactor"])
    git(root, "cherry-pick", "-x", exp["shas"]["fix"])
    prov(root, "analyze")  # rerere (enabled by install) replays the first resolution: no markers left
    assert prov(root, "commit", "--no-tests")["change_id"] == first


def test_gate_failure_blocks_commit_and_override_marks_it(tmp_path):
    root, _ = build(tmp_path, 1)
    hs = hunks(prov(root, "analyze"))
    broken = tmp_path / "broken.py"
    broken.write_text("def total(items:\n")
    prov(root, "apply", "--hunk", hs[1]["id"], "--take", "theirs", "--hash", hs[1]["content_hash"])
    prov(root, "apply", "--hunk", hs[0]["id"], "--take", "text", "--hash", hs[0]["content_hash"],
         "--text-file", str(broken))
    err = prov(root, "commit", ok=False)["error"]
    assert err["exit"] == 5 and "shop/billing.py" in err["message"]
    res = prov(root, "commit", "--allow-failed-gates", "--no-tests")
    assert "Prov-Gates: failed" in res["trailers"]
    assert "Prov-Gates: failed" in git(root, "log", "-1", "--format=%B")


def test_stale_and_unresolved(tmp_path):
    root, _ = build(tmp_path, 1)
    h = hunks(prov(root, "analyze"))[0]
    assert prov(root, "apply", "--hunk", h["id"], "--take", "ours", "--hash", "0" * 40, ok=False)["error"]["exit"] == 6
    assert prov(root, "commit", ok=False)["error"]["exit"] == 4


def test_scenario_5_sync_restores_recorded(tmp_path):
    root, exp = build(tmp_path, 5)
    rep = prov(root, "analyze")
    assert "recorded" not in {h[s]["tier"] for h in hunks(rep) for s in ("ours", "theirs")}
    assert "reachable from production" in [e["note"] for e in hunks(rep)[0]["theirs"]["evidence"]]
    prov(root, "install")
    prov(root, "sync")
    rep = prov(root, "analyze")
    assert {tuple(h["theirs"]) for h in badges(rep).values()} == {tuple(exp["after_sync"]["theirs"])}


def _point_llm_at(root: Path, url: str) -> None:
    toml = root / ".prov.toml"
    text = toml.read_text().replace('base_url = "https://api.groq.com/openai/v1"', f'base_url = "{url}"')
    toml.write_text(text.replace('fallback_base_url = "http://localhost:11434/v1"', 'fallback_base_url = ""'))


def test_draft_gates_accept_and_record_llm(tmp_path):
    root, _ = build(tmp_path, 1)
    good = {"explanation_ours": "Adds a tax-rate argument.", "explanation_theirs": "Rounds each line item.",
            "strategy": "combine", "resolved_code": "        s += round(price * qty * (1 + rate), 2)\n",
            "risk_notes": ["rate is untested in production"], "untested_lines": [1]}
    bad = {**good, "resolved_code": "<<<<<<< HEAD\n        s +=\n"}
    fake = FakeLLM([(200, json.dumps(good)), (200, json.dumps(bad))])
    _point_llm_at(root, fake.url)
    hs = hunks(prov(root, "analyze"))
    d = prov(root, "draft", "--hunk", hs[0]["id"])
    assert d["gates_passed"] is True and d["strategy"] == "combine" and d["model"]
    assert "EVIDENCE" in fake.requests[0]["messages"][1]["content"]
    d2 = prov(root, "draft", "--hunk", hs[1]["id"])
    assert d2["gates_passed"] is False and d2["gate_errors"]
    assert prov(root, "apply", "--hunk", hs[1]["id"], "--take", "draft", "--hash", hs[1]["content_hash"],
                ok=False)["error"]["exit"] == 5
    prov(root, "apply", "--hunk", hs[0]["id"], "--take", "draft", "--hash", hs[0]["content_hash"])
    prov(root, "apply", "--hunk", hs[1]["id"], "--take", "theirs", "--hash", hs[1]["content_hash"])
    prov(root, "commit")
    rec = json.loads(git(root, "notes", "--ref=prov/resolutions", "show", "HEAD").splitlines()[0])
    drafted = [x for x in rec["decisions"] if x["choice"] == "draft"]
    assert drafted and drafted[0]["llm_model"] and drafted[0]["llm_prompt_hash"] == d["prompt_hash"]
    shown = prov(root, "llm-show", d["prompt_hash"][:12])
    assert shown["entries"][0]["user"].startswith("FILE: shop/billing.py")


def test_import_junit_failed(tmp_path):
    root, exp = build(tmp_path, 1)
    xml = tmp_path / "r.xml"
    xml.write_text('<testsuite><testcase classname="t" name="ok"/><testcase classname="t" name="bad">'
                   '<failure message="x"/></testcase></testsuite>')
    res = prov(root, "import-junit", str(xml), exp["shas"]["refactor"], "--env", "uat")
    assert res["result"] == "FAILED"
    assert prov(root, "status", exp["shas"]["refactor"])["badge"] == "FAILED"


def test_keeping_ours_everywhere_is_an_explicit_choice(tmp_path):
    root, exp = build(tmp_path, 1)
    for h in reversed(hunks(prov(root, "analyze"))):
        prov(root, "apply", "--hunk", h["id"], "--take", "ours", "--hash", h["content_hash"])
    err = prov(root, "commit", "--no-tests", ok=False)["error"]
    assert err["code"] == "empty_resolution" and err["exit"] == 3
    assert (root / ".git" / "CHERRY_PICK_HEAD").exists()  # nothing changed, operation still in progress
    res = prov(root, "commit", "--no-tests", "--empty", "commit")
    assert git(root, "diff", "HEAD~1", "HEAD") == "" and "Prov-Resolved: shop/billing.py ours=TESTED_ELSEWHERE hunks=2" in res["trailers"]
    assert f"(cherry picked from commit {exp['shas']['fix']})" in git(root, "log", "-1", "--format=%B")
    assert not (root / ".git" / "CHERRY_PICK_HEAD").exists()


def test_empty_resolution_skip(tmp_path):
    root, _ = build(tmp_path, 1)
    head = git(root, "rev-parse", "HEAD")
    for h in reversed(hunks(prov(root, "analyze"))):
        prov(root, "apply", "--hunk", h["id"], "--take", "ours", "--hash", h["content_hash"])
    assert prov(root, "commit", "--no-tests", "--empty", "skip")["skipped"] is True
    assert git(root, "rev-parse", "HEAD") == head and not (root / ".git" / "CHERRY_PICK_HEAD").exists()


def test_hunks_resolved_outside_git_prov_are_still_recorded(tmp_path):
    root, _ = build(tmp_path, 1)
    hs = hunks(prov(root, "analyze"))
    prov(root, "apply", "--hunk", hs[1]["id"], "--take", "theirs", "--hash", hs[1]["content_hash"])
    git(root, "checkout", "--theirs", "shop/billing.py")  # like the editor's own "Accept Incoming" button
    assert hunks(prov(root, "analyze")) == []  # nothing open any more; the extension re-analyzes on save
    res = prov(root, "commit", "--no-tests")
    assert "Prov-Resolved: shop/billing.py mixed hunks=2" in res["trailers"]
    rec = json.loads(git(root, "notes", "--ref=prov/resolutions", "show", "HEAD").splitlines()[0])
    assert sorted(d["choice"] for d in rec["decisions"]) == ["manual", "theirs"]
    assert {d["theirs_badge"] for d in rec["decisions"]} == {"PROD_VERIFIED"}
