#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# publish-rom-secret.sh -- package a local MAME rompath into the private
# MAME_ROMS_TGZ_B64 secret and upload it to every GitHub scope that needs it.
#
# This is the *producer* side of the ROM handoff; scripts/provision-roms.sh is
# the *consumer* that decodes and verifies it. The Apple IIe firmware is
# copyrighted and NEVER lives in this repo -- it is delivered out-of-band as:
#     MAME_ROMS_TGZ_B64 = base64( tar.gz of the rompath contents )
#
# The same value must be set in three separate GitHub secret stores, because
# each runner reads its own scope:
#     * actions    -> ci.yml + copilot-setup-steps.yml (Copilot *cloud* agent)
#     * agents     -> the Copilot coding agent (Settings > Secrets > Agents)
#     * codespaces -> Codespaces / dev containers
#
# Usage:
#     scripts/publish-rom-secret.sh [ROMPATH]
#
# ROMPATH defaults to $MAME_ROMPATH, else ~/.mame/roms. Override the target
# repo with REPO=owner/name and the scope list with SCOPES="actions agents".
# Requires: gh (authenticated), tar, base64.
# ---------------------------------------------------------------------------
set -euo pipefail

ROMPATH="${1:-${MAME_ROMPATH:-$HOME/.mame/roms}}"
REPO="${REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
SCOPES="${SCOPES:-actions agents codespaces}"

if [ ! -d "${ROMPATH}" ]; then
    echo "publish-rom-secret: rompath '${ROMPATH}' does not exist." >&2
    exit 2
fi

# Sanity check: the rompath must contain the sets provision-roms.sh verifies.
for want in apple2e a2ssc; do
    if [ -z "$(find "${ROMPATH}" -maxdepth 1 -iname "${want}*" -print -quit)" ]; then
        echo "publish-rom-secret: '${ROMPATH}' is missing the ${want} ROM set." >&2
        exit 2
    fi
done

tgz="$(mktemp -t mame-roms.XXXXXX.tgz)"
trap 'rm -f "${tgz}"' EXIT

# Pack the rompath contents (matches provision-roms.sh's `tar -xzf ... -C DEST`).
tar -C "${ROMPATH}" -czf "${tgz}" .

# Single-line base64 (GNU `-w0` is unavailable on BSD/macOS, so strip newlines).
b64="$(base64 < "${tgz}" | tr -d '\n')"

echo "Packed $(wc -c < "${tgz}") bytes from ${ROMPATH} -> ${#b64} base64 chars."
echo "Publishing MAME_ROMS_TGZ_B64 to ${REPO} for scopes: ${SCOPES}"
for scope in ${SCOPES}; do
    printf '%s' "${b64}" | gh secret set MAME_ROMS_TGZ_B64 --app "${scope}" --repo "${REPO}"
    echo "  set (${scope})"
done

echo "Done. Verify with: gh secret list --app agents --repo ${REPO}"
