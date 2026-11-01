"""Structured LLM outputs (docs/07 §5). Only explanation_*/draft/risk fields come from the model."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DraftOut(BaseModel):
    explanation_ours: str
    explanation_theirs: str
    strategy: Literal["take_ours", "take_theirs", "combine", "manual"]
    resolved_code: str
    risk_notes: list[str] = []
    untested_lines: list[int] = []


class JudgeOut(BaseModel):
    same_change: bool
    confidence: float = Field(ge=0, le=1)
    reason: str
