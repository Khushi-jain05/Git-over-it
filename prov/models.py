"""Shared data shapes. Contract: docs/03-data-model.md §1. Change only with a schema_version bump."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel

SCHEMA_VERSION = 1


class Env(str, Enum):
    dev = "dev"
    uat = "uat"
    prod = "prod"
    release = "release"
    local = "local"


class Kind(str, Enum):
    test = "test"
    deploy = "deploy"
    review = "review"


class Result(str, Enum):
    PASSED = "PASSED"
    WARNED = "WARNED"
    FAILED = "FAILED"


class Tier(str, Enum):
    recorded = "recorded"  # read from refs/notes/prov/*
    inferred = "inferred"  # from trailers or branch/tag reachability
    none = "none"


class Badge(str, Enum):
    PROD_VERIFIED = "PROD_VERIFIED"
    TESTED_ELSEWHERE = "TESTED_ELSEWHERE"
    UNVERIFIED = "UNVERIFIED"
    FAILED = "FAILED"
    NO_METADATA = "NO_METADATA"


class Level(str, Enum):
    L0 = "L0_trailer"
    L1 = "L1_patch_id"
    L2 = "L2_hunks"
    L3 = "L3_fuzzy"
    L4 = "L4_metadata"
    L5 = "L5_llm"
    SELF = "self"


# ---------- provenance record (stored in notes) ----------
class Subject(BaseModel):
    commit: str
    tree: str
    patch_id: str | None = None
    change_id: str | None = None


class ProvRecord(BaseModel):
    """One line of JSON in a note. Serialise with sort_keys=True, no spaces."""

    v: int = SCHEMA_VERSION
    id: str
    subject: Subject
    kind: Kind
    env: Env
    result: Result
    suite: str | None = None
    failed_tests: list[str] = []
    url: str | None = None
    runner: str | None = None
    at: datetime
    signature: str | None = None


# ---------- identity ----------
class Match(BaseModel):
    sha: str
    branch: str
    env: Env | None
    level: Level
    confidence: float
    reason: str


# ---------- analysis ----------
class Origin(BaseModel):
    sha: str
    subject: str
    author: str
    date: datetime
    lines: int
    branches: list[str]


class EvidenceItem(BaseModel):
    record: ProvRecord | None
    via: Match
    tier: Tier
    note: str
    rolled_back_by: str | None = None  # a later prod deploy (by `at`) of a commit without this change


class SideReport(BaseModel):
    side: Literal["ours", "theirs", "base"]
    text: str
    origins: list[Origin]
    matches: list[Match]
    evidence: list[EvidenceItem]
    badge: Badge
    tier: Tier
    tip_tree_tested: list[Env] = []
    tip_tree_deployed: list[Env] = []  # recorded, not rolled-back deploys of the side's tip tree


class HunkReport(BaseModel):
    id: str
    path: str
    index: int
    start_line: int
    end_line: int
    content_hash: str
    base: SideReport | None
    ours: SideReport
    theirs: SideReport
    recommendation: Literal["take_ours", "take_theirs", "combine", "manual"]
    recommendation_reason: str
    mergiraf_solved: bool = False
    moved_to: str | None = None  # one side deleted these lines because it moved them to this file
    previous_resolution: str | None = None  # an earlier merge that resolved this same conflict


class FileConflict(BaseModel):
    path: str
    kind: Literal["content", "modify_delete", "add_add", "rename", "binary", "submodule", "other"]
    hunks: list[HunkReport]
    # file-level provenance for conflicts without hunks (modify/delete, binary, ...): last commit touching the path
    ours: SideReport | None = None
    theirs: SideReport | None = None
    previous_resolution: str | None = None  # an earlier merge that resolved this same conflict


class ConflictReport(BaseModel):
    schema_version: int = SCHEMA_VERSION
    operation: Literal["cherry-pick", "merge", "rebase", "revert", "am", "squash-merge", "none"]
    ours_ref: str
    theirs_ref: str | None
    metadata_state: Tier
    files: list[FileConflict]
    generated_at: datetime


# ---------- LLM ----------
class Draft(BaseModel):
    hunk_id: str
    model: str
    prompt_hash: str
    explanation_ours: str
    explanation_theirs: str
    strategy: Literal["take_ours", "take_theirs", "combine", "manual"]
    resolved_code: str
    risk_notes: list[str]
    untested_lines: list[int]
    gates_passed: bool | None = None
    gate_errors: list[str] = []


# ---------- resolution (stored in notes on the new commit) ----------
class HunkDecision(BaseModel):
    hunk_id: str
    choice: Literal["ours", "theirs", "draft", "manual", "mergiraf"]
    ours_badge: Badge
    theirs_badge: Badge
    llm_model: str | None = None
    llm_prompt_hash: str | None = None


class ResolutionRecord(BaseModel):
    v: int = SCHEMA_VERSION
    commit: str
    operation: str
    source: str | None
    change_id: str
    decisions: list[HunkDecision]
    gates: dict[str, bool]
    at: datetime
