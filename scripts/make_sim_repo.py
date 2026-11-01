"""Build the simulated dev/uat/production repo (docs/10 §4).

    python scripts/make_sim_repo.py /tmp/sim [--scenario N] [--stop-at-conflict] [--no-notes]

Creates <path> (working repo), <path>-remote.git (bare remote) and <path>/.git/prov/expected.json.

| # | Scenario                      | Pick                          |
| 1 | Hotfix forward-port           | production fix F -> dev       |
| 2 | Promotion with edited hunk    | uat G' (edited copy of G) -> production |
| 3 | Independent same fix          | uat fix U -> dev (dev fixed it differently) |
| 4 | Rebased branch                | like 1, after dev was rebased |
| 5 | Fresh clone, no notes         | like 1, in a plain clone      |
| 6 | Failed test                   | like 1, latest uat test FAILED |
| 7 | Modify/delete                 | production deletes legacy.py -> dev edited it |
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prov import config  # noqa: E402
from prov.gitio import Repo, git  # noqa: E402
from prov.store.notes import record  # noqa: E402
from prov.store.sync import install, sync  # noqa: E402

MARK = ".prov-sim"  # only directories carrying this marker are ever deleted

BILLING = '''"""Billing helpers."""


def total(items):
    s = 0
    for price, qty in items:
        s += price * qty
    return s


def describe(items):
    """Human summary of a basket."""
    return f"{len(items)} items"


def tax(amount):
    return amount * 0.18
'''

BILLING_FIX = BILLING.replace("        s += price * qty\n", "        s += round(price * qty, 2)\n").replace(
    "    return amount * 0.18\n", "    return round(amount * 0.18, 2)\n")

BILLING_REFACTOR = BILLING.replace(
    "def total(items):\n    s = 0\n    for price, qty in items:\n        s += price * qty\n",
    "def total(items, rate=0.0):\n    s = 0\n    for price, qty in items:\n        s += price * qty * (1 + rate)\n",
).replace("def tax(amount):\n    return amount * 0.18\n", "def tax(amount, rate=0.18):\n    return amount * rate\n")

INVENTORY = '''"""Stock keeping."""


def add_item(stock, sku, qty):
    stock[sku] = stock.get(sku, 0) + qty
    return stock


def remove_item(stock, sku, qty):
    stock[sku] = stock.get(sku, 0) - qty
    return stock


def count(stock):
    return sum(stock.values())


def restock(stock, sku, qty):
    stock[sku] = stock.get(sku, 0) + qty
    return stock
'''

INV_G = (INVENTORY.replace("stock.get(sku, 0) + qty\n    return stock\n\n\ndef remove",
                           "stock.get(sku, 0) + int(qty)\n    return stock\n\n\ndef remove")
         .replace("stock.get(sku, 0) - qty", "stock.get(sku, 0) - int(qty)")
         .replace("return sum(stock.values())", "return int(sum(stock.values()))"))
INV_G_EDITED = INV_G.replace("return int(sum(stock.values()))", "return sum(int(v) for v in stock.values())")
INV_HOTFIX = INVENTORY.replace("stock.get(sku, 0) + qty\n    return stock\n\n\ndef remove",
                               "stock.get(sku, 0) + abs(qty)\n    return stock\n\n\ndef remove")
GUARD = '    if {v} < 0:\n        raise ValueError(f"negative quantity for {{sku}}: {{{v}}}")\n'
INV_FIX_UAT = INVENTORY.replace("def restock(stock, sku, qty):\n", "def restock(stock, sku, qty):\n" + GUARD.format(v="qty"))
INV_FIX_DEV = INVENTORY.replace("def restock(stock, sku, qty):\n",
                                "def restock(stock, sku, qty):\n    n = qty\n" + GUARD.format(v="n"))  # renamed variable

LEGACY = '''"""Old export format, kept for one customer."""


def export_csv(rows):
    return "\\n".join(",".join(map(str, r)) for r in rows)
'''

TESTS = {
    "tests/test_billing.py": "from shop.billing import total\n\n\ndef test_total():\n    assert total([(1.0, 2)]) == 2.0\n",
    "tests/test_inventory.py": "from shop.inventory import add_item\n\n\ndef test_add():\n    assert add_item({}, 'a', 2) == {'a': 2}\n",
}

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
PEOPLE = {"Arjun": "arjun@example.com", "Riya": "riya@example.com", "Sim": "sim@example.com"}
CI = "https://ci.example/run/"


def fresh_dir(p: Path) -> None:
    if p.exists():
        if not (p / MARK).exists() and not (p / ".git" / MARK).exists():
            sys.exit(f"{p} exists and was not made by this script; refusing to delete it")
        shutil.rmtree(p)


def write(root: Path, files: dict[str, str | None]) -> None:
    for rel, text in files.items():
        p = root / rel
        if text is None:
            p.unlink()
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def commit(r: Repo, files: dict[str, str | None], msg: str, author: str = "Sim", day: int = 0) -> str:
    write(r.root, files)
    r.run(["add", "-A"])
    r.run(["-c", f"user.name={author}", "-c", f"user.email={PEOPLE[author]}", "commit", "-q", "-m", msg,
           f"--date={(T0 + timedelta(days=day)).isoformat()}"])
    return r.rev_parse("HEAD")


def pick(r: Repo, sha: str, author: str = "Arjun") -> str:
    r.run(["-c", f"user.name={author}", "-c", f"user.email={PEOPLE[author]}", "cherry-pick", "-x", sha])
    return r.rev_parse("HEAD")


def ci(r: Repo, sha: str, env: str, kind: str = "test", result: str = "PASSED", day: float = 0, run: int = 700) -> None:
    record(r, sha, kind=kind, env=env, result=result, suite="pytest" if kind == "test" else "deploy",
           url=f"{CI}{run}", runner="github-actions", at=T0 + timedelta(days=day))


def setup(root: Path, remote: Path) -> Repo:
    fresh_dir(root)
    fresh_dir(remote)
    git(["init", "-q", "--bare", "-b", "dev", str(remote)], ".")
    (remote / MARK).touch()
    git(["init", "-q", "-b", "dev", str(root)], ".")
    (root / ".git" / MARK).touch()
    r = Repo(root)
    for k, v in [("user.name", "Sim"), ("user.email", PEOPLE["Sim"]), ("commit.gpgsign", "false")]:
        r.run(["config", k, v])
    r.run(["remote", "add", "origin", str(remote)])
    install(r)
    toml = config.render({"dev": "dev", "uat": "uat", "production": "prod"},
                         test_command=f"{sys.executable} -m pytest -q -x -p no:cacheprovider {{tests}}",
                         test_map='test_map = [{ src = "shop/*.py", tests = "tests/test_{stem}.py" }]')
    commit(r, {"shop/__init__.py": "", "shop/billing.py": BILLING, "shop/inventory.py": INVENTORY,
               "shop/legacy.py": LEGACY, ".prov.toml": toml, ".gitignore": "__pycache__/\n", **TESTS}, "Initial shop")
    r.run(["branch", "uat"])
    r.run(["branch", "production"])
    return r


def hotfix_vs_refactor(r: Repo, notes: bool, *, uat_failed: bool = False) -> dict[str, str]:
    """production fix F (+ uat copy F' tested, prod deploy) vs dev refactor R (dev test)."""
    r.run(["checkout", "-q", "production"])
    f = commit(r, {"shop/billing.py": BILLING_FIX}, "Fix rounding in totals", "Arjun", day=19)
    r.run(["checkout", "-q", "uat"])
    f_uat = pick(r, f)
    r.run(["checkout", "-q", "dev"])
    rr = commit(r, {"shop/billing.py": BILLING_REFACTOR}, "Refactor pricing helpers", "Riya", day=27)
    if notes:
        ci(r, f_uat, "uat", day=19.1, run=701)
        if uat_failed:
            ci(r, f_uat, "uat", result="FAILED", day=21, run=703)
        ci(r, f, "prod", kind="deploy", day=20, run=702)
        ci(r, rr, "dev", day=27.1, run=812)
    return {"fix": f, "fix_uat": f_uat, "refactor": rr, "pick": f, "onto": "dev"}


def scenario2(r: Repo, notes: bool) -> dict[str, str]:
    r.run(["checkout", "-q", "dev"])
    g = commit(r, {"shop/inventory.py": INV_G}, "Track inventory in whole units", "Riya", day=10)
    r.run(["checkout", "-q", "uat"])  # promoted by hand, one hunk changed while resolving: no trailer, new patch-id
    g2 = commit(r, {"shop/inventory.py": INV_G_EDITED}, "Track inventory in whole units", "Riya", day=12)
    r.run(["checkout", "-q", "production"])
    h = commit(r, {"shop/inventory.py": INV_HOTFIX}, "Ignore sign of restocked quantity", "Arjun", day=14)
    if notes:
        ci(r, g, "dev", day=10.1, run=601)
        ci(r, g2, "uat", day=12.1, run=602)
        ci(r, h, "prod", day=14.1, run=603)
        ci(r, h, "prod", kind="deploy", day=14.2, run=604)
    return {"feature": g, "feature_uat": g2, "hotfix": h, "pick": g2, "onto": "production"}


def scenario3(r: Repo, notes: bool) -> dict[str, str]:
    r.run(["checkout", "-q", "uat"])
    u = commit(r, {"shop/inventory.py": INV_FIX_UAT}, "Reject negative restock quantity", "Arjun", day=5)
    r.run(["checkout", "-q", "dev"])
    d = commit(r, {"shop/inventory.py": INV_FIX_DEV}, "Validate restock quantity", "Riya", day=6)
    if notes:
        ci(r, u, "uat", day=5.1, run=501)
        ci(r, d, "dev", day=6.1, run=502)
    return {"fix_uat": u, "fix_dev": d, "pick": u, "onto": "dev"}


def scenario4(r: Repo, notes: bool) -> dict[str, str]:
    shas = hotfix_vs_refactor(r, notes)
    r.run(["checkout", "-q", "dev"])
    # Rewrites dev's commits (new committer dates -> new shas, same trees); notes.rewriteRef copies commit notes.
    r.run(["rebase", "-q", "--force-rebase", "--committer-date-is-author-date", "HEAD~1"])
    shas["refactor_before_rebase"] = shas["refactor"]
    shas["refactor"] = r.rev_parse("HEAD")
    return shas


def scenario7(r: Repo, notes: bool) -> dict[str, str]:
    r.run(["checkout", "-q", "production"])
    rm = commit(r, {"shop/legacy.py": None}, "Drop legacy CSV export", "Arjun", day=8)
    r.run(["checkout", "-q", "dev"])
    ed = commit(r, {"shop/legacy.py": LEGACY.replace('",".join', '";".join')}, "Use semicolons in legacy export",
                "Riya", day=9)
    if notes:
        ci(r, rm, "prod", kind="deploy", day=8.1, run=401)
        ci(r, ed, "dev", day=9.1, run=402)
    return {"delete": rm, "edit": ed, "pick": rm, "onto": "dev"}


def expected(scenario: int) -> dict:
    s1 = {"theirs": ["PROD_VERIFIED", "recorded"], "ours": ["TESTED_ELSEWHERE", "recorded"]}
    two = ("shop/billing.py#0", "shop/billing.py#1")
    if scenario in (1, 4):
        return {"metadata_state": "recorded", "recommendation": "take_theirs", "hunks": dict.fromkeys(two, s1),
                "levels": {"theirs": [["production", "self"], ["uat", "L0_trailer"]]}}
    if scenario == 2:
        return {"metadata_state": "recorded", "recommendation": "take_ours",
                "hunks": {"shop/inventory.py#0": {"ours": ["PROD_VERIFIED", "recorded"],
                                                  "theirs": ["TESTED_ELSEWHERE", "recorded"]}},
                "levels": {"theirs": [["uat", "self"], ["dev", "L2_hunks"]]}}
    if scenario == 3:
        side = ["TESTED_ELSEWHERE", "recorded"]
        return {"metadata_state": "recorded", "recommendation": "combine",
                "hunks": {"shop/inventory.py#0": {"ours": side, "theirs": side}},
                "levels": {"theirs": [["uat", "self"], ["dev", "L3_fuzzy"]], "ours": [["dev", "self"], ["uat", "L3_fuzzy"]]}}
    if scenario == 5:
        side = {"theirs": ["UNVERIFIED", "inferred"], "ours": ["NO_METADATA", "none"]}
        return {"metadata_state": "none", "recommendation": "manual", "hunks": dict.fromkeys(two, side),
                "after_sync": s1}
    if scenario == 6:
        side = {"theirs": ["FAILED", "recorded"], "ours": ["TESTED_ELSEWHERE", "recorded"]}
        return {"metadata_state": "recorded", "recommendation": "take_ours", "hunks": dict.fromkeys(two, side)}
    return {"metadata_state": "recorded", "hunks": {}, "files": [["shop/legacy.py", "modify_delete"]]}


BUILDERS = {1: hotfix_vs_refactor, 2: scenario2, 3: scenario3, 4: scenario4,
            6: lambda r, n: hotfix_vs_refactor(r, n, uat_failed=True), 7: scenario7}


def build(root: Path, remote: Path, scenario: int, notes: bool) -> dict[str, str]:
    r = setup(root, remote)
    shas = BUILDERS[scenario](r, notes)
    r.run(["checkout", "-q", "dev"])
    r.run(["push", "-q", "--no-verify", "origin", "dev", "uat", "production"])
    if notes:
        sync(r, push=True, fetch=False)
    return shas


def main(argv: list[str] | None = None) -> Path:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", type=Path)
    ap.add_argument("--scenario", type=int, default=1, choices=range(1, 8))
    ap.add_argument("--stop-at-conflict", action="store_true", help="run the conflicting cherry-pick")
    ap.add_argument("--no-notes", action="store_true")
    a = ap.parse_args(argv)
    root = a.path.resolve()
    remote = root.parent / f"{root.name}-remote.git"

    if a.scenario == 5:  # build 1 elsewhere, then a plain clone (Git does not fetch notes by default)
        shas = build(root.parent / f"{root.name}-seed", remote, 1, notes=True)
        fresh_dir(root)
        git(["clone", "-q", "-b", "dev", str(remote), str(root)], ".")
        (root / ".git" / MARK).touch()
        r = Repo(root)
        for k, v in [("user.name", "Sim"), ("user.email", PEOPLE["Sim"]), ("merge.conflictStyle", "diff3")]:
            r.run(["config", k, v])
        for b in ("uat", "production"):
            r.run(["branch", "-q", "--track", b, f"origin/{b}"])
    else:
        shas = build(root, remote, a.scenario, notes=not a.no_notes)

    r = Repo(root)
    (r.git_dir / "prov").mkdir(exist_ok=True)
    (r.git_dir / "prov" / "expected.json").write_text(json.dumps({**expected(a.scenario), "shas": shas}, indent=1))
    cmd = f"git checkout {shas['onto']} && git cherry-pick -x {shas['pick']}"
    if a.stop_at_conflict:
        r.run(["checkout", "-q", shas["onto"]])
        r.run(["cherry-pick", "-x", shas["pick"]], check=False)
        print(f"{root}: scenario {a.scenario}, conflict in progress ({cmd})")
    else:
        print(f"{root}: scenario {a.scenario} ready. Next: cd {root} && {cmd}")
    return root


if __name__ == "__main__":
    main()
