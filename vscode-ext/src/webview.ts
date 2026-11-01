import * as vscode from "vscode";
import { BADGE_COLOR, badgeText, escapeHtml as esc, levelText, shortDate } from "./format";
import { Draft, HunkReport, SideReport } from "./types";

export type AcceptMessage = { type: "accept"; side: "ours" | "theirs" | "draft"; hunkId: string };

function sideHtml(s: SideReport): string {
  const color = BADGE_COLOR[s.badge];
  const style = s.tier === "recorded" ? `background:${color}33;border:1px solid ${color}` : `border:1px dashed ${color}`;
  const origins = s.origins
    .map((o) => `<tr><td><code>${o.sha.slice(0, 7)}</code></td><td>${esc(o.subject)}</td><td>${esc(o.author)}</td><td>${shortDate(o.date)}</td><td>${o.lines}</td></tr>`)
    .join("");
  const matches = s.matches
    .map((m) => `<tr><td>${esc(m.branch || "-")}</td><td>${m.env ?? "-"}</td><td><code>${m.sha.slice(0, 7)}</code></td><td>${levelText(m.level)}</td><td>${m.confidence.toFixed(2)}</td><td>${esc(m.reason)}</td></tr>`)
    .join("");
  const evidence = s.evidence
    .map((e) => {
      const r = e.record;
      const link = r?.url ? `<a href="${esc(r.url)}">run</a>` : "";
      return r
        ? `<tr><td>${r.env}</td><td>${r.kind}</td><td>${r.result}</td><td>${esc(r.suite ?? "")}</td><td>${shortDate(r.at)}</td><td>${link}</td><td>${e.tier}</td><td><code>${e.via.sha.slice(0, 7)}</code> ${levelText(e.via.level)}</td></tr>`
        : `<tr><td>${e.via.env ?? "-"}</td><td colspan="5">${esc(e.note)}</td><td>${e.tier}</td><td><code>${e.via.sha.slice(0, 7)}</code> ${levelText(e.via.level)}</td></tr>`;
    })
    .join("");
  return `<section>
  <h2>${s.side.toUpperCase()} <span class="badge" style="${style}">${badgeText(s.badge, s.tier)}</span> <small>${s.tier}</small></h2>
  <h3>Origin commits</h3><table><tr><th>sha</th><th>subject</th><th>author</th><th>date</th><th>lines</th></tr>${origins || '<tr><td colspan="5">-</td></tr>'}</table>
  <h3>Equivalents</h3><table><tr><th>branch</th><th>env</th><th>sha</th><th>level</th><th>conf</th><th>reason</th></tr>${matches || '<tr><td colspan="6">-</td></tr>'}</table>
  <h3>Evidence</h3><table><tr><th>env</th><th>kind</th><th>result</th><th>suite</th><th>date</th><th>link</th><th>tier</th><th>via</th></tr>${evidence || '<tr><td colspan="8">no test evidence</td></tr>'}</table>
  <button data-side="${s.side}">Accept ${s.side.toUpperCase()}</button>
</section>`;
}

function draftHtml(d: Draft): string {
  const untested = new Set(d.untested_lines);
  const code = d.resolved_code
    .split("\n")
    .map((ln, i) => `<div class="${untested.has(i + 1) ? "untested" : ""}">${esc(ln) || "&nbsp;"}</div>`)
    .join("");
  const gates = d.gates_passed ? `<span class="ok">gates passed</span>` : `<span class="bad">gates failed: ${esc(d.gate_errors.join("; "))}</span>`;
  return `<section><h2>Draft <small>${esc(d.model)} · ${esc(d.strategy)}</small></h2>
  <p><b>OURS:</b> ${esc(d.explanation_ours)}</p><p><b>THEIRS:</b> ${esc(d.explanation_theirs)}</p>
  ${d.risk_notes.map((r) => `<p class="risk">risk: ${esc(r)}</p>`).join("")}
  <pre>${code}</pre><p>${gates} · highlighted lines are untested</p>
  <button data-side="draft" ${d.gates_passed ? "" : "disabled"}>Accept draft</button></section>`;
}

export function evidencePanel(hunk: HunkReport, draft: Draft | undefined, onAccept: (m: AcceptMessage) => void): vscode.WebviewPanel {
  const panel = vscode.window.createWebviewPanel("gitProvEvidence", `Evidence: ${hunk.id}`, vscode.ViewColumn.Beside, {
    enableScripts: true,
  });
  const nonce = Math.random().toString(36).slice(2);
  panel.webview.html = `<!DOCTYPE html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';">
<style>
body{font-family:var(--vscode-font-family);color:var(--vscode-foreground);padding:0 1em}
table{border-collapse:collapse;width:100%;margin-bottom:.5em}td,th{border-bottom:1px solid var(--vscode-panel-border);padding:2px 6px;text-align:left;font-size:.9em}
.badge{padding:1px 6px;border-radius:3px;font-size:.8em}.untested{background:#d2992233}.ok{color:#2ea043}.bad{color:#f85149}.risk{color:#d29922}
pre{background:var(--vscode-textCodeBlock-background);padding:6px}button{margin:.5em 0}
</style></head><body>
<h1>${esc(hunk.id)}</h1><p>Recommend <b>${esc(hunk.recommendation)}</b>: ${esc(hunk.recommendation_reason)}</p>
${sideHtml(hunk.theirs)}${sideHtml(hunk.ours)}${draft ? draftHtml(draft) : ""}
<script nonce="${nonce}">
const vscode = acquireVsCodeApi();
document.querySelectorAll("button[data-side]").forEach(b => b.addEventListener("click", () =>
  vscode.postMessage({type: "accept", side: b.dataset.side, hunkId: ${JSON.stringify(hunk.id)}})));
</script></body></html>`;
  panel.webview.onDidReceiveMessage((m: AcceptMessage) => {
    if (m?.type === "accept") onAccept(m);
  });
  return panel;
}
