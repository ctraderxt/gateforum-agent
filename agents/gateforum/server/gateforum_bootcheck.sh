#!/usr/bin/env bash
# One-shot GateForum stack check. Called by systemd timer / ExecStartPost.
set -euo pipefail
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN || true
exec "$(command -v python3)" "$(dirname "$0")/gateforum_heal.py"
