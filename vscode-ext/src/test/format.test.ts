import assert from "node:assert/strict";
import { test } from "node:test";
import { badgeText, escapeHtml, sideLabel } from "../format";
import { SideReport } from "../types";

const side = (over: Partial<SideReport>): SideReport => ({
  side: "theirs", text: "", origins: [], matches: [], evidence: [], badge: "PROD_VERIFIED", tier: "recorded",
  tip_tree_tested: [], ...over,
});

test("inferred tier is never rendered like recorded", () => {
  assert.equal(badgeText("PROD_VERIFIED", "recorded"), "PROD-VERIFIED");
  assert.equal(badgeText("PROD_VERIFIED", "inferred"), "PROD-VERIFIED (inferred)");
});

test("side label lists evidence environments in order", () => {
  const via = { sha: "a".repeat(40), branch: "uat", env: "uat" as const, level: "self", confidence: 1, reason: "" };
  const rec = (env: "uat" | "prod") => ({
    v: 1, id: env, subject: { commit: "", tree: "", patch_id: null, change_id: null }, kind: "test" as const, env,
    result: "PASSED" as const, suite: null, failed_tests: [], url: null, runner: null, at: "2026-10-20T00:00:00Z", signature: null,
  });
  const s = side({ evidence: [{ record: rec("prod"), via, tier: "recorded", note: "" }, { record: rec("uat"), via, tier: "recorded", note: "" }] });
  assert.equal(sideLabel(s), "THEIRS: PROD-VERIFIED (uat, prod)");
});

test("html escaping", () => {
  assert.equal(escapeHtml(`<a href="x">'&'</a>`), "&lt;a href=&quot;x&quot;&gt;&#39;&amp;&#39;&lt;/a&gt;");
});
