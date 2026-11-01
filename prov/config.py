"""Load `.prov.toml` (docs/02). Missing file → defaults."""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

from prov.models import Env

DEFAULT_ENVIRONMENTS = {
    "main": "dev",
    "develop": "dev",
    "dev": "dev",
    "uat": "uat",
    "production": "prod",
    "prod": "prod",
    "stable/*": "release",
    "release/*": "release",
}


DEFAULT_LLM = {
    "provider": "groq",
    "base_url": "https://api.groq.com/openai/v1",
    "api_key_env": "GROQ_API_KEY",
    "draft_model": "openai/gpt-oss-120b",
    "judge_model": "openai/gpt-oss-20b",
    "fallback_base_url": "http://localhost:11434/v1",  # Ollama, offline
    "fallback_model": "qwen2.5-coder:7b",
    "rpm": 30,
    "tpm": 8000,
    "max_prompt_tokens": 3500,
    "cache": True,
}


@dataclass
class Config:
    environments: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ENVIRONMENTS))
    remote: str = "origin"
    window_days: int = 730
    test_command: str = "pytest -q -x {tests}"
    test_timeout_s: int = 300
    test_map: list[dict] = field(default_factory=list)
    l2_min_jaccard: float = 0.5
    l3_accept: float = 0.72  # tuned on Django E1 (docs/IMPLEMENTATION.md); CPython held out
    l5_enabled: bool = True
    l5_band: tuple[float, float] = (0.40, 0.72)
    llm: dict = field(default_factory=lambda: dict(DEFAULT_LLM))
    mergiraf_enabled: bool = True
    mergiraf_binary: str = "mergiraf"
    read_refs: list[str] = field(default_factory=list)
    extra_refs: list[str] = field(default_factory=list)  # also index these (for-each-ref patterns), no env  # other tools' JSON-line notes, read-only (e.g. "tests")
    raw: dict = field(default_factory=dict)

    def env_for(self, branch: str) -> Env | None:
        for pattern, env in self.environments.items():
            if fnmatchcase(branch, pattern):
                return Env(env)
        return None


def load(root: Path) -> Config:
    """`.prov.toml` at the repo root; $PROV_CONFIG points elsewhere (keeps the working tree untouched)."""
    path = Path(os.environ["PROV_CONFIG"]) if os.environ.get("PROV_CONFIG") else root / ".prov.toml"
    if not path.exists():
        return Config()
    data = tomllib.loads(path.read_text())
    ident, gates, mg = data.get("identity", {}), data.get("gates", {}), data.get("mergiraf", {})
    return Config(
        environments=data.get("environments") or dict(DEFAULT_ENVIRONMENTS),
        remote=data.get("remote", {}).get("name", "origin"),
        window_days=ident.get("window_days", 730),
        test_command=gates.get("test_command", "pytest -q -x {tests}"),
        test_timeout_s=gates.get("test_timeout_s", 300),
        test_map=gates.get("test_map", []),
        l2_min_jaccard=ident.get("l2_min_jaccard", 0.5),
        l3_accept=ident.get("l3_accept", 0.72),
        l5_enabled=ident.get("l5_enabled", True),
        l5_band=tuple(ident.get("l5_band", (0.40, 0.72))),
        llm={**DEFAULT_LLM, **data.get("llm", {})},
        mergiraf_enabled=mg.get("enabled", True),
        mergiraf_binary=mg.get("binary", "mergiraf"),
        read_refs=list(data.get("provenance", {}).get("read_refs", [])),
        extra_refs=list(data.get("index", {}).get("extra_refs", [])),
        raw=data,
    )


def render(environments: dict[str, str], remote: str = "origin", test_command: str = "pytest -q -x {tests}",
           test_map: str = "") -> str:
    envs = "\n".join(f'"{k}" = "{v}"' for k, v in environments.items())
    return f"""schema_version = 1

[environments]
# branch (glob allowed) -> environment name. First match wins.
{envs}

[remote]
name = "{remote}"

[identity]
window_days = 730
l2_min_jaccard = 0.5
l3_accept = 0.72
l5_enabled = true
l5_band = [0.40, 0.72]

[gates]
test_command = {json.dumps(test_command)}
test_timeout_s = 300
{test_map}

[llm]
provider = "groq"
base_url = "https://api.groq.com/openai/v1"
api_key_env = "GROQ_API_KEY"
draft_model = "openai/gpt-oss-120b"
judge_model = "openai/gpt-oss-20b"
fallback_base_url = "http://localhost:11434/v1"
fallback_model = "qwen2.5-coder:7b"
rpm = 30
tpm = 8000
max_prompt_tokens = 3500
cache = true

[mergiraf]
enabled = true
binary = "mergiraf"
"""
