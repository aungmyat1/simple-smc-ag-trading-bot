#!/bin/bash
# SessionStart hook — install Python dependencies so tests, the backtest gate,
# and the bot import cleanly in Claude Code on the web.
#
# Synchronous (the session waits for this to finish) and idempotent
# (safe to run on every session start). Web-only: a no-op locally.
set -euo pipefail

# Only run in the remote (Claude Code on the web) environment.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"

python3 -m pip install --quiet --disable-pip-version-check -r requirements.txt

# Let `import bot` / `python scripts/*.py` resolve from the repo root.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo 'export PYTHONPATH="."' >> "$CLAUDE_ENV_FILE"
fi
