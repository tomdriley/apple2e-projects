# Apple IIe "Hello World" in C — cc65 cross-compile build
# Requires: GNU Make (scoop install make), cc65, MAME, Git Bash
#
# Targets:
#   make        — compile, assemble, link, build disk image → build/hello.dsk
#   make run    — build + launch in MAME
#   make debug  — build + launch in MAME with debugger
#   make clean  — remove build/ directory

# Git Bash — 8.3 short path avoids the space in "Program Files"
SHELL       := C:/PROGRA~1/Git/bin/bash.exe
.SHELLFLAGS := -c

# --- Tool paths -----------------------------------------------------------
CC65    := C:/cc65/bin/cc65.exe
CA65    := C:/cc65/bin/ca65.exe
LD65    := C:/cc65/bin/ld65.exe
MAME    := C:/mame/mame.exe
# none.lib = bare 6502 runtime (cc65 helpers), no OS/platform startup
NONELIB := C:/cc65/lib/none.lib

# --- Build artefacts -------------------------------------------------------
BUILDDIR := build
ASM      := $(BUILDDIR)/hello.s
OBJ      := $(BUILDDIR)/hello.o
CRT0     := $(BUILDDIR)/crt0.o
BIN      := $(BUILDDIR)/boot.bin
DSK      := $(BUILDDIR)/hello.dsk

# --------------------------------------------------------------------------
.PHONY: all run debug clean

all: $(DSK)

$(BUILDDIR):
	mkdir -p $(BUILDDIR)

# Compile C -> 6502 assembly
$(ASM): hello.c | $(BUILDDIR)
	"$(CC65)" -O -t none -o $(ASM) hello.c

# Assemble compiler output -> relocatable object
$(OBJ): $(ASM)
	"$(CA65)" -t none -l $(BUILDDIR)/hello.lst -o $(OBJ) $(ASM)

# Assemble the boot/startup shim (sets up C stack, calls _start)
$(CRT0): crt0.s | $(BUILDDIR)
	"$(CA65)" -t none -o $(CRT0) crt0.s

# Link -> raw boot image placed at $0800; runtime helpers pulled from none.lib
$(BIN): $(CRT0) $(OBJ) hello.cfg
	"$(LD65)" -C hello.cfg -m $(BUILDDIR)/hello.map -o $(BIN) $(CRT0) $(OBJ) "$(NONELIB)"
	@sz=$$(stat -c %s $(BIN)); echo "boot.bin: $$sz bytes"; \
	 if [ $$sz -gt 256 ]; then \
	   echo "ERROR: boot image $$sz bytes > 256 — won't fit in one boot sector."; \
	   echo "       (would need a multi-sector boot loader; see crt0.s/hello.cfg)"; \
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
		-flop1 "$(CURDIR)/$(DSK)" $(MAME_FLAGS)

debug: $(DSK)
	cd "$(dir $(MAME))" && "$(MAME)" apple2e \
		-flop1 "$(CURDIR)/$(DSK)" $(MAME_FLAGS) -debug

clean:
	rm -rf $(BUILDDIR)
