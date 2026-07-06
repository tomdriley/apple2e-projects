# Apple IIe Super Serial Card demo in C — cc65 cross-compile build
# Requires: GNU Make (scoop install make), cc65, MAME, Git Bash
#
# Targets:
#   make        — compile, assemble, link, build disk image → build/ssc-serial.dsk
#   make run    — build + launch in MAME with the Super Serial Card wired to a socket
#   make debug  — build + launch in MAME with the debugger
#   make clean  — remove build/ directory
#
# For a two-way demo, start the Python client first so it is listening, then
# `make run` (MAME connects out to it). See client/serial_demo.py and README.md.

# --- Tool paths -----------------------------------------------------------
# Windows uses absolute tool paths (the Git-Bash shell's 8.3 short path avoids
# the space in "Program Files"). Linux/CI resolves the tools from PATH. Every
# entry is overridable from the environment or the make command line, so CI can
# inject its own toolchain (see scripts/setup-toolchain.sh).
ifeq ($(OS),Windows_NT)
  SHELL   := C:/PROGRA~1/Git/bin/bash.exe
  CC65    ?= C:/cc65/bin/cc65.exe
  CA65    ?= C:/cc65/bin/ca65.exe
  LD65    ?= C:/cc65/bin/ld65.exe
  MAME    ?= C:/mame/mame.exe
  # none.lib = bare 6502 runtime (cc65 helpers), no OS/platform startup
  NONELIB ?= C:/cc65/lib/none.lib
else
  SHELL   := /bin/bash
  CC65    ?= cc65
  CA65    ?= ca65
  LD65    ?= ld65
  MAME    ?= mame
  # none.lib default from scripts/setup-toolchain.sh (pinned cc65 source build);
  # override (e.g. NONELIB=/usr/share/cc65/lib/none.lib) for a system cc65.
  NONELIB ?= /opt/cc65/share/cc65/lib/none.lib
endif
.SHELLFLAGS := -c

# --- Build artefacts -------------------------------------------------------
PROG     := ssc-serial
BUILDDIR := build
ASM      := $(BUILDDIR)/$(PROG).s
OBJ      := $(BUILDDIR)/$(PROG).o
CRT0     := $(BUILDDIR)/crt0.o
MON      := $(BUILDDIR)/monitor.o
BIN      := $(BUILDDIR)/boot.bin
DSK      := $(BUILDDIR)/$(PROG).dsk

# --- Serial wiring for MAME ------------------------------------------------
# Super Serial Card in slot 2, its RS-232 port bridged to a null modem whose
# bit stream is piped to a TCP socket. MAME connects out as a client, so the
# Python listener must already be running on this port.
SSC_SLOT    := 2
SERIAL_PORT := 6551
MAME_SERIAL := -sl$(SSC_SLOT) ssc \
               -sl$(SSC_SLOT):ssc:rs232 null_modem \
               -bitb socket.127.0.0.1:$(SERIAL_PORT)

# --------------------------------------------------------------------------
.PHONY: all run debug clean

all: $(DSK)

$(BUILDDIR):
	mkdir -p $(BUILDDIR)

# Compile C -> 6502 assembly
#   -O   optimize
#   -Cl  static locals (fixed addresses, not the software stack): smaller/faster
#        loops. Safe here because nothing is reentrant (no recursion/interrupts).
$(ASM): $(PROG).c | $(BUILDDIR)
	"$(CC65)" -O -Cl -t none -o $(ASM) $(PROG).c

# Assemble compiler output -> relocatable object
$(OBJ): $(ASM)
	"$(CA65)" -t none -l $(BUILDDIR)/$(PROG).lst -o $(OBJ) $(ASM)

# Assemble the boot/startup shim (sets up C stack, calls _start)
$(CRT0): crt0.s | $(BUILDDIR)
	"$(CA65)" -t none -o $(CRT0) crt0.s

# Assemble the hardware address registry (symbols only, zero bytes emitted)
$(MON): monitor.s | $(BUILDDIR)
	"$(CA65)" -t none -o $(MON) monitor.s

# Link -> raw boot image placed at $0800; runtime helpers pulled from none.lib
$(BIN): $(CRT0) $(OBJ) $(MON) linker.cfg
	"$(LD65)" -C linker.cfg -m $(BUILDDIR)/$(PROG).map -o $(BIN) $(CRT0) $(OBJ) $(MON) "$(NONELIB)"
	@sz=$$(stat -c %s $(BIN)); echo "boot.bin: $$sz bytes"; \
	 if [ $$sz -gt 256 ]; then \
	   echo "ERROR: boot image $$sz bytes > 256 — won't fit in one boot sector."; \
	   echo "       (would need a multi-sector boot loader; see crt0.s/linker.cfg)"; \
	   exit 1; \
	 fi

# Flat 35-track disk image: boot sector at offset 0, rest zeroed
$(DSK): $(BIN)
	dd if=/dev/zero of=$(DSK) bs=256 count=$$((35*16)) status=none
	dd if=$(BIN) of=$(DSK) bs=256 count=1 conv=notrunc status=none

MAME_FLAGS := -skip_gameinfo -video bgfx -bgfx_backend d3d11

# MAME must run from its own directory so the relative rompath resolves
run: $(DSK)
	cd "$(dir $(MAME))" && "$(MAME)" apple2e \
		-flop1 "$(CURDIR)/$(DSK)" $(MAME_SERIAL) $(MAME_FLAGS)

debug: $(DSK)
	cd "$(dir $(MAME))" && "$(MAME)" apple2e \
		-flop1 "$(CURDIR)/$(DSK)" $(MAME_SERIAL) $(MAME_FLAGS) -debug

clean:
	rm -rf $(BUILDDIR)
