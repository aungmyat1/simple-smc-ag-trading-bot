#!/bin/bash
# SessionStart hook — install Python deps so tests and the backtest gate
# work in Claude Code on the web. Web-only, idempotent, non-interactive.
set -euo pipefail

# Only run in the remote (Claude Code on the web) environment.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"

echo "[session-start] installing Python dependencies…"
python3 -m pip install --quiet --disable-pip-version-check -r requirements.txt

# Let `import bot` / `python -m bot.runner` resolve from the repo root.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo 'export PYTHONPATH="."' >> "$CLAUDE_ENV_FILE"
fi

echo "[session-start] done."
