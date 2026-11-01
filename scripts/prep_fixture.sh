#!/bin/sh
# Prepare a test-fixture repo the way a team adopts git prov:
#   init + install + import the repo's existing CI notes (refs/notes/tests) + VS Code workspace settings.
# Usage: scripts/prep_fixture.sh <path/to/fixture/repo>
set -eu
REPO=$(cd "${1:?usage: prep_fixture.sh <repo>}" && pwd)
HERE=$(cd "$(dirname "$0")/.." && pwd)
BIN="$HERE/.venv/bin"
cd "$REPO"
"$BIN/git-prov" init --yes >/dev/null
sed -i '' "s|^test_command = .*|test_command = \"$BIN/python -m pytest -q -x -p no:cacheprovider {tests}\"|" .prov.toml
"$BIN/git-prov" install >/dev/null
"$BIN/git-prov" import-notes --ref tests
mkdir -p .vscode
cat > .vscode/settings.json <<JSON
{
  "gitProv.path": "$BIN/git-prov",
  "gitProv.autoAnalyze": true,
  "terminal.integrated.env.osx": { "PATH": "$BIN:\${env:PATH}" }
}
JSON
grep -qx ".vscode/" .git/info/exclude 2>/dev/null || echo ".vscode/" >> .git/info/exclude
echo "ready: $REPO (open with: code \"$REPO\")"
