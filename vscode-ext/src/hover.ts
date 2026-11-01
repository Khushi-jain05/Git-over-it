import * as vscode from "vscode";
import { recommendationText, sideMarkdown } from "./format";
import { State } from "./state";

export class ProvHover implements vscode.HoverProvider {
  constructor(private state: State) {}

  async provideHover(doc: vscode.TextDocument, pos: vscode.Position): Promise<vscode.Hover | undefined> {
    const hit = await this.state.hunkAt(doc, pos.line);
    if (!hit) return undefined;
    const h = hit.hunk;
    const md = new vscode.MarkdownString(
      [sideMarkdown(h.theirs), sideMarkdown(h.ours), `**Recommend:** ${recommendationText(h.recommendation)} — ${h.recommendation_reason}`].join(
        "\n\n---\n\n",
      ),
    );
    md.isTrusted = false;
    return new vscode.Hover(md, new vscode.Range(h.start_line - 1, 0, h.end_line - 1, 0));
  }
}
