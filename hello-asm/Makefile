# Apple IIe Hello World — cross-assembler build
# Requires: GNU Make (scoop install make), cc65, MAME, Git Bash
#
# Targets:
#   make        — assemble, link, build disk image → build/hello.dsk
#   make run    — build + launch in MAME
#   make debug  — build + launch in MAME with debugger
#   make clean  — remove build/ directory

# Git Bash — 8.3 short path avoids the space in "Program Files"
SHELL       := C:/PROGRA~1/Git/bin/bash.exe
.SHELLFLAGS := -c

# --- Tool paths -----------------------------------------------------------
CA65 := C:/cc65/bin/ca65.exe
LD65 := C:/cc65/bin/ld65.exe
MAME := C:/mame/mame.exe

# --- Build artefacts -------------------------------------------------------
BUILDDIR := build
OBJ      := $(BUILDDIR)/hello.o
BIN      := $(BUILDDIR)/boot.bin
DSK      := $(BUILDDIR)/hello.dsk

# --------------------------------------------------------------------------
.PHONY: all run debug clean

all: $(DSK)

$(BUILDDIR):
	mkdir -p $(BUILDDIR)

# Assemble 6502 source -> relocatable object
$(OBJ): hello.s | $(BUILDDIR)
	"$(CA65)" -o $(OBJ) hello.s

# Link -> 256-byte boot sector placed at $0800
$(BIN): $(OBJ) hello.cfg
	"$(LD65)" -C hello.cfg -o $(BIN) $(OBJ)

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
