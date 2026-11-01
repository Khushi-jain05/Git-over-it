"""T0.5: check the Groq key works with both models and list what the account can see.

    GROQ_API_KEY=gsk_... python scripts/groq_ping.py
"""

from __future__ import annotations

import os
import sys

from openai import OpenAI

MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]


def main() -> int:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        print("GROQ_API_KEY not set", file=sys.stderr)
        return 1
    client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=key)
    available = sorted(m.id for m in client.models.list().data)
    print("models:", ", ".join(available))
    ok = True
    for model in MODELS:
        if model not in available:
            print(f"{model}: NOT AVAILABLE on this account")
            ok = False
            continue
        r = client.chat.completions.create(model=model, max_tokens=64, temperature=0,
                                           messages=[{"role": "user", "content": "Reply with the word pong."}])
        print(f"{model}: {(r.choices[0].message.content or '').strip()!r}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
