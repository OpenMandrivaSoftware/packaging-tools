#!/bin/bash
# Optional: recreate /tmp layout used by older sessions/scripts.
set -e
ROOT="${TEXLIVE_SYNC_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
WORK="${TEXLIVE_ABF_WORK:-$HOME/texlive-abf-work}"
mkdir -p /tmp/grok-update
ln -sfn "$ROOT" /tmp/grok-update/texlive-sync
ln -sfn "$WORK" /tmp/texlive-abf-work
echo "symlinks:"
ls -la /tmp/grok-update/texlive-sync /tmp/texlive-abf-work
