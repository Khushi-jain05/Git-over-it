import assert from "node:assert/strict";
import * as path from "path";
import * as vscode from "vscode";

export async function run(): Promise<void> {
  const root = vscode.workspace.workspaceFolders![0].uri.fsPath;
  const doc = await vscode.workspace.openTextDocument(path.join(root, "shop/billing.py"));
  await vscode.window.showTextDocument(doc);
  const api = await vscode.extensions.getExtension("git-over-it.git-prov")!.activate();
  await vscode.commands.executeCommand("gitProv.analyze");
  assert.ok(api.state.all().length, "analyze produced no report (is git-prov on PATH?)");
  const lenses = (await vscode.commands.executeCommand("vscode.executeCodeLensProvider", doc.uri)) as vscode.CodeLens[];
  const titles = lenses.map((l) => l.command?.title ?? "");
  assert.ok(titles.some((t) => t.includes("THEIRS: PROD-VERIFIED")), titles.join(" | "));
  const marker = doc.getText().split("\n").findIndex((l) => l.startsWith("<<<<<<<"));
  assert.ok(lenses.some((l) => l.range.start.line === marker));
  await vscode.commands.executeCommand("gitProv.acceptTheirs", { hunkId: "shop/billing.py#0" });
  const after = (await vscode.workspace.openTextDocument(doc.uri)).getText();
  assert.equal(after.split("<<<<<<<").length - 1, 1, "one hunk left");
}
