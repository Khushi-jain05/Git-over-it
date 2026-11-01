import json

import pytest

from prov.config import DEFAULT_LLM
from prov.errors import ProvError
from prov.llm.cache import Cache
from prov.llm.provider import OpenAICompatProvider
from prov.llm.ratelimit import RateLimiter
from prov.llm.schemas import JudgeOut
from prov.store.index import Index
from tests.helpers.fake_llm import FakeLLM

GOOD = json.dumps({"same_change": True, "confidence": 0.8, "reason": "same fix"})


def provider(repo, url, cache=True):
    from prov.config import Config

    idx = Index(repo, Config())
    cfg = {**DEFAULT_LLM, "base_url": url, "fallback_base_url": None}
    return OpenAICompatProvider(cfg, Cache(idx.db, cache), repo.prov_dir / "llm.log")


def call(p):
    return p.complete_json(model="m", system="s", user="u", schema=JudgeOut)


def test_valid_json_then_cached(repo):
    fake = FakeLLM([(200, GOOD)])
    p = provider(repo, fake.url)
    obj, meta = call(p)
    assert obj.same_change and not meta["cached"]
    obj2, meta2 = call(p)
    assert meta2["cached"] and len(fake.requests) == 1
    assert (repo.prov_dir / "llm.log").read_text().count("\n") == 2


def test_invalid_json_is_repaired_once(repo):
    fake = FakeLLM([(200, "not json"), (200, "```json\n" + GOOD + "\n```")])
    obj, _ = call(provider(repo, fake.url))
    assert obj.reason == "same fix" and len(fake.requests) == 2


def test_invalid_twice_gives_up(repo):
    fake = FakeLLM([(200, "nope")])
    with pytest.raises(ProvError) as e:
        call(provider(repo, fake.url))
    assert e.value.exit == 8 and len(fake.requests) == 2


def test_429_raises_rate_limited(repo):
    fake = FakeLLM([(429, "slow down")])
    with pytest.raises(ProvError) as e:
        call(provider(repo, fake.url))
    assert e.value.code == "llm_rate_limited" and e.value.exit == 8


def test_unreachable_is_unavailable(repo):
    with pytest.raises(ProvError) as e:
        call(provider(repo, "http://localhost:9/v1"))
    assert e.value.code == "llm_unavailable"


def test_rate_limiter_waits_for_tokens():
    t = [0.0]
    slept = []
    rl = RateLimiter(rpm=60, tpm=600, clock=lambda: t[0], sleep=lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)))
    rl.acquire(600)
    rl.acquire(300)  # bucket empty: needs 30 s of refill
    assert sum(slept) == pytest.approx(30, rel=0.05)
