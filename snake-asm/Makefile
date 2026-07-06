# Apple IIe Snake — cross-assembler build
# Requires: GNU Make (scoop install make), cc65, AppleCommander, MAME, Git Bash
#
# Targets:
#   make        — assemble, link, build disk image → build/snake.po
#   make run    — build + launch in MAME
#   make debug  — build + launch in MAME with debugger
#   make clean  — remove build/ directory

# --- Tool paths -----------------------------------------------------------
# Windows uses absolute tool paths (the Git-Bash shell's 8.3 short path avoids
# the space in "Program Files"). Linux/CI resolves the tools from PATH. Every
# entry is overridable from the environment or the make command line, so CI can
# inject its own toolchain (see scripts/setup-toolchain.sh).
ifeq ($(OS),Windows_NT)
  SHELL := C:/PROGRA~1/Git/bin/bash.exe
  CA65  ?= C:/cc65/bin/ca65.exe
  LD65  ?= C:/cc65/bin/ld65.exe
  AC    ?= C:/AppleCommander/ac.cmd
  MAME  ?= C:/mame/mame.exe
else
  SHELL := /bin/bash
  CA65  ?= ca65
  LD65  ?= ld65
  AC    ?= ac
  MAME  ?= mame
endif
.SHELLFLAGS := -c

# --- Build artefacts -------------------------------------------------------
BUILDDIR := build
OBJ      := $(BUILDDIR)/snake.o
BIN      := $(BUILDDIR)/SNAKE.BIN
DSK      := $(BUILDDIR)/snake.dsk

# --------------------------------------------------------------------------
.PHONY: all run debug clean

all: $(DSK)

$(BUILDDIR):
	mkdir -p $(BUILDDIR)

# Assemble 6502 source -> relocatable object
$(OBJ): snake.s | $(BUILDDIR)
	"$(CA65)" -l $(BUILDDIR)/snake.lst -o $(OBJ) snake.s

# Link -> binary placed at $0800
$(BIN): $(OBJ) snake.cfg
	"$(LD65)" -C snake.cfg -m $(BUILDDIR)/snake.map -o $(BIN) $(OBJ)

# DOS 3.3 disk image: copy base, then inject the binary via AppleCommander
$(DSK): $(BIN)
	cp dos33-base.dsk $(DSK)
	"$(AC)" -d $(DSK) SNAKE 2>/dev/null || true
	"$(AC)" -p $(DSK) SNAKE BIN 0x0800 < $(BIN)

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
