#!/usr/bin/env bash
# gamba-pick bootstrap (POSIX). Installs uv if missing, syncs the
# locked Python environment, fetches browsers on first run, and
# execs the gamba-pick CLI with whatever args you passed.
#
# Pinned uv installer SHA256. If this fails, see README.txt's
# "Manual fallback" section.

set -euo pipefail

UV_INSTALL_URL="https://astral.sh/uv/install.sh"
UV_INSTALL_SHA256="facbed3a7e2750df3aef698537c6d50869b025a58bdd13154cfdcc2f30354ca2"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 1. Detect / install uv.
if ! command -v uv >/dev/null 2>&1; then
    if [[ ! -x "$HOME/.local/bin/uv" ]]; then
        echo "Installing uv..."
        TMP_INSTALLER="$(mktemp)"
        trap 'rm -f "$TMP_INSTALLER"' EXIT
        curl -fsSL "$UV_INSTALL_URL" -o "$TMP_INSTALLER"
        ACTUAL_SHA="$(sha256sum "$TMP_INSTALLER" | awk '{print $1}')"
        if [[ "$ACTUAL_SHA" != "$UV_INSTALL_SHA256" ]]; then
            echo "ERROR: uv installer hash mismatch." >&2
            echo "  expected: $UV_INSTALL_SHA256" >&2
            echo "  actual:   $ACTUAL_SHA" >&2
            echo "Refusing to run installer. See README.txt for manual install." >&2
            exit 1
        fi
        sh "$TMP_INSTALLER"
    fi
    export PATH="$HOME/.local/bin:$PATH"
fi

# 2. Sync deps (auto-installs Python 3.12 if missing).
uv sync --frozen

# 3. First-run browser fetch.
SENTINEL="$SCRIPT_DIR/.bootstrap-done"
if [[ ! -f "$SENTINEL" ]]; then
    echo "First-run setup: fetching Camoufox + Patchright Chromium (~300 MB)..."
    uv run python -m camoufox fetch
    uv run python -m patchright install chromium
    touch "$SENTINEL"
fi

# 4. Hand off to the CLI.
exec uv run gamba-pick "$@"
