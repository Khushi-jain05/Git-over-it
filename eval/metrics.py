"""Precision / recall / F1 and per-level tallies (docs/10 §2)."""

from __future__ import annotations

from collections import defaultdict


def prf(tp: int, predicted: int, positives: int) -> dict[str, float]:
    p = tp / predicted if predicted else 0.0
    r = tp / positives if positives else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(2 * p * r / (p + r), 4) if p + r else 0.0,
            "tp": tp, "predicted": predicted, "positives": positives}


def identity_metrics(rows: list[dict]) -> dict:
    """rows: {original (None for negatives), predicted, level, edited}"""
    def block(rs):
        pos = [r for r in rs if r["original"]]
        tp = sum(1 for r in pos if r["predicted"] == r["original"])
        return prf(tp, sum(1 for r in rs if r["predicted"]), len(pos))

    negs = [r for r in rows if not r["original"]]
    by_level = defaultdict(lambda: {"predicted": 0, "tp": 0})
    for r in rows:
        if r["predicted"]:
            by_level[r["level"]]["predicted"] += 1
            by_level[r["level"]]["tp"] += int(r["predicted"] == r["original"])
    return {
        "all": block(rows),
        "edited": block([r for r in rows if r.get("edited")] + negs),
        "unedited": block([r for r in rows if r["original"] and not r.get("edited")] + negs),
        "negatives": len(negs),
        "false_positive_on_negatives": sum(1 for r in negs if r["predicted"]),
        "by_level": {k: {**v, "precision": round(v["tp"] / v["predicted"], 4)} for k, v in sorted(by_level.items())},
    }
