// Pure formatting helpers (no vscode import, unit-tested with node --test).
import { Badge, EvidenceItem, Env, SideReport, Tier } from "./types";

const BADGE_TEXT: Record<Badge, string> = {
  PROD_VERIFIED: "PROD-VERIFIED",
  TESTED_ELSEWHERE: "TESTED ELSEWHERE",
  UNVERIFIED: "UNVERIFIED",
  FAILED: "FAILED",
  NO_METADATA: "NO METADATA",
};

export function badgeText(badge: Badge, tier: Tier): string {
  return BADGE_TEXT[badge] + (tier === "inferred" ? " (inferred)" : "");
}

/** Environments a side has evidence in, e.g. "(uat, prod)". */
export function envList(side: SideReport): string {
  const order: Env[] = ["dev", "release", "uat", "prod", "local"];
  const envs = new Set<Env>();
  for (const e of side.evidence) {
    const env = e.record ? e.record.env : e.via.env;
    if (env) envs.add(env);
  }
  const list = order.filter((e) => envs.has(e));
  return list.length ? ` (${list.join(", ")})` : "";
}

export function sideLabel(side: SideReport): string {
  return `${side.side.toUpperCase()}: ${badgeText(side.badge, side.tier)}${envList(side)}`;
}

export function recommendationText(rec: string): string {
  return { take_ours: "take OURS", take_theirs: "take THEIRS", combine: "combine", manual: "manual" }[rec] ?? rec;
}

export function shortDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
}

export function evidenceLine(e: EvidenceItem): string {
  const via = e.via.level === "self" ? "" : ` · via \`${e.via.sha.slice(0, 7)}\` (${levelText(e.via.level)})`;
  if (!e.record) return `- ${e.note} *(inferred)*${via}`;
  const r = e.record;
  const suite = r.kind === "test" && r.suite ? ` (${r.suite})` : "";
  const link = r.url ? ` · [run](${r.url})` : "";
  return `- ${r.env} ${r.kind} ${r.result}${suite} · ${shortDate(r.at)}${link}${via}`;
}

export function levelText(level: string): string {
  return (
    {
      L0_trailer: "cherry-pick trailer",
      L1_patch_id: "identical patch",
      L2_hunks: "same hunks",
      L3_fuzzy: "similar diff",
      L5_llm: "LLM judge",
      self: "origin",
    }[level] ?? level
  );
}

/** Markdown for one side, as in docs/09 §Hover. */
export function sideMarkdown(side: SideReport): string {
  const o = side.origins[0];
  const head = o
    ? `**${side.side.toUpperCase()}** — \`${o.sha.slice(0, 7)}\` ${o.subject} — ${o.author}, ${shortDate(o.date)}`
    : `**${side.side.toUpperCase()}**`;
  const branches = o && o.branches.length ? `\n\nBranches: ${o.branches.join(", ")}` : "";
  const ev = side.evidence.length ? "\n\n" + side.evidence.slice(0, 6).map(evidenceLine).join("\n") : "\n\n- no test evidence";
  return `${head}${branches}${ev}\n\nBadge: **${badgeText(side.badge, side.tier)}** (${side.tier})`;
}

export function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

export const BADGE_COLOR: Record<Badge, string> = {
  PROD_VERIFIED: "#2ea043",
  TESTED_ELSEWHERE: "#d29922",
  FAILED: "#f85149",
  UNVERIFIED: "#8b949e",
  NO_METADATA: "#8b949e",
};
