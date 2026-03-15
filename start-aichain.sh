#!/usr/bin/env bash
# Launcher script for the AIchain sidecar daemon on POSIX systems.

set -e

WORKSPACE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
cd "$WORKSPACE_DIR"

export PYTHONPATH="."

echo -e "\033[36mBooting AIchain daemon...\033[0m"
python3 -m aichaind.main
