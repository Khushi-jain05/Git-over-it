import * as vscode from "vscode";
import { recommendationText, sideLabel } from "./format";
import { State } from "./state";

export class ProvCodeLens implements vscode.CodeLensProvider {
  readonly onDidChangeCodeLenses: vscode.Event<void>;

  constructor(private state: State) {
    this.onDidChangeCodeLenses = state.onDidChange;
  }

  async provideCodeLenses(doc: vscode.TextDocument): Promise<vscode.CodeLens[]> {
    const root = await this.state.rootFor(doc.uri);
    if (!root) return [];
    const lenses: vscode.CodeLens[] = [];
    for (const h of this.state.hunksFor(root, doc)) {
      const top = new vscode.Range(h.start_line - 1, 0, h.start_line - 1, 0);
      const second = new vscode.Range(Math.min(h.start_line, doc.lineCount - 1), 0, Math.min(h.start_line, doc.lineCount - 1), 0);
      const arg = { hunkId: h.id };
      const evidence = { title: "", command: "gitProv.showEvidence", arguments: [arg] };
      lenses.push(
        new vscode.CodeLens(top, { ...evidence, title: `$(git-merge) ${sideLabel(h.theirs)}` }),
        new vscode.CodeLens(top, { ...evidence, title: sideLabel(h.ours) }),
        new vscode.CodeLens(top, { ...evidence, title: `Recommend: ${recommendationText(h.recommendation)}`, tooltip: h.recommendation_reason }),
        new vscode.CodeLens(top, { title: "Explain", command: "gitProv.explain", arguments: [arg] }),
        new vscode.CodeLens(top, { ...evidence, title: "Evidence" }),
        new vscode.CodeLens(second, { title: "Accept OURS", command: "gitProv.acceptOurs", arguments: [arg] }),
        new vscode.CodeLens(second, { title: "Accept THEIRS", command: "gitProv.acceptTheirs", arguments: [arg] }),
      );
      const d = this.state.drafts.get(h.content_hash);
      if (d) {
        lenses.push(
          d.gates_passed
            ? new vscode.CodeLens(second, { title: "Accept draft", command: "gitProv.acceptDraft", arguments: [arg] })
            : new vscode.CodeLens(second, {
                title: "Draft failed gates",
                command: "gitProv.showEvidence",
                arguments: [arg],
                tooltip: d.gate_errors.join("\n"),
              }),
        );
      }
    }
    return lenses;
  }
}
