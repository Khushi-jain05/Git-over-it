import { execFile } from "child_process";
import * as vscode from "vscode";

export class ProvCliError extends Error {
  constructor(
    public code: string,
    message: string,
    public exit: number,
    public hint?: string | null,
  ) {
    super(message);
  }
}

/** Run `git-prov <args> --json` and parse the JSON contract (docs/08). */
export function runProv(args: string[], cwd: string): Promise<any> {
  const exe = vscode.workspace.getConfiguration("gitProv").get<string>("path", "git-prov");
  return new Promise((resolve, reject) => {
    execFile(exe, [...args, "--json"], { cwd, maxBuffer: 64 * 1024 * 1024 }, (err, stdout, stderr) => {
      let doc: any;
      try {
        doc = JSON.parse(stdout);
      } catch {
        const exit = typeof (err as any)?.code === "number" ? (err as any).code : 1;
        const missing = (err as any)?.code === "ENOENT";
        reject(
          new ProvCliError(
            missing ? "not_found" : "bad_output",
            missing ? `git-prov not found at "${exe}"` : (stderr || String(err)).trim(),
            exit,
            missing ? "set gitProv.path or pip install git-prov" : null,
          ),
        );
        return;
      }
      if (doc.error) {
        reject(new ProvCliError(doc.error.code, doc.error.message, doc.error.exit, doc.error.hint));
      } else if (doc.schema_version !== 1) {
        reject(new ProvCliError("schema", `unsupported schema_version ${doc.schema_version}`, 1));
      } else {
        resolve(doc);
      }
    });
  });
}

export function runGit(args: string[], cwd: string): Promise<string> {
  return new Promise((resolve, reject) =>
    execFile("git", args, { cwd }, (err, stdout, stderr) => (err ? reject(new Error(stderr || String(err))) : resolve(stdout))),
  );
}
