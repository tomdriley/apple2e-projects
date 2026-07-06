#!/usr/bin/env bash
# Dev Container / Codespaces post-create: make the Apple IIe ROMs available for
# Tier 2 (real MAME) work. ROMs are never baked into the repo or the image.
#
# Resolution order:
#   1. ROMs already present at $MAME_ROMPATH (e.g. a bind mount) -> just verify.
#   2. $MAME_ROMS_TGZ_B64 set (Codespaces secret / host env) -> decode + verify.
#   3. Neither -> print guidance. Tier 1 (hermetic) work needs no ROMs.
set -euo pipefail

DEST="${MAME_ROMPATH:-/opt/mame-roms}"
mkdir -p "$DEST"

if [ -n "$(ls -A "$DEST" 2>/dev/null || true)" ]; then
  echo "ROMs already present at $DEST -- verifying."
  mame -rompath "$DEST" -verifyroms apple2e a2ssc || true
elif [ -n "${MAME_ROMS_TGZ_B64:-}" ]; then
  bash scripts/provision-roms.sh "$DEST"
else
  echo "No Apple IIe ROMs available."
  echo "  * Codespaces: add a Codespaces secret MAME_ROMS_TGZ_B64."
  echo "  * Local Dev Container: export MAME_ROMS_TGZ_B64 on the host, or"
  echo "    bind-mount your rompath to $DEST (see .devcontainer/devcontainer.json)."
  echo "Tier 1 (hermetic build + oracle) works without ROMs."
fi
