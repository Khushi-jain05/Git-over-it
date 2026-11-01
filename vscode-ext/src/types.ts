// Hand-written mirror of the --json contract (docs/08). Keep in sync with prov/models.py; schema_version 1.

export type Badge = "PROD_VERIFIED" | "TESTED_ELSEWHERE" | "UNVERIFIED" | "FAILED" | "NO_METADATA";
export type Tier = "recorded" | "inferred" | "none";
export type Env = "dev" | "uat" | "prod" | "release" | "local";

export interface ProvRecord {
  v: number;
  id: string;
  subject: { commit: string; tree: string; patch_id: string | null; change_id: string | null };
  kind: "test" | "deploy" | "review";
  env: Env;
  result: "PASSED" | "WARNED" | "FAILED";
  suite: string | null;
  failed_tests: string[];
  url: string | null;
  runner: string | null;
  at: string;
  signature: string | null;
}

export interface Match {
  sha: string;
  branch: string;
  env: Env | null;
  level: string;
  confidence: number;
  reason: string;
}

export interface Origin {
  sha: string;
  subject: string;
  author: string;
  date: string;
  lines: number;
  branches: string[];
}

export interface EvidenceItem {
  record: ProvRecord | null;
  via: Match;
  tier: Tier;
  note: string;
  rolled_back_by?: string | null;
}

export interface SideReport {
  side: "ours" | "theirs" | "base";
  text: string;
  origins: Origin[];
  matches: Match[];
  evidence: EvidenceItem[];
  badge: Badge;
  tier: Tier;
  tip_tree_tested: Env[];
  tip_tree_deployed?: Env[];
}

export interface HunkReport {
  id: string;
  path: string;
  index: number;
  start_line: number;
  end_line: number;
  content_hash: string;
  base: SideReport | null;
  ours: SideReport;
  theirs: SideReport;
  recommendation: "take_ours" | "take_theirs" | "combine" | "manual";
  recommendation_reason: string;
  mergiraf_solved: boolean;
  moved_to?: string | null;
  previous_resolution?: string | null;
}

export interface FileConflict {
  path: string;
  kind: string;
  hunks: HunkReport[];
  ours?: SideReport | null; // file-level provenance when there are no hunks (modify/delete, binary)
  theirs?: SideReport | null;
  previous_resolution?: string | null;
}

export interface ConflictReport {
  schema_version: number;
  operation: string;
  ours_ref: string;
  theirs_ref: string | null;
  metadata_state: Tier;
  files: FileConflict[];
  generated_at: string;
}

export interface Draft {
  hunk_id: string;
  model: string;
  prompt_hash: string;
  explanation_ours: string;
  explanation_theirs: string;
  strategy: string;
  resolved_code: string;
  risk_notes: string[];
  untested_lines: number[];
  gates_passed: boolean | null;
  gate_errors: string[];
}
