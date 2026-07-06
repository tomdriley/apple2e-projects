#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# setup-toolchain.sh -- install the pinned Apple IIe VT100 build + test
# toolchain. This is the single source of truth reused by:
#   * Dockerfile                             (deterministic replication image)
#   * .github/workflows/ci.yml               (cloud CI)
#   * .github/workflows/copilot-setup-steps.yml (Copilot cloud agent)
#   * .devcontainer/devcontainer.json        (Codespaces / Dev Containers)
# so that dev == CI == agent == Codespaces.
#
# It installs NO Apple ROMs. The Apple IIe firmware is copyrighted and is
# delivered privately at runtime (mounted dir or an encrypted secret); see
# vt100-term-c/docs/TESTING.md. Consumers point $MAME_ROMPATH at the ROMs and
# validate them with `mame -verifyroms`.
#
# Idempotent. Runs as root (containers) or via sudo (CI runners / Codespaces).
# All pins are overridable via environment variables (see below).
# ---------------------------------------------------------------------------
set -euo pipefail

# ---- pinned versions (override via env) -----------------------------------
CC65_REPO="${CC65_REPO:-https://github.com/cc65/cc65.git}"
CC65_COMMIT="${CC65_COMMIT:-cc3c40c54e51b2d9a22b63c85c418a2b11763377}"
CC65_PREFIX="${CC65_PREFIX:-/opt/cc65}"

AC_VERSION="${AC_VERSION:-13.0}"
AC_SHA256="${AC_SHA256:-2860b0a2e5a405dbcf3b58fdbd71cabff1e2204a8d066762198125b190e16855}"
AC_PREFIX="${AC_PREFIX:-/opt/applecommander}"

PYTE_VERSION="${PYTE_VERSION:-0.8.2}"

AC_URL="https://github.com/AppleCommander/AppleCommander/releases/download/${AC_VERSION}/AppleCommander-ac-${AC_VERSION}.jar"
AC_JAR="${AC_PREFIX}/AppleCommander-ac-${AC_VERSION}.jar"
NONELIB="${CC65_PREFIX}/share/cc65/lib/none.lib"

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

SUDO=""
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; fi

# ---------------------------------------------------------------------------
# 1. apt packages: build tools, MAME, a headless JRE, Python.
# ---------------------------------------------------------------------------
log "Installing apt packages"
export DEBIAN_FRONTEND=noninteractive
$SUDO apt-get update -y

# MAME lives in the 'universe' component; enable it if the base image/runner
# has it disabled (GitHub runners and Ubuntu Docker images ship it enabled).
if ! apt-cache show mame >/dev/null 2>&1; then
    log "Enabling 'universe' component (provides mame)"
    $SUDO apt-get install -y --no-install-recommends software-properties-common
    $SUDO add-apt-repository -y universe
    $SUDO apt-get update -y
fi

$SUDO apt-get install -y --no-install-recommends \
    ca-certificates curl git \
    build-essential \
    mame \
    openjdk-21-jre-headless \
    python3 python3-pip
$SUDO rm -rf /var/lib/apt/lists/*

# Put MAME on PATH (apt installs it to /usr/games, which is not always on the
# non-login PATH used by CI shells and the agent).
if [ -x /usr/games/mame ]; then
    $SUDO ln -sf /usr/games/mame /usr/local/bin/mame
fi

# ---------------------------------------------------------------------------
# 2. cc65 -- built from a pinned commit. This is the toolchain the project
#    actually ships and the only one that produces a 0-regression conformance
#    build (crt0.s stays unmodified; imports c_sp). Installs to $CC65_PREFIX
#    with the tools symlinked onto PATH.
# ---------------------------------------------------------------------------
log "Installing cc65 @ ${CC65_COMMIT}"
if [ -x "${CC65_PREFIX}/bin/cc65" ] \
    && "${CC65_PREFIX}/bin/cc65" --version 2>&1 | grep -q "${CC65_COMMIT:0:7}"; then
    echo "cc65 ${CC65_COMMIT:0:9} already installed at ${CC65_PREFIX} -- skipping build"
else
    tmp="$(mktemp -d)"
    git clone --filter=blob:none "${CC65_REPO}" "${tmp}/cc65"
    if ! git -C "${tmp}/cc65" checkout --detach "${CC65_COMMIT}" 2>/dev/null; then
        git -C "${tmp}/cc65" fetch --depth 1 origin "${CC65_COMMIT}"
        git -C "${tmp}/cc65" checkout --detach FETCH_HEAD
    fi
    make -C "${tmp}/cc65" -j"$(nproc)" PREFIX="${CC65_PREFIX}"
    $SUDO make -C "${tmp}/cc65" install PREFIX="${CC65_PREFIX}"
    rm -rf "${tmp}"
fi
for t in cc65 ca65 ld65 cl65 ar65 co65 od65 da65 sim65; do
    if [ -x "${CC65_PREFIX}/bin/${t}" ]; then
        $SUDO ln -sf "${CC65_PREFIX}/bin/${t}" "/usr/local/bin/${t}"
    fi
done

# ---------------------------------------------------------------------------
# 3. AppleCommander -- builds the bootable vt100.dsk. Download + verify the
#    published jar checksum, then drop an `ac` wrapper on PATH.
# ---------------------------------------------------------------------------
log "Installing AppleCommander ${AC_VERSION}"
$SUDO mkdir -p "${AC_PREFIX}"
if [ ! -f "${AC_JAR}" ] || ! printf '%s  %s\n' "${AC_SHA256}" "${AC_JAR}" | sha256sum -c - >/dev/null 2>&1; then
    curl -fsSL "${AC_URL}" -o /tmp/ac.jar
    printf '%s  %s\n' "${AC_SHA256}" /tmp/ac.jar | sha256sum -c -
    $SUDO mv /tmp/ac.jar "${AC_JAR}"
fi
# AppleCommander 13.0 is compiled for Java 21 (class-file version 65). Resolve
# the apt-installed JRE by absolute path so the wrapper never picks up an older
# `java` that happens to be first on PATH (e.g. a CI runner's preinstalled JDK).
AC_JAVA="$(dpkg -L openjdk-21-jre-headless 2>/dev/null | grep -m1 '/bin/java$' || true)"
AC_JAVA="${AC_JAVA:-java}"
$SUDO tee /usr/local/bin/ac >/dev/null <<EOF
#!/usr/bin/env bash
exec "${AC_JAVA}" -jar "${AC_JAR}" "\$@"
EOF
$SUDO chmod +x /usr/local/bin/ac

# ---------------------------------------------------------------------------
# 4. Python conformance oracle dependency.
# ---------------------------------------------------------------------------
log "Installing Python deps (pyte==${PYTE_VERSION})"
$SUDO pip3 install --no-cache-dir --break-system-packages "pyte==${PYTE_VERSION}"

# ---------------------------------------------------------------------------
# 5. Summary -- print every tool version for run logs / determinism auditing.
# ---------------------------------------------------------------------------
log "Toolchain summary"
cc65 --version 2>&1 | head -1 || true
ca65 --version 2>&1 | head -1 || true
ld65 --version 2>&1 | head -1 || true
if [ -f "${NONELIB}" ]; then
    echo "none.lib: ${NONELIB} ($(stat -c%s "${NONELIB}") bytes)"
else
    echo "WARNING: none.lib not found at ${NONELIB}" >&2
fi
mame -version 2>&1 | head -1 || true
"${AC_JAVA}" -version 2>&1 | head -1 || true
python3 --version
python3 -c "import importlib.metadata as m; print('pyte', m.version('pyte'))" || true
echo "ac -> ${AC_JAVA} -jar ${AC_JAR}"

cat <<EOF

Toolchain ready. For builds/tests, set:
  NONELIB=${NONELIB}
  MAME=$(command -v mame || echo /usr/games/mame)
  MAME_ROMPATH=<path to Apple IIe ROMs>   # not installed here; supply privately
EOF
