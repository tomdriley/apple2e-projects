# Apple IIe Hello World — cross-assembler build
# Requires: GNU Make (scoop install make), cc65, Java, MAME
#
# Targets:
#   make              — full build (DOS 3.3 disk via AppleCommander)
#   make minimal      — build without AppleCommander (raw boot-sector disk)
#   make run          — build + launch in MAME
#   make run-minimal  — minimal build + launch in MAME
#   make clean        — remove build/ directory

# Git Bash — 8.3 short path avoids the space in "Program Files"
SHELL       := C:/PROGRA~1/Git/bin/bash.exe
.SHELLFLAGS := -c

# --- Tool paths -----------------------------------------------------------
CA65 := C:/cc65/bin/ca65.exe
LD65 := C:/cc65/bin/ld65.exe
JAVA := C:/Program Files/Eclipse Adoptium/jdk-21.0.11.10-hotspot/bin/java.exe
AC   := C:/AppleCommander/AppleCommander-ac-13.0.jar
MAME := C:/mame/mame.exe

# --- Build artefacts -------------------------------------------------------
BUILDDIR := build
OBJ      := $(BUILDDIR)/hello.o
BIN      := $(BUILDDIR)/boot.bin
DSK      := $(BUILDDIR)/hello.dsk
DSK_MIN  := $(BUILDDIR)/hello-minimal.dsk

# --------------------------------------------------------------------------
.PHONY: all minimal run run-minimal clean

all: $(DSK)

minimal: $(DSK_MIN)

$(BUILDDIR):
	mkdir -p $(BUILDDIR)

# Assemble 6502 source -> relocatable object
$(OBJ): hello.s | $(BUILDDIR)
	"$(CA65)" -o $(OBJ) hello.s

# Link -> 256-byte boot sector placed at $0800
$(BIN): $(OBJ) hello.cfg
	"$(LD65)" -C hello.cfg -o $(BIN) $(OBJ)

# Full disk: create DOS 3.3 image, add binary as catalog entry, patch boot sector
$(DSK): $(BIN)
	rm -f $(DSK)
	"$(JAVA)" -jar "$(AC)" -dos140 $(DSK) HELLO
	"$(JAVA)" -jar "$(AC)" -p $(DSK) HELLO BIN 0x0800 < $(BIN)
	dd if=$(BIN) of=$(DSK) bs=256 count=1 conv=notrunc status=none

# Minimal disk: flat 35-track image with boot sector at offset 0, no AppleCommander
$(DSK_MIN): $(BIN)
	dd if=/dev/zero of=$(DSK_MIN) bs=256 count=$$((35*16)) status=none
	dd if=$(BIN) of=$(DSK_MIN) bs=256 count=1 conv=notrunc status=none

# MAME must run from its own directory so the relative rompath resolves
run: $(DSK)
	cd "$(dir $(MAME))" && "$(MAME)" apple2e \
		-flop1 "$(CURDIR)/$(DSK)" \
		-skip_gameinfo -video bgfx -bgfx_backend d3d11

run-minimal: $(DSK_MIN)
	cd "$(dir $(MAME))" && "$(MAME)" apple2e \
		-flop1 "$(CURDIR)/$(DSK_MIN)" \
		-skip_gameinfo

clean:
	rm -rf $(BUILDDIR)
