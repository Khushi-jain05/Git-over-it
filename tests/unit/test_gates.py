import sys

from prov.config import Config
from prov.resolve.gates import check_text, run_gates, select_tests


def test_static_gates():
    assert check_text("a.py", "x = 1\n") == []
    assert "conflict marker" in check_text("a.py", "<<<<<<< HEAD\nx\n")[0]
    assert "a.py:1" in check_text("a.py", "def (:\n")[0]
    assert check_text("a.js", "function f( {")[0].startswith("a.js:")
    dup = "def f():\n    pass\n\n\ndef f():\n    pass\n"
    assert "duplicate" in check_text("a.py", dup, ["def f():\n    pass\n"])[0]
    assert check_text("a.py", dup, [dup]) == []  # duplicates already present in a side are not ours


def test_tests_gate_uses_test_map(repo):
    repo.commit({"src/calc.py": "def add(a, b):\n    return a + b\n",
                 "tests/test_calc.py": "from src.calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
                 "src/__init__.py": "", "tests/test_other.py": "def test_boom():\n    assert False\n"}, "init")
    cfg = Config(test_command=f"{sys.executable} -m pytest -q -p no:cacheprovider {{tests}}",
                 test_map=[{"src": "src/*.py", "tests": "tests/test_{stem}.py"}])
    assert select_tests(repo, cfg, ["src/calc.py"]) == ["tests/test_calc.py"]
    gates, errors, cmd = run_gates(repo, cfg, ["src/calc.py"], side_refs=[])
    assert gates == {"no_markers": True, "parse": True, "no_dup_defs": True, "tests": True} and "test_calc" in cmd
    gates, errors, _ = run_gates(repo, cfg, ["src/__init__.py"], side_refs=[])  # unmapped: full suite, which fails
    assert gates["tests"] is False and errors
    gates, _, _ = run_gates(repo, cfg, ["src/__init__.py"], side_refs=[], quick=True)
    assert "tests" not in gates  # skipped is not "passed"


def test_tests_gate_skipped_when_repo_has_no_tests(repo):
    repo.commit({"app/pay.py": "x = 1\n"}, "init")
    gates, _, cmd = run_gates(repo, Config(test_command="false"), ["app/pay.py"], side_refs=[])
    assert "tests" not in gates and cmd is None  # skipped, never reported as passed
