"""L4: metadata adjustments to L2/L3 scores (docs/04 §L4). Never creates a match on its own."""

from __future__ import annotations

import difflib
import re

PREFIX = re.compile(r"^\s*\[[\w.]+\]\s*")  # [5.1.x], [3.12]
NOISE = re.compile(r"\s*\((?:GH-\d+|#\d+)\)")
TICKET = re.compile(r"(?:Fixed #|Refs #|#|gh-|GH-|bpo-)(\d{3,6})")


def clean_subject(s: str) -> str:
    return NOISE.sub("", PREFIX.sub("", s or "")).strip()


def adjust(x, c) -> float:
    """x, c: commits rows from the index."""
    d = 0.0
    same_author = x["author_email"] and x["author_email"] == c["author_email"]
    if same_author:
        d += 0.04
    if abs((x["author_date"] or 0) - (c["author_date"] or 0)) <= 30 * 86400:
        d += 0.02
    sim = difflib.SequenceMatcher(None, clean_subject(x["subject"]), clean_subject(c["subject"])).ratio()
    if sim >= 0.8:
        d += 0.04
    tx = set(TICKET.findall(f"{x['subject']}\n{x['body']}"))
    if tx & set(TICKET.findall(f"{c['subject']}\n{c['body']}")):
        d += 0.05
    if not same_author and sim < 0.3:
        d -= 0.05
    return max(-0.10, min(0.10, d))
