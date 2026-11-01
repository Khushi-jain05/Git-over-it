# git prov — conflict provenance for VS Code

Thin client for the `git-prov` CLI. On every conflict hunk it shows where each side came from, where that change
was tested and deployed, and which side is production-proven.

- **CodeLens** on each `<<<<<<<` line: `THEIRS: PROD-VERIFIED (uat, prod) | OURS: TESTED ELSEWHERE (dev) | Recommend | Explain | Evidence`, then `Accept OURS | Accept THEIRS | Accept draft`.
- **Hover** inside a hunk: origin commit, branches, every evidence record with links.
- **Colours** per side by badge; evidence that is only *inferred* gets a dashed border, never a fill.
- **Evidence panel**: origins, equivalents (L0–L5 with confidence), evidence, LLM draft with untested lines highlighted.
- **Commit**: runs the gates and finishes the cherry-pick/merge with standard Git.

Requires `git-prov` on `PATH` (or set `gitProv.path`). Badges and test status come only from Git and recorded
evidence; the LLM only explains and drafts.
