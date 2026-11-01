// Integration test (docs/09 §Tests): downloads VS Code, opens a scenario-1 sim repo, checks lenses + accept.
import { execFileSync } from "child_process";
import * as os from "os";
import * as path from "path";
import * as fs from "fs";
import { downloadAndUnzipVSCode, runTests } from "@vscode/test-electron";

async function main() {
  delete process.env.ELECTRON_RUN_AS_NODE; // set when launched from inside another VS Code: would start Code as plain Node
  const repoRoot = path.resolve(__dirname, "../../..");
  const sim = path.join(os.tmpdir(), `prov-ext-sim-${process.pid}`);
  const venvBin = path.join(repoRoot, ".venv/bin");
  execFileSync(process.env.PYTHON ?? path.join(venvBin, "python"), [path.join(repoRoot, "scripts/make_sim_repo.py"), sim, "--stop-at-conflict"], { stdio: "inherit" });
  // Newer VS Code builds ship MacOS/Code instead of MacOS/Electron, which older test-electron versions expect.
  let exe = await downloadAndUnzipVSCode();
  const alt = path.join(path.dirname(exe), "Code");
  if (!fs.existsSync(exe) && fs.existsSync(alt)) exe = alt;
  await runTests({
    vscodeExecutablePath: exe,
    extensionDevelopmentPath: path.resolve(__dirname, "../.."),
    extensionTestsPath: path.resolve(__dirname, "./suite"),
    launchArgs: [sim, "--disable-extensions"],
    extensionTestsEnv: { PATH: `${venvBin}:${process.env.PATH}` },
  });
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
