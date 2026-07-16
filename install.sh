#!/usr/bin/env bash
# install.sh — stamp the real bridge path into the profile and place files.
#
# AntiMicroX execute-slots need an absolute path to the program in <code>.
# The shipped profile uses the placeholder BRIDGE; this swaps it for the real
# location of codex-bridge.sh so you can load the .amgp straight into AntiMicroX.

set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
bridge="$here/bin/codex-bridge.sh"
profile="$here/agent-key.gamecontroller.amgp"
out="$here/agent-key.installed.gamecontroller.amgp"

chmod +x "$bridge"

for src in "$here"/*.gamecontroller.amgp; do
  case "$src" in *.installed.gamecontroller.amgp) continue;; esac
  dst="${src%.gamecontroller.amgp}.installed.gamecontroller.amgp"
  sed "s|<code>BRIDGE</code>|<code>$bridge</code>|g" "$src" > "$dst"
  echo "Stamped: $dst"
done

echo
echo "Bridge:  $bridge"
echo
echo "Next:"
echo "  1. Ensure 'codex' is on PATH (or set CODEX_BIN in your shell)."
echo "  2. Open AntiMicroX, select your controller, and load:"
echo "       $out"
echo "  3. Set your project dir once per session:  export CODEX_WORKDIR=~/my/repo"
