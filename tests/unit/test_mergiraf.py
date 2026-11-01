import shutil

import pytest

from prov.analyze.report import analyze
from prov.resolve.mergiraf import solve

pytestmark = pytest.mark.skipif(shutil.which("mergiraf") is None, reason="mergiraf not installed")

BASE = "import os\n\n\ndef a():\n    return 1\n"


def test_structural_conflict_auto_solves(repo):
    repo.commit({"m.py": BASE}, "init")
    repo.branch("other")
    repo.commit({"m.py": "import os\nimport re\n\n\ndef a():\n    return 1\n"}, "ours: add re")
    repo.checkout("other")
    t = repo.commit({"m.py": "import os\nimport sys\n\n\ndef a():\n    return 1\n"}, "theirs: add sys")
    repo.checkout("main")
    repo.cherry_pick(t, check=False)
    analyze(repo)
    res = solve(repo)
    assert res["files"]["m.py"]["solved"] == 1 and res["files"]["m.py"]["staged"]
    text = (repo.root / "m.py").read_text()
    assert "import re" in text and "import sys" in text and "<<<<<<<" not in text
    assert not list(repo.root.glob("*.orig"))
