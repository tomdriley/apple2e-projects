# Pinned toolchain image for the Apple IIe VT100 terminal:
#   cc65 (pinned commit) + MAME + headless JRE + AppleCommander + Python/pyte.
#
# Reuses scripts/setup-toolchain.sh verbatim so the image, CI, the Copilot
# cloud agent, and Codespaces all share one source of truth (dev == CI == agent).
#
# Contains NO Apple firmware ROMs. The Apple IIe firmware is copyrighted; supply
# it privately at runtime via $MAME_ROMPATH (a bind mount or a decoded encrypted
# secret) and validate with `mame -verifyroms`. See vt100-term-c/docs/TESTING.md.
#
# Base pinned by digest for reproducibility (ubuntu:24.04 multi-arch index).
FROM ubuntu:24.04@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90

LABEL org.opencontainers.image.source="https://github.com/tomdriley/apple2e-projects" \
      org.opencontainers.image.description="Pinned cc65 + MAME + AppleCommander toolchain for the Apple IIe VT100 terminal (no ROMs)." \
      org.opencontainers.image.licenses="MIT"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive

# The installer runs as root here (no sudo in the base image); it auto-detects
# root and skips sudo. Keep it as the single source of truth -- do not inline.
COPY scripts/setup-toolchain.sh /usr/local/share/setup-toolchain.sh
RUN bash /usr/local/share/setup-toolchain.sh

# Toolchain + headless MAME defaults. ROMs are intentionally absent.
ENV PATH="/opt/cc65/bin:${PATH}" \
    NONELIB="/opt/cc65/share/cc65/lib/none.lib" \
    MAME="/usr/local/bin/mame" \
    SDL_VIDEODRIVER=dummy \
    SDL_AUDIODRIVER=dummy

WORKDIR /work
CMD ["/bin/bash"]
