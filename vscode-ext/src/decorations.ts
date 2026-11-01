import * as vscode from "vscode";
import { BADGE_COLOR } from "./format";
import { State } from "./state";
import { Badge, Tier } from "./types";

const MARK = /^(<{7}|\|{7}|={7}|>{7})( |$)/;

/** Background per side by badge; inferred tier gets a dashed left border instead of a fill (AGENTS rule 3). */
export class Decorations {
  private types = new Map<string, vscode.TextEditorDecorationType>();

  constructor(private state: State) {}

  private type(badge: Badge, tier: Tier): vscode.TextEditorDecorationType {
    const key = `${badge}/${tier}`;
    let t = this.types.get(key);
    if (!t) {
      const color = BADGE_COLOR[badge];
      const inferred = tier !== "recorded";
      t = vscode.window.createTextEditorDecorationType({
        isWholeLine: true,
        backgroundColor: inferred ? undefined : `${color}22`,
        borderStyle: inferred ? "none none none dashed" : undefined,
        borderWidth: inferred ? "0 0 0 3px" : undefined,
        borderColor: inferred ? color : undefined,
        overviewRulerColor: color,
        overviewRulerLane: vscode.OverviewRulerLane.Left,
      });
      this.types.set(key, t);
    }
    return t;
  }

  async refresh(editor: vscode.TextEditor): Promise<void> {
    const root = await this.state.rootFor(editor.document.uri);
    const ranges = new Map<vscode.TextEditorDecorationType, vscode.Range[]>();
    for (const t of this.types.values()) ranges.set(t, []);
    for (const h of root ? this.state.hunksFor(root, editor.document) : []) {
      let mode: "ours" | "base" | "theirs" = "ours";
      let start = h.start_line; // 0-based first line after <<<<<<<
      const flush = (end: number, side: "ours" | "theirs") => {
        if (end <= start) return;
        const s = side === "ours" ? h.ours : h.theirs;
        const t = this.type(s.badge, s.tier);
        if (!ranges.has(t)) ranges.set(t, []);
        ranges.get(t)!.push(new vscode.Range(start, 0, end - 1, 0));
      };
      for (let i = h.start_line; i < h.end_line - 1 && i < editor.document.lineCount; i++) {
        const m = MARK.exec(editor.document.lineAt(i).text);
        if (!m) continue;
        if (mode === "ours") flush(i, "ours");
        mode = m[1][0] === "|" ? "base" : "theirs";
        start = i + 1;
      }
      flush(h.end_line - 1, "theirs");
    }
    for (const [t, r] of ranges) editor.setDecorations(t, r);
  }

  dispose(): void {
    for (const t of this.types.values()) t.dispose();
  }
}
