#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# provision-roms.sh -- decode the private Apple IIe ROM bundle into a rompath
# and verify it with MAME.
#
# The Apple IIe firmware is copyrighted and NEVER lives in this repo. It is
# delivered out-of-band as an encrypted secret:
#     MAME_ROMS_TGZ_B64 = base64( tar.gz of the rompath contents )
# provisioned as:
#     * a repo/Actions secret        -> cloud CI (ci.yml, mame-conformance)
#     * a `copilot` environment secret -> Copilot cloud agent
#     * a Codespaces secret          -> Codespaces
# For local/container development, bind-mount an existing rompath instead of
# setting this variable.
#
# Writes the ROMs to $1 (else $MAME_ROMPATH, else ~/.mame/roms), runs
# `mame -verifyroms`, and prints the rompath on stdout so callers can do:
#     export MAME_ROMPATH="$(scripts/provision-roms.sh)"
# Exit codes: 3 = secret missing, 4 = ROM verification failed.
# ---------------------------------------------------------------------------
set -euo pipefail

DEST="${1:-${MAME_ROMPATH:-$HOME/.mame/roms}}"

if [ -z "${MAME_ROMS_TGZ_B64:-}" ]; then
    echo "provision-roms: MAME_ROMS_TGZ_B64 is unset/empty -- cannot provision ROMs." >&2
    exit 3
fi

mkdir -p "${DEST}"
printf '%s' "${MAME_ROMS_TGZ_B64}" | base64 -d | tar -xzf - -C "${DEST}"

MAME_BIN="${MAME:-$(command -v mame || echo /usr/games/mame)}"
verify="$("${MAME_BIN}" -rompath "${DEST}" -verifyroms apple2e a2ssc a2diskiing d2fdc 2>&1)" || true
echo "${verify}" >&2
if printf '%s' "${verify}" | grep -qiE 'is bad|not found|incorrect|no such'; then
    echo "provision-roms: ROM verification FAILED (see above)." >&2
    exit 4
fi

echo "${DEST}"
