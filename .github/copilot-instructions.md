# Copilot instructions

`README.md` explains the projects, build workflow, and boot mechanics;
`vt100-term-c/docs/` (`HACKING.md`, `TESTING.md`, `CONFORMANCE.md`, …) is the deep
reference for the terminal, including the cc65/hardware conventions. **Read those
first.** This file only calls out what's specific to working as an agent here.

## Verifying a change

Your environment is provisioned by `scripts/setup-toolchain.sh` (cc65, MAME,
AppleCommander, pyte) and **must have the Apple IIe ROMs available to MAME**. Every
change is verified by booting the real firmware, so ROM access is required — not
optional, and not something to work around. Verify the way CI does, from the project
directory:

```bash
make                                              # build the bootable disk image
python client/conformance/selftest.py             # vt100-term-c
python client/conformance/oracle.py --audit
python client/conformance/runner.py --target mame # boots the firmware in MAME — the real gate
```

(ssc-serial-c: `python client/roundtrip_test.py --mame`. See
`vt100-term-c/docs/TESTING.md` for `vt100_test.py` / `shell_test.py` and the full matrix.)

**Always run the ROM-backed MAME tests, not just the ROM-free `selftest.py` /
`oracle.py --audit`.** If MAME can't find the ROMs, **stop and treat it as a broken
environment** — do not fall back to only the ROM-free checks. The ROMs are copyrighted
and never in the repo; they're delivered privately via the `MAME_ROMS_TGZ_B64` secret
and decoded by `scripts/provision-roms.sh`. A failure to find them means that secret
isn't configured for your environment — flag it and get it fixed rather than skipping
the firmware tests.

## Guardrails

- **Never commit Apple ROMs or the ROM secret**, or add any firmware to the tree — they
  are copyrighted and deliberately kept out of the repo.
- **There is no root build and nothing is shared centrally.** Each project has its own
  `Makefile`, and `monitor.s` / `monitor.h` / `crt0.s` are *per-project copies that
  differ* — edit the copy in the project you're changing; don't hoist them into a shared
  module.
- **`build/` is generated and git-ignored** — never edit it; change the sources and
  rebuild.
- **Run `clang-format` on C changes** (`.clang-format` is tuned to match the existing
  hand-written style).
- **cc65 is not modern C**: C89-style declarations only (no mid-block declarations),
  `volatile` on every memory-mapped register, and no recursion/interrupts (the build
  uses `-Cl` static locals). See `vt100-term-c/docs/HACKING.md` before writing C to avoid
  the common compile-time and runtime traps.
