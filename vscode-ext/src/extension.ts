import * as vscode from "vscode";
import { ProvCliError, runGit, runProv } from "./cli";
import { ProvCodeLens } from "./codelens";
import { Decorations } from "./decorations";
import { ProvHover } from "./hover";
import { State } from "./state";
import { ConflictReport, Draft } from "./types";
import { evidencePanel } from "./webview";

type HunkArg = { hunkId?: string } | undefined;

export function activate(ctx: vscode.ExtensionContext): { state: State } {
  const state = new State();
  const deco = new Decorations(state);
  const out = vscode.window.createOutputChannel("git prov");
  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  status.command = "gitProv.sync";
  const cfg = () => vscode.workspace.getConfiguration("gitProv");

  const refreshEditors = () => vscode.window.visibleTextEditors.forEach((e) => deco.refresh(e));
  state.onDidChange(refreshEditors);

  const showStatus = (rep: ConflictReport | undefined) => {
    if (!rep) return status.hide();
    status.text = `$(shield) prov: ${rep.metadata_state === "none" ? "no metadata" : rep.metadata_state}`;
    status.tooltip = "git prov evidence tier — click to sync provenance notes";
    status.show();
  };

  const report = (e: unknown) => {
    const err = e as ProvCliError;
    const msg = err.hint ? `${err.message} (${err.hint})` : err.message;
    out.appendLine(`error (${err.code ?? "?"}): ${msg}`);
    console.error(`git prov: ${msg}`);
    if (err.exit === 8) vscode.window.showWarningMessage("git prov: LLM busy or unavailable, try again. " + msg);
    else vscode.window.showErrorMessage(`git prov: ${msg}`);
  };

  async function roots(): Promise<string[]> {
    const rs = new Set<string>();
    for (const f of vscode.workspace.workspaceFolders ?? []) {
      const r = await state.rootFor(f.uri);
      if (r) rs.add(r);
    }
    return [...rs];
  }

  // One analysis at a time: overlapping runs only race on the index and produce stale lenses.
  let running: Promise<void> = Promise.resolve();
  function analyze(root?: string): Promise<void> {
    running = running.then(() => analyzeNow(root), () => analyzeNow(root));
    return running;
  }

  async function analyzeNow(root?: string): Promise<void> {
    for (const r of root ? [root] : await roots()) {
      try {
        const args = ["analyze", ...(cfg().get<boolean>("llmIdentity") ? ["--llm"] : [])];
        const rep = (await runProv(args, r)) as ConflictReport;
        state.set(r, rep);
        showStatus(rep);
      } catch (e) {
        state.set(r, undefined);
        showStatus(undefined);
        if ((e as ProvCliError).exit !== 3) report(e); // 3 = no conflict in progress: clear lenses quietly
      }
    }
  }

  async function target(arg: HunkArg) {
    if (arg?.hunkId) return state.findById(arg.hunkId);
    const ed = vscode.window.activeTextEditor;
    return ed ? state.hunkAt(ed.document, ed.selection.active.line) : undefined;
  }

  async function accept(arg: HunkArg, side: "ours" | "theirs" | "draft"): Promise<void> {
    const hit = await target(arg);
    if (!hit) return void vscode.window.showInformationMessage("git prov: put the cursor inside a conflict hunk");
    const uri = vscode.Uri.file(`${hit.root}/${hit.hunk.path}`);
    const doc = await vscode.workspace.openTextDocument(uri);
    await doc.save(); // the CLI reads from disk
    try {
      await runProv(["apply", "--hunk", hit.hunk.id, "--take", side, "--hash", hit.hunk.content_hash], hit.root);
    } catch (e) {
      if ((e as ProvCliError).exit === 6) {
        await analyze(hit.root);
        return void vscode.window.showWarningMessage("Hunk changed, refreshed. Try again.");
      }
      return report(e);
    }
    const ed = await vscode.window.showTextDocument(doc);
    if (ed.document.uri.toString() === uri.toString()) await vscode.commands.executeCommand("workbench.action.files.revert");
    await analyze(hit.root);
  }

  const reg = (id: string, fn: (...a: any[]) => unknown) => ctx.subscriptions.push(vscode.commands.registerCommand(id, fn));
  reg("gitProv.analyze", () => analyze());
  reg("gitProv.acceptOurs", (a: HunkArg) => accept(a, "ours"));
  reg("gitProv.acceptTheirs", (a: HunkArg) => accept(a, "theirs"));
  reg("gitProv.acceptDraft", (a: HunkArg) => accept(a, "draft"));

  reg("gitProv.explain", async (a: HunkArg) => {
    const hit = await target(a);
    if (!hit) return;
    try {
      const d = await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: `git prov: drafting ${hit.hunk.id}…` },
        () => runProv(["draft", "--hunk", hit.hunk.id], hit.root) as Promise<Draft>,
      );
      state.drafts.set(hit.hunk.content_hash, d);
      state.set(hit.root, state.get(hit.root));
      openEvidence(hit.root, hit.hunk.id);
    } catch (e) {
      report(e);
    }
  });

  function openEvidence(root: string, hunkId: string) {
    const hit = state.findById(hunkId);
    if (!hit) return;
    evidencePanel(hit.hunk, state.drafts.get(hit.hunk.content_hash), (m) => accept({ hunkId: m.hunkId }, m.side));
  }

  reg("gitProv.showEvidence", async (a: HunkArg) => {
    const hit = await target(a);
    if (hit) openEvidence(hit.root, hit.hunk.id);
  });

  reg("gitProv.mergiraf", async () => {
    for (const r of await roots()) {
      try {
        const res = await runProv(["resolve", "--mergiraf"], r);
        const n = Object.values(res.files as Record<string, { solved: number }>).reduce((s, v) => s + v.solved, 0);
        vscode.window.showInformationMessage(`git prov: Mergiraf solved ${n} hunk(s)`);
        await vscode.commands.executeCommand("workbench.action.files.revertAll");
      } catch (e) {
        report(e);
      }
      await analyze(r);
    }
  });

  reg("gitProv.commit", async () => {
    await vscode.workspace.saveAll();
    for (const r of await roots()) {
      if (!state.get(r)) continue;
      try {
        const args = ["commit", ...(cfg().get<boolean>("runTestsOnCommit") ? [] : ["--no-tests"])];
        const run = (extra: string[]) =>
          vscode.window.withProgress(
            { location: vscode.ProgressLocation.Notification, title: "git prov: running gates and committing…" },
            () => runProv([...args, ...extra], r),
          );
        let res: any;
        try {
          res = await run([]);
        } catch (e) {
          if ((e as ProvCliError).code !== "empty_resolution") throw e;
          const choice = await vscode.window.showWarningMessage(
            "git prov: every hunk kept OURS, so this pick changes nothing.",
            { modal: true, detail: "Record the decision in an empty commit (keeps trailers and the resolution note), or skip the pick." },
            "Record empty commit",
            "Skip the pick",
          );
          if (!choice) return;
          res = await run(["--empty", choice === "Skip the pick" ? "skip" : "commit"]);
          if (res.skipped) {
            state.set(r, undefined);
            showStatus(undefined);
            return void vscode.window.showInformationMessage(`git prov: skipped ${String(res.source).slice(0, 10)}`);
          }
        }
        state.set(r, undefined);
        showStatus(undefined);
        const pick = await vscode.window.showInformationMessage(`git prov: committed ${String(res.commit).slice(0, 10)}`, "View trailers");
        if (pick) {
          out.clear();
          out.appendLine(await runGit(["log", "-1", "--format=%B"], r));
          out.show();
        }
      } catch (e) {
        const err = e as ProvCliError;
        if (err.exit === 5) {
          out.clear();
          out.appendLine("Gates failed:\n" + err.message.split("; ").join("\n"));
          out.show();
          vscode.window.showErrorMessage("git prov: gates failed — see the git prov output channel");
        } else report(e);
      }
    }
  });

  reg("gitProv.sync", async () => {
    for (const r of await roots()) {
      try {
        const res = await runProv(["sync"], r);
        vscode.window.showInformationMessage(`git prov: notes merged [${res.merged.join(", ")}], pushed [${res.pushed.join(", ")}]`);
      } catch (e) {
        report(e);
      }
      if (state.get(r)) await analyze(r);
    }
  });

  ctx.subscriptions.push(
    vscode.languages.registerCodeLensProvider({ scheme: "file" }, new ProvCodeLens(state)),
    vscode.languages.registerHoverProvider({ scheme: "file" }, new ProvHover(state)),
    vscode.window.onDidChangeVisibleTextEditors(refreshEditors),
    status,
    out,
    { dispose: () => deco.dispose() },
  );

  // Re-analyze when an operation starts/ends, and on save of a file with markers (debounced).
  let timer: NodeJS.Timeout | undefined;
  const later = () => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => cfg().get<boolean>("autoAnalyze") && analyze(), 500);
  };
  for (const f of vscode.workspace.workspaceFolders ?? []) {
    const w = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(f, ".git/*_HEAD"));
    w.onDidCreate(later);
    w.onDidDelete(later);
    ctx.subscriptions.push(w);
  }
  ctx.subscriptions.push(
    vscode.workspace.onDidSaveTextDocument((doc) => {
      if (/^(<{7}|>{7}) /m.test(doc.getText()) || state.all().length) later();
    }),
  );
  if (cfg().get<boolean>("autoAnalyze")) analyze();
  return { state };
}

export function deactivate(): void {}
