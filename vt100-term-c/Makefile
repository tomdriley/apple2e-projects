# Apple IIe 80-column VT100 terminal in C — cc65 cross-compile build
# Requires: GNU Make (scoop install make), cc65, AppleCommander, MAME, Git Bash
#
# Targets:
#   make        — compile, assemble, link, build disk image → build/vt100.dsk
#   make run    — build + launch in MAME with the Super Serial Card wired to a
#                 socket. The disk boots straight into the terminal (its DOS 3.3
#                 greeting BRUNs VT100 — same on real hardware). Start a serial
#                 host first (see client/) so the null modem has a peer.
#   make debug  — build + launch in MAME with the debugger
#   make test   — build + run the ROM-free host-side unit tests (cc65 sim65)
#   make clean  — remove build/ directory

# --- Tool paths -----------------------------------------------------------
# Windows uses absolute tool paths (the Git-Bash shell's 8.3 short path avoids
# the space in "Program Files"). Linux/CI resolves the tools from PATH. Every
# entry is overridable from the environment or the make command line
# (e.g. `make CC65=/opt/cc65/bin/cc65`), so CI can inject its own toolchain.
ifeq ($(OS),Windows_NT)
  SHELL   := C:/PROGRA~1/Git/bin/bash.exe
  CC65    ?= C:/cc65/bin/cc65.exe
  CA65    ?= C:/cc65/bin/ca65.exe
  LD65    ?= C:/cc65/bin/ld65.exe
  SIM65   ?= C:/cc65/bin/sim65.exe
  AC      ?= C:/AppleCommander/ac.cmd
  MAME    ?= C:/mame/mame.exe
  ROMPATH ?= C:/mame/roms
  # none.lib = bare 6502 runtime (cc65 helpers), no OS/platform startup
  NONELIB ?= C:/cc65/lib/none.lib
else
  SHELL   := /bin/bash
  CC65    ?= cc65
  CA65    ?= ca65
  LD65    ?= ld65
  SIM65   ?= sim65
  AC      ?= ac
  MAME    ?= mame
  ROMPATH ?= $(HOME)/.mame/roms
  # none.lib = bare 6502 runtime (cc65 helpers), no OS/platform startup.
  # Default to the pinned source build installed by scripts/setup-toolchain.sh;
  # override (e.g. NONELIB=/usr/share/cc65/lib/none.lib) for a system cc65.
  NONELIB ?= /opt/cc65/share/cc65/lib/none.lib
endif
.SHELLFLAGS := -c

# --- Build artefacts -------------------------------------------------------
BUILDDIR := build
CSRC     := $(wildcard *.c)
COBJ     := $(patsubst %.c,$(BUILDDIR)/%.o,$(CSRC))
CRT0     := $(BUILDDIR)/crt0.o
MON      := $(BUILDDIR)/monitor.o
OBJ      := $(CRT0) $(COBJ) $(MON)
BIN      := $(BUILDDIR)/VT100.BIN
DSK      := $(BUILDDIR)/vt100.dsk
BASE     := dos33-base.dsk

# --- Serial wiring for MAME ------------------------------------------------
# Super Serial Card in slot 2, its RS-232 port bridged to a null modem whose
# bit stream is a TCP socket. MAME connects out as a client, so a Python
# listener (client/) must already be running on this port.
SERIAL_PORT := 6551
MAME_SERIAL := -sl2 ssc -sl2:ssc:rs232 null_modem -bitb socket.127.0.0.1:$(SERIAL_PORT)
# -aux ext80 supplies the auxiliary RAM the 80-column display needs.
MAME_VIDEO  := -aux ext80 -skip_gameinfo -video bgfx -bgfx_backend d3d11

# --------------------------------------------------------------------------
.PHONY: all run debug clean test
.SECONDARY:                       # keep intermediate build/*.s listings

all: $(DSK)

$(BUILDDIR):
	mkdir -p $(BUILDDIR)

# Compile C -> 6502 assembly
#   -O   optimize
#   -Cl  static locals (fixed addresses, not the software stack): smaller/faster.
#        Safe here because nothing is reentrant (no recursion/interrupts).
$(BUILDDIR)/%.s: %.c monitor.h | $(BUILDDIR)
	"$(CC65)" -O -Cl -t none -o $@ $<

# Assemble compiler output -> relocatable object
$(BUILDDIR)/%.o: $(BUILDDIR)/%.s
	"$(CA65)" -t none -o $@ $<

# Assemble the startup shim (sets up C stack, zeroes BSS, calls _start)
$(CRT0): crt0.s | $(BUILDDIR)
	"$(CA65)" -t none -o $(CRT0) crt0.s

# Assemble the hardware address registry (symbols only, zero bytes emitted)
$(MON): monitor.s | $(BUILDDIR)
	"$(CA65)" -t none -o $(MON) monitor.s

# Link -> raw binary placed at $0800; runtime helpers pulled from none.lib
$(BIN): $(OBJ) vt100.cfg
	"$(LD65)" -C vt100.cfg -m $(BUILDDIR)/vt100.map -Ln $(BUILDDIR)/vt100.lbl -o $(BIN) $(OBJ) "$(NONELIB)"
	@sz=$$(stat -c %s $(BIN)); echo "VT100.BIN: $$sz bytes (loads at \$$0800)"

# DOS 3.3 disk image: copy the base disk, inject the binary, then replace the
# boot greeting with a one-line launcher so booting runs the terminal straight
# away — on MAME and real hardware alike (ADTPro-style auto-boot).
$(DSK): $(BIN) hello.bas
	cp $(BASE) $(DSK)
	"$(AC)" -d $(DSK) VT100 2>/dev/null || true
	"$(AC)" -p $(DSK) VT100 BIN 0x0800 < $(BIN)
	"$(AC)" -d $(DSK) HELLO 2>/dev/null || true
	"$(AC)" -bas $(DSK) HELLO < hello.bas

# MAME must run from its own directory so the relative rompath resolves
run: $(DSK)
	cd "$(dir $(MAME))" && "$(MAME)" apple2e -rompath "$(ROMPATH)" \
		-flop1 "$(CURDIR)/$(DSK)" $(MAME_SERIAL) $(MAME_VIDEO)

debug: $(DSK)
	cd "$(dir $(MAME))" && "$(MAME)" apple2e -rompath "$(ROMPATH)" \
		-flop1 "$(CURDIR)/$(DSK)" $(MAME_SERIAL) $(MAME_VIDEO) -debug

clean:
	rm -rf $(BUILDDIR)

# --- Host-side unit tests (ROM-free) ---------------------------------------
# Compile the ring-buffer unit test with cc65's sim6502 target and run it on
# the host through the sim65 simulator. This exercises the RX ring FIFO logic
# (issue #5) with the real cc65 compiler's integer/wraparound semantics, with
# no ROMs, MAME or serial socket needed -- so it runs in the hermetic CI job.
# main() returns the failing-check count, so a regression fails the build.
TESTDIR   := tests
TESTPRG   := $(BUILDDIR)/ring_test.prg
SIM6502LIB ?= sim6502.lib

$(TESTPRG): $(TESTDIR)/ring_test.c | $(BUILDDIR)
	"$(CC65)" -O -t sim6502 -o $(BUILDDIR)/ring_test.s $(TESTDIR)/ring_test.c
	"$(CA65)" -t sim6502 -o $(BUILDDIR)/ring_test.o $(BUILDDIR)/ring_test.s
	"$(LD65)" -t sim6502 -o $(TESTPRG) $(BUILDDIR)/ring_test.o "$(SIM6502LIB)"

test: $(TESTPRG)
	"$(SIM65)" $(TESTPRG)
