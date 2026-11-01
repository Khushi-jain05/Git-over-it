"""OpenAI-compatible provider (Groq by default, Ollama fallback) with cache, rate limit, audit log (docs/07)."""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

from prov.errors import ProvError
from prov.llm.cache import Cache
from prov.llm.ratelimit import RateLimiter, estimate_tokens


def prompt_hash(model: str, system: str, user: str, schema: type[BaseModel]) -> str:
    blob = json.dumps([model, system, user, schema.model_json_schema()], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


class OpenAICompatProvider:
    def __init__(self, cfg: dict, cache: Cache, log_path: Path, *, use_cache: bool = True):
        self.cfg, self.cache, self.log_path, self.use_cache = cfg, cache, log_path, use_cache
        self.limiter = RateLimiter(cfg["rpm"], cfg["tpm"])
        self._clients: dict[str, object] = {}

    def _client(self, base_url: str, api_key: str):
        if base_url not in self._clients:
            try:
                from openai import OpenAI
            except ImportError as e:
                raise ProvError("llm_unavailable", "openai package not installed", hint="pip install 'git-prov[llm]'") from e
            self._clients[base_url] = OpenAI(base_url=base_url, api_key=api_key, max_retries=0, timeout=60)
        return self._clients[base_url]

    def _endpoints(self, model: str) -> list[tuple[str, str, str]]:
        """[(base_url, api_key, model)] in order of preference."""
        out = []
        key = os.environ.get(self.cfg["api_key_env"], "")
        if key or "localhost" in self.cfg["base_url"]:
            out.append((self.cfg["base_url"], key or "local", model))
        if self.cfg.get("fallback_base_url"):
            out.append((self.cfg["fallback_base_url"], "ollama", self.cfg.get("fallback_model") or model))
        return out

    def complete_json(self, *, model: str, system: str, user: str, schema: type[BaseModel],
                      max_tokens: int = 2048) -> tuple[BaseModel, dict]:
        key = prompt_hash(model, system, user, schema)
        meta = {"model": model, "prompt_hash": key, "cached": False, "latency_ms": 0, "usage": None}
        if self.use_cache and (hit := self.cache.get(key)):
            meta["cached"] = True
            self._log(meta, system, user, hit, "cache")
            return schema.model_validate_json(hit), meta
        errors = []
        for base_url, api_key, m in self._endpoints(model):
            try:
                obj, meta2 = self._call(base_url, api_key, m, system, user, schema, max_tokens)
            except ProvError as e:
                if e.code == "llm_rate_limited":
                    raise
                errors.append(f"{base_url}: {e.message}")
                continue
            meta.update(meta2, prompt_hash=key)
            text = obj.model_dump_json()
            self.cache.put(key, m, text)
            self._log(meta, system, user, text, "ok")
            return obj, meta
        raise ProvError("llm_unavailable", "no LLM endpoint answered: " + ("; ".join(errors) or "none configured"),
                        hint=f"set {self.cfg['api_key_env']} or start Ollama (`ollama serve`)")

    def _call(self, base_url, api_key, model, system, user, schema, max_tokens):
        import openai

        client = self._client(base_url, api_key)
        self.limiter.acquire(estimate_tokens(system + user) + max_tokens)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        formats = [{"type": "json_schema", "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema(),
                                                           "strict": False}},
                   {"type": "json_object"}]
        t0 = time.monotonic()
        resp, last_err = None, None
        for fmt in formats:
            for attempt in range(2):
                try:
                    resp = client.chat.completions.create(model=model, temperature=0.1, max_tokens=max_tokens,
                                                          messages=messages, response_format=fmt)
                    break
                except openai.RateLimitError as e:
                    wait = min(float(e.response.headers.get("retry-after", "5") or 5), 20.0)
                    if attempt == 0:
                        time.sleep(wait)
                        continue
                    raise ProvError("llm_rate_limited", "LLM busy, try again", hint=f"retry after {wait:.0f}s") from e
                except openai.BadRequestError as e:  # response_format not supported: try the next one
                    last_err = e
                    break
                except (openai.APIConnectionError, openai.APITimeoutError, openai.AuthenticationError,
                        openai.NotFoundError, openai.InternalServerError) as e:
                    raise ProvError("llm_unavailable", str(e)) from e
            if resp is not None:
                break
        if resp is None:
            raise ProvError("llm_unavailable", f"request rejected: {last_err}")
        text = resp.choices[0].message.content or ""
        try:
            obj = schema.model_validate_json(_strip_fences(text))
        except ValidationError as e:  # one repair round, then give up
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": f"That was not valid JSON for the schema: {e}. Reply with valid JSON only."}]
            resp = client.chat.completions.create(model=model, temperature=0.0, max_tokens=max_tokens, messages=messages,
                                                  response_format={"type": "json_object"})
            text = resp.choices[0].message.content or ""
            try:
                obj = schema.model_validate_json(_strip_fences(text))
            except ValidationError as e2:
                raise ProvError("llm_unavailable", f"model returned invalid JSON twice: {e2}") from e2
        usage = resp.usage.model_dump() if getattr(resp, "usage", None) else None
        return obj, {"model": model, "latency_ms": int((time.monotonic() - t0) * 1000), "usage": usage}

    def _log(self, meta: dict, system: str, user: str, response: str, outcome: str) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"at": datetime.now(UTC).isoformat(), **meta, "outcome": outcome,
                 "system": system, "user": user, "response": response}
        with self.log_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    return t.strip()


def for_repo(repo, cfg, *, use_cache: bool = True) -> OpenAICompatProvider:
    from prov.store.index import Index

    idx = Index(repo, cfg)
    return OpenAICompatProvider(cfg.llm, Cache(idx.db, cfg.llm.get("cache", True)), repo.prov_dir / "llm.log",
                                use_cache=use_cache)


def show(repo, phash: str) -> list[dict]:
    path = repo.prov_dir / "llm.log"
    if not path.exists():
        return []
    return [e for line in path.read_text().splitlines() if (e := json.loads(line))["prompt_hash"].startswith(phash)]
