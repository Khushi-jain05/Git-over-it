import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import { runGit } from "./cli";
import { ConflictReport, Draft, HunkReport } from "./types";

/** Latest ConflictReport per repo root, plus drafts by content hash. */
export class State {
  private reports = new Map<string, ConflictReport>();
  private roots = new Map<string, string>();
  readonly drafts = new Map<string, Draft>();
  private emitter = new vscode.EventEmitter<void>();
  readonly onDidChange = this.emitter.event;

  async rootFor(uri: vscode.Uri): Promise<string | undefined> {
    const folder = vscode.workspace.getWorkspaceFolder(uri);
    if (!folder) return undefined;
    const key = folder.uri.fsPath;
    if (!this.roots.has(key)) {
      try {
        this.roots.set(key, (await runGit(["rev-parse", "--show-toplevel"], key)).trim());
      } catch {
        return undefined;
      }
    }
    return this.roots.get(key);
  }

  set(root: string, report: ConflictReport | undefined): void {
    if (report) this.reports.set(root, report);
    else this.reports.delete(root);
    this.emitter.fire();
  }

  get(root: string): ConflictReport | undefined {
    return this.reports.get(root);
  }

  all(): [string, ConflictReport][] {
    return [...this.reports.entries()];
  }

  /** Hunks of the report that belong to this document (paths in the report are relative to the repo root). */
  hunksFor(root: string, doc: vscode.TextDocument): HunkReport[] {
    const rep = this.reports.get(root);
    if (!rep) return [];
    const real = (p: string) => {
      try {
        return fs.realpathSync(p); // e.g. macOS /var -> /private/var: git reports the resolved path
      } catch {
        return p;
      }
    };
    const rel = path.relative(real(root), real(doc.uri.fsPath)).split(path.sep).join("/");
    return rep.files.find((f) => f.path === rel)?.hunks ?? [];
  }

  async hunkAt(doc: vscode.TextDocument, line: number): Promise<{ root: string; hunk: HunkReport } | undefined> {
    const root = await this.rootFor(doc.uri);
    if (!root) return undefined;
    const hunk = this.hunksFor(root, doc).find((h) => line >= h.start_line - 1 && line <= h.end_line - 1);
    return hunk ? { root, hunk } : undefined;
  }

  findById(hunkId: string): { root: string; hunk: HunkReport } | undefined {
    for (const [root, rep] of this.reports) {
      for (const f of rep.files) {
        const hunk = f.hunks.find((h) => h.id === hunkId);
        if (hunk) return { root, hunk };
      }
    }
    return undefined;
  }
}
